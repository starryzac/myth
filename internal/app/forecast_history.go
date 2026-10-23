package app

import (
	"context"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// The server provisions issuer keys and exact subject scope. A browser or a
// model may select a registered source; it cannot supply statement amounts,
// private keys, a replacement public key, or an arbitrary file path.
type ForecastHistoryAuthority struct {
	IssuerID     string     `json:"issuer_id"`
	Namespace    string     `json:"namespace"`
	UserID       funds.ID   `json:"user_id"`
	AccountIDs   []funds.ID `json:"account_ids"`
	PublicKeyHex string     `json:"public_key_hex"`
}

type ForecastHistoryReader interface {
	ReadHistory(context.Context) ([]byte, error)
}

type ForecastHistorySource struct {
	Authority ForecastHistoryAuthority
	Reader    ForecastHistoryReader
}

type ForecastHistorySources struct {
	Sources map[string]ForecastHistorySource
}

type ForecastHistoryImport struct {
	Protocol      string    `json:"protocol"`
	ID            string    `json:"id"`
	SourceID      string    `json:"source_id"`
	StatementID   string    `json:"statement_id"`
	StatementHash string    `json:"statement_hash"`
	FromDate      string    `json:"from_date"`
	ThroughDate   string    `json:"through_date"`
	RecordedAt    time.Time `json:"recorded_at"`
	SourceVersion uint64    `json:"source_version"`
	Replayed      bool      `json:"replayed"`
}
