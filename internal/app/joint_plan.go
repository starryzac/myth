package app

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"sort"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const NativeJointProtocol = "native-joint-plan-go-v1"

type JointPlanRequest struct {
	Timezone string `json:"timezone"`
}

// NativeJointLedger exposes every immutable fact and every current bucket.
// IncomeLedger and IncomeOrigin deliberately have private fields: marshaling
// those values directly would lose their contents and make replay impossible.
type NativeJointLedger struct {
	UserID          funds.ID                  `json:"user_id"`
	Currency        funds.Currency            `json:"currency"`
	AsOf            time.Time                 `json:"as_of"`
	ScopeAccountIDs []funds.ID                `json:"scope_account_ids"`
	OriginFacts     []funds.BankCreditFact    `json:"origin_facts"`
	Locations       []funds.IncomeLocation    `json:"locations"`
	Reservations    []funds.IncomeReservation `json:"reservations"`
}

type NativeJointInput struct {
	Ledger             NativeJointLedger      `json:"ledger"`
	Planner            json.RawMessage        `json:"planner"`
	ProtectionForecast planning.ForecastInput `json:"protection_forecast"`
}

type NativeJointPolicyReference struct {
	GoalID            funds.ID `json:"goal_id"`
	GoalVersion       uint64   `json:"goal_version"`
	PolicyID          funds.ID `json:"policy_id"`
	VersionID         funds.ID `json:"version_id"`
	Version           uint64   `json:"version"`
	ConfigurationHash string   `json:"configuration_hash"`
}

type NativeJointPlan struct {
	ID               funds.ID                     `json:"id"`
	Protocol         string                       `json:"protocol"`
	Scope            policy.TemplateOwnerScope    `json:"scope"`
	SessionID        funds.ID                     `json:"session_id"`
	CreatedAt        time.Time                    `json:"created_at"`
	ExpiresAt        time.Time                    `json:"expires_at"`
	SourceHash       string                       `json:"source_hash"`
	InventoryHash    string                       `json:"inventory_hash"`
	PolicyReferences []NativeJointPolicyReference `json:"policy_references"`
	Dependencies     planning.DependencyVector    `json:"dependencies"`
	Input            NativeJointInput             `json:"input"`
	InputHash        string                       `json:"input_hash"`
	Result           planning.JointPlan           `json:"result"`
	ResultHash       string                       `json:"result_hash"`
	BankAuthority    bool                         `json:"bank_authority"`
}

