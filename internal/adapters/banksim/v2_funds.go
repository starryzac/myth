package banksim

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	bankv2 "zhiyu.local/zhiyu-go/internal/execution/bankv2"
)

type v2Hold struct {
	kind, id, detail string
	amount           int64
}
type v2GoalLot struct{ Account, Kind, Source string }

func lotDetail(account, kind, source string) string {
	b, _ := json.Marshal(v2GoalLot{account, kind, source})
	return string(b)
}
func v2Int(ctx context.Context, q querier, sql string, args ...any) (int64, error) {
	var n int64
	err := q.QueryRow(ctx, sql, args...).Scan(&n)
	return n, err
}

func validateV2Funds(ctx context.Context, tx pgx.Tx, r bankv2.Request) ([]v2Hold, string, error) {
	e := r.Effect
	holds := []v2Hold{}
	add := func(kind, id, detail string, n int64) {
		if n > 0 {
			holds = append(holds, v2Hold{kind, id, detail, n})
		}
	}
	for _, use := range e.CashUses {
		var balance, reserved, owned int64
		err := tx.QueryRow(ctx, `SELECT a.balance_cents,a.reserved_cents,coalesce((SELECT sum(balance_cents) FROM bank.goals WHERE namespace=a.namespace AND user_id=a.user_id AND account_id=a.account_id),0)::bigint FROM bank.accounts a WHERE namespace=$1 AND user_id=$2 AND account_id=$3`, r.Namespace, r.UserID, use.AccountID).Scan(&balance, &reserved, &owned)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, "CASH_ACCOUNT_NOT_FOUND", nil
		}
		if err != nil {
			return nil, "", err
		}
		if e.SourceGoalID == "" {
			if balance-reserved-owned < use.Amount.Cents() {
				return nil, "INSUFFICIENT_UNASSIGNED_CASH", nil
			}
			add("CASH", use.AccountID, "", use.Amount.Cents())
		}
	}
	if e.SourceGoalID != "" && (e.Kind == bankv2.Purchase || e.Kind == bankv2.MoveGoal) {
		var account string
		var amount int64
		err := tx.QueryRow(ctx, "SELECT account_id,balance_cents FROM bank.goals WHERE namespace=$1 AND user_id=$2 AND goal_id=$3", r.Namespace, r.UserID, e.SourceGoalID).Scan(&account, &amount)
		if errors.Is(err, pgx.ErrNoRows) || err == nil && account != e.CashUses[0].AccountID {
			return nil, "SOURCE_GOAL_NOT_FOUND_OR_ACCOUNT", nil
		}
		if err != nil {
			return nil, "", err
		}
		held, err := v2Int(ctx, tx, `SELECT coalesce(sum(amount_cents),0)::bigint FROM bank.v2_resource_holds WHERE namespace=$1 AND user_id=$2 AND resource_kind='GOAL' AND resource_id=$3 AND state='RESERVED'`, r.Namespace, r.UserID, e.SourceGoalID)
		if err != nil {
			return nil, "", err
		}
		if amount-held < e.Amount.Cents() {
			return nil, "INSUFFICIENT_GOAL_CASH", nil
		}
		add("GOAL", e.SourceGoalID, "", e.Amount.Cents())
	}
	for _, use := range e.OriginUses {
		var available, reserved, assigned, assignedReserved int64
		var occurred time.Time
		err := tx.QueryRow(ctx, `SELECT l.available_cents,l.reserved_cents,l.assigned_cents,l.assigned_reserved_cents,o.occurred_at FROM bank.income_locations l JOIN bank.origins o USING(namespace,user_id,origin_id) WHERE l.namespace=$1 AND l.user_id=$2 AND l.origin_id=$3 AND l.account_id=$4`, r.Namespace, r.UserID, use.OriginID, use.AccountID).Scan(&available, &reserved, &assigned, &assignedReserved, &occurred)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, "INCOME_ORIGIN_NOT_FOUND", nil
		}
		if err != nil {
			return nil, "", err
		}
		if occurred.After(r.RequestedAt) {
			return nil, "INCOME_NOT_OCCURRED", nil
		}
		if use.Bucket == "AVAILABLE" {
			if available-reserved < use.Amount.Cents() {
				return nil, "INCOME_ALREADY_QUALIFIED_OR_HELD", nil
			}
			add("INCOME", use.OriginID, use.AccountID, use.Amount.Cents())
		} else {
			if assigned-assignedReserved < use.Amount.Cents() {
				return nil, "ASSIGNED_ORIGIN_HELD", nil
			}
			add("ASSIGNED", use.OriginID, use.AccountID, use.Amount.Cents())
		}
		if e.SourceGoalID != "" {
			if code, err := v2GoalLotCapacity(ctx, tx, r, e.SourceGoalID, use.AccountID, "INCOME", use.OriginID, use.Amount.Cents()); code != "" || err != nil {
				return nil, code, err
			}
			add("GOAL_LOT", e.SourceGoalID, lotDetail(use.AccountID, "INCOME", use.OriginID), use.Amount.Cents())
		}
	}
	for _, use := range e.ReturnUses {
		var available, held int64
		var raw []byte
		err := tx.QueryRow(ctx, `SELECT l.available_cents,l.reserved_cents,p.original FROM bank.v2_return_locations l JOIN bank.v2_principal_returns p USING(namespace,user_id,operation_key) WHERE l.namespace=$1 AND l.user_id=$2 AND l.operation_key=$3 AND l.account_id=$4`, r.Namespace, r.UserID, use.OperationKey, use.AccountID).Scan(&available, &held, &raw)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, "PRINCIPAL_RETURN_NOT_SETTLED", nil
		}
		if err != nil {
			return nil, "", err
		}
		var principal bankv2.PrincipalReturn
		if json.Unmarshal(raw, &principal) != nil || principal.Role != "PRINCIPAL_RETURN" || principal.PositionID != use.PositionID {
			return nil, "PRINCIPAL_RETURN_IDENTITY", nil
		}
		if available-held < use.Amount.Cents() {
			return nil, "PRINCIPAL_RETURN_ALREADY_USED", nil
		}
		if e.SourceGoalID == "" {
			var unowned bool
			err = tx.QueryRow(ctx, `SELECT $5::numeric-coalesce((SELECT sum(available_cents::numeric) FROM bank.v2_goal_lots WHERE namespace=$1 AND user_id=$2 AND account_id=$4 AND source_kind='PRINCIPAL_RETURN' AND source_id=$3),0)-coalesce((SELECT sum(h.amount_cents::numeric) FROM bank.v2_resource_holds h JOIN bank.v2_operations o ON(o.namespace=h.namespace AND o.operation_key=h.operation_key) WHERE h.namespace=$1 AND h.user_id=$2 AND h.resource_kind='RETURN' AND h.resource_id=$3 AND h.resource_detail=$4 AND h.state='RESERVED' AND convert_from(o.request_original,'UTF8')::jsonb#>>'{effect,source_goal_id}'=''),0)>=$6::numeric`, r.Namespace, r.UserID, use.OperationKey, use.AccountID, available, use.Amount.Cents()).Scan(&unowned)
			if err != nil {
				return nil, "", err
			}
			if !unowned {
				return nil, "PRINCIPAL_RETURN_ALREADY_OWNED_OR_HELD", nil
			}
		}
		add("RETURN", use.OperationKey, use.AccountID, use.Amount.Cents())
		if e.SourceGoalID != "" {
			if code, err := v2GoalLotCapacity(ctx, tx, r, e.SourceGoalID, use.AccountID, "PRINCIPAL_RETURN", use.OperationKey, use.Amount.Cents()); code != "" || err != nil {
				return nil, code, err
			}
			add("GOAL_LOT", e.SourceGoalID, lotDetail(use.AccountID, "PRINCIPAL_RETURN", use.OperationKey), use.Amount.Cents())
		}
	}
	// Check exact origin/return references before their combined cash floor, so
	// repeated principal consumption retains its independent rejection reason.
	if e.SourceGoalID == "" {
		for _, use := range e.CashUses {
			var qualifiedUse, returnedUse int64
			for _, origin := range e.OriginUses {
				if origin.AccountID == use.AccountID && origin.Bucket == "AVAILABLE" {
					qualifiedUse += origin.Amount.Cents()
				}
			}
			for _, principal := range e.ReturnUses {
				if principal.AccountID == use.AccountID {
					returnedUse += principal.Amount.Cents()
				}
			}
			// Unclassified cash may consume only real slack beyond outstanding
			// income qualification, after all independently persisted holds.
			var covered bool
			err := tx.QueryRow(ctx, `SELECT a.balance_cents::numeric-a.reserved_cents::numeric-coalesce((SELECT sum(balance_cents::numeric) FROM bank.goals g WHERE g.namespace=a.namespace AND g.user_id=a.user_id AND g.account_id=a.account_id),0)-$4::numeric >=
coalesce((SELECT sum(available_cents::numeric-reserved_cents::numeric) FROM bank.income_locations l WHERE l.namespace=a.namespace AND l.user_id=a.user_id AND l.account_id=a.account_id),0)-$5::numeric+
coalesce((SELECT sum(available_cents::numeric) FROM bank.v2_return_locations l WHERE l.namespace=a.namespace AND l.user_id=a.user_id AND l.account_id=a.account_id),0)-
coalesce((SELECT sum(available_cents::numeric) FROM bank.v2_goal_lots l WHERE l.namespace=a.namespace AND l.user_id=a.user_id AND l.account_id=a.account_id AND l.source_kind='PRINCIPAL_RETURN'),0)-
coalesce((SELECT sum(h.amount_cents::numeric) FROM bank.v2_resource_holds h JOIN bank.v2_operations o ON(o.namespace=h.namespace AND o.operation_key=h.operation_key) WHERE h.namespace=a.namespace AND h.user_id=a.user_id AND h.resource_kind='RETURN' AND h.resource_detail=a.account_id AND h.state='RESERVED' AND convert_from(o.request_original,'UTF8')::jsonb#>>'{effect,source_goal_id}'=''),0)-$6::numeric
FROM bank.accounts a WHERE namespace=$1 AND user_id=$2 AND account_id=$3`, r.Namespace, r.UserID, use.AccountID, use.Amount.Cents(), qualifiedUse, returnedUse).Scan(&covered)
			if err != nil {
				return nil, "", err
			}
			if !covered {
				return nil, "QUALIFIED_CASH_FLOOR", nil
			}
		}
	}
	if e.Kind == bankv2.Transfer || e.Kind == bankv2.MoveGoal || e.Kind == bankv2.Redeem || e.Kind == bankv2.Mature {
		n := e.Amount.Cents()
		if e.Kind == bankv2.Redeem || e.Kind == bankv2.Mature {
			n = e.Net.Cents()
		}
		if e.Kind != bankv2.MoveGoal || e.CashUses[0].AccountID != e.DestinationAccountID {
			if code, err := v2IncomingCapacity(ctx, tx, r, e.DestinationAccountID, n); code != "" || err != nil {
				return nil, code, err
			}
			add("INCOMING_CASH", e.DestinationAccountID, "", n)
		}
	}
	if e.DestinationGoalID != "" {
		account := e.CashUses[0].AccountID
		if e.Kind == bankv2.MoveGoal {
			account = e.DestinationAccountID
		}
		// A signed request can register only a zero anchor for its receiving
		// identity. Source ownership and balances are never copied from app JSON.
		if _, err := tx.Exec(ctx, "INSERT INTO bank.goals(namespace,user_id,goal_id,account_id) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING", r.Namespace, r.UserID, e.DestinationGoalID, account); err != nil {
			return nil, "", err
		}
		var actual string
		if err := tx.QueryRow(ctx, "SELECT account_id FROM bank.goals WHERE namespace=$1 AND user_id=$2 AND goal_id=$3", r.Namespace, r.UserID, e.DestinationGoalID).Scan(&actual); err != nil {
			return nil, "", err
		}
		if actual != account {
			return nil, "DESTINATION_GOAL_ACCOUNT_CONFLICT", nil
		}
	}
	if e.Kind == bankv2.Redeem || e.Kind == bankv2.Mature {
		add("POSITION", e.PositionID, "", e.Amount.Cents())
	}
	if e.Kind == bankv2.Payment {
		var raw []byte
		var total, paid, held int64
		err := tx.QueryRow(ctx, `SELECT o.original,o.final_total_cents,s.paid_cents,s.reserved_cents FROM bank.v2_liability_originals o JOIN bank.v2_liability_state s USING(namespace,user_id,liability_id) WHERE o.namespace=$1 AND o.user_id=$2 AND o.liability_id=$3`, r.Namespace, r.UserID, e.Liability.ID).Scan(&raw, &total, &paid, &held)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, "LIABILITY_NOT_CONFIRMED", nil
		}
		if err != nil {
			return nil, "", err
		}
		var original bankv2.LiabilityReference
		if json.Unmarshal(raw, &original) != nil {
			return nil, "", bankv2.ErrInvalid
		}
		expected, _ := json.Marshal(e.Liability)
		current, _ := json.Marshal(original)
		// Each immutable occurrence may be paid in bounded installments. The
		// namespace/user lock serializes paid and held state; a partial payment
		// reserves only its amount and may never exceed the real remainder.
		if string(expected) != string(current) || e.Amount.IsZero() || total-paid-held < e.Amount.Cents() {
			return nil, "LIABILITY_EXACT_REMAINDER_CHANGED", nil
		}
		add("LIABILITY", e.Liability.ID, "", e.Amount.Cents())
	}
	return holds, "", nil
}

