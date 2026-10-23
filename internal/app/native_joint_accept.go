package app

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
)

type NativeJointCommand struct {
	PlanID             funds.ID `json:"plan_id"`
	ReviewedBundleHash string   `json:"reviewed_bundle_hash"`
}

type NativeJointView struct {
	Plan               NativeJointPlan `json:"plan"`
	ReviewedBundleHash string          `json:"reviewed_bundle_hash"`
}

type NativeOperation struct {
	ID                 funds.ID    `json:"id"`
	GroupID            funds.ID    `json:"group_id"`
	ChildNumber        int         `json:"child_number"`
	Kind               bankv2.Kind `json:"kind"`
	State              string      `json:"state"`
	Amount             funds.Money `json:"amount_cents"`
	BankKey            string      `json:"bank_key"`
	BankObservedStatus *string     `json:"bank_observed_status,omitempty"`
	ProcessingHealth   string      `json:"processing_health"`
	ReceiptID          *string     `json:"receipt_id,omitempty"`
	Version            uint64      `json:"version"`
	CreatedAt          time.Time   `json:"created_at"`
	UpdatedAt          time.Time   `json:"updated_at"`
}

type NativeOperationGroup struct {
	ID         funds.ID          `json:"id"`
	PlanID     funds.ID          `json:"plan_id"`
	Operations []NativeOperation `json:"operations"`
}

// The USER reviews the complete immutable plan. Child effects are bound in the
// same original; they never acquire consent from a MODEL or a parent label.
type nativeJointApproval struct {
	Protocol           string    `json:"protocol"`
	PrincipalKind      string    `json:"principal_kind"`
	EnvironmentID      funds.ID  `json:"environment_id"`
	UserID             funds.ID  `json:"user_id"`
	SessionID          funds.ID  `json:"session_id"`
	PlanID             funds.ID  `json:"plan_id"`
	ReviewedBundleHash string    `json:"reviewed_bundle_hash"`
	ChildEffectHashes  []string  `json:"child_effect_hashes"`
	ConfirmedAt        time.Time `json:"confirmed_at"`
	ExpiresAt          time.Time `json:"expires_at"`
}

type nativeExecutionPreview struct {
	Protocol           string              `json:"protocol"`
	PlanID             funds.ID            `json:"plan_id"`
	ReviewedBundleHash string              `json:"reviewed_bundle_hash"`
	Child              planning.JointChild `json:"child"`
	Request            bankv2.Request      `json:"bank_request"`
}

func NativeJointReviewHash(plan NativeJointPlan) (string, error) {
	raw, err := encode(plan)
	return Hash(raw), err
}

func loadNativeGroup(ctx context.Context, tx pgx.Tx, p Principal, group funds.ID) (NativeOperationGroup, error) {
	out := NativeOperationGroup{ID: group, PlanID: group, Operations: []NativeOperation{}}
	rows, err := tx.Query(ctx, `SELECT id::text,child_number,status,bank_key,bank_observed_status,processing_health,receipt_id::text,version,created_at,updated_at,bank_request FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND group_id=$3 ORDER BY child_number`, p.EnvironmentID.String(), p.UserID.String(), group.String())
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var op NativeOperation
		var id string
		var raw []byte
		if err = rows.Scan(&id, &op.ChildNumber, &op.State, &op.BankKey, &op.BankObservedStatus, &op.ProcessingHealth, &op.ReceiptID, &op.Version, &op.CreatedAt, &op.UpdatedAt, &raw); err != nil {
			return out, err
		}
		var request bankv2.Request
		if err = decode(raw, &request); err != nil {
			return out, reject("EVIDENCE_INVALID", err)
		}
		if request.Validate() != nil || request.UserID != p.UserID.String() || request.OperationID != id {
			return out, reject("EVIDENCE_INVALID", nil)
		}
		op.ID, err = funds.ParseID(id)
		if err != nil {
			return out, err
		}
		op.GroupID = group
		op.Kind = request.Effect.Kind
		op.Amount = request.Effect.Amount
		out.Operations = append(out.Operations, op)
	}
	if err = rows.Err(); err != nil {
		return out, err
	}
	if len(out.Operations) == 0 {
		return out, reject("NOT_FOUND", nil)
	}
	return out, nil
}

func (s *Service) NativeGroup(ctx context.Context, p Principal, id funds.ID) (NativeOperationGroup, error) {
	if err := s.auditHuman(ctx, p); err != nil {
		return NativeOperationGroup{}, err
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return NativeOperationGroup{}, err
	}
	defer rollback(ctx, tx)
	return loadNativeGroup(ctx, tx, p, id)
}

