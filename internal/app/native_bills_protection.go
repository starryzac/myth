package app

import (
	"context"

	"github.com/jackc/pgx/v5"
)

// Intake captures are original facts, even when their financial incorporation
// must wait for an existing original-key operation to reconcile. Every NEW
// candidate/admission/unsigned permit must cross this gate.
func ensureNativeBillsReady(ctx context.Context, q queryRow, p Principal) error {
	var present bool
	if e := q.QueryRow(ctx, `SELECT to_regclass('native_bill_captures') IS NOT NULL`).Scan(&present); e != nil {
		return e
	}
	if !present {
		return nil
	}
	var pending bool
	if e := q.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM native_bill_captures c WHERE c.environment_id=$1 AND c.user_id=$2 AND NOT EXISTS(SELECT 1 FROM native_bill_capture_resolutions r WHERE r.environment_id=c.environment_id AND r.user_id=c.user_id AND r.capture_id=c.id))`, p.EnvironmentID.String(), p.UserID.String()).Scan(&pending); e != nil {
		return e
	}
	if pending {
		return reject("BILL_INTAKE_PENDING", nil)
	}
	return nil
}

// Only an already admitted original financial effect may defer a protection
// publication. A true result means no new publication was claimed. All other
// errors remain errors; normal preview/confirmation/T1 uses the strict method.
func (s *Service) refreshTemplateProtectionAfterNativeEffect(ctx context.Context, tx pgx.Tx, p Principal, timezone string) (ForecastView, bool, error) {
	if e := ensureNativeBillsReady(ctx, tx, p); e != nil {
		if Code(e) == "BILL_INTAKE_PENDING" {
			return ForecastView{}, true, nil
		}
		return ForecastView{}, false, e
	}
	view, e := s.refreshTemplateProtection(ctx, tx, p, timezone)
	if Code(e) == "BILL_INTAKE_PENDING" {
		return ForecastView{}, true, nil
	}
	return view, false, e
}
