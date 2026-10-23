package app

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	history "zhiyu.local/zhiyu-go/internal/history/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const HistoryImportProtocol = "zhiyu-passive-history-import-v1"

type HistoryImportRequest struct {
	ArchiveID           funds.ID        `json:"archive_id"`
	Mapping             history.Mapping `json:"mapping"`
	ReviewedHash        string          `json:"reviewed_hash"`
	PassiveOnlyReviewed bool            `json:"passive_only_reviewed"`
}
type HistoryImportOriginal struct {
	Protocol       string                    `json:"protocol"`
	ID             funds.ID                  `json:"id"`
	Scope          policy.TemplateOwnerScope `json:"scope"`
	ArchiveID      funds.ID                  `json:"archive_id"`
	SessionID      funds.ID                  `json:"session_id"`
	IdempotencyKey string                    `json:"idempotency_key"`
	RequestHash    string                    `json:"request_hash"`
	MappingHash    string                    `json:"mapping_hash"`
	ReviewedHash   string                    `json:"reviewed_hash"`
	SourceStatus   string                    `json:"source_status"`
	CreatedAt      time.Time                 `json:"created_at"`
	BankAuthority  bool                      `json:"bank_authority"`
	Activated      bool                      `json:"activated"`
}
type HistoryImport struct {
	Original   HistoryImportOriginal `json:"original"`
	Mapping    history.Mapping       `json:"mapping"`
	State      string                `json:"state"`
	Paused     bool                  `json:"paused"`
	Cursor     int                   `json:"cursor"`
	Generation uint64                `json:"generation"`
	Attempts   uint64                `json:"attempts"`
	UpdatedAt  time.Time             `json:"updated_at"`
	Activated  bool                  `json:"activated"`
}

type HistoryMappingPreview struct {
	Mapping       history.Mapping `json:"mapping"`
	ReviewedHash  string          `json:"reviewed_hash"`
	SourceStatus  string          `json:"source_status"`
	Assurance     string          `json:"assurance"`
	PassiveOnly   bool            `json:"passive_only"`
	BankAuthority bool            `json:"bank_authority"`
	Activated     bool            `json:"activated"`
}

// Entries are explicit user aliases, not a proposal to rewrite source objects.
// The server fills every source identity from the pinned immutable archive.
func (s *Service) PreviewHistoryImport(ctx context.Context, p Principal, id funds.ID, entries []history.MappingEntry) (HistoryMappingPreview, error) {
	record, e := s.HistoryArchive(ctx, p, id)
	if e != nil {
		return HistoryMappingPreview{}, e
	}
	wire, e := s.ExportHistoryArchive(ctx, p, id)
	if e != nil {
		return HistoryMappingPreview{}, e
	}
	bundle, e := history.DecodeBundle(wire)
	if e != nil {
		return HistoryMappingPreview{}, e
	}
	if record.StoredCount != len(bundle.Manifest.Objects) || record.StoredBytes != bundle.Manifest.TotalBytes {
		return HistoryMappingPreview{}, reject("HISTORY_ARCHIVE_MEMBERS_MISSING", nil)
	}
	source := record.Manifest.Source
	mapping := history.Mapping{Protocol: history.MappingProtocol, ArchiveRootHash: record.Manifest.RootHash, SourceEnvironmentID: source.EnvironmentID, SourceUserID: source.UserID, SourceNamespace: source.Namespace, DestinationEnvironmentID: p.EnvironmentID.String(), DestinationUserID: p.UserID.String(), Entries: append([]history.MappingEntry{}, entries...)}
	if history.ValidateMapping(bundle, mapping) != nil {
		return HistoryMappingPreview{}, reject("INVALID_HISTORY_MAPPING", nil)
	}
	hash, e := history.MappingHash(mapping)
	if e != nil {
		return HistoryMappingPreview{}, e
	}
	return HistoryMappingPreview{Mapping: mapping, ReviewedHash: hash, SourceStatus: record.Verification.Status, Assurance: record.Verification.Assurance, PassiveOnly: true}, nil
}

