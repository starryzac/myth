package executionaudit

import (
	"encoding/hex"
	"fmt"
	"math/big"
	"strings"

	evidence "zhiyu.local/zhiyu-go/internal/audit"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func nativeStringV3(row *ledgerRow, field string) string {
	value, ok := row.values[field].(string)
	if !ok {
		row.fail(field)
	}
	return value
}

// V3 has its own larger original-byte boundary. The frozen v1/v2 accessor is
// deliberately not changed by this protocol.
func nativeRawV3(row *ledgerRow, field string) []byte {
	value := row.text(field)
	if !strings.HasPrefix(value, `\x`) || len(value) > 2*NativeMaximumRawRowBytesV3+2 {
		row.fail(field)
		return nil
	}
	raw, err := hex.DecodeString(value[2:])
	if err != nil || len(raw) == 0 {
		row.fail(field)
	}
	return raw
}

func nativeOriginalV3(row *ledgerRow, field string, target any) []byte {
	raw := nativeRawV3(row, field)
	if nativeCompactOriginalV3(raw, target) != nil {
		row.fail(field)
	}
	return raw
}

func nativeHashFieldV3(row *ledgerRow, field, hash string) error {
	raw := nativeRawV3(row, field)
	if row.err != nil || nakedHash(raw) != row.text(hash) {
		return businessInvalid("native exact original bytes/hash: " + field)
	}
	return nil
}

func (g *nativeGraphV3) checkedRowsV3() error {
	for table, rows := range g.rows {
		for _, row := range rows {
			if row.err != nil {
				return fmt.Errorf("%w: native table %s", row.err, table)
			}
		}
	}
	return nil
}

func nativeExpectedReservationStateV3(state string) string {
	switch state {
	case "SETTLED":
		return "COMMITTED"
	case "REJECTED", "CANCELLED":
		return "RELEASED"
	case "QUEUED", "SUBMITTING", "PENDING_SETTLEMENT", "UNKNOWN", "RECONCILING":
		return "RESERVED"
	}
	return ""
}

func (g *nativeGraphV3) checkIncomeV3() error {
	user, err := funds.ParseID(g.manifest.Scope.UserID)
	if err != nil {
		return businessInvalid("native ledger user")
	}
	snapshot := funds.LedgerSnapshot{UserID: user, Currency: funds.CNY, AsOf: g.manifest.Source.CapturedAt}
	for _, row := range g.rows["accounts"] {
		snapshot.ScopeAccountIDs = append(snapshot.ScopeAccountIDs, row.id("id"))
		if row.text("currency") != "CNY" || row.money("goal_owned_cents").Cents() > row.money("balance_cents").Cents() || row.instant("observed_at").After(snapshot.AsOf) {
			return businessInvalid("native account current cash/ownership")
		}
	}
	for _, row := range g.rows["income_origins"] {
		hash, err := funds.ParseEvidenceHash(row.text("evidence_hash"))
		fact := funds.BankCreditFact{TransactionID: row.id("id"), AccountID: row.id("original_account_id"), Currency: funds.Currency(row.text("currency")), Amount: row.money("amount_cents"), Role: funds.RoleIncome, Source: funds.BankConfirmed, OccurredAt: row.instant("occurred_at"), ObservedAt: row.instant("observed_at"), RecordedAt: row.instant("recorded_at"), EvidenceID: row.id("evidence_id"), EvidenceHash: hash}
		origin, originErr := funds.NewIncomeOrigin(fact)
		if err != nil || originErr != nil || g.row("evidence_blobs", fact.EvidenceID.String()) == nil {
			return businessInvalid("native income immutable evidence")
		}
		if fact.RecordedAt.After(snapshot.AsOf) {
			return businessInvalid("native income after captured business instant")
		}
		snapshot.Origins = append(snapshot.Origins, origin)
	}
	for _, row := range g.rows["income_locations"] {
		snapshot.Locations = append(snapshot.Locations, funds.IncomeLocation{FragmentID: row.id("id"), OriginID: row.id("origin_id"), AccountID: row.id("account_id"), Spent: row.money("spent_cents"), Assigned: row.money("assigned_cents"), Reserved: row.money("reserved_cents"), LegacyReserved: row.money("legacy_reserved_cents"), Available: row.money("available_cents")})
		if !row.money("legacy_reserved_cents").IsZero() {
			return fmt.Errorf("%w: unregistered pre-v2 income hold evidence", evidence.ErrIncomplete)
		}
	}
	g.reservations = map[string]funds.IncomeReservation{}
	for _, family := range [][3]string{{"income_reservations", "income_claims", "operations"}, {"native_income_reservations", "native_income_claims", "native_operations"}} {
		for _, row := range g.rows[family[0]] {
			id := row.id("action_id")
			op := g.row(family[2], id.String())
			reservation := funds.IncomeReservation{ActionID: id, Operation: funds.IncomeOperation(row.text("operation")), State: funds.ReservationState(row.text("state"))}
			if op == nil || g.reservations[id.String()].ActionID != (funds.ID{}) || string(reservation.State) != nativeExpectedReservationStateV3(op.text("status")) {
				return businessInvalid("native income reservation operation/state")
			}
			if destination := row.optional("destination_account_id"); destination != "" {
				id, err := funds.ParseID(destination)
				if err != nil {
					return businessInvalid("native income destination")
				}
				reservation.DestinationAccountID = &id
			}
			for _, claim := range g.rows[family[1]] {
				if claim.text("action_id") != id.String() {
					continue
				}
				location := g.row("income_locations", claim.text("fragment_id"))
				if location == nil || claim.money("amount_cents").IsZero() {
					return businessInvalid("native income claim location")
				}
				reservation.Uses = append(reservation.Uses, funds.IncomeUse{FragmentID: claim.id("fragment_id"), OriginID: location.id("origin_id"), AccountID: location.id("account_id"), Amount: claim.money("amount_cents")})
			}
			g.reservations[id.String()] = reservation
			snapshot.Reservations = append(snapshot.Reservations, reservation)
		}
		for _, claim := range g.rows[family[1]] {
			if g.row(family[0], claim.text("action_id")) == nil {
				return businessInvalid("native orphan income claim")
			}
		}
	}
	if len(snapshot.ScopeAccountIDs) == 0 {
		if len(snapshot.Origins)+len(snapshot.Locations)+len(snapshot.Reservations)+len(g.rows["goals"])+len(g.rows["native_operations"])+len(g.rows["operations"]) != 0 {
			return businessInvalid("native financial rows without accounts")
		}
		return g.checkedRowsV3()
	}
	g.ledger, err = funds.NewIncomeLedger(snapshot)
	if err != nil {
		return businessInvalid("native origin conservation and all exact reserved claims")
	}
	return g.checkedRowsV3()
}

func (g *nativeGraphV3) checkGoalLotsV3() error {
	goalCash, accountOwned, lotCash := map[string]*big.Int{}, map[string]*big.Int{}, map[string]*big.Int{}
	for _, row := range g.rows["native_goal_balances"] {
		id := row.text("goal_id")
		goal := g.row("goals", id)
		if goal == nil {
			return businessInvalid("native balance goal missing")
		}
		cash, principal := row.money("cash_cents"), row.money("principal_cents")
		row.money("loss_cents") // Retained historical loss is not current owned value.
		total, err := funds.SumMoney(cash, principal)
		if err != nil || total != goal.money("assigned_cents") {
			return businessInvalid("native goal cash/principal/loss ownership")
		}
		addBusinessSum(goalCash, id, cash.Cents())
		addBusinessSum(accountOwned, goal.text("account_id"), cash.Cents())
	}
	for _, row := range g.rows["native_goal_lots"] {
		goal := g.row("goals", row.text("goal_id"))
		if goal == nil || goal.text("account_id") != row.text("account_id") || g.row("accounts", row.text("account_id")) == nil || row.money("reserved_cents").Cents() > row.money("available_cents").Cents() {
			return businessInvalid("native goal lot ownership/location")
		}
		switch row.text("source_kind") {
		case "INCOME":
			origin, err := funds.ParseID(row.text("source_id"))
			if err != nil || g.row("income_origins", origin.String()) == nil || g.row("income_locations", funds.LocationID(origin, row.id("account_id")).String()) == nil {
				return businessInvalid("native goal lot exact original income")
			}
		case "PRINCIPAL_RETURN":
			if g.row("native_principal_returns", row.text("source_id")) == nil || g.row("native_return_locations", row.text("source_id"), row.text("account_id")) == nil {
				return businessInvalid("native goal lot exact principal return")
			}
		default:
			return fmt.Errorf("%w: native goal lot source", evidence.ErrUnsupportedVersion)
		}
		addBusinessSum(lotCash, row.text("goal_id"), row.money("available_cents").Cents())
	}
	for _, goal := range g.rows["goals"] {
		id := goal.text("id")
		balance := g.row("native_goal_balances", id)
		if balance == nil || !businessSum(lotCash, id, balance.money("cash_cents").Cents()) {
			return businessInvalid("native complete goal cash lot inventory")
		}
	}
	for _, account := range g.rows["accounts"] {
		if !businessSum(accountOwned, account.text("id"), account.money("goal_owned_cents").Cents()) {
			return businessInvalid("native account goal ownership sum")
		}
	}
	return g.checkedRowsV3()
}
