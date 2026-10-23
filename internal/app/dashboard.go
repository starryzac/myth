package app

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type AccountView struct {
	ID        string      `json:"id"`
	Name      string      `json:"name"`
	Balance   funds.Money `json:"balance_cents"`
	GoalOwned funds.Money `json:"goal_owned_cents"`
}
type GoalView struct {
	ID        string      `json:"id"`
	Name      string      `json:"name"`
	AccountID string      `json:"account_id"`
	Target    funds.Money `json:"target_cents"`
	Assigned  funds.Money `json:"assigned_cents"`
}
type Dashboard struct {
	Environment       string              `json:"environment"`
	AsOf              time.Time           `json:"as_of"`
	Accounts          []AccountView       `json:"accounts"`
	Goals             []GoalView          `json:"goals"`
	Policies          []policy.Policy     `json:"policies"`
	Grants            []policy.Grant      `json:"grants"`
	Operations        []Operation         `json:"operations"`
	VerificationScope string              `json:"verification_scope"`
	Projection        ProjectionWatermark `json:"projection"`
	Native            NativeDashboardView `json:"native"`
}

// Dashboard is a bounded display projection from one consistent snapshot. It
// never grants authority or substitutes for the audit of retained originals.
func (s *Service) Dashboard(ctx context.Context, p Principal) (Dashboard, error) {
	v := Dashboard{Environment: s.Namespace, AsOf: s.now(), Accounts: []AccountView{}, Goals: []GoalView{}, Policies: []policy.Policy{}, Grants: []policy.Grant{}, Operations: []Operation{}, VerificationScope: "DISPLAY_SNAPSHOT"}
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return v, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, false)
	if err != nil {
		return v, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return v, err
	}
	if err = s.human(ctx, tx, p); err != nil {
		return v, err
	}
	rows, err := tx.Query(ctx, "SELECT id::text,name,balance_cents,goal_owned_cents FROM accounts WHERE environment_id=$1 AND user_id=$2 ORDER BY id LIMIT 100", env.ID.String(), p.UserID.String())
	if err != nil {
		return v, err
	}
	for rows.Next() {
		var a AccountView
		var balance, owned int64
		if err = rows.Scan(&a.ID, &a.Name, &balance, &owned); err != nil {
			rows.Close()
			return v, err
		}
		a.Balance, err = funds.NewMoney(balance)
		if err != nil {
			rows.Close()
			return v, err
		}
		a.GoalOwned, err = funds.NewMoney(owned)
		if err != nil {
			rows.Close()
			return v, err
		}
		v.Accounts = append(v.Accounts, a)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return v, err
	}
	rows, err = tx.Query(ctx, "SELECT id::text,name,account_id::text,target_cents,assigned_cents FROM goals WHERE environment_id=$1 AND user_id=$2 ORDER BY id LIMIT 100", env.ID.String(), p.UserID.String())
	if err != nil {
		return v, err
	}
	for rows.Next() {
		var g GoalView
		var target, assigned int64
		if err = rows.Scan(&g.ID, &g.Name, &g.AccountID, &target, &assigned); err != nil {
			rows.Close()
			return v, err
		}
		g.Target, err = funds.NewMoney(target)
		if err != nil {
			rows.Close()
			return v, err
		}
		g.Assigned, err = funds.NewMoney(assigned)
		if err != nil {
			rows.Close()
			return v, err
		}
		v.Goals = append(v.Goals, g)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return v, err
	}
	rows, err = tx.Query(ctx, "SELECT original,status FROM policies WHERE environment_id=$1 AND user_id=$2 AND is_current ORDER BY id LIMIT 100", env.ID.String(), p.UserID.String())
	if err != nil {
		return v, err
	}
	for rows.Next() {
		var pol policy.Policy
		var raw []byte
		var status string
		if err = rows.Scan(&raw, &status); err != nil {
			rows.Close()
			return v, err
		}
		if err = decode(raw, &pol); err != nil {
			rows.Close()
			return v, reject("EVIDENCE_INVALID", err)
		}
		if pol.Scope.EnvironmentID != env.ID || pol.Scope.UserID != p.UserID {
			rows.Close()
			return v, reject("EVIDENCE_INVALID", nil)
		}
		pol.Status = policy.Status(status)
		v.Policies = append(v.Policies, pol)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return v, err
	}
	rows, err = tx.Query(ctx, "SELECT original,status FROM grants WHERE environment_id=$1 AND user_id=$2 AND is_current ORDER BY id LIMIT 100", env.ID.String(), p.UserID.String())
	if err != nil {
		return v, err
	}
	for rows.Next() {
		var g policy.Grant
		var raw []byte
		var status string
		if err = rows.Scan(&raw, &status); err != nil {
			rows.Close()
			return v, err
		}
		if err = decode(raw, &g); err != nil {
			rows.Close()
			return v, reject("EVIDENCE_INVALID", err)
		}
		if g.Scope.EnvironmentID != env.ID || g.Scope.UserID != p.UserID {
			rows.Close()
			return v, reject("EVIDENCE_INVALID", nil)
		}
		g.Status = policy.Status(status)
		v.Grants = append(v.Grants, g)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return v, err
	}
	v.Projection, err = projectionWatermark(ctx, tx, p)
	if err != nil {
		return v, err
	}
	if v.Projection.Status != "INVALID" {
		v.Operations, err = readProjectedOperations(ctx, tx, p, 20)
		if err != nil {
			v.Operations = []Operation{}
			v.Projection.Status = "INVALID"
		}
	}
	v.Native, err = s.nativeDashboard(ctx, tx, p, v.AsOf, v.Goals)
	if err != nil {
		return v, err
	}
	return v, tx.Commit(ctx)
}
