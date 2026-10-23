package app

import (
	"sort"
	"time"

	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

// This server-owned selection constraint never replaces ledger/facts amounts.
// Income and settled principal-return identities remain separate end to end.
type nativeStandingAssetSource struct {
	Family     policy.NativeFamily           `json:"family"`
	Source     policy.NativeOccurrenceSource `json:"source"`
	AccountIDs []funds.ID                    `json:"account_ids"`
}

func (source nativeStandingAssetSource) validate(n postgres.NativeLedger, now time.Time) error {
	if source.Family != policy.NativeAssetAllocation && source.Family != policy.NativeMaturityReinvest || source.Source.Validate(source.Family) != nil || source.Source.OccurredAt.After(now) || source.Source.AvailableAt.After(now) || len(source.AccountIDs) == 0 || len(source.AccountIDs) > 16 {
		return reject("NATIVE_ASSET_SOURCE_INVALID", nil)
	}
	last := ""
	for _, id := range source.AccountIDs {
		if id == (funds.ID{}) || id.String() <= last {
			return reject("NATIVE_ASSET_SOURCE_INVALID", nil)
		}
		last = id.String()
	}
	if source.Family == policy.NativeAssetAllocation {
		for _, origin := range n.Income.Snapshot().Origins {
			if origin.ID().String() == source.Source.ID {
				if origin.Fact().EvidenceHash.String() != source.Source.OriginalHash || !origin.Fact().OccurredAt.Equal(source.Source.OccurredAt) {
					return reject("EVIDENCE_INVALID", nil)
				}
				return nil
			}
		}
	} else {
		for _, original := range n.PrincipalReturns {
			if original.BankKey == source.Source.ID {
				if Hash(original.Original) != original.OriginalHash || original.OriginalHash != source.Source.OriginalHash || !original.OccurredAt.Equal(source.Source.OccurredAt) || !original.OccurredAt.Equal(source.Source.AvailableAt) {
					return reject("EVIDENCE_INVALID", nil)
				}
				return nil
			}
		}
	}
	return reject("EVIDENCE_INVALID", nil)
}

func (source nativeStandingAssetSource) account(id funds.ID) bool {
	for _, allowed := range source.AccountIDs {
		if allowed == id {
			return true
		}
	}
	return false
}
func (source nativeStandingAssetSource) lot(lot postgres.NativeGoalLot) bool {
	return source.account(lot.AccountID) && lot.SourceID == source.Source.ID && (source.Family == policy.NativeAssetAllocation && lot.SourceKind == "INCOME" || source.Family == policy.NativeMaturityReinvest && lot.SourceKind == "PRINCIPAL_RETURN")
}

func selectStandingAssetCash(n postgres.NativeLedger, source nativeStandingAssetSource, cap funds.Money) (NativeCashResources, error) {
	var result NativeCashResources
	if err := source.validate(n, n.Income.Snapshot().AsOf); err != nil {
		return result, err
	}
	a, err := nativeUnownedCashAvailability(n)
	if err != nil {
		return result, err
	}
	remaining, cash := cap, map[funds.ID]funds.Money{}
	if source.Family == policy.NativeAssetAllocation {
		for _, use := range a.income {
			if use.OriginID.String() != source.Source.ID || !source.account(use.AccountID) || remaining.IsZero() {
				continue
			}
			use.Amount = nativeResourceMinimum(use.Amount, remaining)
			result.IncomeUses = append(result.IncomeUses, use)
			if err = nativeResourceAdd(cash, use.AccountID, use.Amount); err != nil {
				return result, err
			}
			remaining, _ = remaining.Sub(use.Amount)
		}
	} else {
		for _, use := range a.returns {
			account, e := funds.ParseID(use.AccountID)
			if e != nil {
				return result, e
			}
			if use.OperationKey != source.Source.ID || !source.account(account) || remaining.IsZero() {
				continue
			}
			use.Amount = nativeResourceMinimum(use.Amount, remaining)
			result.ReturnUses = append(result.ReturnUses, use)
			if err = nativeResourceAdd(cash, account, use.Amount); err != nil {
				return result, err
			}
			remaining, _ = remaining.Sub(use.Amount)
		}
	}
	ids := make([]funds.ID, 0, len(cash))
	for id := range cash {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i].String() < ids[j].String() })
	for _, id := range ids {
		result.CashUses = append(result.CashUses, bankv2.CashUse{AccountID: id.String(), Amount: cash[id]})
	}
	result.Amount, _ = cap.Sub(remaining)
	return result, nil
}

func standingAssetGoalAmount(n postgres.NativeLedger, source nativeStandingAssetSource, goal funds.ID) (funds.Money, error) {
	if err := source.validate(n, n.Income.Snapshot().AsOf); err != nil {
		return funds.Money{}, err
	}
	var total funds.Money
	for _, lot := range n.GoalLots {
		if lot.GoalID != goal || !source.lot(lot) {
			continue
		}
		free, err := lot.Available.Sub(lot.Reserved)
		if err != nil {
			return funds.Money{}, nativeResourceConflict()
		}
		if source.Family == policy.NativeAssetAllocation {
			var assigned, held funds.Money
			fragment := funds.LocationID(func() funds.ID { id, _ := funds.ParseID(source.Source.ID); return id }(), lot.AccountID)
			for _, l := range n.Income.Snapshot().Locations {
				if l.FragmentID == fragment {
					assigned = l.Assigned
				}
			}
			for _, h := range n.Holds {
				if h.State == funds.Reserved && h.ResourceKind == "ASSIGNED" && h.ResourceID == fragment.String() {
					held, err = held.Add(h.Amount)
					if err != nil {
						return funds.Money{}, err
					}
				}
			}
			available, e := assigned.Sub(held)
			if e != nil {
				return funds.Money{}, nativeResourceConflict()
			}
			free = nativeResourceMinimum(free, available)
		} else {
			found := false
			for _, l := range n.ReturnLocations {
				if l.BankKey == lot.SourceID && l.AccountID == lot.AccountID {
					available, e := l.Available.Sub(l.Reserved)
					if e != nil {
						return funds.Money{}, nativeResourceConflict()
					}
					free, found = nativeResourceMinimum(free, available), true
				}
			}
			if !found {
				return funds.Money{}, nativeResourceConflict()
			}
		}
		total, err = total.Add(free)
		if err != nil {
			return funds.Money{}, err
		}
	}
	return total, nil
}