func historyImportLoad(ctx context.Context, q queryRow, p Principal, id funds.ID, key string, lock bool) (HistoryImport, error) {
	query := `SELECT original,original_hash,mapping_original,mapping_hash,state,paused,cursor,generation,attempts,updated_at,activated FROM history_imports WHERE environment_id=$1 AND user_id=$2 AND `
	arg := any(id.String())
	if key != "" {
		query += "idempotency_key=$3"
		arg = key
	} else {
		query += "id=$3"
	}
	if lock {
		query += " FOR UPDATE"
	}
	var out HistoryImport
	var raw, mapping []byte
	var rawHash, mappingHash string
	e := q.QueryRow(ctx, query, p.EnvironmentID.String(), p.UserID.String(), arg).Scan(&raw, &rawHash, &mapping, &mappingHash, &out.State, &out.Paused, &out.Cursor, &out.Generation, &out.Attempts, &out.UpdatedAt, &out.Activated)
	if e != nil {
		return out, e
	}
	if Hash(raw) != rawHash || Hash(mapping) != mappingHash || decode(raw, &out.Original) != nil || decode(mapping, &out.Mapping) != nil || out.Original.Protocol != HistoryImportProtocol || out.Original.Scope != (policy.TemplateOwnerScope{EnvironmentID: p.EnvironmentID, UserID: p.UserID}) || out.Original.MappingHash != mappingHash || out.Original.BankAuthority || out.Original.Activated || out.Activated {
		return HistoryImport{}, reject("HISTORY_EVIDENCE_INVALID", nil)
	}
	if key == "" && out.Original.ID != id {
		return HistoryImport{}, reject("HISTORY_EVIDENCE_INVALID", nil)
	}
	review, e := history.MappingHash(out.Mapping)
	if e != nil || review != out.Original.ReviewedHash || out.Mapping.Activated || out.Mapping.BankAuthority {
		return HistoryImport{}, reject("HISTORY_EVIDENCE_INVALID", e)
	}
	return out, nil
}
func (s *Service) PrepareHistoryImport(ctx context.Context, p Principal, key string, in HistoryImportRequest) (HistoryImport, error) {
	if !historyKey(key) || !in.PassiveOnlyReviewed {
		return HistoryImport{}, reject("HISTORY_PASSIVE_REVIEW_REQUIRED", nil)
	}
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return HistoryImport{}, e
	}
	request, e := encode(in)
	if e != nil {
		return HistoryImport{}, e
	}
	requestHash := Hash(request)
	old, e := historyImportLoad(ctx, s.Pool, p, funds.ID{}, key, false)
	if e == nil {
		if old.Original.RequestHash != requestHash {
			return HistoryImport{}, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		return old, nil
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return HistoryImport{}, e
	}
	wire, e := s.ExportHistoryArchive(ctx, p, in.ArchiveID)
	if e != nil {
		return HistoryImport{}, e
	}
	bundle, e := history.DecodeBundle(wire)
	if e != nil {
		return HistoryImport{}, e
	}
	record, e := s.HistoryArchive(ctx, p, in.ArchiveID)
	if e != nil {
		return HistoryImport{}, e
	}
	if record.StoredCount != len(bundle.Manifest.Objects) || record.StoredBytes != bundle.Manifest.TotalBytes {
		return HistoryImport{}, reject("HISTORY_ARCHIVE_MEMBERS_MISSING", nil)
	}
	if history.ValidateMapping(bundle, in.Mapping) != nil || in.Mapping.DestinationEnvironmentID != p.EnvironmentID.String() || in.Mapping.DestinationUserID != p.UserID.String() {
		return HistoryImport{}, reject("INVALID_HISTORY_MAPPING", nil)
	}
	reviewHash, e := history.MappingHash(in.Mapping)
	if e != nil || reviewHash != in.ReviewedHash {
		return HistoryImport{}, reject("HISTORY_MAPPING_REVIEW_MISMATCH", e)
	}
	mapping, e := encode(in.Mapping)
	if e != nil {
		return HistoryImport{}, e
	}
	id, e := newID()
	if e != nil {
		return HistoryImport{}, e
	}
	now := s.now()
	original := HistoryImportOriginal{Protocol: HistoryImportProtocol, ID: id, Scope: policy.TemplateOwnerScope{EnvironmentID: p.EnvironmentID, UserID: p.UserID}, ArchiveID: in.ArchiveID, SessionID: p.SessionID, IdempotencyKey: key, RequestHash: requestHash, MappingHash: Hash(mapping), ReviewedHash: reviewHash, SourceStatus: record.Verification.Status, CreatedAt: now}
	raw, e := encode(original)
	if e != nil {
		return HistoryImport{}, e
	}
	state := "READY"
	if len(in.Mapping.Entries) == 0 {
		state = "COMPLETED"
	}
	tx, e := s.begin(ctx)
	if e != nil {
		return HistoryImport{}, e
	}
	defer rollback(ctx, tx)
	if e = s.historyPrincipal(ctx, tx, p); e != nil {
		return HistoryImport{}, e
	}
	tag, e := tx.Exec(ctx, `INSERT INTO history_imports(environment_id,user_id,id,archive_id,session_id,idempotency_key,request_hash,original,original_hash,mapping_original,mapping_hash,reviewed_hash,source_status,state,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$15) ON CONFLICT(environment_id,user_id,idempotency_key) DO NOTHING`, p.EnvironmentID.String(), p.UserID.String(), id.String(), in.ArchiveID.String(), p.SessionID.String(), key, requestHash, raw, Hash(raw), mapping, Hash(mapping), reviewHash, record.Verification.Status, state, now)
	if e != nil {
		return HistoryImport{}, e
	}
	if tag.RowsAffected() == 0 {
		old, e = historyImportLoad(ctx, tx, p, funds.ID{}, key, false)
		if e != nil {
			return HistoryImport{}, e
		}
		if old.Original.RequestHash != requestHash {
			return HistoryImport{}, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		return old, tx.Commit(ctx)
	}
	out := HistoryImport{Original: original, Mapping: in.Mapping, State: state, UpdatedAt: now}
	return out, tx.Commit(ctx)
}
func (s *Service) HistoryImport(ctx context.Context, p Principal, id funds.ID) (HistoryImport, error) {
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return HistoryImport{}, e
	}
	out, e := historyImportLoad(ctx, s.Pool, p, id, "", false)
	if errors.Is(e, pgx.ErrNoRows) {
		return out, reject("NOT_FOUND", nil)
	}
	return out, e
}

