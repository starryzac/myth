// Package domain defines the immutable simulated-bank request and observation
// contracts. It has no database, network or environment dependencies.
package domain

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"regexp"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

const BankProtocol = "zhiyu-bank-v1"

type BankStatus string

const (
	BankNotFound BankStatus = "NOT_FOUND"
	BankAccepted BankStatus = "ACCEPTED"
	BankSettled  BankStatus = "SETTLED"
	BankRejected BankStatus = "REJECTED"
)

// BankRequest binds the original bank namespace/key to the complete economics
// and the T2 permit. Amounts are integer cents encoded as decimal strings.
// No client-supplied success, posting or balance is accepted by this contract.
type BankRequest struct {
	ProtocolVersion      string      `json:"protocol_version"`
	Namespace            string      `json:"namespace"`
	Key                  string      `json:"key"`
	OperationID          string      `json:"operation_id"`
	UserID               string      `json:"user_id"`
	Kind                 string      `json:"kind"`
	Amount               funds.Money `json:"amount_cents"`
	SourceAccountID      string      `json:"source_account_id"`
	DestinationAccountID string      `json:"destination_account_id"`
	IncomeOriginID       string      `json:"income_origin_id"`
	GoalID               string      `json:"goal_id"`
	ReferenceHash        string      `json:"reference_hash"`
	AuthorizationID      string      `json:"authorization_id"`
	AuthorizationVersion int64       `json:"authorization_version"`
	PermitID             string      `json:"permit_id"`
	RequestedAt          time.Time   `json:"requested_at"`
	SettlementDelayDays  int         `json:"settlement_delay_days"`
	LatestArrivalAt      time.Time   `json:"latest_arrival_at"`
}

var bankIdentity = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)
var bankNamespace = regexp.MustCompile(`^zygo_[a-z][a-z0-9_]{0,63}$`)

func (r BankRequest) Validate() error {
	if r.ProtocolVersion != BankProtocol || !bankNamespace.MatchString(r.Namespace) {
		return errors.New("invalid bank protocol or isolated namespace")
	}
	for _, value := range []string{r.Key, r.OperationID, r.UserID, r.SourceAccountID, r.DestinationAccountID, r.IncomeOriginID, r.AuthorizationID, r.PermitID} {
		if !bankIdentity.MatchString(value) {
			return errors.New("invalid bank request identity")
		}
	}
	if (r.Kind != "TRANSFER" && r.Kind != "ALLOCATE_GOAL") || r.Amount.IsZero() || r.AuthorizationVersion < 1 {
		return errors.New("unsupported or invalid bank economics")
	}
	if r.Kind == "TRANSFER" && (r.SourceAccountID == r.DestinationAccountID || r.GoalID != "") {
		return errors.New("invalid transfer accounts or goal")
	}
	if r.Kind == "ALLOCATE_GOAL" && (r.SourceAccountID != r.DestinationAccountID || !bankIdentity.MatchString(r.GoalID)) {
		return errors.New("goal allocation requires one account and a goal")
	}
	if raw, err := hex.DecodeString(r.ReferenceHash); err != nil || len(raw) != 32 {
		return errors.New("bank request requires original application evidence digest")
	}
	if r.RequestedAt.IsZero() || r.LatestArrivalAt.Before(r.RequestedAt) || r.SettlementDelayDays < 0 || r.SettlementDelayDays > 1 {
		return errors.New("invalid settlement agreement")
	}
	return nil
}

// CanonicalBytes is a versioned fixed-field encoding, independent of transport
// whitespace and UTC offset spelling. This is a new protocol, not legacy JSON.
func (r BankRequest) CanonicalBytes() ([]byte, error) {
	if err := r.Validate(); err != nil {
		return nil, err
	}
	r.RequestedAt = r.RequestedAt.UTC()
	r.LatestArrivalAt = r.LatestArrivalAt.UTC()
	return json.Marshal(r)
}

func (r BankRequest) Digest() (string, error) {
	raw, err := r.CanonicalBytes()
	if err != nil {
		return "", err
	}
	h := sha256.Sum256(append([]byte(BankProtocol+"\x00"), raw...))
	return hex.EncodeToString(h[:]), nil
}

// SignPermit authenticates a trusted application's committed T2 request. A
// signature does not substitute for the bank's independent funds checks.
func SignPermit(r BankRequest, secret []byte) (string, error) {
	if len(secret) < 32 {
		return "", errors.New("bank permit secret must contain at least 32 bytes")
	}
	raw, err := r.CanonicalBytes()
	if err != nil {
		return "", err
	}
	h := hmac.New(sha256.New, secret)
	h.Write([]byte("zhiyu-bank-permit-v1\x00"))
	h.Write(raw)
	return hex.EncodeToString(h.Sum(nil)), nil
}

func VerifyPermit(r BankRequest, signature string, secret []byte) bool {
	want, err := SignPermit(r, secret)
	if err != nil {
		return false
	}
	a, err := hex.DecodeString(signature)
	if err != nil {
		return false
	}
	b, _ := hex.DecodeString(want)
	return hmac.Equal(a, b)
}

type BankPosting struct {
	ID         string      `json:"id"`
	Dimension  string      `json:"dimension"`
	LedgerID   string      `json:"ledger_id"`
	Direction  string      `json:"direction"`
	Amount     funds.Money `json:"amount_cents"`
	OccurredAt time.Time   `json:"occurred_at"`
}

type BankResult struct {
	Status         BankStatus    `json:"status"`
	Namespace      string        `json:"namespace"`
	Key            string        `json:"key"`
	UserID         string        `json:"user_id"`
	Request        BankRequest   `json:"request"`
	RequestHash    string        `json:"request_hash"`
	Sequence       int64         `json:"sequence"`
	Version        int64         `json:"version"`
	AcceptedAt     *time.Time    `json:"accepted_at"`
	AvailableAt    *time.Time    `json:"available_at"`
	SettledAt      *time.Time    `json:"settled_at"`
	RejectionCode  string        `json:"rejection_code,omitempty"`
	AbsenceIsFinal bool          `json:"absence_is_final"`
	Postings       []BankPosting `json:"postings"`
}

type BankInventory struct {
	Namespace  string       `json:"namespace"`
	UserID     string       `json:"user_id"`
	Watermark  int64        `json:"watermark"`
	NextAfter  int64        `json:"next_after"`
	HasMore    bool         `json:"has_more"`
	Operations []BankResult `json:"operations"`
}
