// Package bankcredit defines externally authorized simulated-bank credits.
// Its authentication domain is independent from an application bank permit.
package bankcredit

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

const Protocol = "zhiyu-bank-credit-v1"
const MaxEventBytes = 16 << 10
const MaxPage = 100

var ErrInvalid = errors.New("invalid external bank credit")
var idPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)
var nsPattern = regexp.MustCompile(`^zygo_[a-z][a-z0-9_]{0,63}$`)

func Hash(raw []byte) string {
	h := sha256.Sum256(raw)
	return hex.EncodeToString(h[:])
}
func hashValid(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, r := range value {
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'f') {
			return false
		}
	}
	return true
}
func validTime(t time.Time) bool { return !t.IsZero() && t.Equal(t.Truncate(time.Microsecond)) }
func validUUID(value string) bool {
	id, err := funds.ParseID(value)
	return err == nil && id != (funds.ID{})
}

// Event is an immutable external fact, never an LLM or USER permission.
// OccurredAt remains the original event time even if ingestion is delayed.
type Event struct {
	Protocol              string      `json:"protocol"`
	Namespace             string      `json:"namespace"`
	AuthorityID           string      `json:"authority_id"`
	EventID               string      `json:"event_id"`
	UserID                string      `json:"user_id"`
	AccountID             string      `json:"account_id"`
	OriginID              string      `json:"origin_id"`
	EvidenceID            string      `json:"evidence_id"`
	Role                  string      `json:"role"`
	Currency              string      `json:"currency"`
	Amount                funds.Money `json:"amount_cents"`
	OccurredAt            time.Time   `json:"occurred_at"`
	IssuedAt              time.Time   `json:"issued_at"`
	ExpiresAt             time.Time   `json:"expires_at"`
	ExternalReferenceHash string      `json:"external_reference_hash"`
}

func (e Event) Validate() error {
	if e.Protocol != Protocol || !nsPattern.MatchString(e.Namespace) || !idPattern.MatchString(e.AuthorityID) || !idPattern.MatchString(e.EventID) || !validUUID(e.UserID) || !validUUID(e.AccountID) || !validUUID(e.OriginID) || !validUUID(e.EvidenceID) || e.Role != "INCOME" || e.Currency != "CNY" || e.Amount.IsZero() || !hashValid(e.ExternalReferenceHash) || !validTime(e.OccurredAt) || !validTime(e.IssuedAt) || !validTime(e.ExpiresAt) || e.OccurredAt.After(e.IssuedAt) || !e.ExpiresAt.After(e.IssuedAt) || e.ExpiresAt.Sub(e.IssuedAt) > 24*time.Hour {
		return ErrInvalid
	}
	return nil
}
func (e Event) CanonicalBytes() ([]byte, error) {
	if e.Validate() != nil {
		return nil, ErrInvalid
	}
	e.OccurredAt, e.IssuedAt, e.ExpiresAt = e.OccurredAt.UTC(), e.IssuedAt.UTC(), e.ExpiresAt.UTC()
	raw, err := json.Marshal(e)
	if err != nil || len(raw) > MaxEventBytes {
		return nil, ErrInvalid
	}
	return raw, nil
}
func (e Event) Digest() (string, error) {
	raw, err := e.CanonicalBytes()
	if err != nil {
		return "", err
	}
	return Hash(raw), nil
}
func SignExternal(e Event, secret []byte) (string, error) {
	if len(secret) < 32 {
		return "", ErrInvalid
	}
	raw, err := e.CanonicalBytes()
	if err != nil {
		return "", err
	}
	mac := hmac.New(sha256.New, secret)
	_, _ = mac.Write([]byte(Protocol + "/external-authority\x00"))
	_, _ = mac.Write(raw)
	return hex.EncodeToString(mac.Sum(nil)), nil
}
func VerifyExternal(e Event, signature string, secret []byte) bool {
	want, err := SignExternal(e, secret)
	return err == nil && hashValid(signature) && hmac.Equal([]byte(want), []byte(signature))
}

type Posting struct {
	ID         string      `json:"id"`
	Dimension  string      `json:"dimension"`
	LedgerID   string      `json:"ledger_id"`
	Direction  string      `json:"direction"`
	Amount     funds.Money `json:"amount_cents"`
	OccurredAt time.Time   `json:"occurred_at"`
}
type Record struct {
	Protocol               string      `json:"protocol"`
	Sequence               int64       `json:"sequence"`
	Event                  Event       `json:"event"`
	EventHash              string      `json:"event_hash"`
	AuthoritySignatureHash string      `json:"authority_signature_hash"`
	CreditedAt             time.Time   `json:"credited_at"`
	BalanceBefore          funds.Money `json:"balance_before_cents"`
	BalanceAfter           funds.Money `json:"balance_after_cents"`
	Postings               []Posting   `json:"postings"`
}
type Entry struct {
	Record     Record `json:"record"`
	RecordHash string `json:"record_hash"`
}

