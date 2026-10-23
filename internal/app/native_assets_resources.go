package app

import (
	"encoding/json"
	"sort"

	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// NativeCashResources classifies existing unowned cash without changing its
// identity. Principal returns never enter IncomeUses. Slack is the independently
// remaining physical cash after every classified available source and hold.
type NativeCashResources struct {
	Amount     funds.Money        `json:"amount_cents"`
	CashUses   []bankv2.CashUse   `json:"cash_uses"`
	IncomeUses []funds.IncomeUse  `json:"income_uses"`
	ReturnUses []bankv2.ReturnUse `json:"return_uses"`
	SlackUses  []bankv2.CashUse   `json:"slack_uses"`
}

type nativeUnownedAvailability struct {
	accounts map[funds.ID]bool
	free     map[funds.ID]funds.Money
	slack    map[funds.ID]funds.Money
	income   []funds.IncomeUse
	returns  []bankv2.ReturnUse
}

func nativeResourceConflict() error { return reject("NATIVE_RESOURCE_CONFLICT", nil) }
func nativeResourceAdd[K comparable](values map[K]funds.Money, key K, value funds.Money) error {
	next, err := values[key].Add(value)
	if err != nil {
		return err
	}
	values[key] = next
	return nil
}
func nativeResourceMinimum(a, b funds.Money) funds.Money {
	if a.Cents() < b.Cents() {
		return a
	}
	return b
}
func nativeReturnKey(key string, account funds.ID) string { return key + ":" + account.String() }

// The caller supplies the complete LoadNativeLedger snapshot. CASH includes
// each operation's AVAILABLE income/return claims; count their overlap once.
// Goal cash and goal-owned return lots are excluded before selecting anything.
func nativeUnownedCashAvailability(n postgres.NativeLedger) (nativeUnownedAvailability, error) {
	a := nativeUnownedAvailability{accounts: map[funds.ID]bool{}, free: map[funds.ID]funds.Money{}, slack: map[funds.ID]funds.Money{}}
	if n.Income.Validate() != nil {
		return a, nativeResourceConflict()
	}
	snapshot := n.Income.Snapshot()
	for _, account := range n.Accounts {
		if account.ID == (funds.ID{}) || a.accounts[account.ID] {
			return a, nativeResourceConflict()
		}
		free, err := account.Balance.Sub(account.GoalOwned)
		if err != nil {
			return a, nativeResourceConflict()
		}
		a.accounts[account.ID], a.free[account.ID] = true, free
	}
	if len(a.accounts) != len(snapshot.ScopeAccountIDs) {
		return a, nativeResourceConflict()
	}
	for _, account := range snapshot.ScopeAccountIDs {
		if !a.accounts[account] {
			return a, nativeResourceConflict()
		}
	}
	type heldKey struct{ operation, account funds.ID }
	cashHeld, incomeHeld := map[heldKey]funds.Money{}, map[heldKey]funds.Money{}
	for _, hold := range n.Holds {
		if hold.State != funds.Reserved || hold.ResourceKind != "CASH" {
			continue
		}
		account, err := funds.ParseID(hold.ResourceID)
		if err != nil || !a.accounts[account] || hold.Amount.IsZero() {
			return a, nativeResourceConflict()
		}
		if err = nativeResourceAdd(cashHeld, heldKey{hold.OperationID, account}, hold.Amount); err != nil {
			return a, err
		}
	}
	for _, reservation := range snapshot.Reservations {
		if reservation.State != funds.Reserved {
			continue
		}
		for _, use := range reservation.Uses {
			if err := nativeResourceAdd(incomeHeld, heldKey{reservation.ActionID, use.AccountID}, use.Amount); err != nil {
				return a, err
			}
		}
	}
	for key, amount := range incomeHeld {
		if amount.Cents() > cashHeld[key].Cents() {
			cashHeld[key] = amount
		}
	}
	for key, amount := range cashHeld {
		var err error
		a.free[key.account], err = a.free[key.account].Sub(amount)
		if err != nil {
			return a, nativeResourceConflict()
		}
	}
	incomeTotal := map[funds.ID]funds.Money{}
	for _, location := range snapshot.Locations {
		var err error
		a.free[location.AccountID], err = a.free[location.AccountID].Sub(location.LegacyReserved)
		if err != nil {
			return a, nativeResourceConflict()
		}
		if location.Available.IsZero() {
			continue
		}
		a.income = append(a.income, funds.IncomeUse{FragmentID: location.FragmentID, OriginID: location.OriginID, AccountID: location.AccountID, Amount: location.Available})
		if err = nativeResourceAdd(incomeTotal, location.AccountID, location.Available); err != nil {
			return a, err
		}
	}
	ownedReturn, heldOwnedReturn := map[string]funds.Money{}, map[string]funds.Money{}
	for _, lot := range n.GoalLots {
		if lot.SourceKind != "PRINCIPAL_RETURN" {
			continue
		}
		key := nativeReturnKey(lot.SourceID, lot.AccountID)
		if lot.Reserved.Cents() > lot.Available.Cents() {
			return a, nativeResourceConflict()
		}
		if err := nativeResourceAdd(ownedReturn, key, lot.Available); err != nil {
			return a, err
		}
		if err := nativeResourceAdd(heldOwnedReturn, key, lot.Reserved); err != nil {
			return a, err
		}
	}
	returns := map[string]postgres.NativePrincipalReturn{}
	for _, original := range n.PrincipalReturns {
		var proof bankv2.PrincipalReturn
		if _, duplicate := returns[original.BankKey]; duplicate || original.BankKey == "" || Hash(original.Original) != original.OriginalHash || json.Unmarshal(original.Original, &proof) != nil || proof.Role != "PRINCIPAL_RETURN" || proof.OperationKey != original.BankKey || proof.PositionID != original.PositionID || proof.Amount != original.Amount || !proof.OccurredAt.Equal(original.OccurredAt) {
			return a, nativeResourceConflict()
		}
		returns[original.BankKey] = original
	}
	returnTotal, locations := map[funds.ID]funds.Money{}, map[string]bool{}
	for _, location := range n.ReturnLocations {
		key := nativeReturnKey(location.BankKey, location.AccountID)
		original, exists := returns[location.BankKey]
		if !exists || locations[key] || !a.accounts[location.AccountID] {
			return a, nativeResourceConflict()
		}
		locations[key] = true
		unowned, err := location.Available.Sub(ownedReturn[key])
		if err != nil {
			return a, nativeResourceConflict()
		}
		held, err := location.Reserved.Sub(heldOwnedReturn[key])
		if err != nil {
			return a, nativeResourceConflict()
		}
		free, err := unowned.Sub(held)
		if err != nil {
			return a, nativeResourceConflict()
		}
		if free.IsZero() {
			continue
		}
		a.returns = append(a.returns, bankv2.ReturnUse{OperationKey: original.BankKey, PositionID: original.PositionID, AccountID: location.AccountID.String(), Amount: free})
		if err = nativeResourceAdd(returnTotal, location.AccountID, free); err != nil {
			return a, err
		}
	}
	for key := range ownedReturn {
		if !locations[key] {
			return a, nativeResourceConflict()
		}
	}
	for account := range a.accounts {
		classified, err := incomeTotal[account].Add(returnTotal[account])
		if err != nil {
			return a, err
		}
		a.slack[account], err = a.free[account].Sub(classified)
		if err != nil {
			return a, nativeResourceConflict()
		}
	}
	sort.Slice(a.income, func(i, j int) bool {
		x, y := a.income[i], a.income[j]
		if x.AccountID != y.AccountID {
			return x.AccountID.String() < y.AccountID.String()
		}
		if x.OriginID != y.OriginID {
			return x.OriginID.String() < y.OriginID.String()
		}
		return x.FragmentID.String() < y.FragmentID.String()
	})
	sort.Slice(a.returns, func(i, j int) bool {
		if a.returns[i].AccountID != a.returns[j].AccountID {
			return a.returns[i].AccountID < a.returns[j].AccountID
		}
		return a.returns[i].OperationKey < a.returns[j].OperationKey
	})
	return a, nil
}

// Selects global tiers in this order: actual AVAILABLE income, unowned original
// principal-return locations, then physical unclassified slack. The caller must
// separately cap this by the complete hard-protection forecast and permission.
func selectNativeUnownedCash(n postgres.NativeLedger, accountIDs []funds.ID, cap funds.Money) (NativeCashResources, error) {
	var result NativeCashResources
	a, err := nativeUnownedCashAvailability(n)
	if err != nil {
		return result, err
	}
	selected := map[funds.ID]bool{}
	if accountIDs == nil {
		for id := range a.accounts {
			selected[id] = true
		}
	} else {
		for _, id := range accountIDs {
			if !a.accounts[id] || selected[id] {
				return result, nativeResourceConflict()
			}
			selected[id] = true
		}
	}
	remaining, cash := cap, map[funds.ID]funds.Money{}
	for _, use := range a.income {
		if !selected[use.AccountID] || remaining.IsZero() {
			continue
		}
		use.Amount = nativeResourceMinimum(use.Amount, remaining)
		result.IncomeUses = append(result.IncomeUses, use)
		if err = nativeResourceAdd(cash, use.AccountID, use.Amount); err != nil {
			return NativeCashResources{}, err
		}
		remaining, _ = remaining.Sub(use.Amount)
	}
	for _, use := range a.returns {
		account, _ := funds.ParseID(use.AccountID)
		if !selected[account] || remaining.IsZero() {
			continue
		}
		use.Amount = nativeResourceMinimum(use.Amount, remaining)
		result.ReturnUses = append(result.ReturnUses, use)
		if err = nativeResourceAdd(cash, account, use.Amount); err != nil {
			return NativeCashResources{}, err
		}
		remaining, _ = remaining.Sub(use.Amount)
	}
	ids := make([]funds.ID, 0, len(selected))
	for id := range selected {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i].String() < ids[j].String() })
	for _, id := range ids {
		amount := nativeResourceMinimum(a.slack[id], remaining)
		if amount.IsZero() {
			continue
		}
		result.SlackUses = append(result.SlackUses, bankv2.CashUse{AccountID: id.String(), Amount: amount})
		if err = nativeResourceAdd(cash, id, amount); err != nil {
			return NativeCashResources{}, err
		}
		remaining, _ = remaining.Sub(amount)
	}
	for _, id := range ids {
		if !cash[id].IsZero() {
			result.CashUses = append(result.CashUses, bankv2.CashUse{AccountID: id.String(), Amount: cash[id]})
		}
	}
	if len(result.CashUses) > 100 || len(result.IncomeUses) > 100 || len(result.ReturnUses) > 100 {
		return NativeCashResources{}, reject("CAPACITY_EXCEEDED", nil)
	}
	result.Amount, _ = cap.Sub(remaining)
	return result, nil
}

