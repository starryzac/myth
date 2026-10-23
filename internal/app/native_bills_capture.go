package app

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/audit"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type NativeBillBasis struct {
	FundsVersion     uint64 `json:"funds_version"`
	SourceVersion    uint64 `json:"source_version"`
	WriterGeneration uint64 `json:"writer_generation"`
	ControlVersion   uint64 `json:"control_version"`
}
type NativeBillCapture struct {
	Protocol             string                      `json:"protocol"`
	ID                   funds.ID                    `json:"id"`
	Scope                policy.TemplateOwnerScope   `json:"scope"`
	SourceID             string                      `json:"source_id"`
	ActorKind            string                      `json:"actor_kind"`
	SessionID            funds.ID                    `json:"session_id"`
	IdempotencyKey       string                      `json:"idempotency_key"`
	RequestHash          string                      `json:"request_hash"`
	Basis                NativeBillBasis             `json:"basis"`
	RecordedFundsVersion uint64                      `json:"recorded_funds_version"`
	Statements           NativeBillStatementSnapshot `json:"statements"`
	Bank                 bankv2.LiabilitySnapshot    `json:"bank"`
	OriginalHashes       map[string]string           `json:"original_hashes"`
	ObservedAt           time.Time                   `json:"observed_at"`
	BankAuthority        bool                        `json:"bank_authority"`
}

// Keep a final slot for a fresh complete observation that can close older
// frozen bank reservations. Original-key replay and Resume never use a slot.
func nativeBillCaptureAdmission(pending int, bank bankv2.LiabilitySnapshot, paid map[string]funds.Money) error {
	if pending < 0 || pending >= 1000 {
		return reject("BILL_CAPTURE_CAPACITY", nil)
	}
	if pending < 999 {
		return nil
	}
	for _, row := range bank.Liabilities {
		if row.Original.Kind == "BILL" && (!row.Reserved.IsZero() || row.Paid != paid[row.Original.ID]) {
			return reject("BILL_CAPTURE_RECOVERY_SLOT_REQUIRED", nil)
		}
	}
	return nil
}

// NativeBillFileReader is configured by the local operator. Its path is never
// accepted from HTTP, a model tool or an import body. Malformed files produce a
// closed error and are never copied to an error message or log.
type NativeBillFileReader struct{ path, sourceID string }

