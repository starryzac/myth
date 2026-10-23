package app

import (
	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
)

func verifyNativeIncomeStock(ledger postgres.NativeLedger, capture nativeIncomeCapture) error {
	fail := func() error { return reject("INCOME_NATIVE_STOCK_MISMATCH", nil) }
	goals := map[string]postgres.NativeGoalBalance{}
	for _, g := range ledger.Goals {
		goals[g.GoalID.String()] = g
	}
	for _, g := range capture.NativeStock.Goals {
		local, exists := goals[g.ID]
		if !exists || local.AccountID.String() != g.AccountID || local.Cash != g.Cash || local.Principal != g.Principal || local.Loss != g.Loss {
			return fail()
		}
		delete(goals, g.ID)
	}
	for _, g := range goals {
		// A USER-created goal has no bank ownership before its first real
		// allocation. Missing zero anchors never copy an application balance.
		if !g.Cash.IsZero() || !g.Principal.IsZero() || !g.Loss.IsZero() {
			return fail()
		}
	}
	if len(ledger.Positions) != len(capture.NativeStock.Positions) {
		return fail()
	}
	positions := map[string]postgres.NativePosition{}
	for _, p := range ledger.Positions {
		positions[p.ID] = p
	}
	closingKeys := map[string]string{}
	for _, result := range capture.Native {
		if result.Request == nil {
			return fail()
		}
		closingKeys[result.Request.OperationID] = result.Key
	}
	for _, bank := range capture.NativeStock.Positions {
		p, exists := positions[bank.Opening.PositionID]
		hash, err := bank.Opening.Digest()
		if !exists || err != nil || hash != bank.OpeningHash || p.OriginalHash != hash || bank.Opening.Namespace != capture.State.Namespace || bank.Opening.UserID != capture.State.UserID || p.BankKey != bank.Opening.OpeningKey || p.Closed != bank.Closed {
			return fail()
		}
		if p.ClosingOperationID == nil {
			if bank.ClosingKey != "" {
				return fail()
			}
		} else if closingKeys[p.ClosingOperationID.String()] == "" || closingKeys[p.ClosingOperationID.String()] != bank.ClosingKey {
			return fail()
		}
		delete(positions, p.ID)
	}
	if len(positions) != 0 {
		return fail()
	}
	return nil
}
