package app

import (
	"context"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// NativeBridgePreview reads the captured original, including after its expiry.
// Reading it grants no permission to sign or activate a staged step.
func (s *Service) NativeBridgePreview(ctx context.Context, p Principal, id funds.ID) (NativeBridgePreview, error) {
	if err := s.auditHuman(ctx, p); err != nil {
		return NativeBridgePreview{}, err
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return NativeBridgePreview{}, err
	}
	defer rollback(ctx, tx)
	return loadNativeBridgePreview(ctx, tx, p, id)
}

// NativeBridgeGroupByKey discovers only this user's original accepted command.
func (s *Service) NativeBridgeGroupByKey(ctx context.Context, p Principal, key string) (NativeBridgeGroup, error) {
	if err := validateKey(key); err != nil {
		return NativeBridgeGroup{}, err
	}
	if err := s.auditHuman(ctx, p); err != nil {
		return NativeBridgeGroup{}, err
	}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return NativeBridgeGroup{}, err
	}
	defer rollback(ctx, tx)
	var raw string
	if err = tx.QueryRow(ctx, `SELECT id::text FROM native_group_batches WHERE environment_id=$1 AND user_id=$2 AND idempotency_key=$3`, p.EnvironmentID.String(), p.UserID.String(), key).Scan(&raw); err != nil {
		return NativeBridgeGroup{}, reject("NOT_FOUND", err)
	}
	id, err := funds.ParseID(raw)
	if err != nil {
		return NativeBridgeGroup{}, reject("EVIDENCE_INVALID", err)
	}
	return s.loadNativeBridgeGroup(ctx, tx, p, id)
}
