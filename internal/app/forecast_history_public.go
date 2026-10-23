package app

import (
	"context"
	"sort"

	"github.com/jackc/pgx/v5"
)

// Source IDs are provisioned by the operator. No key, issuer profile, account
// inventory or file path is exposed to an ordinary application client.
type ForecastHistorySourceView struct {
	SourceID string `json:"source_id"`
	State    string `json:"state"`
}

type ForecastHistoryImportRequest struct {
	Key      string `json:"key"`
	SourceID string `json:"source_id"`
}

// ForecastHistorySourceInventory reads registered source identities and already
// captured facts. In particular, a GET never opens a configured source file.
func (s *Service) ForecastHistorySourceInventory(ctx context.Context, p Principal) ([]ForecastHistorySourceView, error) {
	if err := s.auditHuman(ctx, p); err != nil {
		return nil, err
	}
	out := []ForecastHistorySourceView{}
	if s.ForecastHistorySources == nil {
		return out, nil
	}
	if len(s.ForecastHistorySources.Sources) > 100 {
		return nil, reject("HISTORY_CAPACITY_EXCEEDED", nil)
	}
	for id := range s.ForecastHistorySources.Sources {
		if _, err := s.historySource(id, p, false); err == nil {
			out = append(out, ForecastHistorySourceView{SourceID: id, State: "REGISTERED"})
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].SourceID < out[j].SourceID })
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, err
	}
	defer rollback(ctx, tx)
	rows, err := tx.Query(ctx, `SELECT canonical,digest FROM evidence_blobs WHERE environment_id=$1 AND user_id=$2 AND kind=$3 ORDER BY id LIMIT 101`, p.EnvironmentID.String(), p.UserID.String(), forecastHistoryKind)
	if err != nil {
		return nil, err
	}
	imported := map[string]bool{}
	count := 0
	for rows.Next() {
		var raw []byte
		var digest string
		var c forecastHistoryCapture
		if err = rows.Scan(&raw, &digest); err != nil {
			rows.Close()
			return nil, err
		}
		count++
		if count > 100 || Hash(raw) != digest || auditJSON(raw, &c) != nil || c.Protocol != forecastHistoryCaptureProtocol || c.EnvironmentID != p.EnvironmentID || c.UserID != p.UserID || c.Namespace != s.Namespace {
			rows.Close()
			return nil, reject("HISTORY_CAPTURE_INVALID", nil)
		}
		// Withdrawn source IDs are not advertised. Their original commands are
		// still recoverable by the separate scoped ByKey method.
		if _, e := s.historySource(c.SourceID, p, false); e == nil {
			imported[c.SourceID] = true
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	for i := range out {
		if imported[out[i].SourceID] {
			out[i].State = "IMPORTED"
		}
	}
	return out, tx.Commit(ctx)
}

// ForecastHistoryByKey recovers the immutable command even after its source is
// offline or removed from the current registry. Authentication and exact
// environment/user scope remain mandatory; no observation or write occurs.
func (s *Service) ForecastHistoryByKey(ctx context.Context, p Principal, key, sourceID string) (ForecastHistoryImport, error) {
	if err := validateKey(key); err != nil {
		return ForecastHistoryImport{}, err
	}
	if err := s.auditHuman(ctx, p); err != nil {
		return ForecastHistoryImport{}, err
	}
	result, found, err := historyCommand(ctx, s.Pool, p, key, sourceID)
	if err != nil {
		return ForecastHistoryImport{}, err
	}
	if !found {
		return ForecastHistoryImport{}, reject("NOT_FOUND", nil)
	}
	return result, nil
}
