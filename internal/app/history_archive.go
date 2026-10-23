package app

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	evidence "zhiyu.local/zhiyu-go/internal/audit"
	executionaudit "zhiyu.local/zhiyu-go/internal/execution/audit"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	history "zhiyu.local/zhiyu-go/internal/history/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const HistoryRecordProtocol = "zhiyu-history-record-v1"

type HistoryArchiveRequest struct {
	Bundle []byte `json:"bundle"`
}
type HistoryArchive struct {
	Protocol       string                    `json:"protocol"`
	ID             funds.ID                  `json:"id"`
	Scope          policy.TemplateOwnerScope `json:"scope"`
	SessionID      funds.ID                  `json:"session_id"`
	IdempotencyKey string                    `json:"idempotency_key"`
	RequestHash    string                    `json:"request_hash"`
	Mode           string                    `json:"mode"`
	SourceTrust    string                    `json:"source_trust"`
	Manifest       history.Manifest          `json:"manifest"`
	Verification   history.Verification      `json:"verification"`
	WireHash       string                    `json:"wire_hash"`
	StoredCount    int                       `json:"stored_count"`
	StoredBytes    int64                     `json:"stored_bytes"`
	CreatedAt      time.Time                 `json:"created_at"`
	BankAuthority  bool                      `json:"bank_authority"`
	Activated      bool                      `json:"activated"`
}