// JointPreview captures one complete RR scope. No input supplied by an HTTP
// caller can mark facts complete, supply a safe amount, select weaker policy
// versions, alter the solver budget, or authorize a bank action.
func (s *Service) JointPreview(ctx context.Context, p Principal, request JointPlanRequest) (NativeJointPlan, error) {
	var plan NativeJointPlan
	if err := s.auditHuman(ctx, p); err != nil {
		return plan, err
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return plan, err
	}
	defer rollback(ctx, tx)
	timezone, err := templateScopedTimezone(ctx, tx, p, request.Timezone)
	if err != nil {
		return plan, err
	}
	now := s.now()
	snapshot, err := s.loadTemplateSnapshot(ctx, tx, p, now, timezone)
	if err != nil {
		return plan, err
	}
	goals, refs, err := nativeJointGoals(snapshot)
	if err != nil {
		return plan, err
	}
	for _, goal := range goals {
		if err = ensureGoalAvailable(ctx, tx, p, goal.Goal.ID); err != nil {
			return plan, err
		}
	}
	ledger, err := s.Store.LoadLedger(ctx, tx, p.EnvironmentID, p.UserID, now)
	if err != nil {
		return plan, reject("JOINT_EVIDENCE_INVALID", err)
	}
	cash, err := nativeJointCash(snapshot)
	if err != nil {
		return plan, err
	}
	if err = tx.Commit(ctx); err != nil {
		return plan, err
	}
	// Keep every selected LongTerm version in the protection proof. The pure
	// v2 solver models contribution-dependent current/future floors itself;
	// deleting the versions here would erase future monthly protections.
	forecast := snapshot.forecastInput(365)
	dependencies := planning.DependencyVector{FundsVersion: snapshot.FundsVersion, PolicyVersion: 1, ProductTermsVersion: 1, ProductAvailabilityVersion: 1, SourceEvidenceVersion: snapshot.SourceVersion, WriterGeneration: snapshot.WriterGeneration, ControlVersion: snapshot.ControlVersion}
	in := planning.JointInput{Scope: snapshot.Facts.Scope, Now: now, Timezone: timezone, Ledger: ledger, Cash: cash, Goals: goals, ProtectionInput: &forecast, Complete: true, InventoryHash: Hash(snapshot.InventoryRaw), Dependencies: dependencies, SolverNodeBudget: 200000}
	input, err := captureNativeJointInput(in, forecast)
	if err != nil {
		return plan, err
	}
	result, err := planning.SolveJoint(in)
	if err != nil {
		return plan, reject("JOINT_PLANNING_INCOMPLETE", err)
	}
	id, err := newID()
	if err != nil {
		return plan, err
	}
	inputRaw, err := encode(input)
	if err != nil {
		return plan, err
	}
	resultRaw, err := encode(result)
	if err != nil {
		return plan, err
	}
	depsRaw, err := encode(dependencies)
	if err != nil {
		return plan, err
	}
	plan = NativeJointPlan{ID: id, Protocol: NativeJointProtocol, Scope: snapshot.Facts.Scope, SessionID: p.SessionID, CreatedAt: now, ExpiresAt: now.Add(2 * time.Minute), SourceHash: snapshot.Facts.SourceHash, InventoryHash: Hash(snapshot.InventoryRaw), PolicyReferences: refs, Dependencies: dependencies, Input: input, InputHash: Hash(inputRaw), Result: result, ResultHash: Hash(resultRaw)}
	zone, _ := policy.TemplateLocation(timezone)
	local := now.In(zone)
	nextMidnight := time.Date(local.Year(), local.Month(), local.Day()+1, 0, 0, 0, 0, zone).UTC()
	if nextMidnight.Before(plan.ExpiresAt) {
		plan.ExpiresAt = nextMidnight
	}
	original, err := encode(plan)
	if err != nil {
		return plan, err
	}
	if len(original) > 16*1024*1024 || len(inputRaw) > 8*1024*1024 || len(resultRaw) > 4*1024*1024 {
		return plan, reject("JOINT_CAPTURE_CAPACITY", nil)
	}
	if err = s.human(ctx, s.Pool, p); err != nil {
		return plan, err
	}
	_, err = s.writerExec(ctx, `INSERT INTO native_joint_plans(environment_id,user_id,id,session_id,protocol_version,status,original,original_hash,input,input_hash,result,result_hash,dependencies,dependencies_hash,source_hash,inventory_hash,effect_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)`, p.EnvironmentID.String(), p.UserID.String(), id.String(), p.SessionID.String(), NativeJointProtocol, result.Status, original, Hash(original), inputRaw, plan.InputHash, resultRaw, plan.ResultHash, depsRaw, Hash(depsRaw), plan.SourceHash, plan.InventoryHash, result.EffectHash, now, plan.ExpiresAt)
	return plan, err
}

func captureNativeJointInput(in planning.JointInput, forecast planning.ForecastInput) (NativeJointInput, error) {
	snapshot := in.Ledger.Snapshot()
	facts := make([]funds.BankCreditFact, len(snapshot.Origins))
	for i, origin := range snapshot.Origins {
		facts[i] = origin.Fact()
	}
	// Remove the private ledger object entirely. The separately typed capture
	// above is the only ledger representation in this wire protocol.
	raw, err := encode(in)
	if err != nil {
		return NativeJointInput{}, err
	}
	var core map[string]json.RawMessage
	if err = json.Unmarshal(raw, &core); err != nil {
		return NativeJointInput{}, err
	}
	delete(core, "Ledger")
	raw, err = encode(core)
	return NativeJointInput{Ledger: NativeJointLedger{snapshot.UserID, snapshot.Currency, snapshot.AsOf, snapshot.ScopeAccountIDs, facts, snapshot.Locations, snapshot.Reservations}, Planner: raw, ProtectionForecast: forecast}, err
}

