// Package banksim provides a local simulated bank with its own PostgreSQL
// transactions and immutable originals. It never calls application storage.
package banksim

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	bankdb "zhiyu.local/zhiyu-go/db/bank"
	execution "zhiyu.local/zhiyu-go/internal/execution/domain"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

var (
	ErrConflict     = errors.New("IDEMPOTENCY_CONFLICT")
	ErrUnauthorized = errors.New("INVALID_BANK_PERMIT")
	ErrNotFound     = errors.New("BANK_NOT_FOUND")
	ErrNotDue       = errors.New("BANK_NOT_DUE")
	ErrPendingOrder = errors.New("EARLIER_ACCEPTED_OPERATION_DUE")
)

type Store struct {
	pool   *pgxpool.Pool
	secret []byte
}

// New accepts only an explicit isolated local bank database. No fallback DSN or
// shared application transaction exists.
func New(ctx context.Context, dsn string, permitSecret []byte) (*Store, error) {
	u, err := url.Parse(dsn)
	if err != nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") || len(permitSecret) < 32 {
		return nil, errors.New("explicit isolated bank DSN and permit secret required")
	}
	ip := net.ParseIP(u.Hostname())
	database := strings.TrimPrefix(u.Path, "/")
	if (u.Hostname() != "localhost" && (ip == nil || !ip.IsLoopback())) || !strings.HasPrefix(database, "zygo_") || !strings.HasSuffix(database, "_bank") {
		return nil, errors.New("bank DSN must address a local zygo_*_bank database")
	}
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, errors.New("bank pool configuration failed")
	}
	if err = pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, errors.New("bank database unavailable")
	}
	return &Store{pool: pool, secret: append([]byte(nil), permitSecret...)}, nil
}

func (s *Store) Close() { s.pool.Close() }
func (s *Store) Migrate(ctx context.Context) error {
	_, err := s.pool.Exec(ctx, bankdb.Schema)
	return err
}

func userLock(ctx context.Context, tx pgx.Tx, namespace, user string) error {
	_, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", namespace+"/user/"+user)
	return err
}
func keyLock(ctx context.Context, tx pgx.Tx, namespace, key string) error {
	_, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,1))", namespace+"/key/"+key)
	return err
}

