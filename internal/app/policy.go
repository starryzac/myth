package app

import (
	"bytes"
	"context"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type queryRow interface {
	QueryRow(context.Context, string, ...any) pgx.Row
}

func (s *Service) human(ctx context.Context, q queryRow, p Principal) error {
	if p.Kind != "USER" || p.SessionID == (funds.ID{}) {
		return reject("FORBIDDEN", nil)
	}
	var yes bool
	err := q.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM sessions WHERE id=$1 AND environment_id=$2 AND user_id=$3 AND NOT revoked AND expires_at>$4)", p.SessionID.String(), p.EnvironmentID.String(), p.UserID.String(), s.now()).Scan(&yes)
	if err != nil {
		return err
	}
	if !yes {
		return reject("UNAUTHENTICATED", nil)
	}
	return nil
}

func loadPolicy(ctx context.Context, q queryRow, p Principal, id funds.ID, lock bool) (policy.Policy, error) {
	query := `SELECT version,status,original FROM policies WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND is_current`
	if lock {
		query += " FOR UPDATE"
	}
	var version uint64
	var status string
	var raw []byte
	var result policy.Policy
	if err := q.QueryRow(ctx, query, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&version, &status, &raw); err != nil {
		return result, reject("NOT_FOUND", err)
	}
	if err := decode(raw, &result); err != nil {
		return result, reject("EVIDENCE_INVALID", err)
	}
	if result.ID != id || result.Version != version || result.Scope.EnvironmentID != p.EnvironmentID || result.Scope.UserID != p.UserID {
		return result, reject("EVIDENCE_INVALID", nil)
	}
	if err := checkConfirmation(ctx, q, p, result.Confirmation); err != nil {
		return result, err
	}
	result.Status = policy.Status(status)
	var current bool
	if err := q.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM goals WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND policy_id=$4 AND policy_version=$5)", p.EnvironmentID.String(), p.UserID.String(), result.Scope.GoalID.String(), id.String(), version).Scan(&current); err != nil {
		return result, err
	}
	if !current {
		return result, reject("SOURCE_CHANGED", nil)
	}
	return result, nil
}
func loadGrant(ctx context.Context, q queryRow, p Principal, id funds.ID, lock bool) (policy.Grant, error) {
	query := `SELECT version,status,original FROM grants WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND is_current`
	if lock {
		query += " FOR UPDATE"
	}
	var version uint64
	var status string
	var raw []byte
	var result policy.Grant
	if err := q.QueryRow(ctx, query, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&version, &status, &raw); err != nil {
		return result, reject("NOT_FOUND", err)
	}
	if err := decode(raw, &result); err != nil {
		return result, reject("EVIDENCE_INVALID", err)
	}
	if result.ID != id || result.Version != version || result.Scope.EnvironmentID != p.EnvironmentID || result.Scope.UserID != p.UserID {
		return result, reject("EVIDENCE_INVALID", nil)
	}
	if err := checkConfirmation(ctx, q, p, result.Confirmation); err != nil {
		return result, err
	}
	result.Status = policy.Status(status)
	return result, nil
}

func checkConfirmation(ctx context.Context, q queryRow, p Principal, c policy.ConfirmationSource) error {
	var raw []byte
	if err := q.QueryRow(ctx, "SELECT original FROM confirmations WHERE environment_id=$1 AND user_id=$2 AND id=$3", p.EnvironmentID.String(), p.UserID.String(), c.ID.String()).Scan(&raw); err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	expected, err := encode(c)
	if err != nil || !bytes.Equal(raw, expected) {
		return reject("EVIDENCE_INVALID", err)
	}
	return nil
}
func saveConfirmation(ctx context.Context, tx pgx.Tx, p Principal, c policy.ConfirmationSource) error {
	raw, err := encode(c)
	if err != nil {
		return err
	}
	var expires any
	if !c.ExpiresAt.IsZero() {
		expires = c.ExpiresAt
	}
	_, err = tx.Exec(ctx, `INSERT INTO confirmations(environment_id,user_id,id,target_kind,target_id,target_version,reviewed_hash,confirmed_at,expires_at,original,session_id) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`, p.EnvironmentID.String(), p.UserID.String(), c.ID.String(), c.TargetKind, c.TargetID.String(), c.TargetVersion, c.ReviewedHash, c.ConfirmedAt, expires, raw, p.SessionID.String())
	return err
}

type PolicyRequest struct {
	GoalID     funds.ID    `json:"goal_id"`
	MaxAmount  funds.Money `json:"max_amount"`
	ValidUntil time.Time   `json:"valid_until,omitempty"`
}

