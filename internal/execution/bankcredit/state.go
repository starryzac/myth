package bankcredit

import (
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

const MaxAccounts = 16
const MaxRecords = 1000
const MaxOrigins = 1000
const MaxLocations = 4000

type Account struct {
	ID       string      `json:"id"`
	Balance  funds.Money `json:"balance_cents"`
	Reserved funds.Money `json:"reserved_cents"`
}
type Location struct {
	AccountID string      `json:"account_id"`
	Available funds.Money `json:"available_cents"`
	Assigned  funds.Money `json:"assigned_cents"`
	Reserved  funds.Money `json:"reserved_cents"`
}
type Origin struct {
	ID                string      `json:"id"`
	OriginalAccountID string      `json:"original_account_id"`
	Amount            funds.Money `json:"amount_cents"`
	OccurredAt        time.Time   `json:"occurred_at"`
	EvidenceHash      string      `json:"evidence_hash"`
	Locations         []Location  `json:"locations"`
}

// CurrentState is one RR observation of both cash and qualification inventory.
// CreditWatermark does not reconstruct historical balances. Credits and source
// origins are bound by the original RecordHash, including after consumption or
// internal transfer. Existing OpenIncome anchors remain visibly older sources.
type CurrentState struct {
	Protocol             string    `json:"protocol"`
	Namespace            string    `json:"namespace"`
	UserID               string    `json:"user_id"`
	CapturedAt           time.Time `json:"captured_at"`
	CreditWatermark      int64     `json:"credit_watermark"`
	V1OperationWatermark int64     `json:"v1_operation_watermark"`
	V2OperationWatermark int64     `json:"v2_operation_watermark"`
	Accounts             []Account `json:"accounts"`
	Credits              []Entry   `json:"credits"`
	Origins              []Origin  `json:"origins"`
}

func (s CurrentState) Validate() error {
	if s.Protocol != Protocol+"/state" || !Scope(s.Namespace, s.UserID) || !validTime(s.CapturedAt) || s.CreditWatermark < 0 || s.V1OperationWatermark < 0 || s.V2OperationWatermark < 0 || len(s.Accounts) > MaxAccounts || len(s.Credits) > MaxRecords || len(s.Origins) > MaxOrigins {
		return ErrInvalid
	}
	accounts := map[string]bool{}
	for _, a := range s.Accounts {
		if !validUUID(a.ID) || a.Reserved.Cents() > a.Balance.Cents() || accounts[a.ID] {
			return ErrInvalid
		}
		accounts[a.ID] = true
	}
	origins := map[string]Origin{}
	locations := 0
	for _, o := range s.Origins {
		if !validUUID(o.ID) || !accounts[o.OriginalAccountID] || o.Amount.IsZero() || !validTime(o.OccurredAt) || o.OccurredAt.After(s.CapturedAt) || !hashValid(o.EvidenceHash) {
			return ErrInvalid
		}
		if _, exists := origins[o.ID]; exists {
			return ErrInvalid
		}
		origins[o.ID] = o
		seen := map[string]bool{}
		var total funds.Money
		for _, l := range o.Locations {
			locations++
			if !accounts[l.AccountID] || l.Reserved.Cents() > l.Available.Cents() || seen[l.AccountID] {
				return ErrInvalid
			}
			seen[l.AccountID] = true
			var err error
			total, err = funds.SumMoney(total, l.Available, l.Assigned)
			if err != nil || total.Cents() > o.Amount.Cents() {
				return ErrInvalid
			}
		}
	}
	if locations > MaxLocations {
		return ErrInvalid
	}
	var last int64
	credited := map[string]bool{}
	for _, e := range s.Credits {
		v := e.Record.Event
		o, exists := origins[v.OriginID]
		if e.Validate() != nil || v.Namespace != s.Namespace || v.UserID != s.UserID || e.Record.Sequence <= last || e.Record.CreditedAt.After(s.CapturedAt) || credited[v.OriginID] || !exists || o.OriginalAccountID != v.AccountID || o.Amount != v.Amount || !o.OccurredAt.Equal(v.OccurredAt) || o.EvidenceHash != e.RecordHash {
			return ErrInvalid
		}
		credited[v.OriginID] = true
		last = e.Record.Sequence
	}
	if last != s.CreditWatermark {
		return ErrInvalid
	}
	return nil
}
