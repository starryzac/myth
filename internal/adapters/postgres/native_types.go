package postgres

import (
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// NativeLedger is a complete scoped storage snapshot. Income contains BOTH
// original and native AVAILABLE claims; the remaining resources never become
// LegacyReserved. Application owns authentication, the user lock and T1/T2/T3.
// Raw bank originals are retained byte-for-byte, with their owning digest.
type NativeLedger struct {
	Namespace        string
	Income           funds.IncomeLedger
	Accounts         []NativeAccount
	Goals            []NativeGoalBalance
	GoalLots         []NativeGoalLot
	Holds            []NativeHold
	Positions        []NativePosition
	PrincipalReturns []NativePrincipalReturn
	ReturnLocations  []NativeReturnLocation
	ReturnClaims     []NativeReturnClaim
}

type NativeAccount struct {
	ID         funds.ID
	Balance    funds.Money
	GoalOwned  funds.Money // Cash only; asset principal is never account cash.
	ObservedAt time.Time
}

type NativeGoalBalance struct {
	GoalID, AccountID     funds.ID
	Cash, Principal, Loss funds.Money
}

// GoalLot retains the original source identity while its cash is owned by a
// goal. Available includes Reserved, matching the independent bank v2 model.
type NativeGoalLot struct {
	GoalID, AccountID    funds.ID
	SourceKind, SourceID string // INCOME UUID or PRINCIPAL_RETURN original bank key.
	Available, Reserved  funds.Money
}

// ResourceDetail is part of the immutable identity. GOAL_LOT detail is the
// compact JSON of NativeLotReference, not an unverified display label.
type NativeHold struct {
	OperationID                              funds.ID
	ResourceKind, ResourceID, ResourceDetail string
	Amount                                   funds.Money
	State                                    funds.ReservationState
}

type NativeLotReference struct {
	AccountID  string `json:"account_id"`
	SourceKind string `json:"source_kind"`
	SourceID   string `json:"source_id"`
}

type NativePosition struct {
	ID, BankKey        string
	OpeningOperationID *funds.ID // nil only for explicitly witnessed historical import.
	Original           []byte    // bankv2.PositionOpening, complete original bytes.
	OriginalHash       string
	Principal          funds.Money
	GoalID             *funds.ID
	ReturnAccountID    funds.ID
	PurchasedAt        time.Time
	MaturityAt         *time.Time
	ClosingOperationID *funds.ID
	Closed             bool
}

type NativePrincipalReturn struct {
	BankKey      string
	OperationID  funds.ID
	PositionID   string
	Original     []byte // bankv2.PrincipalReturn; role fixed PRINCIPAL_RETURN.
	OriginalHash string // naked SHA256 of these exact bytes.
	Amount       funds.Money
	OccurredAt   time.Time
}

type NativeReturnLocation struct {
	BankKey             string
	AccountID           funds.ID
	Available, Reserved funds.Money // Available includes Reserved, never income.
}

type NativeReturnClaim struct {
	OperationID funds.ID
	BankKey     string
	AccountID   funds.ID
	Amount      funds.Money
	State       funds.ReservationState
}
