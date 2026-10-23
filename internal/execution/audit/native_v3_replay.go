// Code generated from the frozen, pure consumer functions by RF028 replay exporter.
// No database, HTTP, environment, credentials, random IDs or wall clock calls.
package executionaudit

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
	"zhiyu.local/zhiyu-go/internal/audit"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

// Frozen pure replay source: internal/app/native_bills_contract.go.
const mirrorMaxNativeBillBytesV3 = 65536

// Frozen pure replay source: internal/app/native_assets.go.
const mirrorNativeAssetProtocolV3 = "native-asset-action-v1"

// Frozen pure replay source: internal/app/native_automation_types.go.
const mirrorNativeAutomationProtocolV3 = "native-automation-v1"

// Frozen pure replay source: internal/app/native_bills_contract.go.
const mirrorNativeBillProtocolV3 = "zhiyu-external-bill-v1"

// Frozen pure replay source: internal/app/native_goal_transfer.go.
const mirrorNativeGoalTransferProtocolV3 = "native-paired-goal-transfer-v1"

// Frozen pure replay source: internal/app/joint_plan.go.
const mirrorNativeJointProtocolV3 = "native-joint-plan-go-v1"

// Frozen pure replay source: internal/app/native_payments_worker.go.
const mirrornativePaymentPermitProtocolV3 = "native-payment-submission-permit-v1"

// Frozen pure replay source: internal/app/native_automation_bridge.go.
const mirrornativeStandingBridgeAllocateV3 = "STANDING_BRIDGE_ALLOCATE"

// Frozen pure replay source: internal/app/native_automation_bridge.go.
const mirrornativeStandingBridgeProtocolV3 = "native-standing-bridge-v1"

// Frozen pure replay source: internal/app/native_automation_bridge.go.
const mirrornativeStandingBridgeTransferV3 = "STANDING_BRIDGE_TRANSFER"

// Frozen pure replay source: internal/app/native_payments_standing.go.
const mirrornativeStandingPaymentPermitProtocolV3 = mirrorNativeAutomationProtocolV3 + "/payment-submission-permit"

// Frozen pure replay source: internal/app/service.go.
type mirrorErrorV3 struct {
	Code  string
	Cause error
}

// Frozen pure replay source: internal/adapters/postgres/native_types.go.
type mirrorNativeLotReferenceV3 struct {
	AccountID  string `json:"account_id"`
	SourceKind string `json:"source_kind"`
	SourceID   string `json:"source_id"`
}

// Frozen pure replay source: internal/app/service.go.
type mirrorPrincipalV3 struct {
	EnvironmentID funds.ID
	UserID        funds.ID
	Kind          string
	SessionID     funds.ID
}

// Frozen pure replay source: internal/app/native_payments_adopted.go.
type mirrornativeAdoptedRuleV3 struct {
	Payee        string
	Amount       policy.TemplateAmountRule
	Prepare, Due int
	Cap          funds.Money
}

// Frozen pure replay source: internal/app/native_assets_resources.go.
type mirrornativeUnownedAvailabilityV3 struct {
	accounts map[funds.ID]bool
	free     map[funds.ID]funds.Money
	slack    map[funds.ID]funds.Money
	income   []funds.IncomeUse
	returns  []bankv2.ReturnUse
}

// Frozen pure replay source: internal/app/forecast.go.
type mirrortemplateInventoryV3 struct {
	Protocol              string                    `json:"protocol"`
	Scope                 policy.TemplateOwnerScope `json:"scope"`
	SeedProtectionVersion uint64                    `json:"seed_protection_version"`
	SeedProtectionHash    string                    `json:"seed_protection_hash"`
	ClosedWorld           string                    `json:"closed_world"`
}

