package app

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"regexp"
	"time"

	"zhiyu.local/zhiyu-go/internal/audit"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const NativeBillProtocol = "zhiyu-external-bill-v1"
const NativeBillCaptureProtocol = "zhiyu-external-bill-capture-v1"
const MaxNativeBills = 1000
const MaxNativeBillBytes = 65536

var nativeBillNamespace = regexp.MustCompile(`^zygo_[a-z][a-z0-9_]{0,63}$`)

// This is a statement original from a configured trusted source. It is not an
// HTTP payment request or a model-supplied amount. Its exact bytes are bound by
// the independent bank's immutable BILL.EvidenceHash.
type NativeBillOriginal struct {
	Protocol           string                    `json:"protocol"`
	ID                 funds.ID                  `json:"id"`
	Scope              policy.TemplateOwnerScope `json:"scope"`
	BankNamespace      string                    `json:"bank_namespace"`
	StatementAccountID funds.ID                  `json:"statement_account_id"`
	DueDate            string                    `json:"due_date"`
	IssuedAt           time.Time                 `json:"issued_at"`
	FinalTotal         funds.Money               `json:"final_total_cents"`
	PayeeID            string                    `json:"payee_id"`
	ExternalKey        string                    `json:"external_key"`
}
type NativeBillStatementSnapshot struct {
	Protocol  string   `json:"protocol"`
	Namespace string   `json:"namespace"`
	UserID    string   `json:"user_id"`
	SourceID  string   `json:"source_id"`
	Complete  bool     `json:"complete"`
	Originals [][]byte `json:"originals"`
}

// Only operator-registered adapters supply this capability. Web routes accept
// a source ID, never a snapshot, its Complete flag, original or amount.
type NativeBillStatementReader interface {
	Bills(context.Context, string, string) (NativeBillStatementSnapshot, error)
}
type NativeBillSources struct {
	Readers map[string]NativeBillStatementReader
}

type NativeBillProof struct {
	Original          []byte                    `json:"original"`
	OriginalHash      string                    `json:"original_hash"`
	BankReference     bankv2.LiabilityReference `json:"bank_reference"`
	BankReferenceHash string                    `json:"bank_reference_hash"`
	FirstObservedAt   time.Time                 `json:"first_observed_at"`
	ProvenPaid        funds.Money               `json:"proven_paid_cents"`
	AdoptedVersion    policy.TemplateVersion    `json:"adopted_version"`
}

func decodeNativeBillOriginal(raw []byte, scope policy.TemplateOwnerScope, namespace string, observedAt time.Time) (NativeBillOriginal, error) {
	var out NativeBillOriginal
	if len(raw) == 0 || len(raw) > MaxNativeBillBytes {
		return out, reject("BILL_ORIGINAL_INVALID", nil)
	}
	if _, e := audit.ParseRawObject(raw); e != nil {
		return out, reject("BILL_ORIGINAL_INVALID", e)
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&out) != nil || decoder.Decode(new(any)) != io.EOF {
		return NativeBillOriginal{}, reject("BILL_ORIGINAL_INVALID", nil)
	}
	if out.Protocol != NativeBillProtocol || out.ID == (funds.ID{}) || out.Scope != scope || out.BankNamespace != namespace || !nativeBillNamespace.MatchString(namespace) || out.StatementAccountID == (funds.ID{}) || out.IssuedAt.IsZero() || out.IssuedAt.After(observedAt) || out.FinalTotal.IsZero() || !historyKey(out.PayeeID) || !historyKey(out.ExternalKey) {
		return NativeBillOriginal{}, reject("BILL_ORIGINAL_INVALID", nil)
	}
	if _, e := policy.TemplateDate(out.DueDate); e != nil {
		return NativeBillOriginal{}, reject("BILL_ORIGINAL_INVALID", e)
	}
	return out, nil
}
func nativeBillReference(original NativeBillOriginal, raw []byte) bankv2.LiabilityReference {
	return bankv2.LiabilityReference{ID: "bill:" + original.ID.String(), Kind: "BILL", FinalTotal: original.FinalTotal, PayeeID: original.PayeeID, EvidenceHash: Hash(raw)}
}
func nativeBillProofOriginal(proof NativeBillProof, scope policy.TemplateOwnerScope, now time.Time) (NativeBillOriginal, error) {
	if proof.FirstObservedAt.IsZero() || proof.FirstObservedAt.After(now) || Hash(proof.Original) != proof.OriginalHash {
		return NativeBillOriginal{}, reject("BILL_ORIGINAL_INVALID", nil)
	}
	// Namespace is independently pinned by the stored original and bank request;
	// all production constructors additionally compare it with Service.Namespace.
	var header struct {
		Namespace string `json:"bank_namespace"`
	}
	if json.Unmarshal(proof.Original, &header) != nil {
		return NativeBillOriginal{}, reject("BILL_ORIGINAL_INVALID", nil)
	}
	original, e := decodeNativeBillOriginal(proof.Original, scope, header.Namespace, proof.FirstObservedAt)
	if e != nil {
		return NativeBillOriginal{}, e
	}
	want := nativeBillReference(original, proof.Original)
	raw, _ := json.Marshal(want)
	if want.Validate() != nil || want != proof.BankReference || Hash(raw) != proof.BankReferenceHash {
		return NativeBillOriginal{}, reject("BILL_BANK_ORIGINAL_MISMATCH", nil)
	}
	return original, nil
}

