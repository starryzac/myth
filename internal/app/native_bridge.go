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
)

const NativeBridgeProtocol = "native-bridge-group-v1"

type NativeBridgePreviewRequest struct {
	PlanID funds.ID `json:"plan_id"`
}
type NativeBridgeCommand struct {
	PreviewID    funds.ID `json:"preview_id"`
	ReviewedHash string   `json:"reviewed_hash"`
}
type NativeBridgeResources struct {
	IncomeUses           []funds.IncomeUse     `json:"income_uses"`
	IncomeOperation      funds.IncomeOperation `json:"income_operation"`
	DestinationAccountID *funds.ID             `json:"destination_account_id,omitempty"`
	CashAccountID        funds.ID              `json:"cash_account_id"`
	Amount               funds.Money           `json:"amount_cents"`
}
type NativeBridgeStep struct {
	Number              int                   `json:"number"`
	ChildNumber         int                   `json:"child_number"`
	OperationID         funds.ID              `json:"operation_id"`
	PreviousOperationID *funds.ID             `json:"previous_operation_id,omitempty"`
	ConfirmationID      funds.ID              `json:"confirmation_id"`
	GoalID              funds.ID              `json:"goal_id"`
	InitialSource       bool                  `json:"initial_source"`
	CommandKind         string                `json:"command_kind"`
	Request             bankv2.Request        `json:"bank_request"`
	RequestHash         string                `json:"bank_request_hash"`
	EffectHash          string                `json:"effect_hash"`
	Resources           NativeBridgeResources `json:"resources"`
}
type NativeBridgePreview struct {
	Protocol      string             `json:"protocol"`
	ID            funds.ID           `json:"id"`
	PlanID        funds.ID           `json:"plan_id"`
	EnvironmentID funds.ID           `json:"environment_id"`
	UserID        funds.ID           `json:"user_id"`
	SessionID     funds.ID           `json:"session_id"`
	PlanHash      string             `json:"plan_hash"`
	ReviewHash    string             `json:"review_hash"`
	CreatedAt     time.Time          `json:"created_at"`
	ExpiresAt     time.Time          `json:"expires_at"`
	Steps         []NativeBridgeStep `json:"steps"`
}
type nativeBridgeApproval struct {
	Protocol         string    `json:"protocol"`
	PrincipalKind    string    `json:"principal_kind"`
	EnvironmentID    funds.ID  `json:"environment_id"`
	UserID           funds.ID  `json:"user_id"`
	SessionID        funds.ID  `json:"session_id"`
	GroupID          funds.ID  `json:"group_id"`
	PlanID           funds.ID  `json:"plan_id"`
	ReviewedHash     string    `json:"reviewed_hash"`
	StepEffectHashes []string  `json:"step_effect_hashes"`
	ConfirmedAt      time.Time `json:"confirmed_at"`
	ExpiresAt        time.Time `json:"expires_at"`
}
type NativeBridgeStepState struct {
	Step      NativeBridgeStep `json:"step"`
	State     string           `json:"state"`
	ReceiptID *string          `json:"receipt_id,omitempty"`
}
type NativeBridgeGroup struct {
	ID     funds.ID                `json:"id"`
	PlanID funds.ID                `json:"plan_id"`
	State  string                  `json:"state"`
	Steps  []NativeBridgeStepState `json:"steps"`
}

func nativeBridgeReviewHash(preview NativeBridgePreview) (string, error) {
	preview.ReviewHash = ""
	raw, err := encode(preview)
	return Hash(raw), err
}

