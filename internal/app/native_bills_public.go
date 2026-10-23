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

// Public requests name only an operator-installed source. A request cannot
// supply statement bytes, amounts, completeness or bank observations.
type NativeBillImportRequest struct {
	SourceID string `json:"source_id"`
}
type NativeBillSourceView struct {
	ID          string     `json:"id"`
	State       string     `json:"state"`
	CaptureID   *funds.ID  `json:"capture_id,omitempty"`
	CaptureHash string     `json:"capture_hash,omitempty"`
	ObservedAt  *time.Time `json:"observed_at,omitempty"`
	IntakeState string     `json:"intake_state,omitempty"`
}
type NativeBillSourcesView struct {
	Protocol      string                    `json:"protocol"`
	Scope         policy.TemplateOwnerScope `json:"scope"`
	Sources       []NativeBillSourceView    `json:"sources"`
	BankAuthority bool                      `json:"bank_authority"`
}

func (s *Service) ImportConfiguredNativeBills(ctx context.Context, p Principal, key string, in NativeBillImportRequest) (NativeBillIntakeView, error) {
	return s.ImportNativeBills(ctx, p, key, in.SourceID, s.NativeBillSources)
}
func (s *Service) NativeBillImportByKey(ctx context.Context, p Principal, key string) (NativeBillIntakeView, error) {
	if !historyKey(key) {
		return NativeBillIntakeView{}, reject("INVALID_IDEMPOTENCY_KEY", nil)
	}
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return NativeBillIntakeView{}, e
	}
	capture, e := loadNativeBillCapture(ctx, s.Pool, p, funds.ID{}, key)
	if errors.Is(e, pgx.ErrNoRows) {
		return NativeBillIntakeView{}, reject("NOT_FOUND", nil)
	}
	if e != nil {
		return NativeBillIntakeView{}, e
	}
	return loadNativeBillIntakeView(ctx, s.Pool, p, capture)
}
func (s *Service) NativeBillSourceInventory(ctx context.Context, p Principal) (NativeBillSourcesView, error) {
	out := NativeBillSourcesView{Protocol: "native-bill-source-inventory-v1", Scope: policy.TemplateOwnerScope{EnvironmentID: p.EnvironmentID, UserID: p.UserID}, Sources: []NativeBillSourceView{}}
	if e := s.historyPrincipal(ctx, s.Pool, p); e != nil {
		return out, e
	}
	ids := []string{}
	for id, reader := range s.NativeBillSources.Readers {
		if reader != nil && historyKey(id) {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	if len(ids) > 1 {
		return out, reject("BILL_SOURCE_NOT_CONFIGURED", nil)
	}
	var present bool
	if e := s.Pool.QueryRow(ctx, `SELECT to_regclass('native_bill_captures') IS NOT NULL`).Scan(&present); e != nil {
		return out, e
	}
	for _, id := range ids {
		view := NativeBillSourceView{ID: id, State: "CONFIGURED_NOT_OBSERVED"}
		if !present {
			view.State = "SCHEMA_UNAVAILABLE"
			out.Sources = append(out.Sources, view)
			continue
		}
		var captureID string
		e := s.Pool.QueryRow(ctx, `SELECT id::text FROM native_bill_captures WHERE environment_id=$1 AND user_id=$2 AND source_id=$3 ORDER BY recorded_funds_version DESC,id DESC LIMIT 1`, p.EnvironmentID.String(), p.UserID.String(), id).Scan(&captureID)
		if errors.Is(e, pgx.ErrNoRows) {
			out.Sources = append(out.Sources, view)
			continue
		}
		if e != nil {
			return out, e
		}
		parsed, e := funds.ParseID(captureID)
		if e != nil {
			return out, e
		}
		capture, e := loadNativeBillCapture(ctx, s.Pool, p, parsed, "")
		if e != nil {
			return out, e
		}
		intake, e := loadNativeBillIntakeView(ctx, s.Pool, p, capture)
		if e != nil {
			return out, e
		}
		view.State = "OBSERVED_CAPTURE"
		view.CaptureID = &parsed
		view.CaptureHash = intake.CaptureHash
		view.ObservedAt = &capture.ObservedAt
		view.IntakeState = intake.State
		out.Sources = append(out.Sources, view)
	}
	return out, nil
}
