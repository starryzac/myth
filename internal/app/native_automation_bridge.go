package app

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const nativeStandingBridgeProtocol = "native-standing-bridge-v1"
const nativeStandingBridgeTransfer = "STANDING_BRIDGE_TRANSFER"
const nativeStandingBridgeAllocate = "STANDING_BRIDGE_ALLOCATE"

func nativeStandingBridgeKind(kind string) bool {
	return kind == nativeStandingBridgeTransfer || kind == nativeStandingBridgeAllocate
}

// Every future request and resource identity is fixed before the first bank
// call. A future allocation is a declared step, not an existing reservation.
type nativeStandingBridgeGroup struct {
	Protocol           string                            `json:"protocol"`
	Scope              policy.TemplateOwnerScope         `json:"scope"`
	Occurrence         NativeOccurrence                  `json:"occurrence"`
	Grant              NativeStandingGrant               `json:"grant"`
	GrantStateSequence uint64                            `json:"grant_state_sequence"`
	Candidate          nativeStandingAllocationCandidate `json:"candidate"`
	Input              NativeJointInput                  `json:"input"`
	Result             planning.JointPlan                `json:"result"`
	GoalVersions       map[string]uint64                 `json:"goal_versions"`
	Dependencies       planning.DependencyVector         `json:"dependencies"`
	CreatedAt          time.Time                         `json:"created_at"`
	ExpiresAt          time.Time                         `json:"expires_at"`
	Steps              []NativeBridgeStep                `json:"steps"`
}

// Normalize only the self-reference fields of a copy. The commitment still
// binds the complete group, all economic terms, permits, keys and resources.
func standingBridgeCommitment(g nativeStandingBridgeGroup) (string, error) {
	g.Steps = append([]NativeBridgeStep(nil), g.Steps...)
	for i := range g.Steps {
		g.Steps[i].Request.ReferenceHash = ""
		g.Steps[i].RequestHash = ""
	}
	raw, e := encode(g)
	return Hash(raw), e
}

func standingBridgeSchema(ctx context.Context, q queryRow) error {
	var exists bool
	if e := q.QueryRow(ctx, `SELECT to_regclass('native_standing_bridge_groups') IS NOT NULL`).Scan(&exists); e != nil {
		return e
	}
	if !exists {
		return reject("NATIVE_STANDING_BRIDGE_SCHEMA_REQUIRED", nil)
	}
	return nil
}

func buildStandingBridge(g nativeStandingBridgeGroup, namespace string) (nativeStandingBridgeGroup, error) {
	g.Steps = []NativeBridgeStep{}
	for _, child := range g.Result.Children {
		phases := []bankv2.Kind{bankv2.Allocate}
		if child.SourceAccountID != child.DestinationAccountID {
			phases = []bankv2.Kind{bankv2.Transfer, bankv2.Allocate}
		}
		var ref policy.NativePolicyReference
		for _, r := range g.Grant.Terms.Policies {
			if r.GoalID != nil && *r.GoalID == child.GoalID {
				ref = r
			}
		}
		if ref.PolicyID == (funds.ID{}) || len(child.Uses) != 1 {
			return g, reject("EVIDENCE_INVALID", nil)
		}
		for phaseIndex, phase := range phases {
			op, e := newID()
			if e != nil {
				return g, e
			}
			permit, e := newID()
			if e != nil {
				return g, e
			}
			step := NativeBridgeStep{Number: len(g.Steps) + 1, ChildNumber: child.Number, OperationID: op, GoalID: child.GoalID, InitialSource: phaseIndex == 0, CommandKind: nativeStandingBridgeAllocate}
			if len(g.Steps) > 0 {
				prev := g.Steps[len(g.Steps)-1].OperationID
				step.PreviousOperationID = &prev
			}
			account := child.SourceAccountID
			operation := funds.AllocateGoal
			var destination *funds.ID
			if phase == bankv2.Transfer {
				step.CommandKind = nativeStandingBridgeTransfer
				operation = funds.TransferInternal
				v := child.DestinationAccountID
				destination = &v
			} else {
				account = child.DestinationAccountID
			}
			use := child.Uses[0]
			use.AccountID = account
			use.FragmentID = funds.LocationID(use.OriginID, account)
			step.Resources = NativeBridgeResources{IncomeUses: []funds.IncomeUse{use}, IncomeOperation: operation, DestinationAccountID: destination, CashAccountID: account, Amount: child.Amount}
			effect := bankv2.Effect{Kind: phase, BusinessKey: fmt.Sprintf("%s:%d", g.Occurrence.Key, step.Number), Amount: child.Amount, CashUses: []bankv2.CashUse{{AccountID: account.String(), Amount: child.Amount}}, OriginUses: []bankv2.OriginUse{{OriginID: use.OriginID.String(), AccountID: account.String(), Amount: child.Amount, Bucket: "AVAILABLE"}}, ReturnUses: []bankv2.ReturnUse{}, LatestArrivalAt: g.ExpiresAt}
			if phase == bankv2.Transfer {
				effect.DestinationAccountID = child.DestinationAccountID.String()
			} else {
				effect.DestinationGoalID = child.GoalID.String()
			}
			step.Request = bankv2.Request{Protocol: bankv2.Protocol, Namespace: namespace, Key: op.String(), OperationID: op.String(), UserID: g.Scope.UserID.String(), PermitID: permit.String(), RequestedAt: g.CreatedAt, ExpiresAt: g.ExpiresAt, Effect: effect, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: g.Grant.ID.String(), Version: int64(g.Grant.Version), GoalID: child.GoalID.String(), PolicyID: ref.PolicyID.String(), PolicyVersion: int64(ref.Version)}}}
			step.EffectHash, e = effect.Digest()
			if e != nil {
				return g, e
			}
			g.Steps = append(g.Steps, step)
		}
	}
	commitment, e := standingBridgeCommitment(g)
	if e != nil {
		return g, e
	}
	for i := range g.Steps {
		g.Steps[i].Request.ReferenceHash = commitment
		g.Steps[i].RequestHash, e = g.Steps[i].Request.Digest()
		if e != nil {
			return g, e
		}
	}
	return g, nil
}

