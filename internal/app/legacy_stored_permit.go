package app

import (
	"bytes"
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	executionaudit "zhiyu.local/zhiyu-go/internal/execution/audit"
	execution "zhiyu.local/zhiyu-go/internal/execution/domain"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	planning "zhiyu.local/zhiyu-go/internal/planning/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

// A committed T2 witness binds the original signature to the exact historical
// decision. Recovery must not depend on today's signer, policy or session.
// The bank still authenticates the original signature at its own boundary.
func (s *Service) verifyLegacyStoredPermit(ctx context.Context, q queryRow, p Principal, operation funds.ID, request execution.BankRequest, signature string) error {
	if request.Validate() != nil || request.Namespace != s.Namespace || request.UserID != p.UserID.String() || request.OperationID != operation.String() || signature == "" {
		return reject("EVIDENCE_INVALID", nil)
	}
	var raw []byte
	var originalHash, protocol, permit string
	var decisionAt time.Time
	err := q.QueryRow(ctx, `SELECT original,original_hash,protocol_version,permit_id::text,decision_at FROM submission_permits WHERE environment_id=$1 AND user_id=$2 AND operation_id=$3`, p.EnvironmentID.String(), p.UserID.String(), operation.String()).Scan(&raw, &originalHash, &protocol, &permit, &decisionAt)
	if errors.Is(err, pgx.ErrNoRows) {
		// Pre-009 originals have no retained witness. Preserve their previous
		// verification path and its explicit audit completeness gap.
		if execution.VerifyPermit(request, signature, s.PermitSecret) {
			return nil
		}
		return reject("EVIDENCE_INCOMPLETE", nil)
	}
	if err != nil {
		return err
	}
	var proof executionaudit.SubmissionPermitEvidence
	if decode(raw, &proof) != nil || Hash(raw) != originalHash {
		return reject("EVIDENCE_INVALID", nil)
	}
	if protocol != executionaudit.SubmissionPermitProtocol || proof.Protocol != protocol {
		return reject("UNSUPPORTED_VERSION", nil)
	}
	var planID, policyID, authorizationKind, requestHash, namespace, bankKey, operationPermit string
	var policyVersion, reservedFV uint64
	var grantID, confirmationID *string
	var grantVersion *uint64
	var dependencies []byte
	err = q.QueryRow(ctx, `SELECT plan_id::text,policy_id::text,policy_version,authorization_kind,grant_id::text,grant_version,confirmation_id::text,dependencies,reserved_funds_version,bank_request_hash,bank_namespace,bank_key,permit_id::text FROM operations WHERE environment_id=$1 AND user_id=$2 AND id=$3`, p.EnvironmentID.String(), p.UserID.String(), operation.String()).Scan(&planID, &policyID, &policyVersion, &authorizationKind, &grantID, &grantVersion, &confirmationID, &dependencies, &reservedFV, &requestHash, &namespace, &bankKey, &operationPermit)
	if err != nil {
		return err
	}
	plan, err := funds.ParseID(planID)
	if err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	candidate, err := loadCandidate(ctx, q, p, plan)
	if err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	var deps planning.DependencyVector
	digest, digestErr := request.Digest()
	if decode(dependencies, &deps) != nil || digestErr != nil || digest != requestHash || proof.EnvironmentID != p.EnvironmentID || proof.UserID != p.UserID || proof.OperationID != operation || proof.Namespace != request.Namespace || namespace != request.Namespace || bankKey != request.Key || proof.PermitID != request.PermitID || permit != proof.PermitID || operationPermit != permit || proof.BankRequestHash != digest || proof.EffectHash != candidate.EffectHash || proof.Dependencies != deps || deps != candidate.Dependencies || proof.ReservedFundsVersion != reservedFV || proof.SourceVersion != deps.SourceEvidenceVersion || proof.SignatureHash != Hash([]byte(signature)) || !proof.DecisionAt.Equal(decisionAt) || decisionAt.Before(request.RequestedAt) || policyID != candidate.PolicyID.String() || policyVersion != candidate.PolicyVersion {
		return reject("EVIDENCE_INVALID", nil)
	}
	if request.Kind != "ALLOCATE_GOAL" || request.Amount != candidate.Amount || request.ReferenceHash != candidate.EffectHash || request.SourceAccountID != candidate.Scope.SourceAccountID.String() || request.DestinationAccountID != candidate.Scope.DestinationAccountID.String() || request.GoalID != candidate.Scope.GoalID.String() || len(candidate.Uses) != 1 || request.IncomeOriginID != candidate.Uses[0].OriginID.String() {
		return reject("EVIDENCE_INVALID", nil)
	}
	var policyOriginal []byte
	if err = q.QueryRow(ctx, `SELECT original FROM policies WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND version=$4`, p.EnvironmentID.String(), p.UserID.String(), policyID, policyVersion).Scan(&policyOriginal); err != nil {
		return reject("EVIDENCE_INVALID", err)
	}
	encodedPolicy, err := encode(proof.Policy)
	if err != nil || !bytes.Equal(policyOriginal, encodedPolicy) {
		return reject("EVIDENCE_INVALID", err)
	}
	if err = checkHistoricalConfirmation(ctx, q, p, proof.Policy.Confirmation); err != nil {
		return err
	}
	var authorization planning.Authorization
	switch authorizationKind {
	case "STANDING_GRANT":
		if proof.Grant == nil || proof.Confirmation != nil || grantID == nil || grantVersion == nil || proof.Grant.ID.String() != *grantID || proof.Grant.Version != *grantVersion {
			return reject("EVIDENCE_INVALID", nil)
		}
		var grantOriginal []byte
		if err = q.QueryRow(ctx, `SELECT original FROM grants WHERE environment_id=$1 AND user_id=$2 AND id=$3 AND version=$4`, p.EnvironmentID.String(), p.UserID.String(), *grantID, *grantVersion).Scan(&grantOriginal); err != nil {
			return reject("EVIDENCE_INVALID", err)
		}
		encodedGrant, e := encode(*proof.Grant)
		if e != nil || !bytes.Equal(grantOriginal, encodedGrant) {
			return reject("EVIDENCE_INVALID", e)
		}
		if err = checkHistoricalConfirmation(ctx, q, p, proof.Grant.Confirmation); err != nil {
			return err
		}
		authorization, err = planning.AuthorizeStanding(candidate, proof.Policy, *proof.Grant, proof.DecisionAt)
	case "SINGLE_USE":
		if proof.Confirmation == nil || proof.Grant != nil || confirmationID == nil || proof.Confirmation.ID.String() != *confirmationID || grantID != nil || grantVersion != nil {
			return reject("EVIDENCE_INVALID", nil)
		}
		if err = checkHistoricalConfirmation(ctx, q, p, *proof.Confirmation); err != nil {
			return err
		}
		authorization, err = planning.AuthorizeOnce(candidate, proof.Policy, *proof.Confirmation, proof.DecisionAt)
	default:
		return reject("EVIDENCE_INVALID", nil)
	}
	if err != nil || authorization.SourceID.String() != request.AuthorizationID || int64(authorization.SourceVersion) != request.AuthorizationVersion {
		return reject("EVIDENCE_INVALID", err)
	}
	return nil
}

func checkHistoricalConfirmation(ctx context.Context, q queryRow, p Principal, source policy.ConfirmationSource) error {
	if err := checkConfirmation(ctx, q, p, source); err != nil {
		return err
	}
	var expires time.Time
	err := q.QueryRow(ctx, `SELECT s.expires_at FROM confirmations c JOIN audit_session_subjects s ON (s.environment_id=c.environment_id AND s.user_id=c.user_id AND s.id=c.session_id) WHERE c.environment_id=$1 AND c.user_id=$2 AND c.id=$3`, p.EnvironmentID.String(), p.UserID.String(), source.ID.String()).Scan(&expires)
	if err != nil || source.PrincipalKind != "USER" || source.EnvironmentID != p.EnvironmentID || source.UserID != p.UserID || !source.ConfirmedAt.Before(expires) {
		return reject("EVIDENCE_INVALID", err)
	}
	return nil
}
