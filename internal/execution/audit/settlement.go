// Package executionaudit supplies pure execution-specific validators to the
// generic evidence registry. The generic audit kernel does not import it.
package executionaudit

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"

	evidence "zhiyu.local/zhiyu-go/internal/audit"
	execution "zhiyu.local/zhiyu-go/internal/execution/domain"
)

const SettlementKind = "EXECUTION_SETTLEMENT"
const SettlementVersion = "v1"

type SettlementEvidence struct {
	ID          string
	Environment string
	UserID      string
	OperationID string
	ReceiptID   string
	Request     execution.BankRequest
	Result      execution.BankResult
	References  []evidence.Reference
}

func SealSettlement(input SettlementEvidence) (evidence.Original, error) {
	request, err := input.Request.CanonicalBytes()
	if err != nil {
		return evidence.Original{}, err
	}
	result, err := json.Marshal(input.Result)
	if err != nil {
		return evidence.Original{}, err
	}
	refs := input.References
	if refs == nil {
		refs = []evidence.Reference{}
	}
	doc := evidence.Document{SchemaVersion: evidence.SchemaVersion, CanonicalVersion: evidence.CanonicalVersion, HashAlgorithm: evidence.HashAlgorithm, Simulation: true, ID: input.ID, Kind: SettlementKind, SemanticVersion: SettlementVersion, Outcome: "SETTLED", Data: map[string]any{"environment": input.Environment, "user_id": input.UserID, "operation_id": input.OperationID, "receipt_id": input.ReceiptID, "bank_request_hex": hex.EncodeToString(request), "bank_result_hex": hex.EncodeToString(result)}, References: refs}
	if err = validateSettlement(doc); err != nil {
		return evidence.Original{}, err
	}
	return evidence.Seal(doc)
}

func Register(registry *evidence.Registry) error {
	if err := registry.Register(SettlementKind, SettlementVersion, validateSettlement); err != nil {
		return err
	}
	return registry.RegisterReferenceValidator(SettlementKind, SettlementVersion, func(parent, child evidence.Document, _ evidence.Reference) error {
		if parent.Data["environment"] != child.Data["environment"] || parent.Data["user_id"] != child.Data["user_id"] {
			return fmt.Errorf("%w: settlement reference crosses environment or user", evidence.ErrInvalid)
		}
		return nil
	})
}

func validateSettlement(doc evidence.Document) error {
	invalid := fmt.Errorf("%w: execution settlement binding", evidence.ErrInvalid)
	if doc.Kind != SettlementKind || doc.SemanticVersion != SettlementVersion || doc.Outcome != "SETTLED" || len(doc.Data) != 6 {
		return invalid
	}
	fields := map[string]string{}
	for _, key := range []string{"environment", "user_id", "operation_id", "receipt_id", "bank_request_hex", "bank_result_hex"} {
		value, ok := doc.Data[key].(string)
		if !ok || value == "" {
			return invalid
		}
		fields[key] = value
	}
	if len(fields["receipt_id"]) > 160 || strings.ContainsAny(fields["receipt_id"], "\x00\r\n") {
		return invalid
	}
	var request execution.BankRequest
	var result execution.BankResult
	if decodeHex(fields["bank_request_hex"], &request) != nil || decodeHex(fields["bank_result_hex"], &result) != nil {
		return invalid
	}
	if request.Namespace != fields["environment"] || request.UserID != fields["user_id"] || request.OperationID != fields["operation_id"] || result.Status != execution.BankSettled {
		return invalid
	}
	if execution.VerifyBankResult(request, result) != nil {
		return invalid
	}
	return nil
}

func decodeHex(encoded string, value any) error {
	if len(encoded) > 2*evidence.MaxCanonicalBytes {
		return evidence.ErrInvalid
	}
	raw, err := hex.DecodeString(encoded)
	if err != nil {
		return err
	}
	if _, err = evidence.ParseRawObject(raw); err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	return decoder.Decode(value)
}
