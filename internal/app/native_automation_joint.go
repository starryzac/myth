package app

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type nativeStandingJointSlot struct {
	OperationID funds.ID `json:"operation_id"`
	PermitID    funds.ID `json:"permit_id"`
}

// The full real ledger remains in Candidate.Ledger. Input.Ledger is an
// explicitly derived eligibility projection containing this single immutable
// source origin; it is never persisted as the account's real full ledger.
type nativeStandingJointCapture struct {
	Protocol     string                    `json:"protocol"`
	GroupID      funds.ID                  `json:"group_id"`
	Input        NativeJointInput          `json:"input"`
	Result       planning.JointPlan        `json:"result"`
	GoalVersions map[string]uint64         `json:"goal_versions"`
	Slots        []nativeStandingJointSlot `json:"slots"`
}

func standingJointBudget(grant NativeStandingGrant, usage policy.NativeStandingUsage) (funds.Money, error) {
	life, e := funds.SumMoney(usage.LifetimeCommitted, usage.LifetimeReserved)
	month, e2 := funds.SumMoney(usage.MonthCommitted, usage.MonthReserved)
	if e != nil || e2 != nil {
		return funds.Money{}, reject("NATIVE_STANDING_CAP_EXCEEDED", nil)
	}
	life, e = grant.Terms.LifetimeCap.Sub(life)
	month, e2 = grant.Terms.MonthlyCap.Sub(month)
	if e != nil || e2 != nil {
		return funds.Money{}, reject("NATIVE_STANDING_CAP_EXCEEDED", nil)
	}
	return nativeResourceMinimum(grant.Terms.SingleCap, nativeResourceMinimum(life, month)), nil
}