func nativeBillPaymentRuleAmount(current policy.TemplateVersion, debt planning.ForecastObligation, liability bankv2.LiabilityState, proof NativeBillProof, requested funds.Money, now time.Time, timezone string) (funds.Money, error) {
	zero := funds.Money{}
	state, e := current.EffectiveState(now, timezone)
	if e != nil || state != "ACTIVE" || !debt.Verified || debt.PolicyID != current.PolicyID || !nativeActionHash(debt.SourceHash) || liability.Validate() != nil {
		return zero, reject("PAYMENT_LIABILITY_INVALID", e)
	}
	original, e := nativeBillProofOriginal(proof, current.Scope, now)
	if e != nil {
		return zero, e
	}
	if liability.Original != proof.BankReference || liability.OriginalHash != proof.BankReferenceHash || liability.Paid != proof.ProvenPaid || debt.ID != "bill:"+original.ID.String() || debt.DueDate != original.DueDate {
		return zero, reject("PAYMENT_LIABILITY_INVALID", nil)
	}
	adopted := proof.AdoptedVersion
	if adopted.Scope != current.Scope || adopted.PolicyID != debt.PolicyID || adopted.VersionID != debt.VersionID || !adopted.ConfirmationVerified || adopted.ConfirmedConfigurationHash != adopted.Compiled.Hash || adopted.Compiled.Hash != debt.SourceHash || adopted.ConfirmedAt.IsZero() || adopted.ConfirmedAt.After(now) {
		return zero, reject("BILL_ADOPTION_INVALID", nil)
	}
	configuration, e := current.Compiled.Decode()
	if e != nil {
		return zero, reject("PAYMENT_RULE_INVALID", e)
	}
	rule, ok := configuration.(*policy.RecurringObligationConfig)
	if !ok || rule.AmountRule.Kind != "bill_balance" || rule.AmountRule.AccountID == nil || *rule.AmountRule.AccountID != original.StatementAccountID || rule.PayeeID != original.PayeeID {
		return zero, reject("PAYMENT_BILL_RULE_MISMATCH", nil)
	}
	old, e := adopted.Compiled.Decode()
	if e != nil {
		return zero, reject("BILL_ADOPTION_INVALID", e)
	}
	oldRule, ok := old.(*policy.RecurringObligationConfig)
	if !ok || oldRule.AmountRule.Kind != "bill_balance" || oldRule.AmountRule.AccountID == nil || *oldRule.AmountRule.AccountID != original.StatementAccountID || oldRule.PayeeID != original.PayeeID {
		return zero, reject("BILL_ADOPTION_INVALID", nil)
	}
	due, e := policy.TemplateDate(original.DueDate)
	zone, ze := policy.TemplateLocation(timezone)
	if e != nil || ze != nil {
		return zero, reject("PAYMENT_LIABILITY_INVALID", e)
	}
	start := time.Date(due.Year(), due.Month(), due.Day(), 0, 0, 0, 0, zone).AddDate(0, 0, -rule.PrepareDaysBefore)
	if now.Before(start) {
		return zero, reject("PAYMENT_PREPARATION_NOT_OPEN", nil)
	}
	if !liability.Reserved.IsZero() {
		return zero, reject("PAYMENT_BANK_RESERVATION_PENDING", nil)
	}
	remaining, e := original.FinalTotal.Sub(liability.Paid)
	if e != nil || remaining.IsZero() {
		return zero, reject("PAYMENT_ALREADY_PAID", e)
	}
	if debt.Amount != remaining {
		return zero, reject("BILL_SETTLEMENT_INCOMPLETE", nil)
	}
	if requested.IsZero() {
		requested = remaining
	}
	if requested.Cents() > remaining.Cents() {
		return zero, reject("PAYMENT_AMOUNT_OUTSIDE_RULE", nil)
	}
	return requested, nil
}

