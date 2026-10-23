package app

import (
	"context"
	"time"

	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const nativeStandingAssetProtocol = "native-standing-asset-v1"

type nativeStandingAssetPlan struct {
	Protocol           string                         `json:"protocol"`
	ID                 funds.ID                       `json:"id"`
	Scope              policy.TemplateOwnerScope      `json:"scope"`
	Occurrence         NativeOccurrence               `json:"occurrence"`
	Grant              NativeStandingGrant            `json:"grant"`
	GrantStateSequence uint64                         `json:"grant_state_sequence"`
	Source             nativeStandingAssetSource      `json:"source"`
	Usage              policy.NativeStandingUsage     `json:"usage"`
	Authority          policy.NativeStandingCandidate `json:"authority"`
	Preview            NativeAssetPreview             `json:"preview"`
	PermitID           funds.ID                       `json:"permit_id"`
}

func standingAssetBudget(grant NativeStandingGrant, usage policy.NativeStandingUsage) (funds.Money, error) {
	lifeUsed, err := funds.SumMoney(usage.LifetimeCommitted, usage.LifetimeReserved)
	if err != nil {
		return funds.Money{}, reject("NATIVE_STANDING_CAP_EXCEEDED", err)
	}
	monthUsed, err := funds.SumMoney(usage.MonthCommitted, usage.MonthReserved)
	if err != nil {
		return funds.Money{}, reject("NATIVE_STANDING_CAP_EXCEEDED", err)
	}
	life, err := grant.Terms.LifetimeCap.Sub(lifeUsed)
	if err != nil {
		return funds.Money{}, reject("NATIVE_STANDING_CAP_EXCEEDED", err)
	}
	month, err := grant.Terms.MonthlyCap.Sub(monthUsed)
	if err != nil {
		return funds.Money{}, reject("NATIVE_STANDING_CAP_EXCEEDED", err)
	}
	return nativeResourceMinimum(grant.Terms.SingleCap, nativeResourceMinimum(life, month)), nil
}

func standingAssetAuthority(grant NativeStandingGrant, source policy.NativeOccurrenceSource, preview NativeAssetPreview) (policy.NativeStandingCandidate, error) {
	accounts := []funds.ID{}
	for _, use := range preview.BankEffect.CashUses {
		id, err := funds.ParseID(use.AccountID)
		if err != nil {
			return policy.NativeStandingCandidate{}, err
		}
		accounts = append(accounts, id)
	}
	goals := []funds.ID{}
	if preview.BankEffect.SourceGoalID != "" {
		id, err := funds.ParseID(preview.BankEffect.SourceGoalID)
		if err != nil {
			return policy.NativeStandingCandidate{}, err
		}
		goals = append(goals, id)
	}
	return policy.NativeStandingCandidate{Scope: grant.Scope, Family: grant.Terms.Family, Source: source, Policies: grant.Terms.Policies, AccountIDs: accounts, GoalIDs: goals, ProductID: preview.BankEffect.Product.Terms.ProductID, Amount: preview.BankEffect.Amount, Fee: preview.BankEffect.Fee, Loss: preview.BankEffect.Loss}, nil
}

func standingAssetCandidate(snapshot templateSnapshot, grant NativeStandingGrant, source nativeStandingAssetSource, usage policy.NativeStandingUsage, id funds.ID, state bankv2.CurrentState, pending []bankv2.Request) (NativeAssetPreview, policy.NativeStandingCandidate, error) {
	var zero NativeAssetPreview
	var noAuthority policy.NativeStandingCandidate
	if grant.Terms.Validate() != nil || source.Family != grant.Terms.Family || source.Source.Validate(source.Family) != nil || snapshot.Native == nil {
		return zero, noAuthority, reject("EVIDENCE_INVALID", nil)
	}
	cap, err := standingAssetBudget(grant, usage)
	if err != nil || cap.IsZero() {
		return zero, noAuthority, err
	}
	for _, reference := range grant.Terms.Policies {
		if reference.Template != policy.AssetAuthorizationTemplate {
			continue
		}
		for _, product := range grant.Terms.ProductIDs {
			preview, e := deriveNativeAssetPreviewWithSource(snapshot, NativeAssetRequest{Kind: bankv2.Purchase, PolicyVersionID: reference.VersionID, ProductID: product, Amount: cap, Timezone: grant.Terms.Timezone}, id, funds.ID{}, state, nil, pending, &source, grant.Terms.ExpiresAt)
			if e != nil {
				switch Code(e) {
				case "ASSET_PRODUCT_UNAVAILABLE", "ASSET_POLICY_REJECTED", "ASSET_NO_SAFE_PURCHASE", "ASSET_BELOW_MINIMUM", "ASSET_PROTECTION_DEFICIT", "ASSET_EXIT_AFTER_USE":
					continue
				default:
					return zero, noAuthority, e
				}
			}
			if preview.Policy.Version.PolicyID != reference.PolicyID || preview.Policy.Version.Version != reference.Version || preview.Policy.Version.Compiled.Hash != reference.ConfigurationHash {
				return zero, noAuthority, reject("NATIVE_GRANT_POLICY_CHANGED", nil)
			}
			preview.Dependencies.GrantVersion = grant.Version
			preview.ReviewedHash, e = NativeAssetReviewHash(preview)
			if e != nil {
				return zero, noAuthority, e
			}
			authority, e := standingAssetAuthority(grant, source.Source, preview)
			if e != nil {
				return zero, noAuthority, e
			}
			if e = policy.AuthorizeNativeStanding(grant.Terms, "ACTIVE", authority, usage, snapshot.AsOf); e != nil {
				return zero, noAuthority, reject(e.Error(), e)
			}
			return preview, authority, nil
		}
	}
	return zero, noAuthority, nil
}

func standingAssetRequest(plan nativeStandingAssetPlan, namespace, hash string) bankv2.Request {
	p := plan.Preview
	return bankv2.Request{Protocol: bankv2.Protocol, Namespace: namespace, Key: plan.ID.String(), OperationID: plan.ID.String(), UserID: plan.Scope.UserID.String(), PermitID: plan.PermitID.String(), ReferenceHash: hash, RequestedAt: p.CreatedAt, ExpiresAt: p.ExpiresAt, Effect: p.BankEffect, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: plan.Grant.ID.String(), Version: int64(plan.Grant.Version), GoalID: p.BankEffect.SourceGoalID, PolicyID: p.Policy.Version.PolicyID.String(), PolicyVersion: int64(p.Policy.Version.Version)}}}
}

