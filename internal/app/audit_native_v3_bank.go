package app

import (
	"context"
	"sort"
	"time"

	executionaudit "zhiyu.local/zhiyu-go/internal/execution/audit"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	execution "zhiyu.local/zhiyu-go/internal/execution/domain"
)

// Bank reads happen before opening the application RR transaction. This reader
// has no source-credit writer, no Submit/Settle call, and no DB credentials for
// the independent bank. Missing/failing sources survive as explicit problems.
func (s *Service) captureIndependentNativeAuditBankV3(ctx context.Context, p Principal) executionaudit.NativeIndependentBankV3 {
	out := executionaudit.NativeIndependentBankV3{Protocol: executionaudit.NativeBankProtocolV3, Complete: true, Problems: []string{}, Legacy: []execution.BankResult{}, Native: []bankv2.Result{}, ObservationStartedAt: time.Now().UTC().Truncate(time.Microsecond)}
	finish := func() executionaudit.NativeIndependentBankV3 {
		out.ObservationFinishedAt = time.Now().UTC().Truncate(time.Microsecond)
		return out
	}
	problem := func(code string) { out.Complete = false; out.Problems = append(out.Problems, code) }
	// Only public, operator-configured profiles are captured here. The database
	// cannot promote its own claimed issuer key into an independent trust root.
	if s.ForecastHistorySources != nil {
		ids := []string{}
		for id, source := range s.ForecastHistorySources.Sources {
			if source.Authority.UserID == p.UserID {
				ids = append(ids, id)
			}
		}
		sort.Strings(ids)
		for _, id := range ids {
			source, err := s.historySource(id, p, false)
			if err != nil {
				problem("INDEPENDENT_HISTORY_ISSUER_PROFILE_INVALID")
				continue
			}
			a := source.Authority
			out.HistoryAuthorities = append(out.HistoryAuthorities, executionaudit.NativeConfiguredHistoryAuthorityV3{SourceID: id, Authority: executionaudit.NativeHistoryAuthorityV3{IssuerID: a.IssuerID, Namespace: a.Namespace, UserID: a.UserID, AccountIDs: a.AccountIDs, PublicKeyHex: a.PublicKeyHex}})
		}
	}
	sources := s.NativeIncomeSources
	if sources == nil || sources.Credit == nil || sources.Legacy == nil || sources.Native == nil {
		problem("INDEPENDENT_BANK_SOURCES_NOT_CONFIGURED")
		return finish()
	}
	var err error
	out.Credit, err = sources.Credit.CurrentState(ctx, s.Namespace, p.UserID.String())
	if err != nil {
		problem("INDEPENDENT_CREDIT_READ_UNAVAILABLE")
		return finish()
	}
	state := out.Credit
	if state.Validate() != nil || state.Namespace != s.Namespace || state.UserID != p.UserID.String() {
		problem("INDEPENDENT_CREDIT_SCOPE_INVALID")
		return finish()
	}
	for after := int64(0); after < state.V1OperationWatermark; {
		page, e := sources.Legacy.Inventory(ctx, s.Namespace, p.UserID.String(), after, state.V1OperationWatermark, 100)
		if e != nil {
			problem("INDEPENDENT_LEGACY_INVENTORY_UNAVAILABLE")
			return finish()
		}
		if page.Namespace != s.Namespace || page.UserID != p.UserID.String() || page.Watermark != state.V1OperationWatermark || len(page.Operations) == 0 || len(page.Operations) > 100 || page.NextAfter <= after || page.NextAfter > page.Watermark {
			problem("INDEPENDENT_LEGACY_INVENTORY_BOUNDS")
			return finish()
		}
		last := after
		for _, result := range page.Operations {
			if result.Status == execution.BankNotFound || result.Namespace != s.Namespace || result.UserID != p.UserID.String() || result.Sequence <= last || result.Sequence > page.Watermark || execution.VerifyBankResult(result.Request, result) != nil {
				problem("INDEPENDENT_LEGACY_OPERATION_INVALID")
				return finish()
			}
			out.Legacy = append(out.Legacy, result)
			last = result.Sequence
		}
		if last != page.NextAfter || !page.HasMore && last != page.Watermark || page.HasMore && len(page.Operations) != 100 || len(out.Legacy) > 1000 {
			problem("INDEPENDENT_LEGACY_INVENTORY_CAPACITY")
			return finish()
		}
		after = last
	}
	for after := int64(0); after < state.V2OperationWatermark; {
		page, e := sources.Native.Inventory(ctx, s.Namespace, p.UserID.String(), after, state.V2OperationWatermark, 100)
		if e != nil {
			problem("INDEPENDENT_NATIVE_INVENTORY_UNAVAILABLE")
			return finish()
		}
		if page.Protocol != bankv2.Protocol || page.Namespace != s.Namespace || page.UserID != p.UserID.String() || page.Watermark != state.V2OperationWatermark || len(page.Operations) == 0 || len(page.Operations) > 100 || page.NextAfter <= after || page.NextAfter > page.Watermark {
			problem("INDEPENDENT_NATIVE_INVENTORY_BOUNDS")
			return finish()
		}
		last := after
		for _, result := range page.Operations {
			if result.Request == nil || result.Status == bankv2.NotFound || result.Namespace != s.Namespace || result.UserID != p.UserID.String() || result.Sequence <= last || result.Sequence > page.Watermark || bankv2.VerifyResult(*result.Request, result) != nil {
				problem("INDEPENDENT_NATIVE_OPERATION_INVALID")
				return finish()
			}
			out.Native = append(out.Native, result)
			last = result.Sequence
		}
		if last != page.NextAfter || !page.HasMore && last != page.Watermark || page.HasMore && len(page.Operations) != 100 || len(out.Native) > 1000 {
			problem("INDEPENDENT_NATIVE_INVENTORY_CAPACITY")
			return finish()
		}
		after = last
	}
	out.Stock, err = sources.Native.CurrentState(ctx, s.Namespace, p.UserID.String())
	if err != nil {
		problem("INDEPENDENT_NATIVE_STOCK_UNAVAILABLE")
		return finish()
	}
	if out.Stock.Protocol != bankv2.Protocol || out.Stock.Namespace != s.Namespace || out.Stock.UserID != p.UserID.String() || out.Stock.OperationWatermark != state.V2OperationWatermark {
		problem("INDEPENDENT_BANK_CHANGED_DURING_READ")
		return finish()
	}
	liabilities, ok := s.NativeBank.(interface {
		Liabilities(context.Context, string, string) (bankv2.LiabilitySnapshot, error)
	})
	if !ok {
		problem("INDEPENDENT_LIABILITY_SOURCE_NOT_CONFIGURED")
		return finish()
	}
	out.Liabilities, err = liabilities.Liabilities(ctx, s.Namespace, p.UserID.String())
	if err != nil {
		problem("INDEPENDENT_LIABILITIES_UNAVAILABLE")
		return finish()
	}
	if out.Liabilities.Validate() != nil || out.Liabilities.Namespace != s.Namespace || out.Liabilities.UserID != p.UserID.String() {
		problem("INDEPENDENT_LIABILITY_SCOPE_INVALID")
	}
	return finish()
}