// OpenAccount is a trusted fixture/import operation. HTTP handlers never expose
// it and missing balances are never invented from application projections.
func (s *Store) OpenAccount(ctx context.Context, namespace, user, account string, balance funds.Money) error {
	if !validIdentity(namespace, user, account) {
		return errors.New("invalid opening identity")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = userLock(ctx, tx, namespace, user); err != nil {
		return err
	}
	var existing int64
	err = tx.QueryRow(ctx, "SELECT opening_cents FROM bank.opening_anchors WHERE namespace=$1 AND user_id=$2 AND account_id=$3", namespace, user, account).Scan(&existing)
	if err == nil {
		if existing != balance.Cents() {
			return ErrConflict
		}
		return tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO bank.opening_anchors VALUES($1,$2,$3,$4)", namespace, user, account, balance.Cents()); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO bank.accounts(namespace,user_id,account_id,balance_cents) VALUES($1,$2,$3,$4)", namespace, user, account, balance.Cents()); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) OpenGoal(ctx context.Context, namespace, user, goal, account string) error {
	if !validIdentity(namespace, user, goal, account) {
		return errors.New("invalid goal identity")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = userLock(ctx, tx, namespace, user); err != nil {
		return err
	}
	var existing string
	err = tx.QueryRow(ctx, "SELECT account_id FROM bank.goals WHERE namespace=$1 AND user_id=$2 AND goal_id=$3", namespace, user, goal).Scan(&existing)
	if err == nil {
		if existing != account {
			return ErrConflict
		}
		return tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO bank.goals(namespace,user_id,goal_id,account_id) VALUES($1,$2,$3,$4)", namespace, user, goal, account); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) OpenIncome(ctx context.Context, namespace, user, origin, account string, amount funds.Money, occurredAt time.Time, evidenceHash string) error {
	if !validIdentity(namespace, user, origin, account) || amount.IsZero() || occurredAt.IsZero() || len(evidenceHash) != 64 {
		return errors.New("invalid income anchor")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = userLock(ctx, tx, namespace, user); err != nil {
		return err
	}
	var oldAccount, oldHash string
	var oldAmount int64
	var oldTime time.Time
	err = tx.QueryRow(ctx, "SELECT original_account_id,original_cents,occurred_at,evidence_hash FROM bank.origins WHERE namespace=$1 AND user_id=$2 AND origin_id=$3", namespace, user, origin).Scan(&oldAccount, &oldAmount, &oldTime, &oldHash)
	if err == nil {
		if oldAccount != account || oldAmount != amount.Cents() || !oldTime.Equal(occurredAt) || oldHash != evidenceHash {
			return ErrConflict
		}
		return tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return err
	}
	var balance, located int64
	if err = tx.QueryRow(ctx, "SELECT balance_cents FROM bank.accounts WHERE namespace=$1 AND user_id=$2 AND account_id=$3", namespace, user, account).Scan(&balance); err != nil {
		return err
	}
	if err = tx.QueryRow(ctx, "SELECT coalesce(sum(available_cents+assigned_cents),0)::bigint FROM bank.income_locations WHERE namespace=$1 AND user_id=$2 AND account_id=$3", namespace, user, account).Scan(&located); err != nil {
		return err
	}
	if located > balance-amount.Cents() {
		return errors.New("income anchors exceed independent cash")
	}
	if _, err = tx.Exec(ctx, "INSERT INTO bank.origins VALUES($1,$2,$3,$4,$5,$6,$7)", namespace, user, origin, account, amount.Cents(), occurredAt.UTC(), evidenceHash); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO bank.income_locations(namespace,user_id,origin_id,account_id,available_cents) VALUES($1,$2,$3,$4,$5)", namespace, user, origin, account, amount.Cents()); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func validIdentity(namespace string, ids ...string) bool {
	if !strings.HasPrefix(namespace, "zygo_") || len(namespace) > 69 {
		return false
	}
	for _, id := range ids {
		if id == "" || len(id) > 128 || strings.ContainsAny(id, "/\\\x00\r\n") {
			return false
		}
	}
	return true
}

func (s *Store) Submit(ctx context.Context, request execution.BankRequest, signature string, now time.Time) (execution.BankResult, error) {
	if err := request.Validate(); err != nil {
		return execution.BankResult{}, err
	}
	if !execution.VerifyPermit(request, signature, s.secret) {
		return execution.BankResult{}, ErrUnauthorized
	}
	digest, _ := request.Digest()
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return execution.BankResult{}, err
	}
	defer tx.Rollback(ctx)
	if err = keyLock(ctx, tx, request.Namespace, request.Key); err != nil {
		return execution.BankResult{}, err
	}
	if err = userLock(ctx, tx, request.Namespace, request.UserID); err != nil {
		return execution.BankResult{}, err
	}
	existing, err := loadResult(ctx, tx, request.Namespace, request.UserID, request.Key)
	if err == nil && existing.Status != execution.BankNotFound {
		if existing.RequestHash != digest {
			return execution.BankResult{}, ErrConflict
		}
		if err = tx.Commit(ctx); err != nil {
			return execution.BankResult{}, err
		}
		return existing, nil
	}
	if err != nil {
		return execution.BankResult{}, err
	}
	// The primary key is namespace/key, including keys submitted for another
	// user. Such a collision cannot disclose or replace the original request.
	var keyExists bool
	if err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM bank.operations WHERE namespace=$1 AND operation_key=$2)", request.Namespace, request.Key).Scan(&keyExists); err != nil {
		return execution.BankResult{}, err
	}
	if keyExists {
		return execution.BankResult{}, ErrConflict
	}
	if err = requireOrder(ctx, tx, request.Namespace, request.UserID, now, ""); err != nil {
		return execution.BankResult{}, err
	}
	rejection, err := validateFunds(ctx, tx, request, now)
	if err != nil {
		return execution.BankResult{}, err
	}
	available := now.UTC().AddDate(0, 0, request.SettlementDelayDays)
	if available.After(request.LatestArrivalAt) {
		rejection = "ARRIVAL_BOUND_EXCEEDED"
	}
	raw, _ := request.CanonicalBytes()
	if rejection != "" {
		_, err = tx.Exec(ctx, "INSERT INTO bank.operations(namespace,user_id,operation_key,operation_id,request_hash,request,status,rejection_code) VALUES($1,$2,$3,$4,$5,$6,'REJECTED',$7)", request.Namespace, request.UserID, request.Key, request.OperationID, digest, raw, rejection)
	} else {
		_, err = tx.Exec(ctx, "INSERT INTO bank.operations(namespace,user_id,operation_key,operation_id,request_hash,request,status,accepted_at,available_at) VALUES($1,$2,$3,$4,$5,$6,'ACCEPTED',$7,$8)", request.Namespace, request.UserID, request.Key, request.OperationID, digest, raw, now.UTC(), available)
		if err == nil {
			_, err = tx.Exec(ctx, "UPDATE bank.accounts SET reserved_cents=reserved_cents+$4 WHERE namespace=$1 AND user_id=$2 AND account_id=$3", request.Namespace, request.UserID, request.SourceAccountID, request.Amount.Cents())
		}
		if err == nil {
			_, err = tx.Exec(ctx, "UPDATE bank.income_locations SET reserved_cents=reserved_cents+$5 WHERE namespace=$1 AND user_id=$2 AND origin_id=$3 AND account_id=$4", request.Namespace, request.UserID, request.IncomeOriginID, request.SourceAccountID, request.Amount.Cents())
		}
		if err == nil && !available.After(now) {
			err = settle(ctx, tx, request, available)
		}
	}
	if err != nil {
		return execution.BankResult{}, err
	}
	result, err := loadResult(ctx, tx, request.Namespace, request.UserID, request.Key)
	if err != nil {
		return execution.BankResult{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return execution.BankResult{}, err
	}
	return result, nil
}

func validateFunds(ctx context.Context, tx pgx.Tx, r execution.BankRequest, now time.Time) (string, error) {
	if r.RequestedAt.After(now) {
		return "FUTURE_REQUEST", nil
	}
	var balance, reserved, goals int64
	err := tx.QueryRow(ctx, "SELECT balance_cents,reserved_cents,(SELECT coalesce(sum(g.balance_cents),0)::bigint FROM bank.goals g WHERE g.namespace=a.namespace AND g.user_id=a.user_id AND g.account_id=a.account_id) FROM bank.accounts a WHERE namespace=$1 AND user_id=$2 AND account_id=$3", r.Namespace, r.UserID, r.SourceAccountID).Scan(&balance, &reserved, &goals)
	if errors.Is(err, pgx.ErrNoRows) {
		return "SOURCE_ACCOUNT_NOT_FOUND", nil
	}
	if err != nil {
		return "", err
	}
	if balance-reserved-goals < r.Amount.Cents() {
		return "INSUFFICIENT_UNASSIGNED_CASH", nil
	}
	var destBalance int64
	err = tx.QueryRow(ctx, "SELECT balance_cents FROM bank.accounts WHERE namespace=$1 AND user_id=$2 AND account_id=$3", r.Namespace, r.UserID, r.DestinationAccountID).Scan(&destBalance)
	if errors.Is(err, pgx.ErrNoRows) {
		return "DESTINATION_ACCOUNT_NOT_FOUND", nil
	}
	if err != nil {
		return "", err
	}
	if r.Kind == "TRANSFER" && destBalance > funds.MaxMoneyCents-r.Amount.Cents() {
		return "DESTINATION_CAPACITY", nil
	}
	if r.Kind == "TRANSFER" {
		var incoming int64
		if err = tx.QueryRow(ctx, "SELECT coalesce(sum((request->>'amount_cents')::bigint),0)::bigint FROM bank.operations WHERE namespace=$1 AND user_id=$2 AND status='ACCEPTED' AND request->>'kind'='TRANSFER' AND request->>'destination_account_id'=$3", r.Namespace, r.UserID, r.DestinationAccountID).Scan(&incoming); err != nil {
			return "", err
		}
		if incoming > funds.MaxMoneyCents-destBalance-r.Amount.Cents() {
			return "DESTINATION_CAPACITY", nil
		}
	}
	var available, held int64
	var occurred time.Time
	err = tx.QueryRow(ctx, "SELECT l.available_cents,l.reserved_cents,o.occurred_at FROM bank.income_locations l JOIN bank.origins o USING(namespace,user_id,origin_id) WHERE l.namespace=$1 AND l.user_id=$2 AND l.origin_id=$3 AND l.account_id=$4", r.Namespace, r.UserID, r.IncomeOriginID, r.SourceAccountID).Scan(&available, &held, &occurred)
	if errors.Is(err, pgx.ErrNoRows) {
		return "INCOME_SOURCE_NOT_FOUND", nil
	}
	if err != nil {
		return "", err
	}
	if occurred.After(now) {
		return "INCOME_NOT_RECEIVED", nil
	}
	if available-held < r.Amount.Cents() {
		return "INSUFFICIENT_AVAILABLE_INCOME", nil
	}
	if r.Kind == "ALLOCATE_GOAL" {
		var account string
		err = tx.QueryRow(ctx, "SELECT account_id FROM bank.goals WHERE namespace=$1 AND user_id=$2 AND goal_id=$3", r.Namespace, r.UserID, r.GoalID).Scan(&account)
		if errors.Is(err, pgx.ErrNoRows) || err == nil && account != r.SourceAccountID {
			return "GOAL_NOT_FOUND_OR_ACCOUNT_MISMATCH", nil
		}
		if err != nil {
			return "", err
		}
	}
	return "", nil
}

func requireOrder(ctx context.Context, tx pgx.Tx, namespace, user string, now time.Time, ownKey string) error {
	var first string
	err := tx.QueryRow(ctx, "SELECT operation_key FROM bank.operations WHERE namespace=$1 AND user_id=$2 AND status='ACCEPTED' AND available_at<=$3 ORDER BY available_at,sequence LIMIT 1", namespace, user, now.UTC()).Scan(&first)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	if first != ownKey {
		return ErrPendingOrder
	}
	return nil
}

func settle(ctx context.Context, tx pgx.Tx, r execution.BankRequest, available time.Time) error {
	amount := r.Amount.Cents()
	if r.Kind == "TRANSFER" {
		if _, err := tx.Exec(ctx, "UPDATE bank.accounts SET balance_cents=balance_cents-$4,reserved_cents=reserved_cents-$4 WHERE namespace=$1 AND user_id=$2 AND account_id=$3", r.Namespace, r.UserID, r.SourceAccountID, amount); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "UPDATE bank.accounts SET balance_cents=balance_cents+$4 WHERE namespace=$1 AND user_id=$2 AND account_id=$3", r.Namespace, r.UserID, r.DestinationAccountID, amount); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "UPDATE bank.income_locations SET available_cents=available_cents-$5,reserved_cents=reserved_cents-$5 WHERE namespace=$1 AND user_id=$2 AND origin_id=$3 AND account_id=$4", r.Namespace, r.UserID, r.IncomeOriginID, r.SourceAccountID, amount); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "INSERT INTO bank.income_locations(namespace,user_id,origin_id,account_id,available_cents) VALUES($1,$2,$3,$4,$5) ON CONFLICT(namespace,user_id,origin_id,account_id) DO UPDATE SET available_cents=bank.income_locations.available_cents+excluded.available_cents", r.Namespace, r.UserID, r.IncomeOriginID, r.DestinationAccountID, amount); err != nil {
			return err
		}
	} else {
		if _, err := tx.Exec(ctx, "UPDATE bank.accounts SET reserved_cents=reserved_cents-$4 WHERE namespace=$1 AND user_id=$2 AND account_id=$3", r.Namespace, r.UserID, r.SourceAccountID, amount); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "UPDATE bank.goals SET balance_cents=balance_cents+$4 WHERE namespace=$1 AND user_id=$2 AND goal_id=$3", r.Namespace, r.UserID, r.GoalID, amount); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "UPDATE bank.income_locations SET available_cents=available_cents-$5,assigned_cents=assigned_cents+$5,reserved_cents=reserved_cents-$5 WHERE namespace=$1 AND user_id=$2 AND origin_id=$3 AND account_id=$4", r.Namespace, r.UserID, r.IncomeOriginID, r.SourceAccountID, amount); err != nil {
			return err
		}
	}
	for _, posting := range expectedPostings(r, available) {
		if _, err := tx.Exec(ctx, "INSERT INTO bank.postings(id,namespace,operation_key,dimension,ledger_id,direction,amount_cents,occurred_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)", posting.ID, r.Namespace, r.Key, posting.Dimension, posting.LedgerID, posting.Direction, amount, available); err != nil {
			return err
		}
	}
	_, err := tx.Exec(ctx, "UPDATE bank.operations SET status='SETTLED',version=version+1,settled_at=available_at WHERE namespace=$1 AND operation_key=$2 AND status='ACCEPTED'", r.Namespace, r.Key)
	return err
}

