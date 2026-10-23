package app

import (
	"context"
	"errors"
	"sort"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type NativeBillImportResult struct {
	Protocol           string                    `json:"protocol"`
	Scope              policy.TemplateOwnerScope `json:"scope"`
	CaptureID          funds.ID                  `json:"capture_id"`
	ImportedBillIDs    []funds.ID                `json:"imported_bill_ids"`
	ResolvedCaptureIDs []funds.ID                `json:"resolved_capture_ids"`
	FundsVersion       uint64                    `json:"funds_version"`
	SourceVersion      uint64                    `json:"source_version"`
	ProtectionHash     string                    `json:"protection_hash"`
	RecordedAt         time.Time                 `json:"recorded_at"`
	BankAuthority      bool                      `json:"bank_authority"`
}
type NativeBillIntakeView struct {
	Protocol      string                  `json:"protocol"`
	CaptureID     funds.ID                `json:"capture_id"`
	CaptureHash   string                  `json:"capture_hash"`
	State         string                  `json:"state"`
	PendingReason string                  `json:"pending_reason,omitempty"`
	Result        *NativeBillImportResult `json:"result,omitempty"`
	BankAuthority bool                    `json:"bank_authority"`
}
type nativeBillCaptureResolution struct {
	Protocol        string                    `json:"protocol"`
	Scope           policy.TemplateOwnerScope `json:"scope"`
	CaptureID       funds.ID                  `json:"capture_id"`
	ResultCaptureID funds.ID                  `json:"result_capture_id"`
	RecordedAt      time.Time                 `json:"recorded_at"`
}

func loadNativeBillIntakeView(ctx context.Context, q queryRow, p Principal, capture NativeBillCapture) (NativeBillIntakeView, error) {
	raw, e := encode(capture)
	if e != nil {
		return NativeBillIntakeView{}, e
	}
	view := NativeBillIntakeView{Protocol: "native-bill-intake-view-v1", CaptureID: capture.ID, CaptureHash: Hash(raw), State: "CAPTURED_PENDING", PendingReason: "TRUSTED_CAPTURE_NOT_INCORPORATED"}
	var resultRaw []byte
	var resultHash string
	e = q.QueryRow(ctx, `SELECT i.original,i.original_hash FROM native_bill_capture_resolutions r JOIN native_bill_import_results i ON(i.environment_id=r.environment_id AND i.user_id=r.user_id AND i.capture_id=r.result_capture_id) WHERE r.environment_id=$1 AND r.user_id=$2 AND r.capture_id=$3`, p.EnvironmentID.String(), p.UserID.String(), capture.ID.String()).Scan(&resultRaw, &resultHash)
	if errors.Is(e, pgx.ErrNoRows) {
		return view, nil
	}
	if e != nil {
		return view, e
	}
	var result NativeBillImportResult
	if Hash(resultRaw) != resultHash || auditJSON(resultRaw, &result) != nil || result.Protocol != "native-bill-import-result-v1" || result.Scope != capture.Scope || result.BankAuthority {
		return NativeBillIntakeView{}, reject("BILL_IMPORT_EVIDENCE_INVALID", nil)
	}
	view.State = "IMPORTED"
	view.PendingReason = ""
	view.Result = &result
	return view, nil
}
func (s *Service) ImportNativeBills(ctx context.Context, p Principal, key, sourceID string, sources NativeBillSources) (NativeBillIntakeView, error) {
	capture, e := s.captureNativeBills(ctx, p, key, sourceID, sources)
	if e != nil {
		return NativeBillIntakeView{}, e
	}
	if s.Failpoint != nil {
		if e = s.Failpoint("native_bills_after_capture_commit"); e != nil {
			return NativeBillIntakeView{}, e
		}
	}
	return s.completeNativeBillCapture(ctx, p, capture)
}
func (s *Service) NativeBillIntake(ctx context.Context, p Principal, id funds.ID) (NativeBillIntakeView, error) {
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return NativeBillIntakeView{}, e
	}
	capture, e := loadNativeBillCapture(ctx, s.Pool, p, id, "")
	if errors.Is(e, pgx.ErrNoRows) {
		return NativeBillIntakeView{}, reject("NOT_FOUND", nil)
	}
	if e != nil {
		return NativeBillIntakeView{}, e
	}
	return loadNativeBillIntakeView(ctx, s.Pool, p, capture)
}
func (s *Service) ResumeNativeBillImport(ctx context.Context, p Principal, id funds.ID) (NativeBillIntakeView, error) {
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return NativeBillIntakeView{}, e
	}
	capture, e := loadNativeBillCapture(ctx, s.Pool, p, id, "")
	if errors.Is(e, pgx.ErrNoRows) {
		return NativeBillIntakeView{}, reject("NOT_FOUND", nil)
	}
	if e != nil {
		return NativeBillIntakeView{}, e
	}
	return s.completeNativeBillCapture(ctx, p, capture)
}
func (s *Service) completeNativeBillCapture(ctx context.Context, p Principal, capture NativeBillCapture) (NativeBillIntakeView, error) {
	tx, e := s.begin(ctx)
	if e != nil {
		return NativeBillIntakeView{}, e
	}
	defer rollback(ctx, tx)
	env, e := s.environment(ctx, tx, true)
	if e != nil {
		return NativeBillIntakeView{}, e
	}
	if e = sameUser(p, env.ID); e != nil {
		return NativeBillIntakeView{}, e
	}
	fv, _, e := lockUser(ctx, tx, p)
	if e != nil {
		return NativeBillIntakeView{}, e
	}
	if e = s.human(ctx, tx, p); e != nil {
		return NativeBillIntakeView{}, e
	}
	capture, e = loadNativeBillCapture(ctx, tx, p, capture.ID, "")
	if e != nil {
		return NativeBillIntakeView{}, e
	}
	view, e := loadNativeBillIntakeView(ctx, tx, p, capture)
	if e != nil {
		return view, e
	}
	if view.State == "IMPORTED" {
		return view, tx.Commit(ctx)
	}
	paid, e := nativeBillPaidRows(ctx, tx, p, s.now())
	if e != nil {
		return view, e
	}
	for _, row := range capture.Bank.Liabilities {
		if row.Original.Kind != "BILL" {
			continue
		}
		if !row.Reserved.IsZero() {
			view.PendingReason = "BILL_BANK_RESERVATION_PENDING"
			return view, tx.Commit(ctx)
		}
		if row.Paid.Cents() > paid[row.Original.ID].Cents() {
			view.PendingReason = "BILL_PAYMENT_RECEIPT_REQUIRED"
			return view, tx.Commit(ctx)
		}
	}
	// No already known statement may disappear or be replaced by a new source.
	rows, e := tx.Query(ctx, `SELECT id::text,source_id,original_hash::text FROM native_bill_originals WHERE environment_id=$1 AND user_id=$2 ORDER BY id LIMIT 1001`, p.EnvironmentID.String(), p.UserID.String())
	if e != nil {
		return view, e
	}
	known := 0
	for rows.Next() {
		var id, source, hash string
		if e = rows.Scan(&id, &source, &hash); e != nil {
			rows.Close()
			return view, e
		}
		known++
		if known > MaxNativeBills || source != capture.SourceID || capture.OriginalHashes[id] != hash {
			rows.Close()
			return view, reject("BILL_SOURCE_INCOMPLETE", nil)
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return view, e
	}
	now := s.now()
	imported := []funds.ID{}
	for _, raw := range capture.Statements.Originals {
		bill, e := decodeNativeBillOriginal(raw, capture.Scope, s.Namespace, capture.ObservedAt)
		if e != nil {
			return view, e
		}
		hash := Hash(raw)
		var firstID string
		var firstAt time.Time
		e = tx.QueryRow(ctx, `SELECT id::text,observed_at FROM native_bill_captures WHERE environment_id=$1 AND user_id=$2 AND source_id=$3 AND convert_from(original,'UTF8')::jsonb->'original_hashes'->>$4=$5 ORDER BY observed_at,recorded_funds_version,id LIMIT 1`, p.EnvironmentID.String(), p.UserID.String(), capture.SourceID, bill.ID.String(), hash).Scan(&firstID, &firstAt)
		if e != nil {
			return view, e
		}
		reference := nativeBillReference(bill, raw)
		referenceRaw, _ := encode(reference)
		tag, e := tx.Exec(ctx, `INSERT INTO native_bill_originals(environment_id,user_id,id,source_id,external_key,statement_account_id,due_date,issued_at,final_total_cents,payee_id,original,original_hash,bank_reference_original,bank_reference_hash,first_capture_id,first_observed_at,materialized_at) VALUES($1,$2,$3,$4,$5,$6,$7::date,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17) ON CONFLICT(environment_id,user_id,id) DO NOTHING`, p.EnvironmentID.String(), p.UserID.String(), bill.ID.String(), capture.SourceID, bill.ExternalKey, bill.StatementAccountID.String(), bill.DueDate, bill.IssuedAt, bill.FinalTotal.Cents(), bill.PayeeID, raw, hash, referenceRaw, Hash(referenceRaw), firstID, firstAt, now)
		if e != nil {
			return view, e
		}
		if tag.RowsAffected() > 0 {
			imported = append(imported, bill.ID)
		}
	}
	// A new complete capture may resolve earlier interrupted captures only when
	// every earlier statement raw identity is present unchanged in the new one.
	rows, e = tx.Query(ctx, `SELECT c.id::text,c.original,c.original_hash::text FROM native_bill_captures c WHERE c.environment_id=$1 AND c.user_id=$2 AND NOT EXISTS(SELECT 1 FROM native_bill_capture_resolutions r WHERE r.environment_id=c.environment_id AND r.user_id=c.user_id AND r.capture_id=c.id) ORDER BY c.recorded_funds_version,c.id LIMIT 1001`, p.EnvironmentID.String(), p.UserID.String())
	if e != nil {
		return view, e
	}
	prior := []NativeBillCapture{}
	for rows.Next() {
		var id, hash string
		var raw []byte
		if e = rows.Scan(&id, &raw, &hash); e != nil {
			rows.Close()
			return view, e
		}
		var item NativeBillCapture
		if len(prior) >= 1000 {
			rows.Close()
			return view, reject("BILL_CAPTURE_CAPACITY", nil)
		}
		if Hash(raw) != hash || auditJSON(raw, &item) != nil || item.ID.String() != id || item.Scope != capture.Scope {
			rows.Close()
			return view, reject("BILL_CAPTURE_INVALID", nil)
		}
		if item.SourceID != capture.SourceID || item.ObservedAt.After(capture.ObservedAt) {
			rows.Close()
			return view, reject("BILL_NEWER_CAPTURE_PENDING", nil)
		}
		for billID, h := range item.OriginalHashes {
			if capture.OriginalHashes[billID] != h {
				rows.Close()
				return view, reject("BILL_SOURCE_CHANGED", nil)
			}
		}
		prior = append(prior, item)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return view, e
	}
	resolved := []funds.ID{}
	for _, item := range prior {
		resolution := nativeBillCaptureResolution{Protocol: "native-bill-capture-resolution-v1", Scope: capture.Scope, CaptureID: item.ID, ResultCaptureID: capture.ID, RecordedAt: now}
		raw, _ := encode(resolution)
		_, e = tx.Exec(ctx, `INSERT INTO native_bill_capture_resolutions(environment_id,user_id,capture_id,result_capture_id,original,original_hash,recorded_at) VALUES($1,$2,$3,$4,$5,$6,$7)`, p.EnvironmentID.String(), p.UserID.String(), item.ID.String(), capture.ID.String(), raw, Hash(raw), now)
		if e != nil {
			return view, e
		}
		resolved = append(resolved, item.ID)
	}
	timezone, e := templateScopedTimezone(ctx, tx, p, "")
	if e != nil {
		return view, e
	}
	forecast, e := s.refreshTemplateProtection(ctx, tx, p, timezone)
	if e != nil {
		return view, e
	}
	if forecast.ProtectionSourceVersion == nil {
		return view, reject("BILL_PROTECTION_PUBLICATION_REQUIRED", nil)
	}
	var protectionHash string
	e = tx.QueryRow(ctx, `SELECT original_hash::text FROM protection_snapshots WHERE environment_id=$1 AND user_id=$2 AND version=$3`, p.EnvironmentID.String(), p.UserID.String(), *forecast.ProtectionSourceVersion).Scan(&protectionHash)
	if e != nil {
		return view, e
	}
	sort.Slice(imported, func(i, j int) bool { return imported[i].String() < imported[j].String() })
	result := NativeBillImportResult{Protocol: "native-bill-import-result-v1", Scope: capture.Scope, CaptureID: capture.ID, ImportedBillIDs: imported, ResolvedCaptureIDs: resolved, FundsVersion: fv, SourceVersion: *forecast.ProtectionSourceVersion, ProtectionHash: protectionHash, RecordedAt: now}
	raw, e := encode(result)
	if e != nil {
		return view, e
	}
	_, e = tx.Exec(ctx, `INSERT INTO native_bill_import_results(environment_id,user_id,capture_id,original,original_hash,funds_version,source_version,protection_hash,recorded_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)`, p.EnvironmentID.String(), p.UserID.String(), capture.ID.String(), raw, Hash(raw), fv, *forecast.ProtectionSourceVersion, protectionHash, now)
	if e != nil {
		return view, e
	}
	event, e := newID()
	if e != nil {
		return view, e
	}
	_, e = tx.Exec(ctx, `INSERT INTO outbox(environment_id,user_id,id,aggregate_id,aggregate_version,kind,payload,created_at) VALUES($1,$2,$3,$4,1,'NATIVE_BILLS_IMPORTED',$5,$6)`, p.EnvironmentID.String(), p.UserID.String(), event.String(), capture.ID.String(), raw, now)
	if e != nil {
		return view, e
	}
	if s.Failpoint != nil {
		if e = s.Failpoint("native_bills_before_complete_commit"); e != nil {
			return view, e
		}
	}
	view.State = "IMPORTED"
	view.PendingReason = ""
	view.Result = &result
	return view, tx.Commit(ctx)
}
