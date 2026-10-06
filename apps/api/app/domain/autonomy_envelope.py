"""Five necessary sets over trusted facts; no new execution authority or effect."""

from typing import Literal
from uuid import UUID

from app.domain.autonomy import SUPPORTED_ACTIONS, classify_autonomy
from app.domain.autonomy_types import AutonomyDecision, AutonomyFacts
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash
from app.domain.policy_configuration import configuration_hash

SetName = Literal[
    "FinanciallySafeSet",
    "UserAuthorizedSet",
    "LiquidityCompatibleSet",
    "EvidenceSufficientSet",
    "SupportedActionSet",
]
Membership = Literal["IN", "OUT", "UNKNOWN"]
SET_NAMES: tuple[SetName, ...] = (
    "FinanciallySafeSet",
    "UserAuthorizedSet",
    "LiquidityCompatibleSet",
    "EvidenceSufficientSet",
    "SupportedActionSet",
)
# Only specific original checks justify attributing a rejection to liquidity.
# Other financial failures do not imply that the independent liquidity branch ran.
LIQUIDITY_REJECTIONS = frozenset(
    {
        "UNAVAILABLE_SOURCE_CASH",
        "PRINCIPAL_ARRIVES_AFTER_CONFIRMED_BOUND",
        "UNACCEPTED_PLANNED_EXIT_CANNOT_SUPPORT_CASH_BOUNDARY",
        "ORIGINAL_PRINCIPAL_REQUIRES_RECONCILIATION",
        "ORIGINAL_MATURITY_REQUIRES_RECONCILIATION",
        "BASELINE_LIQUIDITY_RISK",
        "LIQUIDITY_RISK",
    }
)


class EnvelopeSet(BoundaryModel):
    name: SetName
    membership: Membership
    reasons: list[str]
    evidence_ids: list[UUID]
    policy_version_ids: list[UUID]
    source_context_hash: str | None
    effect_hash: str | None


class EnvelopeEvaluation(BoundaryModel):
    algorithm_version: Literal["five-set-original-evaluation-v1"] = (
        "five-set-original-evaluation-v1"
    )
    evaluation_only: Literal[True] = True
    authority_granted: Literal[False] = False
    sets: list[EnvelopeSet]
    intersection: Membership
    execution_eligible: bool
    automatic_execution_allowed: bool
    original_decision: AutonomyDecision | None
    facts_hash: str