func expectedPostings(r execution.BankRequest, at time.Time) []execution.BankPosting {
	type leg struct{ dimension, ledger, direction string }
	var legs []leg
	if r.Kind == "TRANSFER" {
		legs = []leg{{"ECONOMIC", "CASH:" + r.SourceAccountID, "DEBIT"}, {"ECONOMIC", "CASH:" + r.DestinationAccountID, "CREDIT"}, {"INCOME_LOCATION", "AVAILABLE:" + r.IncomeOriginID + ":" + r.SourceAccountID, "DEBIT"}, {"INCOME_LOCATION", "AVAILABLE:" + r.IncomeOriginID + ":" + r.DestinationAccountID, "CREDIT"}}
	} else {
		legs = []leg{{"GOAL_OWNERSHIP", "UNASSIGNED:" + r.SourceAccountID, "DEBIT"}, {"GOAL_OWNERSHIP", "GOAL:" + r.GoalID, "CREDIT"}, {"INCOME_LOCATION", "AVAILABLE:" + r.IncomeOriginID + ":" + r.SourceAccountID, "DEBIT"}, {"INCOME_LOCATION", "ASSIGNED:" + r.IncomeOriginID + ":" + r.SourceAccountID, "CREDIT"}}
	}
	postings := make([]execution.BankPosting, 0, len(legs))
	digest, _ := r.Digest()
	for i, leg := range legs {
		postings = append(postings, execution.BankPosting{ID: fmt.Sprintf("%s:%d", digest, i+1), Dimension: leg.dimension, LedgerID: leg.ledger, Direction: leg.direction, Amount: r.Amount, OccurredAt: at.UTC()})
	}
	return postings
}

