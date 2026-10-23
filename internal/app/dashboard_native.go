package app

import (
	"context"
	"errors"
	"sort"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"zhiyu.local/zhiyu-go/internal/adapters/postgres"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

const nativeDisplayScope = "AUTHORITATIVE_LOCAL_NATIVE_SNAPSHOT_NOT_BANK_RECONCILED"

type NativeDashboardAccount struct {
	ID                 funds.ID    `json:"id"`
	Cash               funds.Money `json:"cash_cents"`
	GoalCash           funds.Money `json:"goal_cash_cents"`
	GoalPrincipal      funds.Money `json:"goal_principal_cents"`
	ReturningPrincipal funds.Money `json:"returning_principal_cents"`
}
type NativeDashboardGoal struct {
	ID        funds.ID    `json:"id"`
	AccountID funds.ID    `json:"account_id"`
	Cash      funds.Money `json:"cash_cents"`
	Principal funds.Money `json:"principal_cents"`
	Loss      funds.Money `json:"loss_cents"`
}
type NativeDashboardPosition struct {
	ID                   string      `json:"id"`
	ProductID            string      `json:"product_id"`
	AssetClass           string      `json:"asset_class"`
	TermsVersion         int64       `json:"terms_version"`
	TermDays             int         `json:"term_days"`
	OriginalPrincipal    funds.Money `json:"original_principal_cents"`
	OutstandingPrincipal funds.Money `json:"outstanding_principal_cents"`
	GoalID               *funds.ID   `json:"goal_id,omitempty"`
	ReturnAccountID      funds.ID    `json:"return_account_id"`
	PurchasedAt          time.Time   `json:"purchased_at"`
	MaturityAt           *time.Time  `json:"maturity_at,omitempty"`
	Closed               bool        `json:"closed"`
	ClosingOperationID   *funds.ID   `json:"closing_operation_id,omitempty"`
	OpeningHash          string      `json:"opening_hash"`
	SourceKind           string      `json:"source_kind"`
}
type NativeDashboardTotals struct {
	Cash          funds.Money `json:"cash_cents"`
	GoalCash      funds.Money `json:"goal_cash_cents"`
	GoalPrincipal funds.Money `json:"goal_principal_cents"`
	Principal     funds.Money `json:"principal_cents"`
	Assets        funds.Money `json:"assets_cents"`
	GoalLoss      funds.Money `json:"goal_loss_cents"`
}
type NativeDashboardView struct {
	Status                string                    `json:"status"`
	ProblemCode           string                    `json:"problem_code,omitempty"`
	VerificationScope     string                    `json:"verification_scope"`
	AsOf                  time.Time                 `json:"as_of"`
	FundsVersion          uint64                    `json:"funds_version"`
	SourceVersion         uint64                    `json:"source_version"`
	Policies              []TemplatePolicyView      `json:"policies"`
	CurrentPolicyCount    int                       `json:"current_policy_count"`
	ActivePolicyCount     int                       `json:"active_policy_count"`
	Operations            []NativeOperation         `json:"operations"`
	OperationCount        int                       `json:"operation_count"`
	PendingOperationCount int                       `json:"pending_operation_count"`
	HasMoreOperations     bool                      `json:"has_more_operations"`
	Accounts              []NativeDashboardAccount  `json:"accounts"`
	Goals                 []NativeDashboardGoal     `json:"goals"`
	Positions             []NativeDashboardPosition `json:"positions"`
	PositionCount         int                       `json:"position_count"`
	OpenPositionCount     int                       `json:"open_position_count"`
	HasMorePositions      bool                      `json:"has_more_positions"`
	Totals                *NativeDashboardTotals    `json:"totals,omitempty"`
	LocalSnapshotHash     string                    `json:"local_snapshot_hash"`
	BankAuthority         bool                      `json:"bank_authority"`
}

// Validate capacities BEFORE loading immutable originals and complete resource
// graphs. Names below are fixed code, never supplied by HTTP or a stored cursor.
func nativeDashboardCapacity(ctx context.Context, tx pgx.Tx, p Principal) error {
	for _, table := range []string{"accounts", "goals", "income_origins", "income_locations", "income_reservations", "income_claims", "native_income_reservations", "native_income_claims", "native_goal_lots", "native_holds", "native_positions", "native_principal_returns", "native_return_locations", "native_return_claims", "native_operations"} {
		limit := 10000
		if table == "accounts" || table == "goals" {
			limit = 100
		}
		var count int
		query := `SELECT count(*) FROM (SELECT 1 FROM ` + table + ` WHERE environment_id=$1 AND user_id=$2 LIMIT ` + strconv.Itoa(limit+1) + `) bounded`
		if err := tx.QueryRow(ctx, query, p.EnvironmentID.String(), p.UserID.String()).Scan(&count); err != nil {
			return err
		}
		if count > limit {
			return reject("CAPACITY_EXCEEDED", nil)
		}
	}
	return nil
}

func nativeDashboardOperations(ctx context.Context, tx pgx.Tx, p Principal, namespace string, limit int) ([]NativeOperation, error) {
	out := []NativeOperation{}
	rows, err := tx.Query(ctx, `SELECT id::text,group_id::text,child_number,status,bank_key,bank_observed_status,processing_health,receipt_id::text,version,created_at,updated_at,bank_request,bank_request_hash::text,effect_hash::text FROM native_operations WHERE environment_id=$1 AND user_id=$2 ORDER BY created_at DESC,id DESC LIMIT $3`, p.EnvironmentID.String(), p.UserID.String(), limit)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var op NativeOperation
		var id, requestHash, effectHash string
		var group *string
		var child *int
		var raw []byte
		if err = rows.Scan(&id, &group, &child, &op.State, &op.BankKey, &op.BankObservedStatus, &op.ProcessingHealth, &op.ReceiptID, &op.Version, &op.CreatedAt, &op.UpdatedAt, &raw, &requestHash, &effectHash); err != nil {
			return out, err
		}
		var request bankv2.Request
		if auditJSON(raw, &request) != nil {
			return out, reject("EVIDENCE_INVALID", nil)
		}
		h, e := request.Digest()
		eh, e2 := request.Effect.Digest()
		if e != nil || e2 != nil || h != requestHash || eh != effectHash || request.Namespace != namespace || request.UserID != p.UserID.String() || request.OperationID != id || request.Key != op.BankKey || (op.State == "SETTLED") != (op.ReceiptID != nil) {
			return out, reject("EVIDENCE_INVALID", nil)
		}
		op.ID, err = funds.ParseID(id)
		if err != nil {
			return out, err
		}
		if group != nil {
			op.GroupID, err = funds.ParseID(*group)
			if err != nil || child == nil || *child < 1 {
				return out, reject("EVIDENCE_INVALID", err)
			}
			op.ChildNumber = *child
		}
		op.Kind, op.Amount = request.Effect.Kind, request.Effect.Amount
		out = append(out, op)
	}
	return out, rows.Err()
}

