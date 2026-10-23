package banksim

import (
	"bytes"
	"context"
	"crypto/hmac"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/execution/bankcredit"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

var ErrCreditUnauthorized = errors.New("INVALID_EXTERNAL_CREDIT_AUTHORITY")
var ErrCreditCapacity = errors.New("EXTERNAL_CREDIT_CAPACITY_EXCEEDED")

// CreditAuthority is a separately provisioned external-event capability. No
// application permit or USER session is an external income source.
type CreditAuthority struct {
	ID        string `json:"id"`
	Namespace string `json:"namespace"`
	UserID    string `json:"user_id"`
	AccountID string `json:"account_id"`
	Secret    []byte `json:"-"`
}
type CreditGateway struct {
	store       *Store
	authorities map[string]CreditAuthority
}

func NewCreditGateway(store *Store, authorities []CreditAuthority) (*CreditGateway, error) {
	if store == nil || len(authorities) < 1 || len(authorities) > 1000 {
		return nil, ErrCreditUnauthorized
	}
	g := &CreditGateway{store: store, authorities: map[string]CreditAuthority{}}
	for _, a := range authorities {
		account, err := funds.ParseID(a.AccountID)
		if !bankcredit.Identity(a.ID) || !bankcredit.Scope(a.Namespace, a.UserID) || err != nil || account == (funds.ID{}) || len(a.Secret) < 32 || hmac.Equal(a.Secret, store.secret) {
			return nil, ErrCreditUnauthorized
		}
		if _, exists := g.authorities[a.ID]; exists {
			return nil, ErrCreditUnauthorized
		}
		a.Secret = append([]byte(nil), a.Secret...)
		g.authorities[a.ID] = a
	}
	return g, nil
}

func decodeCredit(raw []byte, hash string) (bankcredit.Entry, error) {
	var record bankcredit.Record
	if bankcredit.Hash(raw) != hash || json.Unmarshal(raw, &record) != nil {
		return bankcredit.Entry{}, bankcredit.ErrInvalid
	}
	canonical, err := record.CanonicalBytes()
	if err != nil || !bytes.Equal(raw, canonical) {
		return bankcredit.Entry{}, bankcredit.ErrInvalid
	}
	return bankcredit.Entry{Record: record, RecordHash: hash}, nil
}

func loadCredit(ctx context.Context, q querier, namespace, user, authority, event string) (bankcredit.Lookup, error) {
	out := bankcredit.Lookup{Protocol: bankcredit.Protocol + "/lookup", Namespace: namespace, UserID: user, AuthorityID: authority, EventID: event}
	var raw []byte
	var hash string
	err := q.QueryRow(ctx, `SELECT record_original,record_hash FROM bank.external_credits WHERE namespace=$1 AND user_id=$2 AND authority_id=$3 AND event_id=$4`, namespace, user, authority, event).Scan(&raw, &hash)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, nil
	}
	if err != nil {
		return out, err
	}
	entry, err := decodeCredit(raw, hash)
	if err != nil {
		return out, err
	}
	e := entry.Record.Event
	if e.Namespace != namespace || e.UserID != user || e.AuthorityID != authority || e.EventID != event {
		return out, bankcredit.ErrInvalid
	}
	out.Found, out.Entry = true, &entry
	return out, nil
}

