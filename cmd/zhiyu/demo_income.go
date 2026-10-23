package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"zhiyu.local/zhiyu-go/internal/adapters/banksim"
	"zhiyu.local/zhiyu-go/internal/app"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	"zhiyu.local/zhiyu-go/internal/platform"
)

func demoIncome(ctx context.Context, service *app.Service, c platform.Runtime, cents int64, out io.Writer) error {
	amount, err := funds.NewMoney(cents)
	if err != nil || amount.IsZero() {
		return errors.New("demo income amount must be positive supported integer cents")
	}
	// The file lock protects first preparation across trusted CLI processes.
	// A stale lock is retained for explicit inspection; never silently replace
	// a prepared bank origin after an interrupted run.
	lock, err := os.OpenFile(c.SeedState+".lock", os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return errors.New("persistent demo state is locked; inspect the retained preparation before retry")
	}
	defer os.Remove(c.SeedState + ".lock")
	defer lock.Close()
	state, err := readSeed(c.SeedState)
	if err != nil {
		return err
	}
	if state.Namespace != c.Namespace {
		return errors.New("runtime and retained demo namespace differ")
	}
	if state.Income == nil {
		authority, err := service.PrepareDemoIncome(ctx, state.SeedIdentity)
		if err != nil {
			return fmt.Errorf("demo income requires prior real USER policy and grant confirmation (%s)", app.Code(err))
		}
		origin, err := commandID()
		if err != nil {
			return errors.New("demo origin identity unavailable")
		}
		evidence, err := commandID()
		if err != nil {
			return errors.New("demo evidence identity unavailable")
		}
		at := time.Now().UTC().Truncate(time.Microsecond)
		if at.Before(authority.EffectiveFrom) {
			return errors.New("demo income cannot predate its confirmed authority")
		}
		prepared := app.DemoIncomePreparation{OriginID: origin, EvidenceID: evidence, Amount: amount, OccurredAt: at, Authority: authority}
		prepared.EvidenceHash, err = app.DemoIncomeHash(state.SeedIdentity, prepared)
		if err != nil {
			return errors.New("demo original cannot be frozen")
		}
		state.Income = &prepared
		if err = saveSeed(c.SeedState, state); err != nil {
			return err
		}
	}
	if state.Income.Amount != amount {
		return errors.New("demo income amount conflicts with the first immutable preparation")
	}
	expected, err := app.DemoIncomeHash(state.SeedIdentity, *state.Income)
	if err != nil || state.Income.EvidenceHash != expected {
		return errors.New("retained demo income preparation does not match its original hash")
	}
	bank, err := banksim.New(ctx, c.BankDSN, []byte(c.BankPermitSecret))
	if err != nil {
		return errors.New("independent seed bank unavailable")
	}
	defer bank.Close()
	// Each anchor is independently idempotent and checks the immutable opening
	// amount/origin occurrence/evidence. Partial failure retries these same IDs.
	if bank.OpenAccount(ctx, c.Namespace, state.UserID.String(), state.AccountID.String(), state.Income.Amount) != nil {
		return errors.New("independent original account anchor failed; retained preparation unchanged")
	}
	if bank.OpenIncome(ctx, c.Namespace, state.UserID.String(), state.Income.OriginID.String(), state.AccountID.String(), state.Income.Amount, state.Income.OccurredAt, state.Income.EvidenceHash) != nil {
		return errors.New("independent original income anchor failed; retained preparation unchanged")
	}
	if bank.OpenGoal(ctx, c.Namespace, state.UserID.String(), state.GoalID.String(), state.AccountID.String()) != nil {
		return errors.New("independent original goal anchor failed; retained preparation unchanged")
	}
	facts, err := readDemoBankFacts(ctx, c.BankDSN, state.SeedIdentity, *state.Income)
	if err != nil {
		return err
	}
	state.BankObservation = &facts
	if err = saveSeed(c.SeedState, state); err != nil {
		return err
	}
	result, err := service.ImportDemoIncome(ctx, state.SeedIdentity, *state.Income, facts)
	if err != nil {
		return fmt.Errorf("verified demo fact import failed (%s); original preparation retained", app.Code(err))
	}
	state.ImportResult = &result
	if err = saveSeed(c.SeedState, state); err != nil {
		return err
	}
	return json.NewEncoder(out).Encode(struct {
		app.DemoIncomeResult
		SeedState                 string `json:"seed_state"`
		OriginalBankFactsVerified bool   `json:"original_bank_facts_verified"`
	}{result, c.SeedState, true})
}

func readDemoBankFacts(ctx context.Context, dsn string, seed app.SeedIdentity, in app.DemoIncomePreparation) (app.DemoBankFacts, error) {
	var result app.DemoBankFacts
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return result, errors.New("bank fact reader unavailable")
	}
	defer pool.Close()
	tx, err := pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return result, errors.New("bank fact observation unavailable")
	}
	defer tx.Rollback(ctx)
	var originalAccount, goalAccount string
	var opening, balance, accountReserved, original, available, assigned, incomeReserved, owned int64
	err = tx.QueryRow(ctx, `SELECT a.opening_cents,c.balance_cents,c.reserved_cents,o.original_account_id,o.original_cents,o.occurred_at,o.evidence_hash,l.available_cents,l.assigned_cents,l.reserved_cents,g.account_id,g.balance_cents,(SELECT count(*) FROM bank.operations b WHERE b.namespace=$1 AND b.user_id=$2) FROM bank.opening_anchors a JOIN bank.accounts c USING(namespace,user_id,account_id) JOIN bank.origins o ON(o.namespace=a.namespace AND o.user_id=a.user_id AND o.origin_id=$4) JOIN bank.income_locations l ON(l.namespace=o.namespace AND l.user_id=o.user_id AND l.origin_id=o.origin_id AND l.account_id=a.account_id) JOIN bank.goals g ON(g.namespace=a.namespace AND g.user_id=a.user_id AND g.goal_id=$5) WHERE a.namespace=$1 AND a.user_id=$2 AND a.account_id=$3`, seed.Namespace, seed.UserID.String(), seed.AccountID.String(), in.OriginID.String(), seed.GoalID.String()).Scan(&opening, &balance, &accountReserved, &originalAccount, &original, &result.OccurredAt, &result.EvidenceHash, &available, &assigned, &incomeReserved, &goalAccount, &owned, &result.OperationCount)
	if err != nil || originalAccount != seed.AccountID.String() || goalAccount != seed.AccountID.String() {
		return result, errors.New("independent bank original source/account/goal facts are incomplete")
	}
	values := []int64{opening, balance, accountReserved, original, available, assigned, incomeReserved, owned}
	converted := make([]funds.Money, len(values))
	for i, n := range values {
		converted[i], err = funds.NewMoney(n)
		if err != nil {
			return result, errors.New("independent bank facts exceed supported money capacity")
		}
	}
	result.Namespace = seed.Namespace
	result.UserID = seed.UserID
	result.AccountID = seed.AccountID
	result.GoalID = seed.GoalID
	result.OriginID = in.OriginID
	result.OpeningAmount = converted[0]
	result.Balance = converted[1]
	result.AccountReserved = converted[2]
	result.OriginalAmount = converted[3]
	result.Available = converted[4]
	result.Assigned = converted[5]
	result.IncomeReserved = converted[6]
	result.GoalOwned = converted[7]
	result.OccurredAt = result.OccurredAt.UTC()
	result.ObservedAt = time.Now().UTC().Truncate(time.Microsecond)
	if err = tx.Commit(ctx); err != nil {
		return result, errors.New("bank fact observation could not complete")
	}
	return result, nil
}
