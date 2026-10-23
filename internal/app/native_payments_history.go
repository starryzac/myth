package app

import (
	"context"
	"encoding/json"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// A receipt from another family cannot retire debt. This complete native
// registry is read in the same RR snapshot as the immutable original receipt.
func nativeTemplateDebtPayments(ctx context.Context, tx pgx.Tx, p Principal, snapshot templateSnapshot) (map[string]funds.Money, error) {
	paid := map[string]funds.Money{}
	for _, raw := range snapshot.Raw["native_obligation_payments"] {
		var row struct {
			ID           funds.ID `json:"id"`
			ObligationID string   `json:"obligation_id"`
			OperationID  funds.ID `json:"operation_id"`
			ReceiptID    funds.ID `json:"receipt_id"`
			Amount       int64    `json:"amount_cents"`
			Original     string   `json:"original"`
			Hash         string   `json:"original_hash"`
		}
		if json.Unmarshal([]byte(raw), &row) != nil {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", nil)
		}
		original, e := decodePGBytea(row.Original)
		var payment nativeObligationPayment
		if e != nil || Hash(original) != row.Hash || auditJSON(original, &payment) != nil || payment.Protocol != "native-obligation-payment-v1" || payment.ID != row.ID || payment.OperationID != row.OperationID || payment.ReceiptID != row.ReceiptID || payment.ObligationID != row.ObligationID || payment.Amount.Cents() != row.Amount || payment.RecordedAt.After(snapshot.AsOf) {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", e)
		}
		var receiptRaw, requestRaw []byte
		var rh, receiptHash, state, kind string
		var receiptText *string
		e = tx.QueryRow(ctx, `SELECT o.status,o.command_kind,o.bank_request,o.bank_request_hash::text,o.receipt_id::text,r.original,r.original_hash::text FROM native_operations o JOIN native_receipts r ON(r.environment_id=o.environment_id AND r.user_id=o.user_id AND r.id=$4) WHERE o.environment_id=$1 AND o.user_id=$2 AND o.id=$3`, p.EnvironmentID.String(), p.UserID.String(), row.OperationID.String(), row.ReceiptID.String()).Scan(&state, &kind, &requestRaw, &rh, &receiptText, &receiptRaw, &receiptHash)
		var request bankv2.Request
		var receipt NativeReceipt
		if e != nil || state != "SETTLED" || kind != string(bankv2.Payment) || receiptText == nil || *receiptText != row.ReceiptID.String() || Hash(receiptRaw) != receiptHash || auditJSON(requestRaw, &request) != nil || auditJSON(receiptRaw, &receipt) != nil {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", e)
		}
		digest, e := request.Digest()
		if e != nil || digest != rh || request.UserID != p.UserID.String() || request.OperationID != row.OperationID.String() || request.Effect.Kind != bankv2.Payment || request.Effect.Liability == nil || request.Effect.Liability.ID != row.ObligationID || request.Effect.Amount != payment.Amount || receipt.ID != row.ReceiptID || receipt.OperationID != row.OperationID || receipt.BankResult.Status != bankv2.Settled || bankv2.VerifyResult(request, receipt.BankResult) != nil || !receipt.RecordedAt.Equal(payment.RecordedAt) {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", e)
		}
		var sourceRaw []byte
		var sourceHash string
		e = tx.QueryRow(ctx, `SELECT original,original_hash::text FROM native_payment_sources WHERE environment_id=$1 AND user_id=$2 AND operation_id=$3 AND obligation_id=$4`, p.EnvironmentID.String(), p.UserID.String(), row.OperationID.String(), row.ObligationID).Scan(&sourceRaw, &sourceHash)
		var source bankv2.LiabilityState
		if e != nil || Hash(sourceRaw) != sourceHash || auditJSON(sourceRaw, &source) != nil || source.Validate() != nil || source.Original != *request.Effect.Liability || source.OriginalHash != payment.LiabilityHash {
			return nil, reject("TEMPLATE_PAYMENT_EVIDENCE_INVALID", e)
		}
		paid[row.ObligationID], e = paid[row.ObligationID].Add(payment.Amount)
		if e != nil {
			return nil, e
		}
	}
	return paid, nil
}