// PreviewNativeBridge exposes both economic transfer and goal assignment.
// No client can supply a bank request or claim that future cash has arrived.
func (s *Service) PreviewNativeBridge(ctx context.Context, p Principal, in NativeBridgePreviewRequest) (NativeBridgePreview, error) {
	var out NativeBridgePreview
	if err := s.auditHuman(ctx, p); err != nil {
		return out, err
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return out, err
	}
	defer rollback(ctx, tx)
	plan, err := loadNativeJointPlan(ctx, tx, p, in.PlanID)
	if err != nil {
		return out, err
	}
	if _, err = ReplayNativeJointPlan(plan); err != nil {
		return out, err
	}
	now := s.now()
	if !now.Before(plan.ExpiresAt) || plan.Result.Status != "OPTIMAL" || len(plan.Result.Children) == 0 {
		return out, reject("PLAN_EXPIRED", nil)
	}
	if len(plan.Result.Children) > 8 {
		return out, reject("BRIDGE_CAPACITY", nil)
	}
	if err = s.bridgeCurrentBasis(ctx, tx, p, plan, now); err != nil {
		return out, err
	}
	group, err := newID()
	if err != nil {
		return out, err
	}
	planHash, err := NativeJointReviewHash(plan)
	if err != nil {
		return out, err
	}
	out = NativeBridgePreview{Protocol: NativeBridgeProtocol, ID: group, PlanID: plan.ID, EnvironmentID: p.EnvironmentID, UserID: p.UserID, SessionID: p.SessionID, PlanHash: planHash, CreatedAt: now, ExpiresAt: plan.ExpiresAt, Steps: []NativeBridgeStep{}}
	for _, child := range plan.Result.Children {
		var policyVersion uint64
		for _, ref := range plan.PolicyReferences {
			if ref.VersionID == child.VersionID && ref.PolicyID == child.PolicyID {
				policyVersion = ref.Version
			}
		}
		phases := []bankv2.Kind{bankv2.Allocate}
		if child.SourceAccountID != child.DestinationAccountID {
			phases = []bankv2.Kind{bankv2.Transfer, bankv2.Allocate}
		}
		for phaseIndex, phase := range phases {
			op, err := newID()
			if err != nil {
				return out, err
			}
			permit, err := newID()
			if err != nil {
				return out, err
			}
			confirmation, err := newID()
			if err != nil {
				return out, err
			}
			step := NativeBridgeStep{Number: len(out.Steps) + 1, ChildNumber: child.Number, OperationID: op, ConfirmationID: confirmation, GoalID: child.GoalID, InitialSource: phaseIndex == 0, CommandKind: "BRIDGE_ALLOCATE"}
			if len(out.Steps) > 0 {
				previous := out.Steps[len(out.Steps)-1].OperationID
				step.PreviousOperationID = &previous
			}
			account := child.SourceAccountID
			operation := funds.AllocateGoal
			var destination *funds.ID
			if phase == bankv2.Transfer {
				step.CommandKind = "BRIDGE_TRANSFER"
				operation = funds.TransferInternal
				d := child.DestinationAccountID
				destination = &d
			} else {
				account = child.DestinationAccountID
			}
			uses := make([]funds.IncomeUse, len(child.Uses))
			bankUses := make([]bankv2.OriginUse, len(child.Uses))
			for i, use := range child.Uses {
				if phase == bankv2.Allocate {
					use.AccountID = account
					use.FragmentID = funds.LocationID(use.OriginID, account)
				}
				uses[i] = use
				bankUses[i] = bankv2.OriginUse{OriginID: use.OriginID.String(), AccountID: account.String(), Amount: use.Amount, Bucket: "AVAILABLE"}
			}
			step.Resources = NativeBridgeResources{IncomeUses: uses, IncomeOperation: operation, DestinationAccountID: destination, CashAccountID: account, Amount: child.Amount}
			effect := bankv2.Effect{Kind: phase, BusinessKey: fmt.Sprintf("bridge:%s:%d", group.String(), step.Number), Amount: child.Amount, CashUses: []bankv2.CashUse{{AccountID: account.String(), Amount: child.Amount}}, OriginUses: bankUses, ReturnUses: []bankv2.ReturnUse{}, LatestArrivalAt: out.ExpiresAt}
			if phase == bankv2.Transfer {
				effect.DestinationAccountID = child.DestinationAccountID.String()
			} else {
				effect.DestinationGoalID = child.GoalID.String()
			}
			step.Request = bankv2.Request{Protocol: bankv2.Protocol, Namespace: s.Namespace, Key: op.String(), OperationID: op.String(), UserID: p.UserID.String(), PermitID: permit.String(), ReferenceHash: planHash, RequestedAt: now, ExpiresAt: out.ExpiresAt, Effect: effect, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: confirmation.String(), Version: 1, GoalID: child.GoalID.String(), PolicyID: child.PolicyID.String(), PolicyVersion: int64(policyVersion)}}}
			if step.Request.Validate() != nil {
				return out, reject("BRIDGE_PLAN_INVALID", nil)
			}
			step.RequestHash, err = step.Request.Digest()
			if err != nil {
				return out, err
			}
			step.EffectHash, err = effect.Digest()
			if err != nil {
				return out, err
			}
			out.Steps = append(out.Steps, step)
		}
	}
	out.ReviewHash, err = nativeBridgeReviewHash(out)
	if err != nil {
		return out, err
	}
	raw, err := encode(out)
	if err != nil {
		return out, err
	}
	if len(raw) > 1048576 || len(out.Steps) > 16 {
		return out, reject("BRIDGE_CAPACITY", nil)
	}
	if err = tx.Commit(ctx); err != nil {
		return out, err
	}
	if err = s.human(ctx, s.Pool, p); err != nil {
		return out, err
	}
	refs, _ := encode(plan.PolicyReferences)
	deps, _ := encode(plan.Dependencies)
	first, _ := out.Steps[0].Request.CanonicalBytes()
	_, err = s.writerExec(ctx, `INSERT INTO native_previews(environment_id,user_id,id,kind,original,original_hash,effect_hash,policy_refs,source_hash,dependencies,dependencies_hash,bank_request,bank_request_hash,created_at,expires_at) VALUES($1,$2,$3,'BRIDGE_GROUP',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`, p.EnvironmentID.String(), p.UserID.String(), out.ID.String(), raw, Hash(raw), out.ReviewHash, refs, plan.SourceHash, deps, Hash(deps), first, out.Steps[0].RequestHash, now, out.ExpiresAt)
	return out, err
}

