package app

import (
	"bytes"
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type PlanRequest struct {
	PolicyID funds.ID `json:"policy_id"`
}
type capturedInput struct {
	Policy           policy.Policy             `json:"policy"`
	ScopeAccounts    []funds.ID                `json:"scope_accounts"`
	OriginFacts      []funds.BankCreditFact    `json:"origin_facts"`
	Locations        []funds.IncomeLocation    `json:"locations"`
	Reservations     []funds.IncomeReservation `json:"reservations"`
	Cash             []funds.CashLocationFact  `json:"cash"`
	GoalTarget       funds.Money               `json:"goal_target"`
	GoalAssigned     funds.Money               `json:"goal_assigned"`
	MonthContributed funds.Money               `json:"month_contributed"`
	MonthPeriod      string                    `json:"month_period"`
	Protection       planning.Protection       `json:"protection"`
	Dependencies     planning.DependencyVector `json:"dependencies"`
	Now              time.Time                 `json:"now"`
}

func (s *Service) Plan(ctx context.Context, p Principal, in PlanRequest) (planning.Candidate, error) {
	var result planning.Candidate
	tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return result, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, false)
	if err != nil {
		return result, err
	}
	if err = sameUser(p, env.ID); err != nil {
		return result, err
	}
	if err = ensureNativeBillsReady(ctx, tx, p); err != nil {
		return result, err
	}
	pol, err := loadPolicy(ctx, tx, p, in.PolicyID, false)
	if err != nil {
		return result, err
	}
	if err = ensureLegacyGoalUnbound(ctx, tx, p, pol.Scope.GoalID); err != nil {
		return result, err
	}
	if err = ensureGoalAvailable(ctx, tx, p, pol.Scope.GoalID); err != nil {
		return result, err
	}
	var fv, sv uint64
	if err = tx.QueryRow(ctx, "SELECT funds_version,source_version FROM users WHERE environment_id=$1 AND id=$2", env.ID.String(), p.UserID.String()).Scan(&fv, &sv); err != nil {
		return result, err
	}
	var gv uint64
	var gid string
	err = tx.QueryRow(ctx, "SELECT id::text,version FROM grants WHERE environment_id=$1 AND user_id=$2 AND policy_id=$3 AND policy_version=$4 AND is_current AND status='ACTIVE' ORDER BY id LIMIT 1", env.ID.String(), p.UserID.String(), pol.ID.String(), pol.Version).Scan(&gid, &gv)
	if err != nil && err != pgx.ErrNoRows {
		return result, err
	}
	var target, assigned, month int64
	var period string
	if err = tx.QueryRow(ctx, "SELECT target_cents,assigned_cents,month_contributed_cents,month_period FROM goals WHERE environment_id=$1 AND user_id=$2 AND id=$3", env.ID.String(), p.UserID.String(), pol.Scope.GoalID.String()).Scan(&target, &assigned, &month, &period); err != nil {
		return result, err
	}
	now := s.now()
	ledger, err := s.Store.LoadLedger(ctx, tx, env.ID, p.UserID, now)
	if err != nil {
		return result, reject("EVIDENCE_INVALID", err)
	}
	rows, err := tx.Query(ctx, "SELECT id::text,balance_cents,goal_owned_cents,observed_at FROM accounts WHERE environment_id=$1 AND user_id=$2 ORDER BY id", env.ID.String(), p.UserID.String())
	if err != nil {
		return result, err
	}
	var cash []funds.CashLocationFact
	for rows.Next() {
		var id string
		var balance, owned int64
		var at time.Time
		if err = rows.Scan(&id, &balance, &owned, &at); err != nil {
			rows.Close()
			return result, err
		}
		acct, err := funds.ParseID(id)
		if err != nil {
			rows.Close()
			return result, err
		}
		b, err := funds.NewMoney(balance)
		if err != nil {
			rows.Close()
			return result, err
		}
		o, err := funds.NewMoney(owned)
		if err != nil {
			rows.Close()
			return result, err
		}
		cash = append(cash, funds.CashLocationFact{AccountID: acct, Currency: funds.CNY, Balance: b, GoalOwned: o, ObservedAt: at})
	}
	if err = rows.Err(); err != nil {
		rows.Close()
		return result, err
	}
	rows.Close()
	var protectionRaw []byte
	var protectionHash string
	if err = tx.QueryRow(ctx, "SELECT original,original_hash FROM protection_snapshots WHERE environment_id=$1 AND user_id=$2 AND version=$3", env.ID.String(), p.UserID.String(), sv).Scan(&protectionRaw, &protectionHash); err != nil {
		return result, reject("EVIDENCE_INCOMPLETE", err)
	}
	if Hash(protectionRaw) != protectionHash {
		return result, reject("EVIDENCE_INVALID", nil)
	}
	var protection planning.Protection
	if err = decode(protectionRaw, &protection); err != nil {
		return result, reject("EVIDENCE_INVALID", err)
	}
	targetMoney, err := funds.NewMoney(target)
	if err != nil {
		return result, err
	}
	assignedMoney, err := funds.NewMoney(assigned)
	if err != nil {
		return result, err
	}
	monthMoney, err := funds.NewMoney(month)
	if err != nil {
		return result, err
	}
	dependencies := planning.DependencyVector{FundsVersion: fv, GrantVersion: gv, PolicyVersion: pol.Version, ProductTermsVersion: 1, ProductAvailabilityVersion: 1, SourceEvidenceVersion: sv, WriterGeneration: env.WriterGeneration, ControlVersion: env.ControlVersion}
	input := planning.Input{Policy: pol, Ledger: ledger, Cash: cash, GoalTarget: targetMoney, GoalAssigned: assignedMoney, MonthContributed: monthMoney, MonthPeriod: period, Protection: protection, Dependencies: dependencies, Now: now}
	if err = tx.Commit(ctx); err != nil {
		return result, err
	}
	result, err = planning.PlanSingleGoal(input)
	if err != nil {
		return result, reject("PLANNING_REJECTED", err)
	}
	snapshot := ledger.Snapshot()
	facts := make([]funds.BankCreditFact, len(snapshot.Origins))
	for i, v := range snapshot.Origins {
		facts[i] = v.Fact()
	}
	capture := capturedInput{pol, snapshot.ScopeAccountIDs, facts, snapshot.Locations, snapshot.Reservations, cash, targetMoney, assignedMoney, monthMoney, period, protection, dependencies, now}
	raw, err := encode(result)
	if err != nil {
		return result, err
	}
	inputRaw, err := encode(capture)
	if err != nil {
		return result, err
	}
	depRaw, err := encode(dependencies)
	if err != nil {
		return result, err
	}
	var grantID, grantVersion any
	if gid != "" {
		grantID = gid
		grantVersion = gv
	}
	_, err = s.writerExec(ctx, `INSERT INTO plans(environment_id,user_id,id,policy_id,policy_version,grant_id,grant_version,goal_id,candidate,input,effect_hash,input_hash,dependencies,created_at,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) ON CONFLICT(environment_id,user_id,id) DO NOTHING`, env.ID.String(), p.UserID.String(), result.ID.String(), pol.ID.String(), pol.Version, grantID, grantVersion, pol.Scope.GoalID.String(), raw, inputRaw, result.EffectHash, result.InputHash, depRaw, now, result.ValidUntil)
	return result, err
}

