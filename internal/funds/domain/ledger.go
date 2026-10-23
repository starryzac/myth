package domain

import (
	"bytes"
	"errors"
	"fmt"
	"sort"
	"time"
)

const LedgerProtocol = "new-funds-ledger-v2"
const MaxLedgerItems = 100_000
const MaxScopeAccounts = 100

var (
	ErrConservation          = errors.New("income origin or reservation does not conserve")
	ErrInvalidLedger         = errors.New("invalid income ledger")
	ErrClock                 = errors.New("business instant is missing or moves backwards")
	ErrCommandConflict       = errors.New("action cannot change its original income command")
	ErrInsufficientAvailable = errors.New("use exceeds its exact available origin location")
	ErrReservationState      = errors.New("invalid income reservation state")
	ErrNoEffectRequired      = errors.New("reserved income requires confirmed no effect before release")
)

type IncomeOperation string
type ReservationState string

const (
	TransferInternal IncomeOperation  = "TRANSFER_INTERNAL"
	AllocateGoal     IncomeOperation  = "ALLOCATE_GOAL"
	Spend            IncomeOperation  = "SPEND"
	Reserved         ReservationState = "RESERVED"
	Committed        ReservationState = "COMMITTED"
	Released         ReservationState = "RELEASED"
)

// Assigned is goal ownership, not a second economic asset. Reserved includes
// LegacyReserved plus each still-RESERVED application action claim.
type IncomeLocation struct {
	FragmentID     ID
	OriginID       IncomeOriginID
	AccountID      ID
	Spent          Money
	Assigned       Money
	Reserved       Money
	LegacyReserved Money
	Available      Money
}

type IncomeUse struct {
	FragmentID ID
	OriginID   IncomeOriginID
	AccountID  ID
	Amount     Money
}

type IncomeReservation struct {
	ActionID  ID
	Operation IncomeOperation
	// DestinationAccountID must be nil for consumption and non-nil for transfer.
	DestinationAccountID *ID
	Uses                 []IncomeUse
	State                ReservationState
}

type LedgerSnapshot struct {
	UserID          ID
	Currency        Currency
	AsOf            time.Time
	ScopeAccountIDs []ID
	Origins         []IncomeOrigin
	Locations       []IncomeLocation
	Reservations    []IncomeReservation
}

// IncomeLedger owns defensive copies of all input slices and pointers. The
// complete source/evidence set must be verified by the adapter before entry.
type IncomeLedger struct{ snapshot LedgerSnapshot }

func idLess(a, b ID) bool { return bytes.Compare(a[:], b[:]) < 0 }

func cloneReservation(r IncomeReservation) IncomeReservation {
	r.Uses = append([]IncomeUse(nil), r.Uses...)
	if r.DestinationAccountID != nil {
		destination := *r.DestinationAccountID
		r.DestinationAccountID = &destination
	}
	return r
}

func cloneSnapshot(s LedgerSnapshot) LedgerSnapshot {
	s.ScopeAccountIDs = append([]ID(nil), s.ScopeAccountIDs...)
	s.Origins = append([]IncomeOrigin(nil), s.Origins...)
	s.Locations = append([]IncomeLocation(nil), s.Locations...)
	s.Reservations = append([]IncomeReservation(nil), s.Reservations...)
	for i := range s.Reservations {
		s.Reservations[i] = cloneReservation(s.Reservations[i])
	}
	return s
}

func (ledger IncomeLedger) Snapshot() LedgerSnapshot { return cloneSnapshot(ledger.snapshot) }

func NewIncomeLedger(input LedgerSnapshot) (IncomeLedger, error) {
	if err := validateSnapshot(input); err != nil {
		return IncomeLedger{}, err
	}
	s := cloneSnapshot(input)
	sort.Slice(s.ScopeAccountIDs, func(i, j int) bool { return idLess(s.ScopeAccountIDs[i], s.ScopeAccountIDs[j]) })
	sort.Slice(s.Origins, func(i, j int) bool { return idLess(s.Origins[i].ID(), s.Origins[j].ID()) })
	sort.Slice(s.Locations, func(i, j int) bool { return idLess(s.Locations[i].FragmentID, s.Locations[j].FragmentID) })
	sort.Slice(s.Reservations, func(i, j int) bool { return idLess(s.Reservations[i].ActionID, s.Reservations[j].ActionID) })
	for i := range s.Reservations {
		uses := s.Reservations[i].Uses
		sort.Slice(uses, func(i, j int) bool { return idLess(uses[i].FragmentID, uses[j].FragmentID) })
	}
	return IncomeLedger{snapshot: s}, nil
}

func (ledger IncomeLedger) Validate() error { return validateSnapshot(ledger.snapshot) }