func (s *Service) ConfirmPolicy(ctx context.Context, p Principal, in PolicyRequest) (policy.Policy, error) {
	var result policy.Policy
	tx, err := s.begin(ctx)
	if err != nil {
		return result, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return result, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return result, err
	}
	if _, _, err = lockUser(ctx, tx, p); err != nil {
		return result, err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return result, err
	}
	var account string
	var target int64
	if err = tx.QueryRow(ctx, "SELECT account_id::text,target_cents FROM goals WHERE environment_id=$1 AND user_id=$2 AND id=$3 FOR UPDATE", env.ID.String(), p.UserID.String(), in.GoalID.String()).Scan(&account, &target); err != nil {
		return result, reject("NOT_FOUND", err)
	}
	if in.MaxAmount.IsZero() || in.MaxAmount.Cents() > target {
		return result, reject("INVALID_POLICY", nil)
	}
	now := s.now()
	end := in.ValidUntil
	if end.IsZero() {
		end = now.AddDate(1, 0, 0)
	}
	end = end.UTC().Truncate(time.Microsecond)
	if !end.After(now) || end.After(now.AddDate(2, 0, 0)) {
		return result, reject("INVALID_POLICY", nil)
	}
	id, err := newID()
	if err != nil {
		return result, err
	}
	cid, err := newID()
	if err != nil {
		return result, err
	}
	acct, err := funds.ParseID(account)
	if err != nil {
		return result, err
	}
	result = policy.Policy{ID: id, Version: 1, Scope: policy.Scope{EnvironmentID: env.ID, UserID: p.UserID, GoalID: in.GoalID, SourceAccountID: acct, DestinationAccountID: acct, Currency: funds.CNY}, MaxAmount: in.MaxAmount, ValidFrom: now, ValidUntil: end, Status: policy.Active}
	result.Confirmation = policy.ConfirmationSource{ID: cid, PrincipalKind: "USER", EnvironmentID: env.ID, UserID: p.UserID, TargetKind: "POLICY", TargetID: id, TargetVersion: 1, ReviewedHash: result.ConfigurationHash(), ConfirmedAt: now}
	if _, err = result.EffectiveFrom(now); err != nil {
		return result, reject("INVALID_POLICY", err)
	}
	if err = saveConfirmation(ctx, tx, p, result.Confirmation); err != nil {
		return result, err
	}
	// Replacing a goal's reviewed policy stops the old configuration and all
	// of its future grants. Already committed permits keep their original key.
	_, err = tx.Exec(ctx, `UPDATE grants SET status='REVOKED' WHERE environment_id=$1 AND user_id=$2 AND policy_id IN (SELECT policy_id FROM goals WHERE environment_id=$1 AND user_id=$2 AND id=$3) AND status<>'REVOKED'`, env.ID.String(), p.UserID.String(), in.GoalID.String())
	if err != nil {
		return result, err
	}
	_, err = tx.Exec(ctx, `UPDATE policies SET status='REVOKED',is_current=false WHERE environment_id=$1 AND user_id=$2 AND id IN (SELECT policy_id FROM goals WHERE environment_id=$1 AND user_id=$2 AND id=$3) AND is_current`, env.ID.String(), p.UserID.String(), in.GoalID.String())
	if err != nil {
		return result, err
	}
	raw, err := encode(result)
	if err != nil {
		return result, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO policies(environment_id,user_id,id,version,status,is_current,confirmation_id,original) VALUES($1,$2,$3,1,'ACTIVE',true,$4,$5)`, env.ID.String(), p.UserID.String(), id.String(), cid.String(), raw)
	if err != nil {
		return result, err
	}
	_, err = tx.Exec(ctx, `UPDATE goals SET policy_id=$3,policy_version=1,version=version+1 WHERE environment_id=$1 AND user_id=$2 AND id=$4`, env.ID.String(), p.UserID.String(), id.String(), in.GoalID.String())
	if err != nil {
		return result, err
	}
	_, err = tx.Exec(ctx, "UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2", env.ID.String(), p.UserID.String())
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

type GrantRequest struct {
	PolicyID  funds.ID    `json:"policy_id"`
	MaxAmount funds.Money `json:"max_amount"`
}

func (s *Service) ConfirmGrant(ctx context.Context, p Principal, in GrantRequest) (policy.Grant, error) {
	var result policy.Grant
	tx, err := s.begin(ctx)
	if err != nil {
		return result, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return result, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return result, err
	}
	if _, _, err = lockUser(ctx, tx, p); err != nil {
		return result, err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return result, err
	}
	pol, err := loadPolicy(ctx, tx, p, in.PolicyID, true)
	if err != nil {
		return result, err
	}
	now := s.now()
	if _, err = pol.EffectiveFrom(now); err != nil {
		return result, reject("INVALID_POLICY", err)
	}
	if in.MaxAmount.IsZero() || in.MaxAmount.Cents() > pol.MaxAmount.Cents() {
		return result, reject("INVALID_GRANT", nil)
	}
	id, err := newID()
	if err != nil {
		return result, err
	}
	cid, err := newID()
	if err != nil {
		return result, err
	}
	result = policy.Grant{ID: id, Version: 1, PolicyID: pol.ID, PolicyVersion: pol.Version, Scope: pol.Scope, MaxAmount: in.MaxAmount, EffectiveFrom: now, ValidUntil: pol.ValidUntil, Status: policy.Active}
	result.Confirmation = policy.ConfirmationSource{ID: cid, PrincipalKind: "USER", EnvironmentID: env.ID, UserID: p.UserID, TargetKind: "GRANT", TargetID: id, TargetVersion: 1, ReviewedHash: result.ConfigurationHash(), ConfirmedAt: now}
	if _, err = policy.CheckGrant(pol, result, now); err != nil {
		return result, reject("INVALID_GRANT", err)
	}
	if err = saveConfirmation(ctx, tx, p, result.Confirmation); err != nil {
		return result, err
	}
	raw, err := encode(result)
	if err != nil {
		return result, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO grants(environment_id,user_id,id,version,policy_id,policy_version,status,is_current,confirmation_id,original) VALUES($1,$2,$3,1,$4,$5,'ACTIVE',true,$6,$7)`, env.ID.String(), p.UserID.String(), id.String(), pol.ID.String(), pol.Version, cid.String(), raw)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

func (s *Service) RevokeGrant(ctx context.Context, p Principal, id funds.ID) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return err
	}
	if err = sameUser(p, env.ID); err != nil {
		return err
	}
	if _, _, err = lockUser(ctx, tx, p); err != nil {
		return err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return err
	}
	g, err := loadGrant(ctx, tx, p, id, true)
	if err != nil {
		return err
	}
	if g.Status == policy.Revoked {
		return nil
	}
	_, err = tx.Exec(ctx, "UPDATE grants SET status='REVOKED' WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND version=$4", env.ID.String(), p.UserID.String(), id.String(), g.Version)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}