func replayStandingBridge(g nativeStandingBridgeGroup, namespace string) error {
	if g.Protocol != nativeStandingBridgeProtocol || g.Scope != g.Grant.Scope || g.Scope != g.Occurrence.Scope || g.Grant.Terms.Family != policy.NativeJointGoals || g.Occurrence.Family != policy.NativeJointGoals || g.Occurrence.GrantID != g.Grant.ID || g.Occurrence.GrantVersion != g.Grant.Version || g.Grant.Terms.Validate() != nil || g.Grant.PrincipalKind != "USER" || g.Grant.SessionID == (funds.ID{}) || !g.Grant.Terms.EffectiveFrom.Equal(g.Grant.ConfirmedAt) || len(g.Steps) == 0 || len(g.Steps) > 16 || len(g.Result.Children) > 8 || !g.ExpiresAt.After(g.CreatedAt) || g.ExpiresAt.After(g.CreatedAt.Add(2*time.Minute)) || g.Dependencies.Validate() != nil {
		return reject("EVIDENCE_INVALID", nil)
	}
	if len(g.GoalVersions) != len(g.Grant.Terms.GoalIDs) {
		return reject("EVIDENCE_INVALID", nil)
	}
	for _, id := range g.Grant.Terms.GoalIDs {
		if g.GoalVersions[id.String()] == 0 {
			return reject("EVIDENCE_INVALID", nil)
		}
	}
	ledger, e := g.Candidate.Ledger.DomainLedger()
	if e != nil || ledger.Namespace != namespace || ledger.Income.Snapshot().UserID != g.Scope.UserID || !ledger.Income.Snapshot().AsOf.Equal(g.CreatedAt) || Hash(g.Candidate.InventoryOriginal) != g.Candidate.InventoryHash {
		return reject("EVIDENCE_INVALID", e)
	}
	f := g.Candidate.ForecastInput
	d := g.Dependencies
	snapshot := templateSnapshot{Facts: f.Facts, Versions: f.Versions, Views: g.Candidate.Policies, Retained: f.RetainedObligations, Held: f.HeldCash, UnassignedGoalCash: f.UnassignedGoalCash, ExternalLiabilities: f.ExternalLiabilities, InventoryRaw: g.Candidate.InventoryOriginal, FundsVersion: d.FundsVersion, SourceVersion: d.SourceEvidenceVersion, WriterGeneration: d.WriterGeneration, ControlVersion: d.ControlVersion, AsOf: g.CreatedAt, Timezone: g.Grant.Terms.Timezone, Native: &ledger}
	candidate, input, result, e := standingJointCalculation(snapshot, ledger, g.Grant, g.Occurrence.Source, g.Candidate.Usage, g.GoalVersions, g.CreatedAt)
	if e != nil {
		return reject("EVIDENCE_INVALID", e)
	}
	for _, pair := range [][2]any{{candidate, g.Candidate}, {input, g.Input}, {result, g.Result}} {
		a, _ := encode(pair[0])
		b, _ := encode(pair[1])
		if string(a) != string(b) {
			return reject("EVIDENCE_INVALID", nil)
		}
	}
	commitment, e := standingBridgeCommitment(g)
	if e != nil {
		return e
	}
	number := 0
	ids := map[funds.ID]bool{}
	permits := map[string]bool{}
	for _, child := range result.Children {
		phases := []bankv2.Kind{bankv2.Allocate}
		if child.SourceAccountID != child.DestinationAccountID {
			phases = []bankv2.Kind{bankv2.Transfer, bankv2.Allocate}
		}
		for phaseIndex, phase := range phases {
			if number >= len(g.Steps) {
				return reject("EVIDENCE_INVALID", nil)
			}
			step := g.Steps[number]
			number++
			if step.Number != number || step.ChildNumber != child.Number || step.GoalID != child.GoalID || step.InitialSource != (phaseIndex == 0) || ids[step.OperationID] || permits[step.Request.PermitID] || step.ConfirmationID != (funds.ID{}) {
				return reject("EVIDENCE_INVALID", nil)
			}
			ids[step.OperationID] = true
			permits[step.Request.PermitID] = true
			if number == 1 && step.PreviousOperationID != nil || number > 1 && (step.PreviousOperationID == nil || *step.PreviousOperationID != g.Steps[number-2].OperationID) {
				return reject("EVIDENCE_INVALID", nil)
			}
			account := child.DestinationAccountID
			kind := nativeStandingBridgeAllocate
			operation := funds.AllocateGoal
			var destination *funds.ID
			if phase == bankv2.Transfer {
				account = child.SourceAccountID
				kind = nativeStandingBridgeTransfer
				operation = funds.TransferInternal
				v := child.DestinationAccountID
				destination = &v
			}
			use := child.Uses[0]
			use.AccountID = account
			use.FragmentID = funds.LocationID(use.OriginID, account)
			resources := NativeBridgeResources{IncomeUses: []funds.IncomeUse{use}, IncomeOperation: operation, DestinationAccountID: destination, CashAccountID: account, Amount: child.Amount}
			a, _ := encode(resources)
			b, _ := encode(step.Resources)
			request := step.Request
			h, e := request.Digest()
			eh, e2 := request.Effect.Digest()
			var ref policy.NativePolicyReference
			for _, r := range g.Grant.Terms.Policies {
				if r.GoalID != nil && *r.GoalID == child.GoalID {
					ref = r
				}
			}
			wantEffect := bankv2.Effect{Kind: phase, BusinessKey: fmt.Sprintf("%s:%d", g.Occurrence.Key, number), Amount: child.Amount, CashUses: []bankv2.CashUse{{AccountID: account.String(), Amount: child.Amount}}, OriginUses: []bankv2.OriginUse{{OriginID: use.OriginID.String(), AccountID: account.String(), Amount: child.Amount, Bucket: "AVAILABLE"}}, ReturnUses: []bankv2.ReturnUse{}, LatestArrivalAt: g.ExpiresAt}
			if phase == bankv2.Transfer {
				wantEffect.DestinationAccountID = child.DestinationAccountID.String()
			} else {
				wantEffect.DestinationGoalID = child.GoalID.String()
			}
			want := bankv2.Request{Protocol: bankv2.Protocol, Namespace: namespace, Key: step.OperationID.String(), OperationID: step.OperationID.String(), UserID: g.Scope.UserID.String(), PermitID: request.PermitID, ReferenceHash: commitment, RequestedAt: g.CreatedAt, ExpiresAt: g.ExpiresAt, Effect: wantEffect, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: g.Grant.ID.String(), Version: int64(g.Grant.Version), GoalID: child.GoalID.String(), PolicyID: ref.PolicyID.String(), PolicyVersion: int64(ref.Version)}}}
			wh, e3 := want.Digest()
			if step.CommandKind != kind || string(a) != string(b) || e != nil || e2 != nil || e3 != nil || h != step.RequestHash || eh != step.EffectHash || h != wh {
				return reject("EVIDENCE_INVALID", nil)
			}
		}
	}
	if number != len(g.Steps) {
		return reject("EVIDENCE_INVALID", nil)
	}
	return nil
}