func standingJointCalculation(snapshot templateSnapshot, ledger postgres.NativeLedger, grant NativeStandingGrant, source policy.NativeOccurrenceSource, usage policy.NativeStandingUsage, versions map[string]uint64, now time.Time) (nativeStandingAllocationCandidate, NativeJointInput, planning.JointPlan, error) {
	var out nativeStandingAllocationCandidate
	var input NativeJointInput
	var result planning.JointPlan
	fail := func(code string, e error) (nativeStandingAllocationCandidate, NativeJointInput, planning.JointPlan, error) {
		return out, input, result, reject(code, e)
	}
	if grant.Terms.Family != policy.NativeJointGoals || grant.Terms.Validate() != nil || source.Validate(grant.Terms.Family) != nil {
		return fail("EVIDENCE_INVALID", nil)
	}
	goals := []planning.JointGoal{}
	for _, ref := range grant.Terms.Policies {
		found := false
		for _, v := range snapshot.Views {
			if v.Version.VersionID != ref.VersionID {
				continue
			}
			if v.EffectiveState != "ACTIVE" || v.Version.PolicyID != ref.PolicyID || v.Version.Version != ref.Version || v.Version.Compiled.Hash != ref.ConfigurationHash || v.GoalID == nil || ref.GoalID == nil || *v.GoalID != *ref.GoalID {
				return fail("NATIVE_GRANT_POLICY_CHANGED", nil)
			}
			for _, g := range snapshot.Facts.Goals {
				if g.ID == *ref.GoalID && g.PolicyID == ref.PolicyID && g.VersionID == ref.VersionID {
					if versions[g.ID.String()] == 0 {
						return fail("EVIDENCE_INVALID", nil)
					}
					goals = append(goals, planning.JointGoal{Version: v.Version, Goal: g})
					found = true
				}
			}
		}
		if !found {
			return fail("NATIVE_GRANT_POLICY_CHANGED", nil)
		}
	}
	full := ledger.Income.Snapshot()
	selected := funds.LedgerSnapshot{UserID: full.UserID, Currency: full.Currency, AsOf: full.AsOf, ScopeAccountIDs: full.ScopeAccountIDs, Origins: []funds.IncomeOrigin{}, Locations: []funds.IncomeLocation{}, Reservations: []funds.IncomeReservation{}}
	var sourceID funds.ID
	for _, o := range full.Origins {
		if o.ID().String() == source.ID {
			if o.Fact().EvidenceHash.String() != source.OriginalHash || !o.Fact().OccurredAt.Equal(source.OccurredAt) {
				return fail("EVIDENCE_INVALID", nil)
			}
			sourceID = o.ID()
			selected.Origins = append(selected.Origins, o)
		}
	}
	if sourceID == (funds.ID{}) {
		return fail("EVIDENCE_INVALID", nil)
	}
	for _, l := range full.Locations {
		if l.OriginID == sourceID {
			selected.Locations = append(selected.Locations, l)
		}
	}
	for _, r := range full.Reservations {
		matches, other := false, false
		for _, u := range r.Uses {
			if u.OriginID == sourceID {
				matches = true
			} else {
				other = true
			}
		}
		if matches && !other {
			selected.Reservations = append(selected.Reservations, r)
		}
		if matches && other && r.State == funds.Reserved {
			return fail("NATIVE_SOURCE_OPERATION_PENDING", nil)
		}
	}
	eligible, e := funds.NewIncomeLedger(selected)
	if e != nil {
		return fail("EVIDENCE_INVALID", e)
	}
	cap, e := standingJointBudget(grant, usage)
	if e != nil {
		return out, input, result, e
	}
	cash := []funds.CashLocationFact{}
	for _, a := range ledger.Accounts {
		cash = append(cash, funds.CashLocationFact{AccountID: a.ID, Currency: funds.CNY, Balance: a.Balance, GoalOwned: a.GoalOwned, ObservedAt: a.ObservedAt})
	}
	forecast := snapshot.forecastInput(365)
	before, e := planning.Forecast365(forecast)
	if e != nil {
		return fail("PROTECTION_INCOMPLETE", e)
	}
	if !before.MaximumDeficit.IsZero() {
		return fail("PROTECTION_DEFICIT", nil)
	}
	deps := planning.DependencyVector{FundsVersion: snapshot.FundsVersion, PolicyVersion: 1, GrantVersion: grant.Version, ProductTermsVersion: 1, ProductAvailabilityVersion: 1, SourceEvidenceVersion: snapshot.SourceVersion, WriterGeneration: snapshot.WriterGeneration, ControlVersion: snapshot.ControlVersion}
	in := planning.JointInput{Scope: grant.Scope, Now: now, Timezone: grant.Terms.Timezone, Ledger: eligible, Cash: cash, Goals: goals, ProtectionInput: &forecast, Complete: true, InventoryHash: Hash(snapshot.InventoryRaw), Dependencies: deps, SolverNodeBudget: 200000, BudgetCap: &cap}
	input, e = captureNativeJointInput(in, forecast)
	if e != nil {
		return fail("EVIDENCE_INVALID", e)
	}
	result, e = planning.SolveJoint(in)
	if e != nil {
		return fail("NATIVE_STANDING_PLANNING_INCOMPLETE", e)
	}
	if result.Status != "OPTIMAL" {
		return fail("NATIVE_STANDING_PLANNING_INCOMPLETE", nil)
	}
	if len(result.Children) == 0 {
		return out, input, result, nil
	}
	if len(result.Children) > 8 {
		return fail("NATIVE_STANDING_CHILD_CAPACITY", nil)
	}
	var total funds.Money
	accounts := map[funds.ID]bool{}
	for _, child := range result.Children {
		if len(child.Uses) != 1 || child.Uses[0].OriginID != sourceID || child.Uses[0].Amount != child.Amount {
			return fail("EVIDENCE_INVALID", nil)
		}
		allowed, destinationAllowed := false, false
		for _, a := range grant.Terms.AccountIDs {
			allowed = allowed || a == child.SourceAccountID
			destinationAllowed = destinationAllowed || a == child.DestinationAccountID
		}
		if !allowed || !destinationAllowed {
			return fail("NATIVE_GRANT_SCOPE", nil)
		}
		accounts[child.SourceAccountID] = true
		accounts[child.DestinationAccountID] = true
		total, e = total.Add(child.Amount)
		if e != nil {
			return fail("EVIDENCE_INVALID", e)
		}
	}
	ids := []funds.ID{}
	for a := range accounts {
		ids = append(ids, a)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i].String() < ids[j].String() })
	authority := policy.NativeStandingCandidate{Scope: grant.Scope, Family: grant.Terms.Family, Source: source, Policies: grant.Terms.Policies, AccountIDs: ids, GoalIDs: grant.Terms.GoalIDs, Amount: total}
	if e = policy.AuthorizeNativeStanding(grant.Terms, "ACTIVE", authority, usage, now); e != nil {
		return fail(e.Error(), e)
	}
	afterRaw, _ := encode(forecast)
	var afterInput planning.ForecastInput
	if auditJSON(afterRaw, &afterInput) != nil {
		return fail("EVIDENCE_INVALID", nil)
	}
	for _, a := range result.Allocations {
		for i := range afterInput.Facts.Goals {
			g := &afterInput.Facts.Goals[i]
			if g.ID == a.GoalID {
				g.Owned, e = g.Owned.Add(a.Amount)
				if e != nil {
					return fail("EVIDENCE_INVALID", e)
				}
				g.CashOwned, e = g.CashOwned.Add(a.Amount)
				if e != nil {
					return fail("EVIDENCE_INVALID", e)
				}
				g.MonthContributed, e = g.MonthContributed.Add(a.Amount)
				if e != nil {
					return fail("EVIDENCE_INVALID", e)
				}
			}
		}
	}
	for i := range afterInput.Facts.Income {
		if afterInput.Facts.Income[i].ID == sourceID {
			afterInput.Facts.Income[i].Available, e = afterInput.Facts.Income[i].Available.Sub(total)
			if e != nil {
				return fail("EVIDENCE_INVALID", e)
			}
		}
	}
	for _, child := range result.Children {
		if child.SourceAccountID == child.DestinationAccountID {
			continue
		}
		for i := range afterInput.Facts.Accounts {
			a := &afterInput.Facts.Accounts[i]
			if a.ID == child.SourceAccountID {
				a.Balance, e = a.Balance.Sub(child.Amount)
			} else if a.ID == child.DestinationAccountID {
				a.Balance, e = a.Balance.Add(child.Amount)
			}
			if e != nil {
				return fail("EVIDENCE_INVALID", e)
			}
		}
	}
	after, e := planning.Forecast365(afterInput)
	if e != nil || !after.MaximumDeficit.IsZero() {
		return fail("PROTECTION_DEFICIT", e)
	}
	out = nativeStandingAllocationCandidate{Ledger: captureNativeAssetLedger(ledger), Policies: snapshot.Views, InventoryOriginal: append(json.RawMessage(nil), snapshot.InventoryRaw...), Usage: usage, Authority: authority, SourceHash: snapshot.Facts.SourceHash, InventoryHash: Hash(snapshot.InventoryRaw), ForecastInput: forecast, ForecastBefore: before, ForecastAfterInput: afterInput, ForecastAfter: after}
	return out, input, result, nil
}

