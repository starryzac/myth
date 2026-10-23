package app

import (
	"context"
	"time"
	"zhiyu.local/zhiyu-go/internal/execution/bankv2"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type nativeStandingPaymentProof struct {
	Protocol           string                     `json:"protocol"`
	OperationID        funds.ID                   `json:"operation_id"`
	PlanHash           string                     `json:"plan_hash"`
	BankRequestHash    string                     `json:"bank_request_hash"`
	EffectHash         string                     `json:"effect_hash"`
	SignatureHash      string                     `json:"signature_hash"`
	PermitID           funds.ID                   `json:"permit_id"`
	Grant              NativeStandingGrant        `json:"grant"`
	GrantStateSequence uint64                     `json:"grant_state_sequence"`
	Usage              policy.NativeStandingUsage `json:"usage"`
	Current            NativePaymentPreview       `json:"current"`
	BankCapturedAt     time.Time                  `json:"bank_captured_at"`
	DecisionAt         time.Time                  `json:"decision_at"`
}

func validateStandingPaymentRequest(plan nativeStandingPaymentPlan, hash string, request bankv2.Request) error {
	expected := bankv2.Request{Protocol: bankv2.Protocol, Namespace: request.Namespace, Key: plan.ID.String(), OperationID: plan.ID.String(), UserID: plan.Scope.UserID.String(), PermitID: plan.PermitID.String(), ReferenceHash: hash, RequestedAt: plan.Preview.CreatedAt, ExpiresAt: plan.Preview.ExpiresAt, Effect: plan.Preview.BankEffect, Authorities: []bankv2.Authority{{Role: "EXECUTE", SourceID: plan.Grant.ID.String(), Version: int64(plan.Grant.Version), PolicyID: plan.Grant.Terms.Policies[0].PolicyID.String(), PolicyVersion: int64(plan.Grant.Terms.Policies[0].Version)}}}
	a, e := request.Digest()
	b, e2 := expected.Digest()
	if e != nil || e2 != nil || a != b {
		return reject("EVIDENCE_INVALID", nil)
	}
	return nil
}

func validateStandingPaymentProof(plan nativeStandingPaymentPlan, hash string, proof nativeStandingPaymentProof, request bankv2.Request) error {
	if e := validateStandingPaymentRequest(plan, hash, request); e != nil {
		return e
	}
	a, _ := encode(plan.Grant)
	b, _ := encode(proof.Grant)
	d, c := plan.Preview.Dependencies, proof.Current
	if proof.Protocol != nativeStandingPaymentPermitProtocol || proof.OperationID != plan.ID || proof.PlanHash != hash || proof.PermitID != plan.PermitID || string(a) != string(b) || proof.GrantStateSequence < plan.GrantStateSequence || proof.DecisionAt.Before(plan.Preview.CreatedAt) || !proof.DecisionAt.Before(plan.Preview.ExpiresAt) || proof.BankCapturedAt.After(proof.DecisionAt.Add(time.Second)) || proof.DecisionAt.Sub(proof.BankCapturedAt) > 30*time.Second || !c.CreatedAt.Equal(proof.DecisionAt) || !c.ExpiresAt.Equal(plan.Preview.ExpiresAt) || c.ID != plan.Preview.ID || c.Request != plan.Preview.Request || c.Scope != plan.Scope || c.SessionID != (funds.ID{}) || c.Liability != plan.Preview.Liability || c.EffectHash != plan.Preview.EffectHash || c.InventoryHash != plan.Preview.InventoryHash || c.Dependencies.FundsVersion != d.FundsVersion+1 || c.Dependencies.SourceEvidenceVersion != d.SourceEvidenceVersion || c.Dependencies.WriterGeneration != d.WriterGeneration || c.Dependencies.ControlVersion != d.ControlVersion || c.Dependencies.GrantVersion != plan.Grant.Version {
		return reject("EVIDENCE_INVALID", nil)
	}
	if e := replayNativePaymentPreview(c); e != nil {
		return e
	}
	a, _ = encode(c.BankEffect)
	b, _ = encode(request.Effect)
	if string(a) != string(b) {
		return reject("EVIDENCE_INVALID", nil)
	}
	if e := policy.AuthorizeNativeStanding(plan.Grant.Terms, "ACTIVE", plan.Authority, proof.Usage, proof.DecisionAt); e != nil {
		return reject("EVIDENCE_INVALID", e)
	}
	return nil
}

func standingPaymentStoredPermit(ctx context.Context, q queryRow, p Principal, operation funds.ID, request bankv2.Request, signature string) error {
	var raw []byte
	var hash, rh, eh, sh, permit string
	if e := q.QueryRow(ctx, `SELECT original,original_hash::text,bank_request_hash::text,effect_hash::text,signature_hash::text,permit_id::text FROM native_submission_permits WHERE environment_id=$1 AND user_id=$2 AND operation_id=$3`, p.EnvironmentID.String(), p.UserID.String(), operation.String()).Scan(&raw, &hash, &rh, &eh, &sh, &permit); e != nil {
		return e
	}
	var proof nativeStandingPaymentProof
	want, e := request.Digest()
	effect, e2 := request.Effect.Digest()
	if e != nil || e2 != nil || auditJSON(raw, &proof) != nil || Hash(raw) != hash || rh != want || eh != effect || sh != Hash([]byte(signature)) || permit != request.PermitID || proof.BankRequestHash != rh || proof.EffectHash != eh || proof.SignatureHash != sh {
		return reject("EVIDENCE_INVALID", nil)
	}
	plan, planHash, e := loadStandingPaymentPlan(ctx, q, p, operation)
	if e != nil {
		return e
	}
	return validateStandingPaymentProof(plan, planHash, proof, request)
}

func (s *Service) standingPaymentPermit(ctx context.Context, c Claim) (nativePermitted, error) {
	var out nativePermitted
	var unsigned bool
	if e := s.Pool.QueryRow(ctx, `SELECT permit_signature IS NULL FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND command_kind='PAY_RECURRING' AND mode='STANDING'`, c.Principal.EnvironmentID.String(), c.Principal.UserID.String(), c.OperationID.String()).Scan(&unsigned); e != nil {
		return out, e
	}
	plan, planHash, e := loadStandingPaymentPlan(ctx, s.Pool, c.Principal, c.OperationID)
	if e != nil {
		return out, e
	}
	var bank bankv2.LiabilityState
	var captured time.Time
	if unsigned {
		bank, captured, e = s.paymentBankLiability(ctx, c.Principal, plan.Preview.Debt.ID)
		if e != nil {
			return out, e
		}
	}
	tx, e := s.begin(ctx)
	if e != nil {
		return out, e
	}
	defer rollback(ctx, tx)
	if e = s.lockNativeClaim(ctx, tx, c); e != nil {
		return out, e
	}
	p := c.Principal
	var raw []byte
	var rh, eh string
	var sig *string
	var reserved uint64
	if e = tx.QueryRow(ctx, `SELECT status,bank_request,bank_request_hash::text,effect_hash::text,permit_signature,reserved_funds_version FROM native_operations WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND mode='STANDING'`, p.EnvironmentID.String(), p.UserID.String(), c.OperationID.String()).Scan(&out.State, &raw, &rh, &eh, &sig, &reserved); e != nil {
		return out, e
	}
	if auditJSON(raw, &out.Request) != nil || out.Request.Namespace != s.Namespace {
		return out, reject("EVIDENCE_INVALID", nil)
	}
	if e = validateStandingPaymentRequest(plan, planHash, out.Request); e != nil {
		return out, e
	}
	if out.State == "SETTLED" || out.State == "REJECTED" || out.State == "CANCELLED" {
		out.Cancelled = true
		return out, nil
	}
	if sig != nil {
		if e = standingPaymentStoredPermit(ctx, tx, p, c.OperationID, out.Request, *sig); e != nil {
			return out, e
		}
		out.Signature = *sig
		return out, tx.Commit(ctx)
	}
	if out.State != "QUEUED" {
		return out, reject("OPERATION_UNKNOWN", nil)
	}
	now := s.now()
	var current NativePaymentPreview
	var grant NativeStandingGrantView
	var usage policy.NativeStandingUsage
	permissionErr := func() error {
		if now.Before(plan.Preview.CreatedAt) || !now.Before(plan.Preview.ExpiresAt) {
			return reject("AUTHORIZATION_EXPIRED", nil)
		}
		var e error
		grant, e = loadNativeStandingGrant(ctx, tx, p, plan.Grant.ID)
		if e != nil {
			return e
		}
		a, _ := encode(plan.Grant)
		b, _ := encode(grant.Grant)
		if string(a) != string(b) {
			return reject("EVIDENCE_INVALID", nil)
		}
		if grant.State != "ACTIVE" {
			return reject("NATIVE_GRANT_NOT_ACTIVE", nil)
		}
		versions := []funds.ID{plan.Grant.Terms.Policies[0].VersionID}
		refs, _, _, e := nativeGrantReferences(ctx, tx, s, p, versions)
		if e != nil {
			return e
		}
		a, _ = encode(refs)
		b, _ = encode(plan.Grant.Terms.Policies)
		if string(a) != string(b) {
			return reject("NATIVE_GRANT_POLICY_CHANGED", nil)
		}
		usage, e = nativeStandingUsage(ctx, tx, p, grant.Grant, now, &plan.Occurrence.ID)
		if e != nil {
			return e
		}
		if e = policy.AuthorizeNativeStanding(grant.Grant.Terms, grant.State, plan.Authority, usage, now); e != nil {
			return reject(e.Error(), e)
		}
		snapshot, e := s.loadTemplateSnapshot(ctx, tx, p, now, plan.Preview.Timezone)
		if e != nil {
			return e
		}
		d := plan.Preview.Dependencies
		if snapshot.Native == nil || snapshot.FundsVersion != reserved || reserved != d.FundsVersion+1 || snapshot.SourceVersion != d.SourceEvidenceVersion || snapshot.WriterGeneration != d.WriterGeneration || snapshot.ControlVersion != d.ControlVersion || bank != plan.Preview.Liability {
			return reject("SOURCE_CHANGED", nil)
		}
		if e = checkNativePaymentHolds(*snapshot.Native, c.OperationID, plan.Preview); e != nil {
			return e
		}
		if e = releaseNativeAssetResources(snapshot.Native, c.OperationID); e != nil {
			return e
		}
		snapshot.Held, e = snapshot.Held.Sub(plan.Preview.Resources.Amount)
		if e != nil {
			return reject("EVIDENCE_INVALID", e)
		}
		for _, use := range plan.Preview.Resources.IncomeUses {
			found := false
			for i := range snapshot.Facts.Income {
				fact := &snapshot.Facts.Income[i]
				if fact.ID == use.OriginID {
					fact.Available, e = fact.Available.Add(use.Amount)
					if e != nil {
						return e
					}
					found = true
				}
			}
			if !found {
				return reject("EVIDENCE_INVALID", nil)
			}
		}
		resources, e := currentNativePayment(snapshot, plan.Preview, bank)
		if e != nil {
			return e
		}
		a, _ = encode(resources)
		b, _ = encode(plan.Preview.Resources)
		if string(a) != string(b) {
			return reject("SOURCE_CHANGED", nil)
		}
		current, e = buildNativePaymentPreview(snapshot, plan.Preview.Request, bankv2.LiabilitySnapshot{Liabilities: []bankv2.LiabilityState{bank}}, now, plan.Preview.ExpiresAt, plan.Preview.ID)
		if e != nil {
			return e
		}
		current.Dependencies.GrantVersion = grant.Grant.Version
		current.ReviewedHash, e = NativePaymentReviewHash(current)
		if e != nil {
			return e
		}
		var budget bool
		e = tx.QueryRow(ctx, `SELECT b.state='RESERVED' AND b.amount_cents=$4 AND b.grant_id=$5::uuid AND b.grant_version=$6 AND x.economic_amount_cents=$4 FROM native_grant_budget_claims b JOIN native_occurrence_operations x ON(x.environment_id=b.environment_id AND x.user_id=b.user_id AND x.occurrence_id=b.occurrence_id) WHERE b.environment_id=$1 AND b.user_id=$2 AND b.occurrence_id=$3 AND x.operation_id=$7`, p.EnvironmentID.String(), p.UserID.String(), plan.Occurrence.ID.String(), plan.Preview.Resources.Amount.Cents(), grant.Grant.ID.String(), grant.Grant.Version, plan.ID.String()).Scan(&budget)
		if e != nil {
			return e
		}
		if !budget {
			return reject("EVIDENCE_INVALID", nil)
		}
		return nil
	}()
	if permissionErr != nil {
		if Code(permissionErr) == "INTERNAL_ERROR" || Code(permissionErr) == "EVIDENCE_INVALID" {
			return out, permissionErr
		}
		if e = s.paymentCancelUnsigned(ctx, tx, c, Code(permissionErr)); e != nil {
			return out, e
		}
		out.Cancelled = true
		return out, tx.Commit(ctx)
	}
	out.Signature, e = bankv2.SignPermit(out.Request, s.PermitSecret)
	if e != nil {
		return out, e
	}
	proof := nativeStandingPaymentProof{nativeStandingPaymentPermitProtocol, c.OperationID, planHash, rh, eh, Hash([]byte(out.Signature)), plan.PermitID, grant.Grant, grant.StateSequence, usage, current, captured, now}
	if e = validateStandingPaymentProof(plan, planHash, proof, out.Request); e != nil {
		return out, e
	}
	proofRaw, e := encode(proof)
	if e != nil {
		return out, e
	}
	if _, e = tx.Exec(ctx, `INSERT INTO native_submission_permits(environment_id,user_id,operation_id,permit_id,bank_request_hash,effect_hash,signature_hash,original,original_hash,decision_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, p.EnvironmentID.String(), p.UserID.String(), c.OperationID.String(), out.Request.PermitID, rh, eh, proof.SignatureHash, proofRaw, Hash(proofRaw), now); e != nil {
		return out, e
	}
	if _, e = tx.Exec(ctx, `UPDATE native_operations SET status='SUBMITTING',permit_signature=$4,version=version+1,updated_at=$5 WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), c.OperationID.String(), out.Signature, now); e != nil {
		return out, e
	}
	out.State = "SUBMITTING"
	return out, tx.Commit(ctx)
}
