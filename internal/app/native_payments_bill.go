package app

import (
	"time"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
)

func nativePaymentPreviewAmount(preview NativePaymentPreview, now time.Time) (funds.Money, error) {
	if preview.Liability.Original.Kind == "BILL" {
		if preview.BillProof == nil || preview.AdoptionProof != nil {
			return funds.Money{}, reject("BILL_ORIGINAL_REQUIRED", nil)
		}
		return nativeBillPaymentRuleAmount(preview.Policy.Version, preview.Debt, preview.Liability, *preview.BillProof, preview.Request.Amount, now, preview.Timezone)
	}
	if preview.BillProof != nil {
		return funds.Money{}, reject("EVIDENCE_INVALID", nil)
	}
	if preview.AdoptionProof != nil {
		return nativeAdoptedPaymentRuleAmount(preview.Policy.Version, preview.Debt, preview.Liability, *preview.AdoptionProof, preview.Request.Amount, now, preview.Timezone)
	}
	return nativePaymentRuleAmount(preview.Policy.Version, preview.Debt, preview.Liability, preview.Request.Amount, now, preview.Timezone)
}

func nativePaymentPreviewAfter(input planning.ForecastInput, preview NativePaymentPreview, amount funds.Money) (planning.ForecastInput, error) {
	if preview.Liability.Original.Kind == "BILL" {
		if preview.BillProof == nil || preview.AdoptionProof != nil {
			return planning.ForecastInput{}, reject("BILL_ORIGINAL_REQUIRED", nil)
		}
		return nativeBillPaymentForecastAfter(input, preview.Request.SourceAccountID, preview.Debt, preview.Liability, *preview.BillProof, amount)
	}
	if preview.BillProof != nil {
		return planning.ForecastInput{}, reject("EVIDENCE_INVALID", nil)
	}
	if preview.AdoptionProof != nil {
		return nativeAdoptedPaymentForecastAfter(input, preview.Request.SourceAccountID, preview.Debt, preview.Liability, *preview.AdoptionProof, amount)
	}
	return nativePaymentForecastAfter(input, preview.Request.SourceAccountID, preview.Debt, preview.Liability, amount)
}

func nativePaymentCurrentBillProof(snapshot templateSnapshot, preview NativePaymentPreview, liability bankv2.LiabilityState) (*NativeBillProof, error) {
	if liability.Original.Kind != "BILL" {
		if preview.BillProof != nil {
			return nil, reject("EVIDENCE_INVALID", nil)
		}
		return nil, nil
	}
	var debt *planning.ForecastObligation
	for i := range snapshot.Retained {
		if snapshot.Retained[i].ID == preview.Debt.ID {
			if debt != nil {
				return nil, reject("EVIDENCE_INVALID", nil)
			}
			debt = &snapshot.Retained[i]
		}
	}
	if debt == nil {
		return nil, reject("PAYMENT_DEBT_NOT_ADOPTED", nil)
	}
	proof, e := nativeBillProofForDebt(snapshot, *debt)
	if e != nil {
		return nil, e
	}
	if preview.BillProof != nil {
		a, _ := encode(proof)
		b, _ := encode(*preview.BillProof)
		if !templateSameBytes(a, b) {
			return nil, reject("SOURCE_CHANGED", nil)
		}
	}
	return &proof, nil
}

func nativePaymentCurrentAdoptionProof(snapshot templateSnapshot, preview NativePaymentPreview, liability bankv2.LiabilityState) (*NativePaymentAdoptionProof, error) {
	if preview.AdoptionProof == nil {
		// Do not add evidence or change the bytes of a retained older preview.
		return nil, nil
	}
	if liability.Original.Kind != "OCCURRENCE" || preview.BillProof != nil {
		return nil, reject("EVIDENCE_INVALID", nil)
	}
	var debt *planning.ForecastObligation
	for i := range snapshot.Retained {
		if snapshot.Retained[i].ID == preview.Debt.ID {
			if debt != nil {
				return nil, reject("EVIDENCE_INVALID", nil)
			}
			debt = &snapshot.Retained[i]
		}
	}
	if debt == nil {
		return nil, reject("PAYMENT_DEBT_NOT_ADOPTED", nil)
	}
	proof, err := nativePaymentAdoptionProofForDebt(snapshot, *debt)
	if err != nil {
		return nil, err
	}
	a, _ := encode(proof)
	b, _ := encode(*preview.AdoptionProof)
	if !templateSameBytes(a, b) {
		return nil, reject("SOURCE_CHANGED", nil)
	}
	return &proof, nil
}