// DomainInput reconstructs the original pure input. It grants no authority and
// does not claim that an old snapshot is current; T1 must compare authority.
func (input NativeJointInput) DomainInput() (planning.JointInput, error) {
	var in planning.JointInput
	var core map[string]json.RawMessage
	if auditJSON(input.Planner, &core) != nil || core == nil {
		return in, reject("JOINT_INPUT_INVALID", nil)
	}
	for key := range core {
		if key == "Ledger" || key == "ledger" {
			return in, reject("JOINT_INPUT_INVALID", nil)
		}
	}
	// Planner is encoded as a sorted map so future explicit pure-input fields
	// remain present while the private Ledger member is absent. Its canonical
	// order is therefore different from the Go struct declaration order.
	decoder := json.NewDecoder(bytes.NewReader(input.Planner))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&in); err != nil {
		return in, reject("JOINT_INPUT_INVALID", err)
	}
	origins := make([]funds.IncomeOrigin, len(input.Ledger.OriginFacts))
	for i, fact := range input.Ledger.OriginFacts {
		origin, err := funds.NewIncomeOrigin(fact)
		if err != nil {
			return in, reject("JOINT_INPUT_INVALID", err)
		}
		origins[i] = origin
	}
	ledger, err := funds.NewIncomeLedger(funds.LedgerSnapshot{UserID: input.Ledger.UserID, Currency: input.Ledger.Currency, AsOf: input.Ledger.AsOf, ScopeAccountIDs: input.Ledger.ScopeAccountIDs, Origins: origins, Locations: input.Ledger.Locations, Reservations: input.Ledger.Reservations})
	if err != nil || input.Ledger.UserID != in.Scope.UserID || !input.Ledger.AsOf.Equal(in.Now) {
		return in, reject("JOINT_INPUT_INVALID", err)
	}
	in.Ledger = ledger
	return in, nil
}

func ReplayNativeJointPlan(plan NativeJointPlan) (planning.JointPlan, error) {
	var zero planning.JointPlan
	inputRaw, err := encode(plan.Input)
	if err != nil || plan.Protocol != NativeJointProtocol || plan.BankAuthority || Hash(inputRaw) != plan.InputHash {
		return zero, reject("JOINT_PLAN_INVALID", errors.New("frozen input integrity"))
	}
	resultRaw, err := encode(plan.Result)
	if err != nil || Hash(resultRaw) != plan.ResultHash || plan.Result.BankAuthority {
		return zero, reject("JOINT_PLAN_INVALID", errors.New("frozen result integrity"))
	}
	in, err := plan.Input.DomainInput()
	if err != nil || in.Scope != plan.Scope || !in.Now.Equal(plan.CreatedAt) || in.InventoryHash != plan.InventoryHash || in.Dependencies != plan.Dependencies || plan.Input.ProtectionForecast.Facts.SourceHash != plan.SourceHash {
		if err != nil {
			return zero, reject("JOINT_PLAN_INVALID", err)
		}
		return zero, reject("JOINT_PLAN_INVALID", errors.New("frozen scope and dependencies"))
	}
	if in.ProtectionInput == nil || len(in.Protection) != 0 || len(in.ProtectionInput.FutureIncome) != 0 || len(in.ProtectionInput.PrincipalReturns) != 0 || !in.ProtectionInput.AsOf.Equal(in.Now) || in.ProtectionInput.Scope != in.Scope || in.ProtectionInput.Timezone != in.Timezone || !plan.ExpiresAt.After(plan.CreatedAt) || plan.ExpiresAt.After(plan.CreatedAt.Add(2*time.Minute)) || len(plan.PolicyReferences) != len(in.Goals) {
		return zero, reject("JOINT_PLAN_INVALID", errors.New("frozen protection scope or references"))
	}
	proof, err := encode(in.ProtectionInput)
	storedProof, secondErr := encode(plan.Input.ProtectionForecast)
	if err != nil || secondErr != nil || !bytes.Equal(proof, storedProof) {
		return zero, reject("JOINT_PLAN_INVALID", errors.New("frozen protection proof"))
	}
	seen := map[funds.ID]bool{}
	for _, ref := range plan.PolicyReferences {
		if seen[ref.GoalID] || ref.GoalVersion == 0 {
			return zero, reject("JOINT_PLAN_INVALID", nil)
		}
		seen[ref.GoalID] = true
		found := false
		for _, goal := range in.Goals {
			if ref.GoalID == goal.Goal.ID && ref.PolicyID == goal.Version.PolicyID && ref.VersionID == goal.Version.VersionID && ref.Version == goal.Version.Version && ref.ConfigurationHash == goal.Version.Compiled.Hash {
				found = true
			}
		}
		if !found {
			return zero, reject("JOINT_PLAN_INVALID", nil)
		}
	}
	replayed, err := planning.SolveJoint(in)
	if err != nil {
		return zero, reject("JOINT_PLAN_INVALID", err)
	}
	replayRaw, err := encode(replayed)
	if err != nil || !bytes.Equal(replayRaw, resultRaw) {
		return zero, reject("JOINT_PLAN_INVALID", errors.New("deterministic result replay"))
	}
	return replayed, nil
}