func postingSet(e Event, hash string, sequence int64) []Posting {
	dimensions := []string{"ECONOMIC", "ECONOMIC", "INCOME_QUALIFICATION", "INCOME_QUALIFICATION"}
	ledgers := []string{"external:" + e.AuthorityID, "cash:" + e.AccountID, "external-income:" + e.AuthorityID, "income:" + e.OriginID + ":" + e.AccountID}
	out := make([]Posting, 4)
	for i := range out {
		direction := "DEBIT"
		if i%2 == 1 {
			direction = "CREDIT"
		}
		out[i] = Posting{Hash([]byte(fmt.Sprintf("%s/posting\x00%s\x00%d\x00%d", Protocol, hash, sequence, i))), dimensions[i], ledgers[i], direction, e.Amount, e.OccurredAt.UTC()}
	}
	return out
}
func NewRecord(e Event, signatureHash string, sequence int64, creditedAt time.Time, before funds.Money) (Record, error) {
	h, err := e.Digest()
	if err != nil || !hashValid(signatureHash) || sequence < 1 || !validTime(creditedAt) || creditedAt.Before(e.IssuedAt) || !creditedAt.Before(e.ExpiresAt) {
		return Record{}, ErrInvalid
	}
	after, err := before.Add(e.Amount)
	if err != nil {
		return Record{}, err
	}
	return Record{Protocol + "/record", sequence, e, h, signatureHash, creditedAt.UTC(), before, after, postingSet(e, h, sequence)}, nil
}
func (r Record) Validate() error {
	want, err := NewRecord(r.Event, r.AuthoritySignatureHash, r.Sequence, r.CreditedAt, r.BalanceBefore)
	if err != nil || r.Protocol != want.Protocol || r.EventHash != want.EventHash || r.BalanceAfter != want.BalanceAfter || len(r.Postings) != len(want.Postings) {
		return ErrInvalid
	}
	for i, p := range r.Postings {
		w := want.Postings[i]
		if p.ID != w.ID || p.Dimension != w.Dimension || p.LedgerID != w.LedgerID || p.Direction != w.Direction || p.Amount != w.Amount || !p.OccurredAt.Equal(w.OccurredAt) {
			return ErrInvalid
		}
	}
	return nil
}
func (r Record) CanonicalBytes() ([]byte, error) {
	if r.Validate() != nil {
		return nil, ErrInvalid
	}
	eventRaw, _ := r.Event.CanonicalBytes()
	if json.Unmarshal(eventRaw, &r.Event) != nil {
		return nil, ErrInvalid
	}
	r.CreditedAt = r.CreditedAt.UTC()
	r.Postings = append([]Posting(nil), r.Postings...)
	for i := range r.Postings {
		r.Postings[i].OccurredAt = r.Postings[i].OccurredAt.UTC()
	}
	return json.Marshal(r)
}
func (r Record) Digest() (string, error) {
	raw, err := r.CanonicalBytes()
	if err != nil {
		return "", err
	}
	return Hash(raw), nil
}
func (e Entry) Validate() error {
	h, err := e.Record.Digest()
	if err != nil || e.RecordHash != h {
		return ErrInvalid
	}
	return nil
}

type Lookup struct {
	Protocol    string `json:"protocol"`
	Namespace   string `json:"namespace"`
	UserID      string `json:"user_id"`
	AuthorityID string `json:"authority_id"`
	EventID     string `json:"event_id"`
	Found       bool   `json:"found"`
	Entry       *Entry `json:"entry,omitempty"`
}
type Inventory struct {
	Protocol  string  `json:"protocol"`
	Namespace string  `json:"namespace"`
	UserID    string  `json:"user_id"`
	Watermark int64   `json:"watermark"`
	NextAfter int64   `json:"next_after"`
	HasMore   bool    `json:"has_more"`
	Credits   []Entry `json:"credits"`
}

func (i Inventory) Validate(after int64, watermark *int64, limit int) error {
	if i.Protocol != Protocol+"/inventory" || !nsPattern.MatchString(i.Namespace) || !validUUID(i.UserID) || after < 0 || i.Watermark < after || limit < 1 || limit > MaxPage || len(i.Credits) > limit || i.NextAfter < after || i.NextAfter > i.Watermark || watermark != nil && i.Watermark != *watermark {
		return ErrInvalid
	}
	last := after
	for _, e := range i.Credits {
		if e.Validate() != nil || e.Record.Event.Namespace != i.Namespace || e.Record.Event.UserID != i.UserID || e.Record.Sequence <= last || e.Record.Sequence > i.Watermark {
			return ErrInvalid
		}
		last = e.Record.Sequence
	}
	if last != i.NextAfter || i.HasMore && len(i.Credits) != limit {
		return ErrInvalid
	}
	return nil
}

// Scope validates read identities without pretending that a client is the
// authority for a financial fact. Authentication is held by the bank adapter.
func Scope(namespace, user string) bool { return nsPattern.MatchString(namespace) && validUUID(user) }
func Identity(value string) bool        { return idPattern.MatchString(value) }
