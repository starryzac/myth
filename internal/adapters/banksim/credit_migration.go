package banksim

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	bankdb "zhiyu.local/zhiyu-go/db/bank"
	"zhiyu.local/zhiyu-go/internal/execution/bankcredit"
)

const frozenBankCreditMigration = "4aba3912c62d51cb678af5fd0336945a6e0a9de0f8abb8cb75c449c40fcd4c57"

func (s *Store) CheckExternalCredit(ctx context.Context) error {
	var hash string
	if err := s.pool.QueryRow(ctx, `SELECT sha256 FROM bank.protocol_migrations WHERE version=3`).Scan(&hash); err != nil || hash != frozenBankCreditMigration {
		return errors.New("external credit schema unavailable or differs from freeze")
	}
	return nil
}

func (s *Store) MigrateExternalCredit(ctx context.Context) error {
	if bankcredit.Hash([]byte(bankdb.ExternalCreditSchema)) != frozenBankCreditMigration {
		return errors.New("external credit migration source differs from freeze")
	}
	if err := s.MigrateV2(ctx); err != nil {
		return err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended('zhiyu:bank:external-credit:migration',0))`); err != nil {
		return err
	}
	var existing string
	err = tx.QueryRow(ctx, `SELECT sha256 FROM bank.protocol_migrations WHERE version=3`).Scan(&existing)
	if err == nil {
		if existing != frozenBankCreditMigration {
			return errors.New("applied external credit migration differs from freeze")
		}
		return tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return err
	}
	if _, err = tx.Exec(ctx, bankdb.ExternalCreditSchema); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO bank.protocol_migrations(version,sha256) VALUES(3,$1)`, frozenBankCreditMigration); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