func cloneNativeAssetSlice[T any](values []T) []T {
	if values == nil {
		return nil
	}
	return append(make([]T, 0, len(values)), values...)
}
func cloneNativeAssetLedger(n postgres.NativeLedger) postgres.NativeLedger {
	n.Accounts = cloneNativeAssetSlice(n.Accounts)
	n.Goals = cloneNativeAssetSlice(n.Goals)
	n.GoalLots = cloneNativeAssetSlice(n.GoalLots)
	n.Holds = cloneNativeAssetSlice(n.Holds)
	n.Positions = cloneNativeAssetSlice(n.Positions)
	n.PrincipalReturns = cloneNativeAssetSlice(n.PrincipalReturns)
	n.ReturnLocations = cloneNativeAssetSlice(n.ReturnLocations)
	n.ReturnClaims = cloneNativeAssetSlice(n.ReturnClaims)
	return n
}

func nativeCashOriginUses(resources NativeCashResources) []bankv2.OriginUse {
	uses := make([]bankv2.OriginUse, 0, len(resources.IncomeUses))
	for _, use := range resources.IncomeUses {
		uses = append(uses, bankv2.OriginUse{OriginID: use.OriginID.String(), AccountID: use.AccountID.String(), Amount: use.Amount, Bucket: "AVAILABLE"})
	}
	return uses
}

