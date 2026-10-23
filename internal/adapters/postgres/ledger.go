package postgres

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

var ErrLedgerConflict = errors.New("persisted income history differs from complete immutable ledger")

func parseID(s string) (funds.ID, error)      { return funds.ParseID(s) }
func parseMoney(n int64) (funds.Money, error) { return funds.NewMoney(n) }

func nativeLedgerEnabled(ctx context.Context, tx pgx.Tx) (bool, error) {
	var enabled bool
	err := tx.QueryRow(ctx, `SELECT to_regclass('public.native_income_reservations') IS NOT NULL`).Scan(&enabled)
	return enabled, err
}

// LoadLedger reads a complete scoped snapshot in the caller's transaction.
// The caller must authenticate ownership and acquire the user funds row lock.
// It does not infer evidence authenticity from a database source label.
func (s *Store) LoadLedger(ctx context.Context, tx pgx.Tx, env, user funds.ID, now time.Time) (funds.IncomeLedger, error) {
	if tx == nil || now.IsZero() {
		return funds.IncomeLedger{}, ErrLedgerConflict
	}
	native, err := nativeLedgerEnabled(ctx, tx)
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	snapshot := funds.LedgerSnapshot{UserID: user, Currency: funds.CNY, AsOf: now.UTC()}
	rows, err := tx.Query(ctx, `SELECT id::text FROM accounts WHERE environment_id=$1 AND user_id=$2 ORDER BY id`, env.String(), user.String())
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	for rows.Next() {
		var value string
		if err = rows.Scan(&value); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		id, e := parseID(value)
		if e != nil {
			rows.Close()
			return funds.IncomeLedger{}, e
		}
		snapshot.ScopeAccountIDs = append(snapshot.ScopeAccountIDs, id)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	rows, err = tx.Query(ctx, `SELECT id::text,original_account_id::text,currency,amount_cents,occurred_at,observed_at,recorded_at,evidence_id::text,evidence_hash::text FROM income_origins WHERE environment_id=$1 AND user_id=$2 ORDER BY id`, env.String(), user.String())
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	for rows.Next() {
		var id, account, currency, evidence, hash string
		var amount int64
		var f funds.BankCreditFact
		if err = rows.Scan(&id, &account, &currency, &amount, &f.OccurredAt, &f.ObservedAt, &f.RecordedAt, &evidence, &hash); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if f.TransactionID, err = parseID(id); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if f.AccountID, err = parseID(account); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if f.EvidenceID, err = parseID(evidence); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if f.EvidenceHash, err = funds.ParseEvidenceHash(hash); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if f.Amount, err = parseMoney(amount); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		f.Currency = funds.Currency(currency)
		f.Role = funds.RoleIncome
		f.Source = funds.BankConfirmed
		origin, e := funds.NewIncomeOrigin(f)
		if e != nil {
			rows.Close()
			return funds.IncomeLedger{}, e
		}
		snapshot.Origins = append(snapshot.Origins, origin)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	rows, err = tx.Query(ctx, `SELECT id::text,origin_id::text,account_id::text,spent_cents,assigned_cents,reserved_cents,legacy_reserved_cents,available_cents FROM income_locations WHERE environment_id=$1 AND user_id=$2 ORDER BY id`, env.String(), user.String())
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	for rows.Next() {
		var id, origin, account string
		var spent, assigned, reserved, legacy, available int64
		if err = rows.Scan(&id, &origin, &account, &spent, &assigned, &reserved, &legacy, &available); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		l := funds.IncomeLocation{}
		if l.FragmentID, err = parseID(id); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if l.OriginID, err = parseID(origin); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if l.AccountID, err = parseID(account); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if l.Spent, err = parseMoney(spent); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if l.Assigned, err = parseMoney(assigned); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if l.Reserved, err = parseMoney(reserved); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if l.LegacyReserved, err = parseMoney(legacy); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if l.Available, err = parseMoney(available); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		snapshot.Locations = append(snapshot.Locations, l)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	reservationQuery := `SELECT action_id::text,operation,destination_account_id::text,state FROM income_reservations WHERE environment_id=$1 AND user_id=$2`
	if native {
		reservationQuery += ` UNION ALL SELECT action_id::text,operation,destination_account_id::text,state FROM native_income_reservations WHERE environment_id=$1 AND user_id=$2`
	}
	rows, err = tx.Query(ctx, reservationQuery+` ORDER BY 1`, env.String(), user.String())
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	for rows.Next() {
		var action, operation, state string
		var destination *string
		if err = rows.Scan(&action, &operation, &destination, &state); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		r := funds.IncomeReservation{Operation: funds.IncomeOperation(operation), State: funds.ReservationState(state)}
		if r.ActionID, err = parseID(action); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if destination != nil {
			dest, e := parseID(*destination)
			if e != nil {
				rows.Close()
				return funds.IncomeLedger{}, e
			}
			r.DestinationAccountID = &dest
		}
		snapshot.Reservations = append(snapshot.Reservations, r)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	index := make(map[funds.ID]int, len(snapshot.Reservations))
	for i, r := range snapshot.Reservations {
		if _, exists := index[r.ActionID]; exists {
			return funds.IncomeLedger{}, ErrLedgerConflict
		}
		index[r.ActionID] = i
	}
	claimQuery := `SELECT c.action_id::text,c.fragment_id::text,l.origin_id::text,l.account_id::text,c.amount_cents FROM income_claims c JOIN income_locations l ON (l.environment_id=c.environment_id AND l.user_id=c.user_id AND l.id=c.fragment_id) WHERE c.environment_id=$1 AND c.user_id=$2`
	if native {
		claimQuery += ` UNION ALL SELECT c.action_id::text,c.fragment_id::text,l.origin_id::text,l.account_id::text,c.amount_cents FROM native_income_claims c JOIN income_locations l ON (l.environment_id=c.environment_id AND l.user_id=c.user_id AND l.id=c.fragment_id) WHERE c.environment_id=$1 AND c.user_id=$2`
	}
	rows, err = tx.Query(ctx, claimQuery+` ORDER BY 1,2`, env.String(), user.String())
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	for rows.Next() {
		var action, fragment, origin, account string
		var amount int64
		if err = rows.Scan(&action, &fragment, &origin, &account, &amount); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		aid, e := parseID(action)
		if e != nil {
			rows.Close()
			return funds.IncomeLedger{}, e
		}
		i, ok := index[aid]
		if !ok {
			rows.Close()
			return funds.IncomeLedger{}, ErrLedgerConflict
		}
		use := funds.IncomeUse{}
		if use.FragmentID, err = parseID(fragment); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if use.OriginID, err = parseID(origin); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if use.AccountID, err = parseID(account); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		if use.Amount, err = parseMoney(amount); err != nil {
			rows.Close()
			return funds.IncomeLedger{}, err
		}
		snapshot.Reservations[i].Uses = append(snapshot.Reservations[i].Uses, use)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return funds.IncomeLedger{}, err
	}
	return funds.NewIncomeLedger(snapshot)
}

// sameStoredOrigin compares PostgreSQL timestamptz's encoded microsecond
// precision, matching the previous SQL equality checks for supplied parameters.
func sameStoredOrigin(a, b funds.BankCreditFact) bool {
	a.OccurredAt, b.OccurredAt = a.OccurredAt.UTC().Truncate(time.Microsecond), b.OccurredAt.UTC().Truncate(time.Microsecond)
	a.ObservedAt, b.ObservedAt = a.ObservedAt.UTC().Truncate(time.Microsecond), b.ObservedAt.UTC().Truncate(time.Microsecond)
	a.RecordedAt, b.RecordedAt = a.RecordedAt.UTC().Truncate(time.Microsecond), b.RecordedAt.UTC().Truncate(time.Microsecond)
	return a == b
}

func sameStoredCommand(a, b funds.IncomeReservation) bool {
	if a.Operation != b.Operation || (a.DestinationAccountID == nil) != (b.DestinationAccountID == nil) || len(a.Uses) != len(b.Uses) {
		return false
	}
	if a.DestinationAccountID != nil && *a.DestinationAccountID != *b.DestinationAccountID {
		return false
	}
	// Both snapshots originate in NewIncomeLedger, which sorts exact uses.
	for i := range a.Uses {
		if a.Uses[i] != b.Uses[i] {
			return false
		}
	}
	return true
}

// SaveLedger preserves the complete user's ledger without deleting history.
// The same transaction must first insert operations for any new action IDs and
// lock the user's funds version. A caller must roll back on EVERY returned error.
// Every save rereads the entire persisted scope; no cache or SQL no-op is used
// as evidence that an original command, descriptor or complete use set agrees.
func (s *Store) SaveLedger(ctx context.Context, tx pgx.Tx, env, user funds.ID, ledger funds.IncomeLedger) error {
	if tx == nil {
		return ErrLedgerConflict
	}
	if err := ledger.Validate(); err != nil {
		return err
	}
	snap := ledger.Snapshot()
	if snap.UserID != user {
		return ErrLedgerConflict
	}
	native, err := nativeLedgerEnabled(ctx, tx)
	if err != nil {
		return err
	}
	persisted, err := s.LoadLedger(ctx, tx, env, user, snap.AsOf)
	if err != nil {
		return err
	}
	previous := persisted.Snapshot()
	if previous.Currency != snap.Currency || len(previous.ScopeAccountIDs) != len(snap.ScopeAccountIDs) {
		return ErrLedgerConflict
	}
	for i := range previous.ScopeAccountIDs {
		if previous.ScopeAccountIDs[i] != snap.ScopeAccountIDs[i] {
			return ErrLedgerConflict
		}
	}
	origins := make(map[funds.ID]funds.BankCreditFact, len(previous.Origins))
	locations := make(map[funds.ID]funds.IncomeLocation, len(previous.Locations))
	reservations := make(map[funds.ID]funds.IncomeReservation, len(previous.Reservations))
	for _, o := range previous.Origins {
		origins[o.ID()] = o.Fact()
	}
	for _, l := range previous.Locations {
		locations[l.FragmentID] = l
	}
	for _, r := range previous.Reservations {
		reservations[r.ActionID] = r
	}
	newOrigins := []funds.BankCreditFact{}
	changedLocations := []funds.IncomeLocation{}
	changedReservations := []funds.IncomeReservation{}
	newReservations := make(map[funds.ID]bool)
	for _, o := range snap.Origins {
		f := o.Fact()
		if old, exists := origins[o.ID()]; exists {
			if !sameStoredOrigin(old, f) {
				return ErrLedgerConflict
			}
			delete(origins, o.ID())
		} else {
			newOrigins = append(newOrigins, f)
		}
	}
	for _, l := range snap.Locations {
		if old, exists := locations[l.FragmentID]; exists {
			if old.OriginID != l.OriginID || old.AccountID != l.AccountID {
				return ErrLedgerConflict
			}
			if old != l {
				changedLocations = append(changedLocations, l)
			}
			delete(locations, l.FragmentID)
		} else {
			changedLocations = append(changedLocations, l)
		}
	}
	claimCount := 0
	actions := make([]string, 0, len(snap.Reservations))
	for _, r := range snap.Reservations {
		actions = append(actions, r.ActionID.String())
		claimCount += len(r.Uses)
		if old, exists := reservations[r.ActionID]; exists {
			if !sameStoredCommand(old, r) {
				return ErrLedgerConflict
			}
			if old.State != r.State {
				changedReservations = append(changedReservations, r)
			}
			delete(reservations, r.ActionID)
		} else {
			newReservations[r.ActionID] = true
			changedReservations = append(changedReservations, r)
		}
	}
	// A caller cannot omit even terminal history or substitute an equally sized
	// set. Complete identity checks precede all writes, including new origins.
	if len(origins) != 0 || len(locations) != 0 || len(reservations) != 0 {
		return ErrLedgerConflict
	}
	args := []any{env.String(), user.String()}
	owners := make(map[funds.ID]bool, len(actions))
	if len(actions) != 0 {
		query := `SELECT id::text,false FROM operations WHERE environment_id=$1 AND user_id=$2 AND id=ANY($3::uuid[])`
		if native {
			query += ` UNION ALL SELECT id::text,true FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=ANY($3::uuid[])`
		}
		rows, err := tx.Query(ctx, query, env.String(), user.String(), actions)
		if err != nil {
			return err
		}
		for rows.Next() {
			var value string
			var isNative bool
			if err = rows.Scan(&value, &isNative); err != nil {
				rows.Close()
				return err
			}
			id, err := parseID(value)
			if err != nil {
				rows.Close()
				return err
			}
			if _, exists := owners[id]; exists {
				rows.Close()
				return ErrLedgerConflict
			}
			owners[id] = isNative
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return err
		}
		if len(owners) != len(actions) {
			return ErrLedgerConflict
		}
	}
	for _, f := range newOrigins {
		if err := nativeExecOne(ctx, tx, `INSERT INTO income_origins(environment_id,user_id,id,original_account_id,currency,amount_cents,occurred_at,observed_at,recorded_at,evidence_id,evidence_hash) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`, env.String(), user.String(), f.TransactionID.String(), f.AccountID.String(), string(f.Currency), f.Amount.Cents(), f.OccurredAt, f.ObservedAt, f.RecordedAt, f.EvidenceID.String(), f.EvidenceHash.String()); err != nil {
			return err
		}
	}
	for _, l := range changedLocations {
		if err := nativeExecOne(ctx, tx, `INSERT INTO income_locations(environment_id,user_id,id,origin_id,account_id,spent_cents,assigned_cents,reserved_cents,legacy_reserved_cents,available_cents) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT(environment_id,user_id,id) DO UPDATE SET spent_cents=EXCLUDED.spent_cents,assigned_cents=EXCLUDED.assigned_cents,reserved_cents=EXCLUDED.reserved_cents,legacy_reserved_cents=EXCLUDED.legacy_reserved_cents,available_cents=EXCLUDED.available_cents WHERE income_locations.origin_id=EXCLUDED.origin_id AND income_locations.account_id=EXCLUDED.account_id`, env.String(), user.String(), l.FragmentID.String(), l.OriginID.String(), l.AccountID.String(), l.Spent.Cents(), l.Assigned.Cents(), l.Reserved.Cents(), l.LegacyReserved.Cents(), l.Available.Cents()); err != nil {
			return err
		}
	}
	for _, r := range changedReservations {
		reservationTable, claimTable := "income_reservations", "income_claims"
		if owners[r.ActionID] {
			reservationTable, claimTable = "native_income_reservations", "native_income_claims"
		}
		var dest any
		if r.DestinationAccountID != nil {
			dest = r.DestinationAccountID.String()
		}
		if err := nativeExecOne(ctx, tx, `INSERT INTO `+reservationTable+`(environment_id,user_id,action_id,operation,destination_account_id,state) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(environment_id,user_id,action_id) DO UPDATE SET state=EXCLUDED.state WHERE `+reservationTable+`.operation=EXCLUDED.operation AND `+reservationTable+`.destination_account_id IS NOT DISTINCT FROM EXCLUDED.destination_account_id`, env.String(), user.String(), r.ActionID.String(), string(r.Operation), dest, string(r.State)); err != nil {
			return err
		}
		if newReservations[r.ActionID] {
			for _, use := range r.Uses {
				if err := nativeExecOne(ctx, tx, `INSERT INTO `+claimTable+`(environment_id,user_id,action_id,fragment_id,amount_cents) VALUES($1,$2,$3,$4,$5)`, env.String(), user.String(), r.ActionID.String(), use.FragmentID.String(), use.Amount.Cents()); err != nil {
					return err
				}
			}
		}
	}
	var originCount, locationCount, reservationCount, claims int
	countQuery := `SELECT (SELECT count(*) FROM income_origins WHERE environment_id=$1 AND user_id=$2),(SELECT count(*) FROM income_locations WHERE environment_id=$1 AND user_id=$2),(SELECT count(*) FROM income_reservations WHERE environment_id=$1 AND user_id=$2),(SELECT count(*) FROM income_claims WHERE environment_id=$1 AND user_id=$2)`
	if native {
		countQuery = `SELECT (SELECT count(*) FROM income_origins WHERE environment_id=$1 AND user_id=$2),(SELECT count(*) FROM income_locations WHERE environment_id=$1 AND user_id=$2),(SELECT count(*) FROM income_reservations WHERE environment_id=$1 AND user_id=$2)+(SELECT count(*) FROM native_income_reservations WHERE environment_id=$1 AND user_id=$2),(SELECT count(*) FROM income_claims WHERE environment_id=$1 AND user_id=$2)+(SELECT count(*) FROM native_income_claims WHERE environment_id=$1 AND user_id=$2)`
	}
	if err = tx.QueryRow(ctx, countQuery, args...).Scan(&originCount, &locationCount, &reservationCount, &claims); err != nil {
		return err
	}
	if originCount != len(snap.Origins) || locationCount != len(snap.Locations) || reservationCount != len(snap.Reservations) || claims != claimCount {
		return ErrLedgerConflict
	}
	if _, err = s.LoadLedger(ctx, tx, env, user, snap.AsOf); err != nil {
		return fmt.Errorf("saved ledger validation: %w", err)
	}
	return nil
}
