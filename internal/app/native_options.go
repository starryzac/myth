package app

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
)

type NativeFinancialOptions struct {
	Protocol      string                        `json:"protocol"`
	CapturedAt    time.Time                     `json:"captured_at"`
	SourceHash    string                        `json:"source_hash"`
	FundsVersion  uint64                        `json:"funds_version"`
	SourceVersion uint64                        `json:"source_version"`
	Policies      []TemplatePolicyView          `json:"policies"`
	Debts         []planning.ForecastObligation `json:"debts"`
	Liabilities   []bankv2.LiabilityState       `json:"liabilities"`
	BankState     bankv2.CurrentState           `json:"bank_state"`
	BankAuthority bool                          `json:"bank_authority"`
}

// This bounded reader supplies actual IDs to typed forms. Bank reads remain
// outside the local RR transaction and no preview/permission is created.
func (s *Service) NativeOptions(ctx context.Context, p Principal) (NativeFinancialOptions, error) {
	var out NativeFinancialOptions
	if e := s.auditHuman(ctx, p); e != nil {
		return out, e
	}
	assets, ok := s.NativeBank.(nativeAssetBankReader)
	if !ok {
		return out, reject("BANK_UNAVAILABLE", nil)
	}
	payments, ok := s.NativeBank.(nativePaymentBankReader)
	if !ok {
		return out, reject("BANK_UNAVAILABLE", nil)
	}
	state, e := assets.CurrentState(ctx, s.Namespace, p.UserID.String())
	if e != nil {
		return out, reject("BANK_UNAVAILABLE", e)
	}
	liabilities, e := payments.Liabilities(ctx, s.Namespace, p.UserID.String())
	if e != nil {
		return out, reject("BANK_UNAVAILABLE", e)
	}
	now := s.now()
	fresh := func(at time.Time) bool {
		return !at.IsZero() && !at.After(now.Add(time.Second)) && now.Sub(at) <= 30*time.Second
	}
	if state.Protocol != bankv2.Protocol || state.Namespace != s.Namespace || state.UserID != p.UserID.String() || state.CapturedAt.IsZero() || liabilities.Validate() != nil || liabilities.Namespace != s.Namespace || liabilities.UserID != p.UserID.String() || !fresh(liabilities.CapturedAt) {
		return out, reject("BANK_SNAPSHOT_STALE", nil)
	}
	tx, e := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if e != nil {
		return out, e
	}
	defer rollback(ctx, tx)
	if e = s.human(ctx, tx, p); e != nil {
		return out, e
	}
	zone, e := templateScopedTimezone(ctx, tx, p, "")
	if e != nil {
		return out, e
	}
	snapshot, e := s.loadTemplateSnapshot(ctx, tx, p, now, zone)
	if e != nil {
		return out, e
	}
	// The stock timestamp is the bank's PostgreSQL transaction instant; an
	// explicit simulation business clock need not equal that wall clock. Exact
	// complete stock equality is required, rather than relabeling its timestamp.
	if snapshot.Native == nil {
		return out, reject("NATIVE_STORAGE_REQUIRED", nil)
	}
	if e = nativeAssetState(*snapshot.Native, state, snapshot.Facts.Scope); e != nil {
		return out, e
	}
	out = NativeFinancialOptions{Protocol: "native-financial-options-v1", CapturedAt: now, SourceHash: snapshot.Facts.SourceHash, FundsVersion: snapshot.FundsVersion, SourceVersion: snapshot.SourceVersion, Policies: snapshot.Views, Debts: append([]planning.ForecastObligation{}, snapshot.Retained...), Liabilities: liabilities.Liabilities, BankState: state}
	return out, tx.Commit(ctx)
}