func nativeStandingCurrentReferences(ctx context.Context, q templateQuery, s *Service, p Principal, grant NativeStandingGrant) error {
	ids := make([]funds.ID, len(grant.Terms.Policies))
	for i, ref := range grant.Terms.Policies {
		ids[i] = ref.VersionID
	}
	refs, _, _, err := nativeGrantReferences(ctx, q, s, p, ids)
	if err != nil {
		return err
	}
	a, _ := encode(refs)
	b, _ := encode(grant.Terms.Policies)
	if string(a) != string(b) {
		return reject("NATIVE_GRANT_POLICY_CHANGED", nil)
	}
	return nil
}

func (s *Service) admitStandingAsset(ctx context.Context, c NativeOccurrenceClaim) error {
	p := Principal{EnvironmentID: c.Occurrence.Scope.EnvironmentID, UserID: c.Occurrence.Scope.UserID, Kind: "SYSTEM"}
	if c.Occurrence.Family != policy.NativeAssetAllocation && c.Occurrence.Family != policy.NativeMaturityReinvest {
		return reject("FAMILY_NOT_QUALIFIED", nil)
	}
	reader, ok := s.NativeBank.(nativeAssetBankReader)
	if !ok {
		return reject("BANK_UNAVAILABLE", nil)
	}
	state, err := reader.CurrentState(ctx, s.Namespace, p.UserID.String())
	if err != nil {
		return reject("BANK_UNAVAILABLE", nil)
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return err
	}
	if err = sameUser(p, env.ID); err != nil {
		return err
	}
	_, _, err = lockUser(ctx, tx, p)
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
	if grant.State != "ACTIVE" || grant.Grant.Terms.Family != c.Occurrence.Family || now.Before(grant.Grant.Terms.EffectiveFrom) || !now.Before(grant.Grant.Terms.ExpiresAt) {
		if err = nativeFinishOccurrence(ctx, tx, c, "BLOCKED", "NATIVE_GRANT_NOT_ACTIVE"); err != nil {
			return err
		}
		return tx.Commit(ctx)
	}
	if err = nativeStandingCurrentReferences(ctx, tx, s, p, grant.Grant); err != nil {
		return err
	}
	for _, id := range grant.Grant.Terms.GoalIDs {
		if err = ensureGoalAvailable(ctx, tx, p, id); err != nil {
			return err
		}
	}
	snapshot, err := s.loadTemplateSnapshot(ctx, tx, p, now, grant.Grant.Terms.Timezone)
	if err != nil {
		return err
	}
	usage, err := nativeStandingUsage(ctx, tx, p, grant.Grant, now, nil)
	if err != nil {
		return err
	}
	pending, err := assetPendingPurchases(ctx, tx, p, s.Namespace)
	if err != nil {
		return err
	}
	id, err := newID()
	if err != nil {
		return err
	}
	permit, err := newID()
	if err != nil {
		return err
	}
	source := nativeStandingAssetSource{Family: c.Occurrence.Family, Source: c.Occurrence.Source, AccountIDs: grant.Grant.Terms.AccountIDs}
	preview, authority, err := standingAssetCandidate(snapshot, grant.Grant, source, usage, id, state, pending)
	if err != nil {
		return err
	}
	if preview.BankEffect.Amount.IsZero() {
		if err = nativeFinishOccurrence(ctx, tx, c, "NO_ACTION", "NO_CURRENT_ELIGIBLE_AMOUNT"); err != nil {
			return err
		}
		return tx.Commit(ctx)
	}
	plan := nativeStandingAssetPlan{Protocol: nativeStandingAssetProtocol + "/plan", ID: id, Scope: grant.Grant.Scope, Occurrence: c.Occurrence, Grant: grant.Grant, GrantStateSequence: grant.StateSequence, Source: source, Usage: usage, Authority: authority, Preview: preview, PermitID: permit}
	planRaw, err := encode(plan)
	if err != nil || len(planRaw) > 8<<20 {
		return reject("NATIVE_STANDING_CAPTURE_CAPACITY", err)
	}
	planHash := Hash(planRaw)
	request := standingAssetRequest(plan, s.Namespace, planHash)
	bankRaw, err := request.CanonicalBytes()
	if err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	bankHash, _ := request.Digest()
	effectHash, _ := request.Effect.Digest()
	depsRaw, _ := encode(preview.Dependencies)
	refsRaw, _ := encode(grant.Grant.Terms.Policies)
	commandRaw, _ := encode(nativeStandingExecutionOriginal{Protocol: nativeStandingAssetProtocol + "/execution", PlanID: id, PlanHash: planHash, Request: request})
	_, err = tx.Exec(ctx, `INSERT INTO native_standing_plans(environment_id,user_id,id,occurrence_id,grant_id,grant_version,original,original_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, p.EnvironmentID.String(), p.UserID.String(), id.String(), c.Occurrence.ID.String(), grant.Grant.ID.String(), grant.Grant.Version, planRaw, planHash, now, preview.ExpiresAt)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_previews(environment_id,user_id,id,kind,original,original_hash,effect_hash,policy_refs,source_hash,dependencies,dependencies_hash,bank_request,bank_request_hash,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`, p.EnvironmentID.String(), p.UserID.String(), id.String(), string(bankv2.Purchase), commandRaw, Hash(commandRaw), effectHash, refsRaw, preview.SourceHash, depsRaw, Hash(depsRaw), bankRaw, bankHash, now, preview.ExpiresAt)
	if err != nil {
		return err
	}
	goals := []string{}
	for _, goal := range authority.GoalIDs {
		goals = append(goals, goal.String())
	}
	_, err = tx.Exec(ctx, `INSERT INTO native_operations(environment_id,user_id,id,command_kind,idempotency_key,request,request_hash,bank_namespace,bank_key,bank_request,bank_request_hash,effect_hash,dependencies,dependencies_hash,plan_id,preview_id,group_id,child_number,subject_goal_ids,mode,grant_id,grant_version,permit_id,reserved_funds_version,created_at,updated_at) VALUES($1,$2,$3::uuid,$4,$5,$6,$7,$8,$3::text,$9,$10,$11,$12,$13,$3::uuid,$3::uuid,$3::uuid,1,$14::uuid[],'STANDING',$15,$16,$17,$18,$19,$19)`, p.EnvironmentID.String(), p.UserID.String(), id.String(), string(bankv2.Purchase), "native_asset_standing_"+c.Occurrence.Key, commandRaw, Hash(commandRaw), s.Namespace, bankRaw, bankHash, effectHash, depsRaw, Hash(depsRaw), goals, grant.Grant.ID.String(), grant.Grant.Version, permit.String(), snapshot.FundsVersion+1, now)
	if err != nil {
		return err
	}
	ledger := cloneNativeAssetLedger(*snapshot.Native)
	if err = reserveNativeAssetResourcesWithSource(&ledger, id, preview, &source); err != nil {
		return err
	}
	budgetRaw, _ := encode(struct {
		Protocol string
		PlanID   funds.ID
		PlanHash string
		Amount   funds.Money
	}{nativeStandingAssetProtocol + "/budget", id, planHash, authority.Amount})
	if err = reserveNativeOccurrenceBudget(ctx, tx, p, c, grant.Grant, authority.Amount, budgetRaw, now); err != nil {
		return err
	}
	if err = bindNativeOccurrenceOperation(ctx, tx, p, c.Occurrence.ID, id, authority.Amount, effectHash, bankHash, now); err != nil {
		return err
	}
	if err = bridgeCreateJob(ctx, tx, p, id); err != nil {
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
		return s.Failpoint("native_asset_standing_after_t1_commit")
	}
	return nil
}