func (s *Service) nativeDashboard(ctx context.Context, tx pgx.Tx, p Principal, at time.Time, goals []GoalView) (NativeDashboardView, error) {
	v := NativeDashboardView{Status: "CURRENT_LOCAL", VerificationScope: nativeDisplayScope, AsOf: at, Policies: []TemplatePolicyView{}, Operations: []NativeOperation{}, Accounts: []NativeDashboardAccount{}, Goals: []NativeDashboardGoal{}, Positions: []NativeDashboardPosition{}, Totals: &NativeDashboardTotals{}}
	if err := nativeDashboardCapacity(ctx, tx, p); err != nil {
		return v, err
	}
	if err := tx.QueryRow(ctx, `SELECT funds_version,source_version FROM users WHERE environment_id=$1 AND id=$2`, p.EnvironmentID.String(), p.UserID.String()).Scan(&v.FundsVersion, &v.SourceVersion); err != nil {
		return v, err
	}
	var err error
	v.Policies, err = s.loadTemplatePolicies(ctx, tx, p)
	if err != nil {
		return v, err
	}
	for i := range v.Policies {
		view := &v.Policies[i]
		view.EffectiveState, err = view.Version.EffectiveState(at, view.Timezone)
		if err != nil {
			return v, reject("EVIDENCE_INVALID", err)
		}
		v.CurrentPolicyCount++
		if view.EffectiveState == "ACTIVE" {
			v.ActivePolicyCount++
		}
	}
	ledger, err := s.Store.LoadNativeLedger(ctx, tx, p.EnvironmentID, p.UserID, at)
	if errors.Is(err, postgres.ErrLedgerConflict) {
		v.Status, v.ProblemCode, v.Totals = "UNAVAILABLE", "NATIVE_LEDGER_CONFLICT", nil
		return v, nil
	}
	if err != nil {
		return v, err
	}
	if ledger.Namespace != s.Namespace {
		return v, reject("EVIDENCE_INVALID", nil)
	}
	accounts := map[funds.ID]*NativeDashboardAccount{}
	for _, a := range ledger.Accounts {
		v.Accounts = append(v.Accounts, NativeDashboardAccount{ID: a.ID, Cash: a.Balance, GoalCash: a.GoalOwned})
		v.Totals.Cash, err = v.Totals.Cash.Add(a.Balance)
		if err != nil {
			return v, err
		}
		v.Totals.GoalCash, err = v.Totals.GoalCash.Add(a.GoalOwned)
		if err != nil {
			return v, err
		}
	}
	for i := range v.Accounts {
		accounts[v.Accounts[i].ID] = &v.Accounts[i]
	}
	owned := map[funds.ID]NativeDashboardGoal{}
	for _, g := range ledger.Goals {
		owned[g.GoalID] = NativeDashboardGoal{g.GoalID, g.AccountID, g.Cash, g.Principal, g.Loss}
		a := accounts[g.AccountID]
		if a == nil {
			return v, reject("EVIDENCE_INVALID", nil)
		}
		a.GoalPrincipal, err = a.GoalPrincipal.Add(g.Principal)
		if err != nil {
			return v, err
		}
		v.Totals.GoalPrincipal, err = v.Totals.GoalPrincipal.Add(g.Principal)
		if err != nil {
			return v, err
		}
		v.Totals.GoalLoss, err = v.Totals.GoalLoss.Add(g.Loss)
		if err != nil {
			return v, err
		}
	}
	for _, g := range goals {
		id, e := funds.ParseID(g.ID)
		account, e2 := funds.ParseID(g.AccountID)
		if e != nil || e2 != nil {
			return v, reject("EVIDENCE_INVALID", nil)
		}
		item, ok := owned[id]
		if !ok {
			item = NativeDashboardGoal{ID: id, AccountID: account}
		} else if item.AccountID != account {
			return v, reject("EVIDENCE_INVALID", nil)
		}
		v.Goals = append(v.Goals, item)
	}
	for _, position := range ledger.Positions {
		var opening bankv2.PositionOpening
		if auditJSON(position.Original, &opening) != nil {
			return v, reject("EVIDENCE_INVALID", nil)
		}
		h, e := opening.Digest()
		if e != nil || h != position.OriginalHash || opening.Namespace != s.Namespace || opening.UserID != p.UserID.String() || opening.PositionID != position.ID {
			return v, reject("EVIDENCE_INVALID", e)
		}
		item := NativeDashboardPosition{ID: position.ID, ProductID: opening.Terms.ProductID, AssetClass: opening.Terms.AssetClass, TermsVersion: opening.Terms.Version, TermDays: opening.Terms.TermDays, OriginalPrincipal: position.Principal, GoalID: position.GoalID, ReturnAccountID: position.ReturnAccountID, PurchasedAt: position.PurchasedAt, MaturityAt: position.MaturityAt, Closed: position.Closed, ClosingOperationID: position.ClosingOperationID, OpeningHash: position.OriginalHash, SourceKind: opening.SourceKind}
		if !position.Closed {
			v.OpenPositionCount++
			item.OutstandingPrincipal = position.Principal
			v.Totals.Principal, err = v.Totals.Principal.Add(position.Principal)
			if err != nil {
				return v, err
			}
			a := accounts[position.ReturnAccountID]
			if a == nil {
				return v, reject("EVIDENCE_INVALID", nil)
			}
			a.ReturningPrincipal, err = a.ReturningPrincipal.Add(position.Principal)
			if err != nil {
				return v, err
			}
		}
		v.Positions = append(v.Positions, item)
	}
	v.PositionCount = len(v.Positions)
	sort.Slice(v.Positions, func(i, j int) bool {
		if !v.Positions[i].PurchasedAt.Equal(v.Positions[j].PurchasedAt) {
			return v.Positions[i].PurchasedAt.After(v.Positions[j].PurchasedAt)
		}
		return v.Positions[i].ID > v.Positions[j].ID
	})
	if len(v.Positions) > 20 {
		v.HasMorePositions, v.Positions = true, v.Positions[:20]
	}
	v.Totals.Assets, err = v.Totals.Cash.Add(v.Totals.Principal)
	if err != nil {
		return v, err
	}
	if err = tx.QueryRow(ctx, `SELECT count(*),count(*) FILTER(WHERE status NOT IN('SETTLED','REJECTED','CANCELLED')) FROM native_operations WHERE environment_id=$1 AND user_id=$2`, p.EnvironmentID.String(), p.UserID.String()).Scan(&v.OperationCount, &v.PendingOperationCount); err != nil {
		return v, err
	}
	v.Operations, err = nativeDashboardOperations(ctx, tx, p, s.Namespace, 20)
	if err != nil {
		return v, err
	}
	v.HasMoreOperations = v.OperationCount > len(v.Operations)
	raw, err := encode(v)
	if err != nil {
		return v, err
	}
	v.LocalSnapshotHash = Hash(raw)
	return v, nil
}