func (s *Service) bridgeCurrentBasis(ctx context.Context, tx pgx.Tx, p Principal, plan NativeJointPlan, now time.Time) error {
	var fv, sv, wg, cv uint64
	if err := tx.QueryRow(ctx, `SELECT u.funds_version,u.source_version,e.writer_generation,e.control_version FROM users u JOIN environments e ON e.id=u.environment_id WHERE u.environment_id=$1 AND u.id=$2`, p.EnvironmentID.String(), p.UserID.String()).Scan(&fv, &sv, &wg, &cv); err != nil {
		return err
	}
	d := plan.Dependencies
	if fv != d.FundsVersion || sv != d.SourceEvidenceVersion || wg != d.WriterGeneration || cv != d.ControlVersion {
		return reject("SOURCE_CHANGED", nil)
	}
	snapshot, err := s.loadTemplateSnapshot(ctx, tx, p, now, plan.Input.ProtectionForecast.Timezone)
	if err != nil {
		return err
	}
	if snapshot.Facts.SourceHash != plan.SourceHash {
		return reject("SOURCE_CHANGED", nil)
	}
	_, refs, err := nativeJointGoals(snapshot)
	if err != nil {
		return err
	}
	a, _ := encode(refs)
	b, _ := encode(plan.PolicyReferences)
	if string(a) != string(b) {
		return reject("SOURCE_CHANGED", nil)
	}
	for _, ref := range refs {
		if err = ensureGoalAvailable(ctx, tx, p, ref.GoalID); err != nil {
			return err
		}
	}
	return nil
}

