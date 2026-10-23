package app

import (
	"context"
	"errors"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

type GoalConfigurationRequest struct {
	Name            string      `json:"name"`
	AccountID       funds.ID    `json:"account_id"`
	Target          funds.Money `json:"target_cents"`
	ExpectedVersion uint64      `json:"expected_version"`
}

type GoalDetail struct {
	ID        funds.ID    `json:"id"`
	Name      string      `json:"name"`
	AccountID funds.ID    `json:"account_id"`
	Target    funds.Money `json:"target_cents"`
	Assigned  funds.Money `json:"assigned_cents"`
	Version   uint64      `json:"version"`
}

type goalConfigurationOriginal struct {
	Protocol      string      `json:"protocol"`
	EnvironmentID funds.ID    `json:"environment_id"`
	UserID        funds.ID    `json:"user_id"`
	SessionID     funds.ID    `json:"session_id"`
	PrincipalKind string      `json:"principal_kind"`
	Action        string      `json:"action"`
	Before        *GoalDetail `json:"before,omitempty"`
	After         GoalDetail  `json:"after"`
	ConfirmedAt   time.Time   `json:"confirmed_at"`
}

func validGoalName(name string) bool {
	if name != strings.TrimSpace(name) || utf8.RuneCountInString(name) < 1 || utf8.RuneCountInString(name) > 80 || !utf8.ValidString(name) {
		return false
	}
	for _, r := range name {
		if unicode.IsControl(r) {
			return false
		}
	}
	return true
}

func loadGoalDetail(ctx context.Context, q queryRow, p Principal, id funds.ID) (GoalDetail, error) {
	var out GoalDetail
	var account string
	var target, assigned int64
	err := q.QueryRow(ctx, `SELECT name,account_id::text,target_cents,assigned_cents,version FROM goals WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&out.Name, &account, &target, &assigned, &out.Version)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, reject("NOT_FOUND", err)
	}
	if err != nil {
		return out, err
	}
	out.ID = id
	if out.AccountID, err = funds.ParseID(account); err != nil {
		return out, reject("EVIDENCE_INVALID", err)
	}
	if out.Target, err = funds.NewMoney(target); err != nil {
		return out, reject("EVIDENCE_INVALID", err)
	}
	if out.Assigned, err = funds.NewMoney(assigned); err != nil {
		return out, reject("EVIDENCE_INVALID", err)
	}
	return out, nil
}

func (s *Service) Goal(ctx context.Context, p Principal, id funds.ID) (GoalDetail, error) {
	if err := s.auditHuman(ctx, p); err != nil {
		return GoalDetail{}, err
	}
	return loadGoalDetail(ctx, s.Pool, p, id)
}

func (s *Service) CreateGoal(ctx context.Context, p Principal, key string, in GoalConfigurationRequest) (GoalDetail, error) {
	return s.configureGoal(ctx, p, key, funds.ID{}, in)
}
func (s *Service) UpdateGoal(ctx context.Context, p Principal, key string, id funds.ID, in GoalConfigurationRequest) (GoalDetail, error) {
	if id == (funds.ID{}) {
		return GoalDetail{}, reject("INVALID_ID", nil)
	}
	return s.configureGoal(ctx, p, key, id, in)
}

// Goal configuration changes neither cash nor ownership. The exact human
// command is retained separately; bootstrap rows never gain invented history.
func (s *Service) configureGoal(ctx context.Context, p Principal, key string, id funds.ID, in GoalConfigurationRequest) (GoalDetail, error) {
	var out GoalDetail
	if err := validateKey(key); err != nil {
		return out, err
	}
	create := id == (funds.ID{})
	if !validGoalName(in.Name) || in.AccountID == (funds.ID{}) || in.Target.IsZero() || (create && in.ExpectedVersion != 0) || (!create && in.ExpectedVersion == 0) {
		return out, reject("INVALID_GOAL", nil)
	}
	request, err := encode(struct {
		GoalID        funds.ID                 `json:"goal_id"`
		Configuration GoalConfigurationRequest `json:"configuration"`
	}{id, in})
	if err != nil {
		return out, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, true)
	if err != nil {
		return out, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return out, err
	}
	if _, _, err = lockUser(ctx, tx, p); err != nil {
		return out, err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return out, err
	}
	var previousRaw []byte
	var requestHash, originalHash string
	err = tx.QueryRow(ctx, `SELECT request_hash::text,original,original_hash::text FROM goal_configuration_events WHERE environment_id=$1 AND user_id=$2 AND idempotency_key=$3`, env.ID.String(), p.UserID.String(), key).Scan(&requestHash, &previousRaw, &originalHash)
	if err == nil {
		if requestHash != Hash(request) {
			return out, reject("IDEMPOTENCY_CONFLICT", nil)
		}
		var previous goalConfigurationOriginal
		if Hash(previousRaw) != originalHash || auditJSON(previousRaw, &previous) != nil || previous.Protocol != "human-goal-configuration-v1" || previous.EnvironmentID != env.ID || previous.UserID != p.UserID || previous.PrincipalKind != "USER" {
			return out, reject("EVIDENCE_INVALID", nil)
		}
		return previous.After, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return out, err
	}
	var accountExists bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM accounts WHERE environment_id=$1 AND user_id=$2 AND id=$3)`, env.ID.String(), p.UserID.String(), in.AccountID.String()).Scan(&accountExists); err != nil {
		return out, err
	}
	if !accountExists {
		return out, reject("NOT_FOUND", nil)
	}
	var before *GoalDetail
	now := s.now()
	action := "CREATE"
	if create {
		var count int
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM goals WHERE environment_id=$1 AND user_id=$2`, env.ID.String(), p.UserID.String()).Scan(&count); err != nil {
			return out, err
		}
		if count >= 8 {
			return out, reject("CAPACITY_EXCEEDED", nil)
		}
		if id, err = newID(); err != nil {
			return out, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO goals(environment_id,user_id,id,name,account_id,target_cents,month_period) VALUES($1,$2,$3,$4,$5,$6,$7)`, env.ID.String(), p.UserID.String(), id.String(), in.Name, in.AccountID.String(), in.Target.Cents(), now.In(time.FixedZone("Asia/Shanghai", 8*3600)).Format("2006-01"))
	} else {
		action = "UPDATE"
		old, e := loadGoalDetail(ctx, tx, p, id)
		if e != nil {
			return out, e
		}
		before = &old
		if old.Version != in.ExpectedVersion {
			return out, reject("SOURCE_CHANGED", nil)
		}
		if old.AccountID != in.AccountID {
			return out, reject("GOAL_ACCOUNT_CHANGE_REQUIRES_TRANSFER", nil)
		}
		if in.Target.Cents() < old.Assigned.Cents() {
			return out, reject("GOAL_TARGET_BELOW_OWNED", nil)
		}
		if err = ensureGoalAvailable(ctx, tx, p, id); err != nil {
			return out, err
		}
		if in.Target != old.Target {
			var native bool
			if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM template_policies WHERE environment_id=$1 AND user_id=$2 AND goal_id=$3)`, env.ID.String(), p.UserID.String(), id.String()).Scan(&native); err != nil {
				return out, err
			}
			if native {
				return out, reject("GOAL_TARGET_CHANGE_REQUIRES_POLICY_VERSION", nil)
			}
		}
		_, err = tx.Exec(ctx, `UPDATE goals SET name=$4,target_cents=$5,version=version+1 WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND version=$6`, env.ID.String(), p.UserID.String(), id.String(), in.Name, in.Target.Cents(), in.ExpectedVersion)
	}
	if err != nil {
		return out, err
	}
	if out, err = loadGoalDetail(ctx, tx, p, id); err != nil {
		return out, err
	}
	original, err := encode(goalConfigurationOriginal{Protocol: "human-goal-configuration-v1", EnvironmentID: env.ID, UserID: p.UserID, SessionID: p.SessionID, PrincipalKind: "USER", Action: action, Before: before, After: out, ConfirmedAt: now})
	if err != nil {
		return out, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO goal_configuration_events(environment_id,user_id,goal_id,goal_version,idempotency_key,request,request_hash,session_id,action,original,original_hash,confirmed_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`, env.ID.String(), p.UserID.String(), id.String(), out.Version, key, request, Hash(request), p.SessionID.String(), action, original, Hash(original), now)
	if err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `UPDATE users SET funds_version=funds_version+1 WHERE environment_id=$1 AND id=$2`, env.ID.String(), p.UserID.String()); err != nil {
		return out, err
	}
	var native bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM template_policies WHERE environment_id=$1 AND user_id=$2)`, env.ID.String(), p.UserID.String()).Scan(&native); err != nil {
		return out, err
	}
	if native {
		if _, err = s.refreshTemplateProtection(ctx, tx, p, "Asia/Shanghai"); err != nil {
			return out, err
		}
	}
	eventID, err := newID()
	if err != nil {
		return out, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO outbox(environment_id,user_id,id,kind,aggregate_id,aggregate_version,payload,created_at) VALUES($1,$2,$3,'GOAL_CONFIGURED',$4,$5,$6,$7)`, env.ID.String(), p.UserID.String(), eventID.String(), id.String(), out.Version, original, now)
	if err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}
