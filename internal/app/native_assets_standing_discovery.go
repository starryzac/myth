package app

import (
	"context"

	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

// Only a retained, independently verified MATURE settlement can supply a
// reinvestment occurrence. A maturity schedule/clock or uncredited position is
// never principal-return cash and never qualifies as an income event.
func (s *Service) DiscoverStandingMaturityReturns(ctx context.Context) (int, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return 0, err
	}
	defer rollback(ctx, tx)
	env, err := s.environment(ctx, tx, false)
	if err != nil {
		return 0, err
	}
	rows, err := tx.Query(ctx, `SELECT g.user_id::text,g.id::text,o.id::text,o.bank_request,o.bank_request_hash,o.permit_signature,r.original,r.original_hash,p.original,p.original_hash FROM native_grants g JOIN native_operations o ON(o.environment_id=g.environment_id AND o.user_id=g.user_id AND o.command_kind='MATURE_ASSET' AND o.status='SETTLED') JOIN native_receipts r ON(r.environment_id=o.environment_id AND r.user_id=o.user_id AND r.operation_id=o.id AND r.id=o.receipt_id) JOIN native_principal_returns p ON(p.environment_id=o.environment_id AND p.user_id=o.user_id AND p.bank_key=o.bank_key) WHERE g.environment_id=$1 AND g.family='MATURITY_REINVEST' AND g.effective_from<=$2 AND g.expires_at>$2 AND NOT EXISTS(SELECT 1 FROM native_occurrences c WHERE c.environment_id=g.environment_id AND c.user_id=g.user_id AND c.grant_id=g.id AND c.grant_version=g.version AND c.family=g.family AND c.source_kind='PRINCIPAL_RETURN' AND c.source_id=o.bank_key) ORDER BY r.created_at,o.id,g.id LIMIT $3`, env.ID.String(), s.now(), nativeOccurrenceDiscoveryBatch)
	if err != nil {
		return 0, err
	}
	type observed struct {
		p                                               Principal
		grant, operation                                funds.ID
		request, receipt, returned                      []byte
		requestHash, receiptHash, returnHash, signature string
	}
	items := []observed{}
	for rows.Next() {
		var user, grant, operation string
		v := observed{p: Principal{EnvironmentID: env.ID, Kind: "SYSTEM"}}
		if err = rows.Scan(&user, &grant, &operation, &v.request, &v.requestHash, &v.signature, &v.receipt, &v.receiptHash, &v.returned, &v.returnHash); err != nil {
			rows.Close()
			return 0, err
		}
		v.p.UserID, err = funds.ParseID(user)
		if err != nil {
			rows.Close()
			return 0, err
		}
		v.grant, err = funds.ParseID(grant)
		if err != nil {
			rows.Close()
			return 0, err
		}
		v.operation, err = funds.ParseID(operation)
		if err != nil {
			rows.Close()
			return 0, err
		}
		items = append(items, v)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return 0, err
	}
	registered := 0
	for _, item := range items {
		var request bankv2.Request
		var receipt NativeReceipt
		var returned bankv2.PrincipalReturn
		if Hash(item.receipt) != item.receiptHash || Hash(item.returned) != item.returnHash || auditJSON(item.request, &request) != nil || auditJSON(item.receipt, &receipt) != nil || auditJSON(item.returned, &returned) != nil {
			return 0, reject("EVIDENCE_INVALID", nil)
		}
		h, e := request.Digest()
		if e != nil || h != item.requestHash || request.Namespace != s.Namespace || request.UserID != item.p.UserID.String() || request.OperationID != item.operation.String() || request.Effect.Kind != bankv2.Mature || !request.Effect.Fee.IsZero() || !request.Effect.Loss.IsZero() || receipt.Protocol != "native-receipt-v1" || receipt.OperationID != item.operation || receipt.BankResult.Status != bankv2.Settled || bankv2.VerifyResult(request, receipt.BankResult) != nil || receipt.BankResult.PrincipalReturn == nil || returned.Role != "PRINCIPAL_RETURN" || returned.OperationKey != request.Key || returned.RequestHash != h || returned.Amount != request.Effect.Net || returned.OccurredAt.After(s.now()) {
			return 0, reject("EVIDENCE_INVALID", e)
		}
		a, _ := encode(*receipt.BankResult.PrincipalReturn)
		b, _ := encode(returned)
		if string(a) != string(b) {
			return 0, reject("EVIDENCE_INVALID", nil)
		}
		if err = assetStoredPermit(ctx, tx, item.p, item.operation, request, item.signature); err != nil {
			return 0, err
		}
		grant, err := loadNativeStandingGrant(ctx, tx, item.p, item.grant)
		if err != nil {
			return 0, err
		}
		source := policy.NativeOccurrenceSource{Kind: "PRINCIPAL_RETURN", ID: returned.OperationKey, OriginalHash: item.returnHash, OccurredAt: returned.OccurredAt, AvailableAt: returned.OccurredAt}
		original, err := encode(struct {
			Protocol        string                 `json:"protocol"`
			Request         bankv2.Request         `json:"request"`
			Receipt         NativeReceipt          `json:"receipt"`
			PrincipalReturn bankv2.PrincipalReturn `json:"principal_return"`
		}{nativeStandingAssetProtocol + "/settled-maturity-source", request, receipt, returned})
		if err != nil {
			return 0, err
		}
		_, created, err := registerNativeOccurrence(ctx, tx, item.p, grant.Grant, source, nil, original, s.now())
		if err != nil {
			return 0, err
		}
		if created {
			registered++
		}
	}
	return registered, tx.Commit(ctx)
}