func loadNativeBridgePreview(ctx context.Context, q queryRow, p Principal, id funds.ID) (NativeBridgePreview, error) {
	var out NativeBridgePreview
	var raw []byte
	var hash string
	if err := q.QueryRow(ctx, `SELECT original,original_hash FROM native_previews WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND kind='BRIDGE_GROUP'`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&raw, &hash); err != nil {
		return out, reject("NOT_FOUND", err)
	}
	if Hash(raw) != hash || decode(raw, &out) != nil || out.Protocol != NativeBridgeProtocol || out.ID != id || out.EnvironmentID != p.EnvironmentID || out.UserID != p.UserID || len(out.Steps) < 1 || len(out.Steps) > 16 {
		return out, reject("EVIDENCE_INVALID", nil)
	}
	want, err := nativeBridgeReviewHash(out)
	if err != nil || want != out.ReviewHash {
		return out, reject("EVIDENCE_INVALID", err)
	}
	for i, step := range out.Steps {
		rh, e := step.Request.Digest()
		eh, e2 := step.Request.Effect.Digest()
		if e != nil || e2 != nil || rh != step.RequestHash || eh != step.EffectHash || step.Number != i+1 || step.Request.OperationID != step.OperationID.String() || step.Request.UserID != p.UserID.String() || step.Request.ReferenceHash != out.PlanHash {
			return out, reject("EVIDENCE_INVALID", nil)
		}
	}
	return out, nil
}

