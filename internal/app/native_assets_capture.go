package app

import (
	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// Explicit income facts are essential: IncomeLedger and IncomeOrigin have
// private fields and must never be marshaled as empty JSON objects.
type NativeAssetLedgerCapture struct {
	Namespace        string                           `json:"namespace"`
	Income           NativeJointLedger                `json:"income"`
	Accounts         []postgres.NativeAccount         `json:"accounts"`
	Goals            []postgres.NativeGoalBalance     `json:"goals"`
	GoalLots         []postgres.NativeGoalLot         `json:"goal_lots"`
	Holds            []postgres.NativeHold            `json:"holds"`
	Positions        []postgres.NativePosition        `json:"positions"`
	PrincipalReturns []postgres.NativePrincipalReturn `json:"principal_returns"`
	ReturnLocations  []postgres.NativeReturnLocation  `json:"return_locations"`
	ReturnClaims     []postgres.NativeReturnClaim     `json:"return_claims"`
}

func captureNativeAssetLedger(n postgres.NativeLedger) NativeAssetLedgerCapture {
	s := n.Income.Snapshot()
	facts := make([]funds.BankCreditFact, len(s.Origins))
	for i, o := range s.Origins {
		facts[i] = o.Fact()
	}
	return NativeAssetLedgerCapture{Namespace: n.Namespace, Income: NativeJointLedger{s.UserID, s.Currency, s.AsOf, s.ScopeAccountIDs, facts, s.Locations, s.Reservations}, Accounts: n.Accounts, Goals: n.Goals, GoalLots: n.GoalLots, Holds: n.Holds, Positions: n.Positions, PrincipalReturns: n.PrincipalReturns, ReturnLocations: n.ReturnLocations, ReturnClaims: n.ReturnClaims}
}
func (c NativeAssetLedgerCapture) DomainLedger() (postgres.NativeLedger, error) {
	origins := make([]funds.IncomeOrigin, len(c.Income.OriginFacts))
	for i, fact := range c.Income.OriginFacts {
		o, e := funds.NewIncomeOrigin(fact)
		if e != nil {
			return postgres.NativeLedger{}, e
		}
		origins[i] = o
	}
	income, e := funds.NewIncomeLedger(funds.LedgerSnapshot{UserID: c.Income.UserID, Currency: c.Income.Currency, AsOf: c.Income.AsOf, ScopeAccountIDs: c.Income.ScopeAccountIDs, Origins: origins, Locations: c.Income.Locations, Reservations: c.Income.Reservations})
	if e != nil {
		return postgres.NativeLedger{}, e
	}
	return cloneNativeAssetLedger(postgres.NativeLedger{Namespace: c.Namespace, Income: income, Accounts: c.Accounts, Goals: c.Goals, GoalLots: c.GoalLots, Holds: c.Holds, Positions: c.Positions, PrincipalReturns: c.PrincipalReturns, ReturnLocations: c.ReturnLocations, ReturnClaims: c.ReturnClaims}), nil
}

func replayNativeAssetPreview(preview NativeAssetPreview) error {
	ledger, err := preview.Ledger.DomainLedger()
	if err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	if Hash(preview.InventoryOriginal) != preview.InventoryHash || ledger.Income.Snapshot().UserID != preview.Scope.UserID || !ledger.Income.Snapshot().AsOf.Equal(preview.CreatedAt) || len(preview.ForecastInput.FutureIncome) != 0 || len(preview.ForecastInput.PrincipalReturns) != 0 {
		return reject("EVIDENCE_INVALID", nil)
	}
	d := preview.Dependencies
	snapshot := templateSnapshot{Facts: preview.ForecastInput.Facts, Versions: preview.ForecastInput.Versions, Views: preview.Policies, Retained: preview.ForecastInput.RetainedObligations, Held: preview.ForecastInput.HeldCash, UnassignedGoalCash: preview.ForecastInput.UnassignedGoalCash, InventoryRaw: preview.InventoryOriginal, FundsVersion: d.FundsVersion, SourceVersion: d.SourceEvidenceVersion, WriterGeneration: d.WriterGeneration, ControlVersion: d.ControlVersion, AsOf: preview.CreatedAt, Timezone: preview.Timezone, Native: &ledger}
	snapshot.ExternalLiabilities = preview.ForecastInput.ExternalLiabilities
	expected, err := deriveNativeAssetPreview(snapshot, preview.Request, preview.ID, preview.SessionID, preview.BankState, preview.Quote, preview.PendingPurchases, preview.ExpiresAt)
	if err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	a, err := encode(expected)
	if err != nil {
		return err
	}
	b, err := encode(preview)
	if err != nil || string(a) != string(b) {
		return reject("EVIDENCE_INVALID", err)
	}
	return nil
}