func loadStandingBridge(ctx context.Context, q queryRow, p Principal, operation funds.ID) (nativeStandingBridgeGroup, string, int, error) {
	var out nativeStandingBridgeGroup
	var raw []byte
	var hash, namespace string
	var number int
	e := q.QueryRow(ctx, `SELECT g.original,g.original_hash,e.namespace,s.number FROM native_standing_bridge_groups g JOIN native_standing_bridge_steps s ON(s.environment_id=g.environment_id AND s.user_id=g.user_id AND s.group_id=g.id) JOIN environments e ON(e.id=g.environment_id) WHERE g.environment_id=$1 AND g.user_id=$2 AND s.operation_id=$3`, p.EnvironmentID.String(), p.UserID.String(), operation.String()).Scan(&raw, &hash, &namespace, &number)
	if e != nil {
		return out, "", 0, e
	}
	if Hash(raw) != hash || auditJSON(raw, &out) != nil || out.Scope != nativeAutomationScope(p) || number < 1 || number > len(out.Steps) || out.Steps[number-1].OperationID != operation {
		return out, "", 0, reject("EVIDENCE_INVALID", nil)
	}
	return out, hash, number, standingBridgeVerified.verify(raw, namespace)
}

// Called under the existing occurrence/user lock. All initial sources are
// reserved together; only step one receives a job. Future allocations exist
// solely as immutable declarations until a real transfer receipt is present.
func (s *Service) admitStandingBridge(ctx context.Context, tx pgx.Tx, p Principal, c NativeOccurrenceClaim, grant NativeStandingGrantView, candidate nativeStandingAllocationCandidate, input NativeJointInput, result planning.JointPlan, versions map[string]uint64, deps planning.DependencyVector, ledger postgres.NativeLedger, now time.Time) error {
	if e := standingBridgeSchema(ctx, tx); e != nil {
		return e
	}
	expires := now.Add(2 * time.Minute)
	if grant.Grant.Terms.ExpiresAt.Before(expires) {
		expires = grant.Grant.Terms.ExpiresAt
	}
	zone, _ := policy.TemplateLocation(grant.Grant.Terms.Timezone)
	local := now.In(zone)
	midnight := time.Date(local.Year(), local.Month(), local.Day()+1, 0, 0, 0, 0, zone).UTC()
	if midnight.Before(expires) {
		expires = midnight
	}
	g, e := buildStandingBridge(nativeStandingBridgeGroup{Protocol: nativeStandingBridgeProtocol, Scope: grant.Grant.Scope, Occurrence: c.Occurrence, Grant: grant.Grant, GrantStateSequence: grant.StateSequence, Candidate: candidate, Input: input, Result: result, GoalVersions: versions, Dependencies: deps, CreatedAt: now, ExpiresAt: expires}, s.Namespace)
	if e != nil {
		return e
	}
	raw, e := encode(g)
	if e != nil || len(raw) > 8<<20 {
		return reject("NATIVE_STANDING_CAPTURE_CAPACITY", e)
	}
	hash := Hash(raw)
	budgetRaw, _ := encode(struct {
		Protocol  string `json:"protocol"`
		GroupHash string `json:"group_hash"`
	}{nativeStandingBridgeProtocol + "/budget", hash})
	if e = reserveNativeOccurrenceBudget(ctx, tx, p, c, grant.Grant, candidate.Authority.Amount, budgetRaw, now); e != nil {
		return e
	}
	_, e = tx.Exec(ctx, `INSERT INTO native_standing_bridge_groups(environment_id,user_id,id,grant_id,grant_version,original,original_hash,reserved_funds_version,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, p.EnvironmentID.String(), p.UserID.String(), c.Occurrence.ID.String(), grant.Grant.ID.String(), grant.Grant.Version, raw, hash, deps.FundsVersion+1, now, expires)
	if e != nil {
		return e
	}
	for _, step := range g.Steps {
		bankRaw, _ := step.Request.CanonicalBytes()
		resources, _ := encode(step.Resources)
		var previous any
		if step.PreviousOperationID != nil {
			previous = step.PreviousOperationID.String()
		}
		_, e = tx.Exec(ctx, `INSERT INTO native_standing_bridge_steps(environment_id,user_id,group_id,number,child_number,operation_id,previous_operation_id,initial_source,command_kind,goal_id,bank_request,bank_request_hash,effect_hash,resources,resources_hash) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`, p.EnvironmentID.String(), p.UserID.String(), c.Occurrence.ID.String(), step.Number, step.ChildNumber, step.OperationID.String(), previous, step.InitialSource, step.CommandKind, step.GoalID.String(), bankRaw, step.RequestHash, step.EffectHash, resources, Hash(resources))
		if e != nil {
			return e
		}
		if step.InitialSource {
			if e = s.insertStandingBridgeOperation(ctx, tx, p, g, hash, step, now); e != nil {
				return e
			}
			ledger, e = bridgeReserve(ledger, step, now)
			if e != nil {
				return e
			}
		}
	}
	if e = bridgeCreateJob(ctx, tx, p, g.Steps[0].OperationID); e != nil {
		return e
	}
	if e = s.Store.PersistNativeLedger(ctx, tx, p.EnvironmentID, p.UserID, ledger); e != nil {
		return e
	}
	if _, e = tx.Exec(ctx, `UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2`, p.EnvironmentID.String(), p.UserID.String()); e != nil {
		return e
	}
	if e = nativeFinishOccurrence(ctx, tx, c, "ACCEPTED", ""); e != nil {
		return e
	}
	return tx.Commit(ctx)
}

func (s *Service) insertStandingBridgeOperation(ctx context.Context, tx pgx.Tx, p Principal, g nativeStandingBridgeGroup, hash string, step NativeBridgeStep, now time.Time) error {
	bankRaw, _ := step.Request.CanonicalBytes()
	deps, _ := encode(g.Dependencies)
	refs, _ := encode(g.Grant.Terms.Policies)
	commandRaw, _ := encode(nativeStandingExecutionOriginal{Protocol: nativeStandingBridgeProtocol + "/execution", PlanID: g.Occurrence.ID, PlanHash: hash, Request: step.Request})
	preview, e := newID()
	if e != nil {
		return e
	}
	_, e = tx.Exec(ctx, `INSERT INTO native_previews(environment_id,user_id,id,kind,original,original_hash,effect_hash,policy_refs,source_hash,dependencies,dependencies_hash,bank_request,bank_request_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`, p.EnvironmentID.String(), p.UserID.String(), preview.String(), step.CommandKind, commandRaw, Hash(commandRaw), step.EffectHash, refs, g.Candidate.SourceHash, deps, Hash(deps), bankRaw, step.RequestHash, g.CreatedAt, g.ExpiresAt)
	if e != nil {
		return e
	}
	var previous any
	if step.PreviousOperationID != nil {
		var exists bool
		if e = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=$3)`, p.EnvironmentID.String(), p.UserID.String(), step.PreviousOperationID.String()).Scan(&exists); e != nil {
			return e
		}
		if exists {
			previous = step.PreviousOperationID.String()
		}
	}
	goals := []string{}
	for _, id := range g.Grant.Terms.GoalIDs {
		goals = append(goals, id.String())
	}
	_, e = tx.Exec(ctx, `INSERT INTO native_operations(environment_id,user_id,id,command_kind,idempotency_key,request,request_hash,bank_namespace,bank_key,bank_request,bank_request_hash,effect_hash,dependencies,dependencies_hash,plan_id,preview_id,group_id,child_number,previous_operation_id,subject_goal_ids,mode,grant_id,grant_version,permit_id,reserved_funds_version,created_at,updated_at) VALUES($1,$2,$3::uuid,$4,$5,$6,$7,$8,$3::text,$9,$10,$11,$12,$13,$14,$15,$14,$16,$17,$18::uuid[],'STANDING',$19,$20,$21,$22,$23,$23)`, p.EnvironmentID.String(), p.UserID.String(), step.OperationID.String(), step.CommandKind, fmt.Sprintf("standing_bridge_%s_%d", g.Occurrence.Key, step.Number), commandRaw, Hash(commandRaw), s.Namespace, bankRaw, step.RequestHash, step.EffectHash, deps, Hash(deps), g.Occurrence.ID.String(), preview.String(), step.Number, previous, goals, g.Grant.ID.String(), g.Grant.Version, step.Request.PermitID, g.Dependencies.FundsVersion+1, now)
	if e != nil {
		return e
	}
	amount := funds.Money{}
	if step.InitialSource {
		amount = step.Resources.Amount
	}
	return bindNativeOccurrenceOperation(ctx, tx, p, g.Occurrence.ID, step.OperationID, amount, step.EffectHash, step.RequestHash, now)
}
