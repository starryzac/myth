package app

import (
	"sort"
	"strings"
	"time"

	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// A lot is already owned cash. Neither this reference nor its move makes an
// AVAILABLE income claim. Principal returns keep their separate original key.
type NativeGoalTransferUse struct {
	SourceKind string      `json:"source_kind"`
	SourceID   string      `json:"source_id"`
	PositionID string      `json:"position_id,omitempty"`
	Amount     funds.Money `json:"amount_cents"`
}

func goalTransferLotUses(ledger postgres.NativeLedger, source, account funds.ID, amount funds.Money) ([]NativeGoalTransferUse, error) {
	return goalTransferLotUsesMatching(ledger, source, account, amount, nil)
}

// A source selector narrows eligible lots without changing the physical ledger
// or the complete forecast facts retained by the caller.
func goalTransferLotUsesMatching(ledger postgres.NativeLedger, source, account funds.ID, amount funds.Money, eligible func(postgres.NativeGoalLot) bool) ([]NativeGoalTransferUse, error) {
	if amount.IsZero() {
		return nil, reject("GOAL_TRANSFER_INVALID", nil)
	}
	lots := append([]postgres.NativeGoalLot(nil), ledger.GoalLots...)
	sort.Slice(lots, func(i, j int) bool {
		if lots[i].SourceKind != lots[j].SourceKind {
			return lots[i].SourceKind < lots[j].SourceKind
		}
		return lots[i].SourceID < lots[j].SourceID
	})
	remaining := amount
	uses := []NativeGoalTransferUse{}
	for _, lot := range lots {
		if lot.GoalID != source || lot.AccountID != account || remaining.IsZero() || eligible != nil && !eligible(lot) {
			continue
		}
		free, err := lot.Available.Sub(lot.Reserved)
		if err != nil {
			return nil, reject("EVIDENCE_INVALID", err)
		}
		use := NativeGoalTransferUse{SourceKind: lot.SourceKind, SourceID: lot.SourceID}
		switch lot.SourceKind {
		case "INCOME":
			origin, err := funds.ParseID(lot.SourceID)
			if err != nil {
				return nil, reject("EVIDENCE_INVALID", err)
			}
			fragment := funds.LocationID(origin, account)
			var assigned, held funds.Money
			found := false
			for _, location := range ledger.Income.Snapshot().Locations {
				if location.FragmentID == fragment && location.OriginID == origin && location.AccountID == account {
					assigned, found = location.Assigned, true
				}
			}
			for _, hold := range ledger.Holds {
				if hold.State == funds.Reserved && hold.ResourceKind == "ASSIGNED" && hold.ResourceID == fragment.String() {
					held, err = held.Add(hold.Amount)
					if err != nil {
						return nil, err
					}
				}
			}
			available, err := assigned.Sub(held)
			if !found || err != nil {
				return nil, reject("EVIDENCE_INVALID", err)
			}
			if available.Cents() < free.Cents() {
				free = available
			}
		case "PRINCIPAL_RETURN":
			found := false
			for _, original := range ledger.PrincipalReturns {
				if original.BankKey == lot.SourceID {
					use.PositionID, found = original.PositionID, true
				}
			}
			locationFound := false
			for _, location := range ledger.ReturnLocations {
				if location.BankKey == lot.SourceID && location.AccountID == account {
					available, err := location.Available.Sub(location.Reserved)
					if err != nil {
						return nil, reject("EVIDENCE_INVALID", err)
					}
					if available.Cents() < free.Cents() {
						free = available
					}
					locationFound = true
				}
			}
			if !found || !locationFound {
				return nil, reject("EVIDENCE_INVALID", nil)
			}
		default:
			return nil, reject("EVIDENCE_INVALID", nil)
		}
		if free.IsZero() {
			continue
		}
		if remaining.Cents() < free.Cents() {
			free = remaining
		}
		use.Amount = free
		uses = append(uses, use)
		remaining, err = remaining.Sub(free)
		if err != nil {
			return nil, err
		}
	}
	if !remaining.IsZero() || len(uses) > bankv2.MaxUses {
		return nil, reject("GOAL_CASH_UNAVAILABLE", nil)
	}
	return uses, nil
}

func goalTransferEffect(id, source, target, sourceAccount, targetAccount funds.ID, amount funds.Money, uses []NativeGoalTransferUse, expires time.Time) (bankv2.Effect, error) {
	e := bankv2.Effect{Kind: bankv2.MoveGoal, BusinessKey: "paired-goal:" + id.String(), Amount: amount, CashUses: []bankv2.CashUse{{AccountID: sourceAccount.String(), Amount: amount}}, OriginUses: []bankv2.OriginUse{}, ReturnUses: []bankv2.ReturnUse{}, SourceGoalID: source.String(), DestinationGoalID: target.String(), DestinationAccountID: targetAccount.String(), LatestArrivalAt: expires}
	if source == target || sourceAccount == targetAccount || id == (funds.ID{}) || len(uses) == 0 || len(uses) > bankv2.MaxUses {
		return e, reject("GOAL_TRANSFER_INVALID", nil)
	}
	for _, use := range uses {
		switch use.SourceKind {
		case "INCOME":
			if use.PositionID != "" {
				return e, reject("EVIDENCE_INVALID", nil)
			}
			if _, err := funds.ParseID(use.SourceID); err != nil {
				return e, reject("EVIDENCE_INVALID", err)
			}
			e.OriginUses = append(e.OriginUses, bankv2.OriginUse{OriginID: use.SourceID, AccountID: sourceAccount.String(), Amount: use.Amount, Bucket: "ASSIGNED_GOAL"})
		case "PRINCIPAL_RETURN":
			if use.PositionID == "" {
				return e, reject("EVIDENCE_INVALID", nil)
			}
			e.ReturnUses = append(e.ReturnUses, bankv2.ReturnUse{OperationKey: use.SourceID, PositionID: use.PositionID, AccountID: sourceAccount.String(), Amount: use.Amount})
		default:
			return e, reject("EVIDENCE_INVALID", nil)
		}
	}
	if err := e.Validate(); err != nil {
		return e, reject("EVIDENCE_INVALID", err)
	}
	return e, nil
}

func cloneGoalTransferLedger(ledger postgres.NativeLedger) postgres.NativeLedger {
	ledger.Accounts = append([]postgres.NativeAccount(nil), ledger.Accounts...)
	ledger.Goals = append([]postgres.NativeGoalBalance(nil), ledger.Goals...)
	ledger.GoalLots = append([]postgres.NativeGoalLot(nil), ledger.GoalLots...)
	ledger.Holds = append([]postgres.NativeHold(nil), ledger.Holds...)
	ledger.ReturnLocations = append([]postgres.NativeReturnLocation(nil), ledger.ReturnLocations...)
	ledger.ReturnClaims = append([]postgres.NativeReturnClaim(nil), ledger.ReturnClaims...)
	return ledger
}

func goalTransferExpectedHolds(operation funds.ID, effect bankv2.Effect, uses []NativeGoalTransferUse) ([]postgres.NativeHold, error) {
	if effect.Kind != bankv2.MoveGoal || !strings.HasPrefix(effect.BusinessKey, "paired-goal:") || effect.Validate() != nil {
		return nil, reject("EVIDENCE_INVALID", nil)
	}
	preview, e := funds.ParseID(strings.TrimPrefix(effect.BusinessKey, "paired-goal:"))
	if e != nil {
		return nil, reject("EVIDENCE_INVALID", e)
	}
	sourceAccount, _ := funds.ParseID(effect.CashUses[0].AccountID)
	targetAccount, _ := funds.ParseID(effect.DestinationAccountID)
	sourceGoal, _ := funds.ParseID(effect.SourceGoalID)
	targetGoal, _ := funds.ParseID(effect.DestinationGoalID)
	expected, e := goalTransferEffect(preview, sourceGoal, targetGoal, sourceAccount, targetAccount, effect.Amount, uses, effect.LatestArrivalAt)
	if e != nil {
		return nil, e
	}
	a, _ := expected.Digest()
	b, _ := effect.Digest()
	if a != b {
		return nil, reject("EVIDENCE_INVALID", nil)
	}
	source, e := funds.ParseID(effect.SourceGoalID)
	if e != nil {
		return nil, e
	}
	account, e := funds.ParseID(effect.CashUses[0].AccountID)
	if e != nil {
		return nil, e
	}
	holds := []postgres.NativeHold{{OperationID: operation, ResourceKind: "GOAL", ResourceID: source.String(), Amount: effect.Amount, State: funds.Reserved}, {OperationID: operation, ResourceKind: "INCOMING_GOAL", ResourceID: effect.DestinationGoalID, Amount: effect.Amount, State: funds.Reserved}}
	for _, use := range uses {
		ref, e := encode(postgres.NativeLotReference{AccountID: account.String(), SourceKind: use.SourceKind, SourceID: use.SourceID})
		if e != nil {
			return nil, e
		}
		holds = append(holds, postgres.NativeHold{OperationID: operation, ResourceKind: "GOAL_LOT", ResourceID: source.String(), ResourceDetail: string(ref), Amount: use.Amount, State: funds.Reserved})
		if use.SourceKind == "INCOME" {
			origin, e := funds.ParseID(use.SourceID)
			if e != nil {
				return nil, e
			}
			holds = append(holds, postgres.NativeHold{OperationID: operation, ResourceKind: "ASSIGNED", ResourceID: funds.LocationID(origin, account).String(), Amount: use.Amount, State: funds.Reserved})
		}
	}
	return holds, nil
}

func reserveGoalTransferResources(ledger postgres.NativeLedger, operation funds.ID, effect bankv2.Effect, uses []NativeGoalTransferUse) (postgres.NativeLedger, error) {
	zero := postgres.NativeLedger{}
	if effect.Kind != bankv2.MoveGoal || effect.Validate() != nil || operation == (funds.ID{}) {
		return zero, reject("EVIDENCE_INVALID", nil)
	}
	for _, hold := range ledger.Holds {
		if hold.OperationID == operation {
			return zero, reject("IDEMPOTENCY_CONFLICT", nil)
		}
	}
	source, _ := funds.ParseID(effect.SourceGoalID)
	account, _ := funds.ParseID(effect.CashUses[0].AccountID)
	selected, err := goalTransferLotUses(ledger, source, account, effect.Amount)
	if err != nil {
		return zero, err
	}
	a, _ := encode(selected)
	b, _ := encode(uses)
	if string(a) != string(b) {
		return zero, reject("SOURCE_CHANGED", nil)
	}
	var cash, held funds.Money
	found := false
	for _, goal := range ledger.Goals {
		if goal.GoalID == source && goal.AccountID == account {
			cash, found = goal.Cash, true
		}
	}
	for _, hold := range ledger.Holds {
		if hold.ResourceKind == "GOAL" && hold.ResourceID == source.String() && hold.State == funds.Reserved {
			held, err = held.Add(hold.Amount)
			if err != nil {
				return zero, err
			}
		}
	}
	available, err := cash.Sub(held)
	if !found || err != nil || available.Cents() < effect.Amount.Cents() {
		return zero, reject("GOAL_CASH_UNAVAILABLE", err)
	}
	out := cloneGoalTransferLedger(ledger)
	for _, use := range uses {
		matched := false
		for i := range out.GoalLots {
			lot := &out.GoalLots[i]
			if lot.GoalID == source && lot.AccountID == account && lot.SourceKind == use.SourceKind && lot.SourceID == use.SourceID {
				lot.Reserved, err = lot.Reserved.Add(use.Amount)
				if err != nil || lot.Reserved.Cents() > lot.Available.Cents() {
					return zero, reject("GOAL_CASH_UNAVAILABLE", err)
				}
				matched = true
			}
		}
		if !matched {
			return zero, reject("EVIDENCE_INVALID", nil)
		}
		if use.SourceKind == "PRINCIPAL_RETURN" {
			matched = false
			for i := range out.ReturnLocations {
				location := &out.ReturnLocations[i]
				if location.BankKey == use.SourceID && location.AccountID == account {
					location.Reserved, err = location.Reserved.Add(use.Amount)
					if err != nil || location.Reserved.Cents() > location.Available.Cents() {
						return zero, reject("GOAL_CASH_UNAVAILABLE", err)
					}
					matched = true
				}
			}
			if !matched {
				return zero, reject("EVIDENCE_INVALID", nil)
			}
			out.ReturnClaims = append(out.ReturnClaims, postgres.NativeReturnClaim{OperationID: operation, BankKey: use.SourceID, AccountID: account, Amount: use.Amount, State: funds.Reserved})
		}
	}
	holds, err := goalTransferExpectedHolds(operation, effect, uses)
	if err != nil {
		return zero, err
	}
	out.Holds = append(out.Holds, holds...)
	return out, nil
}

// A signed UNKNOWN keeps every pin. Only an independently verified no-effect
// result (or an unsigned local cancellation) calls this release projection.
func releaseGoalTransferResources(ledger postgres.NativeLedger, operation funds.ID, effect bankv2.Effect, uses []NativeGoalTransferUse, noEffect bool) (postgres.NativeLedger, error) {
	if !noEffect {
		return postgres.NativeLedger{}, reject("OPERATION_UNKNOWN", nil)
	}
	return finishGoalTransferResources(ledger, operation, effect, uses, false, ledger.Income.Snapshot().AsOf)
}

func finishGoalTransferResources(ledger postgres.NativeLedger, operation funds.ID, effect bankv2.Effect, uses []NativeGoalTransferUse, commit bool, now time.Time) (postgres.NativeLedger, error) {
	zero := postgres.NativeLedger{}
	if effect.Kind != bankv2.MoveGoal || effect.Validate() != nil {
		return zero, reject("EVIDENCE_INVALID", nil)
	}
	expected, err := goalTransferExpectedHolds(operation, effect, uses)
	if err != nil {
		return zero, err
	}
	wantState := funds.Released
	if commit {
		wantState = funds.Committed
	}
	reserved, ended, actual := 0, 0, 0
	for _, hold := range ledger.Holds {
		if hold.OperationID != operation {
			continue
		}
		actual++
		matched := false
		for _, want := range expected {
			if hold.ResourceKind == want.ResourceKind && hold.ResourceID == want.ResourceID && hold.ResourceDetail == want.ResourceDetail && hold.Amount == want.Amount {
				matched = true
			}
		}
		if !matched {
			return zero, reject("EVIDENCE_INVALID", nil)
		}
		if hold.State == funds.Reserved {
			reserved++
		} else if hold.State == wantState {
			ended++
		} else {
			return zero, reject("EVIDENCE_INVALID", nil)
		}
	}
	if actual != len(expected) || reserved != actual && ended != actual {
		return zero, reject("EVIDENCE_INVALID", nil)
	}
	if ended == actual {
		return cloneGoalTransferLedger(ledger), nil
	}
	out := cloneGoalTransferLedger(ledger)
	source, _ := funds.ParseID(effect.SourceGoalID)
	target, _ := funds.ParseID(effect.DestinationGoalID)
	account, _ := funds.ParseID(effect.CashUses[0].AccountID)
	destination, _ := funds.ParseID(effect.DestinationAccountID)
	for _, use := range uses {
		found := false
		for i := range out.GoalLots {
			lot := &out.GoalLots[i]
			if lot.GoalID == source && lot.AccountID == account && lot.SourceKind == use.SourceKind && lot.SourceID == use.SourceID {
				lot.Reserved, err = lot.Reserved.Sub(use.Amount)
				if err == nil && commit {
					lot.Available, err = lot.Available.Sub(use.Amount)
				}
				if err != nil {
					return zero, reject("EVIDENCE_INVALID", err)
				}
				found = true
			}
		}
		if !found {
			return zero, reject("EVIDENCE_INVALID", nil)
		}
		if use.SourceKind == "PRINCIPAL_RETURN" {
			claimFound, locationFound := false, false
			for i := range out.ReturnClaims {
				claim := &out.ReturnClaims[i]
				if claim.OperationID == operation && claim.BankKey == use.SourceID && claim.AccountID == account && claim.Amount == use.Amount && claim.State == funds.Reserved {
					claim.State, claimFound = wantState, true
				}
			}
			for i := range out.ReturnLocations {
				location := &out.ReturnLocations[i]
				if location.BankKey == use.SourceID && location.AccountID == account {
					location.Reserved, err = location.Reserved.Sub(use.Amount)
					if err == nil && commit {
						location.Available, err = location.Available.Sub(use.Amount)
					}
					if err != nil {
						return zero, reject("EVIDENCE_INVALID", err)
					}
					locationFound = true
				}
			}
			if !claimFound || !locationFound {
				return zero, reject("EVIDENCE_INVALID", nil)
			}
		}
		if !commit {
			continue
		}
		found = false
		for i := range out.GoalLots {
			lot := &out.GoalLots[i]
			if lot.GoalID == target && lot.AccountID == destination && lot.SourceKind == use.SourceKind && lot.SourceID == use.SourceID {
				lot.Available, err = lot.Available.Add(use.Amount)
				if err != nil {
					return zero, err
				}
				found = true
			}
		}
		if !found {
			out.GoalLots = append(out.GoalLots, postgres.NativeGoalLot{GoalID: target, AccountID: destination, SourceKind: use.SourceKind, SourceID: use.SourceID, Available: use.Amount})
		}
		if use.SourceKind == "INCOME" {
			origin, _ := funds.ParseID(use.SourceID)
			snapshot := out.Income.Snapshot()
			found = false
			for i := range snapshot.Locations {
				location := &snapshot.Locations[i]
				if location.OriginID == origin && location.AccountID == account {
					location.Assigned, err = location.Assigned.Sub(use.Amount)
					if err != nil {
						return zero, err
					}
					found = true
				}
			}
			if !found {
				return zero, reject("EVIDENCE_INVALID", nil)
			}
			found = false
			for i := range snapshot.Locations {
				location := &snapshot.Locations[i]
				if location.OriginID == origin && location.AccountID == destination {
					location.Assigned, err = location.Assigned.Add(use.Amount)
					if err != nil {
						return zero, err
					}
					found = true
				}
			}
			if !found {
				snapshot.Locations = append(snapshot.Locations, funds.IncomeLocation{FragmentID: funds.LocationID(origin, destination), OriginID: origin, AccountID: destination, Assigned: use.Amount})
			}
			snapshot.AsOf = now
			out.Income, err = funds.NewIncomeLedger(snapshot)
			if err != nil {
				return zero, err
			}
		} else {
			found = false
			for i := range out.ReturnLocations {
				location := &out.ReturnLocations[i]
				if location.BankKey == use.SourceID && location.AccountID == destination {
					location.Available, err = location.Available.Add(use.Amount)
					if err != nil {
						return zero, err
					}
					found = true
				}
			}
			if !found {
				out.ReturnLocations = append(out.ReturnLocations, postgres.NativeReturnLocation{BankKey: use.SourceID, AccountID: destination, Available: use.Amount})
			}
		}
	}
	for i := range out.Holds {
		if out.Holds[i].OperationID == operation {
			out.Holds[i].State = wantState
		}
	}
	if !commit {
		return out, nil
	}
	sourceFound, targetFound := false, false
	for i := range out.Goals {
		goal := &out.Goals[i]
		if goal.GoalID == source && goal.AccountID == account {
			goal.Cash, err = goal.Cash.Sub(effect.Amount)
			sourceFound = true
		} else if goal.GoalID == target && goal.AccountID == destination {
			goal.Cash, err = goal.Cash.Add(effect.Amount)
			targetFound = true
		}
		if err != nil {
			return zero, err
		}
	}
	if !sourceFound || !targetFound {
		return zero, reject("EVIDENCE_INVALID", nil)
	}
	sourceFound, targetFound = false, false
	for i := range out.Accounts {
		a := &out.Accounts[i]
		if a.ID == account {
			a.Balance, err = a.Balance.Sub(effect.Amount)
			if err == nil {
				a.GoalOwned, err = a.GoalOwned.Sub(effect.Amount)
			}
			a.ObservedAt, sourceFound = now, true
		} else if a.ID == destination {
			a.Balance, err = a.Balance.Add(effect.Amount)
			if err == nil {
				a.GoalOwned, err = a.GoalOwned.Add(effect.Amount)
			}
			a.ObservedAt, targetFound = now, true
		}
		if err != nil {
			return zero, err
		}
	}
	if !sourceFound || !targetFound {
		return zero, reject("EVIDENCE_INVALID", nil)
	}
	return out, nil
}