func standingAllocationGoal(plan nativeStandingAllocationPlan) funds.ID {
	if plan.Joint != nil && plan.JointChild > 0 && plan.JointChild <= len(plan.Joint.Result.Children) {
		return plan.Joint.Result.Children[plan.JointChild-1].GoalID
	}
	return plan.Candidate.Authority.GoalIDs[0]
}

func standingAllocationReference(plan nativeStandingAllocationPlan) policy.NativePolicyReference {
	goal := standingAllocationGoal(plan)
	for _, r := range plan.Grant.Terms.Policies {
		if r.GoalID != nil && *r.GoalID == goal {
			return r
		}
	}
	return policy.NativePolicyReference{}
}

func replayStandingJointAllocation(plan nativeStandingAllocationPlan, namespace string) error {
	j := plan.Joint
	if j == nil || j.Protocol != NativeAutomationProtocol+"/joint-capture" || plan.Grant.Terms.Family != policy.NativeJointGoals || j.GroupID != plan.Occurrence.ID || plan.JointChild < 1 || plan.JointChild > len(j.Result.Children) || len(j.Slots) != len(j.Result.Children) || len(j.Slots) > 8 || len(j.Slots) == 0 || len(j.GoalVersions) != len(plan.Grant.Terms.GoalIDs) {
		return reject("EVIDENCE_INVALID", nil)
	}
	slot := j.Slots[plan.JointChild-1]
	child := j.Result.Children[plan.JointChild-1]
	if slot.OperationID != plan.ID || slot.PermitID != plan.PermitID || child.Number != plan.JointChild {
		return reject("EVIDENCE_INVALID", nil)
	}
	seen := map[funds.ID]bool{}
	for _, s := range j.Slots {
		if s.OperationID == (funds.ID{}) || s.PermitID == (funds.ID{}) || seen[s.OperationID] {
			return reject("EVIDENCE_INVALID", nil)
		}
		seen[s.OperationID] = true
	}
	ledger, e := plan.Candidate.Ledger.DomainLedger()
	if e != nil || ledger.Namespace != namespace || ledger.Income.Snapshot().UserID != plan.Scope.UserID || !ledger.Income.Snapshot().AsOf.Equal(plan.CreatedAt) || Hash(plan.Candidate.InventoryOriginal) != plan.Candidate.InventoryHash {
		return reject("EVIDENCE_INVALID", e)
	}
	f := plan.Candidate.ForecastInput
	d := plan.Dependencies
	snapshot := templateSnapshot{Facts: f.Facts, Versions: f.Versions, Views: plan.Candidate.Policies, Retained: f.RetainedObligations, Held: f.HeldCash, UnassignedGoalCash: f.UnassignedGoalCash, InventoryRaw: plan.Candidate.InventoryOriginal, FundsVersion: d.FundsVersion, SourceVersion: d.SourceEvidenceVersion, WriterGeneration: d.WriterGeneration, ControlVersion: d.ControlVersion, AsOf: plan.CreatedAt, Timezone: plan.Grant.Terms.Timezone, Native: &ledger}
	snapshot.ExternalLiabilities = f.ExternalLiabilities
	expected, input, result, e := standingJointCalculation(snapshot, ledger, plan.Grant, plan.Occurrence.Source, plan.Candidate.Usage, j.GoalVersions, plan.CreatedAt)
	if e != nil {
		return reject("EVIDENCE_INVALID", e)
	}
	expected.Use = child.Uses[0]
	expected.GoalVersion = j.GoalVersions[child.GoalID.String()]
	a, _ := encode(input)
	b, _ := encode(j.Input)
	x, _ := encode(result)
	y, _ := encode(j.Result)
	c, _ := encode(expected)
	v, _ := encode(plan.Candidate)
	if string(a) != string(b) || string(x) != string(y) || string(c) != string(v) {
		return reject("EVIDENCE_INVALID", nil)
	}
	return nil
}

