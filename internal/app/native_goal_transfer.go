package app

import (
	"context"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const NativeGoalTransferProtocol = "native-paired-goal-transfer-v1"

type NativeGoalTransferPreviewRequest struct {
	PolicyVersionID funds.ID    `json:"policy_version_id"`
	SourceGoalID    funds.ID    `json:"source_goal_id"`
	TargetGoalID    funds.ID    `json:"target_goal_id"`
	Amount          funds.Money `json:"amount_cents"`
	Timezone        string      `json:"timezone"`
}
type NativeGoalTransferCommand struct {
	PreviewID     funds.ID `json:"preview_id"`
	ReviewedHash  string   `json:"reviewed_hash"`
	ReleaseSource bool     `json:"release_source"`
	ReceiveTarget bool     `json:"receive_target"`
}
type NativeGoalTransferPreview struct {
	NativeActionPreviewHeader
	Protocol       string                           `json:"protocol"`
	Timezone       string                           `json:"timezone"`
	Request        NativeGoalTransferPreviewRequest `json:"request"`
	PairedInput    planning.PairedGoalTransferInput `json:"paired_input"`
	PairedEffect   planning.PairedGoalTransfer      `json:"paired_effect"`
	BankEffect     bankv2.Effect                    `json:"bank_effect"`
	LotUses        []NativeGoalTransferUse          `json:"lot_uses"`
	ForecastInput  planning.ForecastInput           `json:"forecast_input"`
	ForecastBefore planning.ForecastResult          `json:"forecast_before"`
	ForecastAfter  planning.ForecastResult          `json:"forecast_after"`
	ReviewedHash   string                           `json:"reviewed_hash"`
}

func NativeGoalTransferReviewHash(preview NativeGoalTransferPreview) (string, error) {
	preview.ReviewedHash = ""
	raw, err := encode(preview)
	return Hash(raw), err
}

func goalTransferCounterfactual(before planning.ForecastInput, source, target funds.ID, amount funds.Money) (planning.ForecastInput, error) {
	var after planning.ForecastInput
	raw, err := encode(before)
	if err != nil || decode(raw, &after) != nil {
		return after, reject("EVIDENCE_INVALID", err)
	}
	var sourceAccount, targetAccount funds.ID
	sourceFound, targetFound := false, false
	for i := range after.Facts.Goals {
		goal := &after.Facts.Goals[i]
		if goal.ID == source {
			sourceAccount, sourceFound = goal.AccountID, true
			goal.Owned, err = goal.Owned.Sub(amount)
			if err == nil {
				goal.CashOwned, err = goal.CashOwned.Sub(amount)
			}
		}
		if goal.ID == target {
			targetAccount, targetFound = goal.AccountID, true
			goal.Owned, err = goal.Owned.Add(amount)
			if err == nil {
				goal.CashOwned, err = goal.CashOwned.Add(amount)
			}
		}
		if err != nil {
			return after, reject("GOAL_CASH_UNAVAILABLE", err)
		}
	}
	if !sourceFound || !targetFound || sourceAccount == targetAccount {
		return after, reject("GOAL_TRANSFER_INVALID", nil)
	}
	sourceFound, targetFound = false, false
	for i := range after.Facts.Accounts {
		account := &after.Facts.Accounts[i]
		if account.ID == sourceAccount {
			account.Balance, err = account.Balance.Sub(amount)
			sourceFound = true
		} else if account.ID == targetAccount {
			account.Balance, err = account.Balance.Add(amount)
			targetFound = true
		}
		if err != nil {
			return after, reject("GOAL_TRANSFER_CAPACITY", err)
		}
	}
	if !sourceFound || !targetFound {
		return after, reject("EVIDENCE_INVALID", nil)
	}
	// Existing money changes its owner and location. No income, contributed
	// amount, future-income assumption or historical payment is manufactured.
	return after, nil
}

// Isolate each explicitly protected policy, retaining its own obligations and
// goal-owned cash, then compare EVERY protection point. A scalar max alone
// would hide a reduction at a different date.
func goalTransferProtectedReferences(before, after planning.ForecastInput, ids []funds.ID) ([]policy.TemplatePolicyReference, error) {
	refs := []policy.TemplatePolicyReference{}
	for _, id := range ids {
		var version policy.TemplateVersion
		found := false
		for _, v := range before.Versions {
			if v.PolicyID == id {
				if found {
					return nil, reject("EVIDENCE_INVALID", nil)
				}
				version, found = v, true
			}
		}
		if !found {
			return nil, reject("GOAL_TRANSFER_PROTECTED_REFERENCE_MISSING", nil)
		}
		isolate := func(in planning.ForecastInput) (planning.ForecastResult, error) {
			var input planning.ForecastInput
			raw, err := encode(in)
			if err != nil || decode(raw, &input) != nil {
				return planning.ForecastResult{}, reject("EVIDENCE_INVALID", err)
			}
			input.Versions = []policy.TemplateVersion{version}
			input.HeldCash, input.UnassignedGoalCash = funds.Money{}, funds.Money{}
			input.FutureIncome = []planning.ForecastIncome{}
			input.PrincipalReturns = []planning.ForecastPrincipalReturn{}
			for i := range input.Facts.Goals {
				if input.Facts.Goals[i].PolicyID != id {
					input.Facts.Goals[i].CashOwned = funds.Money{}
				}
			}
			input.RetainedObligations = nil
			for _, obligation := range in.RetainedObligations {
				if obligation.PolicyID == id {
					input.RetainedObligations = append(input.RetainedObligations, obligation)
				}
			}
			input.RetainedFloors = nil
			for _, floor := range in.RetainedFloors {
				if floor.PolicyID == id {
					input.RetainedFloors = append(input.RetainedFloors, floor)
				}
			}
			return planning.Forecast365(input)
		}
		left, err := isolate(before)
		if err != nil {
			return nil, reject("GOAL_TRANSFER_PROTECTION_INCOMPLETE", err)
		}
		right, err := isolate(after)
		if err != nil || len(left.Points) != len(right.Points) {
			return nil, reject("GOAL_TRANSFER_PROTECTION_INCOMPLETE", err)
		}
		var leftMax, rightMax funds.Money
		for i, point := range left.Points {
			changed := right.Points[i]
			if point.ID != changed.ID || point.At != changed.At || changed.Protected.Cents() < point.Protected.Cents() {
				return nil, reject("GOAL_TRANSFER_PROTECTED_REFERENCE_REDUCED", nil)
			}
			if point.Protected.Cents() > leftMax.Cents() {
				leftMax = point.Protected
			}
			if changed.Protected.Cents() > rightMax.Cents() {
				rightMax = changed.Protected
			}
		}
		refs = append(refs, policy.TemplatePolicyReference{PolicyID: id, VersionID: version.VersionID, ConfigurationHash: version.Compiled.Hash, Current: version.Current, ConfirmationVerified: version.ConfirmationVerified, ImpactVerified: true, ProtectedBefore: leftMax, ProtectedAfter: rightMax})
	}
	return refs, nil
}

func goalTransferUsage(ctx context.Context, tx pgx.Tx, p Principal, namespace string, policyID funds.ID) (funds.Money, error) {
	var total funds.Money
	rows, err := tx.Query(ctx, `SELECT bank_request,bank_request_hash::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND command_kind='TRANSFER_GOAL' AND status NOT IN ('REJECTED','CANCELLED') ORDER BY id LIMIT 10001`, p.EnvironmentID.String(), p.UserID.String())
	if err != nil {
		return total, err
	}
	defer rows.Close()
	count := 0
	for rows.Next() {
		count++
		if count > 10000 {
			return funds.Money{}, reject("GOAL_TRANSFER_CAPACITY", nil)
		}
		var raw []byte
		var stored string
		if err = rows.Scan(&raw, &stored); err != nil {
			return funds.Money{}, err
		}
		var request bankv2.Request
		if auditJSON(raw, &request) != nil {
			return funds.Money{}, reject("EVIDENCE_INVALID", nil)
		}
		hash, e := request.Digest()
		if e != nil || hash != stored || request.Effect.Kind != bankv2.MoveGoal || request.Namespace != namespace || request.UserID != p.UserID.String() {
			return funds.Money{}, reject("EVIDENCE_INVALID", e)
		}
		for _, authority := range request.Authorities {
			if authority.Role == "EXECUTE" && authority.PolicyID == policyID.String() {
				total, err = total.Add(request.Effect.Amount)
				if err != nil {
					return funds.Money{}, err
				}
			}
		}
	}
	return total, rows.Err()
}

func deriveGoalTransferPreview(snapshot templateSnapshot, request NativeGoalTransferPreviewRequest, id, session funds.ID, usage funds.Money) (NativeGoalTransferPreview, error) {
	var out NativeGoalTransferPreview
	if snapshot.Native == nil || request.Amount.IsZero() || request.PolicyVersionID == (funds.ID{}) || request.SourceGoalID == (funds.ID{}) || request.TargetGoalID == (funds.ID{}) || request.SourceGoalID == request.TargetGoalID {
		return out, reject("GOAL_TRANSFER_INVALID", nil)
	}
	var cross policy.TemplateVersion
	for _, view := range snapshot.Views {
		if view.Version.VersionID == request.PolicyVersionID {
			cross = view.Version
		}
	}
	decoded, err := cross.Compiled.Decode()
	if err != nil {
		return out, reject("GOAL_TRANSFER_POLICY_REQUIRED", err)
	}
	config, ok := decoded.(*policy.CrossGoalReallocationConfig)
	if !ok {
		return out, reject("GOAL_TRANSFER_POLICY_REQUIRED", nil)
	}
	var source, target policy.TemplateGoalFact
	for _, goal := range snapshot.Facts.Goals {
		if goal.ID == request.SourceGoalID {
			source = goal
		}
		if goal.ID == request.TargetGoalID {
			target = goal
		}
	}
	if source.GoalVersion == nil || target.GoalVersion == nil {
		return out, reject("GOAL_TRANSFER_GOAL_VERSION_REQUIRED", nil)
	}
	sourceConfig, e := source.GoalVersion.Compiled.Decode()
	if e != nil {
		return out, reject("EVIDENCE_INVALID", e)
	}
	longTerm, ok := sourceConfig.(*policy.LongTermGoalConfig)
	if !ok {
		return out, reject("GOAL_TRANSFER_GOAL_VERSION_REQUIRED", nil)
	}
	var held funds.Money
	for _, hold := range snapshot.Native.Holds {
		if hold.ResourceKind == "GOAL" && hold.ResourceID == source.ID.String() && hold.State == funds.Reserved {
			held, err = held.Add(hold.Amount)
			if err != nil {
				return out, err
			}
		}
	}
	source.ReleaseableCash, err = source.CashOwned.Sub(held)
	if err != nil {
		return out, reject("EVIDENCE_INVALID", err)
	}
	minimum := longTerm.MinimumGuarantee
	zone, e := policy.TemplateLocation(snapshot.Timezone)
	if e != nil {
		return out, e
	}
	if snapshot.AsOf.In(zone).Format("2006-01-02") >= longTerm.Deadline && !longTerm.AllowPartial && !longTerm.AllowDeferral {
		minimum = longTerm.Target
	}
	remaining := max(int64(0), source.Owned.Cents()-minimum.Cents())
	if source.ReleaseableCash.Cents() > remaining {
		source.ReleaseableCash, _ = funds.NewMoney(remaining)
	}
	uses, err := goalTransferLotUses(*snapshot.Native, source.ID, source.AccountID, request.Amount)
	if err != nil {
		return out, err
	}
	beforeInput := snapshot.forecastInput(365)
	afterInput, err := goalTransferCounterfactual(beforeInput, source.ID, target.ID, request.Amount)
	if err != nil {
		return out, err
	}
	before, err := planning.Forecast365(beforeInput)
	if err != nil {
		return out, reject("GOAL_TRANSFER_PROTECTION_INCOMPLETE", err)
	}
	after, err := planning.Forecast365(afterInput)
	if err != nil || !after.MaximumDeficit.IsZero() {
		return out, reject("GOAL_TRANSFER_PROTECTION_DEFICIT", err)
	}
	refs, err := goalTransferProtectedReferences(beforeInput, afterInput, config.MustNotReducePolicyIDs)
	if err != nil {
		return out, err
	}
	deps := planning.DependencyVector{FundsVersion: snapshot.FundsVersion, PolicyVersion: cross.Version, ProductTermsVersion: 1, ProductAvailabilityVersion: 1, SourceEvidenceVersion: snapshot.SourceVersion, WriterGeneration: snapshot.WriterGeneration, ControlVersion: snapshot.ControlVersion}
	basis, _ := encode(struct {
		Dependencies      planning.DependencyVector
		Source, Inventory string
	}{deps, snapshot.Facts.SourceHash, Hash(snapshot.InventoryRaw)})
	input := planning.PairedGoalTransferInput{Policy: cross, Source: source, Target: target, Amount: request.Amount, AlreadyUsedAndHeld: usage, UsageVerified: true, ProtectedReferences: refs, Now: snapshot.AsOf, Timezone: snapshot.Timezone, DependencyHash: Hash(basis)}
	paired, err := planning.PreviewPairedGoalTransfer(input)
	if err != nil {
		return out, reject("GOAL_TRANSFER_NOT_AUTHORIZED", err)
	}
	expires := snapshot.AsOf.Add(2 * time.Minute)
	effect, err := goalTransferEffect(id, source.ID, target.ID, source.AccountID, target.AccountID, request.Amount, uses, expires)
	if err != nil {
		return out, err
	}
	effectHash, err := effect.Digest()
	if err != nil {
		return out, err
	}
	request.Timezone = snapshot.Timezone
	out = NativeGoalTransferPreview{NativeActionPreviewHeader: NativeActionPreviewHeader{ID: id, Scope: snapshot.Facts.Scope, SessionID: session, Kind: bankv2.MoveGoal, EffectHash: effectHash, Dependencies: deps, SourceHash: snapshot.Facts.SourceHash, InventoryHash: Hash(snapshot.InventoryRaw), CreatedAt: snapshot.AsOf, ExpiresAt: expires}, Protocol: NativeGoalTransferProtocol, Timezone: snapshot.Timezone, Request: request, PairedInput: input, PairedEffect: paired, BankEffect: effect, LotUses: uses, ForecastInput: beforeInput, ForecastBefore: before, ForecastAfter: after}
	out.ReviewedHash, err = NativeGoalTransferReviewHash(out)
	return out, err
}

func replayGoalTransferPreview(preview NativeGoalTransferPreview) error {
	if preview.Protocol != NativeGoalTransferProtocol || preview.Kind != bankv2.MoveGoal || preview.BankAuthority || preview.PairedEffect.BankAuthority || preview.PairedEffect.CreatesIncome || preview.PairedInput.Policy.Scope != preview.Scope || preview.Request.PolicyVersionID != preview.PairedInput.Policy.VersionID || preview.Request.SourceGoalID != preview.PairedEffect.SourceGoalID || preview.Request.TargetGoalID != preview.PairedEffect.TargetGoalID || preview.Request.Amount != preview.PairedEffect.Amount || preview.Request.Timezone != preview.Timezone || preview.PairedInput.Timezone != preview.Timezone || !preview.PairedInput.Now.Equal(preview.CreatedAt) || preview.ForecastInput.Scope != preview.Scope || !preview.ForecastInput.AsOf.Equal(preview.CreatedAt) {
		return reject("EVIDENCE_INVALID", nil)
	}
	basis, err := encode(struct {
		Dependencies      planning.DependencyVector
		Source, Inventory string
	}{preview.Dependencies, preview.SourceHash, preview.InventoryHash})
	if err != nil || preview.PairedInput.DependencyHash != Hash(basis) || preview.ForecastInput.InventoryHash != preview.InventoryHash || preview.ForecastInput.Facts.SourceHash != preview.SourceHash {
		return reject("EVIDENCE_INVALID", err)
	}
	hash, err := NativeGoalTransferReviewHash(preview)
	if err != nil || hash != preview.ReviewedHash {
		return reject("EVIDENCE_INVALID", err)
	}
	paired, err := planning.PreviewPairedGoalTransfer(preview.PairedInput)
	a, _ := encode(paired)
	b, _ := encode(preview.PairedEffect)
	if err != nil || string(a) != string(b) {
		return reject("EVIDENCE_INVALID", err)
	}
	effect, err := goalTransferEffect(preview.ID, paired.SourceGoalID, paired.TargetGoalID, paired.SourceAccountID, paired.TargetAccountID, paired.Amount, preview.LotUses, preview.ExpiresAt)
	if err != nil {
		return err
	}
	eh, err := effect.Digest()
	stored, e := preview.BankEffect.Digest()
	if err != nil || e != nil || eh != preview.EffectHash || stored != eh {
		return reject("EVIDENCE_INVALID", err)
	}
	before, err := planning.Forecast365(preview.ForecastInput)
	if err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	afterInput, err := goalTransferCounterfactual(preview.ForecastInput, paired.SourceGoalID, paired.TargetGoalID, paired.Amount)
	if err != nil {
		return err
	}
	after, err := planning.Forecast365(afterInput)
	if err != nil || !after.MaximumDeficit.IsZero() {
		return reject("EVIDENCE_INVALID", err)
	}
	decoded, err := preview.PairedInput.Policy.Compiled.Decode()
	if err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	config, ok := decoded.(*policy.CrossGoalReallocationConfig)
	if !ok {
		return reject("EVIDENCE_INVALID", nil)
	}
	refs, err := goalTransferProtectedReferences(preview.ForecastInput, afterInput, config.MustNotReducePolicyIDs)
	if err != nil {
		return err
	}
	a, _ = encode(refs)
	b, _ = encode(preview.PairedInput.ProtectedReferences)
	if string(a) != string(b) {
		return reject("EVIDENCE_INVALID", nil)
	}
	a, _ = encode(before)
	b, _ = encode(preview.ForecastBefore)
	if string(a) != string(b) {
		return reject("EVIDENCE_INVALID", nil)
	}
	a, _ = encode(after)
	b, _ = encode(preview.ForecastAfter)
	if string(a) != string(b) {
		return reject("EVIDENCE_INVALID", nil)
	}
	for _, point := range after.Points {
		margin, e := strconv.ParseInt(point.MarginCents, 10, 64)
		if e != nil || margin < 0 {
			return reject("EVIDENCE_INVALID", e)
		}
	}
	return nil
}

func (s *Service) PreviewNativeGoalTransfer(ctx context.Context, p Principal, in NativeGoalTransferPreviewRequest) (NativeGoalTransferPreview, error) {
	var out NativeGoalTransferPreview
	if err := s.auditHuman(ctx, p); err != nil {
		return out, err
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return out, err
	}
	defer rollback(ctx, tx)
	zone, err := templateScopedTimezone(ctx, tx, p, in.Timezone)
	if err != nil {
		return out, err
	}
	snapshot, err := s.loadTemplateSnapshot(ctx, tx, p, s.now(), zone)
	if err != nil {
		return out, err
	}
	for _, goal := range []funds.ID{in.SourceGoalID, in.TargetGoalID} {
		if err = ensureGoalAvailable(ctx, tx, p, goal); err != nil {
			return out, err
		}
	}
	var policyID funds.ID
	for _, view := range snapshot.Views {
		if view.Version.VersionID == in.PolicyVersionID {
			policyID = view.Version.PolicyID
		}
	}
	usage, err := goalTransferUsage(ctx, tx, p, s.Namespace, policyID)
	if err != nil {
		return out, err
	}
	id, err := newID()
	if err != nil {
		return out, err
	}
	out, err = deriveGoalTransferPreview(snapshot, in, id, p.SessionID, usage)
	if err != nil {
		return out, err
	}
	if err = tx.Commit(ctx); err != nil {
		return out, err
	}
	raw, err := encode(out)
	if err != nil {
		return out, err
	}
	if err = replayGoalTransferPreview(out); err != nil {
		return out, err
	}
	err = s.saveNativeActionPreview(ctx, s.Pool, p, out.NativeActionPreviewHeader, raw)
	return out, err
}

func loadGoalTransferPreview(ctx context.Context, q queryRow, p Principal, id funds.ID) (NativeGoalTransferPreview, error) {
	var out NativeGoalTransferPreview
	header, raw, err := loadNativeActionPreview(ctx, q, p, bankv2.MoveGoal, id)
	if err != nil {
		return out, err
	}
	if auditJSON(raw, &out) != nil {
		return out, reject("EVIDENCE_INVALID", nil)
	}
	a, _ := encode(header)
	b, _ := encode(out.NativeActionPreviewHeader)
	if string(a) != string(b) {
		return out, reject("EVIDENCE_INVALID", nil)
	}
	return out, replayGoalTransferPreview(out)
}

func (s *Service) NativeGoalTransferPreview(ctx context.Context, p Principal, id funds.ID) (NativeGoalTransferPreview, error) {
	if err := s.auditHuman(ctx, p); err != nil {
		return NativeGoalTransferPreview{}, err
	}
	return loadGoalTransferPreview(ctx, s.Pool, p, id)
}
