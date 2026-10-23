package banksim

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"time"

	"github.com/jackc/pgx/v5"
	bankv2 "zhiyu.local/zhiyu-go/internal/execution/bankv2"
)

func execV2(ctx context.Context, tx pgx.Tx, sql string, args ...any) error {
	tag, err := tx.Exec(ctx, sql, args...)
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return bankv2.ErrInvalid
	}
	return nil
}
func adjustCashV2(ctx context.Context, tx pgx.Tx, r bankv2.Request, account string, n int64) error {
	return execV2(ctx, tx, "UPDATE bank.accounts SET balance_cents=balance_cents+$4 WHERE namespace=$1 AND user_id=$2 AND account_id=$3", r.Namespace, r.UserID, account, n)
}
func adjustGoalV2(ctx context.Context, tx pgx.Tx, r bankv2.Request, goal string, n int64) error {
	return execV2(ctx, tx, "UPDATE bank.goals SET balance_cents=balance_cents+$4 WHERE namespace=$1 AND user_id=$2 AND goal_id=$3", r.Namespace, r.UserID, goal, n)
}
func addGoalLotV2(ctx context.Context, tx pgx.Tx, r bankv2.Request, goal, account, kind, source string, n int64) error {
	_, err := tx.Exec(ctx, `INSERT INTO bank.v2_goal_lots(namespace,user_id,goal_id,account_id,source_kind,source_id,available_cents) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(namespace,user_id,goal_id,account_id,source_kind,source_id) DO UPDATE SET available_cents=bank.v2_goal_lots.available_cents+EXCLUDED.available_cents`, r.Namespace, r.UserID, goal, account, kind, source, n)
	return err
}
func addReturnLocationV2(ctx context.Context, tx pgx.Tx, r bankv2.Request, key, account string, n int64) error {
	_, err := tx.Exec(ctx, `INSERT INTO bank.v2_return_locations(namespace,user_id,operation_key,account_id,available_cents) VALUES($1,$2,$3,$4,$5) ON CONFLICT(namespace,user_id,operation_key,account_id) DO UPDATE SET available_cents=bank.v2_return_locations.available_cents+EXCLUDED.available_cents`, r.Namespace, r.UserID, key, account, n)
	return err
}

