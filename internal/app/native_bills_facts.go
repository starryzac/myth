package app

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

func appendNativeBillSourceRow(snapshot *templateSnapshot, table, raw string) error {
	rows, total := 1, len(raw)
	for _, values := range snapshot.Raw {
		rows += len(values)
		for _, value := range values {
			total += len(value)
		}
	}
	if rows > 10000 || total > 4<<20 {
		return reject("TEMPLATE_CAPACITY_EXCEEDED", nil)
	}
	snapshot.Raw[table] = append(snapshot.Raw[table], raw)
	return nil
}
func nativeBillPaidRows(ctx context.Context, tx pgx.Tx, p Principal, now time.Time) (map[string]funds.Money, error) {
	snapshot := templateSnapshot{AsOf: now, Raw: map[string][]string{"native_obligation_payments": {}}}
	rows, e := tx.Query(ctx, `SELECT to_jsonb(t)::text FROM native_obligation_payments t WHERE environment_id=$1 AND user_id=$2 ORDER BY id LIMIT 10001`, p.EnvironmentID.String(), p.UserID.String())
	if e != nil {
		return nil, e
	}
	for rows.Next() {
		var raw string
		if e = rows.Scan(&raw); e != nil {
			rows.Close()
			return nil, e
		}
		if e = appendNativeBillSourceRow(&snapshot, "native_obligation_payments", raw); e != nil {
			rows.Close()
			return nil, e
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	return nativeTemplateDebtPayments(ctx, tx, p, snapshot)
}
func (s *Service) loadNativeBillFacts(ctx context.Context, tx pgx.Tx, p Principal, snapshot *templateSnapshot) error {
	if e := ensureNativeBillsReady(ctx, tx, p); e != nil {
		return e
	}
	var present bool
	if e := tx.QueryRow(ctx, `SELECT to_regclass('native_bill_originals') IS NOT NULL`).Scan(&present); e != nil {
		return e
	}
	if !present {
		return nil
	}
	var lastID string
	// B writes resolutions before its protection publication/result in this
	// same transaction. The deferred FK prevents committing a resolution alone.
	e := tx.QueryRow(ctx, `SELECT r.result_capture_id::text FROM native_bill_capture_resolutions r JOIN native_bill_captures c ON(c.environment_id=r.environment_id AND c.user_id=r.user_id AND c.id=r.result_capture_id) WHERE r.environment_id=$1 AND r.user_id=$2 ORDER BY c.recorded_funds_version DESC,c.id DESC LIMIT 1`, p.EnvironmentID.String(), p.UserID.String()).Scan(&lastID)
	if errors.Is(e, pgx.ErrNoRows) {
		return nil
	}
	if e != nil {
		return e
	}
	last, e := funds.ParseID(lastID)
	if e != nil {
		return e
	}
	current, e := loadNativeBillCapture(ctx, tx, p, last, "")
	if e != nil {
		return e
	}
	if current.Bank.Namespace != s.Namespace {
		return reject("BILL_CAPTURE_INVALID", nil)
	}
	paid, e := nativeTemplateDebtPayments(ctx, tx, p, *snapshot)
	if e != nil {
		return e
	}
	bankRows := map[string]struct{ Paid, Reserved funds.Money }{}
	for _, row := range current.Bank.Liabilities {
		if row.Original.Kind == "BILL" {
			bankRows[row.Original.ID] = struct{ Paid, Reserved funds.Money }{row.Paid, row.Reserved}
		}
	}
	snapshot.Raw["native_bill_originals"] = []string{}
	snapshot.Raw["native_bill_capture_references"] = []string{}
	snapshot.BillProofs = map[string]NativeBillProof{}
	snapshot.HistoricalBills = []planning.HistoricalBillFact{}
	snapshot.ExternalLiabilities = []planning.ForecastExternalLiability{}
	rows, e := tx.Query(ctx, `SELECT to_jsonb(t)::text FROM native_bill_originals t WHERE environment_id=$1 AND user_id=$2 ORDER BY id LIMIT 1001`, p.EnvironmentID.String(), p.UserID.String())
	if e != nil {
		return e
	}
	rawRows := []string{}
	for rows.Next() {
		var raw string
		if e = rows.Scan(&raw); e != nil {
			rows.Close()
			return e
		}
		if len(rawRows) >= MaxNativeBills {
			rows.Close()
			return reject("BILL_SOURCE_CAPACITY", nil)
		}
		rawRows = append(rawRows, raw)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	captureCache := map[funds.ID]NativeBillCapture{}
	for _, raw := range rawRows {
		var row struct {
			ID                 funds.ID  `json:"id"`
			SourceID           string    `json:"source_id"`
			AccountID          funds.ID  `json:"statement_account_id"`
			DueDate            string    `json:"due_date"`
			IssuedAt           time.Time `json:"issued_at"`
			Total              int64     `json:"final_total_cents"`
			Payee, ExternalKey string
			Original           string    `json:"original"`
			Hash               string    `json:"original_hash"`
			Reference          string    `json:"bank_reference_original"`
			ReferenceHash      string    `json:"bank_reference_hash"`
			FirstCaptureID     funds.ID  `json:"first_capture_id"`
			FirstObservedAt    time.Time `json:"first_observed_at"`
		}
		// Read projected fields with explicit database names; row JSON is retained
		// separately in the source hash and is not normalized into a fake original.
		var names struct {
			Payee       string `json:"payee_id"`
			ExternalKey string `json:"external_key"`
		}
		if json.Unmarshal([]byte(raw), &row) != nil || json.Unmarshal([]byte(raw), &names) != nil {
			return reject("BILL_ORIGINAL_INVALID", nil)
		}
		row.Payee, row.ExternalKey = names.Payee, names.ExternalKey
		original, e := decodePGBytea(row.Original)
		if e != nil || Hash(original) != row.Hash {
			return reject("BILL_ORIGINAL_INVALID", e)
		}
		bill, e := decodeNativeBillOriginal(original, snapshot.Facts.Scope, s.Namespace, row.FirstObservedAt)
		if e != nil || bill.ID != row.ID || bill.StatementAccountID != row.AccountID || bill.DueDate != row.DueDate || bill.FinalTotal.Cents() != row.Total || bill.PayeeID != row.Payee || bill.ExternalKey != row.ExternalKey || !bill.IssuedAt.Equal(row.IssuedAt) {
			return reject("BILL_ORIGINAL_INVALID", e)
		}
		reference, e := decodePGBytea(row.Reference)
		proof := NativeBillProof{Original: original, OriginalHash: row.Hash, BankReferenceHash: row.ReferenceHash, FirstObservedAt: row.FirstObservedAt, ProvenPaid: paid["bill:"+row.ID.String()]}
		if e != nil || Hash(reference) != row.ReferenceHash || auditJSON(reference, &proof.BankReference) != nil {
			return reject("BILL_BANK_ORIGINAL_MISMATCH", e)
		}
		if _, e = nativeBillProofOriginal(proof, snapshot.Facts.Scope, snapshot.AsOf); e != nil {
			return e
		}
		first, ok := captureCache[row.FirstCaptureID]
		if !ok {
			first, e = loadNativeBillCapture(ctx, tx, p, row.FirstCaptureID, "")
			if e != nil {
				return e
			}
			captureCache[row.FirstCaptureID] = first
			firstRaw, _ := encode(first)
			refRaw, _ := encode(struct {
				ID   funds.ID  `json:"id"`
				Hash string    `json:"original_hash"`
				At   time.Time `json:"observed_at"`
			}{first.ID, Hash(firstRaw), first.ObservedAt})
			if e = appendNativeBillSourceRow(snapshot, "native_bill_capture_references", string(refRaw)); e != nil {
				return e
			}
		}
		if first.SourceID != row.SourceID || !first.ObservedAt.Equal(row.FirstObservedAt) || first.OriginalHashes[row.ID.String()] != row.Hash {
			return reject("BILL_FIRST_OBSERVATION_INVALID", nil)
		}
		if current.SourceID != row.SourceID || current.OriginalHashes[row.ID.String()] != row.Hash {
			return reject("BILL_SOURCE_INCOMPLETE", nil)
		}
		state, ok := bankRows[proof.BankReference.ID]
		if !ok || state.Paid.Cents() > proof.ProvenPaid.Cents() || !state.Reserved.IsZero() {
			return reject("BILL_SETTLEMENT_INCOMPLETE", nil)
		}
		remaining, e := bill.FinalTotal.Sub(proof.ProvenPaid)
		if e != nil {
			return reject("BILL_SETTLEMENT_INCOMPLETE", e)
		}
		fact := policy.TemplateBillFact{ID: bill.ID, AccountID: bill.StatementAccountID, DueDate: bill.DueDate, Total: bill.FinalTotal, Paid: proof.ProvenPaid, Verified: true}
		snapshot.Facts.Bills = append(snapshot.Facts.Bills, fact)
		snapshot.HistoricalBills = append(snapshot.HistoricalBills, planning.HistoricalBillFact{Bill: fact, FirstObservedAt: proof.FirstObservedAt, SourceHash: proof.OriginalHash})
		snapshot.ExternalLiabilities = append(snapshot.ExternalLiabilities, planning.ForecastExternalLiability{ID: proof.BankReference.ID, DueDate: bill.DueDate, Remaining: remaining, Verified: true, SourceHash: proof.OriginalHash})
		snapshot.BillProofs[proof.BankReference.ID] = proof
		if e = appendNativeBillSourceRow(snapshot, "native_bill_originals", raw); e != nil {
			return e
		}
	}
	for id, amount := range paid {
		if strings.HasPrefix(id, "bill:") && !amount.IsZero() {
			if _, ok := snapshot.BillProofs[id]; !ok {
				return reject("BILL_PAYMENT_EVIDENCE_INCOMPLETE", nil)
			}
		}
	}
	return nil
}
func nativeBillProofForDebt(snapshot templateSnapshot, debt planning.ForecastObligation) (NativeBillProof, error) {
	proof, ok := snapshot.BillProofs[debt.ID]
	if !ok {
		return NativeBillProof{}, reject("BILL_ORIGINAL_REQUIRED", nil)
	}
	for _, v := range snapshot.HistoricalVersions {
		if v.VersionID == debt.VersionID {
			if v.PolicyID != debt.PolicyID || v.Compiled.Hash != debt.SourceHash {
				return NativeBillProof{}, reject("BILL_ADOPTION_INVALID", nil)
			}
			proof.AdoptedVersion = v
			return proof, nil
		}
	}
	return NativeBillProof{}, reject("BILL_ADOPTION_INVALID", nil)
}