func v2GoalLotCapacity(ctx context.Context, tx pgx.Tx, r bankv2.Request, goal, account, kind, source string, n int64) (string, error) {
	amount, err := v2Int(ctx, tx, "SELECT available_cents-reserved_cents FROM bank.v2_goal_lots WHERE namespace=$1 AND user_id=$2 AND goal_id=$3 AND account_id=$4 AND source_kind=$5 AND source_id=$6", r.Namespace, r.UserID, goal, account, kind, source)
	if errors.Is(err, pgx.ErrNoRows) {
		return "GOAL_ORIGINAL_LOT_NOT_FOUND", nil
	}
	if err != nil {
		return "", err
	}
	if amount < n {
		return "GOAL_ORIGINAL_LOT_HELD", nil
	}
	return "", nil
}
func v2IncomingCapacity(ctx context.Context, tx pgx.Tx, r bankv2.Request, account string, n int64) (string, error) {
	var balance int64
	err := tx.QueryRow(ctx, "SELECT balance_cents FROM bank.accounts WHERE namespace=$1 AND user_id=$2 AND account_id=$3", r.Namespace, r.UserID, account).Scan(&balance)
	if errors.Is(err, pgx.ErrNoRows) {
		return "DESTINATION_ACCOUNT_NOT_FOUND", nil
	}
	if err != nil {
		return "", err
	}
	var capacity bool
	err = tx.QueryRow(ctx, `SELECT $4::numeric+coalesce((SELECT sum(amount_cents) FROM bank.v2_resource_holds WHERE namespace=$1 AND user_id=$2 AND resource_kind='INCOMING_CASH' AND resource_id=$3 AND state='RESERVED'),0)+coalesce((SELECT sum((request->>'amount_cents')::bigint) FROM bank.operations WHERE namespace=$1 AND user_id=$2 AND status='ACCEPTED' AND request->>'kind'='TRANSFER' AND request->>'destination_account_id'=$3),0)+$5::numeric<=9000000000000000`, r.Namespace, r.UserID, account, balance, n).Scan(&capacity)
	if err != nil {
		return "", err
	}
	if !capacity {
		return "DESTINATION_CAPACITY_HELD", nil
	}
	return "", nil
}

