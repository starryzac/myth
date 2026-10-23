package app

import (
	"context"
	"errors"
	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func (s *Service) NativeGrantPreview(ctx context.Context, p Principal, id funds.ID) (NativeStandingGrantPreview, error) {
	if e := s.auditHuman(ctx, p); e != nil {
		return NativeStandingGrantPreview{}, e
	}
	return loadNativeGrantPreview(ctx, s.Pool, p, id)
}
func (s *Service) NativeGrantByKey(ctx context.Context, p Principal, key string) (NativeStandingGrantView, error) {
	if e := s.auditHuman(ctx, p); e != nil {
		return NativeStandingGrantView{}, e
	}
	if e := validateKey(key); e != nil {
		return NativeStandingGrantView{}, e
	}
	var raw string
	e := s.Pool.QueryRow(ctx, `SELECT id::text FROM native_grants WHERE environment_id=$1 AND user_id=$2 AND idempotency_key=$3`, p.EnvironmentID.String(), p.UserID.String(), key).Scan(&raw)
	if errors.Is(e, pgx.ErrNoRows) {
		return NativeStandingGrantView{}, reject("NOT_FOUND", nil)
	}
	if e != nil {
		return NativeStandingGrantView{}, e
	}
	id, e := funds.ParseID(raw)
	if e != nil {
		return NativeStandingGrantView{}, e
	}
	return loadNativeStandingGrant(ctx, s.Pool, p, id)
}

type NativeGrantStateRequest struct {
	ExpectedSequence uint64 `json:"expected_sequence"`
	Action           string `json:"action"`
}