func (s *Service) admitStandingJoint(ctx context.Context, c NativeOccurrenceClaim) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return err
	}
	p := Principal{EnvironmentID: c.Occurrence.Scope.EnvironmentID, UserID: c.Occurrence.Scope.UserID, Kind: "SYSTEM"}
	if err = sameUser(p, env.ID); err != nil {
		return err
	}
	fv, sv, err := lockUser(ctx, tx, p)
	if err != nil {
		return err
	}
	if err = nativeCurrentOccurrenceClaim(ctx, tx, c); err != nil {
		return err
	}
	grant, err := loadNativeStandingGrant(ctx, tx, p, c.Occurrence.GrantID)
	if err != nil {
		return err
	}
	now := s.now()
	if grant.State != "ACTIVE" || now.Before(grant.Grant.Terms.EffectiveFrom) || !now.Before(grant.Grant.Terms.ExpiresAt) {
		if err = nativeFinishOccurrence(ctx, tx, c, "BLOCKED", "NATIVE_GRANT_NOT_ACTIVE"); err != nil {
			return err
		}
		return tx.Commit(ctx)
	}
	if c.Occurrence.Family != policy.NativeJointGoals || grant.Grant.Terms.Family != c.Occurrence.Family {
		return reject("FAMILY_NOT_QUALIFIED", nil)
	}
	versions := map[string]uint64{}
	for _, goal := range grant.Grant.Terms.GoalIDs {
		if err = ensureGoalAvailable(ctx, tx, p, goal); err != nil {
			return err
		}
		var v uint64
		if err = tx.QueryRow(ctx, `SELECT version FROM goals WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), goal.String()).Scan(&v); err != nil {
			return err
		}
		versions[goal.String()] = v
	}
	ledger, err := s.Store.LoadNativeLedger(ctx, tx, p.EnvironmentID, p.UserID, now)
	if err != nil {
		return err
	}
	snapshot, err := s.loadTemplateSnapshot(ctx, tx, p, now, grant.Grant.Terms.Timezone)
	if err != nil {
		return err
	}
	usage, err := nativeStandingUsage(ctx, tx, p, grant.Grant, now, nil)
	if err != nil {
		return err
	}
	candidate, input, result, err := standingJointCalculation(snapshot, ledger, grant.Grant, c.Occurrence.Source, usage, versions, now)
	if err != nil {
		return err
	}
	if len(result.Children) == 0 {
		if err = nativeFinishOccurrence(ctx, tx, c, "NO_ACTION", "NO_CURRENT_ELIGIBLE_AMOUNT"); err != nil {
			return err
		}
		return tx.Commit(ctx)
	}
	for _, child := range result.Children {
		if child.SourceAccountID != child.DestinationAccountID {
			deps := planning.DependencyVector{FundsVersion: fv, PolicyVersion: 1, GrantVersion: grant.Grant.Version, ProductTermsVersion: 1, ProductAvailabilityVersion: 1, SourceEvidenceVersion: sv, WriterGeneration: env.WriterGeneration, ControlVersion: env.ControlVersion}
			return s.admitStandingBridge(ctx, tx, p, c, grant, candidate, input, result, versions, deps, ledger, now)
		}
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
	joint := nativeStandingJointCapture{Protocol: NativeAutomationProtocol + "/joint-capture", GroupID: c.Occurrence.ID, Input: input, Result: result, GoalVersions: versions, Slots: []nativeStandingJointSlot{}}
	for range result.Children {
		op, e := newID()
		if e != nil {
			return e
		}
		permit, e := newID()
		if e != nil {
			return e
		}
		joint.Slots = append(joint.Slots, nativeStandingJointSlot{op, permit})
	}
	jointRaw, _ := encode(joint)
	budgetRaw, _ := encode(struct {
		Protocol    string      `json:"protocol"`
		CaptureHash string      `json:"capture_hash"`
		Amount      funds.Money `json:"amount_cents"`
	}{NativeAutomationProtocol + "/joint-budget", Hash(jointRaw), candidate.Authority.Amount})
	if err = reserveNativeOccurrenceBudget(ctx, tx, p, c, grant.Grant, candidate.Authority.Amount, budgetRaw, now); err != nil {
		return err
	}
	deps := planning.DependencyVector{FundsVersion: fv, PolicyVersion: 1, GrantVersion: grant.Grant.Version, ProductTermsVersion: 1, ProductAvailabilityVersion: 1, SourceEvidenceVersion: sv, WriterGeneration: env.WriterGeneration, ControlVersion: env.ControlVersion}
	depsRaw, _ := encode(deps)
	refsRaw, _ := encode(grant.Grant.Terms.Policies)
	for i, child := range result.Children {
		slot := joint.Slots[i]
		cc := candidate
		cc.Use = child.Uses[0]
		cc.GoalVersion = versions[child.GoalID.String()]
		plan := nativeStandingAllocationPlan{Protocol: NativeAutomationProtocol + "/allocation-plan", ID: slot.OperationID, Scope: grant.Grant.Scope, Occurrence: c.Occurrence, Grant: grant.Grant, GrantStateSequence: grant.StateSequence, Candidate: cc, Dependencies: deps, PermitID: slot.PermitID, CreatedAt: now, ExpiresAt: expires, Joint: &joint, JointChild: i + 1}
		planRaw, e := encode(plan)
		if e != nil || len(planRaw) > 8<<20 {
			return reject("NATIVE_STANDING_CAPTURE_CAPACITY", e)
		}
		planHash := Hash(planRaw)
		request := standingAllocationExpectedRequest(plan, s.Namespace, planHash)
		bankRaw, e := request.CanonicalBytes()
		if e != nil {
			return e
		}
		bankHash, _ := request.Digest()
		effectHash, _ := request.Effect.Digest()
		command := nativeStandingExecutionOriginal{Protocol: NativeAutomationProtocol + "/allocation-execution", PlanID: slot.OperationID, PlanHash: planHash, Request: request}
		commandRaw, _ := encode(command)
		previewID, e := newID()
		if e != nil {
			return e
		}
		jobID, e := newID()
		if e != nil {
			return e
		}
		_, err = tx.Exec(ctx, `INSERT INTO native_standing_plans(environment_id,user_id,id,occurrence_id,grant_id,grant_version,original,original_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, p.EnvironmentID.String(), p.UserID.String(), slot.OperationID.String(), c.Occurrence.ID.String(), grant.Grant.ID.String(), grant.Grant.Version, planRaw, planHash, now, expires)
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO native_previews(environment_id,user_id,id,kind,original,original_hash,effect_hash,policy_refs,source_hash,dependencies,dependencies_hash,bank_request,bank_request_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`, p.EnvironmentID.String(), p.UserID.String(), previewID.String(), nativeStandingAllocateKind, commandRaw, Hash(commandRaw), effectHash, refsRaw, candidate.SourceHash, depsRaw, Hash(depsRaw), bankRaw, bankHash, now, expires)
		if err != nil {
			return err
		}
		var previous any
		if i > 0 {
			previous = joint.Slots[i-1].OperationID.String()
		}
		_, err = tx.Exec(ctx, `INSERT INTO native_operations(environment_id,user_id,id,command_kind,idempotency_key,request,request_hash,bank_namespace,bank_key,bank_request,bank_request_hash,effect_hash,dependencies,dependencies_hash,plan_id,preview_id,group_id,child_number,previous_operation_id,subject_goal_ids,mode,grant_id,grant_version,permit_id,reserved_funds_version,created_at,updated_at) VALUES($1,$2,$3::uuid,$4,$5,$6,$7,$8,$3::text,$9,$10,$11,$12,$13,$3::uuid,$14,$15,$16,$17,ARRAY[$18::uuid],'STANDING',$19,$20,$21,$22,$23,$23)`, p.EnvironmentID.String(), p.UserID.String(), slot.OperationID.String(), nativeStandingAllocateKind, fmt.Sprintf("native_joint_%s_%d", c.Occurrence.Key, i+1), commandRaw, Hash(commandRaw), s.Namespace, bankRaw, bankHash, effectHash, depsRaw, Hash(depsRaw), previewID.String(), c.Occurrence.ID.String(), i+1, previous, child.GoalID.String(), grant.Grant.ID.String(), grant.Grant.Version, slot.PermitID.String(), fv+1, now)
		if err != nil {
			return err
		}
		ledger.Income, err = ledger.Income.Reserve(slot.OperationID, child.Uses, funds.AllocateGoal, now, nil)
		if err != nil {
			return err
		}
		ledger.Holds = append(ledger.Holds, postgres.NativeHold{OperationID: slot.OperationID, ResourceKind: "CASH", ResourceID: child.SourceAccountID.String(), Amount: child.Amount, State: funds.Reserved})
		if err = bindNativeOccurrenceOperation(ctx, tx, p, c.Occurrence.ID, slot.OperationID, child.Amount, effectHash, bankHash, now); err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO native_jobs(environment_id,user_id,id,operation_id,next_attempt_at) VALUES($1,$2,$3,$4,clock_timestamp())`, p.EnvironmentID.String(), p.UserID.String(), jobID.String(), slot.OperationID.String())
		if err != nil {
			return err
		}
	}
	if err = s.Store.PersistNativeLedger(ctx, tx, p.EnvironmentID, p.UserID, ledger); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2`, p.EnvironmentID.String(), p.UserID.String()); err != nil {
		return err
	}
	if err = nativeFinishOccurrence(ctx, tx, c, "ACCEPTED", ""); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
