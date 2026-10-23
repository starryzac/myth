package app

import (
	"time"

	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

// A recurring payment uses an adopted debt and an independent final liability.
// A model or a client-supplied amount cannot create either original.
func nativePaymentRuleAmount(version policy.TemplateVersion, debt planning.ForecastObligation, liability bankv2.LiabilityState, requested funds.Money, now time.Time, timezone string) (funds.Money, error) {
	var zero funds.Money
	state, e := version.EffectiveState(now, timezone)
	if e != nil || state != "ACTIVE" || !debt.Verified || debt.PolicyID != version.PolicyID || debt.VersionID != version.VersionID || !nativeActionHash(debt.SourceHash) || liability.Validate() != nil || liability.Original.Kind != "OCCURRENCE" || liability.Original.ID != debt.ID || liability.Original.PolicyID != version.PolicyID.String() || liability.Original.EvidenceHash != debt.SourceHash {
		return zero, reject("PAYMENT_LIABILITY_INVALID", e)
	}
	date, e := policy.TemplateDate(debt.DueDate)
	if e != nil || liability.Original.Period != debt.DueDate[:7] {
		return zero, reject("PAYMENT_LIABILITY_INVALID", e)
	}
	configuration, e := version.Compiled.Decode()
	if e != nil {
		return zero, reject("PAYMENT_RULE_INVALID", e)
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
		return zero, reject("PAYMENT_RULE_INVALID", nil)
	}
	if liability.Original.PayeeID != payee || policy.TemplateDueDate(date.Year(), date.Month(), dueDay) != debt.DueDate {
		return zero, reject("PAYMENT_LIABILITY_INVALID", nil)
	}
	final := liability.Original.FinalTotal
	switch rule.Kind {
	case "exact":
		if final != rule.Amount {
			return zero, reject("PAYMENT_FINAL_TOTAL_OUTSIDE_RULE", nil)
		}
	case "range":
		if final.Cents() < rule.Min.Cents() || final.Cents() > rule.Max.Cents() {
			return zero, reject("PAYMENT_FINAL_TOTAL_OUTSIDE_RULE", nil)
		}
	default:
		return zero, reject("PAYMENT_BILL_ORIGINAL_REQUIRED", nil)
	}
	zone, e := policy.TemplateLocation(timezone)
	if e != nil {
		return zero, e
	}
	start := time.Date(date.Year(), date.Month(), date.Day(), 0, 0, 0, 0, zone).AddDate(0, 0, -prepare)
	if now.Before(start) {
		return zero, reject("PAYMENT_PREPARATION_NOT_OPEN", nil)
	}
	if !liability.Reserved.IsZero() {
		return zero, reject("PAYMENT_BANK_RESERVATION_PENDING", nil)
	}
	remaining, e := final.Sub(liability.Paid)
	if e != nil || remaining.IsZero() {
		return zero, reject("PAYMENT_ALREADY_PAID", e)
	}
	if requested.IsZero() {
		requested = remaining
		if !cap.IsZero() && requested.Cents() > cap.Cents() {
			requested = cap
		}
	}
	if requested.Cents() > remaining.Cents() || requested.Cents() > debt.Amount.Cents() || !cap.IsZero() && requested.Cents() > cap.Cents() {
		return zero, reject("PAYMENT_AMOUNT_OUTSIDE_RULE", nil)
	}
	return requested, nil
}

// The conditional after-state pays exactly one adopted liability. No future
// income is inserted and cash is reduced at the actual current instant.
func nativePaymentForecastAfter(input planning.ForecastInput, account funds.ID, debt planning.ForecastObligation, liability bankv2.LiabilityState, amount funds.Money) (planning.ForecastInput, error) {
	raw, e := encode(input)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	var after planning.ForecastInput
	if auditJSON(raw, &after) != nil {
		return after, reject("PAYMENT_FORECAST_INVALID", nil)
	}
	_, dateErr := policy.TemplateDate(debt.DueDate)
	if dateErr != nil || amount.IsZero() || liability.Validate() != nil || liability.Original.PolicyID != debt.PolicyID.String() || liability.Original.Period != debt.DueDate[:7] || liability.Original.ID != debt.ID {
		return after, reject("PAYMENT_LIABILITY_INVALID", nil)
	}
	found := false
	for i := range after.Facts.Accounts {
		a := &after.Facts.Accounts[i]
		if a.ID != account {
			continue
		}
		if found || !a.Verified {
			return after, reject("PAYMENT_SOURCE_INVALID", nil)
		}
		found = true
		a.Balance, e = a.Balance.Sub(amount)
		if e != nil {
			return after, reject("FUNDS_UNAVAILABLE", e)
		}
	}
	if !found {
		return after, reject("PAYMENT_SOURCE_INVALID", nil)
	}
	after.Facts.CurrentCash, e = after.Facts.CurrentCash.Sub(amount)
	if e != nil {
		return after, e
	}
	paid, e := liability.Paid.Add(amount)
	if e != nil || paid.Cents() > liability.Original.FinalTotal.Cents() {
		return after, reject("PAYMENT_AMOUNT_OUTSIDE_RULE", e)
	}
	found = false
	for i := range after.Facts.Settlements {
		s := &after.Facts.Settlements[i]
		if s.PolicyID == debt.PolicyID && s.Period == liability.Original.Period {
			if found || !s.Verified || s.Paid != liability.Paid {
				return after, reject("PAYMENT_SOURCE_INVALID", nil)
			}
			found = true
			s.Paid = paid
			total := liability.Original.FinalTotal
			s.FinalTotal = &total
		}
	}
	if !found {
		if !liability.Paid.IsZero() {
			return after, reject("PAYMENT_SOURCE_INVALID", nil)
		}
		total := liability.Original.FinalTotal
		after.Facts.Settlements = append(after.Facts.Settlements, policy.TemplateSettlementFact{PolicyID: debt.PolicyID, Period: liability.Original.Period, Paid: paid, FinalTotal: &total, Verified: true})
	}
	for i := range after.RetainedObligations {
		row := &after.RetainedObligations[i]
		if row.ID == debt.ID {
			if row.PolicyID != debt.PolicyID || row.VersionID != debt.VersionID || row.Amount != debt.Amount {
				return after, reject("PAYMENT_SOURCE_INVALID", nil)
			}
			row.Amount, e = row.Amount.Sub(amount)
			if e != nil {
				return after, e
			}
		}
	}
	return after, nil
}