func reserveV2(ctx context.Context, tx pgx.Tx, r bankv2.Request, holds []v2Hold) error {
	for _, h := range holds {
		if _, err := tx.Exec(ctx, `INSERT INTO bank.v2_resource_holds(namespace,user_id,operation_key,resource_kind,resource_id,resource_detail,amount_cents) VALUES($1,$2,$3,$4,$5,$6,$7)`, r.Namespace, r.UserID, r.Key, h.kind, h.id, h.detail, h.amount); err != nil {
			return err
		}
		var sql string
		args := []any{r.Namespace, r.UserID, h.id, h.amount}
		switch h.kind {
		case "CASH":
			sql = "UPDATE bank.accounts SET reserved_cents=reserved_cents+$4 WHERE namespace=$1 AND user_id=$2 AND account_id=$3"
		case "INCOME":
			sql = "UPDATE bank.income_locations SET reserved_cents=reserved_cents+$4 WHERE namespace=$1 AND user_id=$2 AND origin_id=$3 AND account_id=$5"
			args = append(args, h.detail)
		case "ASSIGNED":
			sql = "UPDATE bank.income_locations SET assigned_reserved_cents=assigned_reserved_cents+$4 WHERE namespace=$1 AND user_id=$2 AND origin_id=$3 AND account_id=$5"
			args = append(args, h.detail)
		case "RETURN":
			sql = "UPDATE bank.v2_return_locations SET reserved_cents=reserved_cents+$4 WHERE namespace=$1 AND user_id=$2 AND operation_key=$3 AND account_id=$5"
			args = append(args, h.detail)
		case "POSITION":
			sql = "UPDATE bank.v2_position_state SET closing_key=$4 WHERE namespace=$1 AND user_id=$2 AND position_id=$3 AND closing_key IS NULL AND NOT closed"
			args[3] = r.Key
		case "LIABILITY":
			sql = "UPDATE bank.v2_liability_state SET reserved_cents=reserved_cents+$4 WHERE namespace=$1 AND user_id=$2 AND liability_id=$3"
		case "GOAL_LOT":
			var lot v2GoalLot
			if json.Unmarshal([]byte(h.detail), &lot) != nil {
				return bankv2.ErrInvalid
			}
			sql = "UPDATE bank.v2_goal_lots SET reserved_cents=reserved_cents+$4 WHERE namespace=$1 AND user_id=$2 AND goal_id=$3 AND account_id=$5 AND source_kind=$6 AND source_id=$7"
			args = append(args, lot.Account, lot.Kind, lot.Source)
		case "GOAL", "INCOMING_CASH", "INCOMING_GOAL":
			continue
		default:
			return bankv2.ErrInvalid
		}
		tag, err := tx.Exec(ctx, sql, args...)
		if err != nil {
			return err
		}
		if tag.RowsAffected() != 1 {
			return bankv2.ErrInvalid
		}
	}
	return nil
}
