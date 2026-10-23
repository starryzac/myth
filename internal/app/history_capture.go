package app

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"

	"github.com/jackc/pgx/v5"
	history "zhiyu.local/zhiyu-go/internal/history/domain"
)

// CaptureHistoryCurrent reads the registered authoritative set in one RR
// snapshot. It does not claim a complete financial audit, wire provenance,
// source writer ownership or readiness to activate an imported environment.
func (s *Service) CaptureHistoryCurrent(ctx context.Context, p Principal, key string) (HistoryArchive, error) {
	if !historyKey(key) {
		return HistoryArchive{}, reject("INVALID_IDEMPOTENCY_KEY", nil)
	}
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return HistoryArchive{}, e
	}
	requestHash := Hash([]byte("authenticated-current-history-readset-v1\x00" + history.CatalogHash()))
	old, e := s.historyByKey(ctx, s.Pool, p, key)
	if e == nil {
		if old.Mode != "AUTHENTICATED_CURRENT_READSET" || old.RequestHash != requestHash {
			return HistoryArchive{}, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		return old, nil
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return HistoryArchive{}, e
	}
	tx, e := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if e != nil {
		return HistoryArchive{}, e
	}
	defer rollback(ctx, tx)
	if e = s.historyPrincipal(ctx, tx, p); e != nil {
		return HistoryArchive{}, e
	}
	env, e := s.environment(ctx, tx, false)
	if e != nil {
		return HistoryArchive{}, e
	}
	var fundsVersion, sourceVersion uint64
	if e = tx.QueryRow(ctx, `SELECT funds_version,source_version FROM users WHERE environment_id=$1 AND id=$2`, p.EnvironmentID.String(), p.UserID.String()).Scan(&fundsVersion, &sourceVersion); e != nil {
		return HistoryArchive{}, e
	}
	source := history.Source{System: "ZHIYU_GO", Schema: history.CurrentSchema, EnvironmentID: p.EnvironmentID.String(), UserID: p.UserID.String(), Namespace: env.Namespace, Scope: "REGISTERED_TABLE_SET", CapturedAt: s.now(), CatalogHash: history.CatalogHash(), Watermarks: map[string]uint64{"funds_version": fundsVersion, "source_version": sourceVersion, "writer_generation": env.WriterGeneration, "control_version": env.ControlVersion}, UnavailableTables: []string{}}
	tables := []history.TableSpec{}
	objects := []history.Object{}
	var total int64
	for _, spec := range history.CurrentTables() {
		var present bool
		if e = tx.QueryRow(ctx, `SELECT to_regclass('public.'||$1) IS NOT NULL`, spec.Name).Scan(&present); e != nil {
			return HistoryArchive{}, e
		}
		if !present {
			source.UnavailableTables = append(source.UnavailableTables, spec.Name)
			continue
		}
		if spec.Name == "audit_session_subjects" {
			var columns []string
			if e = tx.QueryRow(ctx, `SELECT array_agg(attname::text ORDER BY attnum) FROM pg_attribute WHERE attrelid=to_regclass('public.audit_session_subjects') AND attnum>0 AND NOT attisdropped`).Scan(&columns); e != nil {
				return HistoryArchive{}, e
			}
			if !reflect.DeepEqual(columns, []string{"id", "environment_id", "user_id", "expires_at", "revoked"}) {
				return HistoryArchive{}, reject("UNSUPPORTED_HISTORY_SOURCE_SCHEMA", nil)
			}
		} else {
			var columns []string
			e = tx.QueryRow(ctx, `SELECT array_agg(a.attname::text ORDER BY k.ordinality) FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum,ordinality) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum WHERE c.conrelid=to_regclass('public.'||$1) AND c.contype='p'`, spec.Name).Scan(&columns)
			if e != nil {
				return HistoryArchive{}, e
			}
			if !reflect.DeepEqual(columns, spec.PrimaryKey) {
				return HistoryArchive{}, reject("UNSUPPORTED_HISTORY_SOURCE_SCHEMA", nil)
			}
		}
		parts := make([]string, len(spec.PrimaryKey))
		orders := make([]string, len(parts))
		for i, column := range spec.PrimaryKey {
			orders[i] = "t." + pgx.Identifier{column}.Sanitize()
			parts[i] = orders[i] + "::text"
		}
		where := "t.environment_id=$1 AND t.user_id=$2"
		args := []any{p.EnvironmentID.String(), p.UserID.String()}
		switch spec.Name {
		case "environments":
			where = "t.id=$1"
			args = args[:1]
		case "users":
			where = "t.environment_id=$1 AND t.id=$2"
		case "standing_consumers":
			where = "t.environment_id=$1"
			args = args[:1]
		}
		query := "SELECT to_jsonb(t)::text,jsonb_build_array(" + strings.Join(parts, ",") + ")::text FROM " + pgx.Identifier{"public", spec.Name}.Sanitize() + " t WHERE " + where + " ORDER BY " + strings.Join(orders, ",") + " LIMIT 10001"
		rows, e := tx.Query(ctx, query, args...)
		if e != nil {
			return HistoryArchive{}, e
		}
		for rows.Next() {
			var raw []byte
			var keyRaw string
			if e = rows.Scan(&raw, &keyRaw); e != nil {
				rows.Close()
				return HistoryArchive{}, e
			}
			var values []string
			if json.Unmarshal([]byte(keyRaw), &values) != nil {
				rows.Close()
				return HistoryArchive{}, reject("HISTORY_EVIDENCE_INVALID", nil)
			}
			rowKey, e := history.RowKey(values)
			if e != nil {
				rows.Close()
				return HistoryArchive{}, e
			}
			o, e := history.NewObject(history.ObjectRef{Table: spec.Name, RowKey: rowKey, Representation: "db_export", Protocol: history.RowProtocol, Bindings: []history.Binding{}}, raw)
			if e != nil {
				rows.Close()
				return HistoryArchive{}, reject("HISTORY_CAPACITY_EXCEEDED", e)
			}
			objects = append(objects, o)
			total += int64(len(raw))
			if len(objects) > history.MaxObjects || total > history.MaxTotalBytes {
				rows.Close()
				return HistoryArchive{}, reject("HISTORY_CAPACITY_EXCEEDED", nil)
			}
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return HistoryArchive{}, e
		}
		tables = append(tables, spec)
	}
	if e = tx.Commit(ctx); e != nil {
		return HistoryArchive{}, e
	}
	bundle, e := history.Seal(source, tables, objects)
	if e != nil {
		return HistoryArchive{}, reject("INVALID_HISTORY_ARCHIVE", e)
	}
	wire, e := json.Marshal(bundle)
	if e != nil {
		return HistoryArchive{}, e
	}
	return s.saveHistoryArchive(ctx, p, key, requestHash, "AUTHENTICATED_CURRENT_READSET", bundle, wire)
}