func settleV2(ctx context.Context, tx pgx.Tx, r bankv2.Request, position *bankv2.PositionOpening, available time.Time) error {
	e := r.Effect
	// Release only the real bank holds consumed by this settled original.
	rows, err := tx.Query(ctx, "SELECT resource_kind,resource_id,resource_detail,amount_cents FROM bank.v2_resource_holds WHERE namespace=$1 AND operation_key=$2 AND state='RESERVED'", r.Namespace, r.Key)
	if err != nil {
		return err
	}
	holds := []v2Hold{}
	for rows.Next() {
		var h v2Hold
		if err = rows.Scan(&h.kind, &h.id, &h.detail, &h.amount); err != nil {
			rows.Close()
			return err
		}
		holds = append(holds, h)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for _, h := range holds {
		args := []any{r.Namespace, r.UserID, h.id, h.amount}
		var sql string
		switch h.kind {
		case "CASH":
			sql = "UPDATE bank.accounts SET reserved_cents=reserved_cents-$4 WHERE namespace=$1 AND user_id=$2 AND account_id=$3"
		case "INCOME":
			sql = "UPDATE bank.income_locations SET reserved_cents=reserved_cents-$4 WHERE namespace=$1 AND user_id=$2 AND origin_id=$3 AND account_id=$5"
			args = append(args, h.detail)
		case "ASSIGNED":
			sql = "UPDATE bank.income_locations SET assigned_reserved_cents=assigned_reserved_cents-$4 WHERE namespace=$1 AND user_id=$2 AND origin_id=$3 AND account_id=$5"
			args = append(args, h.detail)
		case "RETURN":
			sql = "UPDATE bank.v2_return_locations SET reserved_cents=reserved_cents-$4 WHERE namespace=$1 AND user_id=$2 AND operation_key=$3 AND account_id=$5"
			args = append(args, h.detail)
		case "POSITION":
			sql = "UPDATE bank.v2_position_state SET closed=true WHERE namespace=$1 AND user_id=$2 AND position_id=$3 AND closing_key=$4 AND NOT closed"
			args[3] = r.Key
		case "LIABILITY":
			sql = "UPDATE bank.v2_liability_state SET reserved_cents=reserved_cents-$4,paid_cents=paid_cents+$4 WHERE namespace=$1 AND user_id=$2 AND liability_id=$3"
		case "GOAL_LOT":
			var lot v2GoalLot
			if json.Unmarshal([]byte(h.detail), &lot) != nil {
				return bankv2.ErrInvalid
			}
			sql = "UPDATE bank.v2_goal_lots SET available_cents=available_cents-$4,reserved_cents=reserved_cents-$4 WHERE namespace=$1 AND user_id=$2 AND goal_id=$3 AND account_id=$5 AND source_kind=$6 AND source_id=$7"
			args = append(args, lot.Account, lot.Kind, lot.Source)
		case "GOAL", "INCOMING_CASH", "INCOMING_GOAL":
			continue
		default:
			return bankv2.ErrInvalid
		}
		if err = execV2(ctx, tx, sql, args...); err != nil {
			return err
		}
	}
	if e.Kind == bankv2.Transfer || e.Kind == bankv2.Payment || e.Kind == bankv2.Purchase {
		for _, use := range e.CashUses {
			if err = adjustCashV2(ctx, tx, r, use.AccountID, -use.Amount.Cents()); err != nil {
				return err
			}
		}
		if e.Kind == bankv2.Transfer {
			if err = adjustCashV2(ctx, tx, r, e.DestinationAccountID, e.Amount.Cents()); err != nil {
				return err
			}
		}
	}
	switch e.Kind {
	case bankv2.Allocate:
		if err = adjustGoalV2(ctx, tx, r, e.DestinationGoalID, e.Amount.Cents()); err != nil {
			return err
		}
	case bankv2.MoveGoal:
		if e.CashUses[0].AccountID != e.DestinationAccountID {
			if err = adjustCashV2(ctx, tx, r, e.CashUses[0].AccountID, -e.Amount.Cents()); err != nil {
				return err
			}
			if err = adjustCashV2(ctx, tx, r, e.DestinationAccountID, e.Amount.Cents()); err != nil {
				return err
			}
		}
		if err = adjustGoalV2(ctx, tx, r, e.SourceGoalID, -e.Amount.Cents()); err != nil {
			return err
		}
		if err = adjustGoalV2(ctx, tx, r, e.DestinationGoalID, e.Amount.Cents()); err != nil {
			return err
		}
	case bankv2.Purchase:
		if position == nil {
			return bankv2.ErrInvalid
		}
		h, _ := position.Digest()
		raw, _ := json.Marshal(position)
		var maturity any
		if !position.MaturityAt.IsZero() {
			maturity = position.MaturityAt
		}
		if err = execV2(ctx, tx, `INSERT INTO bank.v2_position_openings(namespace,user_id,position_id,opening_key,original,original_hash,principal_cents,product_id,terms_version,goal_id,return_account_id,purchased_at,maturity_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`, r.Namespace, r.UserID, position.PositionID, r.Key, raw, h, position.Principal.Cents(), position.Terms.ProductID, position.Terms.Version, position.GoalID, position.ReturnAccountID, position.PurchasedAt, maturity); err != nil {
			return err
		}
		if err = execV2(ctx, tx, "INSERT INTO bank.v2_position_state(namespace,user_id,position_id) VALUES($1,$2,$3)", r.Namespace, r.UserID, position.PositionID); err != nil {
			return err
		}
		if e.SourceGoalID != "" {
			if err = adjustGoalV2(ctx, tx, r, e.SourceGoalID, -e.Amount.Cents()); err != nil {
				return err
			}
			_, err = tx.Exec(ctx, `INSERT INTO bank.v2_goal_principal(namespace,user_id,goal_id,principal_cents) VALUES($1,$2,$3,$4) ON CONFLICT(namespace,user_id,goal_id) DO UPDATE SET principal_cents=bank.v2_goal_principal.principal_cents+EXCLUDED.principal_cents`, r.Namespace, r.UserID, e.SourceGoalID, e.Amount.Cents())
			if err != nil {
				return err
			}
		}
	case bankv2.Redeem, bankv2.Mature:
		if err = adjustCashV2(ctx, tx, r, e.ReturnAccountID, e.Net.Cents()); err != nil {
			return err
		}
		if e.SourceGoalID != "" {
			if err = adjustGoalV2(ctx, tx, r, e.SourceGoalID, e.Net.Cents()); err != nil {
				return err
			}
			cost, _ := e.Fee.Add(e.Loss)
			if err = execV2(ctx, tx, "UPDATE bank.v2_goal_principal SET principal_cents=principal_cents-$4,loss_cents=loss_cents+$5 WHERE namespace=$1 AND user_id=$2 AND goal_id=$3", r.Namespace, r.UserID, e.SourceGoalID, e.Amount.Cents(), cost.Cents()); err != nil {
				return err
			}
		}
		h, _ := r.Digest()
		principal := bankv2.PrincipalReturn{Role: "PRINCIPAL_RETURN", PositionID: e.PositionID, PositionOpeningHash: e.PositionOpeningHash, OperationKey: r.Key, RequestHash: h, AccountID: e.ReturnAccountID, GoalID: e.SourceGoalID, Amount: e.Net, OccurredAt: available.UTC()}
		raw, _ := json.Marshal(principal)
		sum := sha256.Sum256(raw)
		if err = execV2(ctx, tx, "INSERT INTO bank.v2_principal_returns(namespace,user_id,operation_key,position_id,original,original_hash) VALUES($1,$2,$3,$4,$5,$6)", r.Namespace, r.UserID, r.Key, e.PositionID, raw, hex.EncodeToString(sum[:])); err != nil {
			return err
		}
		if err = addReturnLocationV2(ctx, tx, r, r.Key, e.ReturnAccountID, e.Net.Cents()); err != nil {
			return err
		}
		if e.SourceGoalID != "" {
			if err = addGoalLotV2(ctx, tx, r, e.SourceGoalID, e.ReturnAccountID, "PRINCIPAL_RETURN", r.Key, e.Net.Cents()); err != nil {
				return err
			}
		}
	}
	for _, use := range e.OriginUses {
		args := []any{r.Namespace, r.UserID, use.OriginID, use.AccountID, use.Amount.Cents()}
		var sql string
		switch e.Kind {
		case bankv2.Allocate, bankv2.Purchase:
			if use.Bucket == "AVAILABLE" {
				sql = "UPDATE bank.income_locations SET available_cents=available_cents-$5,assigned_cents=assigned_cents+$5 WHERE namespace=$1 AND user_id=$2 AND origin_id=$3 AND account_id=$4"
			}
		case bankv2.Payment:
			sql = "UPDATE bank.income_locations SET available_cents=available_cents-$5,spent_cents=spent_cents+$5 WHERE namespace=$1 AND user_id=$2 AND origin_id=$3 AND account_id=$4"
		case bankv2.Transfer, bankv2.MoveGoal:
			if e.CashUses[0].AccountID != e.DestinationAccountID {
				column := "available_cents"
				if use.Bucket == "ASSIGNED_GOAL" {
					column = "assigned_cents"
				}
				sql = "UPDATE bank.income_locations SET " + column + "=" + column + "-$5 WHERE namespace=$1 AND user_id=$2 AND origin_id=$3 AND account_id=$4"
				if err = execV2(ctx, tx, sql, args...); err != nil {
					return err
				}
				sql = ""
				insert := "INSERT INTO bank.income_locations(namespace,user_id,origin_id,account_id,available_cents) VALUES($1,$2,$3,$4,$5) ON CONFLICT(namespace,user_id,origin_id,account_id) DO UPDATE SET available_cents=bank.income_locations.available_cents+EXCLUDED.available_cents"
				if column == "assigned_cents" {
					insert = "INSERT INTO bank.income_locations(namespace,user_id,origin_id,account_id,available_cents,assigned_cents) VALUES($1,$2,$3,$4,0,$5) ON CONFLICT(namespace,user_id,origin_id,account_id) DO UPDATE SET assigned_cents=bank.income_locations.assigned_cents+EXCLUDED.assigned_cents"
				}
				_, err = tx.Exec(ctx, insert, r.Namespace, r.UserID, use.OriginID, e.DestinationAccountID, use.Amount.Cents())
				if err != nil {
					return err
				}
			}
		}
		if sql != "" {
			if err = execV2(ctx, tx, sql, args...); err != nil {
				return err
			}
		}
		if e.Kind == bankv2.Allocate {
			if err = addGoalLotV2(ctx, tx, r, e.DestinationGoalID, use.AccountID, "INCOME", use.OriginID, use.Amount.Cents()); err != nil {
				return err
			}
		}
		if e.Kind == bankv2.MoveGoal {
			if err = addGoalLotV2(ctx, tx, r, e.DestinationGoalID, e.DestinationAccountID, "INCOME", use.OriginID, use.Amount.Cents()); err != nil {
				return err
			}
		}
	}
	for _, use := range e.ReturnUses {
		consume := e.Kind == bankv2.Purchase || e.Kind == bankv2.Payment || e.Kind == bankv2.Transfer || e.Kind == bankv2.MoveGoal && use.AccountID != e.DestinationAccountID
		if consume {
			if err = execV2(ctx, tx, "UPDATE bank.v2_return_locations SET available_cents=available_cents-$5 WHERE namespace=$1 AND user_id=$2 AND operation_key=$3 AND account_id=$4", r.Namespace, r.UserID, use.OperationKey, use.AccountID, use.Amount.Cents()); err != nil {
				return err
			}
		}
		if e.Kind == bankv2.Transfer || e.Kind == bankv2.MoveGoal && use.AccountID != e.DestinationAccountID {
			if err = addReturnLocationV2(ctx, tx, r, use.OperationKey, e.DestinationAccountID, use.Amount.Cents()); err != nil {
				return err
			}
		}
		if e.Kind == bankv2.Allocate {
			if err = addGoalLotV2(ctx, tx, r, e.DestinationGoalID, use.AccountID, "PRINCIPAL_RETURN", use.OperationKey, use.Amount.Cents()); err != nil {
				return err
			}
		}
		if e.Kind == bankv2.MoveGoal {
			if err = addGoalLotV2(ctx, tx, r, e.DestinationGoalID, e.DestinationAccountID, "PRINCIPAL_RETURN", use.OperationKey, use.Amount.Cents()); err != nil {
				return err
			}
		}
	}
	postings, err := bankv2.ExpectedPostings(r, available)
	if err != nil {
		return err
	}
	for _, p := range postings {
		if err = execV2(ctx, tx, "INSERT INTO bank.v2_postings(id,namespace,operation_key,dimension,ledger_id,direction,amount_cents,occurred_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)", p.ID, r.Namespace, r.Key, p.Dimension, p.LedgerID, p.Direction, p.Amount.Cents(), available); err != nil {
			return err
		}
	}
	if _, err = tx.Exec(ctx, "UPDATE bank.v2_resource_holds SET state='COMMITTED' WHERE namespace=$1 AND operation_key=$2 AND state='RESERVED'", r.Namespace, r.Key); err != nil {
		return err
	}
	return execV2(ctx, tx, "UPDATE bank.v2_operations SET status='SETTLED',version=version+1,settled_at=available_at WHERE namespace=$1 AND user_id=$2 AND operation_key=$3 AND status='ACCEPTED'", r.Namespace, r.UserID, r.Key)
}