// This conditional result changes cash and the same real external bill once.
// The current rule's authorization remains separate from this calculation.
func nativeBillPaymentForecastAfter(input planning.ForecastInput, account funds.ID, debt planning.ForecastObligation, liability bankv2.LiabilityState, proof NativeBillProof, amount funds.Money) (planning.ForecastInput, error) {
	var out planning.ForecastInput
	raw, e := encode(input)
	if e != nil || auditJSON(raw, &out) != nil {
		return out, reject("PAYMENT_FORECAST_INVALID", e)
	}
	original, e := nativeBillProofOriginal(proof, input.Scope, input.AsOf)
	if e != nil {
		return planning.ForecastInput{}, e
	}
	if liability.Validate() != nil || liability.Original != proof.BankReference || liability.Paid != proof.ProvenPaid || debt.ID != liability.Original.ID || amount.IsZero() {
		return planning.ForecastInput{}, reject("PAYMENT_LIABILITY_INVALID", nil)
	}
	remaining, e := original.FinalTotal.Sub(liability.Paid)
	if e != nil || amount.Cents() > remaining.Cents() || remaining != debt.Amount {
		return planning.ForecastInput{}, reject("PAYMENT_AMOUNT_OUTSIDE_RULE", e)
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
			return planning.ForecastInput{}, reject("PAYMENT_SOURCE_INVALID", nil)
		}
		found = true
		a.Balance, e = a.Balance.Sub(amount)
		if e != nil {
			return planning.ForecastInput{}, reject("FUNDS_UNAVAILABLE", e)
		}
	}
	if !found {
		return planning.ForecastInput{}, reject("PAYMENT_SOURCE_INVALID", nil)
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
			return planning.ForecastInput{}, reject("BILL_SETTLEMENT_INCOMPLETE", nil)
		}
		found = true
		bill.Paid = paid
	}
	if !found {
		return planning.ForecastInput{}, reject("BILL_ORIGINAL_REQUIRED", nil)
	}
	found = false
	for i := range out.ExternalLiabilities {
		bill := &out.ExternalLiabilities[i]
		if bill.ID != debt.ID {
			continue
		}
		if found || !bill.Verified || bill.DueDate != debt.DueDate || bill.Remaining != debt.Amount || bill.SourceHash != proof.OriginalHash {
			return planning.ForecastInput{}, reject("BILL_SETTLEMENT_INCOMPLETE", nil)
		}
		found = true
		bill.Remaining = remaining
	}
	if !found {
		return planning.ForecastInput{}, reject("BILL_ORIGINAL_REQUIRED", nil)
	}
	for i := range out.RetainedObligations {
		row := &out.RetainedObligations[i]
		if row.ID != debt.ID {
			continue
		}
		if row.PolicyID != debt.PolicyID || row.VersionID != debt.VersionID || row.DueDate != debt.DueDate || row.Amount != debt.Amount {
			return planning.ForecastInput{}, reject("BILL_SETTLEMENT_INCOMPLETE", nil)
		}
		row.Amount = remaining
	}
	return out, nil
}