// AcceptNativeBridge freezes all intents but reserves only real source cash.
// An arrival allocation becomes a NEW T1 only inside its transfer's actual T3.
func (s *Service) AcceptNativeBridge(ctx context.Context, p Principal, key string, in NativeBridgeCommand) (NativeBridgeGroup, error) {
	var zero NativeBridgeGroup
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
	var oldID, oldHash string
	err = tx.QueryRow(ctx, `SELECT id::text,request_hash FROM native_group_batches WHERE environment_id=$1 AND user_id=$2 AND idempotency_key=$3`, env.ID.String(), p.UserID.String(), key).Scan(&oldID, &oldHash)
	if err == nil {
		if oldHash != Hash(raw) {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		id, e := funds.ParseID(oldID)
		if e != nil {
			return zero, e
		}
		return s.loadNativeBridgeGroup(ctx, tx, p, id)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return zero, err
	}
	preview, err := loadNativeBridgePreview(ctx, tx, p, in.PreviewID)
	if err != nil {
		return zero, err
	}
	if preview.ReviewHash != in.ReviewedHash {
		return zero, reject("REVIEW_MISMATCH", nil)
	}
	now := s.now()
	if !now.Before(preview.ExpiresAt) || preview.CreatedAt.After(now) {
		return zero, reject("PLAN_EXPIRED", nil)
	}
	plan, err := loadNativeJointPlan(ctx, tx, p, preview.PlanID)
	if err != nil {
		return zero, err
	}
	if _, err = ReplayNativeJointPlan(plan); err != nil {
		return zero, err
	}
	ph, err := NativeJointReviewHash(plan)
	if err != nil || ph != preview.PlanHash {
		return zero, reject("EVIDENCE_INVALID", err)
	}
	if err = s.bridgeCurrentBasis(ctx, tx, p, plan, now); err != nil {
		return zero, err
	}
	effects := make([]string, len(preview.Steps))
	for i, step := range preview.Steps {
		effects[i] = step.EffectHash
	}
	approval := nativeBridgeApproval{NativeBridgeProtocol + "/USER", "USER", env.ID, p.UserID, p.SessionID, preview.ID, plan.ID, preview.ReviewHash, effects, now, preview.ExpiresAt}
	approvalRaw, err := encode(approval)
	if err != nil {
		return zero, err
	}
	effectsRaw, _ := encode(effects)
	_, err = tx.Exec(ctx, `INSERT INTO native_group_batches(environment_id,user_id,id,plan_id,preview_id,idempotency_key,request,request_hash,original,original_hash,session_id,reserved_funds_version,source_version,writer_generation,control_version,created_at,expires_at) VALUES($1,$2,$3,$4,$3,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)`, env.ID.String(), p.UserID.String(), preview.ID.String(), plan.ID.String(), key, raw, Hash(raw), approvalRaw, Hash(approvalRaw), p.SessionID.String(), fv+1, sv, env.WriterGeneration, env.ControlVersion, now, preview.ExpiresAt)
	if err != nil {
		return zero, err
	}
	for _, step := range preview.Steps {
		_, err = tx.Exec(ctx, `INSERT INTO native_confirmations(environment_id,user_id,id,preview_id,session_id,role,policy_id,policy_version,effect_hash,child_effect_hashes,original,original_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,'EXECUTE',$6,$7,$8,$9,$10,$11,$12,$13)`, env.ID.String(), p.UserID.String(), step.ConfirmationID.String(), preview.ID.String(), p.SessionID.String(), step.Request.Authorities[0].PolicyID, step.Request.Authorities[0].PolicyVersion, step.EffectHash, effectsRaw, approvalRaw, Hash(approvalRaw), now, preview.ExpiresAt)
		if err != nil {
			return zero, err
		}
		bankRaw, _ := step.Request.CanonicalBytes()
		resources, _ := encode(step.Resources)
		var previous any
		if step.PreviousOperationID != nil {
			previous = step.PreviousOperationID.String()
		}
		_, err = tx.Exec(ctx, `INSERT INTO native_next_steps(environment_id,user_id,group_id,number,child_number,operation_id,previous_operation_id,command_kind,initial_source,bank_request,bank_request_hash,effect_hash,resources,resources_hash,confirmation_id,goal_id) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)`, env.ID.String(), p.UserID.String(), preview.ID.String(), step.Number, step.ChildNumber, step.OperationID.String(), previous, step.CommandKind, step.InitialSource, bankRaw, step.RequestHash, step.EffectHash, resources, Hash(resources), step.ConfirmationID.String(), step.GoalID.String())
		if err != nil {
			return zero, err
		}
	}
	ledger, err := s.Store.LoadNativeLedger(ctx, tx, env.ID, p.UserID, now)
	if err != nil {
		return zero, err
	}
	for _, step := range preview.Steps {
		if !step.InitialSource {
			continue
		}
		if err = s.bridgeInsertOperation(ctx, tx, p, preview, plan, step, key, raw, fv+1, now); err != nil {
			return zero, err
		}
		ledger, err = bridgeReserve(ledger, step, now)
		if err != nil {
			return zero, err
		}
	}
	if err = s.Store.PersistNativeLedger(ctx, tx, env.ID, p.UserID, ledger); err != nil {
		return zero, err
	}
	if err = bridgeCreateJob(ctx, tx, p, preview.Steps[0].OperationID); err != nil {
		return zero, err
	}
	if _, err = tx.Exec(ctx, `UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2`, env.ID.String(), p.UserID.String()); err != nil {
		return zero, err
	}
	group, err := s.loadNativeBridgeGroup(ctx, tx, p, preview.ID)
	if err != nil {
		return zero, err
	}
	if err = tx.Commit(ctx); err != nil {
		return zero, err
	}
	return group, nil
}

func (s *Service) bridgeInsertOperation(ctx context.Context, tx pgx.Tx, p Principal, preview NativeBridgePreview, plan NativeJointPlan, step NativeBridgeStep, key string, command []byte, reservedFV uint64, now time.Time) error {
	bankRaw, err := step.Request.CanonicalBytes()
	if err != nil {
		return err
	}
	deps, _ := encode(plan.Dependencies)
	var previous any
	if step.PreviousOperationID != nil {
		var exists bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=$3)`, p.EnvironmentID.String(), p.UserID.String(), step.PreviousOperationID.String()).Scan(&exists); err != nil {
			return err
		}
		if exists {
			previous = step.PreviousOperationID.String()
		}
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_operations(environment_id,user_id,id,command_kind,idempotency_key,request,request_hash,bank_namespace,bank_key,bank_request,bank_request_hash,effect_hash,dependencies,dependencies_hash,plan_id,preview_id,group_id,child_number,previous_operation_id,subject_goal_ids,mode,confirmation_id,permit_id,reserved_funds_version,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$16,$17,$18,ARRAY[$19::uuid],'ONCE',$20,$21,$22,$23,$23)`, p.EnvironmentID.String(), p.UserID.String(), step.OperationID.String(), step.CommandKind, fmt.Sprintf("%s_%d", key, step.Number), command, Hash(command), s.Namespace, step.Request.Key, bankRaw, step.RequestHash, step.EffectHash, deps, Hash(deps), plan.ID.String(), preview.ID.String(), step.Number, previous, step.GoalID.String(), step.ConfirmationID.String(), step.Request.PermitID, reservedFV, now)
	return err
}
func bridgeReserve(ledger postgres.NativeLedger, step NativeBridgeStep, now time.Time) (postgres.NativeLedger, error) {
	var err error
	ledger.Income, err = ledger.Income.Reserve(step.OperationID, step.Resources.IncomeUses, step.Resources.IncomeOperation, now, step.Resources.DestinationAccountID)
	if err != nil {
		return ledger, reject("FUNDS_UNAVAILABLE", err)
	}
	ledger.Holds = append(ledger.Holds, postgres.NativeHold{OperationID: step.OperationID, ResourceKind: "CASH", ResourceID: step.Resources.CashAccountID.String(), Amount: step.Resources.Amount, State: funds.Reserved})
	return ledger, nil
}
func bridgeCreateJob(ctx context.Context, tx pgx.Tx, p Principal, operation funds.ID) error {
	id, err := newID()
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_jobs(environment_id,user_id,id,operation_id,next_attempt_at) VALUES($1,$2,$3,$4,clock_timestamp()) ON CONFLICT(environment_id,user_id,operation_id) DO NOTHING`, p.EnvironmentID.String(), p.UserID.String(), id.String(), operation.String())
	return err
}

func (s *Service) loadNativeBridgeGroup(ctx context.Context, tx pgx.Tx, p Principal, id funds.ID) (NativeBridgeGroup, error) {
	var out NativeBridgeGroup
	preview, err := loadNativeBridgePreview(ctx, tx, p, id)
	if err != nil {
		return out, err
	}
	var accepted bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM native_group_batches WHERE environment_id=$1 AND user_id=$2 AND id=$3)`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&accepted); err != nil {
		return out, err
	}
	if !accepted {
		return out, reject("NOT_FOUND", nil)
	}
	out = NativeBridgeGroup{ID: id, PlanID: preview.PlanID, State: "IN_PROGRESS", Steps: []NativeBridgeStepState{}}
	settled, terminal := 0, 0
	stopped := false
	for _, step := range preview.Steps {
		state := NativeBridgeStepState{Step: step, State: "AWAITING_ARRIVAL"}
		err = tx.QueryRow(ctx, `SELECT status,receipt_id::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), step.OperationID.String()).Scan(&state.State, &state.ReceiptID)
		if errors.Is(err, pgx.ErrNoRows) {
			if stopped {
				state.State = "NOT_ACTIVATED_STOPPED"
				terminal++
			} else if !s.now().Before(preview.ExpiresAt) {
				state.State = "NOT_ACTIVATED_EXPIRED"
				terminal++
			}
		} else if err != nil {
			return out, err
		}
		if state.State == "SETTLED" {
			settled++
			terminal++
		} else if state.State == "REJECTED" || state.State == "CANCELLED" {
			terminal++
			stopped = true
		}
		out.Steps = append(out.Steps, state)
	}
	if settled == len(out.Steps) {
		out.State = "SETTLED"
	} else if terminal == len(out.Steps) && !s.now().Before(preview.ExpiresAt) && settled > 0 {
		out.State = "PARTIAL_EXPIRED"
	} else if terminal == len(out.Steps) {
		out.State = "STOPPED"
	}
	return out, nil
}
func (s *Service) NativeBridgeGroup(ctx context.Context, p Principal, id funds.ID) (NativeBridgeGroup, error) {
	if err := s.auditHuman(ctx, p); err != nil {
		return NativeBridgeGroup{}, err
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return NativeBridgeGroup{}, err
	}
	defer rollback(ctx, tx)
	return s.loadNativeBridgeGroup(ctx, tx, p, id)
}