// Admission already replays its API key before this new reservation. Resource
// commands cannot be redefined or revived by calling this helper a second time.
// It changes no account balance and performs no I/O; Persist is in the caller's T1.
func reserveNativeUnownedCash(n *postgres.NativeLedger, operation funds.ID, kind bankv2.Kind, resources NativeCashResources) error {
	return reserveNativeUnownedCashWithSource(n, operation, kind, resources, nil)
}

func reserveNativeUnownedCashWithSource(n *postgres.NativeLedger, operation funds.ID, kind bankv2.Kind, resources NativeCashResources, source *nativeStandingAssetSource) error {
	if n == nil || operation == (funds.ID{}) || kind != bankv2.Purchase && kind != bankv2.Payment || resources.Amount.IsZero() {
		return nativeResourceConflict()
	}
	for _, h := range n.Holds {
		if h.OperationID == operation {
			return nativeResourceConflict()
		}
	}
	for _, c := range n.ReturnClaims {
		if c.OperationID == operation {
			return nativeResourceConflict()
		}
	}
	for _, r := range n.Income.Snapshot().Reservations {
		if r.ActionID == operation {
			return nativeResourceConflict()
		}
	}
	// Re-select the exact requested cap within the named accounts. This rejects
	// inflated slack, changed source identity and omitted preferred source tiers.
	accounts := make([]funds.ID, 0, len(resources.CashUses))
	for _, use := range resources.CashUses {
		id, err := funds.ParseID(use.AccountID)
		if err != nil {
			return nativeResourceConflict()
		}
		accounts = append(accounts, id)
	}
	var expected NativeCashResources
	var err error
	if source == nil {
		expected, err = selectNativeUnownedCash(*n, accounts, resources.Amount)
	} else {
		expected, err = selectStandingAssetCash(*n, *source, resources.Amount)
	}
	if err != nil {
		return err
	}
	left, err := encode(expected)
	if err != nil {
		return err
	}
	right, err := encode(resources)
	if err != nil || string(left) != string(right) {
		return nativeResourceConflict()
	}
	next := cloneNativeAssetLedger(*n)
	if len(resources.IncomeUses) != 0 {
		next.Income, err = next.Income.Reserve(operation, resources.IncomeUses, funds.Spend, next.Income.Snapshot().AsOf, nil)
		if err != nil {
			return err
		}
	}
	for _, use := range resources.ReturnUses {
		account, _ := funds.ParseID(use.AccountID)
		found := false
		for i := range next.ReturnLocations {
			location := &next.ReturnLocations[i]
			if location.BankKey != use.OperationKey || location.AccountID != account {
				continue
			}
			location.Reserved, err = location.Reserved.Add(use.Amount)
			if err != nil || location.Reserved.Cents() > location.Available.Cents() {
				return nativeResourceConflict()
			}
			found = true
		}
		if !found {
			return nativeResourceConflict()
		}
		next.ReturnClaims = append(next.ReturnClaims, postgres.NativeReturnClaim{OperationID: operation, BankKey: use.OperationKey, AccountID: account, Amount: use.Amount, State: funds.Reserved})
	}
	for _, use := range resources.CashUses {
		next.Holds = append(next.Holds, postgres.NativeHold{OperationID: operation, ResourceKind: "CASH", ResourceID: use.AccountID, Amount: use.Amount, State: funds.Reserved})
	}
	*n = next
	return nil
}

