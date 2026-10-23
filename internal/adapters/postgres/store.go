// Package postgres supplies explicit pgx transactions and embedded migrations.
// Transaction ownership and the environment/user lock order belong to app.
package postgres

import (
	"context"
	"database/sql"
	"errors"
	"io/fs"

	"github.com/jackc/pgx/v5/pgxpool"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/pressly/goose/v3"
	"github.com/pressly/goose/v3/lock"

	appdb "zhiyu.local/zhiyu-go/db"
)

type Store struct{ Pool *pgxpool.Pool }

func New(ctx context.Context, dsn string) (*Store, error) {
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, errors.New("invalid application PostgreSQL configuration")
	}
	config.MaxConns = 10
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, errors.New("cannot create application PostgreSQL pool")
	}
	if err = pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, errors.New("application PostgreSQL connection failed")
	}
	return &Store{Pool: pool}, nil
}

func (s *Store) Close() {
	if s != nil && s.Pool != nil {
		s.Pool.Close()
	}
}

// Migrate requires the separate schema-owner DSN. App credentials intentionally
// have neither schema ownership nor access to the Goose version table.
func Migrate(ctx context.Context, migrationDSN string) error {
	conn, err := sql.Open("pgx", migrationDSN)
	if err != nil {
		return errors.New("invalid migration PostgreSQL configuration")
	}
	defer conn.Close()
	conn.SetMaxOpenConns(1)
	if err = conn.PingContext(ctx); err != nil {
		return errors.New("migration PostgreSQL connection failed")
	}
	migrations, err := fs.Sub(appdb.Migrations, "migrations")
	if err != nil {
		return err
	}
	locker, err := lock.NewPostgresSessionLocker()
	if err != nil {
		return err
	}
	provider, err := goose.NewProvider(goose.DialectPostgres, conn, migrations, goose.WithSessionLocker(locker))
	if err != nil {
		return err
	}
	_, err = provider.Up(ctx)
	return err
}