func (s *Service) JointPlan(ctx context.Context, p Principal, id funds.ID) (NativeJointPlan, error) {
	if err := s.auditHuman(ctx, p); err != nil {
		return NativeJointPlan{}, err
	}
	return loadNativeJointPlan(ctx, s.Pool, p, id)
}

func loadNativeJointPlan(ctx context.Context, q queryRow, p Principal, id funds.ID) (NativeJointPlan, error) {
	var plan NativeJointPlan
	var original []byte
	var hash string
	err := q.QueryRow(ctx, `SELECT original,original_hash::text FROM native_joint_plans WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&original, &hash)
	if errors.Is(err, pgx.ErrNoRows) {
		return plan, reject("NOT_FOUND", err)
	}
	if err != nil {
		return plan, err
	}
	if Hash(original) != hash || auditJSON(original, &plan) != nil || plan.ID != id || plan.Scope != (policy.TemplateOwnerScope{EnvironmentID: p.EnvironmentID, UserID: p.UserID}) {
		return plan, reject("JOINT_PLAN_INVALID", nil)
	}
	_, err = ReplayNativeJointPlan(plan)
	return plan, err
}

func nativeJointCash(snapshot templateSnapshot) ([]funds.CashLocationFact, error) {
	cash := make([]funds.CashLocationFact, 0, len(snapshot.Raw["accounts"]))
	for _, raw := range snapshot.Raw["accounts"] {
		var row struct {
			ID         funds.ID  `json:"id"`
			Balance    int64     `json:"balance_cents"`
			Owned      int64     `json:"goal_owned_cents"`
			ObservedAt time.Time `json:"observed_at"`
		}
		if json.Unmarshal([]byte(raw), &row) != nil {
			return nil, reject("JOINT_INPUT_INVALID", nil)
		}
		balance, err := templateMoney(row.Balance)
		if err != nil {
			return nil, err
		}
		owned, err := templateMoney(row.Owned)
		if err != nil {
			return nil, err
		}
		cash = append(cash, funds.CashLocationFact{AccountID: row.ID, Currency: funds.CNY, Balance: balance, GoalOwned: owned, ObservedAt: row.ObservedAt})
	}
	return cash, nil
}

func nativeJointGoals(snapshot templateSnapshot) ([]planning.JointGoal, []NativeJointPolicyReference, error) {
	goals := []planning.JointGoal{}
	refs := []NativeJointPolicyReference{}
	for _, view := range snapshot.Views {
		if view.Version.Compiled.Name != policy.LongTermGoalTemplate {
			continue
		}
		if view.GoalID == nil {
			return nil, nil, reject("JOINT_INPUT_INVALID", nil)
		}
		found := false
		for _, goal := range snapshot.Facts.Goals {
			if goal.ID != *view.GoalID || goal.PolicyID != view.Version.PolicyID || goal.VersionID != view.Version.VersionID {
				continue
			}
			for _, raw := range snapshot.Raw["goals"] {
				var row struct {
					ID      funds.ID `json:"id"`
					Version uint64   `json:"version"`
					Target  int64    `json:"target_cents"`
				}
				if json.Unmarshal([]byte(raw), &row) != nil {
					return nil, nil, reject("JOINT_INPUT_INVALID", nil)
				}
				if row.ID != goal.ID {
					continue
				}
				compiled, err := view.Version.Compiled.Decode()
				if err != nil || compiled.(*policy.LongTermGoalConfig).Target.Cents() != row.Target {
					return nil, nil, reject("JOINT_GOAL_TARGET_MISMATCH", err)
				}
				goals = append(goals, planning.JointGoal{Version: view.Version, Goal: goal})
				refs = append(refs, NativeJointPolicyReference{goal.ID, row.Version, view.Version.PolicyID, view.Version.VersionID, view.Version.Version, view.Version.Compiled.Hash})
				found = true
			}
		}
		if !found {
			return nil, nil, reject("JOINT_INPUT_INVALID", nil)
		}
	}
	sort.Slice(goals, func(i, j int) bool { return goals[i].Goal.ID.String() < goals[j].Goal.ID.String() })
	sort.Slice(refs, func(i, j int) bool { return refs[i].GoalID.String() < refs[j].GoalID.String() })
	if len(goals) < 2 || len(goals) > 8 {
		return nil, nil, reject("JOINT_GOAL_CAPACITY", nil)
	}
	return goals, refs, nil
}
