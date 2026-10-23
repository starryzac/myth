package executionaudit

import (
	"encoding/json"
	"fmt"
	"math/big"
	"strings"

	evidence "zhiyu.local/zhiyu-go/internal/audit"
)

// RegisterLegacySubjects registers the exact row protocols represented by the
// frozen pre-refactor subject oracle. EVIDENCE/ASSET_PRODUCT/DECISION_RUN retain
// old raw row claims; this registration does not replay their financial claims.
// Posting validation follows the preserved bank_posting_codec v1/v2 rules.
func RegisterLegacySubjects(registry *evidence.LegacyRegistry) error {
	for _, kind := range []string{"EVIDENCE", "ASSET_PRODUCT", "DECISION_RUN"} {
		if err := registry.Register(kind, 1, func(evidence.LegacySubject) error { return nil }); err != nil {
			return err
		}
	}
	for _, version := range []int64{1, 2} {
		if err := registry.Register("BANK_POSTING", version, validateLegacyPosting); err != nil {
			return err
		}
	}
	return nil
}

var legacyPostingFields = []string{"id", "user_id", "created_at", "ledger_key", "ledger_dimension", "ledger_metadata", "account_id", "position_id", "redemption_id", "operation_id", "leg_ref", "previous_posting_id", "sequence_number", "entry_kind", "balance_before_cents", "delta_cents", "balance_after_cents", "occurred_at"}

func validateLegacyPosting(subject evidence.LegacySubject) error {
	data := subject.Data
	invalid := fmt.Errorf("%w: historical posting original layout/origin", evidence.ErrInvalid)
	if len(data) != len(legacyPostingFields)+int(subject.SnapshotVersion-1) {
		return invalid
	}
	for _, field := range legacyPostingFields {
		if _, ok := data[field]; !ok {
			return invalid
		}
	}
	if subject.SnapshotVersion == 2 {
		if _, ok := data["external_fact_id"]; !ok {
			return invalid
		}
	}
	numbers := map[string]int64{}
	for _, field := range []string{"sequence_number", "balance_before_cents", "delta_cents", "balance_after_cents"} {
		number, ok := data[field].(json.Number)
		if !ok {
			return invalid
		}
		value, err := number.Int64()
		if err != nil || strings.ContainsAny(string(number), ".eE") {
			return invalid
		}
		numbers[field] = value
	}
	if numbers["sequence_number"] < 1 || numbers["balance_before_cents"] < 0 || numbers["balance_after_cents"] < 0 {
		return invalid
	}
	sum := new(big.Int).Add(big.NewInt(numbers["balance_before_cents"]), big.NewInt(numbers["delta_cents"]))
	if sum.Cmp(big.NewInt(numbers["balance_after_cents"])) != 0 {
		return invalid
	}
	if _, ok := data["ledger_metadata"].(map[string]any); !ok {
		return invalid
	}
	key, ok := data["ledger_key"].(string)
	if !ok {
		return invalid
	}
	opening := data["entry_kind"] == "OPENING"
	external := data["external_fact_id"]
	if opening {
		for _, field := range []string{"operation_id", "redemption_id", "leg_ref", "previous_posting_id"} {
			if data[field] != nil {
				return invalid
			}
		}
		if external != nil || numbers["sequence_number"] != 1 || numbers["balance_before_cents"] != 0 {
			return invalid
		}
	} else {
		if numbers["sequence_number"] <= 1 || (data["operation_id"] == nil) == (external == nil) || external != nil && data["redemption_id"] != nil || data["leg_ref"] == nil || data["previous_posting_id"] == nil {
			return invalid
		}
	}
	actualVersion := int64(1)
	if strings.HasPrefix(key, "CLEARING:") {
		const clearing = "CLEARING:bounded-funds-external-v1:"
		if !strings.HasPrefix(key, clearing) || key == clearing || data["ledger_dimension"] != "ECONOMIC" || data["account_id"] != nil || data["position_id"] != nil || data["redemption_id"] != nil || !opening && external == nil {
			return invalid
		}
		actualVersion = 2
	} else if external != nil {
		actualVersion = 2
	}
	if actualVersion != subject.SnapshotVersion {
		return invalid
	}
	return nil
}