type querier interface {
	QueryRow(context.Context, string, ...any) pgx.Row
	Query(context.Context, string, ...any) (pgx.Rows, error)
}

func loadResult(ctx context.Context, q querier, namespace, user, key string) (execution.BankResult, error) {
	r := execution.BankResult{Status: execution.BankNotFound, Namespace: namespace, UserID: user, Key: key, AbsenceIsFinal: false, Postings: []execution.BankPosting{}}
	var raw []byte
	err := q.QueryRow(ctx, "SELECT request,request_hash,sequence,status,version,accepted_at,available_at,settled_at,rejection_code FROM bank.operations WHERE namespace=$1 AND user_id=$2 AND operation_key=$3", namespace, user, key).Scan(&raw, &r.RequestHash, &r.Sequence, &r.Status, &r.Version, &r.AcceptedAt, &r.AvailableAt, &r.SettledAt, &r.RejectionCode)
	if errors.Is(err, pgx.ErrNoRows) {
		return r, nil
	}
	if err != nil {
		return r, err
	}
	if err = json.Unmarshal(raw, &r.Request); err != nil {
		return r, err
	}
	r.AbsenceIsFinal = r.Status == execution.BankRejected
	rows, err := q.Query(ctx, "SELECT id,dimension,ledger_id,direction,amount_cents,occurred_at FROM bank.postings WHERE namespace=$1 AND operation_key=$2 ORDER BY id", namespace, key)
	if err != nil {
		return r, err
	}
	defer rows.Close()
	for rows.Next() {
		var p execution.BankPosting
		var amount int64
		if err = rows.Scan(&p.ID, &p.Dimension, &p.LedgerID, &p.Direction, &amount, &p.OccurredAt); err != nil {
			return r, err
		}
		p.Amount, err = funds.NewMoney(amount)
		if err != nil {
			return r, err
		}
		r.Postings = append(r.Postings, p)
	}
	return r, rows.Err()
}

