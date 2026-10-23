package app

import (
	"encoding/json"

	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// The caller first verifies every SETTLED receipt/request/source with
// nativeTemplateDebtPayments. Queued previews, bank paid counters and unsigned
// source rows cannot resolve an adopted range's conservative upper bound.
func nativeAdoptedPaymentFinalTotals(snapshot templateSnapshot, verifiedPaid map[string]funds.Money) (map[string]funds.Money, error) {
	operations := map[funds.ID]string{}
	for _, raw := range snapshot.Raw["native_obligation_payments"] {
		var row struct {
			OperationID funds.ID `json:"operation_id"`
			ID          string   `json:"obligation_id"`
		}
		if json.Unmarshal([]byte(raw), &row) != nil || verifiedPaid[row.ID].IsZero() || operations[row.OperationID] != "" {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", nil)
		}
		operations[row.OperationID] = row.ID
	}
	totals := map[string]funds.Money{}
	references := map[string]bankv2.LiabilityReference{}
	seen := map[funds.ID]bool{}
	for _, raw := range snapshot.Raw["native_payment_sources"] {
		var row struct {
			OperationID funds.ID `json:"operation_id"`
			ID          string   `json:"obligation_id"`
			Original    string   `json:"original"`
			Hash        string   `json:"original_hash"`
		}
		if json.Unmarshal([]byte(raw), &row) != nil {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", nil)
		}
		id, actual := operations[row.OperationID]
		if !actual {
			continue
		}
		if row.ID != id || seen[row.OperationID] {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", nil)
		}
		seen[row.OperationID] = true
		original, e := decodePGBytea(row.Original)
		var source bankv2.LiabilityState
		if e != nil || Hash(original) != row.Hash || auditJSON(original, &source) != nil || source.Validate() != nil || source.Original.ID != id {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", e)
		}
		if source.Original.Kind != "OCCURRENCE" {
			continue
		}
		if prior, exists := references[id]; exists && prior != source.Original {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", nil)
		}
		if verifiedPaid[id].Cents() > source.Original.FinalTotal.Cents() {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", nil)
		}
		references[id] = source.Original
		totals[id] = source.Original.FinalTotal
	}
	if len(seen) != len(operations) {
		return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", nil)
	}
	return totals, nil
}
