package executionaudit

import (
	"encoding/json"
	"fmt"
	"regexp"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

var nativeBillNamespace = regexp.MustCompile(`^zygo_[a-z][a-z0-9_]{0,63}$`)

type nativeFrozenLedgerV3 struct {
	Namespace        string
	Income           funds.IncomeLedger
	Accounts         []wireNativeAccount
	Goals            []wireNativeGoalBalance
	GoalLots         []wireNativeGoalLot
	Holds            []wireNativeHold
	Positions        []wireNativePosition
	PrincipalReturns []wireNativePrincipalReturn
	ReturnLocations  []wireNativeReturnLocation
	ReturnClaims     []wireNativeReturnClaim
}

var errNativeFrozenLedgerV3 = businessInvalid("frozen native ledger invariant")

type nativeMirrorErrorV3 struct{ code string }

func (e nativeMirrorErrorV3) Error() string               { return e.code }
func nativeMirrorRejectV3(code string, cause error) error { return nativeMirrorErrorV3{code} }
func nativeMirrorCodeV3(err error) string {
	if e, ok := err.(nativeMirrorErrorV3); ok {
		return e.code
	}
	return ""
}
func nativeMirrorEncodeV3(value any) ([]byte, error) { return json.Marshal(value) }
func nativeMirrorCheckV3(name string, err error) error {
	if err != nil {
		return businessInvalid(fmt.Sprintf("native frozen %s deterministic replay", name))
	}
	return nil
}