func validateSnapshot(s LedgerSnapshot) error {
	if err := s.Currency.Validate(); err != nil {
		return err
	}
	if s.AsOf.IsZero() {
		return ErrClock
	}
	if len(s.ScopeAccountIDs) < 1 || len(s.ScopeAccountIDs) > MaxScopeAccounts ||
		len(s.Origins) > MaxLedgerItems || len(s.Locations) > MaxLedgerItems || len(s.Reservations) > MaxLedgerItems {
		return fmt.Errorf("%w: scope or capacity", ErrInvalidLedger)
	}
	scope := make(map[ID]bool, len(s.ScopeAccountIDs))
	for _, account := range s.ScopeAccountIDs {
		if scope[account] {
			return fmt.Errorf("%w: duplicate account", ErrInvalidLedger)
		}
		scope[account] = true
	}
	origins := make(map[ID]IncomeOrigin, len(s.Origins))
	for _, origin := range s.Origins {
		fact := origin.Fact()
		if _, err := NewIncomeOrigin(fact); err != nil {
			return fmt.Errorf("%w: %v", ErrInvalidLedger, err)
		}
		if _, found := origins[origin.ID()]; found {
			return fmt.Errorf("%w: duplicate origin", ErrInvalidLedger)
		}
		if !scope[fact.AccountID] || fact.Currency != s.Currency || fact.RecordedAt.After(s.AsOf) {
			return fmt.Errorf("%w: origin scope, currency or future fact", ErrInvalidLedger)
		}
		origins[origin.ID()] = origin
	}
	locations := make(map[ID]IncomeLocation, len(s.Locations))
	totals := make(map[ID]Money, len(s.Origins))
	for _, location := range s.Locations {
		_, exists := origins[location.OriginID]
		if _, duplicate := locations[location.FragmentID]; duplicate || !exists || !scope[location.AccountID] ||
			location.FragmentID != LocationID(location.OriginID, location.AccountID) || location.LegacyReserved.Cents() > location.Reserved.Cents() {
			return fmt.Errorf("%w: location identity, scope or legacy claim", ErrInvalidLedger)
		}
		part, err := SumMoney(location.Spent, location.Assigned, location.Reserved, location.Available)
		if err != nil {
			return ErrConservation
		}
		totals[location.OriginID], err = totals[location.OriginID].Add(part)
		if err != nil {
			return ErrConservation
		}
		locations[location.FragmentID] = location
	}
	for id, origin := range origins {
		if totals[id] != origin.Fact().Amount {
			return ErrConservation
		}
	}
	claims := make(map[ID]Money)
	actions := make(map[ID]bool, len(s.Reservations))
	for _, reservation := range s.Reservations {
		if actions[reservation.ActionID] {
			return fmt.Errorf("%w: duplicate action", ErrInvalidLedger)
		}
		actions[reservation.ActionID] = true
		if err := validateCommand(reservation, scope, locations); err != nil {
			return err
		}
		if reservation.State == Reserved {
			for _, use := range reservation.Uses {
				amount, err := claims[use.FragmentID].Add(use.Amount)
				if err != nil {
					return ErrConservation
				}
				claims[use.FragmentID] = amount
			}
		}
	}
	for _, location := range s.Locations {
		expected, err := location.LegacyReserved.Add(claims[location.FragmentID])
		if err != nil || expected != location.Reserved {
			return ErrConservation
		}
	}
	return nil
}

func validateCommand(command IncomeReservation, scope map[ID]bool, locations map[ID]IncomeLocation) error {
	if command.State != Reserved && command.State != Committed && command.State != Released {
		return ErrReservationState
	}
	if len(command.Uses) == 0 || len(command.Uses) > MaxLedgerItems {
		return fmt.Errorf("%w: empty or excessive uses", ErrInvalidLedger)
	}
	switch command.Operation {
	case TransferInternal:
		if command.DestinationAccountID == nil || !scope[*command.DestinationAccountID] {
			return fmt.Errorf("%w: transfer destination", ErrInvalidLedger)
		}
	case Spend, AllocateGoal:
		if command.DestinationAccountID != nil {
			return fmt.Errorf("%w: consumption cannot relabel location", ErrInvalidLedger)
		}
	default:
		return fmt.Errorf("%w: operation", ErrInvalidLedger)
	}
	seen := make(map[ID]bool, len(command.Uses))
	for _, use := range command.Uses {
		location, exists := locations[use.FragmentID]
		if seen[use.FragmentID] || use.Amount.IsZero() || !exists || location.OriginID != use.OriginID || location.AccountID != use.AccountID {
			return fmt.Errorf("%w: uses require unique exact origin locations", ErrInvalidLedger)
		}
		if command.Operation == TransferInternal && use.AccountID == *command.DestinationAccountID {
			return fmt.Errorf("%w: transfer to same account", ErrInvalidLedger)
		}
		seen[use.FragmentID] = true
	}
	return nil
}

func (ledger IncomeLedger) at(now time.Time) error {
	if err := ledger.Validate(); err != nil {
		return err
	}
	if now.IsZero() || now.Before(ledger.snapshot.AsOf) {
		return ErrClock
	}
	return nil
}

func sameCommand(a, b IncomeReservation) bool {
	if a.ActionID != b.ActionID || a.Operation != b.Operation || len(a.Uses) != len(b.Uses) ||
		(a.DestinationAccountID == nil) != (b.DestinationAccountID == nil) {
		return false
	}
	if a.DestinationAccountID != nil && *a.DestinationAccountID != *b.DestinationAccountID {
		return false
	}
	for i := range a.Uses {
		if a.Uses[i] != b.Uses[i] {
			return false
		}
	}
	return true
}