func (s *Store) Lookup(ctx context.Context, namespace, user, key string) (execution.BankResult, error) {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return execution.BankResult{}, err
	}
	defer tx.Rollback(ctx)
	result, err := loadResult(ctx, tx, namespace, user, key)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

// Settle changes only the already accepted original. It cannot change request,
// available_at, amount, authorization or key. Read methods never settle.
func (s *Store) Settle(ctx context.Context, namespace, user, key string, now time.Time) (execution.BankResult, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return execution.BankResult{}, err
	}
	defer tx.Rollback(ctx)
	if err = userLock(ctx, tx, namespace, user); err != nil {
		return execution.BankResult{}, err
	}
	r, err := loadResult(ctx, tx, namespace, user, key)
	if err != nil {
		return r, err
	}
	if r.Status == execution.BankNotFound {
		return r, ErrNotFound
	}
	if r.Status == execution.BankAccepted {
		if r.AvailableAt.After(now) {
			return r, ErrNotDue
		}
		if err = requireOrder(ctx, tx, namespace, user, *r.AvailableAt, key); err != nil {
			return r, err
		}
		if err = settle(ctx, tx, r.Request, *r.AvailableAt); err != nil {
			return r, err
		}
		r, err = loadResult(ctx, tx, namespace, user, key)
		if err != nil {
			return r, err
		}
	}
	if err = tx.Commit(ctx); err != nil {
		return r, err
	}
	return r, nil
}