func historyRegistry() (*history.Registry, error) {
	r := history.NewRegistry()
	legacy := evidence.NewLegacyRegistry()
	if e := executionaudit.RegisterLegacySubjects(legacy); e != nil {
		return nil, e
	}
	e := r.Register(evidence.LegacySubjectSchema, func(o history.Object) (string, string) {
		_, _, err := evidence.DecodeLegacySubject(o.Original, legacy)
		return string(evidence.LegacyStatus(err)), "LEGACY_REGISTERED_SUBJECT_LAYOUT_ONLY"
	})
	return r, e
}
func historyKey(key string) bool {
	return len(key) > 0 && len(key) <= 128 && utf8.ValidString(key) && !strings.ContainsRune(key, 0)
}
func (s *Service) historyPrincipal(ctx context.Context, q queryRow, p Principal) error {
	env, e := s.environment(ctx, q, false)
	if e != nil {
		return e
	}
	if e = sameUser(p, env.ID); e != nil {
		return e
	}
	return s.human(ctx, q, p)
}
func (s *Service) historyByKey(ctx context.Context, q queryRow, p Principal, key string) (HistoryArchive, error) {
	var raw []byte
	var hash string
	e := q.QueryRow(ctx, `SELECT original,original_hash FROM history_archives WHERE environment_id=$1 AND user_id=$2 AND idempotency_key=$3`, p.EnvironmentID.String(), p.UserID.String(), key).Scan(&raw, &hash)
	if e != nil {
		return HistoryArchive{}, e
	}
	return decodeHistoryRecord(raw, hash, p)
}
func decodeHistoryRecord(raw []byte, hash string, p Principal) (HistoryArchive, error) {
	var out HistoryArchive
	if len(raw) > 8<<20 || Hash(raw) != hash || decode(raw, &out) != nil || out.Protocol != HistoryRecordProtocol || out.Scope != (policy.TemplateOwnerScope{EnvironmentID: p.EnvironmentID, UserID: p.UserID}) || out.BankAuthority || out.Activated {
		return HistoryArchive{}, reject("HISTORY_EVIDENCE_INVALID", nil)
	}
	return out, nil
}
func (s *Service) CreateHistoryArchive(ctx context.Context, p Principal, key string, in HistoryArchiveRequest) (HistoryArchive, error) {
	if !historyKey(key) {
		return HistoryArchive{}, reject("INVALID_IDEMPOTENCY_KEY", nil)
	}
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return HistoryArchive{}, e
	}
	bundle, e := history.DecodeBundle(in.Bundle)
	if e != nil {
		return HistoryArchive{}, reject("INVALID_HISTORY_ARCHIVE", e)
	}
	return s.saveHistoryArchive(ctx, p, key, Hash(in.Bundle), "SUPPLIED_HISTORY", bundle, in.Bundle)
}
func (s *Service) saveHistoryArchive(ctx context.Context, p Principal, key, requestHash, mode string, bundle history.Bundle, wire []byte) (HistoryArchive, error) {
	if !historyKey(key) || history.ValidateStructure(bundle) != nil || len(wire) > history.MaxWireBytes {
		return HistoryArchive{}, reject("INVALID_HISTORY_ARCHIVE", nil)
	}
	registry, e := historyRegistry()
	if e != nil {
		return HistoryArchive{}, e
	}
	verification := history.Verify(bundle, registry)
	tx, e := s.begin(ctx)
	if e != nil {
		return HistoryArchive{}, e
	}
	defer rollback(ctx, tx)
	if e = s.historyPrincipal(ctx, tx, p); e != nil {
		return HistoryArchive{}, e
	}
	old, e := s.historyByKey(ctx, tx, p, key)
	if e == nil {
		if old.RequestHash != requestHash || old.Mode != mode {
			return HistoryArchive{}, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		return old, tx.Commit(ctx)
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return HistoryArchive{}, e
	}
	id, e := newID()
	if e != nil {
		return HistoryArchive{}, e
	}
	trust := "DECLARED_UNVERIFIED"
	if mode == "AUTHENTICATED_CURRENT_READSET" {
		trust = "LOCAL_AUTHENTICATED_READSET"
	}
	out := HistoryArchive{Protocol: HistoryRecordProtocol, ID: id, Scope: policy.TemplateOwnerScope{EnvironmentID: p.EnvironmentID, UserID: p.UserID}, SessionID: p.SessionID, IdempotencyKey: key, RequestHash: requestHash, Mode: mode, SourceTrust: trust, Manifest: bundle.Manifest, Verification: verification, WireHash: Hash(wire), StoredCount: len(bundle.Objects), CreatedAt: s.now()}
	for _, o := range bundle.Objects {
		out.StoredBytes += int64(len(o.Original))
	}
	original, e := encode(out)
	if e != nil || len(original) > 8<<20 {
		return HistoryArchive{}, reject("HISTORY_CAPACITY_EXCEEDED", e)
	}
	manifest, e := encode(bundle.Manifest)
	if e != nil {
		return HistoryArchive{}, e
	}
	tag, e := tx.Exec(ctx, `INSERT INTO history_archives(environment_id,user_id,id,session_id,idempotency_key,request_hash,mode,original,original_hash,manifest_original,manifest_hash,root_hash,wire_original,wire_hash,object_count,total_bytes,stored_count,stored_bytes,verification_status,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20) ON CONFLICT(environment_id,user_id,idempotency_key) DO NOTHING`, p.EnvironmentID.String(), p.UserID.String(), id.String(), p.SessionID.String(), key, requestHash, mode, original, Hash(original), manifest, Hash(manifest), bundle.Manifest.RootHash, wire, out.WireHash, len(bundle.Manifest.Objects), bundle.Manifest.TotalBytes, out.StoredCount, out.StoredBytes, verification.Status, out.CreatedAt)
	if e != nil {
		return HistoryArchive{}, e
	}
	if tag.RowsAffected() == 0 {
		old, e = s.historyByKey(ctx, tx, p, key)
		if e != nil {
			return HistoryArchive{}, e
		}
		if old.RequestHash != requestHash || old.Mode != mode {
			return HistoryArchive{}, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		return old, tx.Commit(ctx)
	}
	byID := map[string]history.Object{}
	for _, o := range bundle.Objects {
		byID[o.ID] = o
	}
	for i, ref := range bundle.Manifest.Objects {
		o, ok := byID[ref.ID]
		if !ok {
			continue
		}
		meta, e := json.Marshal(o.ObjectRef)
		if e != nil {
			return HistoryArchive{}, e
		}
		_, e = tx.Exec(ctx, `INSERT INTO history_archive_objects(environment_id,user_id,archive_id,id,ordinal,source_table,row_key,representation,protocol,metadata_original,metadata_hash,original,original_hash) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`, p.EnvironmentID.String(), p.UserID.String(), id.String(), o.ID, i+1, o.Table, o.RowKey, o.Representation, o.Protocol, meta, Hash(meta), o.Original, o.RawHash)
		if e != nil {
			return HistoryArchive{}, e
		}
	}
	return out, tx.Commit(ctx)
}
func (s *Service) HistoryArchive(ctx context.Context, p Principal, id funds.ID) (HistoryArchive, error) {
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return HistoryArchive{}, e
	}
	var raw []byte
	var hash string
	e := s.Pool.QueryRow(ctx, `SELECT original,original_hash FROM history_archives WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&raw, &hash)
	if errors.Is(e, pgx.ErrNoRows) {
		return HistoryArchive{}, reject("NOT_FOUND", nil)
	}
	if e != nil {
		return HistoryArchive{}, e
	}
	out, e := decodeHistoryRecord(raw, hash, p)
	if e == nil && out.ID != id {
		e = reject("HISTORY_EVIDENCE_INVALID", nil)
	}
	return out, e
}
func (s *Service) ExportHistoryArchive(ctx context.Context, p Principal, id funds.ID) ([]byte, error) {
	record, e := s.HistoryArchive(ctx, p, id)
	if e != nil {
		return nil, e
	}
	var raw []byte
	var hash string
	e = s.Pool.QueryRow(ctx, `SELECT wire_original,wire_hash FROM history_archives WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&raw, &hash)
	if e != nil {
		return nil, e
	}
	if Hash(raw) != hash || hash != record.WireHash {
		return nil, reject("HISTORY_EVIDENCE_INVALID", nil)
	}
	bundle, e := history.DecodeBundle(raw)
	if e != nil || history.ValidateStructure(bundle) != nil || bundle.Manifest.RootHash != record.Manifest.RootHash {
		return nil, reject("HISTORY_EVIDENCE_INVALID", e)
	}
	return raw, nil
}
func (s *Service) HistoryArchiveObject(ctx context.Context, p Principal, archiveID funds.ID, objectID string) (history.Object, error) {
	if !nativeActionHash(objectID) {
		return history.Object{}, reject("INVALID_HISTORY_OBJECT_ID", nil)
	}
	if _, e := s.HistoryArchive(ctx, p, archiveID); e != nil {
		return history.Object{}, e
	}
	var meta, raw []byte
	var metaHash, rawHash string
	e := s.Pool.QueryRow(ctx, `SELECT metadata_original,metadata_hash,original,original_hash FROM history_archive_objects WHERE environment_id=$1 AND user_id=$2 AND archive_id=$3 AND id=$4`, p.EnvironmentID.String(), p.UserID.String(), archiveID.String(), objectID).Scan(&meta, &metaHash, &raw, &rawHash)
	if errors.Is(e, pgx.ErrNoRows) {
		return history.Object{}, reject("NOT_FOUND", nil)
	}
	if e != nil {
		return history.Object{}, e
	}
	var ref history.ObjectRef
	if Hash(meta) != metaHash || Hash(raw) != rawHash || decode(meta, &ref) != nil || ref.ID != objectID || ref.RawHash != rawHash || ref.Bytes != len(raw) {
		return history.Object{}, reject("HISTORY_EVIDENCE_INVALID", nil)
	}
	return history.Object{ObjectRef: ref, Original: raw}, nil
}