// Submit commits actual independent cash and qualification atomically. Its
// write capability is deliberately unavailable on Store or the read client.
func (g *CreditGateway) Submit(ctx context.Context, event bankcredit.Event, signature string, now time.Time) (bankcredit.Entry, error) {
	var zero bankcredit.Entry
	if event.Validate() != nil {
		return zero, bankcredit.ErrInvalid
	}
	a, exists := g.authorities[event.AuthorityID]
	if !exists || a.Namespace != event.Namespace || a.UserID != event.UserID || a.AccountID != event.AccountID || !bankcredit.VerifyExternal(event, signature, a.Secret) {
		return zero, ErrCreditUnauthorized
	}
	tx, err := g.store.pool.Begin(ctx)
	if err != nil {
		return zero, err
	}
	defer tx.Rollback(ctx)
	if err = keyLock(ctx, tx, event.Namespace+"/external-credit/"+event.AuthorityID, event.EventID); err != nil {
		return zero, err
	}
	if err = userLock(ctx, tx, event.Namespace, event.UserID); err != nil {
		return zero, err
	}
	hash, _ := event.Digest()
	old, err := loadCredit(ctx, tx, event.Namespace, event.UserID, event.AuthorityID, event.EventID)
	if err != nil {
		return zero, err
	}
	if old.Found {
		if old.Entry.Record.EventHash != hash {
			return zero, ErrConflict
		}
		return *old.Entry, tx.Commit(ctx)
	}
	now = now.UTC().Truncate(time.Microsecond)
	if now.Before(event.IssuedAt) || !now.Before(event.ExpiresAt) {
		return zero, bankcredit.ErrInvalid
	}
	var conflict bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM bank.external_credits WHERE namespace=$1 AND (authority_id=$2 AND event_id=$3 OR user_id=$4 AND evidence_id=$5)) OR EXISTS(SELECT 1 FROM bank.origins WHERE namespace=$1 AND user_id=$4 AND origin_id=$6)`, event.Namespace, event.AuthorityID, event.EventID, event.UserID, event.EvidenceID, event.OriginID).Scan(&conflict); err != nil {
		return zero, err
	}
	if conflict {
		return zero, ErrConflict
	}
	var balance int64
	if err = tx.QueryRow(ctx, `SELECT balance_cents FROM bank.accounts WHERE namespace=$1 AND user_id=$2 AND account_id=$3 FOR UPDATE`, event.Namespace, event.UserID, event.AccountID).Scan(&balance); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return zero, ErrNotFound
		}
		return zero, err
	}
	var capacity bool
	if err = tx.QueryRow(ctx, `SELECT $4::numeric+$5::numeric+coalesce((SELECT sum(amount_cents::numeric) FROM bank.v2_resource_holds WHERE namespace=$1 AND user_id=$2 AND resource_kind='INCOMING_CASH' AND resource_id=$3 AND state='RESERVED'),0)+coalesce((SELECT sum((request->>'amount_cents')::numeric) FROM bank.operations WHERE namespace=$1 AND user_id=$2 AND status='ACCEPTED' AND request->>'kind'='TRANSFER' AND request->>'destination_account_id'=$3),0)<=9000000000000000`, event.Namespace, event.UserID, event.AccountID, balance, event.Amount.Cents()).Scan(&capacity); err != nil {
		return zero, err
	}
	if !capacity {
		return zero, ErrCreditCapacity
	}
	before, err := funds.NewMoney(balance)
	if err != nil {
		return zero, err
	}
	var sequence int64
	if err = tx.QueryRow(ctx, `SELECT nextval('bank.external_credit_sequence')`).Scan(&sequence); err != nil {
		return zero, err
	}
	record, err := bankcredit.NewRecord(event, bankcredit.Hash([]byte(signature)), sequence, now, before)
	if err != nil {
		return zero, err
	}
	recordRaw, err := record.CanonicalBytes()
	if err != nil {
		return zero, err
	}
	eventRaw, _ := event.CanonicalBytes()
	recordHash := bankcredit.Hash(recordRaw)
	if _, err = tx.Exec(ctx, `INSERT INTO bank.origins(namespace,user_id,origin_id,original_account_id,original_cents,occurred_at,evidence_hash) VALUES($1,$2,$3,$4,$5,$6,$7)`, event.Namespace, event.UserID, event.OriginID, event.AccountID, event.Amount.Cents(), event.OccurredAt, recordHash); err != nil {
		return zero, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO bank.income_locations(namespace,user_id,origin_id,account_id,available_cents) VALUES($1,$2,$3,$4,$5)`, event.Namespace, event.UserID, event.OriginID, event.AccountID, event.Amount.Cents()); err != nil {
		return zero, err
	}
	if _, err = tx.Exec(ctx, `UPDATE bank.accounts SET balance_cents=$4 WHERE namespace=$1 AND user_id=$2 AND account_id=$3`, event.Namespace, event.UserID, event.AccountID, record.BalanceAfter.Cents()); err != nil {
		return zero, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO bank.external_credits(sequence,namespace,user_id,authority_id,event_id,account_id,origin_id,evidence_id,amount_cents,occurred_at,credited_at,balance_before_cents,balance_after_cents,event_original,event_hash,authority_signature_hash,record_original,record_hash) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)`, sequence, event.Namespace, event.UserID, event.AuthorityID, event.EventID, event.AccountID, event.OriginID, event.EvidenceID, event.Amount.Cents(), event.OccurredAt, now, balance, record.BalanceAfter.Cents(), eventRaw, hash, record.AuthoritySignatureHash, recordRaw, recordHash); err != nil {
		return zero, err
	}
	for i, p := range record.Postings {
		if _, err = tx.Exec(ctx, `INSERT INTO bank.external_credit_postings(id,namespace,authority_id,event_id,leg_number,dimension,ledger_id,direction,amount_cents,occurred_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, p.ID, event.Namespace, event.AuthorityID, event.EventID, i+1, p.Dimension, p.LedgerID, p.Direction, p.Amount.Cents(), p.OccurredAt); err != nil {
			return zero, err
		}
	}
	if err = tx.Commit(ctx); err != nil {
		return zero, err
	}
	return bankcredit.Entry{Record: record, RecordHash: recordHash}, nil
}

