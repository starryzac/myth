package app

import (
	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func nativeAssetExpectedHolds(operation funds.ID, preview NativeAssetPreview) ([]postgres.NativeHold, error) {
	e := preview.BankEffect
	out := []postgres.NativeHold{}
	if !nativeAssetKind(e.Kind) || e.Validate() != nil {
		return nil, nativeResourceConflict()
	}
	add := func(kind, id, detail string, amount funds.Money) {
		if !amount.IsZero() {
			out = append(out, postgres.NativeHold{OperationID: operation, ResourceKind: kind, ResourceID: id, ResourceDetail: detail, Amount: amount, State: funds.Reserved})
		}
	}
	if e.Kind == bankv2.Purchase {
		if e.SourceGoalID == "" {
			for _, use := range preview.CashResources.CashUses {
				add("CASH", use.AccountID, "", use.Amount)
			}
		} else {
			add("GOAL", e.SourceGoalID, "", e.Amount)
			for _, use := range preview.GoalUses {
				ref, err := encode(postgres.NativeLotReference{AccountID: e.CashUses[0].AccountID, SourceKind: use.SourceKind, SourceID: use.SourceID})
				if err != nil {
					return nil, err
				}
				add("GOAL_LOT", e.SourceGoalID, string(ref), use.Amount)
				if use.SourceKind == "INCOME" {
					origin, err := funds.ParseID(use.SourceID)
					if err != nil {
						return nil, err
					}
					account, _ := funds.ParseID(e.CashUses[0].AccountID)
					add("ASSIGNED", funds.LocationID(origin, account).String(), "", use.Amount)
				}
			}
		}
	} else {
		add("POSITION", e.PositionID, "", e.Amount)
		add("INCOMING_CASH", e.ReturnAccountID, "", e.Net)
		if e.SourceGoalID != "" {
			add("INCOMING_GOAL", e.SourceGoalID, "", e.Net)
		}
	}
	return out, nil
}

func reserveNativeAssetResources(n *postgres.NativeLedger, operation funds.ID, preview NativeAssetPreview) error {
	return reserveNativeAssetResourcesWithSource(n, operation, preview, nil)
}

func reserveNativeAssetResourcesWithSource(n *postgres.NativeLedger, operation funds.ID, preview NativeAssetPreview, source *nativeStandingAssetSource) error {
	if n == nil || operation == (funds.ID{}) {
		return nativeResourceConflict()
	}
	e := preview.BankEffect
	if e.Kind == bankv2.Purchase && e.SourceGoalID == "" {
		return reserveNativeUnownedCashWithSource(n, operation, bankv2.Purchase, preview.CashResources, source)
	}
	next := cloneNativeAssetLedger(*n)
	for _, h := range next.Holds {
		if h.OperationID == operation {
			return nativeResourceConflict()
		}
	}
	holds, err := nativeAssetExpectedHolds(operation, preview)
	if err != nil {
		return err
	}
	if e.Kind == bankv2.Purchase {
		goal, ge := funds.ParseID(e.SourceGoalID)
		account, ae := funds.ParseID(e.CashUses[0].AccountID)
		if ge != nil || ae != nil {
			return nativeResourceConflict()
		}
		var selected []NativeGoalTransferUse
		var err error
		if source == nil {
			selected, err = goalTransferLotUses(next, goal, account, e.Amount)
		} else {
			selected, err = goalTransferLotUsesMatching(next, goal, account, e.Amount, source.lot)
		}
		if err != nil {
			return err
		}
		a, _ := encode(selected)
		b, _ := encode(preview.GoalUses)
		if string(a) != string(b) {
			return reject("SOURCE_CHANGED", nil)
		}
		var cash, held funds.Money
		found := false
		for _, g := range next.Goals {
			if g.GoalID == goal && g.AccountID == account {
				cash, found = g.Cash, true
			}
		}
		for _, h := range next.Holds {
			if h.State == funds.Reserved && h.ResourceKind == "GOAL" && h.ResourceID == goal.String() {
				held, err = held.Add(h.Amount)
				if err != nil {
					return err
				}
			}
		}
		free, err := cash.Sub(held)
		if !found || err != nil || free.Cents() < e.Amount.Cents() {
			return nativeResourceConflict()
		}
		for _, use := range preview.GoalUses {
			found = false
			for i := range next.GoalLots {
				lot := &next.GoalLots[i]
				if lot.GoalID == goal && lot.AccountID == account && lot.SourceKind == use.SourceKind && lot.SourceID == use.SourceID {
					lot.Reserved, err = lot.Reserved.Add(use.Amount)
					if err != nil || lot.Reserved.Cents() > lot.Available.Cents() {
						return nativeResourceConflict()
					}
					found = true
				}
			}
			if !found {
				return nativeResourceConflict()
			}
			if use.SourceKind == "PRINCIPAL_RETURN" {
				found = false
				for i := range next.ReturnLocations {
					r := &next.ReturnLocations[i]
					if r.BankKey == use.SourceID && r.AccountID == account {
						r.Reserved, err = r.Reserved.Add(use.Amount)
						if err != nil || r.Reserved.Cents() > r.Available.Cents() {
							return nativeResourceConflict()
						}
						found = true
					}
				}
				if !found {
					return nativeResourceConflict()
				}
				next.ReturnClaims = append(next.ReturnClaims, postgres.NativeReturnClaim{OperationID: operation, BankKey: use.SourceID, AccountID: account, Amount: use.Amount, State: funds.Reserved})
			}
		}
	} else {
		found := false
		for i := range next.Positions {
			p := &next.Positions[i]
			if p.ID == e.PositionID {
				if p.Closed || p.ClosingOperationID != nil || p.OriginalHash != e.PositionOpeningHash || p.Principal != e.Amount || p.ReturnAccountID.String() != e.ReturnAccountID {
					return nativeResourceConflict()
				}
				copy := operation
				p.ClosingOperationID = &copy
				found = true
			}
		}
		if !found {
			return nativeResourceConflict()
		}
	}
	next.Holds = append(next.Holds, holds...)
	*n = next
	return nil
}

func checkNativeAssetResources(n postgres.NativeLedger, operation funds.ID, preview NativeAssetPreview) error {
	expected, err := nativeAssetExpectedHolds(operation, preview)
	if err != nil {
		return err
	}
	actual := 0
	for _, h := range n.Holds {
		if h.OperationID != operation {
			continue
		}
		actual++
		matched := false
		for _, want := range expected {
			if h == want {
				matched = true
			}
		}
		if !matched {
			return nativeResourceConflict()
		}
	}
	if actual != len(expected) {
		return nativeResourceConflict()
	}
	claims := 0
	for _, claim := range n.ReturnClaims {
		if claim.OperationID != operation {
			continue
		}
		claims++
		matched := false
		for _, use := range preview.BankEffect.ReturnUses {
			if claim.State == funds.Reserved && claim.BankKey == use.OperationKey && claim.AccountID.String() == use.AccountID && claim.Amount == use.Amount {
				matched = true
			}
		}
		if !matched {
			return nativeResourceConflict()
		}
	}
	if claims != len(preview.BankEffect.ReturnUses) {
		return nativeResourceConflict()
	}
	if preview.Kind != bankv2.Purchase {
		found := false
		for _, position := range n.Positions {
			if position.ID == preview.BankEffect.PositionID && position.OriginalHash == preview.BankEffect.PositionOpeningHash && !position.Closed && position.ClosingOperationID != nil && *position.ClosingOperationID == operation {
				found = true
			}
		}
		if !found {
			return nativeResourceConflict()
		}
	}
	if preview.Kind == bankv2.Purchase && preview.BankEffect.SourceGoalID == "" {
		var found bool
		for _, r := range n.Income.Snapshot().Reservations {
			if r.ActionID != operation {
				continue
			}
			a, _ := encode(r.Uses)
			b, _ := encode(preview.CashResources.IncomeUses)
			if r.State != funds.Reserved || r.Operation != funds.Spend || r.DestinationAccountID != nil || string(a) != string(b) {
				// Reserve sorts fragments. Compare canonical independent maps rather
				// than requiring an account-order selection to equal that order.
				if r.State != funds.Reserved || r.Operation != funds.Spend || r.DestinationAccountID != nil || len(r.Uses) != len(preview.CashResources.IncomeUses) {
					return nativeResourceConflict()
				}
				for _, u := range r.Uses {
					same := false
					for _, v := range preview.CashResources.IncomeUses {
						if u == v {
							same = true
						}
					}
					if !same {
						return nativeResourceConflict()
					}
				}
			}
			found = true
		}
		if found != (len(preview.CashResources.IncomeUses) > 0) {
			return nativeResourceConflict()
		}
	}
	return nil
}

func assetSnapshotWithoutReservation(snapshot templateSnapshot, operation funds.ID) (templateSnapshot, error) {
	if snapshot.Native == nil {
		return snapshot, nativeResourceConflict()
	}
	copy := cloneNativeAssetLedger(*snapshot.Native)
	if err := releaseNativeAssetResources(&copy, operation); err != nil {
		return snapshot, err
	}
	snapshot.Native = &copy
	var base funds.Money
	for _, l := range copy.Income.Snapshot().Locations {
		var err error
		base, err = base.Add(l.Reserved)
		if err != nil {
			return snapshot, err
		}
	}
	held, err := nativeTemplateHeld(base, copy.Income, copy)
	if err != nil {
		return snapshot, err
	}
	snapshot.Held = held
	available := map[funds.ID]funds.Money{}
	for _, l := range copy.Income.Snapshot().Locations {
		if err = nativeResourceAdd(available, l.OriginID, l.Available); err != nil {
			return snapshot, err
		}
	}
	snapshot.Facts.Income = append(snapshot.Facts.Income[:0:0], snapshot.Facts.Income...)
	for i := range snapshot.Facts.Income {
		snapshot.Facts.Income[i].Available = available[snapshot.Facts.Income[i].ID]
	}
	return snapshot, nil
}
