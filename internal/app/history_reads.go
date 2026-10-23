package app

import (
	"context"
	"errors"
	"github.com/jackc/pgx/v5"
	"time"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

type HistoryArchiveSummary struct {
	ID            funds.ID  `json:"id"`
	Mode          string    `json:"mode"`
	Status        string    `json:"status"`
	Assurance     string    `json:"assurance"`
	RootHash      string    `json:"root_hash"`
	StoredCount   int       `json:"stored_count"`
	StoredBytes   int64     `json:"stored_bytes"`
	CreatedAt     time.Time `json:"created_at"`
	Activated     bool      `json:"activated"`
	BankAuthority bool      `json:"bank_authority"`
}
type HistoryArchiveList struct {
	Items   []HistoryArchiveSummary `json:"items"`
	HasMore bool                    `json:"has_more"`
}
type HistoryImportList struct {
	Items   []HistoryImport `json:"items"`
	HasMore bool            `json:"has_more"`
}

func (s *Service) HistoryArchiveByKey(ctx context.Context, p Principal, key string) (HistoryArchive, error) {
	if !historyKey(key) {
		return HistoryArchive{}, reject("INVALID_IDEMPOTENCY_KEY", nil)
	}
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return HistoryArchive{}, e
	}
	out, e := s.historyByKey(ctx, s.Pool, p, key)
	if errors.Is(e, pgx.ErrNoRows) {
		e = reject("NOT_FOUND", nil)
	}
	return out, e
}
func (s *Service) HistoryImportByKey(ctx context.Context, p Principal, key string) (HistoryImport, error) {
	if !historyKey(key) {
		return HistoryImport{}, reject("INVALID_IDEMPOTENCY_KEY", nil)
	}
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return HistoryImport{}, e
	}
	out, e := historyImportLoad(ctx, s.Pool, p, funds.ID{}, key, false)
	if errors.Is(e, pgx.ErrNoRows) {
		e = reject("NOT_FOUND", nil)
	}
	return out, e
}
func (s *Service) ListHistoryArchives(ctx context.Context, p Principal) (HistoryArchiveList, error) {
	out := HistoryArchiveList{Items: []HistoryArchiveSummary{}}
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return out, e
	}
	rows, e := s.Pool.Query(ctx, `SELECT id::text FROM history_archives WHERE environment_id=$1 AND user_id=$2 ORDER BY created_at DESC,id DESC LIMIT 21`, p.EnvironmentID.String(), p.UserID.String())
	if e != nil {
		return out, e
	}
	ids := []funds.ID{}
	for rows.Next() {
		var text string
		if e = rows.Scan(&text); e != nil {
			rows.Close()
			return out, e
		}
		id, x := funds.ParseID(text)
		if x != nil {
			rows.Close()
			return out, x
		}
		ids = append(ids, id)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return out, e
	}
	out.HasMore = len(ids) > 20
	for _, id := range ids[:min(len(ids), 20)] {
		r, e := s.HistoryArchive(ctx, p, id)
		if e != nil {
			return out, e
		}
		out.Items = append(out.Items, HistoryArchiveSummary{ID: r.ID, Mode: r.Mode, Status: r.Verification.Status, Assurance: r.Verification.Assurance, RootHash: r.Manifest.RootHash, StoredCount: r.StoredCount, StoredBytes: r.StoredBytes, CreatedAt: r.CreatedAt})
	}
	return out, nil
}
func (s *Service) ListHistoryImports(ctx context.Context, p Principal) (HistoryImportList, error) {
	out := HistoryImportList{Items: []HistoryImport{}}
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return out, e
	}
	rows, e := s.Pool.Query(ctx, `SELECT id::text FROM history_imports WHERE environment_id=$1 AND user_id=$2 ORDER BY updated_at DESC,id DESC LIMIT 21`, p.EnvironmentID.String(), p.UserID.String())
	if e != nil {
		return out, e
	}
	ids := []funds.ID{}
	for rows.Next() {
		var text string
		if e = rows.Scan(&text); e != nil {
			rows.Close()
			return out, e
		}
		id, x := funds.ParseID(text)
		if x != nil {
			rows.Close()
			return out, x
		}
		ids = append(ids, id)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return out, e
	}
	out.HasMore = len(ids) > 20
	for _, id := range ids[:min(len(ids), 20)] {
		r, e := s.HistoryImport(ctx, p, id)
		if e != nil {
			return out, e
		}
		out.Items = append(out.Items, r)
	}
	return out, nil
}