// The import row lock serializes a bounded local-only transaction. There is no
// bank I/O, financial lock or external Worker lease hidden in this method.
func (s *Service) HistoryImportStep(ctx context.Context, p Principal, id funds.ID, batch int) (HistoryImport, error) {
	if batch < 1 || batch > 100 {
		return HistoryImport{}, reject("HISTORY_BATCH_CAPACITY", nil)
	}
	tx, e := s.begin(ctx)
	if e != nil {
		return HistoryImport{}, e
	}
	defer rollback(ctx, tx)
	if e = s.historyPrincipal(ctx, tx, p); e != nil {
		return HistoryImport{}, e
	}
	out, e := historyImportLoad(ctx, tx, p, id, "", true)
	if e != nil {
		return HistoryImport{}, e
	}
	if out.Paused || out.State == "COMPLETED" {
		return out, tx.Commit(ctx)
	}
	end := out.Cursor + batch
	if end > len(out.Mapping.Entries) {
		end = len(out.Mapping.Entries)
	}
	for i := out.Cursor; i < end; i++ {
		entry := out.Mapping.Entries[i]
		_, e = tx.Exec(ctx, `INSERT INTO history_passive_rows(environment_id,user_id,import_id,archive_id,object_id,ordinal,destination_id,source_raw_hash) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, p.EnvironmentID.String(), p.UserID.String(), id.String(), out.Original.ArchiveID.String(), entry.ObjectID, i+1, entry.DestinationID, entry.SourceRawHash)
		if e != nil {
			return HistoryImport{}, e
		}
	}
	if s.Failpoint != nil {
		if e = s.Failpoint("history_after_passive_rows_before_checkpoint"); e != nil {
			return HistoryImport{}, e
		}
	}
	out.Cursor = end
	out.Generation++
	out.Attempts++
	out.UpdatedAt = s.now()
	out.State = "RUNNING"
	if end == len(out.Mapping.Entries) {
		out.State = "COMPLETED"
	}
	_, e = tx.Exec(ctx, `UPDATE history_imports SET cursor=$4,state=$5,generation=$6,attempts=$7,updated_at=$8 WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String(), out.Cursor, out.State, out.Generation, out.Attempts, out.UpdatedAt)
	if e != nil {
		return HistoryImport{}, e
	}
	return out, tx.Commit(ctx)
}
func (s *Service) SetHistoryImportPaused(ctx context.Context, p Principal, id funds.ID, paused bool) (HistoryImport, error) {
	tx, e := s.begin(ctx)
	if e != nil {
		return HistoryImport{}, e
	}
	defer rollback(ctx, tx)
	if e = s.historyPrincipal(ctx, tx, p); e != nil {
		return HistoryImport{}, e
	}
	out, e := historyImportLoad(ctx, tx, p, id, "", true)
	if e != nil {
		return HistoryImport{}, e
	}
	if out.State == "COMPLETED" || out.Paused == paused {
		return out, tx.Commit(ctx)
	}
	out.Paused = paused
	out.UpdatedAt = s.now()
	_, e = tx.Exec(ctx, `UPDATE history_imports SET paused=$4,updated_at=$5 WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String(), paused, out.UpdatedAt)
	if e != nil {
		return HistoryImport{}, e
	}
	return out, tx.Commit(ctx)
}
