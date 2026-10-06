"""Additional FULL protection vetoes; never grants or rewrites an original effect."""

from typing import Literal

from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import ExecutionContext, ExecutionEffect, ExecutionValidation
from app.domain.full_protection_projection import (
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_registered_account_debits import (
    FullAccountDebitBoundsProof,
    validate_full_account_debit_bounds_proof,
)
from app.domain.full_seasonal_current_protection import derive_current_seasonal_protection_bindings
from app.domain.full_seasonal_ended_adoption import REFERENCE_KIND as ENDED_SEASONAL_REFERENCE_KIND
from app.domain.full_seasonal_protection import derive_seasonal_protection_bindings
from app.domain.policy_configuration import configuration_hash


class FullExecutionProtectionResult(BoundaryModel):
    protocol: Literal["full-execution-protection-v1"] = "full-execution-protection-v1"
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    original_effect_hash: str
    status: Literal["NO_ADDITIONAL_POLICY", "PASSED", "BLOCKED", "UNKNOWN"]
    minimum_projected_margin_cents: int | None = None
    projection_input_hash: str | None = None
    current_policy_version_ids: list[str]
    source_evidence_ids: list[str]
    reasons: list[str]
    result_hash: str


def validate_full_execution_protection(
    effect: ExecutionEffect,
    context: ExecutionContext,
    validation: ExecutionValidation,
    sources: list[FullProtectionPolicySource],
    *,
    source_issues: tuple[str, ...] = (),
    account_debit_bounds: FullAccountDebitBoundsProof | None = None,
) -> FullExecutionProtectionResult:
    """Use the original validated post-effect facts, including other reservations.

    This check adds a refusal to the original execution decision. In particular,
    a PASSED result is insufficient without the original financial and consent
    checks. No original canonical text, effect hash or authority is modified.
    """
    digest = execution_effect_hash(effect)
    versions = sorted(str(row.version_id) for row in sources)
    evidence = sorted({str(identity) for row in sources for identity in row.evidence_ids})

    def result(
        status: Literal["NO_ADDITIONAL_POLICY", "PASSED", "BLOCKED", "UNKNOWN"],
        reasons: list[str],
        margin: int | None = None,
        input_hash: str | None = None,
    ) -> FullExecutionProtectionResult:
        values = {
            "original_effect_hash": digest,
            "status": status,
            "minimum_projected_margin_cents": margin,
            "projection_input_hash": input_hash,
            "current_policy_version_ids": versions,
            "source_evidence_ids": evidence,
            "reasons": sorted(set(reasons)),
        }
        return FullExecutionProtectionResult.model_validate(
            {**values, "result_hash": configuration_hash(values)}
        )

    if source_issues:
        return result("UNKNOWN", list(source_issues))
    if not sources:
        return result("NO_ADDITIONAL_POLICY", [])
    if (
        effect.user_id != context.user_id
        or validation.effect_hash != digest
        or context.snapshot.as_of < effect.valid_from
        or context.snapshot.as_of >= effect.expires_at
        or validation.status not in {"READY", "CONFIRMATION_REQUIRED"}
        or validation.projected_snapshot is None
        or context.source_issues
    ):
        return result("UNKNOWN", ["ORIGINAL_EXECUTION_PROJECTION_NOT_VERIFIED"])
    projected = validation.projected_snapshot
    if projected.as_of != context.snapshot.as_of or projected.timezone != context.snapshot.timezone:
        return result("UNKNOWN", ["ORIGINAL_PROJECTED_CLOCK_MISMATCH"])
    derive_seasonal = (
        derive_current_seasonal_protection_bindings
        if any(
            row.get("kind") == ENDED_SEASONAL_REFERENCE_KIND
            for source in sources
            for row in source.reference_snapshots
        )
        else derive_seasonal_protection_bindings
    )
    seasonal = derive_seasonal(
        sources, projected.as_of, projected.timezone, expected_user_id=context.user_id
    )
    if seasonal.reasons:
        return result("UNKNOWN", list(seasonal.reasons))
    if seasonal.requested:
        evidence = sorted(set(evidence) | {str(identity) for identity in seasonal.evidence()})
    try:
        projection_input = FullProtectionProjectionInput(
            snapshot=projected,
            boundary_versions=context.versions,
            positions=validation.projected_positions,
            boundary_products=context.boundary_products,
            policies=sources,
            reserved_cash_by_account=context.reserved_cash_by_account,
        )
        full = project_full_protection(projection_input)
    except (ValueError, TypeError, OverflowError):
        return result("UNKNOWN", ["FULL_CURRENT_OR_PROJECTED_SOURCES_NOT_VERIFIED"])
    if (
        full.status == "UNKNOWN"
        or full.full_annual_projection is None
        or not full.full_obligations_complete_within_registered_current_scope
    ):
        return result("UNKNOWN", ["FULL_PROJECTED_PROTECTION_UNKNOWN", *full.reasons])
    # A present source balance does not prove all future account-specific debits.
    if full.source_account_checks:
        if account_debit_bounds is None:
            return result("UNKNOWN", ["FULL_FUTURE_ACCOUNT_DEBITS_NOT_PROVEN"])
        try:
            bounds = validate_full_account_debit_bounds_proof(
                account_debit_bounds, effect, context, validation, projection_input, full
            )
        except (ValueError, TypeError, OverflowError):
            return result("UNKNOWN", ["FULL_FUTURE_ACCOUNT_DEBIT_PROOF_NOT_BOUND"])
        if bounds.status == "UNKNOWN":
            return result("UNKNOWN", ["FULL_FUTURE_ACCOUNT_DEBITS_NOT_PROVEN", *bounds.reasons])
        if bounds.status == "BLOCKED":
            return result("BLOCKED", ["FULL_REGISTERED_ACCOUNT_CASH_BOUND_NEGATIVE"])
    curve = full.full_annual_projection
    if curve.minimum_margin_cents is None or len(curve.calculation_trace) != 366 * 3:
        return result("UNKNOWN", ["FULL_PROJECTED_PHASE_COVERAGE_NOT_PROVEN"])
    margin = curve.minimum_margin_cents
    return result(
        "BLOCKED" if margin < 0 else "PASSED",
        ["FULL_PROTECTED_COMMITMENT_WOULD_BE_VIOLATED"] if margin < 0 else [],
        margin,
        full.input_hash,
    )