// Only a verified SETTLED original permits the caller to commit these claims.
// Account/goal changes, receipt, journal and operation status remain the T3 owner.
func commitNativeUnownedCash(n *postgres.NativeLedger, operation funds.ID, kind bankv2.Kind) error {
	if n == nil || operation == (funds.ID{}) || kind != bankv2.Purchase && kind != bankv2.Payment {
		return nativeResourceConflict()
	}
	next := cloneNativeAssetLedger(*n)
	for _, r := range next.Income.Snapshot().Reservations {
		if r.ActionID != operation {
			continue
		}
		if r.Operation != funds.Spend {
			return nativeResourceConflict()
		}
		var err error
		next.Income, err = next.Income.Commit(operation, next.Income.Snapshot().AsOf)
		if err != nil {
			return err
		}
	}
	for i := range next.ReturnClaims {
		claim := &next.ReturnClaims[i]
		if claim.OperationID != operation {
			continue
		}
		if claim.State == funds.Committed {
			continue
		}
		if claim.State != funds.Reserved {
			return nativeResourceConflict()
		}
		found := false
		for j := range next.ReturnLocations {
			location := &next.ReturnLocations[j]
			if location.BankKey != claim.BankKey || location.AccountID != claim.AccountID {
				continue
			}
			var err error
			location.Reserved, err = location.Reserved.Sub(claim.Amount)
			if err == nil {
				location.Available, err = location.Available.Sub(claim.Amount)
			}
			if err != nil {
				return nativeResourceConflict()
			}
			found = true
		}
		if !found {
			return nativeResourceConflict()
		}
		claim.State = funds.Committed
	}
	for i := range next.Holds {
		hold := &next.Holds[i]
		if hold.OperationID != operation {
			continue
		}
		if hold.ResourceKind != "CASH" || hold.State == funds.Released {
			return nativeResourceConflict()
		}
		hold.State = funds.Committed
	}
	*n = next
	return nil
}