// A pending bank effect occupies the goal's remaining capacity until T3 has
// reconciled it. Income reservations alone cannot represent this capacity.
// The single-goal chain serializes new commands for this goal; original-key
// recovery remains available regardless of this gate.
func ensureGoalAvailable(ctx context.Context, q queryRow, p Principal, goal funds.ID) error {
	var pending bool
	err := q.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM operations o JOIN plans p ON p.environment_id=o.environment_id AND p.user_id=o.user_id AND p.id=o.plan_id WHERE o.environment_id=$1 AND o.user_id=$2 AND p.goal_id=$3 AND o.status NOT IN ('SETTLED','REJECTED','CANCELLED'))`, p.EnvironmentID.String(), p.UserID.String(), goal.String()).Scan(&pending)
	if err != nil {
		return err
	}
	if pending {
		return reject("GOAL_OPERATION_PENDING", nil)
	}
	var native bool
	if err = q.QueryRow(ctx, "SELECT to_regclass('native_operations') IS NOT NULL").Scan(&native); err != nil {
		return err
	}
	if native {
		if err = q.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND subject_goal_ids @> ARRAY[$3::uuid] AND status NOT IN ('SETTLED','REJECTED','CANCELLED'))`, p.EnvironmentID.String(), p.UserID.String(), goal.String()).Scan(&pending); err != nil {
			return err
		}
		if pending {
			return reject("GOAL_OPERATION_PENDING", nil)
		}
	}
	return nil
}

func loadCandidate(ctx context.Context, q queryRow, p Principal, id funds.ID) (planning.Candidate, error) {
	var raw, inputRaw []byte
	var result planning.Candidate
	if err := q.QueryRow(ctx, "SELECT candidate,input FROM plans WHERE environment_id=$1 AND user_id=$2 AND id=$3", p.EnvironmentID.String(), p.UserID.String(), id.String()).Scan(&raw, &inputRaw); err != nil {
		return result, reject("NOT_FOUND", err)
	}
	if err := decode(raw, &result); err != nil {
		return result, reject("EVIDENCE_INVALID", err)
	}
	if err := result.ValidateCandidate(); err != nil {
		return result, reject("EVIDENCE_INVALID", err)
	}
	if result.ID != id || result.Scope.EnvironmentID != p.EnvironmentID || result.Scope.UserID != p.UserID {
		return result, reject("FORBIDDEN", fmt.Errorf("plan belongs to different scope"))
	}
	replayed, err := replayCandidate(inputRaw)
	if err != nil {
		return result, reject("EVIDENCE_INVALID", err)
	}
	expected, err := encode(replayed)
	if err != nil || !bytes.Equal(raw, expected) {
		return result, reject("EVIDENCE_INVALID", err)
	}
	return result, nil
}

func replayCandidate(raw []byte) (planning.Candidate, error) {
	var in capturedInput
	if err := decode(raw, &in); err != nil {
		return planning.Candidate{}, err
	}
	origins := make([]funds.IncomeOrigin, len(in.OriginFacts))
	for i, fact := range in.OriginFacts {
		v, err := funds.NewIncomeOrigin(fact)
		if err != nil {
			return planning.Candidate{}, err
		}
		origins[i] = v
	}
	ledger, err := funds.NewIncomeLedger(funds.LedgerSnapshot{UserID: in.Policy.Scope.UserID, Currency: in.Policy.Scope.Currency, AsOf: in.Now, ScopeAccountIDs: in.ScopeAccounts, Origins: origins, Locations: in.Locations, Reservations: in.Reservations})
	if err != nil {
		return planning.Candidate{}, err
	}
	return planning.PlanSingleGoal(planning.Input{Policy: in.Policy, Ledger: ledger, Cash: in.Cash, GoalTarget: in.GoalTarget, GoalAssigned: in.GoalAssigned, MonthContributed: in.MonthContributed, MonthPeriod: in.MonthPeriod, Protection: in.Protection, Dependencies: in.Dependencies, Now: in.Now})
}
