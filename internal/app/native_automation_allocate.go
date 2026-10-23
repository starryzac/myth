package app

import (
	"context"
	"encoding/json"
	"time"

	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const nativeStandingAllocateKind = "STANDING_ALLOCATE"

type nativeStandingAllocationCandidate struct {
	Ledger             NativeAssetLedgerCapture       `json:"ledger"`
	Policies           []TemplatePolicyView           `json:"policies"`
	InventoryOriginal  json.RawMessage                `json:"inventory_original"`
	Usage              policy.NativeStandingUsage     `json:"usage"`
	Authority          policy.NativeStandingCandidate `json:"authority"`
	Use                funds.IncomeUse                `json:"use"`
	GoalVersion        uint64                         `json:"goal_version"`
	SourceHash         string                         `json:"source_hash"`
	InventoryHash      string                         `json:"inventory_hash"`
	PolicyPreview      policy.TemplatePreview         `json:"policy_preview"`
	ForecastInput      planning.ForecastInput         `json:"forecast_input"`
	ForecastBefore     planning.ForecastResult        `json:"forecast_before"`
	ForecastAfterInput planning.ForecastInput         `json:"forecast_after_input"`
	ForecastAfter      planning.ForecastResult        `json:"forecast_after"`
}

type nativeStandingAllocationPlan struct {
	Protocol           string                            `json:"protocol"`
	ID                 funds.ID                          `json:"id"`
	Scope              policy.TemplateOwnerScope         `json:"scope"`
	Occurrence         NativeOccurrence                  `json:"occurrence"`
	Grant              NativeStandingGrant               `json:"grant"`
	GrantStateSequence uint64                            `json:"grant_state_sequence"`
	Candidate          nativeStandingAllocationCandidate `json:"candidate"`
	Dependencies       planning.DependencyVector         `json:"dependencies"`
	PermitID           funds.ID                          `json:"permit_id"`
	CreatedAt          time.Time                         `json:"created_at"`
	ExpiresAt          time.Time                         `json:"expires_at"`
	Joint              *nativeStandingJointCapture       `json:"joint,omitempty"`
	JointChild         int                               `json:"joint_child,omitempty"`
}

type nativeStandingExecutionOriginal struct {
	Protocol string         `json:"protocol"`
	PlanID   funds.ID       `json:"plan_id"`
	PlanHash string         `json:"plan_hash"`
	Request  bankv2.Request `json:"request"`
}

func nativeStandingUsage(ctx context.Context, q queryRow, p Principal, grant NativeStandingGrant, now time.Time, exclude *funds.ID) (policy.NativeStandingUsage, error) {
	zone, e := policy.TemplateLocation(grant.Terms.Timezone)
	if e != nil {
		return policy.NativeStandingUsage{}, reject("EVIDENCE_INVALID", e)
	}
	out := policy.NativeStandingUsage{Period: now.In(zone).Format("2006-01")}
	var lc, lr, mc, mr int64
	var ex any
	if exclude != nil {
		ex = exclude.String()
	}
	e = q.QueryRow(ctx, `SELECT coalesce(sum(spent_cents) FILTER(WHERE state='FINAL'),0)::bigint,coalesce(sum(amount_cents) FILTER(WHERE state='RESERVED'),0)::bigint,coalesce(sum(spent_cents) FILTER(WHERE state='FINAL' AND period=$5),0)::bigint,coalesce(sum(amount_cents) FILTER(WHERE state='RESERVED' AND period=$5),0)::bigint FROM native_grant_budget_claims WHERE environment_id=$1 AND user_id=$2 AND grant_id=$3 AND grant_version=$4 AND ($6::uuid IS NULL OR occurrence_id<>$6::uuid)`, p.EnvironmentID.String(), p.UserID.String(), grant.ID.String(), grant.Version, out.Period, ex).Scan(&lc, &lr, &mc, &mr)
	if e != nil {
		return out, e
	}
	var err error
	out.LifetimeCommitted, err = funds.NewMoney(lc)
	if err != nil {
		return out, err
	}
	out.LifetimeReserved, err = funds.NewMoney(lr)
	if err != nil {
		return out, err
	}
	out.MonthCommitted, err = funds.NewMoney(mc)
	if err != nil {
		return out, err
	}
	out.MonthReserved, err = funds.NewMoney(mr)
	return out, err
}

func standingAllocationCandidate(snapshot templateSnapshot, ledger postgres.NativeLedger, grant NativeStandingGrant, source policy.NativeOccurrenceSource, usage policy.NativeStandingUsage, goalVersion uint64, now time.Time) (nativeStandingAllocationCandidate, error) {
	var out nativeStandingAllocationCandidate
	if grant.Terms.Family != policy.NativeSingleGoal || len(grant.Terms.Policies) != 1 || len(grant.Terms.GoalIDs) != 1 {
		return out, reject("FAMILY_NOT_QUALIFIED", nil)
	}
	ref := grant.Terms.Policies[0]
	var view *TemplatePolicyView
	for i := range snapshot.Views {
		if snapshot.Views[i].Version.VersionID == ref.VersionID {
			view = &snapshot.Views[i]
		}
	}
	if view == nil || view.EffectiveState != "ACTIVE" || view.Version.PolicyID != ref.PolicyID || view.Version.Version != ref.Version || view.Version.Compiled.Hash != ref.ConfigurationHash || view.GoalID == nil || *view.GoalID != grant.Terms.GoalIDs[0] {
		return out, reject("NATIVE_GRANT_POLICY_CHANGED", nil)
	}
	goal := grant.Terms.GoalIDs[0]
	var account funds.ID
	for _, g := range ledger.Goals {
		if g.GoalID == goal {
			account = g.AccountID
		}
	}
	if account == (funds.ID{}) {
		return out, reject("EVIDENCE_INVALID", nil)
	}
	allowed := false
	for _, id := range grant.Terms.AccountIDs {
		if id == account {
			allowed = true
		}
	}
	if !allowed {
		return out, reject("NATIVE_GRANT_SCOPE", nil)
	}
	beforeInput := snapshot.forecastInput(365)
	before, e := planning.Forecast365(beforeInput)
	if e != nil {
		return out, reject("PROTECTION_INCOMPLETE", e)
	}
	if !before.MaximumDeficit.IsZero() {
		return out, reject("PROTECTION_DEFICIT", nil)
	}
	income := ledger.Income.Snapshot()
	var available funds.Money
	var fragment, origin funds.ID
	for _, o := range income.Origins {
		if o.ID().String() == source.ID {
			if o.Fact().EvidenceHash.String() != source.OriginalHash || !o.Fact().OccurredAt.Equal(source.OccurredAt) {
				return out, reject("EVIDENCE_INVALID", nil)
			}
			origin = o.ID()
		}
	}
	for _, l := range income.Locations {
		if l.OriginID == origin && l.AccountID == account {
			available, fragment = l.Available, l.FragmentID
		}
	}
	if origin == (funds.ID{}) {
		return out, reject("EVIDENCE_INVALID", nil)
	}
	free, e := nativeUnownedCashAvailability(ledger)
	if e != nil {
		return out, e
	}
	available = nativeResourceMinimum(available, free.free[account])
	available = nativeResourceMinimum(available, before.CurrentSafe)
	facts := snapshot.Facts
	facts.Income = []policy.TemplateIncomeFact{{ID: origin, Available: available, OccurredAt: source.OccurredAt, Verified: true}}
	facts.CurrentSafe, facts.CurrentSafeVerified = before.CurrentSafe, true
	preview, e := policy.PreviewTemplate(view.Version, facts, now, grant.Terms.Timezone)
	if e != nil {
		return out, reject("NATIVE_STANDING_PLANNING_INCOMPLETE", e)
	}
	amount := nativeResourceMinimum(preview.Amount, available)
	amount = nativeResourceMinimum(amount, grant.Terms.SingleCap)
	lifeUsed, e := funds.SumMoney(usage.LifetimeCommitted, usage.LifetimeReserved)
	if e != nil {
		return out, reject("NATIVE_STANDING_CAP_EXCEEDED", e)
	}
	life, e := grant.Terms.LifetimeCap.Sub(lifeUsed)
	if e != nil {
		return out, reject("NATIVE_STANDING_CAP_EXCEEDED", e)
	}
	monthUsed, e := funds.SumMoney(usage.MonthCommitted, usage.MonthReserved)
	if e != nil {
		return out, reject("NATIVE_STANDING_CAP_EXCEEDED", e)
	}
	month, e := grant.Terms.MonthlyCap.Sub(monthUsed)
	if e != nil {
		return out, reject("NATIVE_STANDING_CAP_EXCEEDED", e)
	}
	amount = nativeResourceMinimum(amount, nativeResourceMinimum(life, month))
	if amount.IsZero() {
		return out, nil
	}
	configuration, e := view.Version.Compiled.Decode()
	if e != nil {
		return out, reject("EVIDENCE_INVALID", e)
	}
	cfg, ok := configuration.(*policy.LongTermGoalConfig)
	if !ok {
		return out, reject("EVIDENCE_INVALID", nil)
	}
	if !cfg.AllowPartial && amount.Cents() < preview.Amount.Cents() {
		return out, nil
	}
	afterRaw, e := encode(beforeInput)
	if e != nil {
		return out, e
	}
	var afterInput planning.ForecastInput
	if auditJSON(afterRaw, &afterInput) != nil {
		return out, reject("EVIDENCE_INVALID", nil)
	}
	for i := range afterInput.Facts.Goals {
		g := &afterInput.Facts.Goals[i]
		if g.ID == goal {
			g.Owned, e = g.Owned.Add(amount)
			if e != nil {
				return out, e
			}
			g.CashOwned, e = g.CashOwned.Add(amount)
			if e != nil {
				return out, e
			}
			g.MonthContributed, e = g.MonthContributed.Add(amount)
			if e != nil {
				return out, e
			}
		}
	}
	for i := range afterInput.Facts.Income {
		if afterInput.Facts.Income[i].ID == origin {
			afterInput.Facts.Income[i].Available, e = afterInput.Facts.Income[i].Available.Sub(amount)
			if e != nil {
				return out, e
			}
		}
	}
	after, e := planning.Forecast365(afterInput)
	if e != nil {
		return out, reject("PROTECTION_INCOMPLETE", e)
	}
	if !after.MaximumDeficit.IsZero() {
		return out, reject("PROTECTION_DEFICIT", nil)
	}
	authority := policy.NativeStandingCandidate{Scope: grant.Scope, Family: grant.Terms.Family, Source: source, Policies: grant.Terms.Policies, AccountIDs: []funds.ID{account}, GoalIDs: []funds.ID{goal}, Amount: amount}
	if e = policy.AuthorizeNativeStanding(grant.Terms, "ACTIVE", authority, usage, now); e != nil {
		return out, reject(e.Error(), e)
	}
	out = nativeStandingAllocationCandidate{Ledger: captureNativeAssetLedger(ledger), Policies: snapshot.Views, InventoryOriginal: append(json.RawMessage(nil), snapshot.InventoryRaw...), Usage: usage, GoalVersion: goalVersion, Authority: authority, Use: funds.IncomeUse{FragmentID: fragment, OriginID: origin, AccountID: account, Amount: amount}, SourceHash: snapshot.Facts.SourceHash, InventoryHash: Hash(snapshot.InventoryRaw), PolicyPreview: preview, ForecastInput: beforeInput, ForecastBefore: before, ForecastAfterInput: afterInput, ForecastAfter: after}
	return out, nil
}

func (s *Service) admitStandingAllocation(ctx context.Context, c NativeOccurrenceClaim) error {
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
	if c.Occurrence.Family != policy.NativeSingleGoal || grant.Grant.Terms.Family != c.Occurrence.Family {
		return reject("FAMILY_NOT_QUALIFIED", nil)
	}
	if err = ensureGoalAvailable(ctx, tx, p, grant.Grant.Terms.GoalIDs[0]); err != nil {
		return err
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
	var goalVersion uint64
	if err = tx.QueryRow(ctx, `SELECT version FROM goals WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), grant.Grant.Terms.GoalIDs[0].String()).Scan(&goalVersion); err != nil {
		return err
	}
	candidate, err := standingAllocationCandidate(snapshot, ledger, grant.Grant, c.Occurrence.Source, usage, goalVersion, now)
	if err != nil {
		return err
	}
	if candidate.Authority.Amount.IsZero() {
		if err = nativeFinishOccurrence(ctx, tx, c, "NO_ACTION", "NO_CURRENT_ELIGIBLE_AMOUNT"); err != nil {
			return err
		}
		return tx.Commit(ctx)
	}
	operation, err := newID()
	if err != nil {
		return err
	}
	permit, err := newID()
	if err != nil {
		return err
	}
	previewID, err := newID()
	if err != nil {
		return err
	}
	jobID, err := newID()
	if err != nil {
		return err
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
	deps := planning.DependencyVector{FundsVersion: fv, PolicyVersion: grant.Grant.Terms.Policies[0].Version, GrantVersion: grant.Grant.Version, ProductTermsVersion: 1, ProductAvailabilityVersion: 1, SourceEvidenceVersion: sv, WriterGeneration: env.WriterGeneration, ControlVersion: env.ControlVersion}
	plan := nativeStandingAllocationPlan{Protocol: NativeAutomationProtocol + "/allocation-plan", ID: operation, Scope: nativeAutomationScope(p), Occurrence: c.Occurrence, Grant: grant.Grant, GrantStateSequence: grant.StateSequence, Candidate: candidate, Dependencies: deps, PermitID: permit, CreatedAt: now, ExpiresAt: expires}
	planRaw, err := encode(plan)
	if err != nil || len(planRaw) > 8<<20 {
		return reject("NATIVE_STANDING_CAPTURE_CAPACITY", err)
	}
	planHash := Hash(planRaw)
	ref := grant.Grant.Terms.Policies[0]
	amount := candidate.Authority.Amount
	request := bankv2.Request{Protocol: bankv2.Protocol, Namespace: s.Namespace, Key: operation.String(), OperationID: operation.String(), UserID: p.UserID.String(), PermitID: permit.String(), ReferenceHash: planHash, RequestedAt: now, ExpiresAt: expires, Effect: bankv2.Effect{Kind: bankv2.Allocate, BusinessKey: c.Occurrence.Key, Amount: amount, CashUses: []bankv2.CashUse{{AccountID: candidate.Use.AccountID.String(), Amount: amount}}, OriginUses: []bankv2.OriginUse{{OriginID: candidate.Use.OriginID.String(), AccountID: candidate.Use.AccountID.String(), Amount: amount, Bucket: "AVAILABLE"}}, ReturnUses: []bankv2.ReturnUse{}, DestinationGoalID: candidate.Authority.GoalIDs[0].String(), LatestArrivalAt: expires}, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: grant.Grant.ID.String(), Version: int64(grant.Grant.Version), GoalID: candidate.Authority.GoalIDs[0].String(), PolicyID: ref.PolicyID.String(), PolicyVersion: int64(ref.Version)}}}
	bankRaw, err := request.CanonicalBytes()
	if err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	bankHash, _ := request.Digest()
	effectHash, _ := request.Effect.Digest()
	depsRaw, _ := encode(deps)
	refsRaw, _ := encode(grant.Grant.Terms.Policies)
	command := nativeStandingExecutionOriginal{Protocol: NativeAutomationProtocol + "/allocation-execution", PlanID: operation, PlanHash: planHash, Request: request}
	commandRaw, _ := encode(command)
	_, err = tx.Exec(ctx, `INSERT INTO native_standing_plans(environment_id,user_id,id,occurrence_id,grant_id,grant_version,original,original_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, p.EnvironmentID.String(), p.UserID.String(), operation.String(), c.Occurrence.ID.String(), grant.Grant.ID.String(), grant.Grant.Version, planRaw, planHash, now, expires)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_previews(environment_id,user_id,id,kind,original,original_hash,effect_hash,policy_refs,source_hash,dependencies,dependencies_hash,bank_request,bank_request_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`, p.EnvironmentID.String(), p.UserID.String(), previewID.String(), nativeStandingAllocateKind, commandRaw, Hash(commandRaw), effectHash, refsRaw, candidate.SourceHash, depsRaw, Hash(depsRaw), bankRaw, bankHash, now, expires)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_operations(environment_id,user_id,id,command_kind,idempotency_key,request,request_hash,bank_namespace,bank_key,bank_request,bank_request_hash,effect_hash,dependencies,dependencies_hash,plan_id,preview_id,group_id,child_number,subject_goal_ids,mode,grant_id,grant_version,permit_id,reserved_funds_version,created_at,updated_at) VALUES($1,$2,$3::uuid,$4,$5,$6,$7,$8,$3::text,$9,$10,$11,$12,$13,$3::uuid,$14,$15,1,ARRAY[$16::uuid],'STANDING',$17,$18,$19,$20,$21,$21)`, p.EnvironmentID.String(), p.UserID.String(), operation.String(), nativeStandingAllocateKind, "native_standing_"+c.Occurrence.Key, commandRaw, Hash(commandRaw), s.Namespace, bankRaw, bankHash, effectHash, depsRaw, Hash(depsRaw), previewID.String(), c.Occurrence.ID.String(), candidate.Authority.GoalIDs[0].String(), grant.Grant.ID.String(), grant.Grant.Version, permit.String(), fv+1, now)
	if err != nil {
		return err
	}
	ledger.Income, err = ledger.Income.Reserve(operation, []funds.IncomeUse{candidate.Use}, funds.AllocateGoal, now, nil)
	if err != nil {
		return reject("FUNDS_UNAVAILABLE", err)
	}
	ledger.Holds = append(ledger.Holds, postgres.NativeHold{OperationID: operation, ResourceKind: "CASH", ResourceID: candidate.Use.AccountID.String(), Amount: amount, State: funds.Reserved})
	budgetRaw, _ := encode(struct {
		Protocol     string      `json:"protocol"`
		OccurrenceID funds.ID    `json:"occurrence_id"`
		PlanID       funds.ID    `json:"plan_id"`
		PlanHash     string      `json:"plan_hash"`
		Amount       funds.Money `json:"amount_cents"`
		Period       string      `json:"period"`
	}{NativeAutomationProtocol + "/budget", c.Occurrence.ID, operation, planHash, amount, usage.Period})
	err = reserveNativeOccurrenceBudget(ctx, tx, p, c, grant.Grant, amount, budgetRaw, now)
	if err != nil {
		return err
	}
	err = bindNativeOccurrenceOperation(ctx, tx, p, c.Occurrence.ID, operation, amount, effectHash, bankHash, now)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_jobs(environment_id,user_id,id,operation_id,next_attempt_at) VALUES($1,$2,$3,$4,clock_timestamp())`, p.EnvironmentID.String(), p.UserID.String(), jobID.String(), operation.String())
	if err != nil {
		return err
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
	if err = tx.Commit(ctx); err != nil {
		return err
	}
	if s.Failpoint != nil {
		return s.Failpoint("native_standing_after_t1_commit")
	}
	return nil
}

func loadStandingAllocationPlan(ctx context.Context, q queryRow, p Principal, id funds.ID) (nativeStandingAllocationPlan, string, error) {
	var out nativeStandingAllocationPlan
	var raw []byte
	var hash, namespace string
	err := q.QueryRow(ctx, `SELECT p.original,p.original_hash,e.namespace FROM native_standing_plans p JOIN environments e ON(e.id=p.environment_id) WHERE p.environment_id=$1 AND p.user_id=$2 AND p.id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&raw, &hash, &namespace)
	if err != nil {
		return out, "", err
	}
	if Hash(raw) != hash || auditJSON(raw, &out) != nil || out.Protocol != NativeAutomationProtocol+"/allocation-plan" || out.ID != id || out.Scope != nativeAutomationScope(p) || out.Grant.Scope != out.Scope || out.Occurrence.Scope != out.Scope || out.Occurrence.GrantID != out.Grant.ID || out.Occurrence.GrantVersion != out.Grant.Version || out.Grant.Terms.Validate() != nil || out.Grant.PrincipalKind != "USER" || out.Grant.SessionID == (funds.ID{}) || !out.Grant.Terms.EffectiveFrom.Equal(out.Grant.ConfirmedAt) || out.Candidate.Authority.Scope != out.Scope || out.Candidate.Authority.Amount.IsZero() || out.Dependencies.Validate() != nil || !out.ExpiresAt.After(out.CreatedAt) || out.ExpiresAt.After(out.CreatedAt.Add(2*time.Minute)) {
		return out, "", reject("EVIDENCE_INVALID", nil)
	}
	if out.Joint != nil {
		return out, hash, replayStandingJointAllocation(out, namespace)
	}
	if out.Grant.Terms.Family != policy.NativeSingleGoal || out.JointChild != 0 {
		return out, "", reject("EVIDENCE_INVALID", nil)
	}
	before, e := planning.Forecast365(out.Candidate.ForecastInput)
	if e != nil {
		return out, "", reject("EVIDENCE_INVALID", e)
	}
	after, e := planning.Forecast365(out.Candidate.ForecastAfterInput)
	if e != nil {
		return out, "", reject("EVIDENCE_INVALID", e)
	}
	a, _ := encode(before)
	b, _ := encode(out.Candidate.ForecastBefore)
	x, _ := encode(after)
	y, _ := encode(out.Candidate.ForecastAfter)
	if string(a) != string(b) || string(x) != string(y) || !before.MaximumDeficit.IsZero() || !after.MaximumDeficit.IsZero() {
		return out, "", reject("EVIDENCE_INVALID", nil)
	}
	ledger, err := out.Candidate.Ledger.DomainLedger()
	if err != nil || ledger.Namespace != namespace || ledger.Income.Snapshot().UserID != out.Scope.UserID || !ledger.Income.Snapshot().AsOf.Equal(out.CreatedAt) || Hash(out.Candidate.InventoryOriginal) != out.Candidate.InventoryHash {
		return out, "", reject("EVIDENCE_INVALID", err)
	}
	input := out.Candidate.ForecastInput
	d := out.Dependencies
	snapshot := templateSnapshot{Facts: input.Facts, Versions: input.Versions, Views: out.Candidate.Policies, Retained: input.RetainedObligations, Held: input.HeldCash, UnassignedGoalCash: input.UnassignedGoalCash, InventoryRaw: out.Candidate.InventoryOriginal, FundsVersion: d.FundsVersion, SourceVersion: d.SourceEvidenceVersion, WriterGeneration: d.WriterGeneration, ControlVersion: d.ControlVersion, AsOf: out.CreatedAt, Timezone: out.Grant.Terms.Timezone, Native: &ledger}
	snapshot.ExternalLiabilities = input.ExternalLiabilities
	expected, err := standingAllocationCandidate(snapshot, ledger, out.Grant, out.Occurrence.Source, out.Candidate.Usage, out.Candidate.GoalVersion, out.CreatedAt)
	if err != nil {
		return out, "", reject("EVIDENCE_INVALID", err)
	}
	left, _ := encode(expected)
	right, _ := encode(out.Candidate)
	if string(left) != string(right) {
		return out, "", reject("EVIDENCE_INVALID", nil)
	}
	return out, hash, nil
}