// Frozen pure replay source: internal/app/forecast.go.
type mirrortemplateSnapshotV3 struct {
	Facts                            policy.TemplateFacts
	Versions                         []policy.TemplateVersion
	HistoricalVersions               []policy.TemplateVersion
	HistoricalEvents                 []planning.HistoricalPolicyEvent
	History                          planning.HistoricalObligationResult
	HistoricalBills                  []planning.HistoricalBillFact
	ExternalLiabilities              []planning.ForecastExternalLiability
	BillProofs                       map[string]wireNativeBillProof
	PaymentFinalTotals               map[string]funds.Money
	Views                            []wireTemplatePolicyView
	Retained                         []planning.ForecastObligation
	Held, UnassignedGoalCash         funds.Money
	Inventory                        mirrortemplateInventoryV3
	InventoryRaw                     []byte
	FundsVersion, SourceVersion      uint64
	WriterGeneration, ControlVersion uint64
	AsOf                             time.Time
	Timezone                         string
	Raw                              map[string][]string
	Native                           *nativeFrozenLedgerV3
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrorNativeAssetReviewHashV3(preview wireNativeAssetPreview) (string, error) {
	preview.ReviewedHash = ""
	raw, e := nativeMirrorEncodeV3(preview)
	return nakedHash(raw), e
}

// Frozen pure replay source: internal/app/native_goal_transfer.go.
func mirrorNativeGoalTransferReviewHashV3(preview wireNativeGoalTransferPreview) (string, error) {
	preview.ReviewedHash = ""
	raw, err := nativeMirrorEncodeV3(preview)
	return nakedHash(raw), err
}

// Frozen pure replay source: internal/app/native_payments_preview.go.
func mirrorNativePaymentReviewHashV3(preview wireNativePaymentPreview) (string, error) {
	preview.ReviewedHash = ""
	raw, e := nativeMirrorEncodeV3(preview)
	return nakedHash(raw), e
}

// Frozen pure replay source: internal/app/joint_plan.go.
func mirrorReplayNativeJointPlanV3(plan wireNativeJointPlan) (planning.JointPlan, error) {
	var zero planning.JointPlan
	inputRaw, err := nativeMirrorEncodeV3(plan.Input)
	if err != nil || plan.Protocol != mirrorNativeJointProtocolV3 || plan.BankAuthority || nakedHash(inputRaw) != plan.InputHash {
		return zero, nativeMirrorRejectV3("JOINT_PLAN_INVALID", errors.New("frozen input integrity"))
	}
	resultRaw, err := nativeMirrorEncodeV3(plan.Result)
	if err != nil || nakedHash(resultRaw) != plan.ResultHash || plan.Result.BankAuthority {
		return zero, nativeMirrorRejectV3("JOINT_PLAN_INVALID", errors.New("frozen result integrity"))
	}
	in, err := plan.Input.DomainInput()
	if err != nil || in.Scope != plan.Scope || !in.Now.Equal(plan.CreatedAt) || in.InventoryHash != plan.InventoryHash || in.Dependencies != plan.Dependencies || plan.Input.ProtectionForecast.Facts.SourceHash != plan.SourceHash {
		if err != nil {
			return zero, nativeMirrorRejectV3("JOINT_PLAN_INVALID", err)
		}
		return zero, nativeMirrorRejectV3("JOINT_PLAN_INVALID", errors.New("frozen scope and dependencies"))
	}
	if in.ProtectionInput == nil || len(in.Protection) != 0 || len(in.ProtectionInput.FutureIncome) != 0 || len(in.ProtectionInput.PrincipalReturns) != 0 || !in.ProtectionInput.AsOf.Equal(in.Now) || in.ProtectionInput.Scope != in.Scope || in.ProtectionInput.Timezone != in.Timezone || !plan.ExpiresAt.After(plan.CreatedAt) || plan.ExpiresAt.After(plan.CreatedAt.Add(2*time.Minute)) || len(plan.PolicyReferences) != len(in.Goals) {
		return zero, nativeMirrorRejectV3("JOINT_PLAN_INVALID", errors.New("frozen protection scope or references"))
	}
	proof, err := nativeMirrorEncodeV3(in.ProtectionInput)
	storedProof, secondErr := nativeMirrorEncodeV3(plan.Input.ProtectionForecast)
	if err != nil || secondErr != nil || !bytes.Equal(proof, storedProof) {
		return zero, nativeMirrorRejectV3("JOINT_PLAN_INVALID", errors.New("frozen protection proof"))
	}
	seen := map[funds.ID]bool{}
	for _, ref := range plan.PolicyReferences {
		if seen[ref.GoalID] || ref.GoalVersion == 0 {
			return zero, nativeMirrorRejectV3("JOINT_PLAN_INVALID", nil)
		}
		seen[ref.GoalID] = true
		found := false
		for _, goal := range in.Goals {
			if ref.GoalID == goal.Goal.ID && ref.PolicyID == goal.Version.PolicyID && ref.VersionID == goal.Version.VersionID && ref.Version == goal.Version.Version && ref.ConfigurationHash == goal.Version.Compiled.Hash {
				found = true
			}
		}
		if !found {
			return zero, nativeMirrorRejectV3("JOINT_PLAN_INVALID", nil)
		}
	}
	replayed, err := planning.SolveJoint(in)
	if err != nil {
		return zero, nativeMirrorRejectV3("JOINT_PLAN_INVALID", err)
	}
	replayRaw, err := nativeMirrorEncodeV3(replayed)
	if err != nil || !bytes.Equal(replayRaw, resultRaw) {
		return zero, nativeMirrorRejectV3("JOINT_PLAN_INVALID", errors.New("deterministic result replay"))
	}
	return replayed, nil
}

// Frozen pure replay source: internal/app/native_assets_accept.go.
func mirrorassetEquivalentEconomicV3(current, frozen wireNativeAssetPreview) error {
	if current.Policy.Version.VersionID != frozen.Policy.Version.VersionID || current.Policy.Version.Compiled.Hash != frozen.Policy.Version.Compiled.Hash || current.Kind != frozen.Kind || current.InventoryHash != frozen.InventoryHash {
		return nativeMirrorRejectV3("SOURCE_CHANGED", nil)
	}
	effect := current.BankEffect

	effect.LatestArrivalAt = frozen.BankEffect.LatestArrivalAt
	effect.PrincipalAvailableBy = frozen.BankEffect.PrincipalAvailableBy
	effect.PlannedRedeemAt = frozen.BankEffect.PlannedRedeemAt
	a, e := effect.Digest()
	b, be := frozen.BankEffect.Digest()
	if e != nil || be != nil || a != b {
		return nativeMirrorRejectV3("SOURCE_CHANGED", e)
	}
	x, _ := nativeMirrorEncodeV3(current.CashResources)
	y, _ := nativeMirrorEncodeV3(frozen.CashResources)
	u, _ := nativeMirrorEncodeV3(current.GoalUses)
	v, _ := nativeMirrorEncodeV3(frozen.GoalUses)
	if string(x) != string(y) || string(u) != string(v) {
		return nativeMirrorRejectV3("SOURCE_CHANGED", nil)
	}
	return nil
}

// Frozen pure replay source: internal/app/native_assets_accept.go.
func mirrorassetRequestAuthoritiesV3(preview wireNativeAssetPreview, approval funds.ID) ([]bankv2.Authority, error) {
	v := preview.Policy.Version
	allowCost := false
	config, e := v.Compiled.Decode()
	if e != nil {
		return nil, e
	}
	if asset, ok := config.(*policy.AssetAuthorizationConfig); ok {
		allowCost = asset.AllowEarlyWithdrawalWithPenalty
	}
	goal := preview.BankEffect.SourceGoalID
	a := []bankv2.Authority{{Role: "EXECUTE", SourceID: approval.String(), Version: 1, PolicyID: v.PolicyID.String(), PolicyVersion: int64(v.Version), GoalID: goal, AllowsFee: allowCost, AllowsLoss: allowCost}}
	if preview.Kind != bankv2.Purchase {
		if preview.Position == nil {
			return nil, mirrornativeResourceConflictV3()
		}
		original := preview.Position.SourceAuthority
		original.Role = "POSITION_ORIGINAL"
		a = append(a, original)
	}
	return a, nil
}

// Frozen pure replay source: internal/app/native_assets_worker.go.
func mirrorassetValidateWitnessV3(preview wireNativeAssetPreview, approval wirenativeAssetApproval, w wirenativeAssetWitness, at time.Time) error {
	d := preview.Dependencies
	if w.Protocol != "current-asset-protection-v1" || w.FundsVersion != w.ReservedFundsVersion || w.ReservedFundsVersion != d.FundsVersion+1 || w.SourceVersion != d.SourceEvidenceVersion || w.WriterGeneration != d.WriterGeneration || w.ControlVersion != d.ControlVersion || w.Current.Dependencies.FundsVersion != w.FundsVersion || w.Current.Dependencies.SourceEvidenceVersion != w.SourceVersion || w.Current.Dependencies.WriterGeneration != w.WriterGeneration || w.Current.Dependencies.ControlVersion != w.ControlVersion || w.Current.ID != preview.ID || w.Current.Scope != preview.Scope || w.Current.SessionID != approval.SessionID || !w.Current.CreatedAt.Equal(at) || !at.Before(preview.ExpiresAt) || at.Before(approval.ConfirmedAt) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	if e := mirrorreplayNativeAssetPreviewV3(w.Current); e != nil {
		return e
	}
	return mirrorassetEquivalentEconomicV3(w.Current, preview)
}

// Frozen pure replay source: internal/app/native_assets_capture.go.
func mirrorcaptureNativeAssetLedgerV3(n nativeFrozenLedgerV3) wireNativeAssetLedgerCapture {
	s := n.Income.Snapshot()
	facts := make([]funds.BankCreditFact, len(s.Origins))
	for i, o := range s.Origins {
		facts[i] = o.Fact()
	}
	return wireNativeAssetLedgerCapture{Namespace: n.Namespace, Income: wireNativeJointLedger{s.UserID, s.Currency, s.AsOf, s.ScopeAccountIDs, facts, s.Locations, s.Reservations}, Accounts: n.Accounts, Goals: n.Goals, GoalLots: n.GoalLots, Holds: n.Holds, Positions: n.Positions, PrincipalReturns: n.PrincipalReturns, ReturnLocations: n.ReturnLocations, ReturnClaims: n.ReturnClaims}
}

// Frozen pure replay source: internal/app/joint_plan.go.
func mirrorcaptureNativeJointInputV3(in planning.JointInput, forecast planning.ForecastInput) (wireNativeJointInput, error) {
	snapshot := in.Ledger.Snapshot()
	facts := make([]funds.BankCreditFact, len(snapshot.Origins))
	for i, origin := range snapshot.Origins {
		facts[i] = origin.Fact()
	}

	raw, err := nativeMirrorEncodeV3(in)
	if err != nil {
		return wireNativeJointInput{}, err
	}
	var core map[string]json.RawMessage
	if err = json.Unmarshal(raw, &core); err != nil {
		return wireNativeJointInput{}, err
	}
	delete(core, "Ledger")
	raw, err = nativeMirrorEncodeV3(core)
	return wireNativeJointInput{Ledger: wireNativeJointLedger{snapshot.UserID, snapshot.Currency, snapshot.AsOf, snapshot.ScopeAccountIDs, facts, snapshot.Locations, snapshot.Reservations}, Planner: raw, ProtectionForecast: forecast}, err
}

// Frozen pure replay source: internal/app/native_assets_resources.go.
func mirrorcloneNativeAssetLedgerV3(n nativeFrozenLedgerV3) nativeFrozenLedgerV3 {
	n.Accounts = mirrorcloneNativeAssetSliceV3(n.Accounts)
	n.Goals = mirrorcloneNativeAssetSliceV3(n.Goals)
	n.GoalLots = mirrorcloneNativeAssetSliceV3(n.GoalLots)
	n.Holds = mirrorcloneNativeAssetSliceV3(n.Holds)
	n.Positions = mirrorcloneNativeAssetSliceV3(n.Positions)
	n.PrincipalReturns = mirrorcloneNativeAssetSliceV3(n.PrincipalReturns)
	n.ReturnLocations = mirrorcloneNativeAssetSliceV3(n.ReturnLocations)
	n.ReturnClaims = mirrorcloneNativeAssetSliceV3(n.ReturnClaims)
	return n
}

// Frozen pure replay source: internal/app/native_assets_resources.go.
func mirrorcloneNativeAssetSliceV3[T any](values []T) []T {
	if values == nil {
		return nil
	}
	return append(make([]T, 0, len(values)), values...)
}

// Frozen pure replay source: internal/app/native_bills_contract.go.
func mirrordecodeNativeBillOriginalV3(raw []byte, scope policy.TemplateOwnerScope, namespace string, observedAt time.Time) (wireNativeBillOriginal, error) {
	var out wireNativeBillOriginal
	if len(raw) == 0 || len(raw) > mirrorMaxNativeBillBytesV3 {
		return out, nativeMirrorRejectV3("BILL_ORIGINAL_INVALID", nil)
	}
	if _, e := audit.ParseRawObject(raw); e != nil {
		return out, nativeMirrorRejectV3("BILL_ORIGINAL_INVALID", e)
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&out) != nil || decoder.Decode(new(any)) != io.EOF {
		return wireNativeBillOriginal{}, nativeMirrorRejectV3("BILL_ORIGINAL_INVALID", nil)
	}
	if out.Protocol != mirrorNativeBillProtocolV3 || out.ID == (funds.ID{}) || out.Scope != scope || out.BankNamespace != namespace || !nativeBillNamespace.MatchString(namespace) || out.StatementAccountID == (funds.ID{}) || out.IssuedAt.IsZero() || out.IssuedAt.After(observedAt) || out.FinalTotal.IsZero() || !mirrorhistoryKeyV3(out.PayeeID) || !mirrorhistoryKeyV3(out.ExternalKey) {
		return wireNativeBillOriginal{}, nativeMirrorRejectV3("BILL_ORIGINAL_INVALID", nil)
	}
	if _, e := policy.TemplateDate(out.DueDate); e != nil {
		return wireNativeBillOriginal{}, nativeMirrorRejectV3("BILL_ORIGINAL_INVALID", e)
	}
	return out, nil
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrorderiveNativeAssetPreviewV3(snapshot mirrortemplateSnapshotV3, in wireNativeAssetRequest, id, session funds.ID, state bankv2.CurrentState, quote *bankv2.Quote, pending []bankv2.Request, expiryCap ...time.Time) (wireNativeAssetPreview, error) {
	return mirrorderiveNativeAssetPreviewWithSourceV3(snapshot, in, id, session, state, quote, pending, nil, expiryCap...)
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrorderiveNativeAssetPreviewWithSourceV3(snapshot mirrortemplateSnapshotV3, in wireNativeAssetRequest, id, session funds.ID, state bankv2.CurrentState, quote *bankv2.Quote, pending []bankv2.Request, source *wirenativeStandingAssetSource, expiryCap ...time.Time) (wireNativeAssetPreview, error) {
	var out wireNativeAssetPreview
	if snapshot.Native == nil || !mirrornativeAssetKindV3(in.Kind) || in.PolicyVersionID == (funds.ID{}) {
		return out, nativeMirrorRejectV3("INVALID_NATIVE_ASSET", nil)
	}
	if e := mirrornativeAssetStateV3(*snapshot.Native, state, snapshot.Facts.Scope); e != nil {
		return out, e
	}
	if source != nil {
		if in.Kind != bankv2.Purchase || session != (funds.ID{}) {
			return out, nativeMirrorRejectV3("INVALID_NATIVE_ASSET", nil)
		}
		if e := source.validate(*snapshot.Native, snapshot.AsOf); e != nil {
			return out, e
		}
	}
	var view wireTemplatePolicyView
	for _, v := range snapshot.Views {
		if v.Version.VersionID == in.PolicyVersionID {
			view = v
		}
	}
	if active, e := view.Version.EffectiveState(snapshot.AsOf, snapshot.Timezone); e != nil || active != "ACTIVE" {
		return out, nativeMirrorRejectV3("ASSET_POLICY_NOT_ACTIVE", e)
	}
	decoded, e := view.Version.Compiled.Decode()
	if e != nil {
		return out, nativeMirrorRejectV3("ASSET_POLICY_REQUIRED", e)
	}
	forecastInput := snapshot.forecastInput(365)
	before, e := planning.Forecast365(forecastInput)
	if e != nil {
		return out, nativeMirrorRejectV3("ASSET_PROTECTION_INCOMPLETE", e)
	}
	effect := bankv2.Effect{Kind: in.Kind, BusinessKey: "asset:" + id.String(), CashUses: []bankv2.CashUse{}, OriginUses: []bankv2.OriginUse{}, ReturnUses: []bankv2.ReturnUse{}}
	var cash wireNativeCashResources
	goalUses := []wireNativeGoalTransferUse{}
	var original *bankv2.PositionOpening
	trigger := ""
	var purchaseUseLimit time.Time
	if in.Kind == bankv2.Purchase {
		config, ok := decoded.(*policy.AssetAuthorizationConfig)
		if !ok || in.ProductID == "" || in.PositionID != "" || in.Amount.IsZero() || quote != nil {
			return out, nativeMirrorRejectV3("ASSET_POLICY_REQUIRED", nil)
		}
		var product *bankv2.ProductSnapshot
		for _, p := range state.Products {
			if p.Terms.ProductID == in.ProductID {
				copy := p
				product = &copy
			}
		}
		if product == nil || product.Availability.Status != "AVAILABLE" || snapshot.AsOf.Before(product.Availability.EffectiveFrom) || !snapshot.AsOf.Before(product.Availability.EffectiveUntil) {
			return out, nativeMirrorRejectV3("ASSET_PRODUCT_UNAVAILABLE", nil)
		}
		facts := snapshot.Facts
		facts.Goals = append([]policy.TemplateGoalFact(nil), facts.Goals...)
		facts.CurrentSafe, facts.CurrentSafeVerified = before.CurrentSafe, true
		facts.Products = []policy.TemplateAssetProductFact{}
		facts.ManagedAmount, e = mirrornativeAssetManagedV3(*snapshot.Native, pending, config.GoalID)
		if e != nil {
			return out, e
		}
		zone, _ := policy.TemplateLocation(snapshot.Timezone)
		useDate := snapshot.AsOf.In(zone).AddDate(0, 0, 365).Format("2006-01-02")
		if config.GoalID != nil {
			found := false
			for i := range facts.Goals {
				g := &facts.Goals[i]
				if g.ID != *config.GoalID {
					continue
				}
				found = true
				g.ScopeAvailable = g.CashOwned
				for _, h := range snapshot.Native.Holds {
					if h.ResourceKind == "GOAL" && h.ResourceID == g.ID.String() && h.State == funds.Reserved {
						g.ScopeAvailable, e = g.ScopeAvailable.Sub(h.Amount)
						if e != nil {
							return out, mirrornativeResourceConflictV3()
						}
					}
				}
				if g.GoalVersion == nil {
					return out, nativeMirrorRejectV3("ASSET_GOAL_POLICY_REQUIRED", nil)
				}
				c, ce := g.GoalVersion.Compiled.Decode()
				if ce != nil {
					return out, ce
				}
				long, ok := c.(*policy.LongTermGoalConfig)
				if !ok {
					return out, nativeMirrorRejectV3("ASSET_GOAL_POLICY_REQUIRED", nil)
				}
				if long.Deadline < useDate {
					useDate = long.Deadline
				}
			}
			if !found {
				return out, nativeMirrorRejectV3("ASSET_GOAL_POLICY_REQUIRED", nil)
			}
		}
		facts.AssetUseDate = useDate
		useDay, _ := policy.TemplateDate(useDate)
		purchaseUseLimit = time.Date(useDay.Year(), useDay.Month(), useDay.Day(), 0, 0, 0, 0, zone)
		for _, p := range state.Products {
			kind, accrual := "PLANNED_REDEMPTION", "UNTIL_REDEMPTION_REQUEST"
			if p.Terms.AssetClass == "FIXED_DEPOSIT" {
				kind, accrual = "FIXED_MATURITY", "UNTIL_MATURITY"
			}
			facts.Products = append(facts.Products, policy.TemplateAssetProductFact{ID: mirrornativeAssetObjectIDV3(state.Namespace, "product", p.Terms.ProductID), AssetClass: mirrornativeAssetClassV3(p.Terms), Minimum: p.Terms.MinimumPurchase, LockDays: p.Terms.LockDays, RedemptionDelayDays: p.Terms.RedemptionDelayDays, AutoPurchaseAllowed: p.Availability.Status == "AVAILABLE", AutoRedeemAllowed: p.Terms.RedemptionFeeBPS == 0 && p.Terms.EarlyLossBPS == 0, Current: !snapshot.AsOf.Before(p.Availability.EffectiveFrom) && snapshot.AsOf.Before(p.Availability.EffectiveUntil), TermsVerified: true, TermsHash: p.TermsHash, ReturnKind: kind, TermDays: p.Terms.TermDays, TermsSettlementDelayDays: p.Terms.RedemptionDelayDays, Accrual: accrual})
		}
		pure, pe := policy.PreviewTemplate(view.Version, facts, snapshot.AsOf, snapshot.Timezone)
		if pe != nil {
			return out, nativeMirrorRejectV3("ASSET_POLICY_REJECTED", pe)
		}
		var budget funds.Money
		for _, candidate := range pure.Candidates {
			if candidate.SubjectID == mirrornativeAssetObjectIDV3(state.Namespace, "product", in.ProductID) && candidate.Kind == "ASSET_PURCHASE_CONSTRAINT" {
				budget = candidate.AmountCap
			}
		}
		budget = mirrornativeResourceMinimumV3(budget, in.Amount)
		if source != nil {
			var available funds.Money
			if config.GoalID == nil {
				selected, e := mirrorselectStandingAssetCashV3(*snapshot.Native, *source, budget)
				if e != nil {
					return out, e
				}
				available = selected.Amount
			} else {
				available, e = mirrorstandingAssetGoalAmountV3(*snapshot.Native, *source, *config.GoalID)
				if e != nil {
					return out, e
				}
			}
			budget = mirrornativeResourceMinimumV3(budget, available)
		}
		if config.GoalID == nil {
			for _, point := range before.Points {
				margin, me := strconv.ParseInt(point.MarginCents, 10, 64)
				if me != nil || margin < 0 {
					return out, nativeMirrorRejectV3("ASSET_NO_SAFE_PURCHASE", me)
				}
				bound, _ := funds.NewMoney(margin)
				budget = mirrornativeResourceMinimumV3(budget, bound)
			}
		}
		if budget.IsZero() {
			return out, nativeMirrorRejectV3("ASSET_NO_SAFE_PURCHASE", nil)
		}
		if config.GoalID == nil {
			if source == nil {
				cash, e = mirrorselectNativeUnownedCashV3(*snapshot.Native, nil, budget)
			} else {
				cash, e = mirrorselectStandingAssetCashV3(*snapshot.Native, *source, budget)
			}
			if e != nil {
				return out, e
			}
			effect.Amount = cash.Amount
			effect.CashUses = cash.CashUses
			effect.OriginUses = mirrornativeCashOriginUsesV3(cash)
			effect.ReturnUses = append([]bankv2.ReturnUse{}, cash.ReturnUses...)
		} else {
			effect.Amount = budget
			effect.SourceGoalID = config.GoalID.String()
			for _, g := range facts.Goals {
				if g.ID == *config.GoalID {
					effect.CashUses = []bankv2.CashUse{{AccountID: g.AccountID.String(), Amount: budget}}
					if source == nil {
						goalUses, e = mirrorgoalTransferLotUsesV3(*snapshot.Native, g.ID, g.AccountID, budget)
					} else {
						goalUses, e = mirrorgoalTransferLotUsesMatchingV3(*snapshot.Native, g.ID, g.AccountID, budget, source.lot)
					}
					if e != nil {
						return out, e
					}
					for _, u := range goalUses {
						if u.SourceKind == "INCOME" {
							effect.OriginUses = append(effect.OriginUses, bankv2.OriginUse{OriginID: u.SourceID, AccountID: g.AccountID.String(), Amount: u.Amount, Bucket: "ASSIGNED_GOAL"})
						} else {
							effect.ReturnUses = append(effect.ReturnUses, bankv2.ReturnUse{OperationKey: u.SourceID, PositionID: u.PositionID, AccountID: g.AccountID.String(), Amount: u.Amount})
						}
					}
				}
			}
		}
		if effect.Amount.Cents() < product.Terms.MinimumPurchase.Cents() || len(effect.CashUses) == 0 {
			return out, nativeMirrorRejectV3("ASSET_BELOW_MINIMUM", nil)
		}
		effect.Product = product
		effect.PositionID = "position:" + id.String()
		effect.ReturnAccountID = effect.CashUses[0].AccountID
		if product.Terms.AssetClass == "FIXED_DEPOSIT" {
			effect.PrincipalAvailableBy = snapshot.AsOf.UTC().AddDate(0, 0, product.Terms.TermDays+product.Terms.RedemptionDelayDays)
		} else {
			date, _ := policy.TemplateDate(useDate)
			limit := time.Date(date.Year(), date.Month(), date.Day(), 0, 0, 0, 0, zone)
			effect.PrincipalAvailableBy = limit
			effect.PlannedRedeemAt = limit.AddDate(0, 0, -product.Terms.RedemptionDelayDays)
		}
	} else {
		if in.ProductID != "" || in.PositionID == "" || quote == nil || quote.Validate() != nil || quote.Namespace != state.Namespace || quote.UserID != state.UserID || quote.Kind != in.Kind || quote.PositionID != in.PositionID || snapshot.AsOf.Before(quote.RequestedAt) || !snapshot.AsOf.Before(quote.ExpiresAt) {
			return out, nativeMirrorRejectV3("ASSET_QUOTE_INVALID", nil)
		}
		var local *wireNativePosition
		for _, p := range snapshot.Native.Positions {
			if p.ID == in.PositionID {
				copy := p
				local = &copy
			}
		}
		if local == nil || local.Closed || local.ClosingOperationID != nil || local.OriginalHash != quote.PositionOpeningHash || nativeCompactOriginalV3(local.Original, &original) != nil || original == nil {
			return out, nativeMirrorRejectV3("ASSET_POSITION_UNAVAILABLE", nil)
		}
		h, he := original.Digest()
		if he != nil || h != quote.PositionOpeningHash || original.Principal != quote.Principal || original.TermsHash != quote.Product.TermsHash || original.ReturnAccountID != local.ReturnAccountID.String() {
			return out, nativeMirrorRejectV3("ASSET_QUOTE_INVALID", he)
		}
		net, fee, loss, available, qe := bankv2.RedemptionQuote(original.Principal, original.Terms, original.PurchasedAt, quote.RequestedAt, in.Kind == bankv2.Mature)
		if qe != nil || net != quote.Net || fee != quote.Fee || loss != quote.Loss || !available.Equal(quote.AvailableAt) {
			return out, nativeMirrorRejectV3("ASSET_QUOTE_INVALID", qe)
		}
		if !in.Amount.IsZero() && in.Amount.Cents() < original.Principal.Cents() {
			return out, nativeMirrorRejectV3("ASSET_FULL_POSITION_REQUIRED", nil)
		}
		switch config := decoded.(type) {
		case *policy.AssetAuthorizationConfig:
			if view.Version.PolicyID.String() != original.SourceAuthority.PolicyID || !mirrornativeAssetSameGoalV3(config.GoalID, original.GoalID) || config.SingleActionCap.Cents() < original.Principal.Cents() || config.MaxRedemptionDelayDays < original.Terms.RedemptionDelayDays {
				return out, nativeMirrorRejectV3("ASSET_CLOSE_NOT_AUTHORIZED", nil)
			}
			allowed := false
			for _, class := range config.AllowedAssetClasses {
				if class == mirrornativeAssetClassV3(original.Terms) {
					allowed = true
				}
			}
			if !allowed || (!fee.IsZero() || !loss.IsZero()) && !config.AllowEarlyWithdrawalWithPenalty {
				return out, nativeMirrorRejectV3("ASSET_COST_NOT_AUTHORIZED", nil)
			}
		case *policy.RecoveryConfig:
			if config.AssetPolicyID.String() != original.SourceAuthority.PolicyID || !mirrornativeAssetSameGoalV3(config.GoalID, original.GoalID) || config.SingleActionCap.Cents() < original.Principal.Cents() || config.MaxRedemptionDelayDays < original.Terms.RedemptionDelayDays || !fee.IsZero() || !loss.IsZero() {
				return out, nativeMirrorRejectV3("ASSET_CLOSE_NOT_AUTHORIZED", nil)
			}
			if !before.MaximumDeficit.IsZero() {
				trigger = "LIQUIDITY_SHORTFALL"
			}
			for _, v := range snapshot.Views {
				if v.Version.PolicyID == config.AssetPolicyID {
					if v.EffectiveState == "REVOKED" {
						trigger = "AUTHORIZATION_REVOKED"
					}
					if v.EffectiveState == "EXPIRED" {
						trigger = "POLICY_EXPIRED"
					}
				}
			}
			allowed := false
			for _, value := range config.Triggers {
				if value == trigger {
					allowed = true
				}
			}
			if !allowed {
				return out, nativeMirrorRejectV3("ASSET_RECOVERY_NOT_TRIGGERED", nil)
			}
		default:
			return out, nativeMirrorRejectV3("ASSET_POLICY_REQUIRED", nil)
		}
		effect.Amount, effect.Product, effect.PositionID, effect.PositionOpeningHash = original.Principal, &quote.Product, original.PositionID, local.OriginalHash
		effect.ReturnAccountID, effect.DestinationAccountID = original.ReturnAccountID, original.ReturnAccountID
		effect.SourceGoalID = original.GoalID
		effect.Net, effect.Fee, effect.Loss, effect.QuoteID = quote.Net, quote.Fee, quote.Loss, quote.ID
	}
	expires, e := mirrornativeAssetExpiryV3(snapshot.AsOf, snapshot.Timezone, view.Version, quote, func() *bankv2.ProductSnapshot {
		if in.Kind == bankv2.Purchase {
			return effect.Product
		}
		return nil
	}())
	if e != nil {
		return out, e
	}
	if len(expiryCap) > 1 {
		return out, nativeMirrorRejectV3("INVALID_NATIVE_ASSET", nil)
	}
	if len(expiryCap) == 1 && expiryCap[0].Before(expires) {
		expires = expiryCap[0]
	}
	if !expires.After(snapshot.AsOf) {
		return out, nativeMirrorRejectV3("AUTHORIZATION_EXPIRED", nil)
	}
	effect.LatestArrivalAt = expires
	if in.Kind == bankv2.Purchase && effect.Product.Terms.AssetClass == "FIXED_DEPOSIT" {

		effect.PrincipalAvailableBy = expires.UTC().AddDate(0, 0, effect.Product.Terms.TermDays+effect.Product.Terms.RedemptionDelayDays)
	}
	if in.Kind == bankv2.Purchase {
		if effect.PrincipalAvailableBy.After(purchaseUseLimit) || effect.Product.Terms.AssetClass != "FIXED_DEPOSIT" && effect.PlannedRedeemAt.Before(expires.UTC().AddDate(0, 0, effect.Product.Terms.LockDays)) {
			return out, nativeMirrorRejectV3("ASSET_EXIT_AFTER_USE", nil)
		}
	}
	if quote != nil {
		effect.LatestArrivalAt = expires.UTC().AddDate(0, 0, quote.Product.Terms.RedemptionDelayDays)
		if quote.AvailableAt.After(effect.LatestArrivalAt) {
			effect.LatestArrivalAt = quote.AvailableAt
		}
	}
	effectHash, e := effect.Digest()
	if e != nil {
		return out, nativeMirrorRejectV3("INVALID_NATIVE_ASSET", e)
	}
	afterInput, e := mirrornativeAssetCounterfactualV3(forecastInput, effect)
	if e != nil {
		return out, e
	}
	after, e := planning.Forecast365(afterInput)
	if e != nil {
		return out, nativeMirrorRejectV3("ASSET_PROTECTION_INCOMPLETE", e)
	}
	if in.Kind == bankv2.Purchase && !after.MaximumDeficit.IsZero() || in.Kind != bankv2.Purchase && after.MaximumDeficit.Cents() > before.MaximumDeficit.Cents() {
		return out, nativeMirrorRejectV3("ASSET_PROTECTION_DEFICIT", nil)
	}
	for _, point := range after.Points {
		if _, e = strconv.ParseInt(point.MarginCents, 10, 64); e != nil {
			return out, e
		}
	}
	deps := planning.DependencyVector{FundsVersion: snapshot.FundsVersion, PolicyVersion: view.Version.Version, ProductTermsVersion: uint64(effect.Product.Terms.Version), ProductAvailabilityVersion: uint64(effect.Product.Availability.Version), SourceEvidenceVersion: snapshot.SourceVersion, WriterGeneration: snapshot.WriterGeneration, ControlVersion: snapshot.ControlVersion}
	in.Timezone = snapshot.Timezone
	out = wireNativeAssetPreview{wireNativeActionPreviewHeader: wireNativeActionPreviewHeader{ID: id, Scope: snapshot.Facts.Scope, SessionID: session, Kind: in.Kind, EffectHash: effectHash, Dependencies: deps, SourceHash: snapshot.Facts.SourceHash, InventoryHash: nakedHash(snapshot.InventoryRaw), CreatedAt: snapshot.AsOf, ExpiresAt: expires}, Protocol: mirrorNativeAssetProtocolV3, Timezone: snapshot.Timezone, Request: in, Policy: view, Policies: snapshot.Views, Ledger: mirrorcaptureNativeAssetLedgerV3(*snapshot.Native), InventoryOriginal: append([]byte(nil), snapshot.InventoryRaw...), BankState: state, Quote: quote, Position: original, CashResources: cash, GoalUses: goalUses, PendingPurchases: pending, BankEffect: effect, ForecastInput: forecastInput, ForecastBefore: before, ForecastAfter: after, RecoveryTrigger: trigger}
	out.ReviewedHash, e = mirrorNativeAssetReviewHashV3(out)
	return out, e
}

// Frozen pure replay source: internal/app/native_goal_transfer.go.
func mirrorgoalTransferCounterfactualV3(before planning.ForecastInput, source, target funds.ID, amount funds.Money) (planning.ForecastInput, error) {
	var after planning.ForecastInput
	raw, err := nativeMirrorEncodeV3(before)
	if err != nil || nativeCompactOriginalV3(raw, &after) != nil {
		return after, nativeMirrorRejectV3("EVIDENCE_INVALID", err)
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
			return after, nativeMirrorRejectV3("GOAL_CASH_UNAVAILABLE", err)
		}
	}
	if !sourceFound || !targetFound || sourceAccount == targetAccount {
		return after, nativeMirrorRejectV3("GOAL_TRANSFER_INVALID", nil)
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
			return after, nativeMirrorRejectV3("GOAL_TRANSFER_CAPACITY", err)
		}
	}
	if !sourceFound || !targetFound {
		return after, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}

	return after, nil
}

// Frozen pure replay source: internal/app/native_goal_transfer_resources.go.
func mirrorgoalTransferEffectV3(id, source, target, sourceAccount, targetAccount funds.ID, amount funds.Money, uses []wireNativeGoalTransferUse, expires time.Time) (bankv2.Effect, error) {
	e := bankv2.Effect{Kind: bankv2.MoveGoal, BusinessKey: "paired-goal:" + id.String(), Amount: amount, CashUses: []bankv2.CashUse{{AccountID: sourceAccount.String(), Amount: amount}}, OriginUses: []bankv2.OriginUse{}, ReturnUses: []bankv2.ReturnUse{}, SourceGoalID: source.String(), DestinationGoalID: target.String(), DestinationAccountID: targetAccount.String(), LatestArrivalAt: expires}
	if source == target || sourceAccount == targetAccount || id == (funds.ID{}) || len(uses) == 0 || len(uses) > bankv2.MaxUses {
		return e, nativeMirrorRejectV3("GOAL_TRANSFER_INVALID", nil)
	}
	for _, use := range uses {
		switch use.SourceKind {
		case "INCOME":
			if use.PositionID != "" {
				return e, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
			}
			if _, err := funds.ParseID(use.SourceID); err != nil {
				return e, nativeMirrorRejectV3("EVIDENCE_INVALID", err)
			}
			e.OriginUses = append(e.OriginUses, bankv2.OriginUse{OriginID: use.SourceID, AccountID: sourceAccount.String(), Amount: use.Amount, Bucket: "ASSIGNED_GOAL"})
		case "PRINCIPAL_RETURN":
			if use.PositionID == "" {
				return e, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
			}
			e.ReturnUses = append(e.ReturnUses, bankv2.ReturnUse{OperationKey: use.SourceID, PositionID: use.PositionID, AccountID: sourceAccount.String(), Amount: use.Amount})
		default:
			return e, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
		}
	}
	if err := e.Validate(); err != nil {
		return e, nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	return e, nil
}

// Frozen pure replay source: internal/app/native_goal_transfer_resources.go.
func mirrorgoalTransferExpectedHoldsV3(operation funds.ID, effect bankv2.Effect, uses []wireNativeGoalTransferUse) ([]wireNativeHold, error) {
	if effect.Kind != bankv2.MoveGoal || !strings.HasPrefix(effect.BusinessKey, "paired-goal:") || effect.Validate() != nil {
		return nil, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	preview, e := funds.ParseID(strings.TrimPrefix(effect.BusinessKey, "paired-goal:"))
	if e != nil {
		return nil, nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	sourceAccount, _ := funds.ParseID(effect.CashUses[0].AccountID)
	targetAccount, _ := funds.ParseID(effect.DestinationAccountID)
	sourceGoal, _ := funds.ParseID(effect.SourceGoalID)
	targetGoal, _ := funds.ParseID(effect.DestinationGoalID)
	expected, e := mirrorgoalTransferEffectV3(preview, sourceGoal, targetGoal, sourceAccount, targetAccount, effect.Amount, uses, effect.LatestArrivalAt)
	if e != nil {
		return nil, e
	}
	a, _ := expected.Digest()
	b, _ := effect.Digest()
	if a != b {
		return nil, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	source, e := funds.ParseID(effect.SourceGoalID)
	if e != nil {
		return nil, e
	}
	account, e := funds.ParseID(effect.CashUses[0].AccountID)
	if e != nil {
		return nil, e
	}
	holds := []wireNativeHold{{OperationID: operation, ResourceKind: "GOAL", ResourceID: source.String(), Amount: effect.Amount, State: funds.Reserved}, {OperationID: operation, ResourceKind: "INCOMING_GOAL", ResourceID: effect.DestinationGoalID, Amount: effect.Amount, State: funds.Reserved}}
	for _, use := range uses {
		ref, e := nativeMirrorEncodeV3(mirrorNativeLotReferenceV3{AccountID: account.String(), SourceKind: use.SourceKind, SourceID: use.SourceID})
		if e != nil {
			return nil, e
		}
		holds = append(holds, wireNativeHold{OperationID: operation, ResourceKind: "GOAL_LOT", ResourceID: source.String(), ResourceDetail: string(ref), Amount: use.Amount, State: funds.Reserved})
		if use.SourceKind == "INCOME" {
			origin, e := funds.ParseID(use.SourceID)
			if e != nil {
				return nil, e
			}
			holds = append(holds, wireNativeHold{OperationID: operation, ResourceKind: "ASSIGNED", ResourceID: funds.LocationID(origin, account).String(), Amount: use.Amount, State: funds.Reserved})
		}
	}
	return holds, nil
}

// Frozen pure replay source: internal/app/native_goal_transfer_resources.go.
func mirrorgoalTransferLotUsesV3(ledger nativeFrozenLedgerV3, source, account funds.ID, amount funds.Money) ([]wireNativeGoalTransferUse, error) {
	return mirrorgoalTransferLotUsesMatchingV3(ledger, source, account, amount, nil)
}

// Frozen pure replay source: internal/app/native_goal_transfer_resources.go.
func mirrorgoalTransferLotUsesMatchingV3(ledger nativeFrozenLedgerV3, source, account funds.ID, amount funds.Money, eligible func(wireNativeGoalLot) bool) ([]wireNativeGoalTransferUse, error) {
	if amount.IsZero() {
		return nil, nativeMirrorRejectV3("GOAL_TRANSFER_INVALID", nil)
	}
	lots := append([]wireNativeGoalLot(nil), ledger.GoalLots...)
	sort.Slice(lots, func(i, j int) bool {
		if lots[i].SourceKind != lots[j].SourceKind {
			return lots[i].SourceKind < lots[j].SourceKind
		}
		return lots[i].SourceID < lots[j].SourceID
	})
	remaining := amount
	uses := []wireNativeGoalTransferUse{}
	for _, lot := range lots {
		if lot.GoalID != source || lot.AccountID != account || remaining.IsZero() || eligible != nil && !eligible(lot) {
			continue
		}
		free, err := lot.Available.Sub(lot.Reserved)
		if err != nil {
			return nil, nativeMirrorRejectV3("EVIDENCE_INVALID", err)
		}
		use := wireNativeGoalTransferUse{SourceKind: lot.SourceKind, SourceID: lot.SourceID}
		switch lot.SourceKind {
		case "INCOME":
			origin, err := funds.ParseID(lot.SourceID)
			if err != nil {
				return nil, nativeMirrorRejectV3("EVIDENCE_INVALID", err)
			}
			fragment := funds.LocationID(origin, account)
			var assigned, held funds.Money
			found := false
			for _, location := range ledger.Income.Snapshot().Locations {
				if location.FragmentID == fragment && location.OriginID == origin && location.AccountID == account {
					assigned, found = location.Assigned, true
				}
			}
			for _, hold := range ledger.Holds {
				if hold.State == funds.Reserved && hold.ResourceKind == "ASSIGNED" && hold.ResourceID == fragment.String() {
					held, err = held.Add(hold.Amount)
					if err != nil {
						return nil, err
					}
				}
			}
			available, err := assigned.Sub(held)
			if !found || err != nil {
				return nil, nativeMirrorRejectV3("EVIDENCE_INVALID", err)
			}
			if available.Cents() < free.Cents() {
				free = available
			}
		case "PRINCIPAL_RETURN":
			found := false
			for _, original := range ledger.PrincipalReturns {
				if original.BankKey == lot.SourceID {
					use.PositionID, found = original.PositionID, true
				}
			}
			locationFound := false
			for _, location := range ledger.ReturnLocations {
				if location.BankKey == lot.SourceID && location.AccountID == account {
					available, err := location.Available.Sub(location.Reserved)
					if err != nil {
						return nil, nativeMirrorRejectV3("EVIDENCE_INVALID", err)
					}
					if available.Cents() < free.Cents() {
						free = available
					}
					locationFound = true
				}
			}
			if !found || !locationFound {
				return nil, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
			}
		default:
			return nil, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
		}
		if free.IsZero() {
			continue
		}
		if remaining.Cents() < free.Cents() {
			free = remaining
		}
		use.Amount = free
		uses = append(uses, use)
		remaining, err = remaining.Sub(free)
		if err != nil {
			return nil, err
		}
	}
	if !remaining.IsZero() || len(uses) > bankv2.MaxUses {
		return nil, nativeMirrorRejectV3("GOAL_CASH_UNAVAILABLE", nil)
	}
	return uses, nil
}

// Frozen pure replay source: internal/app/native_goal_transfer.go.
func mirrorgoalTransferProtectedReferencesV3(before, after planning.ForecastInput, ids []funds.ID) ([]policy.TemplatePolicyReference, error) {
	refs := []policy.TemplatePolicyReference{}
	for _, id := range ids {
		var version policy.TemplateVersion
		found := false
		for _, v := range before.Versions {
			if v.PolicyID == id {
				if found {
					return nil, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
				}
				version, found = v, true
			}
		}
		if !found {
			return nil, nativeMirrorRejectV3("GOAL_TRANSFER_PROTECTED_REFERENCE_MISSING", nil)
		}
		isolate := func(in planning.ForecastInput) (planning.ForecastResult, error) {
			var input planning.ForecastInput
			raw, err := nativeMirrorEncodeV3(in)
			if err != nil || nativeCompactOriginalV3(raw, &input) != nil {
				return planning.ForecastResult{}, nativeMirrorRejectV3("EVIDENCE_INVALID", err)
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
			return nil, nativeMirrorRejectV3("GOAL_TRANSFER_PROTECTION_INCOMPLETE", err)
		}
		right, err := isolate(after)
		if err != nil || len(left.Points) != len(right.Points) {
			return nil, nativeMirrorRejectV3("GOAL_TRANSFER_PROTECTION_INCOMPLETE", err)
		}
		var leftMax, rightMax funds.Money
		for i, point := range left.Points {
			changed := right.Points[i]
			if point.ID != changed.ID || point.At != changed.At || changed.Protected.Cents() < point.Protected.Cents() {
				return nil, nativeMirrorRejectV3("GOAL_TRANSFER_PROTECTED_REFERENCE_REDUCED", nil)
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

// Frozen pure replay source: internal/app/native_goal_transfer_accept.go.
func mirrorgoalTransferUserConsentsV3(preview wireNativeGoalTransferPreview, release, receive funds.ID, now time.Time) (planning.PairedGoalConsent, planning.PairedGoalConsent) {
	base := planning.PairedGoalConsent{ID: release, Scope: preview.Scope, Role: "RELEASE_SOURCE", PrincipalKind: "USER", PolicyID: preview.PairedEffect.PolicyID, PolicyVersionID: preview.PairedEffect.PolicyVersionID, SourceGoalID: preview.PairedEffect.SourceGoalID, TargetGoalID: preview.PairedEffect.TargetGoalID, Amount: preview.PairedEffect.Amount, ReviewedEffectHash: preview.PairedEffect.EffectHash, ConfirmedAt: now, ValidUntil: preview.ExpiresAt, Current: true, Verified: true}
	other := base
	other.ID, other.Role = receive, "RECEIVE_TARGET"
	return base, other
}

// Frozen pure replay source: internal/app/native_goal_transfer_worker.go.
func mirrorgoalTransferValidateWitnessV3(preview wireNativeGoalTransferPreview, approval wirenativeGoalTransferApproval, witness wirenativeGoalTransferWitness, at time.Time) error {
	d := preview.Dependencies
	if witness.Protocol != "current-paired-goal-protection-v1" || witness.FundsVersion != witness.ReservedFundsVersion || witness.ReservedFundsVersion != d.FundsVersion+1 || witness.SourceVersion != d.SourceEvidenceVersion || witness.WriterGeneration != d.WriterGeneration || witness.ControlVersion != d.ControlVersion || witness.InventoryHash != preview.InventoryHash || witness.SourceHash != witness.ForecastInput.Facts.SourceHash || witness.ForecastInput.Scope != preview.Scope || !witness.PairedInput.Now.Equal(at) || !witness.ForecastInput.AsOf.Equal(at) || witness.PairedInput.DependencyHash != preview.PairedInput.DependencyHash || !at.Before(preview.ExpiresAt) || at.Before(approval.ConfirmedAt) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	paired, err := planning.CheckPairedGoalTransfer(witness.PairedInput, approval.Release, approval.Receive)
	a, _ := nativeMirrorEncodeV3(paired)
	b, _ := nativeMirrorEncodeV3(preview.PairedEffect)
	if err != nil || string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	before, err := planning.Forecast365(witness.ForecastInput)
	if err != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	afterInput, err := mirrorgoalTransferCounterfactualV3(witness.ForecastInput, paired.SourceGoalID, paired.TargetGoalID, paired.Amount)
	if err != nil {
		return err
	}
	after, err := planning.Forecast365(afterInput)
	if err != nil || !after.MaximumDeficit.IsZero() {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	decoded, err := witness.PairedInput.Policy.Compiled.Decode()
	if err != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	config, ok := decoded.(*policy.CrossGoalReallocationConfig)
	if !ok {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	refs, err := mirrorgoalTransferProtectedReferencesV3(witness.ForecastInput, afterInput, config.MustNotReducePolicyIDs)
	if err != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	a, _ = nativeMirrorEncodeV3(refs)
	b, _ = nativeMirrorEncodeV3(witness.PairedInput.ProtectedReferences)
	if string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	a, _ = nativeMirrorEncodeV3(before)
	b, _ = nativeMirrorEncodeV3(witness.ForecastBefore)
	if string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	a, _ = nativeMirrorEncodeV3(after)
	b, _ = nativeMirrorEncodeV3(witness.ForecastAfter)
	if string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	return nil
}

// Frozen pure replay source: internal/app/history_archive.go.
func mirrorhistoryKeyV3(key string) bool {
	return len(key) > 0 && len(key) <= 128 && utf8.ValidString(key) && !strings.ContainsRune(key, 0)
}

// Frozen pure replay source: internal/app/native_action_preview_store.go.
func mirrornativeActionHashV3(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, c := range value {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

// Frozen pure replay source: internal/adapters/postgres/native_ledger.go.
func mirrornativeAddV3(m map[string]funds.Money, key string, n funds.Money) error {
	value, err := m[key].Add(n)
	if err != nil {
		return err
	}
	m[key] = value
	return nil
}

// Frozen pure replay source: internal/app/native_payments_adopted.go.
func mirrornativeAdoptedFinalWithinV3(rule policy.TemplateAmountRule, total funds.Money) bool {
	if rule.Kind == "exact" {
		return rule.Amount == total
	}
	return rule.Kind == "range" && total.Cents() >= rule.Min.Cents() && total.Cents() <= rule.Max.Cents()
}

// Frozen pure replay source: internal/app/native_payments_adopted.go.
func mirrornativeAdoptedPaymentForecastAfterV3(input planning.ForecastInput, account funds.ID, debt planning.ForecastObligation, liability bankv2.LiabilityState, proof wireNativePaymentAdoptionProof, amount funds.Money) (planning.ForecastInput, error) {
	original, rule, e := mirrornativePaymentAdoptionOriginalV3(proof, debt, input.Scope, input.AsOf)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	if !mirrornativeAdoptedFinalWithinV3(rule.Amount, liability.Original.FinalTotal) || liability.Original.EvidenceHash != original.SourceHash || liability.Original.PayeeID != rule.Payee {
		return planning.ForecastInput{}, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", nil)
	}
	upper, e := original.Amount.Sub(liability.Paid)
	real, e2 := liability.Original.FinalTotal.Sub(liability.Paid)
	if e != nil || e2 != nil || debt.Amount.Cents() < real.Cents() || debt.Amount.Cents() > upper.Cents() {
		return planning.ForecastInput{}, nativeMirrorRejectV3("PAYMENT_SETTLEMENT_INCOMPLETE", nil)
	}
	after, e := mirrornativePaymentForecastAfterV3(input, account, debt, liability, amount)
	if e != nil {
		return after, e
	}
	paid, e := liability.Paid.Add(amount)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	remaining, e := liability.Original.FinalTotal.Sub(paid)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	found := false
	for i := range after.RetainedObligations {
		d := &after.RetainedObligations[i]
		if d.ID == debt.ID {
			if found {
				return planning.ForecastInput{}, nativeMirrorRejectV3("PAYMENT_SOURCE_INVALID", nil)
			}
			found = true
			d.Amount = remaining
		}
	}
	if !found {
		return planning.ForecastInput{}, nativeMirrorRejectV3("PAYMENT_DEBT_NOT_ADOPTED", nil)
	}
	return after, nil
}

// Frozen pure replay source: internal/app/native_payments_adopted.go.
func mirrornativeAdoptedPaymentRuleAmountV3(current policy.TemplateVersion, debt planning.ForecastObligation, liability bankv2.LiabilityState, proof wireNativePaymentAdoptionProof, requested funds.Money, now time.Time, timezone string) (funds.Money, error) {
	zero := funds.Money{}
	state, e := current.EffectiveState(now, timezone)
	if e != nil || state != "ACTIVE" || debt.PolicyID != current.PolicyID || current.Compiled.Name != proof.AdoptedVersion.Compiled.Name || liability.Validate() != nil {
		return zero, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", e)
	}
	original, oldRule, e := mirrornativePaymentAdoptionOriginalV3(proof, debt, current.Scope, now)
	if e != nil {
		return zero, e
	}
	rule, e := mirrornativeAdoptedRuleForV3(current)
	if e != nil {
		return zero, e
	}
	l := liability.Original
	if l.Kind != "OCCURRENCE" || l.ID != original.ID || l.PolicyID != original.PolicyID.String() || l.Period != original.DueDate[:7] || l.EvidenceHash != original.SourceHash || l.PayeeID != oldRule.Payee || l.PayeeID != rule.Payee {
		return zero, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", nil)
	}
	if !mirrornativeAdoptedFinalWithinV3(oldRule.Amount, l.FinalTotal) || !mirrornativeAdoptedFinalWithinV3(rule.Amount, l.FinalTotal) {
		return zero, nativeMirrorRejectV3("PAYMENT_FINAL_TOTAL_OUTSIDE_RULE", nil)
	}
	due, _ := policy.TemplateDate(original.DueDate)
	zone, e := policy.TemplateLocation(timezone)
	if e != nil {
		return zero, e
	}
	start := time.Date(due.Year(), due.Month(), due.Day(), 0, 0, 0, 0, zone).AddDate(0, 0, -rule.Prepare)
	if now.Before(start) {
		return zero, nativeMirrorRejectV3("PAYMENT_PREPARATION_NOT_OPEN", nil)
	}
	if !liability.Reserved.IsZero() {
		return zero, nativeMirrorRejectV3("PAYMENT_BANK_RESERVATION_PENDING", nil)
	}
	remaining, e := l.FinalTotal.Sub(liability.Paid)
	if e != nil || remaining.IsZero() {
		return zero, nativeMirrorRejectV3("PAYMENT_ALREADY_PAID", e)
	}
	upperRemaining, e := original.Amount.Sub(liability.Paid)
	if e != nil || debt.Amount.Cents() < remaining.Cents() || debt.Amount.Cents() > upperRemaining.Cents() {
		return zero, nativeMirrorRejectV3("PAYMENT_SETTLEMENT_INCOMPLETE", e)
	}
	if requested.IsZero() {
		requested = remaining
		if !rule.Cap.IsZero() && requested.Cents() > rule.Cap.Cents() {
			requested = rule.Cap
		}
	}
	if requested.Cents() > remaining.Cents() || !rule.Cap.IsZero() && requested.Cents() > rule.Cap.Cents() {
		return zero, nativeMirrorRejectV3("PAYMENT_AMOUNT_OUTSIDE_RULE", nil)
	}
	return requested, nil
}

// Frozen pure replay source: internal/app/native_payments_adopted.go.
func mirrornativeAdoptedRuleForV3(version policy.TemplateVersion) (mirrornativeAdoptedRuleV3, error) {
	configuration, e := version.Compiled.Decode()
	if e != nil {
		return mirrornativeAdoptedRuleV3{}, nativeMirrorRejectV3("PAYMENT_RULE_INVALID", e)
	}
	var r mirrornativeAdoptedRuleV3
	switch c := configuration.(type) {
	case *policy.RecurringObligationConfig:
		r = mirrornativeAdoptedRuleV3{Payee: c.PayeeID, Amount: c.AmountRule, Prepare: c.PrepareDaysBefore, Due: c.DueDay}
	case *policy.PeriodicTransferConfig:
		r = mirrornativeAdoptedRuleV3{Payee: c.PayeeID, Amount: c.AmountRule, Prepare: c.PrepareDaysBefore, Due: c.DueDay, Cap: c.SingleActionCap}
	default:
		return r, nativeMirrorRejectV3("PAYMENT_RULE_INVALID", nil)
	}
	if r.Amount.Kind != "exact" && r.Amount.Kind != "range" {
		return r, nativeMirrorRejectV3("PAYMENT_BILL_ORIGINAL_REQUIRED", nil)
	}
	return r, nil
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrornativeAssetClassV3(terms bankv2.ProductTerms) string {
	if terms.AssetClass != "FIXED_DEPOSIT" {
		return terms.AssetClass
	}
	switch terms.TermDays {
	case 7:
		return "FIXED_DEPOSIT_7D"
	case 30:
		return "FIXED_DEPOSIT_30D"
	case 90:
		return "FIXED_DEPOSIT_90D"
	}
	return "LOW_RISK_TERM"
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrornativeAssetCounterfactualV3(in planning.ForecastInput, effect bankv2.Effect) (planning.ForecastInput, error) {
	var after planning.ForecastInput
	raw, e := nativeMirrorEncodeV3(in)
	if e != nil || nativeCompactOriginalV3(raw, &after) != nil {
		return after, nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	for _, use := range effect.CashUses {
		found := false
		for i := range after.Facts.Accounts {
			a := &after.Facts.Accounts[i]
			if a.ID.String() != use.AccountID {
				continue
			}
			a.Balance, e = a.Balance.Sub(use.Amount)
			if e != nil {
				return after, e
			}
			found = true
		}
		if !found {
			return after, mirrornativeResourceConflictV3()
		}
	}
	if effect.Kind == bankv2.Purchase {
		after.Facts.CurrentCash, e = after.Facts.CurrentCash.Sub(effect.Amount)
	} else {
		after.Facts.CurrentCash, e = after.Facts.CurrentCash.Add(effect.Net)
		found := false
		for i := range after.Facts.Accounts {
			a := &after.Facts.Accounts[i]
			if a.ID.String() == effect.ReturnAccountID {
				a.Balance, e = a.Balance.Add(effect.Net)
				found = true
			}
		}
		if !found {
			return after, mirrornativeResourceConflictV3()
		}
	}
	if e != nil {
		return after, e
	}
	if effect.SourceGoalID != "" {
		found := false
		for i := range after.Facts.Goals {
			g := &after.Facts.Goals[i]
			if g.ID.String() != effect.SourceGoalID {
				continue
			}
			found = true
			if effect.Kind == bankv2.Purchase {
				g.CashOwned, e = g.CashOwned.Sub(effect.Amount)
			} else {
				principal, pe := g.Owned.Sub(g.CashOwned)
				if pe != nil || principal.Cents() < effect.Amount.Cents() {
					return after, mirrornativeResourceConflictV3()
				}
				_, e = principal.Sub(effect.Amount)
				if e == nil {
					g.CashOwned, e = g.CashOwned.Add(effect.Net)
				}
				if e == nil {
					cost, _ := effect.Fee.Add(effect.Loss)
					g.Owned, e = g.Owned.Sub(cost)
				}
			}
			if e != nil {
				return after, e
			}
		}
		if !found {
			return after, mirrornativeResourceConflictV3()
		}
	}
	return after, nil
}

// Frozen pure replay source: internal/app/native_assets_reserve.go.
func mirrornativeAssetExpectedHoldsV3(operation funds.ID, preview wireNativeAssetPreview) ([]wireNativeHold, error) {
	e := preview.BankEffect
	out := []wireNativeHold{}
	if !mirrornativeAssetKindV3(e.Kind) || e.Validate() != nil {
		return nil, mirrornativeResourceConflictV3()
	}
	add := func(kind, id, detail string, amount funds.Money) {
		if !amount.IsZero() {
			out = append(out, wireNativeHold{OperationID: operation, ResourceKind: kind, ResourceID: id, ResourceDetail: detail, Amount: amount, State: funds.Reserved})
		}
	}
	if e.Kind == bankv2.Purchase {
		if e.SourceGoalID == "" {
			for _, use := range preview.CashResources.CashUses {
				add("CASH", use.AccountID, "", use.Amount)
			}
		} else {
			add("GOAL", e.SourceGoalID, "", e.Amount)
			for _, use := range preview.GoalUses {
				ref, err := nativeMirrorEncodeV3(mirrorNativeLotReferenceV3{AccountID: e.CashUses[0].AccountID, SourceKind: use.SourceKind, SourceID: use.SourceID})
				if err != nil {
					return nil, err
				}
				add("GOAL_LOT", e.SourceGoalID, string(ref), use.Amount)
				if use.SourceKind == "INCOME" {
					origin, err := funds.ParseID(use.SourceID)
					if err != nil {
						return nil, err
					}
					account, _ := funds.ParseID(e.CashUses[0].AccountID)
					add("ASSIGNED", funds.LocationID(origin, account).String(), "", use.Amount)
				}
			}
		}
	} else {
		add("POSITION", e.PositionID, "", e.Amount)
		add("INCOMING_CASH", e.ReturnAccountID, "", e.Net)
		if e.SourceGoalID != "" {
			add("INCOMING_GOAL", e.SourceGoalID, "", e.Net)
		}
	}
	return out, nil
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrornativeAssetExpiryV3(now time.Time, tz string, version policy.TemplateVersion, quote *bankv2.Quote, product *bankv2.ProductSnapshot) (time.Time, error) {
	end := now.Add(2 * time.Minute)
	zone, e := policy.TemplateLocation(tz)
	if e != nil {
		return end, e
	}
	local := now.In(zone)
	midnight := time.Date(local.Year(), local.Month(), local.Day()+1, 0, 0, 0, 0, zone).UTC()
	if midnight.Before(end) {
		end = midnight
	}
	_, validUntil, e := policy.TemplateValidity(version.Compiled, version.ConfirmedAt, tz)
	if e != nil {
		return end, e
	}
	if validUntil != nil && validUntil.Before(end) {
		end = *validUntil
	}
	if quote != nil && quote.ExpiresAt.Before(end) {
		end = quote.ExpiresAt
	}
	if product != nil && product.Availability.EffectiveUntil.Before(end) {
		end = product.Availability.EffectiveUntil
	}
	if !end.After(now) {
		return end, nativeMirrorRejectV3("AUTHORIZATION_EXPIRED", nil)
	}
	return end, nil
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrornativeAssetKindV3(kind bankv2.Kind) bool {
	return kind == bankv2.Purchase || kind == bankv2.Redeem || kind == bankv2.Mature
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrornativeAssetManagedV3(n nativeFrozenLedgerV3, pending []bankv2.Request, goal *funds.ID) (funds.Money, error) {
	var total funds.Money
	for _, p := range n.Positions {
		if p.Closed || !mirrornativeAssetSameGoalV3(goal, func() string {
			if p.GoalID == nil {
				return ""
			}
			return p.GoalID.String()
		}()) {
			continue
		}
		var e error
		total, e = total.Add(p.Principal)
		if e != nil {
			return funds.Money{}, e
		}
	}
	for _, p := range pending {
		if !mirrornativeAssetSameGoalV3(goal, p.Effect.SourceGoalID) {
			continue
		}
		var e error
		total, e = total.Add(p.Effect.Amount)
		if e != nil {
			return funds.Money{}, e
		}
	}
	return total, nil
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrornativeAssetObjectIDV3(namespace, kind, id string) funds.ID {
	h := sha256.Sum256([]byte("native-asset-object-v1\x00" + namespace + "\x00" + kind + "\x00" + id))
	var out funds.ID
	copy(out[:], h[:16])
	out[6] = (out[6] & 15) | 80
	out[8] = (out[8] & 63) | 128
	return out
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrornativeAssetSameGoalV3(a *funds.ID, b string) bool {
	return a == nil && b == "" || a != nil && a.String() == b
}

// Frozen pure replay source: internal/app/native_assets.go.
func mirrornativeAssetStateV3(n nativeFrozenLedgerV3, state bankv2.CurrentState, scope policy.TemplateOwnerScope) error {
	if state.Protocol != bankv2.Protocol || state.Namespace != n.Namespace || state.UserID != scope.UserID.String() || state.CapturedAt.IsZero() || len(state.Products) > 100 || len(state.Positions) > 10000 || len(state.Accounts) != len(n.Accounts) {
		return nativeMirrorRejectV3("ASSET_BANK_STATE_INVALID", nil)
	}
	accounts := map[string]wireNativeAccount{}
	for _, a := range n.Accounts {
		accounts[a.ID.String()] = a
	}
	for _, a := range state.Accounts {
		local, ok := accounts[a.ID]
		if !ok || local.Balance != a.Balance {
			return nativeMirrorRejectV3("ASSET_BANK_STATE_CHANGED", nil)
		}
		delete(accounts, a.ID)
	}
	positions := map[string]wireNativePosition{}
	for _, p := range n.Positions {
		positions[p.ID] = p
	}
	if len(positions) != len(state.Positions) {
		return nativeMirrorRejectV3("ASSET_BANK_STATE_CHANGED", nil)
	}
	for _, p := range state.Positions {
		local, ok := positions[p.Opening.PositionID]
		h, e := p.Opening.Digest()
		if !ok || e != nil || h != p.OpeningHash || h != local.OriginalHash || local.Closed != p.Closed || p.Opening.Namespace != n.Namespace || p.Opening.UserID != scope.UserID.String() {
			return nativeMirrorRejectV3("ASSET_BANK_STATE_CHANGED", e)
		}
		delete(positions, p.Opening.PositionID)
	}
	products := map[string]bool{}
	for _, p := range state.Products {
		if products[p.Terms.ProductID] || p.Validate() != nil {
			return nativeMirrorRejectV3("ASSET_BANK_STATE_INVALID", nil)
		}
		products[p.Terms.ProductID] = true
	}
	goals := map[string]wireNativeGoalBalance{}
	for _, g := range n.Goals {
		goals[g.GoalID.String()] = g
	}
	for _, g := range state.Goals {
		local, ok := goals[g.ID]
		if !ok || local.AccountID.String() != g.AccountID || local.Cash != g.Cash || local.Principal != g.Principal || local.Loss != g.Loss {
			return nativeMirrorRejectV3("ASSET_BANK_STATE_CHANGED", nil)
		}
		delete(goals, g.ID)
	}
	for _, g := range goals {
		if !g.Cash.IsZero() || !g.Principal.IsZero() || !g.Loss.IsZero() {
			return nativeMirrorRejectV3("ASSET_BANK_STATE_CHANGED", nil)
		}
	}
	return nil
}

// Frozen pure replay source: internal/app/native_bills_contract.go.
func mirrornativeBillPaymentForecastAfterV3(input planning.ForecastInput, account funds.ID, debt planning.ForecastObligation, liability bankv2.LiabilityState, proof wireNativeBillProof, amount funds.Money) (planning.ForecastInput, error) {
	var out planning.ForecastInput
	raw, e := nativeMirrorEncodeV3(input)
	if e != nil || nativeCompactOriginalV3(raw, &out) != nil {
		return out, nativeMirrorRejectV3("PAYMENT_FORECAST_INVALID", e)
	}
	original, e := mirrornativeBillProofOriginalV3(proof, input.Scope, input.AsOf)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	if liability.Validate() != nil || liability.Original != proof.BankReference || liability.Paid != proof.ProvenPaid || debt.ID != liability.Original.ID || amount.IsZero() {
		return planning.ForecastInput{}, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", nil)
	}
	remaining, e := original.FinalTotal.Sub(liability.Paid)
	if e != nil || amount.Cents() > remaining.Cents() || remaining != debt.Amount {
		return planning.ForecastInput{}, nativeMirrorRejectV3("PAYMENT_AMOUNT_OUTSIDE_RULE", e)
	}
	paid, e := liability.Paid.Add(amount)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	remaining, e = remaining.Sub(amount)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	found := false
	for i := range out.Facts.Accounts {
		a := &out.Facts.Accounts[i]
		if a.ID != account {
			continue
		}
		if found || !a.Verified {
			return planning.ForecastInput{}, nativeMirrorRejectV3("PAYMENT_SOURCE_INVALID", nil)
		}
		found = true
		a.Balance, e = a.Balance.Sub(amount)
		if e != nil {
			return planning.ForecastInput{}, nativeMirrorRejectV3("FUNDS_UNAVAILABLE", e)
		}
	}
	if !found {
		return planning.ForecastInput{}, nativeMirrorRejectV3("PAYMENT_SOURCE_INVALID", nil)
	}
	out.Facts.CurrentCash, e = out.Facts.CurrentCash.Sub(amount)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	found = false
	for i := range out.Facts.Bills {
		bill := &out.Facts.Bills[i]
		if bill.ID != original.ID {
			continue
		}
		if found || !bill.Verified || bill.AccountID != original.StatementAccountID || bill.DueDate != original.DueDate || bill.Total != original.FinalTotal || bill.Paid != liability.Paid {
			return planning.ForecastInput{}, nativeMirrorRejectV3("BILL_SETTLEMENT_INCOMPLETE", nil)
		}
		found = true
		bill.Paid = paid
	}
	if !found {
		return planning.ForecastInput{}, nativeMirrorRejectV3("BILL_ORIGINAL_REQUIRED", nil)
	}
	found = false
	for i := range out.ExternalLiabilities {
		bill := &out.ExternalLiabilities[i]
		if bill.ID != debt.ID {
			continue
		}
		if found || !bill.Verified || bill.DueDate != debt.DueDate || bill.Remaining != debt.Amount || bill.SourceHash != proof.OriginalHash {
			return planning.ForecastInput{}, nativeMirrorRejectV3("BILL_SETTLEMENT_INCOMPLETE", nil)
		}
		found = true
		bill.Remaining = remaining
	}
	if !found {
		return planning.ForecastInput{}, nativeMirrorRejectV3("BILL_ORIGINAL_REQUIRED", nil)
	}
	for i := range out.RetainedObligations {
		row := &out.RetainedObligations[i]
		if row.ID != debt.ID {
			continue
		}
		if row.PolicyID != debt.PolicyID || row.VersionID != debt.VersionID || row.DueDate != debt.DueDate || row.Amount != debt.Amount {
			return planning.ForecastInput{}, nativeMirrorRejectV3("BILL_SETTLEMENT_INCOMPLETE", nil)
		}
		row.Amount = remaining
	}
	return out, nil
}

// Frozen pure replay source: internal/app/native_bills_contract.go.
func mirrornativeBillPaymentRuleAmountV3(current policy.TemplateVersion, debt planning.ForecastObligation, liability bankv2.LiabilityState, proof wireNativeBillProof, requested funds.Money, now time.Time, timezone string) (funds.Money, error) {
	zero := funds.Money{}
	state, e := current.EffectiveState(now, timezone)
	if e != nil || state != "ACTIVE" || !debt.Verified || debt.PolicyID != current.PolicyID || !mirrornativeActionHashV3(debt.SourceHash) || liability.Validate() != nil {
		return zero, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", e)
	}
	original, e := mirrornativeBillProofOriginalV3(proof, current.Scope, now)
	if e != nil {
		return zero, e
	}
	if liability.Original != proof.BankReference || liability.OriginalHash != proof.BankReferenceHash || liability.Paid != proof.ProvenPaid || debt.ID != "bill:"+original.ID.String() || debt.DueDate != original.DueDate {
		return zero, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", nil)
	}
	adopted := proof.AdoptedVersion
	if adopted.Scope != current.Scope || adopted.PolicyID != debt.PolicyID || adopted.VersionID != debt.VersionID || !adopted.ConfirmationVerified || adopted.ConfirmedConfigurationHash != adopted.Compiled.Hash || adopted.Compiled.Hash != debt.SourceHash || adopted.ConfirmedAt.IsZero() || adopted.ConfirmedAt.After(now) {
		return zero, nativeMirrorRejectV3("BILL_ADOPTION_INVALID", nil)
	}
	configuration, e := current.Compiled.Decode()
	if e != nil {
		return zero, nativeMirrorRejectV3("PAYMENT_RULE_INVALID", e)
	}
	rule, ok := configuration.(*policy.RecurringObligationConfig)
	if !ok || rule.AmountRule.Kind != "bill_balance" || rule.AmountRule.AccountID == nil || *rule.AmountRule.AccountID != original.StatementAccountID || rule.PayeeID != original.PayeeID {
		return zero, nativeMirrorRejectV3("PAYMENT_BILL_RULE_MISMATCH", nil)
	}
	old, e := adopted.Compiled.Decode()
	if e != nil {
		return zero, nativeMirrorRejectV3("BILL_ADOPTION_INVALID", e)
	}
	oldRule, ok := old.(*policy.RecurringObligationConfig)
	if !ok || oldRule.AmountRule.Kind != "bill_balance" || oldRule.AmountRule.AccountID == nil || *oldRule.AmountRule.AccountID != original.StatementAccountID || oldRule.PayeeID != original.PayeeID {
		return zero, nativeMirrorRejectV3("BILL_ADOPTION_INVALID", nil)
	}
	due, e := policy.TemplateDate(original.DueDate)
	zone, ze := policy.TemplateLocation(timezone)
	if e != nil || ze != nil {
		return zero, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", e)
	}
	start := time.Date(due.Year(), due.Month(), due.Day(), 0, 0, 0, 0, zone).AddDate(0, 0, -rule.PrepareDaysBefore)
	if now.Before(start) {
		return zero, nativeMirrorRejectV3("PAYMENT_PREPARATION_NOT_OPEN", nil)
	}
	if !liability.Reserved.IsZero() {
		return zero, nativeMirrorRejectV3("PAYMENT_BANK_RESERVATION_PENDING", nil)
	}
	remaining, e := original.FinalTotal.Sub(liability.Paid)
	if e != nil || remaining.IsZero() {
		return zero, nativeMirrorRejectV3("PAYMENT_ALREADY_PAID", e)
	}
	if debt.Amount != remaining {
		return zero, nativeMirrorRejectV3("BILL_SETTLEMENT_INCOMPLETE", nil)
	}
	if requested.IsZero() {
		requested = remaining
	}
	if requested.Cents() > remaining.Cents() {
		return zero, nativeMirrorRejectV3("PAYMENT_AMOUNT_OUTSIDE_RULE", nil)
	}
	return requested, nil
}

// Frozen pure replay source: internal/app/native_bills_contract.go.
func mirrornativeBillProofOriginalV3(proof wireNativeBillProof, scope policy.TemplateOwnerScope, now time.Time) (wireNativeBillOriginal, error) {
	if proof.FirstObservedAt.IsZero() || proof.FirstObservedAt.After(now) || nakedHash(proof.Original) != proof.OriginalHash {
		return wireNativeBillOriginal{}, nativeMirrorRejectV3("BILL_ORIGINAL_INVALID", nil)
	}

	var header struct {
		Namespace string `json:"bank_namespace"`
	}
	if json.Unmarshal(proof.Original, &header) != nil {
		return wireNativeBillOriginal{}, nativeMirrorRejectV3("BILL_ORIGINAL_INVALID", nil)
	}
	original, e := mirrordecodeNativeBillOriginalV3(proof.Original, scope, header.Namespace, proof.FirstObservedAt)
	if e != nil {
		return wireNativeBillOriginal{}, e
	}
	want := mirrornativeBillReferenceV3(original, proof.Original)
	raw, _ := json.Marshal(want)
	if want.Validate() != nil || want != proof.BankReference || nakedHash(raw) != proof.BankReferenceHash {
		return wireNativeBillOriginal{}, nativeMirrorRejectV3("BILL_BANK_ORIGINAL_MISMATCH", nil)
	}
	return original, nil
}

// Frozen pure replay source: internal/app/native_bills_contract.go.
func mirrornativeBillReferenceV3(original wireNativeBillOriginal, raw []byte) bankv2.LiabilityReference {
	return bankv2.LiabilityReference{ID: "bill:" + original.ID.String(), Kind: "BILL", FinalTotal: original.FinalTotal, PayeeID: original.PayeeID, EvidenceHash: nakedHash(raw)}
}

// Frozen pure replay source: internal/app/native_bridge.go.
func mirrornativeBridgeReviewHashV3(preview wireNativeBridgePreview) (string, error) {
	preview.ReviewHash = ""
	raw, err := nativeMirrorEncodeV3(preview)
	return nakedHash(raw), err
}

// Frozen pure replay source: internal/app/native_assets_resources.go.
func mirrornativeCashOriginUsesV3(resources wireNativeCashResources) []bankv2.OriginUse {
	uses := make([]bankv2.OriginUse, 0, len(resources.IncomeUses))
	for _, use := range resources.IncomeUses {
		uses = append(uses, bankv2.OriginUse{OriginID: use.OriginID.String(), AccountID: use.AccountID.String(), Amount: use.Amount, Bucket: "AVAILABLE"})
	}
	return uses
}

// Frozen pure replay source: internal/adapters/postgres/native_ledger.go.
func mirrornativeLotKeyV3(g, a funds.ID, k, id string) string {
	return g.String() + ":" + a.String() + ":" + k + ":" + id
}

// Frozen pure replay source: internal/app/native_payments_adopted.go.
func mirrornativePaymentAdoptionOriginalV3(proof wireNativePaymentAdoptionProof, debt planning.ForecastObligation, scope policy.TemplateOwnerScope, now time.Time) (planning.ForecastObligation, mirrornativeAdoptedRuleV3, error) {
	var original planning.ForecastObligation
	v := proof.AdoptedVersion
	if len(proof.ObligationOriginal) == 0 || len(proof.ObligationOriginal) > 65536 || nakedHash(proof.ObligationOriginal) != proof.ObligationHash || nativeCompactOriginalV3(proof.ObligationOriginal, &original) != nil || !original.Verified || !debt.Verified || original.ID != debt.ID || original.PolicyID != debt.PolicyID || original.VersionID != debt.VersionID || original.DueDate != debt.DueDate || original.SourceHash != debt.SourceHash || v.Scope != scope || v.PolicyID != original.PolicyID || v.VersionID != original.VersionID || v.VersionID == (funds.ID{}) || v.Version == 0 || !v.ConfirmationVerified || v.ConfirmedConfigurationHash != v.Compiled.Hash || v.Compiled.Hash != original.SourceHash || v.ConfirmedAt.IsZero() || v.ConfirmedAt.After(now) {
		return original, mirrornativeAdoptedRuleV3{}, nativeMirrorRejectV3("PAYMENT_ADOPTION_INVALID", nil)
	}
	rule, e := mirrornativeAdoptedRuleForV3(v)
	if e != nil {
		return original, rule, e
	}
	upper := rule.Amount.Amount
	if rule.Amount.Kind == "range" {
		upper = rule.Amount.Max
	}
	due, e := policy.TemplateDate(original.DueDate)
	if e != nil || original.Amount != upper || original.ID != v.PolicyID.String()+":monthly:"+original.DueDate[:7] || policy.TemplateDueDate(due.Year(), due.Month(), rule.Due) != original.DueDate {
		return original, rule, nativeMirrorRejectV3("PAYMENT_ADOPTION_INVALID", e)
	}
	return original, rule, nil
}

// Frozen pure replay source: internal/app/native_payments_preview.go.
func mirrornativePaymentEffectV3(id funds.ID, resources wireNativeCashResources, liability bankv2.LiabilityReference, expires time.Time) bankv2.Effect {
	return bankv2.Effect{Kind: bankv2.Payment, BusinessKey: id.String(), Amount: resources.Amount, CashUses: append([]bankv2.CashUse{}, resources.CashUses...), OriginUses: mirrornativeCashOriginUsesV3(resources), ReturnUses: append([]bankv2.ReturnUse{}, resources.ReturnUses...), Liability: &liability, LatestArrivalAt: expires}
}

// Frozen pure replay source: internal/app/native_payments_policy.go.
func mirrornativePaymentForecastAfterV3(input planning.ForecastInput, account funds.ID, debt planning.ForecastObligation, liability bankv2.LiabilityState, amount funds.Money) (planning.ForecastInput, error) {
	raw, e := nativeMirrorEncodeV3(input)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	var after planning.ForecastInput
	if nativeCompactOriginalV3(raw, &after) != nil {
		return after, nativeMirrorRejectV3("PAYMENT_FORECAST_INVALID", nil)
	}
	_, dateErr := policy.TemplateDate(debt.DueDate)
	if dateErr != nil || amount.IsZero() || liability.Validate() != nil || liability.Original.PolicyID != debt.PolicyID.String() || liability.Original.Period != debt.DueDate[:7] || liability.Original.ID != debt.ID {
		return after, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", nil)
	}
	found := false
	for i := range after.Facts.Accounts {
		a := &after.Facts.Accounts[i]
		if a.ID != account {
			continue
		}
		if found || !a.Verified {
			return after, nativeMirrorRejectV3("PAYMENT_SOURCE_INVALID", nil)
		}
		found = true
		a.Balance, e = a.Balance.Sub(amount)
		if e != nil {
			return after, nativeMirrorRejectV3("FUNDS_UNAVAILABLE", e)
		}
	}
	if !found {
		return after, nativeMirrorRejectV3("PAYMENT_SOURCE_INVALID", nil)
	}
	after.Facts.CurrentCash, e = after.Facts.CurrentCash.Sub(amount)
	if e != nil {
		return after, e
	}
	paid, e := liability.Paid.Add(amount)
	if e != nil || paid.Cents() > liability.Original.FinalTotal.Cents() {
		return after, nativeMirrorRejectV3("PAYMENT_AMOUNT_OUTSIDE_RULE", e)
	}
	found = false
	for i := range after.Facts.Settlements {
		s := &after.Facts.Settlements[i]
		if s.PolicyID == debt.PolicyID && s.Period == liability.Original.Period {
			if found || !s.Verified || s.Paid != liability.Paid {
				return after, nativeMirrorRejectV3("PAYMENT_SOURCE_INVALID", nil)
			}
			found = true
			s.Paid = paid
			total := liability.Original.FinalTotal
			s.FinalTotal = &total
		}
	}
	if !found {
		if !liability.Paid.IsZero() {
			return after, nativeMirrorRejectV3("PAYMENT_SOURCE_INVALID", nil)
		}
		total := liability.Original.FinalTotal
		after.Facts.Settlements = append(after.Facts.Settlements, policy.TemplateSettlementFact{PolicyID: debt.PolicyID, Period: liability.Original.Period, Paid: paid, FinalTotal: &total, Verified: true})
	}
	for i := range after.RetainedObligations {
		row := &after.RetainedObligations[i]
		if row.ID == debt.ID {
			if row.PolicyID != debt.PolicyID || row.VersionID != debt.VersionID || row.Amount != debt.Amount {
				return after, nativeMirrorRejectV3("PAYMENT_SOURCE_INVALID", nil)
			}
			row.Amount, e = row.Amount.Sub(amount)
			if e != nil {
				return after, e
			}
		}
	}
	return after, nil
}

// Frozen pure replay source: internal/app/native_payments_preview.go.
func mirrornativePaymentMarginsV3(before, after planning.ForecastResult) error {
	if len(before.Points) != len(after.Points) || len(before.Points) == 0 || after.MaximumDeficit.Cents() > before.MaximumDeficit.Cents() {
		return nativeMirrorRejectV3("PAYMENT_PROTECTION_REDUCED", nil)
	}
	for i, left := range before.Points {
		right := after.Points[i]
		a, ok := new(big.Int).SetString(left.MarginCents, 10)
		b, ok2 := new(big.Int).SetString(right.MarginCents, 10)
		if !ok || !ok2 || left.Date != right.Date || left.Phase != right.Phase || b.Cmp(a) < 0 {
			return nativeMirrorRejectV3("PAYMENT_PROTECTION_REDUCED", nil)
		}
	}
	return nil
}

// Frozen pure replay source: internal/app/native_payments_bill.go.
func mirrornativePaymentPreviewAfterV3(input planning.ForecastInput, preview wireNativePaymentPreview, amount funds.Money) (planning.ForecastInput, error) {
	if preview.Liability.Original.Kind == "BILL" {
		if preview.BillProof == nil || preview.AdoptionProof != nil {
			return planning.ForecastInput{}, nativeMirrorRejectV3("BILL_ORIGINAL_REQUIRED", nil)
		}
		return mirrornativeBillPaymentForecastAfterV3(input, preview.Request.SourceAccountID, preview.Debt, preview.Liability, *preview.BillProof, amount)
	}
	if preview.BillProof != nil {
		return planning.ForecastInput{}, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	if preview.AdoptionProof != nil {
		return mirrornativeAdoptedPaymentForecastAfterV3(input, preview.Request.SourceAccountID, preview.Debt, preview.Liability, *preview.AdoptionProof, amount)
	}
	return mirrornativePaymentForecastAfterV3(input, preview.Request.SourceAccountID, preview.Debt, preview.Liability, amount)
}

// Frozen pure replay source: internal/app/native_payments_bill.go.
func mirrornativePaymentPreviewAmountV3(preview wireNativePaymentPreview, now time.Time) (funds.Money, error) {
	if preview.Liability.Original.Kind == "BILL" {
		if preview.BillProof == nil || preview.AdoptionProof != nil {
			return funds.Money{}, nativeMirrorRejectV3("BILL_ORIGINAL_REQUIRED", nil)
		}
		return mirrornativeBillPaymentRuleAmountV3(preview.Policy.Version, preview.Debt, preview.Liability, *preview.BillProof, preview.Request.Amount, now, preview.Timezone)
	}
	if preview.BillProof != nil {
		return funds.Money{}, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	if preview.AdoptionProof != nil {
		return mirrornativeAdoptedPaymentRuleAmountV3(preview.Policy.Version, preview.Debt, preview.Liability, *preview.AdoptionProof, preview.Request.Amount, now, preview.Timezone)
	}
	return mirrornativePaymentRuleAmountV3(preview.Policy.Version, preview.Debt, preview.Liability, preview.Request.Amount, now, preview.Timezone)
}

// Frozen pure replay source: internal/app/native_payments_policy.go.
func mirrornativePaymentRuleAmountV3(version policy.TemplateVersion, debt planning.ForecastObligation, liability bankv2.LiabilityState, requested funds.Money, now time.Time, timezone string) (funds.Money, error) {
	var zero funds.Money
	state, e := version.EffectiveState(now, timezone)
	if e != nil || state != "ACTIVE" || !debt.Verified || debt.PolicyID != version.PolicyID || debt.VersionID != version.VersionID || !mirrornativeActionHashV3(debt.SourceHash) || liability.Validate() != nil || liability.Original.Kind != "OCCURRENCE" || liability.Original.ID != debt.ID || liability.Original.PolicyID != version.PolicyID.String() || liability.Original.EvidenceHash != debt.SourceHash {
		return zero, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", e)
	}
	date, e := policy.TemplateDate(debt.DueDate)
	if e != nil || liability.Original.Period != debt.DueDate[:7] {
		return zero, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", e)
	}
	configuration, e := version.Compiled.Decode()
	if e != nil {
		return zero, nativeMirrorRejectV3("PAYMENT_RULE_INVALID", e)
	}
	var payee string
	var rule policy.TemplateAmountRule
	var prepare, dueDay int
	var cap funds.Money
	switch value := configuration.(type) {
	case *policy.RecurringObligationConfig:
		payee, rule, prepare, dueDay = value.PayeeID, value.AmountRule, value.PrepareDaysBefore, value.DueDay
	case *policy.PeriodicTransferConfig:
		payee, rule, prepare, dueDay, cap = value.PayeeID, value.AmountRule, value.PrepareDaysBefore, value.DueDay, value.SingleActionCap
	default:
		return zero, nativeMirrorRejectV3("PAYMENT_RULE_INVALID", nil)
	}
	if liability.Original.PayeeID != payee || policy.TemplateDueDate(date.Year(), date.Month(), dueDay) != debt.DueDate {
		return zero, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", nil)
	}
	final := liability.Original.FinalTotal
	switch rule.Kind {
	case "exact":
		if final != rule.Amount {
			return zero, nativeMirrorRejectV3("PAYMENT_FINAL_TOTAL_OUTSIDE_RULE", nil)
		}
	case "range":
		if final.Cents() < rule.Min.Cents() || final.Cents() > rule.Max.Cents() {
			return zero, nativeMirrorRejectV3("PAYMENT_FINAL_TOTAL_OUTSIDE_RULE", nil)
		}
	default:
		return zero, nativeMirrorRejectV3("PAYMENT_BILL_ORIGINAL_REQUIRED", nil)
	}
	zone, e := policy.TemplateLocation(timezone)
	if e != nil {
		return zero, e
	}
	start := time.Date(date.Year(), date.Month(), date.Day(), 0, 0, 0, 0, zone).AddDate(0, 0, -prepare)
	if now.Before(start) {
		return zero, nativeMirrorRejectV3("PAYMENT_PREPARATION_NOT_OPEN", nil)
	}
	if !liability.Reserved.IsZero() {
		return zero, nativeMirrorRejectV3("PAYMENT_BANK_RESERVATION_PENDING", nil)
	}
	remaining, e := final.Sub(liability.Paid)
	if e != nil || remaining.IsZero() {
		return zero, nativeMirrorRejectV3("PAYMENT_ALREADY_PAID", e)
	}
	if requested.IsZero() {
		requested = remaining
		if !cap.IsZero() && requested.Cents() > cap.Cents() {
			requested = cap
		}
	}
	if requested.Cents() > remaining.Cents() || requested.Cents() > debt.Amount.Cents() || !cap.IsZero() && requested.Cents() > cap.Cents() {
		return zero, nativeMirrorRejectV3("PAYMENT_AMOUNT_OUTSIDE_RULE", nil)
	}
	return requested, nil
}

// Frozen pure replay source: internal/app/native_assets_resources.go.
func mirrornativeResourceAddV3[K comparable](values map[K]funds.Money, key K, value funds.Money) error {
	next, err := values[key].Add(value)
	if err != nil {
		return err
	}
	values[key] = next
	return nil
}

// Frozen pure replay source: internal/app/native_assets_resources.go.
func mirrornativeResourceConflictV3() error {
	return nativeMirrorRejectV3("NATIVE_RESOURCE_CONFLICT", nil)
}

// Frozen pure replay source: internal/app/native_assets_resources.go.
func mirrornativeResourceMinimumV3(a, b funds.Money) funds.Money {
	if a.Cents() < b.Cents() {
		return a
	}
	return b
}

// Frozen pure replay source: internal/app/native_assets_resources.go.
func mirrornativeReturnKeyV3(key string, account funds.ID) string {
	return key + ":" + account.String()
}

// Frozen pure replay source: internal/app/native_assets_resources.go.
func mirrornativeUnownedCashAvailabilityV3(n nativeFrozenLedgerV3) (mirrornativeUnownedAvailabilityV3, error) {
	a := mirrornativeUnownedAvailabilityV3{accounts: map[funds.ID]bool{}, free: map[funds.ID]funds.Money{}, slack: map[funds.ID]funds.Money{}}
	if n.Income.Validate() != nil {
		return a, mirrornativeResourceConflictV3()
	}
	snapshot := n.Income.Snapshot()
	for _, account := range n.Accounts {
		if account.ID == (funds.ID{}) || a.accounts[account.ID] {
			return a, mirrornativeResourceConflictV3()
		}
		free, err := account.Balance.Sub(account.GoalOwned)
		if err != nil {
			return a, mirrornativeResourceConflictV3()
		}
		a.accounts[account.ID], a.free[account.ID] = true, free
	}
	if len(a.accounts) != len(snapshot.ScopeAccountIDs) {
		return a, mirrornativeResourceConflictV3()
	}
	for _, account := range snapshot.ScopeAccountIDs {
		if !a.accounts[account] {
			return a, mirrornativeResourceConflictV3()
		}
	}
	type heldKey struct{ operation, account funds.ID }
	cashHeld, incomeHeld := map[heldKey]funds.Money{}, map[heldKey]funds.Money{}
	for _, hold := range n.Holds {
		if hold.State != funds.Reserved || hold.ResourceKind != "CASH" {
			continue
		}
		account, err := funds.ParseID(hold.ResourceID)
		if err != nil || !a.accounts[account] || hold.Amount.IsZero() {
			return a, mirrornativeResourceConflictV3()
		}
		if err = mirrornativeResourceAddV3(cashHeld, heldKey{hold.OperationID, account}, hold.Amount); err != nil {
			return a, err
		}
	}
	for _, reservation := range snapshot.Reservations {
		if reservation.State != funds.Reserved {
			continue
		}
		for _, use := range reservation.Uses {
			if err := mirrornativeResourceAddV3(incomeHeld, heldKey{reservation.ActionID, use.AccountID}, use.Amount); err != nil {
				return a, err
			}
		}
	}
	for key, amount := range incomeHeld {
		if amount.Cents() > cashHeld[key].Cents() {
			cashHeld[key] = amount
		}
	}
	for key, amount := range cashHeld {
		var err error
		a.free[key.account], err = a.free[key.account].Sub(amount)
		if err != nil {
			return a, mirrornativeResourceConflictV3()
		}
	}
	incomeTotal := map[funds.ID]funds.Money{}
	for _, location := range snapshot.Locations {
		var err error
		a.free[location.AccountID], err = a.free[location.AccountID].Sub(location.LegacyReserved)
		if err != nil {
			return a, mirrornativeResourceConflictV3()
		}
		if location.Available.IsZero() {
			continue
		}
		a.income = append(a.income, funds.IncomeUse{FragmentID: location.FragmentID, OriginID: location.OriginID, AccountID: location.AccountID, Amount: location.Available})
		if err = mirrornativeResourceAddV3(incomeTotal, location.AccountID, location.Available); err != nil {
			return a, err
		}
	}
	ownedReturn, heldOwnedReturn := map[string]funds.Money{}, map[string]funds.Money{}
	for _, lot := range n.GoalLots {
		if lot.SourceKind != "PRINCIPAL_RETURN" {
			continue
		}
		key := mirrornativeReturnKeyV3(lot.SourceID, lot.AccountID)
		if lot.Reserved.Cents() > lot.Available.Cents() {
			return a, mirrornativeResourceConflictV3()
		}
		if err := mirrornativeResourceAddV3(ownedReturn, key, lot.Available); err != nil {
			return a, err
		}
		if err := mirrornativeResourceAddV3(heldOwnedReturn, key, lot.Reserved); err != nil {
			return a, err
		}
	}
	returns := map[string]wireNativePrincipalReturn{}
	for _, original := range n.PrincipalReturns {
		var proof bankv2.PrincipalReturn
		if _, duplicate := returns[original.BankKey]; duplicate || original.BankKey == "" || nakedHash(original.Original) != original.OriginalHash || json.Unmarshal(original.Original, &proof) != nil || proof.Role != "PRINCIPAL_RETURN" || proof.OperationKey != original.BankKey || proof.PositionID != original.PositionID || proof.Amount != original.Amount || !proof.OccurredAt.Equal(original.OccurredAt) {
			return a, mirrornativeResourceConflictV3()
		}
		returns[original.BankKey] = original
	}
	returnTotal, locations := map[funds.ID]funds.Money{}, map[string]bool{}
	for _, location := range n.ReturnLocations {
		key := mirrornativeReturnKeyV3(location.BankKey, location.AccountID)
		original, exists := returns[location.BankKey]
		if !exists || locations[key] || !a.accounts[location.AccountID] {
			return a, mirrornativeResourceConflictV3()
		}
		locations[key] = true
		unowned, err := location.Available.Sub(ownedReturn[key])
		if err != nil {
			return a, mirrornativeResourceConflictV3()
		}
		held, err := location.Reserved.Sub(heldOwnedReturn[key])
		if err != nil {
			return a, mirrornativeResourceConflictV3()
		}
		free, err := unowned.Sub(held)
		if err != nil {
			return a, mirrornativeResourceConflictV3()
		}
		if free.IsZero() {
			continue
		}
		a.returns = append(a.returns, bankv2.ReturnUse{OperationKey: original.BankKey, PositionID: original.PositionID, AccountID: location.AccountID.String(), Amount: free})
		if err = mirrornativeResourceAddV3(returnTotal, location.AccountID, free); err != nil {
			return a, err
		}
	}
	for key := range ownedReturn {
		if !locations[key] {
			return a, mirrornativeResourceConflictV3()
		}
	}
	for account := range a.accounts {
		classified, err := incomeTotal[account].Add(returnTotal[account])
		if err != nil {
			return a, err
		}
		a.slack[account], err = a.free[account].Sub(classified)
		if err != nil {
			return a, mirrornativeResourceConflictV3()
		}
	}
	sort.Slice(a.income, func(i, j int) bool {
		x, y := a.income[i], a.income[j]
		if x.AccountID != y.AccountID {
			return x.AccountID.String() < y.AccountID.String()
		}
		if x.OriginID != y.OriginID {
			return x.OriginID.String() < y.OriginID.String()
		}
		return x.FragmentID.String() < y.FragmentID.String()
	})
	sort.Slice(a.returns, func(i, j int) bool {
		if a.returns[i].AccountID != a.returns[j].AccountID {
			return a.returns[i].AccountID < a.returns[j].AccountID
		}
		return a.returns[i].OperationKey < a.returns[j].OperationKey
	})
	return a, nil
}

// Frozen pure replay source: internal/app/native_goal_transfer.go.
func mirrorreplayGoalTransferPreviewV3(preview wireNativeGoalTransferPreview) error {
	if preview.Protocol != mirrorNativeGoalTransferProtocolV3 || preview.Kind != bankv2.MoveGoal || preview.BankAuthority || preview.PairedEffect.BankAuthority || preview.PairedEffect.CreatesIncome || preview.PairedInput.Policy.Scope != preview.Scope || preview.Request.PolicyVersionID != preview.PairedInput.Policy.VersionID || preview.Request.SourceGoalID != preview.PairedEffect.SourceGoalID || preview.Request.TargetGoalID != preview.PairedEffect.TargetGoalID || preview.Request.Amount != preview.PairedEffect.Amount || preview.Request.Timezone != preview.Timezone || preview.PairedInput.Timezone != preview.Timezone || !preview.PairedInput.Now.Equal(preview.CreatedAt) || preview.ForecastInput.Scope != preview.Scope || !preview.ForecastInput.AsOf.Equal(preview.CreatedAt) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	basis, err := nativeMirrorEncodeV3(struct {
		Dependencies      planning.DependencyVector
		Source, Inventory string
	}{preview.Dependencies, preview.SourceHash, preview.InventoryHash})
	if err != nil || preview.PairedInput.DependencyHash != nakedHash(basis) || preview.ForecastInput.InventoryHash != preview.InventoryHash || preview.ForecastInput.Facts.SourceHash != preview.SourceHash {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	hash, err := mirrorNativeGoalTransferReviewHashV3(preview)
	if err != nil || hash != preview.ReviewedHash {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	paired, err := planning.PreviewPairedGoalTransfer(preview.PairedInput)
	a, _ := nativeMirrorEncodeV3(paired)
	b, _ := nativeMirrorEncodeV3(preview.PairedEffect)
	if err != nil || string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	effect, err := mirrorgoalTransferEffectV3(preview.ID, paired.SourceGoalID, paired.TargetGoalID, paired.SourceAccountID, paired.TargetAccountID, paired.Amount, preview.LotUses, preview.ExpiresAt)
	if err != nil {
		return err
	}
	eh, err := effect.Digest()
	stored, e := preview.BankEffect.Digest()
	if err != nil || e != nil || eh != preview.EffectHash || stored != eh {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	before, err := planning.Forecast365(preview.ForecastInput)
	if err != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	afterInput, err := mirrorgoalTransferCounterfactualV3(preview.ForecastInput, paired.SourceGoalID, paired.TargetGoalID, paired.Amount)
	if err != nil {
		return err
	}
	after, err := planning.Forecast365(afterInput)
	if err != nil || !after.MaximumDeficit.IsZero() {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	decoded, err := preview.PairedInput.Policy.Compiled.Decode()
	if err != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	config, ok := decoded.(*policy.CrossGoalReallocationConfig)
	if !ok {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	refs, err := mirrorgoalTransferProtectedReferencesV3(preview.ForecastInput, afterInput, config.MustNotReducePolicyIDs)
	if err != nil {
		return err
	}
	a, _ = nativeMirrorEncodeV3(refs)
	b, _ = nativeMirrorEncodeV3(preview.PairedInput.ProtectedReferences)
	if string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	a, _ = nativeMirrorEncodeV3(before)
	b, _ = nativeMirrorEncodeV3(preview.ForecastBefore)
	if string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	a, _ = nativeMirrorEncodeV3(after)
	b, _ = nativeMirrorEncodeV3(preview.ForecastAfter)
	if string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	for _, point := range after.Points {
		margin, e := strconv.ParseInt(point.MarginCents, 10, 64)
		if e != nil || margin < 0 {
			return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
		}
	}
	return nil
}

// Frozen pure replay source: internal/app/native_assets_capture.go.
func mirrorreplayNativeAssetPreviewV3(preview wireNativeAssetPreview) error {
	ledger, err := preview.Ledger.DomainLedger()
	if err != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	if nakedHash(preview.InventoryOriginal) != preview.InventoryHash || ledger.Income.Snapshot().UserID != preview.Scope.UserID || !ledger.Income.Snapshot().AsOf.Equal(preview.CreatedAt) || len(preview.ForecastInput.FutureIncome) != 0 || len(preview.ForecastInput.PrincipalReturns) != 0 {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	d := preview.Dependencies
	snapshot := mirrortemplateSnapshotV3{Facts: preview.ForecastInput.Facts, Versions: preview.ForecastInput.Versions, Views: preview.Policies, Retained: preview.ForecastInput.RetainedObligations, Held: preview.ForecastInput.HeldCash, UnassignedGoalCash: preview.ForecastInput.UnassignedGoalCash, InventoryRaw: preview.InventoryOriginal, FundsVersion: d.FundsVersion, SourceVersion: d.SourceEvidenceVersion, WriterGeneration: d.WriterGeneration, ControlVersion: d.ControlVersion, AsOf: preview.CreatedAt, Timezone: preview.Timezone, Native: &ledger}
	snapshot.ExternalLiabilities = preview.ForecastInput.ExternalLiabilities
	expected, err := mirrorderiveNativeAssetPreviewV3(snapshot, preview.Request, preview.ID, preview.SessionID, preview.BankState, preview.Quote, preview.PendingPurchases, preview.ExpiresAt)
	if err != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	a, err := nativeMirrorEncodeV3(expected)
	if err != nil {
		return err
	}
	b, err := nativeMirrorEncodeV3(preview)
	if err != nil || string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	return nil
}

// Frozen pure replay source: internal/app/native_payments_preview.go.
func mirrorreplayNativePaymentPreviewV3(preview wireNativePaymentPreview) error {
	if preview.Protocol != "native-payment-preview-v1" || preview.Kind != bankv2.Payment || preview.BankAuthority || preview.Policy.GoalID != nil || preview.Policy.Version.Scope != preview.Scope || preview.Policy.Version.VersionID != preview.Request.PolicyVersionID || preview.SourceHash != preview.ForecastInput.Facts.SourceHash || preview.InventoryHash != preview.ForecastInput.InventoryHash || preview.ForecastInput.Scope != preview.Scope || !preview.ForecastInput.AsOf.Equal(preview.CreatedAt) || preview.ForecastInput.Timezone != preview.Timezone || preview.Request.Timezone != preview.Timezone {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	amount, e := mirrornativePaymentPreviewAmountV3(preview, preview.CreatedAt)
	if e != nil || amount != preview.Resources.Amount || len(preview.Resources.CashUses) != 1 || preview.Resources.CashUses[0].AccountID != preview.Request.SourceAccountID.String() || preview.Resources.CashUses[0].Amount != amount {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	ledger, e := preview.Ledger.DomainLedger()
	if e != nil || ledger.Income.Snapshot().UserID != preview.Scope.UserID || !ledger.Income.Snapshot().AsOf.Equal(preview.CreatedAt) || nakedHash(preview.InventoryOriginal) != preview.InventoryHash {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	selected, e := mirrorselectNativeUnownedCashV3(ledger, []funds.ID{preview.Request.SourceAccountID}, amount)
	a, _ := nativeMirrorEncodeV3(selected)
	b, _ := nativeMirrorEncodeV3(preview.Resources)
	if e != nil || !mirrortemplateSameBytesV3(a, b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	decoded, e := preview.Policy.Version.Compiled.Decode()
	if e != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	if config, ok := decoded.(*policy.PeriodicTransferConfig); ok && config.SourceAccountID != preview.Request.SourceAccountID {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	want := mirrornativePaymentEffectV3(preview.ID, preview.Resources, preview.Liability.Original, preview.ExpiresAt)
	left, e := nativeMirrorEncodeV3(want)
	right, e2 := nativeMirrorEncodeV3(preview.BankEffect)
	effect, e3 := want.Digest()
	if e != nil || e2 != nil || e3 != nil || !mirrortemplateSameBytesV3(left, right) || effect != preview.EffectHash {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	before, e := planning.Forecast365(preview.ForecastInput)
	if e != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	afterInput, e := mirrornativePaymentPreviewAfterV3(preview.ForecastInput, preview, amount)
	if e != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	after, e := planning.Forecast365(afterInput)
	if e != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	left, _ = nativeMirrorEncodeV3(before)
	right, _ = nativeMirrorEncodeV3(preview.ForecastBefore)
	if !mirrortemplateSameBytesV3(left, right) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	left, _ = nativeMirrorEncodeV3(after)
	right, _ = nativeMirrorEncodeV3(preview.ForecastAfter)
	if !mirrortemplateSameBytesV3(left, right) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	if e = mirrornativePaymentMarginsV3(before, after); e != nil {
		return e
	}
	hash, e := mirrorNativePaymentReviewHashV3(preview)
	if e != nil || hash != preview.ReviewedHash {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	return nil
}

// Frozen pure replay source: internal/app/native_automation_bridge.go.
func mirrorreplayStandingBridgeV3(g wirenativeStandingBridgeGroup, namespace string) error {
	if g.Protocol != mirrornativeStandingBridgeProtocolV3 || g.Scope != g.Grant.Scope || g.Scope != g.Occurrence.Scope || g.Grant.Terms.Family != policy.NativeJointGoals || g.Occurrence.Family != policy.NativeJointGoals || g.Occurrence.GrantID != g.Grant.ID || g.Occurrence.GrantVersion != g.Grant.Version || g.Grant.Terms.Validate() != nil || g.Grant.PrincipalKind != "USER" || g.Grant.SessionID == (funds.ID{}) || !g.Grant.Terms.EffectiveFrom.Equal(g.Grant.ConfirmedAt) || len(g.Steps) == 0 || len(g.Steps) > 16 || len(g.Result.Children) > 8 || !g.ExpiresAt.After(g.CreatedAt) || g.ExpiresAt.After(g.CreatedAt.Add(2*time.Minute)) || g.Dependencies.Validate() != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	if len(g.GoalVersions) != len(g.Grant.Terms.GoalIDs) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	for _, id := range g.Grant.Terms.GoalIDs {
		if g.GoalVersions[id.String()] == 0 {
			return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
		}
	}
	ledger, e := g.Candidate.Ledger.DomainLedger()
	if e != nil || ledger.Namespace != namespace || ledger.Income.Snapshot().UserID != g.Scope.UserID || !ledger.Income.Snapshot().AsOf.Equal(g.CreatedAt) || nakedHash(g.Candidate.InventoryOriginal) != g.Candidate.InventoryHash {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	f := g.Candidate.ForecastInput
	d := g.Dependencies
	snapshot := mirrortemplateSnapshotV3{Facts: f.Facts, Versions: f.Versions, Views: g.Candidate.Policies, Retained: f.RetainedObligations, Held: f.HeldCash, UnassignedGoalCash: f.UnassignedGoalCash, ExternalLiabilities: f.ExternalLiabilities, InventoryRaw: g.Candidate.InventoryOriginal, FundsVersion: d.FundsVersion, SourceVersion: d.SourceEvidenceVersion, WriterGeneration: d.WriterGeneration, ControlVersion: d.ControlVersion, AsOf: g.CreatedAt, Timezone: g.Grant.Terms.Timezone, Native: &ledger}
	candidate, input, result, e := mirrorstandingJointCalculationV3(snapshot, ledger, g.Grant, g.Occurrence.Source, g.Candidate.Usage, g.GoalVersions, g.CreatedAt)
	if e != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	for _, pair := range [][2]any{{candidate, g.Candidate}, {input, g.Input}, {result, g.Result}} {
		a, _ := nativeMirrorEncodeV3(pair[0])
		b, _ := nativeMirrorEncodeV3(pair[1])
		if string(a) != string(b) {
			return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
		}
	}
	commitment, e := mirrorstandingBridgeCommitmentV3(g)
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
				return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
			}
			step := g.Steps[number]
			number++
			if step.Number != number || step.ChildNumber != child.Number || step.GoalID != child.GoalID || step.InitialSource != (phaseIndex == 0) || ids[step.OperationID] || permits[step.Request.PermitID] || step.ConfirmationID != (funds.ID{}) {
				return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
			}
			ids[step.OperationID] = true
			permits[step.Request.PermitID] = true
			if number == 1 && step.PreviousOperationID != nil || number > 1 && (step.PreviousOperationID == nil || *step.PreviousOperationID != g.Steps[number-2].OperationID) {
				return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
			}
			account := child.DestinationAccountID
			kind := mirrornativeStandingBridgeAllocateV3
			operation := funds.AllocateGoal
			var destination *funds.ID
			if phase == bankv2.Transfer {
				account = child.SourceAccountID
				kind = mirrornativeStandingBridgeTransferV3
				operation = funds.TransferInternal
				v := child.DestinationAccountID
				destination = &v
			}
			use := child.Uses[0]
			use.AccountID = account
			use.FragmentID = funds.LocationID(use.OriginID, account)
			resources := wireNativeBridgeResources{IncomeUses: []funds.IncomeUse{use}, IncomeOperation: operation, DestinationAccountID: destination, CashAccountID: account, Amount: child.Amount}
			a, _ := nativeMirrorEncodeV3(resources)
			b, _ := nativeMirrorEncodeV3(step.Resources)
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
				return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
			}
		}
	}
	if number != len(g.Steps) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	return nil
}

// Frozen pure replay source: internal/app/native_automation_joint.go.
func mirrorreplayStandingJointAllocationV3(plan wirenativeStandingAllocationPlan, namespace string) error {
	j := plan.Joint
	if j == nil || j.Protocol != mirrorNativeAutomationProtocolV3+"/joint-capture" || plan.Grant.Terms.Family != policy.NativeJointGoals || j.GroupID != plan.Occurrence.ID || plan.JointChild < 1 || plan.JointChild > len(j.Result.Children) || len(j.Slots) != len(j.Result.Children) || len(j.Slots) > 8 || len(j.Slots) == 0 || len(j.GoalVersions) != len(plan.Grant.Terms.GoalIDs) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	slot := j.Slots[plan.JointChild-1]
	child := j.Result.Children[plan.JointChild-1]
	if slot.OperationID != plan.ID || slot.PermitID != plan.PermitID || child.Number != plan.JointChild {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	seen := map[funds.ID]bool{}
	for _, s := range j.Slots {
		if s.OperationID == (funds.ID{}) || s.PermitID == (funds.ID{}) || seen[s.OperationID] {
			return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
		}
		seen[s.OperationID] = true
	}
	ledger, e := plan.Candidate.Ledger.DomainLedger()
	if e != nil || ledger.Namespace != namespace || ledger.Income.Snapshot().UserID != plan.Scope.UserID || !ledger.Income.Snapshot().AsOf.Equal(plan.CreatedAt) || nakedHash(plan.Candidate.InventoryOriginal) != plan.Candidate.InventoryHash {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	f := plan.Candidate.ForecastInput
	d := plan.Dependencies
	snapshot := mirrortemplateSnapshotV3{Facts: f.Facts, Versions: f.Versions, Views: plan.Candidate.Policies, Retained: f.RetainedObligations, Held: f.HeldCash, UnassignedGoalCash: f.UnassignedGoalCash, InventoryRaw: plan.Candidate.InventoryOriginal, FundsVersion: d.FundsVersion, SourceVersion: d.SourceEvidenceVersion, WriterGeneration: d.WriterGeneration, ControlVersion: d.ControlVersion, AsOf: plan.CreatedAt, Timezone: plan.Grant.Terms.Timezone, Native: &ledger}
	snapshot.ExternalLiabilities = f.ExternalLiabilities
	expected, input, result, e := mirrorstandingJointCalculationV3(snapshot, ledger, plan.Grant, plan.Occurrence.Source, plan.Candidate.Usage, j.GoalVersions, plan.CreatedAt)
	if e != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	expected.Use = child.Uses[0]
	expected.GoalVersion = j.GoalVersions[child.GoalID.String()]
	a, _ := nativeMirrorEncodeV3(input)
	b, _ := nativeMirrorEncodeV3(j.Input)
	x, _ := nativeMirrorEncodeV3(result)
	y, _ := nativeMirrorEncodeV3(j.Result)
	c, _ := nativeMirrorEncodeV3(expected)
	v, _ := nativeMirrorEncodeV3(plan.Candidate)
	if string(a) != string(b) || string(x) != string(y) || string(c) != string(v) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	return nil
}

// Frozen pure replay source: internal/app/native_assets_resources.go.
func mirrorselectNativeUnownedCashV3(n nativeFrozenLedgerV3, accountIDs []funds.ID, cap funds.Money) (wireNativeCashResources, error) {
	var result wireNativeCashResources
	a, err := mirrornativeUnownedCashAvailabilityV3(n)
	if err != nil {
		return result, err
	}
	selected := map[funds.ID]bool{}
	if accountIDs == nil {
		for id := range a.accounts {
			selected[id] = true
		}
	} else {
		for _, id := range accountIDs {
			if !a.accounts[id] || selected[id] {
				return result, mirrornativeResourceConflictV3()
			}
			selected[id] = true
		}
	}
	remaining, cash := cap, map[funds.ID]funds.Money{}
	for _, use := range a.income {
		if !selected[use.AccountID] || remaining.IsZero() {
			continue
		}
		use.Amount = mirrornativeResourceMinimumV3(use.Amount, remaining)
		result.IncomeUses = append(result.IncomeUses, use)
		if err = mirrornativeResourceAddV3(cash, use.AccountID, use.Amount); err != nil {
			return wireNativeCashResources{}, err
		}
		remaining, _ = remaining.Sub(use.Amount)
	}
	for _, use := range a.returns {
		account, _ := funds.ParseID(use.AccountID)
		if !selected[account] || remaining.IsZero() {
			continue
		}
		use.Amount = mirrornativeResourceMinimumV3(use.Amount, remaining)
		result.ReturnUses = append(result.ReturnUses, use)
		if err = mirrornativeResourceAddV3(cash, account, use.Amount); err != nil {
			return wireNativeCashResources{}, err
		}
		remaining, _ = remaining.Sub(use.Amount)
	}
	ids := make([]funds.ID, 0, len(selected))
	for id := range selected {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i].String() < ids[j].String() })
	for _, id := range ids {
		amount := mirrornativeResourceMinimumV3(a.slack[id], remaining)
		if amount.IsZero() {
			continue
		}
		result.SlackUses = append(result.SlackUses, bankv2.CashUse{AccountID: id.String(), Amount: amount})
		if err = mirrornativeResourceAddV3(cash, id, amount); err != nil {
			return wireNativeCashResources{}, err
		}
		remaining, _ = remaining.Sub(amount)
	}
	for _, id := range ids {
		if !cash[id].IsZero() {
			result.CashUses = append(result.CashUses, bankv2.CashUse{AccountID: id.String(), Amount: cash[id]})
		}
	}
	if len(result.CashUses) > 100 || len(result.IncomeUses) > 100 || len(result.ReturnUses) > 100 {
		return wireNativeCashResources{}, nativeMirrorRejectV3("CAPACITY_EXCEEDED", nil)
	}
	result.Amount, _ = cap.Sub(remaining)
	return result, nil
}

// Frozen pure replay source: internal/app/native_assets_standing_sources.go.
func mirrorselectStandingAssetCashV3(n nativeFrozenLedgerV3, source wirenativeStandingAssetSource, cap funds.Money) (wireNativeCashResources, error) {
	var result wireNativeCashResources
	if err := source.validate(n, n.Income.Snapshot().AsOf); err != nil {
		return result, err
	}
	a, err := mirrornativeUnownedCashAvailabilityV3(n)
	if err != nil {
		return result, err
	}
	remaining, cash := cap, map[funds.ID]funds.Money{}
	if source.Family == policy.NativeAssetAllocation {
		for _, use := range a.income {
			if use.OriginID.String() != source.Source.ID || !source.account(use.AccountID) || remaining.IsZero() {
				continue
			}
			use.Amount = mirrornativeResourceMinimumV3(use.Amount, remaining)
			result.IncomeUses = append(result.IncomeUses, use)
			if err = mirrornativeResourceAddV3(cash, use.AccountID, use.Amount); err != nil {
				return result, err
			}
			remaining, _ = remaining.Sub(use.Amount)
		}
	} else {
		for _, use := range a.returns {
			account, e := funds.ParseID(use.AccountID)
			if e != nil {
				return result, e
			}
			if use.OperationKey != source.Source.ID || !source.account(account) || remaining.IsZero() {
				continue
			}
			use.Amount = mirrornativeResourceMinimumV3(use.Amount, remaining)
			result.ReturnUses = append(result.ReturnUses, use)
			if err = mirrornativeResourceAddV3(cash, account, use.Amount); err != nil {
				return result, err
			}
			remaining, _ = remaining.Sub(use.Amount)
		}
	}
	ids := make([]funds.ID, 0, len(cash))
	for id := range cash {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i].String() < ids[j].String() })
	for _, id := range ids {
		result.CashUses = append(result.CashUses, bankv2.CashUse{AccountID: id.String(), Amount: cash[id]})
	}
	result.Amount, _ = cap.Sub(remaining)
	return result, nil
}

// Frozen pure replay source: internal/app/native_automation_allocate.go.
func mirrorstandingAllocationCandidateV3(snapshot mirrortemplateSnapshotV3, ledger nativeFrozenLedgerV3, grant wireNativeStandingGrant, source policy.NativeOccurrenceSource, usage policy.NativeStandingUsage, goalVersion uint64, now time.Time) (wirenativeStandingAllocationCandidate, error) {
	var out wirenativeStandingAllocationCandidate
	if grant.Terms.Family != policy.NativeSingleGoal || len(grant.Terms.Policies) != 1 || len(grant.Terms.GoalIDs) != 1 {
		return out, nativeMirrorRejectV3("FAMILY_NOT_QUALIFIED", nil)
	}
	ref := grant.Terms.Policies[0]
	var view *wireTemplatePolicyView
	for i := range snapshot.Views {
		if snapshot.Views[i].Version.VersionID == ref.VersionID {
			view = &snapshot.Views[i]
		}
	}
	if view == nil || view.EffectiveState != "ACTIVE" || view.Version.PolicyID != ref.PolicyID || view.Version.Version != ref.Version || view.Version.Compiled.Hash != ref.ConfigurationHash || view.GoalID == nil || *view.GoalID != grant.Terms.GoalIDs[0] {
		return out, nativeMirrorRejectV3("NATIVE_GRANT_POLICY_CHANGED", nil)
	}
	goal := grant.Terms.GoalIDs[0]
	var account funds.ID
	for _, g := range ledger.Goals {
		if g.GoalID == goal {
			account = g.AccountID
		}
	}
	if account == (funds.ID{}) {
		return out, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	allowed := false
	for _, id := range grant.Terms.AccountIDs {
		if id == account {
			allowed = true
		}
	}
	if !allowed {
		return out, nativeMirrorRejectV3("NATIVE_GRANT_SCOPE", nil)
	}
	beforeInput := snapshot.forecastInput(365)
	before, e := planning.Forecast365(beforeInput)
	if e != nil {
		return out, nativeMirrorRejectV3("PROTECTION_INCOMPLETE", e)
	}
	if !before.MaximumDeficit.IsZero() {
		return out, nativeMirrorRejectV3("PROTECTION_DEFICIT", nil)
	}
	income := ledger.Income.Snapshot()
	var available funds.Money
	var fragment, origin funds.ID
	for _, o := range income.Origins {
		if o.ID().String() == source.ID {
			if o.Fact().EvidenceHash.String() != source.OriginalHash || !o.Fact().OccurredAt.Equal(source.OccurredAt) {
				return out, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
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
		return out, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	free, e := mirrornativeUnownedCashAvailabilityV3(ledger)
	if e != nil {
		return out, e
	}
	available = mirrornativeResourceMinimumV3(available, free.free[account])
	available = mirrornativeResourceMinimumV3(available, before.CurrentSafe)
	facts := snapshot.Facts
	facts.Income = []policy.TemplateIncomeFact{{ID: origin, Available: available, OccurredAt: source.OccurredAt, Verified: true}}
	facts.CurrentSafe, facts.CurrentSafeVerified = before.CurrentSafe, true
	preview, e := policy.PreviewTemplate(view.Version, facts, now, grant.Terms.Timezone)
	if e != nil {
		return out, nativeMirrorRejectV3("NATIVE_STANDING_PLANNING_INCOMPLETE", e)
	}
	amount := mirrornativeResourceMinimumV3(preview.Amount, available)
	amount = mirrornativeResourceMinimumV3(amount, grant.Terms.SingleCap)
	lifeUsed, e := funds.SumMoney(usage.LifetimeCommitted, usage.LifetimeReserved)
	if e != nil {
		return out, nativeMirrorRejectV3("NATIVE_STANDING_CAP_EXCEEDED", e)
	}
	life, e := grant.Terms.LifetimeCap.Sub(lifeUsed)
	if e != nil {
		return out, nativeMirrorRejectV3("NATIVE_STANDING_CAP_EXCEEDED", e)
	}
	monthUsed, e := funds.SumMoney(usage.MonthCommitted, usage.MonthReserved)
	if e != nil {
		return out, nativeMirrorRejectV3("NATIVE_STANDING_CAP_EXCEEDED", e)
	}
	month, e := grant.Terms.MonthlyCap.Sub(monthUsed)
	if e != nil {
		return out, nativeMirrorRejectV3("NATIVE_STANDING_CAP_EXCEEDED", e)
	}
	amount = mirrornativeResourceMinimumV3(amount, mirrornativeResourceMinimumV3(life, month))
	if amount.IsZero() {
		return out, nil
	}
	configuration, e := view.Version.Compiled.Decode()
	if e != nil {
		return out, nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	cfg, ok := configuration.(*policy.LongTermGoalConfig)
	if !ok {
		return out, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	if !cfg.AllowPartial && amount.Cents() < preview.Amount.Cents() {
		return out, nil
	}
	afterRaw, e := nativeMirrorEncodeV3(beforeInput)
	if e != nil {
		return out, e
	}
	var afterInput planning.ForecastInput
	if nativeCompactOriginalV3(afterRaw, &afterInput) != nil {
		return out, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
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
		return out, nativeMirrorRejectV3("PROTECTION_INCOMPLETE", e)
	}
	if !after.MaximumDeficit.IsZero() {
		return out, nativeMirrorRejectV3("PROTECTION_DEFICIT", nil)
	}
	authority := policy.NativeStandingCandidate{Scope: grant.Scope, Family: grant.Terms.Family, Source: source, Policies: grant.Terms.Policies, AccountIDs: []funds.ID{account}, GoalIDs: []funds.ID{goal}, Amount: amount}
	if e = policy.AuthorizeNativeStanding(grant.Terms, "ACTIVE", authority, usage, now); e != nil {
		return out, nativeMirrorRejectV3(e.Error(), e)
	}
	out = wirenativeStandingAllocationCandidate{Ledger: mirrorcaptureNativeAssetLedgerV3(ledger), Policies: snapshot.Views, InventoryOriginal: append(json.RawMessage(nil), snapshot.InventoryRaw...), Usage: usage, GoalVersion: goalVersion, Authority: authority, Use: funds.IncomeUse{FragmentID: fragment, OriginID: origin, AccountID: account, Amount: amount}, SourceHash: snapshot.Facts.SourceHash, InventoryHash: nakedHash(snapshot.InventoryRaw), PolicyPreview: preview, ForecastInput: beforeInput, ForecastBefore: before, ForecastAfterInput: afterInput, ForecastAfter: after}
	return out, nil
}

// Frozen pure replay source: internal/app/native_automation_worker.go.
func mirrorstandingAllocationExpectedRequestV3(plan wirenativeStandingAllocationPlan, namespace, planHash string) bankv2.Request {
	c := plan.Candidate
	ref := mirrorstandingAllocationReferenceV3(plan)
	amount := c.Use.Amount
	goal := mirrorstandingAllocationGoalV3(plan)
	businessKey := plan.Occurrence.Key
	if plan.Joint != nil {
		businessKey = fmt.Sprintf("%s:%d", businessKey, plan.JointChild)
	}
	return bankv2.Request{Protocol: bankv2.Protocol, Namespace: namespace, Key: plan.ID.String(), OperationID: plan.ID.String(), UserID: plan.Scope.UserID.String(), PermitID: plan.PermitID.String(), ReferenceHash: planHash, RequestedAt: plan.CreatedAt, ExpiresAt: plan.ExpiresAt, Effect: bankv2.Effect{Kind: bankv2.Allocate, BusinessKey: businessKey, Amount: amount, CashUses: []bankv2.CashUse{{AccountID: c.Use.AccountID.String(), Amount: amount}}, OriginUses: []bankv2.OriginUse{{OriginID: c.Use.OriginID.String(), AccountID: c.Use.AccountID.String(), Amount: amount, Bucket: "AVAILABLE"}}, ReturnUses: []bankv2.ReturnUse{}, DestinationGoalID: goal.String(), LatestArrivalAt: plan.ExpiresAt}, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: plan.Grant.ID.String(), Version: int64(plan.Grant.Version), GoalID: goal.String(), PolicyID: ref.PolicyID.String(), PolicyVersion: int64(ref.Version)}}}
}

// Frozen pure replay source: internal/app/native_automation_joint.go.
func mirrorstandingAllocationGoalV3(plan wirenativeStandingAllocationPlan) funds.ID {
	if plan.Joint != nil && plan.JointChild > 0 && plan.JointChild <= len(plan.Joint.Result.Children) {
		return plan.Joint.Result.Children[plan.JointChild-1].GoalID
	}
	return plan.Candidate.Authority.GoalIDs[0]
}

// Frozen pure replay source: internal/app/native_automation_joint.go.
func mirrorstandingAllocationReferenceV3(plan wirenativeStandingAllocationPlan) policy.NativePolicyReference {
	goal := mirrorstandingAllocationGoalV3(plan)
	for _, r := range plan.Grant.Terms.Policies {
		if r.GoalID != nil && *r.GoalID == goal {
			return r
		}
	}
	return policy.NativePolicyReference{}
}

// Frozen pure replay source: internal/app/native_assets_standing.go.
func mirrorstandingAssetAuthorityV3(grant wireNativeStandingGrant, source policy.NativeOccurrenceSource, preview wireNativeAssetPreview) (policy.NativeStandingCandidate, error) {
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

// Frozen pure replay source: internal/app/native_assets_standing.go.
func mirrorstandingAssetBudgetV3(grant wireNativeStandingGrant, usage policy.NativeStandingUsage) (funds.Money, error) {
	lifeUsed, err := funds.SumMoney(usage.LifetimeCommitted, usage.LifetimeReserved)
	if err != nil {
		return funds.Money{}, nativeMirrorRejectV3("NATIVE_STANDING_CAP_EXCEEDED", err)
	}
	monthUsed, err := funds.SumMoney(usage.MonthCommitted, usage.MonthReserved)
	if err != nil {
		return funds.Money{}, nativeMirrorRejectV3("NATIVE_STANDING_CAP_EXCEEDED", err)
	}
	life, err := grant.Terms.LifetimeCap.Sub(lifeUsed)
	if err != nil {
		return funds.Money{}, nativeMirrorRejectV3("NATIVE_STANDING_CAP_EXCEEDED", err)
	}
	month, err := grant.Terms.MonthlyCap.Sub(monthUsed)
	if err != nil {
		return funds.Money{}, nativeMirrorRejectV3("NATIVE_STANDING_CAP_EXCEEDED", err)
	}
	return mirrornativeResourceMinimumV3(grant.Terms.SingleCap, mirrornativeResourceMinimumV3(life, month)), nil
}

// Frozen pure replay source: internal/app/native_assets_standing.go.
func mirrorstandingAssetCandidateV3(snapshot mirrortemplateSnapshotV3, grant wireNativeStandingGrant, source wirenativeStandingAssetSource, usage policy.NativeStandingUsage, id funds.ID, state bankv2.CurrentState, pending []bankv2.Request) (wireNativeAssetPreview, policy.NativeStandingCandidate, error) {
	var zero wireNativeAssetPreview
	var noAuthority policy.NativeStandingCandidate
	if grant.Terms.Validate() != nil || source.Family != grant.Terms.Family || source.Source.Validate(source.Family) != nil || snapshot.Native == nil {
		return zero, noAuthority, nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	cap, err := mirrorstandingAssetBudgetV3(grant, usage)
	if err != nil || cap.IsZero() {
		return zero, noAuthority, err
	}
	for _, reference := range grant.Terms.Policies {
		if reference.Template != policy.AssetAuthorizationTemplate {
			continue
		}
		for _, product := range grant.Terms.ProductIDs {
			preview, e := mirrorderiveNativeAssetPreviewWithSourceV3(snapshot, wireNativeAssetRequest{Kind: bankv2.Purchase, PolicyVersionID: reference.VersionID, ProductID: product, Amount: cap, Timezone: grant.Terms.Timezone}, id, funds.ID{}, state, nil, pending, &source, grant.Terms.ExpiresAt)
			if e != nil {
				switch nativeMirrorCodeV3(e) {
				case "ASSET_PRODUCT_UNAVAILABLE", "ASSET_POLICY_REJECTED", "ASSET_NO_SAFE_PURCHASE", "ASSET_BELOW_MINIMUM", "ASSET_PROTECTION_DEFICIT", "ASSET_EXIT_AFTER_USE":
					continue
				default:
					return zero, noAuthority, e
				}
			}
			if preview.Policy.Version.PolicyID != reference.PolicyID || preview.Policy.Version.Version != reference.Version || preview.Policy.Version.Compiled.Hash != reference.ConfigurationHash {
				return zero, noAuthority, nativeMirrorRejectV3("NATIVE_GRANT_POLICY_CHANGED", nil)
			}
			preview.Dependencies.GrantVersion = grant.Version
			preview.ReviewedHash, e = mirrorNativeAssetReviewHashV3(preview)
			if e != nil {
				return zero, noAuthority, e
			}
			authority, e := mirrorstandingAssetAuthorityV3(grant, source.Source, preview)
			if e != nil {
				return zero, noAuthority, e
			}
			if e = policy.AuthorizeNativeStanding(grant.Terms, "ACTIVE", authority, usage, snapshot.AsOf); e != nil {
				return zero, noAuthority, nativeMirrorRejectV3(e.Error(), e)
			}
			return preview, authority, nil
		}
	}
	return zero, noAuthority, nil
}

// Frozen pure replay source: internal/app/native_assets_standing_sources.go.
func mirrorstandingAssetGoalAmountV3(n nativeFrozenLedgerV3, source wirenativeStandingAssetSource, goal funds.ID) (funds.Money, error) {
	if err := source.validate(n, n.Income.Snapshot().AsOf); err != nil {
		return funds.Money{}, err
	}
	var total funds.Money
	for _, lot := range n.GoalLots {
		if lot.GoalID != goal || !source.lot(lot) {
			continue
		}
		free, err := lot.Available.Sub(lot.Reserved)
		if err != nil {
			return funds.Money{}, mirrornativeResourceConflictV3()
		}
		if source.Family == policy.NativeAssetAllocation {
			var assigned, held funds.Money
			fragment := funds.LocationID(func() funds.ID { id, _ := funds.ParseID(source.Source.ID); return id }(), lot.AccountID)
			for _, l := range n.Income.Snapshot().Locations {
				if l.FragmentID == fragment {
					assigned = l.Assigned
				}
			}
			for _, h := range n.Holds {
				if h.State == funds.Reserved && h.ResourceKind == "ASSIGNED" && h.ResourceID == fragment.String() {
					held, err = held.Add(h.Amount)
					if err != nil {
						return funds.Money{}, err
					}
				}
			}
			available, e := assigned.Sub(held)
			if e != nil {
				return funds.Money{}, mirrornativeResourceConflictV3()
			}
			free = mirrornativeResourceMinimumV3(free, available)
		} else {
			found := false
			for _, l := range n.ReturnLocations {
				if l.BankKey == lot.SourceID && l.AccountID == lot.AccountID {
					available, e := l.Available.Sub(l.Reserved)
					if e != nil {
						return funds.Money{}, mirrornativeResourceConflictV3()
					}
					free, found = mirrornativeResourceMinimumV3(free, available), true
				}
			}
			if !found {
				return funds.Money{}, mirrornativeResourceConflictV3()
			}
		}
		total, err = total.Add(free)
		if err != nil {
			return funds.Money{}, err
		}
	}
	return total, nil
}

// Frozen pure replay source: internal/app/native_assets_standing_worker.go.
func mirrorstandingAssetReplayCurrentV3(plan wirenativeStandingAssetPlan, current wireNativeAssetPreview) error {
	if current.SessionID != (funds.ID{}) || current.ID != plan.ID || current.Kind != bankv2.Purchase || current.Quote != nil || current.Dependencies.GrantVersion != plan.Grant.Version || current.CreatedAt.Before(plan.Preview.CreatedAt) || !current.CreatedAt.Before(plan.Preview.ExpiresAt) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	ledger, err := current.Ledger.DomainLedger()
	if err != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	d := current.Dependencies
	snapshot := mirrortemplateSnapshotV3{Facts: current.ForecastInput.Facts, Versions: current.ForecastInput.Versions, Views: current.Policies, Retained: current.ForecastInput.RetainedObligations, Held: current.ForecastInput.HeldCash, UnassignedGoalCash: current.ForecastInput.UnassignedGoalCash, InventoryRaw: current.InventoryOriginal, FundsVersion: d.FundsVersion, SourceVersion: d.SourceEvidenceVersion, WriterGeneration: d.WriterGeneration, ControlVersion: d.ControlVersion, AsOf: current.CreatedAt, Timezone: current.Timezone, Native: &ledger}
	snapshot.ExternalLiabilities = current.ForecastInput.ExternalLiabilities
	expected, err := mirrorderiveNativeAssetPreviewWithSourceV3(snapshot, plan.Preview.Request, plan.ID, funds.ID{}, current.BankState, nil, current.PendingPurchases, &plan.Source, plan.Preview.ExpiresAt)
	if err != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", err)
	}
	expected.Dependencies.GrantVersion = plan.Grant.Version
	expected.ReviewedHash, err = mirrorNativeAssetReviewHashV3(expected)
	if err != nil {
		return err
	}
	a, _ := nativeMirrorEncodeV3(expected)
	b, _ := nativeMirrorEncodeV3(current)
	if string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", fmt.Errorf("standing asset current capture replay differs"))
	}
	return mirrorassetEquivalentEconomicV3(current, plan.Preview)
}

// Frozen pure replay source: internal/app/native_assets_standing.go.
func mirrorstandingAssetRequestV3(plan wirenativeStandingAssetPlan, namespace, hash string) bankv2.Request {
	p := plan.Preview
	return bankv2.Request{Protocol: bankv2.Protocol, Namespace: namespace, Key: plan.ID.String(), OperationID: plan.ID.String(), UserID: plan.Scope.UserID.String(), PermitID: plan.PermitID.String(), ReferenceHash: hash, RequestedAt: p.CreatedAt, ExpiresAt: p.ExpiresAt, Effect: p.BankEffect, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: plan.Grant.ID.String(), Version: int64(plan.Grant.Version), GoalID: p.BankEffect.SourceGoalID, PolicyID: p.Policy.Version.PolicyID.String(), PolicyVersion: int64(p.Policy.Version.Version)}}}
}

// Frozen pure replay source: internal/app/native_automation_bridge.go.
func mirrorstandingBridgeCommitmentV3(g wirenativeStandingBridgeGroup) (string, error) {
	g.Steps = append([]wireNativeBridgeStep(nil), g.Steps...)
	for i := range g.Steps {
		g.Steps[i].Request.ReferenceHash = ""
		g.Steps[i].RequestHash = ""
	}
	raw, e := nativeMirrorEncodeV3(g)
	return nakedHash(raw), e
}

// Frozen pure replay source: internal/app/native_automation_joint.go.
func mirrorstandingJointBudgetV3(grant wireNativeStandingGrant, usage policy.NativeStandingUsage) (funds.Money, error) {
	life, e := funds.SumMoney(usage.LifetimeCommitted, usage.LifetimeReserved)
	month, e2 := funds.SumMoney(usage.MonthCommitted, usage.MonthReserved)
	if e != nil || e2 != nil {
		return funds.Money{}, nativeMirrorRejectV3("NATIVE_STANDING_CAP_EXCEEDED", nil)
	}
	life, e = grant.Terms.LifetimeCap.Sub(life)
	month, e2 = grant.Terms.MonthlyCap.Sub(month)
	if e != nil || e2 != nil {
		return funds.Money{}, nativeMirrorRejectV3("NATIVE_STANDING_CAP_EXCEEDED", nil)
	}
	return mirrornativeResourceMinimumV3(grant.Terms.SingleCap, mirrornativeResourceMinimumV3(life, month)), nil
}

// Frozen pure replay source: internal/app/native_automation_joint.go.
func mirrorstandingJointCalculationV3(snapshot mirrortemplateSnapshotV3, ledger nativeFrozenLedgerV3, grant wireNativeStandingGrant, source policy.NativeOccurrenceSource, usage policy.NativeStandingUsage, versions map[string]uint64, now time.Time) (wirenativeStandingAllocationCandidate, wireNativeJointInput, planning.JointPlan, error) {
	var out wirenativeStandingAllocationCandidate
	var input wireNativeJointInput
	var result planning.JointPlan
	fail := func(code string, e error) (wirenativeStandingAllocationCandidate, wireNativeJointInput, planning.JointPlan, error) {
		return out, input, result, nativeMirrorRejectV3(code, e)
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
	cap, e := mirrorstandingJointBudgetV3(grant, usage)
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
	in := planning.JointInput{Scope: grant.Scope, Now: now, Timezone: grant.Terms.Timezone, Ledger: eligible, Cash: cash, Goals: goals, ProtectionInput: &forecast, Complete: true, InventoryHash: nakedHash(snapshot.InventoryRaw), Dependencies: deps, SolverNodeBudget: 200000, BudgetCap: &cap}
	input, e = mirrorcaptureNativeJointInputV3(in, forecast)
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
	afterRaw, _ := nativeMirrorEncodeV3(forecast)
	var afterInput planning.ForecastInput
	if nativeCompactOriginalV3(afterRaw, &afterInput) != nil {
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
	out = wirenativeStandingAllocationCandidate{Ledger: mirrorcaptureNativeAssetLedgerV3(ledger), Policies: snapshot.Views, InventoryOriginal: append(json.RawMessage(nil), snapshot.InventoryRaw...), Usage: usage, Authority: authority, SourceHash: snapshot.Facts.SourceHash, InventoryHash: nakedHash(snapshot.InventoryRaw), ForecastInput: forecast, ForecastBefore: before, ForecastAfterInput: afterInput, ForecastAfter: after}
	return out, input, result, nil
}

// Frozen pure replay source: internal/app/native_payments_standing.go.
func mirrorstandingPaymentAuthorityV3(grant wireNativeStandingGrant, source policy.NativeOccurrenceSource, amount funds.Money) policy.NativeStandingCandidate {
	return policy.NativeStandingCandidate{Scope: grant.Scope, Family: policy.NativePeriodicPayment, Source: source, Policies: grant.Terms.Policies, AccountIDs: grant.Terms.AccountIDs, GoalIDs: []funds.ID{}, PayeeID: grant.Terms.PayeeIDs[0], Amount: amount}
}

// Frozen pure replay source: internal/app/native_payments_standing.go.
func mirrorstandingPaymentSourceV3(version policy.TemplateVersion, debtID, due string, bank bankv2.LiabilityState, timezone string) (policy.NativeOccurrenceSource, error) {
	var out policy.NativeOccurrenceSource
	cfg, e := version.Compiled.Decode()
	if e != nil {
		return out, e
	}
	prepare := 0
	switch v := cfg.(type) {
	case *policy.RecurringObligationConfig:
		if !v.AutoExecute {
			return out, nativeMirrorRejectV3("PAYMENT_AUTO_EXECUTE_DISABLED", nil)
		}
		prepare = v.PrepareDaysBefore
	case *policy.PeriodicTransferConfig:
		prepare = v.PrepareDaysBefore
	default:
		return out, nativeMirrorRejectV3("PAYMENT_RULE_INVALID", nil)
	}
	date, e := policy.TemplateDate(due)
	if e != nil || len(due) < 7 || bank.Original.ID != debtID || (bank.Original.Kind == "OCCURRENCE" && bank.Original.Period != due[:7]) || (bank.Original.Kind != "OCCURRENCE" && bank.Original.Kind != "BILL") {
		return out, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", e)
	}
	zone, e := policy.TemplateLocation(timezone)
	if e != nil {
		return out, e
	}
	at := time.Date(date.Year(), date.Month(), date.Day(), 0, 0, 0, 0, zone)
	out = policy.NativeOccurrenceSource{Kind: "PAYMENT_OCCURRENCE", ID: debtID, OriginalHash: bank.OriginalHash, Period: due[:7], OccurredAt: at.UTC(), AvailableAt: at.AddDate(0, 0, -prepare).UTC()}
	if out.Validate(policy.NativePeriodicPayment) != nil {
		return out, nativeMirrorRejectV3("PAYMENT_LIABILITY_INVALID", nil)
	}
	return out, nil
}

// Frozen pure replay source: internal/app/forecast.go.
func mirrortemplateSameBytesV3(a, b []byte) bool { return bytes.Equal(a, b) }

// Frozen pure replay source: internal/adapters/postgres/native_ledger.go.
func mirrorvalidNativeStateV3(s funds.ReservationState) bool {
	return s == funds.Reserved || s == funds.Committed || s == funds.Released
}

// Frozen pure replay source: internal/app/native_payments_worker.go.
func mirrorvalidatePaymentProofV3(preview wireNativePaymentPreview, proof wirenativePaymentSubmissionProof, request bankv2.Request) error {
	d, c := preview.Dependencies, proof.Current
	if proof.Protocol != mirrornativePaymentPermitProtocolV3 || proof.PreviewID != preview.ID || proof.OperationID.String() != request.OperationID || proof.PermitID != request.PermitID || request.ReferenceHash != preview.ReviewedHash || proof.ReservedFundsVersion != d.FundsVersion+1 || c.Dependencies.FundsVersion != proof.ReservedFundsVersion || c.Dependencies.SourceEvidenceVersion != d.SourceEvidenceVersion || c.Dependencies.WriterGeneration != d.WriterGeneration || c.Dependencies.ControlVersion != d.ControlVersion || c.InventoryHash != preview.InventoryHash || c.ID != preview.ID || c.Scope != preview.Scope || c.Request != preview.Request || c.Liability != preview.Liability || c.Policy.Version.VersionID != preview.Policy.Version.VersionID || c.EffectHash != preview.EffectHash || !c.CreatedAt.Equal(proof.DecisionAt) || !c.ExpiresAt.Equal(preview.ExpiresAt) || proof.DecisionAt.Before(proof.Approval.ConfirmedAt) || !proof.DecisionAt.Before(preview.ExpiresAt) || proof.BankCapturedAt.After(proof.DecisionAt.Add(time.Second)) || proof.DecisionAt.Sub(proof.BankCapturedAt) > 30*time.Second {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	if e := mirrorreplayNativePaymentPreviewV3(c); e != nil {
		return e
	}
	a, _ := nativeMirrorEncodeV3(c.BankEffect)
	b, _ := nativeMirrorEncodeV3(request.Effect)
	if !mirrortemplateSameBytesV3(a, b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	return nil
}

// Frozen pure replay source: internal/app/native_payments_standing_worker.go.
func mirrorvalidateStandingPaymentProofV3(plan wirenativeStandingPaymentPlan, hash string, proof wirenativeStandingPaymentProof, request bankv2.Request) error {
	if e := mirrorvalidateStandingPaymentRequestV3(plan, hash, request); e != nil {
		return e
	}
	a, _ := nativeMirrorEncodeV3(plan.Grant)
	b, _ := nativeMirrorEncodeV3(proof.Grant)
	d, c := plan.Preview.Dependencies, proof.Current
	if proof.Protocol != mirrornativeStandingPaymentPermitProtocolV3 || proof.OperationID != plan.ID || proof.PlanHash != hash || proof.PermitID != plan.PermitID || string(a) != string(b) || proof.GrantStateSequence < plan.GrantStateSequence || proof.DecisionAt.Before(plan.Preview.CreatedAt) || !proof.DecisionAt.Before(plan.Preview.ExpiresAt) || proof.BankCapturedAt.After(proof.DecisionAt.Add(time.Second)) || proof.DecisionAt.Sub(proof.BankCapturedAt) > 30*time.Second || !c.CreatedAt.Equal(proof.DecisionAt) || !c.ExpiresAt.Equal(plan.Preview.ExpiresAt) || c.ID != plan.Preview.ID || c.Request != plan.Preview.Request || c.Scope != plan.Scope || c.SessionID != (funds.ID{}) || c.Liability != plan.Preview.Liability || c.EffectHash != plan.Preview.EffectHash || c.InventoryHash != plan.Preview.InventoryHash || c.Dependencies.FundsVersion != d.FundsVersion+1 || c.Dependencies.SourceEvidenceVersion != d.SourceEvidenceVersion || c.Dependencies.WriterGeneration != d.WriterGeneration || c.Dependencies.ControlVersion != d.ControlVersion || c.Dependencies.GrantVersion != plan.Grant.Version {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	if e := mirrorreplayNativePaymentPreviewV3(c); e != nil {
		return e
	}
	a, _ = nativeMirrorEncodeV3(c.BankEffect)
	b, _ = nativeMirrorEncodeV3(request.Effect)
	if string(a) != string(b) {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	if e := policy.AuthorizeNativeStanding(plan.Grant.Terms, "ACTIVE", plan.Authority, proof.Usage, proof.DecisionAt); e != nil {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", e)
	}
	return nil
}

// Frozen pure replay source: internal/app/native_payments_standing_worker.go.
func mirrorvalidateStandingPaymentRequestV3(plan wirenativeStandingPaymentPlan, hash string, request bankv2.Request) error {
	expected := bankv2.Request{Protocol: bankv2.Protocol, Namespace: request.Namespace, Key: plan.ID.String(), OperationID: plan.ID.String(), UserID: plan.Scope.UserID.String(), PermitID: plan.PermitID.String(), ReferenceHash: hash, RequestedAt: plan.Preview.CreatedAt, ExpiresAt: plan.Preview.ExpiresAt, Effect: plan.Preview.BankEffect, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: plan.Grant.ID.String(), Version: int64(plan.Grant.Version), PolicyID: plan.Grant.Terms.Policies[0].PolicyID.String(), PolicyVersion: int64(plan.Grant.Terms.Policies[0].Version)}}}
	a, e := request.Digest()
	b, e2 := expected.Digest()
	if e != nil || e2 != nil || a != b {
		return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
	}
	return nil
}

// Frozen pure replay source: internal/app/native_assets_capture.go.
func (c wireNativeAssetLedgerCapture) DomainLedger() (nativeFrozenLedgerV3, error) {
	origins := make([]funds.IncomeOrigin, len(c.Income.OriginFacts))
	for i, fact := range c.Income.OriginFacts {
		o, e := funds.NewIncomeOrigin(fact)
		if e != nil {
			return nativeFrozenLedgerV3{}, e
		}
		origins[i] = o
	}
	income, e := funds.NewIncomeLedger(funds.LedgerSnapshot{UserID: c.Income.UserID, Currency: c.Income.Currency, AsOf: c.Income.AsOf, ScopeAccountIDs: c.Income.ScopeAccountIDs, Origins: origins, Locations: c.Income.Locations, Reservations: c.Income.Reservations})
	if e != nil {
		return nativeFrozenLedgerV3{}, e
	}
	return mirrorcloneNativeAssetLedgerV3(nativeFrozenLedgerV3{Namespace: c.Namespace, Income: income, Accounts: c.Accounts, Goals: c.Goals, GoalLots: c.GoalLots, Holds: c.Holds, Positions: c.Positions, PrincipalReturns: c.PrincipalReturns, ReturnLocations: c.ReturnLocations, ReturnClaims: c.ReturnClaims}), nil
}

// Frozen pure replay source: internal/app/joint_plan.go.
func (input wireNativeJointInput) DomainInput() (planning.JointInput, error) {
	var in planning.JointInput
	var core map[string]json.RawMessage
	if nativeCompactOriginalV3(input.Planner, &core) != nil || core == nil {
		return in, nativeMirrorRejectV3("JOINT_INPUT_INVALID", nil)
	}
	for key := range core {
		if key == "Ledger" || key == "ledger" {
			return in, nativeMirrorRejectV3("JOINT_INPUT_INVALID", nil)
		}
	}

	decoder := json.NewDecoder(bytes.NewReader(input.Planner))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&in); err != nil {
		return in, nativeMirrorRejectV3("JOINT_INPUT_INVALID", err)
	}
	origins := make([]funds.IncomeOrigin, len(input.Ledger.OriginFacts))
	for i, fact := range input.Ledger.OriginFacts {
		origin, err := funds.NewIncomeOrigin(fact)
		if err != nil {
			return in, nativeMirrorRejectV3("JOINT_INPUT_INVALID", err)
		}
		origins[i] = origin
	}
	ledger, err := funds.NewIncomeLedger(funds.LedgerSnapshot{UserID: input.Ledger.UserID, Currency: input.Ledger.Currency, AsOf: input.Ledger.AsOf, ScopeAccountIDs: input.Ledger.ScopeAccountIDs, Origins: origins, Locations: input.Ledger.Locations, Reservations: input.Ledger.Reservations})
	if err != nil || input.Ledger.UserID != in.Scope.UserID || !input.Ledger.AsOf.Equal(in.Now) {
		return in, nativeMirrorRejectV3("JOINT_INPUT_INVALID", err)
	}
	in.Ledger = ledger
	return in, nil
}

// Frozen pure replay source: internal/adapters/postgres/native_ledger.go.
func (n nativeFrozenLedgerV3) validate(user funds.ID) error {
	if n.Namespace == "" || n.Income.Validate() != nil || n.Income.Snapshot().UserID != user {
		return errNativeFrozenLedgerV3
	}
	snapshot := n.Income.Snapshot()
	accounts := map[funds.ID]wireNativeAccount{}
	for _, a := range n.Accounts {
		if a.ID == (funds.ID{}) || a.ObservedAt.IsZero() || a.GoalOwned.Cents() > a.Balance.Cents() {
			return errNativeFrozenLedgerV3
		}
		if _, ok := accounts[a.ID]; ok {
			return errNativeFrozenLedgerV3
		}
		accounts[a.ID] = a
	}
	if len(accounts) != len(snapshot.ScopeAccountIDs) {
		return errNativeFrozenLedgerV3
	}
	for _, id := range snapshot.ScopeAccountIDs {
		if _, ok := accounts[id]; !ok {
			return errNativeFrozenLedgerV3
		}
	}
	goals := map[funds.ID]wireNativeGoalBalance{}
	owned := map[string]funds.Money{}
	lotTotals := map[string]funds.Money{}
	principalTotals := map[string]funds.Money{}
	for _, g := range n.Goals {
		if g.GoalID == (funds.ID{}) {
			return errNativeFrozenLedgerV3
		}
		if _, ok := goals[g.GoalID]; ok {
			return errNativeFrozenLedgerV3
		}
		if _, ok := accounts[g.AccountID]; !ok {
			return errNativeFrozenLedgerV3
		}
		if _, err := g.Cash.Add(g.Principal); err != nil {
			return err
		}
		goals[g.GoalID] = g
		if err := mirrornativeAddV3(owned, g.AccountID.String(), g.Cash); err != nil {
			return err
		}
	}
	for _, a := range n.Accounts {
		if owned[a.ID.String()] != a.GoalOwned {
			return errNativeFrozenLedgerV3
		}
	}
	origins := map[string]bool{}
	for _, o := range snapshot.Origins {
		origins[o.ID().String()] = true
	}
	returns := map[string]wireNativePrincipalReturn{}
	positions := map[string]wireNativePosition{}
	for _, p := range n.Positions {
		var original bankv2.PositionOpening
		if json.Unmarshal(p.Original, &original) != nil || original.Validate() != nil {
			return errNativeFrozenLedgerV3
		}
		h, err := original.Digest()
		if err != nil {
			return err
		}
		if h != p.OriginalHash || p.ID != original.PositionID || p.BankKey != original.OpeningKey || original.Namespace != n.Namespace || original.UserID != user.String() || p.Principal != original.Principal || p.ReturnAccountID.String() != original.ReturnAccountID || !p.PurchasedAt.Equal(original.PurchasedAt) || (p.MaturityAt == nil) != original.MaturityAt.IsZero() || p.MaturityAt != nil && !p.MaturityAt.Equal(original.MaturityAt) || (p.GoalID == nil) != (original.GoalID == "") || p.GoalID != nil && p.GoalID.String() != original.GoalID || p.Closed && p.ClosingOperationID == nil {
			return errNativeFrozenLedgerV3
		}
		if (p.OpeningOperationID == nil) != (original.SourceKind == "TRUSTED_HISTORICAL_IMPORT") {
			return errNativeFrozenLedgerV3
		}
		if _, ok := positions[p.ID]; ok {
			return errNativeFrozenLedgerV3
		}
		positions[p.ID] = p
		if _, ok := accounts[p.ReturnAccountID]; !ok {
			return errNativeFrozenLedgerV3
		}
		if p.GoalID != nil {
			if _, ok := goals[*p.GoalID]; !ok {
				return errNativeFrozenLedgerV3
			}
			if !p.Closed {
				if err := mirrornativeAddV3(principalTotals, p.GoalID.String(), p.Principal); err != nil {
					return err
				}
			}
		}
	}
	for _, r := range n.PrincipalReturns {
		var original bankv2.PrincipalReturn
		if json.Unmarshal(r.Original, &original) != nil || original.Role != "PRINCIPAL_RETURN" || nakedHash(r.Original) != r.OriginalHash || r.BankKey != original.OperationKey || r.PositionID != original.PositionID || r.Amount != original.Amount || !r.OccurredAt.Equal(original.OccurredAt) {
			return errNativeFrozenLedgerV3
		}
		p, ok := positions[r.PositionID]
		if !ok || !p.Closed || p.ClosingOperationID == nil || *p.ClosingOperationID != r.OperationID || p.OriginalHash != original.PositionOpeningHash || p.ReturnAccountID.String() != original.AccountID {
			return errNativeFrozenLedgerV3
		}
		if _, ok := returns[r.BankKey]; ok {
			return errNativeFrozenLedgerV3
		}
		returns[r.BankKey] = r
	}
	closedReturns := map[string]bool{}
	lossTotals := map[string]funds.Money{}
	for _, r := range n.PrincipalReturns {
		p := positions[r.PositionID]
		if closedReturns[p.ID] {
			return errNativeFrozenLedgerV3
		}
		closedReturns[p.ID] = true
		loss, err := p.Principal.Sub(r.Amount)
		if err != nil {
			return errNativeFrozenLedgerV3
		}
		if p.GoalID != nil {
			if err = mirrornativeAddV3(lossTotals, p.GoalID.String(), loss); err != nil {
				return err
			}
		}
	}
	for _, p := range n.Positions {
		if p.Closed && !closedReturns[p.ID] {
			return errNativeFrozenLedgerV3
		}
	}
	lotSeen := map[string]bool{}
	for _, l := range n.GoalLots {
		g, ok := goals[l.GoalID]
		if !ok || g.AccountID != l.AccountID || l.Reserved.Cents() > l.Available.Cents() {
			return errNativeFrozenLedgerV3
		}
		if l.SourceKind == "INCOME" {
			if !origins[l.SourceID] {
				return errNativeFrozenLedgerV3
			}
		} else if l.SourceKind == "PRINCIPAL_RETURN" {
			if _, ok := returns[l.SourceID]; !ok {
				return errNativeFrozenLedgerV3
			}
		} else {
			return errNativeFrozenLedgerV3
		}
		key := mirrornativeLotKeyV3(l.GoalID, l.AccountID, l.SourceKind, l.SourceID)
		if lotSeen[key] {
			return errNativeFrozenLedgerV3
		}
		lotSeen[key] = true
		if err := mirrornativeAddV3(lotTotals, l.GoalID.String(), l.Available); err != nil {
			return err
		}
	}
	for _, g := range n.Goals {
		if lotTotals[g.GoalID.String()] != g.Cash || principalTotals[g.GoalID.String()] != g.Principal || lossTotals[g.GoalID.String()] != g.Loss {
			return errNativeFrozenLedgerV3
		}
	}
	locationTotals := map[string]funds.Money{}
	claimTotals := map[string]funds.Money{}
	locationSeen := map[string]bool{}
	claimSeen := map[string]bool{}
	for _, c := range n.ReturnClaims {
		key := c.BankKey + ":" + c.AccountID.String()
		identity := c.OperationID.String() + ":" + key
		if claimSeen[identity] || c.Amount.IsZero() || !mirrorvalidNativeStateV3(c.State) {
			return errNativeFrozenLedgerV3
		}
		claimSeen[identity] = true
		if c.State == funds.Reserved {
			if err := mirrornativeAddV3(claimTotals, key, c.Amount); err != nil {
				return err
			}
		}
	}
	for _, l := range n.ReturnLocations {
		key := l.BankKey + ":" + l.AccountID.String()
		if locationSeen[key] || l.Reserved.Cents() > l.Available.Cents() || claimTotals[key] != l.Reserved {
			return errNativeFrozenLedgerV3
		}
		locationSeen[key] = true
		if _, ok := returns[l.BankKey]; !ok {
			return errNativeFrozenLedgerV3
		}
		if _, ok := accounts[l.AccountID]; !ok {
			return errNativeFrozenLedgerV3
		}
		if err := mirrornativeAddV3(locationTotals, l.BankKey, l.Available); err != nil {
			return err
		}
	}
	for _, c := range n.ReturnClaims {
		if !locationSeen[c.BankKey+":"+c.AccountID.String()] {
			return errNativeFrozenLedgerV3
		}
	}
	for key, total := range locationTotals {
		if total.Cents() > returns[key].Amount.Cents() {
			return errNativeFrozenLedgerV3
		}
	}
	holdSeen := map[string]bool{}
	fragments := map[string]bool{}
	for _, l := range snapshot.Locations {
		fragments[l.FragmentID.String()] = true
	}
	for _, h := range n.Holds {
		key := h.OperationID.String() + ":" + h.ResourceKind + ":" + h.ResourceID + ":" + h.ResourceDetail
		if holdSeen[key] || h.Amount.IsZero() || !mirrorvalidNativeStateV3(h.State) {
			return errNativeFrozenLedgerV3
		}
		holdSeen[key] = true
		switch h.ResourceKind {
		case "CASH", "INCOMING_CASH":
			id, err := funds.ParseID(h.ResourceID)
			if err != nil {
				return errNativeFrozenLedgerV3
			}
			if _, ok := accounts[id]; !ok {
				return errNativeFrozenLedgerV3
			}
		case "GOAL", "INCOMING_GOAL", "GOAL_LOT":
			id, err := funds.ParseID(h.ResourceID)
			if err != nil {
				return errNativeFrozenLedgerV3
			}
			if _, ok := goals[id]; !ok {
				return errNativeFrozenLedgerV3
			}
			if h.ResourceKind == "GOAL_LOT" {
				var ref mirrorNativeLotReferenceV3
				if json.Unmarshal([]byte(h.ResourceDetail), &ref) != nil {
					return errNativeFrozenLedgerV3
				}
				a, err := funds.ParseID(ref.AccountID)
				if err != nil || !lotSeen[mirrornativeLotKeyV3(id, a, ref.SourceKind, ref.SourceID)] {
					return errNativeFrozenLedgerV3
				}
			}
		case "ASSIGNED":
			if !fragments[h.ResourceID] {
				return errNativeFrozenLedgerV3
			}
		case "POSITION":
			if _, ok := positions[h.ResourceID]; !ok {
				return errNativeFrozenLedgerV3
			}
		case "LIABILITY":
		default:
			return errNativeFrozenLedgerV3
		}
	}
	return nil
}

// Frozen pure replay source: internal/app/native_assets_standing_sources.go.
func (source wirenativeStandingAssetSource) account(id funds.ID) bool {
	for _, allowed := range source.AccountIDs {
		if allowed == id {
			return true
		}
	}
	return false
}

// Frozen pure replay source: internal/app/native_assets_standing_sources.go.
func (source wirenativeStandingAssetSource) lot(lot wireNativeGoalLot) bool {
	return source.account(lot.AccountID) && lot.SourceID == source.Source.ID && (source.Family == policy.NativeAssetAllocation && lot.SourceKind == "INCOME" || source.Family == policy.NativeMaturityReinvest && lot.SourceKind == "PRINCIPAL_RETURN")
}

// Frozen pure replay source: internal/app/native_assets_standing_sources.go.
func (source wirenativeStandingAssetSource) validate(n nativeFrozenLedgerV3, now time.Time) error {
	if source.Family != policy.NativeAssetAllocation && source.Family != policy.NativeMaturityReinvest || source.Source.Validate(source.Family) != nil || source.Source.OccurredAt.After(now) || source.Source.AvailableAt.After(now) || len(source.AccountIDs) == 0 || len(source.AccountIDs) > 16 {
		return nativeMirrorRejectV3("NATIVE_ASSET_SOURCE_INVALID", nil)
	}
	last := ""
	for _, id := range source.AccountIDs {
		if id == (funds.ID{}) || id.String() <= last {
			return nativeMirrorRejectV3("NATIVE_ASSET_SOURCE_INVALID", nil)
		}
		last = id.String()
	}
	if source.Family == policy.NativeAssetAllocation {
		for _, origin := range n.Income.Snapshot().Origins {
			if origin.ID().String() == source.Source.ID {
				if origin.Fact().EvidenceHash.String() != source.Source.OriginalHash || !origin.Fact().OccurredAt.Equal(source.Source.OccurredAt) {
					return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
				}
				return nil
			}
		}
	} else {
		for _, original := range n.PrincipalReturns {
			if original.BankKey == source.Source.ID {
				if nakedHash(original.Original) != original.OriginalHash || original.OriginalHash != source.Source.OriginalHash || !original.OccurredAt.Equal(source.Source.OccurredAt) || !original.OccurredAt.Equal(source.Source.AvailableAt) {
					return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
				}
				return nil
			}
		}
	}
	return nativeMirrorRejectV3("EVIDENCE_INVALID", nil)
}

// Frozen pure replay source: internal/app/forecast.go.
func (snapshot mirrortemplateSnapshotV3) forecastInput(days int) planning.ForecastInput {
	return planning.ForecastInput{Scope: snapshot.Facts.Scope, AsOf: snapshot.AsOf, Timezone: snapshot.Timezone, HorizonDays: days, Complete: true, InventoryHash: nakedHash(snapshot.InventoryRaw), Facts: snapshot.Facts, Versions: snapshot.Versions, RetainedObligations: snapshot.Retained, RetainedFloors: []planning.ForecastFloor{}, HeldCash: snapshot.Held, UnassignedGoalCash: snapshot.UnassignedGoalCash, FutureIncome: []planning.ForecastIncome{}, PrincipalReturns: []planning.ForecastPrincipalReturn{}, ExternalLiabilities: snapshot.ExternalLiabilities}
}