// The execution caller supplies confirmed no effect: unsigned cancellation or a
// verified REJECTED bank original. UNKNOWN, timeout, revocation and NOT_FOUND do
// not call this helper. It never changes income/return identities or balances.
func releaseNativeAssetResources(n *postgres.NativeLedger, operation funds.ID) error {
	if n == nil || operation == (funds.ID{}) {
		return nativeResourceConflict()
	}
	next := cloneNativeAssetLedger(*n)
	for _, r := range next.Income.Snapshot().Reservations {
		if r.ActionID != operation {
			continue
		}
		var err error
		next.Income, err = next.Income.Release(operation, next.Income.Snapshot().AsOf, true)
		if err != nil {
			return err
		}
	}
	for i := range next.ReturnClaims {
		claim := &next.ReturnClaims[i]
		if claim.OperationID != operation || claim.State == funds.Released {
			continue
		}
		if claim.State != funds.Reserved {
			return nativeResourceConflict()
		}
		found := false
		for j := range next.ReturnLocations {
			location := &next.ReturnLocations[j]
			if location.BankKey != claim.BankKey || location.AccountID != claim.AccountID {
				continue
			}
			var err error
			location.Reserved, err = location.Reserved.Sub(claim.Amount)
			if err != nil {
				return nativeResourceConflict()
			}
			found = true
		}
		if !found {
			return nativeResourceConflict()
		}
		claim.State = funds.Released
	}
	for i := range next.Holds {
		hold := &next.Holds[i]
		if hold.OperationID != operation || hold.State == funds.Released {
			continue
		}
		if hold.State != funds.Reserved {
			return nativeResourceConflict()
		}
		if hold.ResourceKind == "GOAL_LOT" {
			var ref postgres.NativeLotReference
			if decode([]byte(hold.ResourceDetail), &ref) != nil {
				return nativeResourceConflict()
			}
			found := false
			for j := range next.GoalLots {
				lot := &next.GoalLots[j]
				if lot.GoalID.String() != hold.ResourceID || lot.AccountID.String() != ref.AccountID || lot.SourceKind != ref.SourceKind || lot.SourceID != ref.SourceID {
					continue
				}
				var err error
				lot.Reserved, err = lot.Reserved.Sub(hold.Amount)
				if err != nil {
					return nativeResourceConflict()
				}
				found = true
			}
			if !found {
				return nativeResourceConflict()
			}
		}
		hold.State = funds.Released
	}
	for i := range next.Positions {
		position := &next.Positions[i]
		if position.ClosingOperationID == nil || *position.ClosingOperationID != operation {
			continue
		}
		if position.Closed {
			return nativeResourceConflict()
		}
		position.ClosingOperationID = nil
	}
	*n = next
	return nil
}
