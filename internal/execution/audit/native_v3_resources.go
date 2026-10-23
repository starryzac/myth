package executionaudit

import (
	"fmt"
	"math/big"
	evidence "zhiyu.local/zhiyu-go/internal/audit"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func nativeSumValueV3(values map[string]*big.Int, key string) *big.Int {
	if v := values[key]; v != nil {
		return new(big.Int).Set(v)
	}
	return new(big.Int)
}
func nativeHoldKeyV3(kind, id, detail string) string { return kind + "\x00" + id + "\x00" + detail }

func (g *nativeGraphV3) checkResourcesV3() error {
	holds, cash, incoming, goal, assigned, lot, position := map[string]*big.Int{}, map[string]*big.Int{}, map[string]*big.Int{}, map[string]*big.Int{}, map[string]*big.Int{}, map[string]*big.Int{}, map[string]*big.Int{}
	for id, request := range g.nativeRequests {
		op := g.row("native_operations", id)
		state := nativeExpectedReservationStateV3(op.text("status"))
		expected := []wireNativeHold{}
		add := func(kind, resource, detail string, n funds.Money) {
			if !n.IsZero() {
				expected = append(expected, wireNativeHold{OperationID: mustBusinessID(id), ResourceKind: kind, ResourceID: resource, ResourceDetail: detail, Amount: n, State: funds.ReservationState(state)})
			}
		}
		switch request.Effect.Kind {
		case bankv2.Allocate, bankv2.Transfer:
			for _, use := range request.Effect.CashUses {
				add("CASH", use.AccountID, "", use.Amount)
			}
		case bankv2.Payment:
			for _, use := range request.Effect.CashUses {
				add("CASH", use.AccountID, "", use.Amount)
			}
			add("LIABILITY", request.Effect.Liability.ID, "", request.Effect.Amount)
		case bankv2.Purchase, bankv2.Redeem, bankv2.Mature:
			var preview wireNativeAssetPreview
			if op.text("mode") == "STANDING" {
				p := g.standingPlans[id].asset
				if p == nil {
					return businessInvalid("native asset resource original standing preview")
				}
				preview = p.Preview
			} else {
				p, ok := g.nativeActions[op.text("preview_id")].(wireNativeAssetPreview)
				if !ok {
					return businessInvalid("native asset resource original USER preview")
				}
				preview = p
			}
			var err error
			expected, err = mirrornativeAssetExpectedHoldsV3(mustBusinessID(id), preview)
			if err != nil {
				return nativeMirrorCheckV3("asset exact resource set", err)
			}
			for i := range expected {
				expected[i].State = funds.ReservationState(state)
			}
		case bankv2.MoveGoal:
			p, ok := g.nativeActions[op.text("preview_id")].(wireNativeGoalTransferPreview)
			if !ok {
				return businessInvalid("native paired resource original preview")
			}
			var err error
			expected, err = mirrorgoalTransferExpectedHoldsV3(mustBusinessID(id), request.Effect, p.LotUses)
			if err != nil {
				return nativeMirrorCheckV3("paired exact resource set", err)
			}
			for i := range expected {
				expected[i].State = funds.ReservationState(state)
			}
		default:
			return fmt.Errorf("%w: native resource family", evidence.ErrUnsupportedVersion)
		}
		want := map[string]wireNativeHold{}
		for _, h := range expected {
			key := nativeHoldKeyV3(h.ResourceKind, h.ResourceID, h.ResourceDetail)
			if _, ok := want[key]; ok {
				return businessInvalid("native expected duplicate resource")
			}
			want[key] = h
		}
		for _, row := range g.rows["native_holds"] {
			if row.text("operation_id") != id {
				continue
			}
			key := nativeHoldKeyV3(row.text("resource_kind"), row.text("resource_id"), nativeStringV3(row, "resource_detail"))
			h, ok := want[key]
			if !ok || h.Amount != row.money("amount_cents") || string(h.State) != row.text("state") {
				return businessInvalid("native full exact retained resource/terminal state")
			}
			delete(want, key)
		}
		if len(want) != 0 {
			return businessInvalid("native omitted exact original resource hold")
		}
		uses := map[string]bankv2.OriginUse{}
		for _, use := range request.Effect.OriginUses {
			if use.Bucket == "AVAILABLE" {
				uses[funds.LocationID(mustBusinessID(use.OriginID), mustBusinessID(use.AccountID)).String()] = use
			}
		}
		reservation, found := g.reservations[id]
		if (len(uses) > 0) != found {
			return businessInvalid("native exact AVAILABLE origin reservation existence")
		}
		if found {
			operation := funds.Spend
			switch request.Effect.Kind {
			case bankv2.Allocate:
				operation = funds.AllocateGoal
			case bankv2.Transfer:
				operation = funds.TransferInternal
			}
			if reservation.Operation != operation || len(reservation.Uses) != len(uses) || string(reservation.State) != state || (request.Effect.Kind == bankv2.Transfer) != (reservation.DestinationAccountID != nil) || reservation.DestinationAccountID != nil && reservation.DestinationAccountID.String() != request.Effect.DestinationAccountID {
				return businessInvalid("native origin reserve original operation/destination")
			}
			for _, u := range reservation.Uses {
				v, ok := uses[u.FragmentID.String()]
				if !ok || u.OriginID.String() != v.OriginID || u.AccountID.String() != v.AccountID || u.Amount != v.Amount {
					return businessInvalid("native source claim differs from original bank use")
				}
			}
		}
		returns := map[string]bankv2.ReturnUse{}
		for _, use := range request.Effect.ReturnUses {
			returns[keyParts(use.OperationKey, use.AccountID)] = use
		}
		for _, row := range g.rows["native_return_claims"] {
			if row.text("operation_id") != id {
				continue
			}
			key := keyParts(row.text("bank_key"), row.text("account_id"))
			use, ok := returns[key]
			source := g.row("native_principal_returns", row.text("bank_key"))
			if !ok || source == nil || source.text("position_id") != use.PositionID || row.money("amount_cents") != use.Amount || row.text("state") != state {
				return businessInvalid("native return claim exact immutable PR use/state")
			}
			delete(returns, key)
		}
		if len(returns) != 0 {
			return businessInvalid("native omitted original PR claim")
		}
	}
	for _, row := range g.rows["native_holds"] {
		op := g.row("native_operations", row.text("operation_id"))
		if op == nil {
			return businessInvalid("native orphan resource hold")
		}
		if row.text("state") != "RESERVED" {
			continue
		}
		kind, id, detail := row.text("resource_kind"), row.text("resource_id"), nativeStringV3(row, "resource_detail")
		n := row.money("amount_cents").Cents()
		addBusinessSum(holds, kind+"/"+id, n)
		switch kind {
		case "CASH":
			addBusinessSum(cash, id, n)
		case "INCOMING_CASH":
			addBusinessSum(incoming, id, n)
		case "GOAL":
			addBusinessSum(goal, id, n)
		case "ASSIGNED":
			addBusinessSum(assigned, id, n)
		case "POSITION":
			addBusinessSum(position, id, n)
		case "GOAL_LOT":
			var reference mirrorNativeLotReferenceV3
			if nativeCompactOriginalV3([]byte(detail), &reference) != nil {
				return businessInvalid("native exact resource lot reference schema")
			}
			key := keyParts(id, reference.AccountID, reference.SourceKind, reference.SourceID)
			if g.rows["native_goal_lots"][key] == nil {
				return businessInvalid("native scoped lot pin without original lot")
			}
			addBusinessSum(lot, key, n)
		case "INCOMING_GOAL", "LIABILITY":
		default:
			return fmt.Errorf("%w: native held resource kind", evidence.ErrUnsupportedVersion)
		}
	}
	locations, claimTotals, unownedClaims, consumed, ownedReturns := map[string]*big.Int{}, map[string]*big.Int{}, map[string]*big.Int{}, map[string]*big.Int{}, map[string]*big.Int{}
	for _, row := range g.rows["native_return_claims"] {
		op := g.row("native_operations", row.text("operation_id"))
		if op == nil || g.row("native_return_locations", row.text("bank_key"), row.text("account_id")) == nil {
			return businessInvalid("native orphan principal return claim")
		}
		key := keyParts(row.text("bank_key"), row.text("account_id"))
		n := row.money("amount_cents").Cents()
		if row.text("state") == "RESERVED" {
			addBusinessSum(claimTotals, key, n)
			if g.nativeRequests[op.text("id")].Effect.SourceGoalID == "" {
				addBusinessSum(unownedClaims, key, n)
			}
		}
		if row.text("state") == "COMMITTED" && (op.text("command_kind") == "PURCHASE_ASSET" || op.text("command_kind") == "PAY_RECURRING") {
			addBusinessSum(consumed, row.text("bank_key"), n)
		}
	}
	for key, row := range g.rows["native_goal_lots"] {
		if !businessSum(lot, key, row.money("reserved_cents").Cents()) {
			return businessInvalid("native exact live GOAL_LOT holds")
		}
		if row.text("source_kind") == "PRINCIPAL_RETURN" {
			addBusinessSum(ownedReturns, keyParts(row.text("source_id"), row.text("account_id")), row.money("available_cents").Cents())
		}
	}
	for _, row := range g.rows["native_return_locations"] {
		key := keyParts(row.text("bank_key"), row.text("account_id"))
		available := row.money("available_cents").Cents()
		if g.row("native_principal_returns", row.text("bank_key")) == nil || g.row("accounts", row.text("account_id")) == nil || row.money("reserved_cents").Cents() > available || !businessSum(claimTotals, key, row.money("reserved_cents").Cents()) {
			return businessInvalid("native exact principal return live locations/claims")
		}
		free := big.NewInt(available)
		free.Sub(free, nativeSumValueV3(ownedReturns, key))
		if free.Sign() < 0 || free.Cmp(nativeSumValueV3(unownedClaims, key)) < 0 {
			return businessInvalid("native goal-owned PR cannot fund unowned purchase/payment")
		}
		addBusinessSum(locations, row.text("bank_key"), available)
	}
	activePrincipal, losses := map[string]*big.Int{}, map[string]*big.Int{}
	for _, row := range g.rows["native_positions"] {
		id := row.text("id")
		state := g.row("native_position_states", id)
		if state == nil {
			return businessInvalid("native current position state missing")
		}
		if position[id] != nil && position[id].Cmp(big.NewInt(row.money("principal_cents").Cents())) > 0 {
			return businessInvalid("native whole position reserved twice")
		}
		if state.boolean("closed") && nativeSumValueV3(position, id).Sign() > 0 {
			return businessInvalid("native closed position still reserved")
		}
		if !state.boolean("closed") && row.optional("goal_id") != "" {
			addBusinessSum(activePrincipal, row.optional("goal_id"), row.money("principal_cents").Cents())
		}
	}
	for _, row := range g.rows["native_principal_returns"] {
		id := row.text("bank_key")
		receipt, ok := g.nativeReceipts[row.text("operation_id")]
		var returned bankv2.PrincipalReturn
		nativeOriginalV3(row, "original", &returned)
		if !ok || nativeHashFieldV3(row, "original", "original_hash") != nil || receipt.BankResult.PrincipalReturn == nil || !exactJSON(*receipt.BankResult.PrincipalReturn, returned) || returned.Role != "PRINCIPAL_RETURN" || returned.OperationKey != id || returned.PositionID != row.text("position_id") || returned.Amount != row.money("amount_cents") || !returned.OccurredAt.Equal(row.instant("occurred_at")) {
			return businessInvalid("native current PR requires exact actual closing bank receipt")
		}
		total := nativeSumValueV3(locations, id)
		total.Add(total, nativeSumValueV3(consumed, id))
		if total.Cmp(big.NewInt(returned.Amount.Cents())) != 0 {
			return businessInvalid("native returned principal conserves exact locations/consumption and never becomes income")
		}
		p := g.row("native_positions", returned.PositionID)
		state := g.row("native_position_states", returned.PositionID)
		if p == nil || state == nil || !state.boolean("closed") || state.optional("closing_operation_id") != row.text("operation_id") {
			return businessInvalid("native actual return closed position")
		}
		if goal := p.optional("goal_id"); goal != "" {
			loss, err := p.money("principal_cents").Sub(returned.Amount)
			if err != nil {
				return businessInvalid("native closed goal principal loss")
			}
			addBusinessSum(losses, goal, loss.Cents())
		}
	}
	for _, row := range g.rows["native_goal_balances"] {
		id := row.text("goal_id")
		if !businessSum(activePrincipal, id, row.money("principal_cents").Cents()) || !businessSum(losses, id, row.money("loss_cents").Cents()) || nativeSumValueV3(goal, id).Cmp(big.NewInt(row.money("cash_cents").Cents())) > 0 {
			return businessInvalid("native current owned principal/loss/goal pin arithmetic")
		}
	}
	for _, row := range g.rows["income_locations"] {
		if nativeSumValueV3(assigned, row.text("id")).Cmp(big.NewInt(row.money("assigned_cents").Cents())) > 0 {
			return businessInvalid("native assigned origin held twice")
		}
	}
	for _, account := range g.rows["accounts"] {
		id := account.text("id")
		balance := account.money("balance_cents").Cents()
		capacity := new(big.Int).Add(big.NewInt(balance), nativeSumValueV3(incoming, id))
		if capacity.Cmp(big.NewInt(9000000000000000)) > 0 {
			return businessInvalid("native incoming actual cash capacity")
		}
		left := big.NewInt(balance - account.money("goal_owned_cents").Cents())
		left.Sub(left, nativeSumValueV3(cash, id))
		right := new(big.Int)
		for _, l := range g.rows["income_locations"] {
			if l.text("account_id") == id {
				right.Add(right, big.NewInt(l.money("available_cents").Cents()))
				right.Add(right, big.NewInt(l.money("reserved_cents").Cents()))
			}
		}
		for _, reservation := range g.rows["native_income_reservations"] {
			if reservation.text("state") != "RESERVED" {
				continue
			}
			for _, claim := range g.rows["native_income_claims"] {
				if claim.text("action_id") == reservation.text("action_id") {
					location := g.row("income_locations", claim.text("fragment_id"))
					if location != nil && location.text("account_id") == id {
						left.Add(left, big.NewInt(claim.money("amount_cents").Cents()))
					}
				}
			}
		}
		for key, row := range g.rows["native_return_locations"] {
			if row.text("account_id") == id {
				right.Add(right, big.NewInt(row.money("available_cents").Cents()))
				right.Sub(right, nativeSumValueV3(ownedReturns, key))
				left.Add(left, nativeSumValueV3(unownedClaims, key))
			}
		}
		if left.Cmp(right) < 0 {
			return businessInvalid("native cash/slack cannot consume another source qualification/hold")
		}
	}
	return g.checkedRowsV3()
}