// Validate the immutable capture independently of current USER sessions/state.
func loadStandingAssetPlan(ctx context.Context, q queryRow, p Principal, id funds.ID) (nativeStandingAssetPlan, string, error) {
	var plan nativeStandingAssetPlan
	var raw []byte
	var hash, namespace string
	err := q.QueryRow(ctx, `SELECT p.original,p.original_hash,e.namespace FROM native_standing_plans p JOIN environments e ON(e.id=p.environment_id) WHERE p.environment_id=$1 AND p.user_id=$2 AND p.id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&raw, &hash, &namespace)
	if err != nil {
		return plan, "", err
	}
	if Hash(raw) != hash || auditJSON(raw, &plan) != nil || plan.Protocol != nativeStandingAssetProtocol+"/plan" || plan.ID != id || plan.Scope != nativeAutomationScope(p) || plan.Grant.Scope != plan.Scope || plan.Grant.Terms.Scope != plan.Scope || plan.Grant.Terms.Validate() != nil || plan.Grant.PrincipalKind != "USER" || plan.Occurrence.Scope != plan.Scope || plan.Occurrence.GrantID != plan.Grant.ID || plan.Occurrence.GrantVersion != plan.Grant.Version || plan.Occurrence.Family != plan.Grant.Terms.Family || plan.Source.Family != plan.Occurrence.Family || plan.Source.Source != plan.Occurrence.Source || plan.Preview.ID != id || plan.Preview.SessionID != (funds.ID{}) || plan.Preview.Dependencies.GrantVersion != plan.Grant.Version || plan.Preview.Dependencies.Validate() != nil || !plan.Preview.ExpiresAt.After(plan.Preview.CreatedAt) || plan.Preview.ExpiresAt.After(plan.Preview.CreatedAt.Add(2*time.Minute)) {
		return plan, "", reject("EVIDENCE_INVALID", nil)
	}
	preview := plan.Preview
	retainedGrant, err := loadNativeStandingGrant(ctx, q, p, plan.Grant.ID)
	if err != nil {
		return plan, "", err
	}
	retainedOccurrence, err := loadNativeOccurrence(ctx, q, p, plan.Occurrence.ID)
	if err != nil {
		return plan, "", err
	}
	grantRaw, _ := encode(retainedGrant.Grant)
	planGrantRaw, _ := encode(plan.Grant)
	occurrenceRaw, _ := encode(retainedOccurrence)
	planOccurrenceRaw, _ := encode(plan.Occurrence)
	accountsRaw, _ := encode(plan.Source.AccountIDs)
	grantAccountsRaw, _ := encode(plan.Grant.Terms.AccountIDs)
	if string(grantRaw) != string(planGrantRaw) || string(occurrenceRaw) != string(planOccurrenceRaw) || string(accountsRaw) != string(grantAccountsRaw) {
		return plan, "", reject("EVIDENCE_INVALID", nil)
	}
	ledger, err := preview.Ledger.DomainLedger()
	if err != nil || ledger.Namespace != namespace || ledger.Income.Snapshot().UserID != p.UserID || !ledger.Income.Snapshot().AsOf.Equal(preview.CreatedAt) || Hash(preview.InventoryOriginal) != preview.InventoryHash {
		return plan, "", reject("EVIDENCE_INVALID", err)
	}
	d := preview.Dependencies
	snapshot := templateSnapshot{Facts: preview.ForecastInput.Facts, Versions: preview.ForecastInput.Versions, Views: preview.Policies, Retained: preview.ForecastInput.RetainedObligations, Held: preview.ForecastInput.HeldCash, UnassignedGoalCash: preview.ForecastInput.UnassignedGoalCash, InventoryRaw: preview.InventoryOriginal, FundsVersion: d.FundsVersion, SourceVersion: d.SourceEvidenceVersion, WriterGeneration: d.WriterGeneration, ControlVersion: d.ControlVersion, AsOf: preview.CreatedAt, Timezone: preview.Timezone, Native: &ledger}
	snapshot.ExternalLiabilities = preview.ForecastInput.ExternalLiabilities
	expected, authority, err := standingAssetCandidate(snapshot, plan.Grant, plan.Source, plan.Usage, plan.ID, preview.BankState, preview.PendingPurchases)
	if err != nil {
		return plan, "", reject("EVIDENCE_INVALID", err)
	}
	a, _ := encode(expected)
	b, _ := encode(preview)
	c, _ := encode(authority)
	v, _ := encode(plan.Authority)
	request := standingAssetRequest(plan, namespace, hash)
	if string(a) != string(b) || string(c) != string(v) || request.Validate() != nil {
		return plan, "", reject("EVIDENCE_INVALID", nil)
	}
	return plan, hash, nil
}