// Reserve replays a command idempotently even after its terminal transition.
// It never revives a released claim or changes a committed command.
func (ledger IncomeLedger) Reserve(action ID, uses []IncomeUse, operation IncomeOperation, now time.Time, destination *ID) (IncomeLedger, error) {
	if err := ledger.at(now); err != nil {
		return IncomeLedger{}, err
	}
	command := cloneReservation(IncomeReservation{ActionID: action, Operation: operation, DestinationAccountID: destination, Uses: uses, State: Reserved})
	sort.Slice(command.Uses, func(i, j int) bool { return idLess(command.Uses[i].FragmentID, command.Uses[j].FragmentID) })
	for _, existing := range ledger.snapshot.Reservations {
		if existing.ActionID != action {
			continue
		}
		if !sameCommand(existing, command) {
			return IncomeLedger{}, ErrCommandConflict
		}
		return ledger, nil
	}
	s := ledger.Snapshot()
	scope := make(map[ID]bool, len(s.ScopeAccountIDs))
	for _, account := range s.ScopeAccountIDs {
		scope[account] = true
	}
	locations := make(map[ID]IncomeLocation, len(s.Locations))
	for _, location := range s.Locations {
		locations[location.FragmentID] = location
	}
	if err := validateCommand(command, scope, locations); err != nil {
		return IncomeLedger{}, err
	}
	for _, use := range command.Uses {
		location := locations[use.FragmentID]
		var err error
		location.Available, err = location.Available.Sub(use.Amount)
		if err != nil {
			return IncomeLedger{}, ErrInsufficientAvailable
		}
		location.Reserved, err = location.Reserved.Add(use.Amount)
		if err != nil {
			return IncomeLedger{}, err
		}
		locations[use.FragmentID] = location
	}
	for i := range s.Locations {
		s.Locations[i] = locations[s.Locations[i].FragmentID]
	}
	s.AsOf = now
	s.Reservations = append(s.Reservations, command)
	return NewIncomeLedger(s)
}

// Commit projects an independently established settled result. The caller must
// verify the exact bank operation and original complete request before calling.
// This pure function neither submits an operation nor proves bank settlement.
func (ledger IncomeLedger) Commit(action ID, now time.Time) (IncomeLedger, error) {
	return ledger.finish(action, now, true, false)
}

// Release cannot use a timeout, NOT_FOUND, revocation or retry exhaustion as
// proof. confirmedNoEffect must come from the verified execution boundary.
func (ledger IncomeLedger) Release(action ID, now time.Time, confirmedNoEffect bool) (IncomeLedger, error) {
	if !confirmedNoEffect {
		return IncomeLedger{}, ErrNoEffectRequired
	}
	return ledger.finish(action, now, false, true)
}

func (ledger IncomeLedger) finish(action ID, now time.Time, commit, noEffect bool) (IncomeLedger, error) {
	if err := ledger.at(now); err != nil {
		return IncomeLedger{}, err
	}
	s := ledger.Snapshot()
	commandIndex := -1
	for i, command := range s.Reservations {
		if command.ActionID == action {
			commandIndex = i
			break
		}
	}
	if commandIndex < 0 {
		return IncomeLedger{}, ErrReservationState
	}
	command := s.Reservations[commandIndex]
	if commit && command.State == Committed || !commit && command.State == Released {
		return ledger, nil
	}
	if command.State != Reserved || !commit && !noEffect {
		return IncomeLedger{}, ErrReservationState
	}
	locations := make(map[ID]IncomeLocation, len(s.Locations))
	for _, location := range s.Locations {
		locations[location.FragmentID] = location
	}
	for _, use := range command.Uses {
		location := locations[use.FragmentID]
		var err error
		location.Reserved, err = location.Reserved.Sub(use.Amount)
		if err != nil {
			return IncomeLedger{}, ErrConservation
		}
		if !commit {
			location.Available, err = location.Available.Add(use.Amount)
		} else if command.Operation == TransferInternal {
			destination := *command.DestinationAccountID
			identity := LocationID(use.OriginID, destination)
			target, exists := locations[identity]
			if !exists {
				target = IncomeLocation{FragmentID: identity, OriginID: use.OriginID, AccountID: destination}
			}
			target.Available, err = target.Available.Add(use.Amount)
			locations[identity] = target
		} else if command.Operation == AllocateGoal {
			location.Assigned, err = location.Assigned.Add(use.Amount)
		} else {
			location.Spent, err = location.Spent.Add(use.Amount)
		}
		if err != nil {
			return IncomeLedger{}, err
		}
		locations[use.FragmentID] = location
	}
	s.Locations = make([]IncomeLocation, 0, len(locations))
	for _, location := range locations {
		s.Locations = append(s.Locations, location)
	}
	s.Reservations[commandIndex].State = Released
	if commit {
		s.Reservations[commandIndex].State = Committed
	}
	s.AsOf = now
	return NewIncomeLedger(s)
}
