package domain

import (
	"errors"
	"time"
)

type IncomeLot struct {
	FragmentID ID
	Origin     IncomeOrigin
	AccountID  ID
	Available  Money
}

func (ledger IncomeLedger) lots(now time.Time, action *ID) ([]IncomeLot, error) {
	if err := ledger.at(now); err != nil {
		return nil, err
	}
	restored := make(map[ID]Money)
	if action != nil {
		found := false
		for _, command := range ledger.snapshot.Reservations {
			if command.ActionID != *action {
				continue
			}
			found = true
			if command.State == Reserved {
				for _, use := range command.Uses {
					restored[use.FragmentID] = use.Amount
				}
			}
		}
		if !found {
			return nil, ErrReservationState
		}
	}
	origins := make(map[ID]IncomeOrigin, len(ledger.snapshot.Origins))
	for _, origin := range ledger.snapshot.Origins {
		origins[origin.ID()] = origin
	}
	lots := make([]IncomeLot, 0, len(ledger.snapshot.Locations))
	for _, location := range ledger.snapshot.Locations {
		available, err := location.Available.Add(restored[location.FragmentID])
		if err != nil {
			return nil, err
		}
		lots = append(lots, IncomeLot{FragmentID: location.FragmentID, Origin: origins[location.OriginID], AccountID: location.AccountID, Available: available})
	}
	return lots, nil
}

// IncomeLots returns unclaimed income only; preparing an action does not create
// a claim. The original amount may appear on several locations and is not an
// amount to sum. Sum Available instead.
func (ledger IncomeLedger) IncomeLots(now time.Time) ([]IncomeLot, error) {
	return ledger.lots(now, nil)
}

// IncomeLotsForReservation adds back only this exact still-RESERVED command's
// claims for revalidation. The adapter must also verify its immutable request.
func (ledger IncomeLedger) IncomeLotsForReservation(action ID, now time.Time) ([]IncomeLot, error) {
	return ledger.lots(now, &action)
}

// CashLocationFact describes one current CASH account, including goal ownership
// already present inside Balance. GoalOwned must not be added as another asset.
type CashLocationFact struct {
	AccountID  ID
	Currency   Currency
	Balance    Money
	GoalOwned  Money
	ObservedAt time.Time
}

// ValidateCashCoverage retains the old read-side requirement that available
// income plus ALL reservations fit unowned cash at each exact account. It does
// not establish evidence authenticity or broader future cash protection.
func (ledger IncomeLedger) ValidateCashCoverage(cash []CashLocationFact, now time.Time) error {
	if err := ledger.at(now); err != nil {
		return err
	}
	if len(cash) != len(ledger.snapshot.ScopeAccountIDs) {
		return errors.New("cash coverage must match the complete ledger scope")
	}
	byAccount := make(map[ID]CashLocationFact, len(cash))
	for _, fact := range cash {
		if _, duplicate := byAccount[fact.AccountID]; duplicate || fact.Currency != ledger.snapshot.Currency || fact.ObservedAt.IsZero() || fact.ObservedAt.After(ledger.snapshot.AsOf) {
			return errors.New("invalid, duplicate or future cash fact")
		}
		if _, err := fact.Balance.Sub(fact.GoalOwned); err != nil {
			return errors.New("goal ownership exceeds cash balance")
		}
		byAccount[fact.AccountID] = fact
	}
	for _, account := range ledger.snapshot.ScopeAccountIDs {
		if _, found := byAccount[account]; !found {
			return errors.New("missing scoped cash fact")
		}
	}
	origins := make(map[ID]IncomeOrigin, len(ledger.snapshot.Origins))
	for _, origin := range ledger.snapshot.Origins {
		origins[origin.ID()] = origin
	}
	totals := make(map[ID]Money)
	for _, location := range ledger.snapshot.Locations {
		if origins[location.OriginID].Fact().OccurredAt.After(byAccount[location.AccountID].ObservedAt) {
			return errors.New("income is not covered by the observed account balance")
		}
		part, err := location.Available.Add(location.Reserved)
		if err != nil {
			return err
		}
		totals[location.AccountID], err = totals[location.AccountID].Add(part)
		if err != nil {
			return err
		}
	}
	for account, total := range totals {
		unowned, _ := byAccount[account].Balance.Sub(byAccount[account].GoalOwned)
		if total.Cents() > unowned.Cents() {
			return errors.New("income availability and reservations exceed unowned location cash")
		}
	}
	return nil
}

// CurrentAvailableIncome is a source-eligibility amount, not execution
// permission or the full strict-cash-boundary result. effectiveFrom must come
// from the exact confirmed policy version (max(confirmed_at, valid_from)).
// There is deliberately no future-income input to this current amount.
func (ledger IncomeLedger) CurrentAvailableIncome(cash []CashLocationFact, effectiveFrom, now time.Time) (Money, error) {
	if effectiveFrom.IsZero() {
		return Money{}, ErrClock
	}
	if err := ledger.ValidateCashCoverage(cash, now); err != nil {
		return Money{}, err
	}
	lots, err := ledger.IncomeLots(now)
	if err != nil {
		return Money{}, err
	}
	var total Money
	for _, lot := range lots {
		if lot.Origin.Fact().OccurredAt.Before(effectiveFrom) {
			continue
		}
		total, err = total.Add(lot.Available)
		if err != nil {
			return Money{}, err
		}
	}
	return total, nil
}

type BankLocationView struct {
	IncomeLocation
	ActiveReserved Money
	BankAvailable  Money
	BankReserved   Money
}

// BankLocations separates application claims from actual bank occupancy. It is
// a pure view; it cannot open, execute, settle or authenticate a bank account.
func (ledger IncomeLedger) BankLocations() ([]BankLocationView, error) {
	if err := ledger.Validate(); err != nil {
		return nil, err
	}
	result := make([]BankLocationView, 0, len(ledger.snapshot.Locations))
	for _, location := range ledger.snapshot.Locations {
		active, err := location.Reserved.Sub(location.LegacyReserved)
		if err != nil {
			return nil, err
		}
		available, err := location.Available.Add(active)
		if err != nil {
			return nil, err
		}
		result = append(result, BankLocationView{IncomeLocation: location, ActiveReserved: active, BankAvailable: available, BankReserved: location.LegacyReserved})
	}
	return result, nil
}