func NewNativeBillFileReader(path, sourceID string) (*NativeBillFileReader, error) {
	if !filepath.IsAbs(path) || !historyKey(sourceID) {
		return nil, reject("BILL_SOURCE_NOT_CONFIGURED", nil)
	}
	return &NativeBillFileReader{filepath.Clean(path), sourceID}, nil
}
func (r *NativeBillFileReader) Bills(ctx context.Context, namespace, user string) (NativeBillStatementSnapshot, error) {
	var out NativeBillStatementSnapshot
	if r == nil || ctx.Err() != nil {
		return out, reject("BILL_SOURCE_UNAVAILABLE", nil)
	}
	file, e := os.Open(r.path)
	if e != nil {
		return out, reject("BILL_SOURCE_UNAVAILABLE", nil)
	}
	defer file.Close()
	info, e := file.Stat()
	if e != nil || !info.Mode().IsRegular() || info.Size() > 4<<20 {
		return out, reject("BILL_SOURCE_INVALID", nil)
	}
	raw, e := io.ReadAll(io.LimitReader(file, (4<<20)+1))
	if e != nil || len(raw) > 4<<20 {
		return out, reject("BILL_SOURCE_INVALID", nil)
	}
	if _, e = audit.ParseRawObject(raw); e != nil {
		return out, reject("BILL_SOURCE_INVALID", nil)
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(&out) != nil || d.Decode(new(any)) != io.EOF || out.SourceID != r.sourceID || out.Namespace != namespace || out.UserID != user {
		return NativeBillStatementSnapshot{}, reject("BILL_SOURCE_INVALID", nil)
	}
	return out, nil
}
func validateNativeBillStatements(statements NativeBillStatementSnapshot, bank bankv2.LiabilitySnapshot, scope policy.TemplateOwnerScope, namespace, sourceID string, observedAt time.Time) (map[string]string, error) {
	if statements.Protocol != NativeBillCaptureProtocol+"/statements" || statements.Namespace != namespace || statements.UserID != scope.UserID.String() || statements.SourceID != sourceID || !statements.Complete || statements.Originals == nil || len(statements.Originals) > MaxNativeBills || bank.Validate() != nil || bank.Namespace != namespace || bank.UserID != scope.UserID.String() || bank.CapturedAt.After(observedAt.Add(time.Second)) {
		return nil, reject("BILL_SOURCE_INCOMPLETE", nil)
	}
	bankRows := map[string]bankv2.LiabilityState{}
	for _, row := range bank.Liabilities {
		if row.Original.Kind == "BILL" {
			bankRows[row.Original.ID] = row
		}
	}
	hashes := map[string]string{}
	externalKeys := map[string]bool{}
	total := 0
	for _, raw := range statements.Originals {
		total += len(raw)
		if total > 2<<20 {
			return nil, reject("BILL_SOURCE_CAPACITY", nil)
		}
		bill, e := decodeNativeBillOriginal(raw, scope, namespace, observedAt)
		if e != nil {
			return nil, e
		}
		key := bill.ID.String()
		if _, ok := hashes[key]; ok || externalKeys[bill.ExternalKey] {
			return nil, reject("BILL_SOURCE_DUPLICATE", nil)
		}
		externalKeys[bill.ExternalKey] = true
		reference := nativeBillReference(bill, raw)
		row, ok := bankRows[reference.ID]
		if !ok || reference.Validate() != nil || row.Original != reference {
			return nil, reject("BILL_BANK_ORIGINAL_MISMATCH", nil)
		}
		hashes[key] = Hash(raw)
		delete(bankRows, reference.ID)
	}
	if len(bankRows) != 0 {
		return nil, reject("BILL_SOURCE_INCOMPLETE", nil)
	}
	return hashes, nil
}
func loadNativeBillCapture(ctx context.Context, q queryRow, p Principal, id funds.ID, key string) (NativeBillCapture, error) {
	query := `SELECT original,original_hash FROM native_bill_captures WHERE environment_id=$1 AND user_id=$2 AND `
	value := any(id.String())
	if key != "" {
		query += "idempotency_key=$3"
		value = key
	} else {
		query += "id=$3"
	}
	var raw []byte
	var hash string
	var out NativeBillCapture
	if e := q.QueryRow(ctx, query, p.EnvironmentID.String(), p.UserID.String(), value).Scan(&raw, &hash); e != nil {
		return out, e
	}
	if Hash(raw) != hash || auditJSON(raw, &out) != nil || out.Protocol != NativeBillCaptureProtocol || out.Scope != (policy.TemplateOwnerScope{EnvironmentID: p.EnvironmentID, UserID: p.UserID}) || out.BankAuthority || out.RecordedFundsVersion != out.Basis.FundsVersion+1 {
		return NativeBillCapture{}, reject("BILL_CAPTURE_INVALID", nil)
	}
	hashes, e := validateNativeBillStatements(out.Statements, out.Bank, out.Scope, out.Bank.Namespace, out.SourceID, out.ObservedAt)
	a, _ := encode(hashes)
	b, _ := encode(out.OriginalHashes)
	if e != nil || !bytes.Equal(a, b) {
		return NativeBillCapture{}, reject("BILL_CAPTURE_INVALID", e)
	}
	return out, nil
}
func (s *Service) captureNativeBills(ctx context.Context, p Principal, key, sourceID string, sources NativeBillSources) (NativeBillCapture, error) {
	var zero NativeBillCapture
	if !historyKey(key) || !historyKey(sourceID) || len(sources.Readers) != 1 {
		return zero, reject("BILL_SOURCE_NOT_CONFIGURED", nil)
	}
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return zero, e
	}
	reader, ok := sources.Readers[sourceID]
	if !ok || reader == nil {
		return zero, reject("BILL_SOURCE_NOT_CONFIGURED", nil)
	}
	requestHash := Hash([]byte("native-bill-import-source-v1\x00" + sourceID))
	existing, e := loadNativeBillCapture(ctx, s.Pool, p, funds.ID{}, key)
	if e == nil {
		if existing.RequestHash != requestHash {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		return existing, nil
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return zero, e
	}
	bankReader, ok := s.NativeBank.(nativePaymentBankReader)
	if !ok {
		return zero, reject("BANK_UNAVAILABLE", nil)
	}
	statements, e := reader.Bills(ctx, s.Namespace, p.UserID.String())
	if e != nil {
		return zero, reject("BILL_SOURCE_UNAVAILABLE", e)
	}
	bank, e := bankReader.Liabilities(ctx, s.Namespace, p.UserID.String())
	if e != nil {
		return zero, reject("BANK_UNAVAILABLE", e)
	}
	tx, e := s.begin(ctx)
	if e != nil {
		return zero, e
	}
	defer rollback(ctx, tx)
	env, e := s.environment(ctx, tx, true)
	if e != nil {
		return zero, e
	}
	if e = sameUser(p, env.ID); e != nil {
		return zero, e
	}
	fv, sv, e := lockUser(ctx, tx, p)
	if e != nil {
		return zero, e
	}
	if e = s.human(ctx, tx, p); e != nil {
		return zero, e
	}
	existing, e = loadNativeBillCapture(ctx, tx, p, funds.ID{}, key)
	if e == nil {
		if existing.RequestHash != requestHash {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		return existing, tx.Commit(ctx)
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return zero, e
	}
	now := s.now()
	if now.Sub(bank.CapturedAt) > 30*time.Second {
		return zero, reject("BANK_SNAPSHOT_STALE", nil)
	}
	scope := policy.TemplateOwnerScope{EnvironmentID: p.EnvironmentID, UserID: p.UserID}
	hashes, e := validateNativeBillStatements(statements, bank, scope, s.Namespace, sourceID, now)
	if e != nil {
		return zero, e
	}
	var pending int
	if e = tx.QueryRow(ctx, `SELECT count(*) FROM (SELECT 1 FROM native_bill_captures c WHERE c.environment_id=$1 AND c.user_id=$2 AND NOT EXISTS(SELECT 1 FROM native_bill_capture_resolutions r WHERE r.environment_id=c.environment_id AND r.user_id=c.user_id AND r.capture_id=c.id) LIMIT 1001) pending`, p.EnvironmentID.String(), p.UserID.String()).Scan(&pending); e != nil {
		return zero, e
	}
	var paid map[string]funds.Money
	if pending >= 999 {
		paid, e = nativeBillPaidRows(ctx, tx, p, now)
		if e != nil {
			return zero, e
		}
	}
	if e = nativeBillCaptureAdmission(pending, bank, paid); e != nil {
		return zero, e
	}
	id, e := newID()
	if e != nil {
		return zero, e
	}
	out := NativeBillCapture{Protocol: NativeBillCaptureProtocol, ID: id, Scope: scope, SourceID: sourceID, ActorKind: p.Kind, SessionID: p.SessionID, IdempotencyKey: key, RequestHash: requestHash, Basis: NativeBillBasis{fv, sv, env.WriterGeneration, env.ControlVersion}, RecordedFundsVersion: fv + 1, Statements: statements, Bank: bank, OriginalHashes: hashes, ObservedAt: now}
	raw, e := encode(out)
	if e != nil || len(raw) > 4<<20 {
		return zero, reject("BILL_SOURCE_CAPACITY", e)
	}
	if _, e = tx.Exec(ctx, `UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2`, p.EnvironmentID.String(), p.UserID.String()); e != nil {
		return zero, e
	}
	_, e = tx.Exec(ctx, `INSERT INTO native_bill_captures(environment_id,user_id,id,source_id,actor_kind,session_id,idempotency_key,request_hash,original,original_hash,recorded_funds_version,observed_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`, p.EnvironmentID.String(), p.UserID.String(), id.String(), sourceID, p.Kind, p.SessionID.String(), key, requestHash, raw, Hash(raw), fv+1, now)
	if e != nil {
		return zero, e
	}
	return out, tx.Commit(ctx)
}