// Inventory captures a committed per-user sequence watermark under the same
// serialization lock used by submissions. Subsequent pages cannot miss an
// earlier transaction whose identity sequence was allocated before commit.
// Statuses remain current observations, not a historical snapshot at watermark.
func (s *Store) Inventory(ctx context.Context, namespace, user string, after, watermark int64, limit int) (execution.BankInventory, error) {
	r := execution.BankInventory{Namespace: namespace, UserID: user, Operations: []execution.BankResult{}}
	if !validIdentity(namespace, user) || after < 0 || watermark < 0 || limit < 1 || limit > 500 {
		return r, errors.New("invalid inventory bounds")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return r, err
	}
	defer tx.Rollback(ctx)
	if err = userLock(ctx, tx, namespace, user); err != nil {
		return r, err
	}
	if watermark == 0 {
		if err = tx.QueryRow(ctx, "SELECT coalesce(max(sequence),0) FROM bank.operations WHERE namespace=$1 AND user_id=$2", namespace, user).Scan(&watermark); err != nil {
			return r, err
		}
	}
	r.Watermark = watermark
	r.NextAfter = after
	rows, err := tx.Query(ctx, "SELECT operation_key,sequence FROM bank.operations WHERE namespace=$1 AND user_id=$2 AND sequence>$3 AND sequence<=$4 ORDER BY sequence LIMIT $5", namespace, user, after, watermark, limit+1)
	if err != nil {
		return r, err
	}
	type entry struct {
		key      string
		sequence int64
	}
	entries := []entry{}
	for rows.Next() {
		var e entry
		if err = rows.Scan(&e.key, &e.sequence); err != nil {
			rows.Close()
			return r, err
		}
		entries = append(entries, e)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return r, err
	}
	if len(entries) > limit {
		r.HasMore = true
		entries = entries[:limit]
	}
	for _, e := range entries {
		result, err := loadResult(ctx, tx, namespace, user, e.key)
		if err != nil {
			return r, err
		}
		r.Operations = append(r.Operations, result)
		r.NextAfter = e.sequence
	}
	return r, tx.Commit(ctx)
}