func (s *Store) LookupExternalCredit(ctx context.Context, namespace, user, authority, event string) (bankcredit.Lookup, error) {
	if !bankcredit.Scope(namespace, user) || !bankcredit.Identity(authority) || !bankcredit.Identity(event) {
		return bankcredit.Lookup{}, bankcredit.ErrInvalid
	}
	return loadCredit(ctx, s.pool, namespace, user, authority, event)
}

func (s *Store) ExternalCreditInventory(ctx context.Context, namespace, user string, after int64, watermark *int64, limit int) (bankcredit.Inventory, error) {
	out := bankcredit.Inventory{Protocol: bankcredit.Protocol + "/inventory", Namespace: namespace, UserID: user, NextAfter: after, Credits: []bankcredit.Entry{}}
	if !bankcredit.Scope(namespace, user) || after < 0 || limit < 1 || limit > bankcredit.MaxPage || watermark != nil && (*watermark < 0 || *watermark < after) {
		return out, bankcredit.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	if err = userLock(ctx, tx, namespace, user); err != nil {
		return out, err
	}
	var current int64
	if err = tx.QueryRow(ctx, `SELECT coalesce(max(sequence),0) FROM bank.external_credits WHERE namespace=$1 AND user_id=$2`, namespace, user).Scan(&current); err != nil {
		return out, err
	}
	out.Watermark = current
	if watermark != nil {
		out.Watermark = *watermark
	}
	if out.Watermark > current || after > out.Watermark {
		return out, bankcredit.ErrInvalid
	}
	rows, err := tx.Query(ctx, `SELECT record_original,record_hash FROM bank.external_credits WHERE namespace=$1 AND user_id=$2 AND sequence>$3 AND sequence<=$4 ORDER BY sequence LIMIT $5`, namespace, user, after, out.Watermark, limit+1)
	if err != nil {
		return out, err
	}
	for rows.Next() {
		var raw []byte
		var hash string
		if err = rows.Scan(&raw, &hash); err != nil {
			break
		}
		var e bankcredit.Entry
		e, err = decodeCredit(raw, hash)
		if err != nil {
			break
		}
		out.Credits = append(out.Credits, e)
	}
	rowErr := rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	if rowErr != nil {
		return out, rowErr
	}
	if len(out.Credits) > limit {
		out.HasMore = true
		out.Credits = out.Credits[:limit]
	}
	if len(out.Credits) > 0 {
		out.NextAfter = out.Credits[len(out.Credits)-1].Record.Sequence
	}
	if err = out.Validate(after, watermark, limit); err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}
