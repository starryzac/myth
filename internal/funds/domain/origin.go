package domain

import (
	"errors"
	"time"
)

type CreditRole string
type FactSource string

const (
	RoleIncome           CreditRole = "INCOME"
	RoleInternalTransfer CreditRole = "INTERNAL_TRANSFER"
	RoleRefund           CreditRole = "REFUND"
	RolePrincipalReturn  CreditRole = "PRINCIPAL_RETURN"
	RoleYield            CreditRole = "YIELD"
	BankConfirmed        FactSource = "BANK_CONFIRMED"
	Forecast             FactSource = "FORECAST"
)

// BankCreditFact is an adapter-supplied credit fact. Its original evidence and
// ownership must be verified by the caller; this type is not authorization.
type BankCreditFact struct {
	TransactionID IncomeOriginID
	AccountID     ID
	Currency      Currency
	Amount        Money
	Role          CreditRole
	Source        FactSource
	OccurredAt    time.Time
	ObservedAt    time.Time
	RecordedAt    time.Time
	EvidenceID    ID
	EvidenceHash  EvidenceHash
}

// IncomeOrigin is immutable after construction; transfers retain this value.
type IncomeOrigin struct{ fact BankCreditFact }

func NewIncomeOrigin(fact BankCreditFact) (IncomeOrigin, error) {
	if err := fact.Currency.Validate(); err != nil {
		return IncomeOrigin{}, err
	}
	if fact.Amount.IsZero() || fact.Source != BankConfirmed || fact.Role != RoleIncome {
		return IncomeOrigin{}, errors.New("only a positive confirmed original income credit can create an origin")
	}
	if fact.OccurredAt.IsZero() || fact.ObservedAt.IsZero() || fact.RecordedAt.IsZero() ||
		fact.ObservedAt.Before(fact.OccurredAt) || fact.RecordedAt.Before(fact.ObservedAt) {
		return IncomeOrigin{}, errors.New("income requires ordered occurrence, observation and recording instants")
	}
	return IncomeOrigin{fact: fact}, nil
}

func (origin IncomeOrigin) Fact() BankCreditFact { return origin.fact }
func (origin IncomeOrigin) ID() IncomeOriginID   { return origin.fact.TransactionID }

// LegacyLot is the v1 numeric meaning after a strict historical decoder has
// checked JSON types and evidence. This helper does not rewrite v1 originals.
type LegacyLot struct {
	TransactionID IncomeOriginID
	AccountID     ID
	EvidenceID    ID
	EvidenceHash  EvidenceHash
	Original      Money
	PriorUnspent  Money
	Spent         Money
	Assigned      Money
	Reserved      Money
	Available     Money
}

func NormalizeLegacyLot(lot LegacyLot, fact BankCreditFact) (IncomeOrigin, IncomeLocation, error) {
	origin, err := NewIncomeOrigin(fact)
	if err != nil {
		return IncomeOrigin{}, IncomeLocation{}, err
	}
	if lot.TransactionID != fact.TransactionID || lot.AccountID != fact.AccountID ||
		lot.EvidenceID != fact.EvidenceID || lot.EvidenceHash != fact.EvidenceHash || lot.Original != fact.Amount {
		return IncomeOrigin{}, IncomeLocation{}, errors.New("legacy lot differs from immutable original bank income")
	}
	total, err := SumMoney(lot.Spent, lot.Assigned, lot.Reserved, lot.Available)
	if err != nil || total != lot.Original {
		return IncomeOrigin{}, IncomeLocation{}, ErrConservation
	}
	unspent, err := lot.Reserved.Add(lot.Available)
	if err != nil || unspent != lot.PriorUnspent {
		return IncomeOrigin{}, IncomeLocation{}, ErrConservation
	}
	return origin, IncomeLocation{
		FragmentID: LocationID(lot.TransactionID, lot.AccountID), OriginID: lot.TransactionID,
		AccountID: lot.AccountID, Spent: lot.Spent, Assigned: lot.Assigned,
		Reserved: lot.Reserved, LegacyReserved: lot.Reserved, Available: lot.Available,
	}, nil
}
