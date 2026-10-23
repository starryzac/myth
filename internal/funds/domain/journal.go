package domain

import (
	"errors"
	"fmt"
)

type JournalDimension string
type PostingDirection string

const (
	CashDimension                JournalDimension = "CASH"
	PrincipalDimension           JournalDimension = "PRINCIPAL"
	GoalOwnershipDimension       JournalDimension = "GOAL_OWNERSHIP"
	IncomeQualificationDimension JournalDimension = "INCOME_QUALIFICATION"
	DebtMemoDimension            JournalDimension = "DEBT_MEMO"
	Debit                        PostingDirection = "DEBIT"
	Credit                       PostingDirection = "CREDIT"
)

type Posting struct {
	Dimension JournalDimension
	Currency  Currency
	AccountID ID
	Direction PostingDirection
	Amount    Money
}

// ValidateBalancedPostings checks each currency/dimension independently. It
// cannot hide an unbalanced cash leg by offsetting goal or debt memo entries.
// It does not infer authorization, bank effects or journal uniqueness.
func ValidateBalancedPostings(postings []Posting) error {
	if len(postings) == 0 || len(postings) > MaxLedgerItems {
		return errors.New("journal postings empty or above capacity")
	}
	type key struct {
		currency  Currency
		dimension JournalDimension
	}
	type totals struct{ debit, credit Money }
	byKey := make(map[key]totals)
	for _, posting := range postings {
		if err := posting.Currency.Validate(); err != nil {
			return err
		}
		switch posting.Dimension {
		case CashDimension, PrincipalDimension, GoalOwnershipDimension, IncomeQualificationDimension, DebtMemoDimension:
		default:
			return errors.New("unsupported journal dimension")
		}
		if posting.Amount.IsZero() {
			return errors.New("zero journal posting")
		}
		k := key{posting.Currency, posting.Dimension}
		total := byKey[k]
		var err error
		switch posting.Direction {
		case Debit:
			total.debit, err = total.debit.Add(posting.Amount)
		case Credit:
			total.credit, err = total.credit.Add(posting.Amount)
		default:
			return errors.New("unsupported posting direction")
		}
		if err != nil {
			return err
		}
		byKey[k] = total
	}
	// Report multiple invalid dimensions in a stable order, independent of Go
	// map iteration and the caller's posting order.
	for _, dimension := range []JournalDimension{CashDimension, PrincipalDimension, GoalOwnershipDimension, IncomeQualificationDimension, DebtMemoDimension} {
		k := key{CNY, dimension}
		total := byKey[k]
		if total.debit != total.credit {
			return fmt.Errorf("%w: %s/%s", ErrConservation, k.currency, k.dimension)
		}
	}
	return nil
}
