package app

import (
	"encoding/json"
	"time"

	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

// A retained debt keeps the original adopted total/version. Current permission
// is proved separately; a version change cannot rewrite this original.
type NativePaymentAdoptionProof struct {
	ObligationOriginal []byte                 `json:"obligation_original"`
	ObligationHash     string                 `json:"obligation_hash"`
	AdoptedVersion     policy.TemplateVersion `json:"adopted_version"`
}

type nativeAdoptedRule struct {
	Payee        string
	Amount       policy.TemplateAmountRule
	Prepare, Due int
	Cap          funds.Money
}

func nativeAdoptedRuleFor(version policy.TemplateVersion) (nativeAdoptedRule, error) {
	configuration, e := version.Compiled.Decode()
	if e != nil {
		return nativeAdoptedRule{}, reject("PAYMENT_RULE_INVALID", e)
	}
	var r nativeAdoptedRule
	switch c := configuration.(type) {
	case *policy.RecurringObligationConfig:
		r = nativeAdoptedRule{Payee: c.PayeeID, Amount: c.AmountRule, Prepare: c.PrepareDaysBefore, Due: c.DueDay}
	case *policy.PeriodicTransferConfig:
		r = nativeAdoptedRule{Payee: c.PayeeID, Amount: c.AmountRule, Prepare: c.PrepareDaysBefore, Due: c.DueDay, Cap: c.SingleActionCap}
	default:
		return r, reject("PAYMENT_RULE_INVALID", nil)
	}
	if r.Amount.Kind != "exact" && r.Amount.Kind != "range" {
		return r, reject("PAYMENT_BILL_ORIGINAL_REQUIRED", nil)
	}
	return r, nil
}
func nativeAdoptedFinalWithin(rule policy.TemplateAmountRule, total funds.Money) bool {
	if rule.Kind == "exact" {
		return rule.Amount == total
	}
	return rule.Kind == "range" && total.Cents() >= rule.Min.Cents() && total.Cents() <= rule.Max.Cents()
}
func nativePaymentAdoptionOriginal(proof NativePaymentAdoptionProof, debt planning.ForecastObligation, scope policy.TemplateOwnerScope, now time.Time) (planning.ForecastObligation, nativeAdoptedRule, error) {
	var original planning.ForecastObligation
	v := proof.AdoptedVersion
	if len(proof.ObligationOriginal) == 0 || len(proof.ObligationOriginal) > 65536 || Hash(proof.ObligationOriginal) != proof.ObligationHash || auditJSON(proof.ObligationOriginal, &original) != nil || !original.Verified || !debt.Verified || original.ID != debt.ID || original.PolicyID != debt.PolicyID || original.VersionID != debt.VersionID || original.DueDate != debt.DueDate || original.SourceHash != debt.SourceHash || v.Scope != scope || v.PolicyID != original.PolicyID || v.VersionID != original.VersionID || v.VersionID == (funds.ID{}) || v.Version == 0 || !v.ConfirmationVerified || v.ConfirmedConfigurationHash != v.Compiled.Hash || v.Compiled.Hash != original.SourceHash || v.ConfirmedAt.IsZero() || v.ConfirmedAt.After(now) {
		return original, nativeAdoptedRule{}, reject("PAYMENT_ADOPTION_INVALID", nil)
	}
	rule, e := nativeAdoptedRuleFor(v)
	if e != nil {
		return original, rule, e
	}
	upper := rule.Amount.Amount
	if rule.Amount.Kind == "range" {
		upper = rule.Amount.Max
	}
	due, e := policy.TemplateDate(original.DueDate)
	if e != nil || original.Amount != upper || original.ID != v.PolicyID.String()+":monthly:"+original.DueDate[:7] || policy.TemplateDueDate(due.Year(), due.Month(), rule.Due) != original.DueDate {
		return original, rule, reject("PAYMENT_ADOPTION_INVALID", e)
	}
	return original, rule, nil
}
func nativeAdoptedPaymentRuleAmount(current policy.TemplateVersion, debt planning.ForecastObligation, liability bankv2.LiabilityState, proof NativePaymentAdoptionProof, requested funds.Money, now time.Time, timezone string) (funds.Money, error) {
	zero := funds.Money{}
	state, e := current.EffectiveState(now, timezone)
	if e != nil || state != "ACTIVE" || debt.PolicyID != current.PolicyID || current.Compiled.Name != proof.AdoptedVersion.Compiled.Name || liability.Validate() != nil {
		return zero, reject("PAYMENT_LIABILITY_INVALID", e)
	}
	original, oldRule, e := nativePaymentAdoptionOriginal(proof, debt, current.Scope, now)
	if e != nil {
		return zero, e
	}
	rule, e := nativeAdoptedRuleFor(current)
	if e != nil {
		return zero, e
	}
	l := liability.Original
	if l.Kind != "OCCURRENCE" || l.ID != original.ID || l.PolicyID != original.PolicyID.String() || l.Period != original.DueDate[:7] || l.EvidenceHash != original.SourceHash || l.PayeeID != oldRule.Payee || l.PayeeID != rule.Payee {
		return zero, reject("PAYMENT_LIABILITY_INVALID", nil)
	}
	if !nativeAdoptedFinalWithin(oldRule.Amount, l.FinalTotal) || !nativeAdoptedFinalWithin(rule.Amount, l.FinalTotal) {
		return zero, reject("PAYMENT_FINAL_TOTAL_OUTSIDE_RULE", nil)
	}
	due, _ := policy.TemplateDate(original.DueDate)
	zone, e := policy.TemplateLocation(timezone)
	if e != nil {
		return zero, e
	}
	start := time.Date(due.Year(), due.Month(), due.Day(), 0, 0, 0, 0, zone).AddDate(0, 0, -rule.Prepare)
	if now.Before(start) {
		return zero, reject("PAYMENT_PREPARATION_NOT_OPEN", nil)
	}
	if !liability.Reserved.IsZero() {
		return zero, reject("PAYMENT_BANK_RESERVATION_PENDING", nil)
	}
	remaining, e := l.FinalTotal.Sub(liability.Paid)
	if e != nil || remaining.IsZero() {
		return zero, reject("PAYMENT_ALREADY_PAID", e)
	}
	upperRemaining, e := original.Amount.Sub(liability.Paid)
	if e != nil || debt.Amount.Cents() < remaining.Cents() || debt.Amount.Cents() > upperRemaining.Cents() {
		return zero, reject("PAYMENT_SETTLEMENT_INCOMPLETE", e)
	}
	if requested.IsZero() {
		requested = remaining
		if !rule.Cap.IsZero() && requested.Cents() > rule.Cap.Cents() {
			requested = rule.Cap
		}
	}
	if requested.Cents() > remaining.Cents() || !rule.Cap.IsZero() && requested.Cents() > rule.Cap.Cents() {
		return zero, reject("PAYMENT_AMOUNT_OUTSIDE_RULE", nil)
	}
	return requested, nil
}
func nativeAdoptedPaymentForecastAfter(input planning.ForecastInput, account funds.ID, debt planning.ForecastObligation, liability bankv2.LiabilityState, proof NativePaymentAdoptionProof, amount funds.Money) (planning.ForecastInput, error) {
	original, rule, e := nativePaymentAdoptionOriginal(proof, debt, input.Scope, input.AsOf)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	if !nativeAdoptedFinalWithin(rule.Amount, liability.Original.FinalTotal) || liability.Original.EvidenceHash != original.SourceHash || liability.Original.PayeeID != rule.Payee {
		return planning.ForecastInput{}, reject("PAYMENT_LIABILITY_INVALID", nil)
	}
	upper, e := original.Amount.Sub(liability.Paid)
	real, e2 := liability.Original.FinalTotal.Sub(liability.Paid)
	if e != nil || e2 != nil || debt.Amount.Cents() < real.Cents() || debt.Amount.Cents() > upper.Cents() {
		return planning.ForecastInput{}, reject("PAYMENT_SETTLEMENT_INCOMPLETE", nil)
	}
	after, e := nativePaymentForecastAfter(input, account, debt, liability, amount)
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
				return planning.ForecastInput{}, reject("PAYMENT_SOURCE_INVALID", nil)
			}
			found = true
			d.Amount = remaining
		}
	}
	if !found {
		return planning.ForecastInput{}, reject("PAYMENT_DEBT_NOT_ADOPTED", nil)
	}
	return after, nil
}
func nativePaymentAdoptionProofForDebt(snapshot templateSnapshot, debt planning.ForecastObligation) (NativePaymentAdoptionProof, error) {
	var proof NativePaymentAdoptionProof
	for _, raw := range snapshot.Raw["template_obligations"] {
		var row struct {
			ID       string `json:"id"`
			Original string `json:"original"`
			Hash     string `json:"original_hash"`
		}
		if json.Unmarshal([]byte(raw), &row) != nil {
			return proof, reject("PAYMENT_ADOPTION_INVALID", nil)
		}
		if row.ID != debt.ID {
			continue
		}
		if len(proof.ObligationOriginal) != 0 {
			return proof, reject("PAYMENT_ADOPTION_INVALID", nil)
		}
		original, e := decodePGBytea(row.Original)
		if e != nil {
			return proof, e
		}
		proof.ObligationOriginal = original
		proof.ObligationHash = row.Hash
	}
	for _, version := range snapshot.HistoricalVersions {
		if version.VersionID == debt.VersionID {
			if proof.AdoptedVersion.VersionID != (funds.ID{}) {
				return proof, reject("PAYMENT_ADOPTION_INVALID", nil)
			}
			proof.AdoptedVersion = version
		}
	}
	_, _, e := nativePaymentAdoptionOriginal(proof, debt, snapshot.Facts.Scope, snapshot.AsOf)
	return proof, e
}