func (s *Service) NativeGroupByKey(ctx context.Context, p Principal, key string) (NativeOperationGroup, error) {
	if err := validateKey(key); err != nil {
		return NativeOperationGroup{}, err
	}
	if err := s.auditHuman(ctx, p); err != nil {
		return NativeOperationGroup{}, err
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return NativeOperationGroup{}, err
	}
	defer rollback(ctx, tx)
	var id string
	if err = tx.QueryRow(ctx, `SELECT group_id::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND command_kind='JOINT_ALLOCATE' AND idempotency_key=$3 AND child_number=1`, p.EnvironmentID.String(), p.UserID.String(), key).Scan(&id); err != nil {
		return NativeOperationGroup{}, reject("NOT_FOUND", err)
	}
	group, err := funds.ParseID(id)
	if err != nil {
		return NativeOperationGroup{}, err
	}
	return loadNativeGroup(ctx, tx, p, group)
}

// ResumeNativeGroup reopens only exhausted scheduling for existing originals.
// It neither replaces a key/signature nor grants authority to an unsigned child.
func (s *Service) ResumeNativeGroup(ctx context.Context, p Principal, id funds.ID) (NativeOperationGroup, error) {
	var zero NativeOperationGroup
	tx, err := s.begin(ctx)
	if err != nil {
		return zero, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return zero, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return zero, err
	}
	if _, _, err = lockUser(ctx, tx, p); err != nil {
		return zero, err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return zero, err
	}
	if _, err = loadNativeGroup(ctx, tx, p, id); err != nil {
		return zero, err
	}
	rows, err := tx.Query(ctx, `UPDATE native_jobs j SET state='READY',paused=false,attempts=0,lease_owner=NULL,lease_until=NULL,claim_generation=claim_generation+1,next_attempt_at=clock_timestamp(),last_error='USER_REQUESTED_ORIGINAL_RECOVERY' FROM native_operations o WHERE j.environment_id=$1 AND j.user_id=$2 AND o.environment_id=j.environment_id AND o.user_id=j.user_id AND o.id=j.operation_id AND o.group_id=$3 AND o.status NOT IN ('SETTLED','REJECTED','CANCELLED') AND (j.paused OR j.state='EXHAUSTED') RETURNING o.id::text`, env.ID.String(), p.UserID.String(), id.String())
	if err != nil {
		return zero, err
	}
	operations := []string{}
	for rows.Next() {
		var operation string
		if err = rows.Scan(&operation); err != nil {
			rows.Close()
			return zero, err
		}
		operations = append(operations, operation)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return zero, err
	}
	for _, operation := range operations {
		if _, err = tx.Exec(ctx, `UPDATE native_operations SET processing_health='RECOVERY_REQUESTED',updated_at=$4,version=version+1 WHERE environment_id=$1 AND user_id=$2 AND id=$3`, env.ID.String(), p.UserID.String(), operation, s.now()); err != nil {
			return zero, err
		}
	}
	if len(operations) > 0 {
		event, err := newID()
		if err != nil {
			return zero, err
		}
		payload, err := encode(struct {
			GroupID, SessionID   funds.ID
			OriginalOperationIDs []string
			FinancialAuthority   bool
		}{id, p.SessionID, operations, false})
		if err != nil {
			return zero, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO outbox(environment_id,user_id,id,aggregate_id,aggregate_version,kind,payload,created_at) VALUES($1,$2,$3,$4,1,'NATIVE_ORIGINAL_RECOVERY_REQUESTED',$5,$6)`, env.ID.String(), p.UserID.String(), event.String(), id.String(), payload, s.now()); err != nil {
			return zero, err
		}
	}
	group, err := loadNativeGroup(ctx, tx, p, id)
	if err != nil {
		return zero, err
	}
	if err = tx.Commit(ctx); err != nil {
		return zero, err
	}
	return group, nil
}

// AcceptNativeJoint is one atomic T1. Every resource for every accepted child
// is reserved here, before a durable job or bank permit can be consumed.
func (s *Service) AcceptNativeJoint(ctx context.Context, p Principal, key string, in NativeJointCommand) (NativeOperationGroup, error) {
	var zero NativeOperationGroup
	if err := validateKey(key); err != nil {
		return zero, err
	}
	raw, err := encode(in)
	if err != nil {
		return zero, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return zero, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return zero, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return zero, err
	}
	fv, sv, err := lockUser(ctx, tx, p)
	if err != nil {
		return zero, err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return zero, err
	}
	var prior, requestHash string
	err = tx.QueryRow(ctx, `SELECT group_id::text,request_hash FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND command_kind='JOINT_ALLOCATE' AND idempotency_key=$3 AND child_number=1`, env.ID.String(), p.UserID.String(), key).Scan(&prior, &requestHash)
	if err == nil {
		if requestHash != Hash(raw) {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		group, e := funds.ParseID(prior)
		if e != nil {
			return zero, e
		}
		return loadNativeGroup(ctx, tx, p, group)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return zero, err
	}
	if s.NativeBank == nil {
		return zero, reject("BANK_UNAVAILABLE", nil)
	}
	plan, err := loadNativeJointPlan(ctx, tx, p, in.PlanID)
	if err != nil {
		return zero, err
	}
	want, err := NativeJointReviewHash(plan)
	if err != nil {
		return zero, err
	}
	if in.ReviewedBundleHash != want {
		return zero, reject("REVIEW_MISMATCH", nil)
	}
	now := s.now()
	if !now.Before(plan.ExpiresAt) {
		return zero, reject("PLAN_EXPIRED", nil)
	}
	if plan.Result.Status != "OPTIMAL" || len(plan.Result.Children) == 0 {
		return zero, reject("JOINT_NOT_EXECUTABLE", nil)
	}
	if _, err = ReplayNativeJointPlan(plan); err != nil {
		return zero, err
	}
	deps := plan.Dependencies
	if fv != deps.FundsVersion || sv != deps.SourceEvidenceVersion || env.WriterGeneration != deps.WriterGeneration || env.ControlVersion != deps.ControlVersion {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	snapshot, err := s.loadTemplateSnapshot(ctx, tx, p, now, plan.Input.ProtectionForecast.Timezone)
	if err != nil {
		return zero, err
	}
	if snapshot.Facts.SourceHash != plan.SourceHash {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	_, refs, err := nativeJointGoals(snapshot)
	if err != nil {
		return zero, err
	}
	a, _ := encode(refs)
	b, _ := encode(plan.PolicyReferences)
	if string(a) != string(b) {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	for _, ref := range refs {
		if err = ensureGoalAvailable(ctx, tx, p, ref.GoalID); err != nil {
			return zero, err
		}
	}
	ledger, err := s.Store.LoadNativeLedger(ctx, tx, env.ID, p.UserID, now)
	if err != nil {
		return zero, err
	}
	requests := make([]bankv2.Request, len(plan.Result.Children))
	confirmationIDs := make([]funds.ID, len(requests))
	previewIDs := make([]funds.ID, len(requests))
	childHashes := make([]string, len(requests))
	for i, child := range plan.Result.Children {
		// A bridge needs a separate transfer and an observed arrival. It must
		// never be smuggled into the bank's same-account allocation primitive.
		if child.SourceAccountID != child.DestinationAccountID {
			return zero, reject("JOINT_BRIDGE_REQUIRED", nil)
		}
		opID, e := newID()
		if e != nil {
			return zero, e
		}
		permitID, e := newID()
		if e != nil {
			return zero, e
		}
		confirmationIDs[i], err = newID()
		if err != nil {
			return zero, err
		}
		previewIDs[i], err = newID()
		if err != nil {
			return zero, err
		}
		uses := make([]bankv2.OriginUse, len(child.Uses))
		for j, use := range child.Uses {
			uses[j] = bankv2.OriginUse{OriginID: use.OriginID.String(), AccountID: use.AccountID.String(), Amount: use.Amount, Bucket: "AVAILABLE"}
		}
		effect := bankv2.Effect{Kind: bankv2.Allocate, BusinessKey: fmt.Sprintf("%s:%d", plan.ID.String(), child.Number), Amount: child.Amount, CashUses: []bankv2.CashUse{{AccountID: child.SourceAccountID.String(), Amount: child.Amount}}, OriginUses: uses, ReturnUses: []bankv2.ReturnUse{}, DestinationGoalID: child.GoalID.String(), LatestArrivalAt: plan.ExpiresAt}
		var version uint64
		for _, ref := range refs {
			if ref.VersionID == child.VersionID && ref.PolicyID == child.PolicyID {
				version = ref.Version
			}
		}
		requests[i] = bankv2.Request{Protocol: bankv2.Protocol, Namespace: s.Namespace, Key: opID.String(), OperationID: opID.String(), UserID: p.UserID.String(), PermitID: permitID.String(), ReferenceHash: want, RequestedAt: now, ExpiresAt: plan.ExpiresAt, Effect: effect, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: confirmationIDs[i].String(), Version: 1, GoalID: child.GoalID.String(), PolicyID: child.PolicyID.String(), PolicyVersion: int64(version)}}}
		if err = requests[i].Validate(); err != nil {
			return zero, reject("JOINT_PLAN_INVALID", err)
		}
		childHashes[i], err = effect.Digest()
		if err != nil {
			return zero, err
		}
		ledger.Income, err = ledger.Income.Reserve(opID, child.Uses, funds.AllocateGoal, now, nil)
		if err != nil {
			return zero, reject("FUNDS_UNAVAILABLE", err)
		}
		ledger.Holds = append(ledger.Holds, postgres.NativeHold{OperationID: opID, ResourceKind: "CASH", ResourceID: child.SourceAccountID.String(), Amount: child.Amount, State: funds.Reserved})
	}
	approval := nativeJointApproval{"native-joint-user-confirmation-v1", "USER", env.ID, p.UserID, p.SessionID, plan.ID, want, childHashes, now, plan.ExpiresAt}
	approvalRaw, err := encode(approval)
	if err != nil {
		return zero, err
	}
	hashesRaw, _ := encode(childHashes)
	refsRaw, _ := encode(refs)
	depsRaw, _ := encode(deps)
	for i, request := range requests {
		child := plan.Result.Children[i]
		bankRaw, e := request.CanonicalBytes()
		if e != nil {
			return zero, e
		}
		bankHash, e := request.Digest()
		if e != nil {
			return zero, e
		}
		preview := nativeExecutionPreview{"native-joint-execution-preview-v1", plan.ID, want, child, request}
		previewRaw, e := encode(preview)
		if e != nil {
			return zero, e
		}
		_, err = tx.Exec(ctx, `INSERT INTO native_previews(environment_id,user_id,id,kind,original,original_hash,effect_hash,policy_refs,source_hash,dependencies,dependencies_hash,bank_request,bank_request_hash,created_at,expires_at) VALUES($1,$2,$3,'JOINT_ALLOCATE',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`, env.ID.String(), p.UserID.String(), previewIDs[i].String(), previewRaw, Hash(previewRaw), childHashes[i], refsRaw, plan.SourceHash, depsRaw, Hash(depsRaw), bankRaw, bankHash, now, plan.ExpiresAt)
		if err != nil {
			return zero, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO native_confirmations(environment_id,user_id,id,preview_id,session_id,role,policy_id,policy_version,effect_hash,child_effect_hashes,original,original_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,'EXECUTE',$6,$7,$8,$9,$10,$11,$12,$13)`, env.ID.String(), p.UserID.String(), confirmationIDs[i].String(), previewIDs[i].String(), p.SessionID.String(), child.PolicyID.String(), request.Authorities[0].PolicyVersion, childHashes[i], hashesRaw, approvalRaw, Hash(approvalRaw), now, plan.ExpiresAt)
		if err != nil {
			return zero, err
		}
		var previous any
		if i > 0 {
			previous = requests[i-1].OperationID
		}
		childKey := key
		if i > 0 {
			childKey = fmt.Sprintf("%s_%d", key, i+1)
		}
		_, err = tx.Exec(ctx, `INSERT INTO native_operations(environment_id,user_id,id,command_kind,idempotency_key,request,request_hash,bank_namespace,bank_key,bank_request,bank_request_hash,effect_hash,dependencies,dependencies_hash,plan_id,preview_id,group_id,child_number,previous_operation_id,subject_goal_ids,mode,confirmation_id,permit_id,reserved_funds_version,created_at,updated_at) VALUES($1,$2,$3,'JOINT_ALLOCATE',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$14,$16,$17,ARRAY[$18::uuid],'ONCE',$19,$20,$21,$22,$22)`, env.ID.String(), p.UserID.String(), request.OperationID, childKey, raw, Hash(raw), s.Namespace, request.Key, bankRaw, bankHash, childHashes[i], depsRaw, Hash(depsRaw), plan.ID.String(), previewIDs[i].String(), child.Number, previous, child.GoalID.String(), confirmationIDs[i].String(), request.PermitID, fv+1, now)
		if err != nil {
			return zero, err
		}
		job, e := newID()
		if e != nil {
			return zero, e
		}
		_, err = tx.Exec(ctx, `INSERT INTO native_jobs(environment_id,user_id,id,operation_id,next_attempt_at) VALUES($1,$2,$3,$4,clock_timestamp())`, env.ID.String(), p.UserID.String(), job.String(), request.OperationID)
		if err != nil {
			return zero, err
		}
	}
	if err = s.Store.PersistNativeLedger(ctx, tx, env.ID, p.UserID, ledger); err != nil {
		return zero, err
	}
	if _, err = tx.Exec(ctx, `UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2`, env.ID.String(), p.UserID.String()); err != nil {
		return zero, err
	}
	group, err := loadNativeGroup(ctx, tx, p, plan.ID)
	if err != nil {
		return zero, err
	}
	if err = tx.Commit(ctx); err != nil {
		return zero, err
	}
	return group, nil
}