def evaluate_envelope(facts: AutonomyFacts, *, audit_verified: bool) -> EnvelopeEvaluation:
    """Original evaluator remains the final necessary gate, even for five IN sets.

    IN means that the original check actually completed for this exact effect.
    A short-circuited branch remains UNKNOWN; reason strings never create a pass.
    Trusted adapters alone produce facts and the audit verdict. Neither is public input.
    """
    facts = AutonomyFacts.model_validate(facts.model_dump(warnings=False))
    decision = classify_autonomy(facts)
    effect, validation = facts.effect, facts.validation
    digest = execution_effect_hash(effect) if effect is not None else None
    memberships: dict[SetName, tuple[Membership, list[str]]] = {
        name: ("UNKNOWN", ["ORIGINAL_BRANCH_NOT_VERIFIED"]) for name in SET_NAMES
    }
    memberships["SupportedActionSet"] = (
        ("IN", ["ORIGINAL_MVP_ACTION_IMPLEMENTED"])
        if facts.action_type in SUPPORTED_ACTIONS
        else ("OUT", ["UNSUPPORTED_ACTION"])
    )
    proof_reasons = [issue.code for issue in facts.source_issues]
    if not audit_verified:
        proof_reasons.append("CURRENT_TYPED_AUDIT_NOT_VERIFIED")
    if not facts.source_evidence_ids or facts.source_context_hash is None:
        proof_reasons.append("SOURCE_EVIDENCE_INCOMPLETE")
    if not facts.authority.evidence_ids or facts.authority.status == "MISSING_EVIDENCE":
        proof_reasons.append("AUTHORITY_EVIDENCE_MISSING")
    if validation is not None and validation.status == "INSUFFICIENT_EVIDENCE":
        proof_reasons.extend(validation.reasons or ["INCOMPLETE_EXECUTION_FACTS"])
    if effect is not None and (
        effect.user_id != facts.user_id or effect.action_type != facts.action_type
    ):
        proof_reasons.append("EFFECT_IDENTITY_MISMATCH")
    if validation is not None and validation.effect_hash != digest:
        proof_reasons.append("FINANCIAL_VALIDATION_EFFECT_MISMATCH")
    if validation is not None and validation.status in {"READY", "CONFIRMATION_REQUIRED"}:
        if validation.projected_boundary is None or validation.projected_snapshot is None:
            proof_reasons.append("FINANCIAL_PROJECTION_MISSING")
        elif validation.projected_snapshot.as_of != facts.as_of:
            proof_reasons.append("FINANCIAL_VALIDATION_CLOCK_MISMATCH")
    if validation is not None and any(
        result is not None and result.status == "INSUFFICIENT_EVIDENCE"
        for result in (
            validation.baseline_boundary,
            validation.projected_boundary,
            validation.reservation_adjusted_baseline,
            validation.reservation_adjusted_boundary,
        )
    ):
        proof_reasons.append("SOURCE_EVIDENCE_INCOMPLETE")
    memberships["EvidenceSufficientSet"] = (
        ("OUT", sorted(set(proof_reasons)))
        if proof_reasons
        else (
            ("IN", ["ACTUAL_CONTEXT_AUTHORITY_AND_AUDIT_VERIFIED"])
            if effect is not None and validation is not None
            else ("UNKNOWN", ["EXECUTABLE_EVIDENCE_NOT_VERIFIED"])
        )
    )
    authority_reasons = list(facts.authority.reasons)
    if facts.authority.status != "AUTHORIZED":
        authority_reasons.append(facts.authority.status)
    if facts.payee.status in {"AGENT_NEW", "MISSING_IDENTITY", "UNSUPPORTED", "USER_INITIATED_NEW"}:
        authority_reasons.append("PAYEE_" + facts.payee.status)
    if decision.confirmation_required and not decision.confirmation_satisfied:
        authority_reasons.extend(decision.reasons)
    authority_reasons.extend(
        reason
        for reason in decision.reasons
        if reason
        in {
            "CONFIRMATION_DOES_NOT_BIND_CURRENT_EFFECT",
            "EXACT_CURRENT_POLICY_REQUIRED",
            "EXPLICIT_USER_TRANSFER_INTENT_REQUIRED",
            "EFFECT_OUTSIDE_VALIDITY",
        }
    )
    if authority_reasons:
        memberships["UserAuthorizedSet"] = ("OUT", sorted(set(authority_reasons)))
    checked = (
        not proof_reasons
        and effect is not None
        and validation is not None
        and validation.effect_hash == digest
    )
    if checked and validation is not None:
        if (
            validation.status in {"READY", "CONFIRMATION_REQUIRED"}
            and validation.projected_boundary is not None
            and validation.projected_snapshot is not None
            and validation.projected_snapshot.as_of == facts.as_of
        ):
            if not authority_reasons:
                memberships["UserAuthorizedSet"] = ("IN", ["EXACT_CURRENT_ORIGINAL_AUTHORITY"])
            memberships["FinanciallySafeSet"] = (
                "IN",
                ["ORIGINAL_FINANCIAL_REVALIDATION_COMPLETED"],
            )
            memberships["LiquidityCompatibleSet"] = (
                "IN",
                ["ORIGINAL_LIQUIDITY_REVALIDATION_COMPLETED"],
            )
        elif validation.status == "BLOCKED":
            memberships["FinanciallySafeSet"] = (
                "OUT",
                validation.reasons or ["ORIGINAL_FINANCIAL_REJECTION"],
            )
    liquidity_reasons = sorted(
        set([*facts.hard_block_reasons, *(validation.reasons if validation else [])])
        & LIQUIDITY_REJECTIONS
    )
    if liquidity_reasons:
        memberships["LiquidityCompatibleSet"] = ("OUT", liquidity_reasons)
    sets = [
        EnvelopeSet(
            name=name,
            membership=memberships[name][0],
            reasons=memberships[name][1],
            evidence_ids=facts.source_evidence_ids,
            policy_version_ids=facts.authority.policy_version_ids,
            source_context_hash=facts.source_context_hash,
            effect_hash=digest,
        )
        for name in SET_NAMES
    ]
    if any(item.membership == "OUT" for item in sets) or decision.level in {
        "BLOCKED",
        "ADVISE_ONLY",
    }:
        intersection: Membership = "OUT"
    elif all(item.membership == "IN" for item in sets) and decision.execution_eligible:
        intersection = "IN"
    else:
        intersection = "UNKNOWN"
    return EnvelopeEvaluation(
        sets=sets,
        intersection=intersection,
        execution_eligible=intersection == "IN" and decision.execution_eligible,
        automatic_execution_allowed=intersection == "IN" and decision.level == "AUTO_EXECUTE",
        original_decision=decision,
        facts_hash=configuration_hash(facts.model_dump(mode="json")),
    )
