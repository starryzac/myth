package app

import (
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

type nativeIncomeDisposition struct {
	// Bank v2 retains purchased AVAILABLE qualification in its ASSIGNED
	// bucket even for an unowned position. The application consumes that
	// qualification permanently as Spent. Neither bucket is new income.
	PurchasedAssigned funds.Money
	Spent             funds.Money
}

// Call only after verifyIncomeOperations has matched every bank original to
// its complete local SETTLED receipt, journal and exact posting legs. This is
// a projection between two frozen bucket vocabularies, never a bank mutation
// or permission to restore qualification after redemption/principal return.
func verifiedNativeIncomeDispositions(capture nativeIncomeCapture) (map[string]nativeIncomeDisposition, error) {
	out := map[string]nativeIncomeDisposition{}
	for _, result := range capture.Native {
		if result.Status != bankv2.Settled {
			continue
		}
		request := result.Request
		if request == nil || request.Namespace != capture.State.Namespace || request.UserID != capture.State.UserID || bankv2.VerifyResult(*request, result) != nil {
			return nil, reject("INCOME_BANK_OPERATION_UNRECONCILED", nil)
		}
		for _, use := range request.Effect.OriginUses {
			purchase := request.Effect.Kind == bankv2.Purchase && request.Effect.SourceGoalID == ""
			payment := request.Effect.Kind == bankv2.Payment
			if !purchase && !payment {
				continue
			}
			if use.Bucket != "AVAILABLE" {
				return nil, reject("INCOME_BANK_SNAPSHOT_INVALID", nil)
			}
			key := use.OriginID + "/" + use.AccountID
			d := out[key]
			var err error
			d.Spent, err = d.Spent.Add(use.Amount)
			if err != nil {
				return nil, reject("INCOME_BANK_SNAPSHOT_INVALID", err)
			}
			if purchase {
				d.PurchasedAssigned, err = d.PurchasedAssigned.Add(use.Amount)
				if err != nil {
					return nil, reject("INCOME_BANK_SNAPSHOT_INVALID", err)
				}
			}
			out[key] = d
		}
	}
	return out, nil
}
