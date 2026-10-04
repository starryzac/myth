"""Deterministic four-level classification over verified adapter facts."""

from datetime import UTC
from typing import Any

from app.domain.autonomy_types import (
    AutonomyDecision,
    AutonomyFacts,
    AutonomyLevel,
    FinancialEvaluation,
    FiniteUserVariable,
)
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import ExecutionEffect
from app.domain.policy_configuration import configuration_hash

ALGORITHM_VERSION = "evidence-bound-autonomy-v1"
SUPPORTED_ACTIONS = {
    "TRANSFER_INTERNAL",
    "PAY_RECURRING",
    "ALLOCATE_GOAL",
    "PURCHASE_ASSET",
    "REDEEM_ASSET",
}
# Only these complete-financial rejections may be clarified as a user-owned amount choice.
# Unknown failures, absent bank facts and authority errors always remain fundamental gates.
FINANCIAL_REJECTIONS = {
    "UNAVAILABLE_SOURCE_CASH",
    "UNACCEPTED_PLANNED_EXIT_CANNOT_SUPPORT_CASH_BOUNDARY",
    "ORDINARY_ALLOCATION_REQUIRES_SAFE_BOUNDARY",
    "ACTION_WORSENS_PROTECTED_MARGIN",
    "REDEMPTION_DOES_NOT_SAFELY_IMPROVE_DEFICIT",
}


def economic_signature(effect: ExecutionEffect) -> str:
    """Compare economic outcomes, not generated operation or new-position identities."""
    return configuration_hash(_economic_payload(effect))


def _economic_payload(effect: ExecutionEffect) -> dict[str, Any]:
    effect = ExecutionEffect.model_validate(effect.model_dump(warnings=False))
    value = effect.model_dump(mode="json")
    for field in (
        "operation_id",
        "business_key",
        "valid_from",
        "expires_at",
        "quote_id",
        "payee_evidence_id",
    ):
        value.pop(field)
    if effect.action_type == "PURCHASE_ASSET":
        value.pop("position_id")
    value["cash_uses"] = sorted(value["cash_uses"], key=lambda u: u["account_id"])
    value["income_uses"] = sorted(
        value["income_uses"], key=lambda u: (u["origin_transaction_id"], u["account_id"])
    )
    value["policy_version_ids"] = sorted(value["policy_version_ids"])
    if value["liability"] is not None:
        value["liability"].pop("evidence_ids")
    if effect.latest_arrival_at is not None:
        value["latest_arrival_at"] = effect.latest_arrival_at.astimezone(UTC).isoformat()
    if effect.purchase_exit is not None:
        value["purchase_exit"].pop("earning_days")
        value["purchase_exit"].pop("liquidity_days")
        value["purchase_exit"]["principal_available_at"] = (
            effect.purchase_exit.principal_available_at.astimezone(UTC).isoformat()
        )
        if effect.purchase_exit.request_at is not None:
            value["purchase_exit"]["request_at"] = effect.purchase_exit.request_at.astimezone(
                UTC
            ).isoformat()
    return value


def _amount_invariant(effect: ExecutionEffect | None) -> str | None:
    if effect is None:
        return None
    value = _economic_payload(effect)
    value.pop("amount_cents")
    for key in ("cash_uses", "income_uses"):
        for use in value[key]:
            use.pop("amount_cents")
    return configuration_hash(value)


def classify_autonomy(
    facts: AutonomyFacts, uncertainty: FiniteUserVariable | None = None
) -> AutonomyDecision:
    """Evaluate one action and at most one complete finite user-owned variable."""
    facts = AutonomyFacts.model_validate(facts.model_dump(warnings=False))
    baseline = _single(facts)
    if uncertainty is None:
        return baseline
    uncertainty = FiniteUserVariable.model_validate(uncertainty.model_dump(warnings=False))
    return _finite(facts, baseline, uncertainty)


def _finite(
    facts: AutonomyFacts, baseline: AutonomyDecision, variable: FiniteUserVariable
) -> AutonomyDecision:
    worlds = sorted(variable.worlds, key=lambda w: w.candidate_key)
    outcomes = [_single(w.facts) for w in worlds]
    signatures = {
        w.candidate_key: r.economic_signature for w, r in zip(worlds, outcomes, strict=True)
    }

    def aggregate(
        level: AutonomyLevel,
        reasons: list[str],
        financial: FinancialEvaluation,
        status: str = "BLOCKED",
    ) -> AutonomyDecision:
        return _decision(facts, level, reasons, financial).model_copy(
            update={
                "effect_hash": None,
                "economic_signature": None,
                "uncertainty_status": status,
                "candidate_signatures": signatures,
                "confirmation_required": level == "ASK_ONCE",
            }
        )

    if (
        variable.kind != "USER_PREFERENCE"
        or variable.completeness != "COMPLETE"
        or not variable.evidence_ids
        or len({w.candidate_key for w in worlds}) != len(worlds)
        or facts.source_context_hash != variable.source_context_hash
        or any(
            w.facts.source_context_hash != variable.source_context_hash
            or w.facts.user_id != facts.user_id
            or w.facts.as_of != facts.as_of
            or set(w.facts.authority.policy_version_ids) != set(facts.authority.policy_version_ids)
            or w.facts.initiation != facts.initiation
            for w in worlds
        )
    ):
        return aggregate("BLOCKED", ["FINITE_USER_CONTEXT_NOT_VERIFIED"], "NOT_EVALUATED")
    pairs = [(facts, baseline), *[(w.facts, r) for w, r in zip(worlds, outcomes, strict=True)]]
    fundamental = [
        result
        for fact, result in pairs
        if result.level == "BLOCKED" and not _financial_rejection(fact, result)
    ]
    if fundamental:
        return aggregate(
            "BLOCKED", [reason for r in fundamental for reason in r.reasons], "REJECTED"
        )
    invariant = _amount_invariant(facts.effect)
    if invariant is None or any(_amount_invariant(w.facts.effect) != invariant for w in worlds):
        return aggregate("BLOCKED", ["UNDECLARED_USER_VARIABLE_CHANGE"], "NOT_EVALUATED")
    if any(result.level == "ADVISE_ONLY" for _, result in pairs):
        return aggregate("ADVISE_ONLY", ["CANDIDATE_AUTHORITY_NOT_SUFFICIENT"], "NOT_EVALUATED")
    if all(result.level == "BLOCKED" for result in outcomes):
        return aggregate("BLOCKED", [reason for r in outcomes for reason in r.reasons], "REJECTED")
    if any(result.level == "BLOCKED" for _, result in pairs) or len(set(signatures.values())) > 1:
        financial: FinancialEvaluation = (
            "REJECTED" if any(r.level == "BLOCKED" for _, r in pairs) else "VERIFIED"
        )
        return aggregate(
            "ASK_ONCE",
            ["USER_CLARIFICATION_REQUIRED", "REPREPARE_AFTER_USER_ANSWER"],
            financial,
            "DIVERGENT",
        )
    if baseline.economic_signature not in signatures.values():
        return aggregate("BLOCKED", ["BASE_EFFECT_NOT_IN_CANDIDATE_SET"], "NOT_EVALUATED")
    asks = [r for _, r in pairs if r.level == "ASK_ONCE"]
    return baseline.model_copy(
        update={
            "level": "ASK_ONCE" if asks else "AUTO_EXECUTE",
            "execution_eligible": all(r.execution_eligible for _, r in pairs),
            "confirmation_required": bool(asks),
            "confirmation_satisfied": bool(asks) and all(r.confirmation_satisfied for r in asks),
            "reasons": sorted({reason for r in asks for reason in r.reasons}) or baseline.reasons,
            "uncertainty_status": "STABLE",
            "candidate_signatures": signatures,
        }
    )


def _financial_rejection(facts: AutonomyFacts, decision: AutonomyDecision) -> bool:
    return (
        not facts.hard_block_reasons
        and facts.validation is not None
        and facts.validation.status == "BLOCKED"
        and bool(decision.reasons)
        and set(decision.reasons).issubset(FINANCIAL_REJECTIONS)
    )


def _decision(
    facts: AutonomyFacts,
    level: AutonomyLevel,
    reasons: list[str],
    financial: FinancialEvaluation = "NOT_EVALUATED",
    *,
    eligible: bool = False,
) -> AutonomyDecision:
    return AutonomyDecision(
        algorithm_version=ALGORITHM_VERSION,
        level=level,
        execution_eligible=eligible,
        financial_evaluation=financial,
        reasons=sorted(set(reasons)),
        effect_hash=execution_effect_hash(facts.effect) if facts.effect is not None else None,
        economic_signature=economic_signature(facts.effect) if facts.effect is not None else None,
    )


def _single(facts: AutonomyFacts) -> AutonomyDecision:
    effect, checked = facts.effect, facts.validation
    problems: list[str] = []
    if facts.action_type not in SUPPORTED_ACTIONS:
        problems.append("UNSUPPORTED_ACTION")
    if not facts.source_evidence_ids or facts.source_issues:
        problems.append("SOURCE_EVIDENCE_INCOMPLETE")
    if facts.authority.status in {"MISSING_EVIDENCE", "STALE_VERSION"}:
        problems.append(facts.authority.status)
    if not facts.authority.evidence_ids:
        problems.append("AUTHORITY_EVIDENCE_MISSING")
    if facts.action_type == "TRANSFER_INTERNAL" and facts.initiation != "USER_EXPLICIT":
        problems.append("EXPLICIT_USER_TRANSFER_INTENT_REQUIRED")
    problems.extend(facts.hard_block_reasons)
    if facts.payee.status in {"AGENT_NEW", "MISSING_IDENTITY", "UNSUPPORTED"}:
        problems.append("PAYEE_" + facts.payee.status)
    if facts.payee.status == "USER_INITIATED_NEW":
        problems.append("UNSUPPORTED_PAYEE_RELATIONSHIP")
    if facts.action_type == "PAY_RECURRING":
        if facts.payee.status == "NOT_APPLICABLE" or not facts.payee.evidence_ids:
            problems.append("PAYEE_IDENTITY_EVIDENCE_MISSING")
    elif facts.payee.status != "NOT_APPLICABLE":
        problems.append("PAYEE_DOES_NOT_MATCH_ACTION")
    if effect is not None:
        if effect.user_id != facts.user_id or effect.action_type != facts.action_type:
            problems.append("EFFECT_IDENTITY_MISMATCH")
        if not effect.valid_from <= facts.as_of < effect.expires_at:
            problems.append("EFFECT_OUTSIDE_VALIDITY")
        if set(effect.policy_version_ids) != set(facts.authority.policy_version_ids):
            problems.append("EXACT_CURRENT_POLICY_REQUIRED")
        consent = facts.confirmation
        if consent is not None and not (
            consent.user_id == facts.user_id
            and consent.operation_id == effect.operation_id
            and consent.effect_hash == execution_effect_hash(effect)
            and effect.valid_from <= consent.confirmed_at <= facts.as_of < consent.expires_at
            and consent.expires_at <= effect.expires_at
        ):
            problems.append("CONFIRMATION_DOES_NOT_BIND_CURRENT_EFFECT")
        if checked is None:
            problems.append("FINANCIAL_VALIDATION_MISSING")
        elif checked.effect_hash != execution_effect_hash(effect):
            problems.append("FINANCIAL_VALIDATION_EFFECT_MISMATCH")
        elif checked.status in {"BLOCKED", "INSUFFICIENT_EVIDENCE"}:
            problems.extend(checked.reasons or [checked.status])
        elif checked.projected_boundary is None or checked.projected_snapshot is None:
            problems.append("FINANCIAL_PROJECTION_MISSING")
        elif checked.projected_snapshot.as_of != facts.as_of:
            problems.append("FINANCIAL_VALIDATION_CLOCK_MISMATCH")
        elif any(
            b is not None and b.status == "INSUFFICIENT_EVIDENCE"
            for b in (
                checked.baseline_boundary,
                checked.projected_boundary,
                checked.reservation_adjusted_baseline,
                checked.reservation_adjusted_boundary,
            )
        ):
            problems.append("SOURCE_EVIDENCE_INCOMPLETE")
    elif checked is not None:
        problems.append("FINANCIAL_VALIDATION_WITHOUT_EFFECT")
    if problems:
        return _decision(facts, "BLOCKED", problems, "REJECTED" if checked else "NOT_EVALUATED")
    if facts.authority.status == "OUTSIDE_AUTHORITY":
        return _decision(
            facts,
            "ADVISE_ONLY",
            facts.authority.reasons or ["OUTSIDE_AUTHORITY"],
            "VERIFIED" if checked is not None else "NOT_EVALUATED",
        )
    if effect is None:
        return _decision(facts, "BLOCKED", ["EXECUTABLE_EFFECT_MISSING"])
    confirmation_reasons = list(facts.confirmation_reasons)
    if checked is not None and checked.status == "CONFIRMATION_REQUIRED":
        confirmation_reasons.extend(checked.reasons or ["EXPLICIT_CONFIRMATION_REQUIRED"])
    if effect.action_type == "TRANSFER_INTERNAL":
        confirmation_reasons.append("EXPLICIT_TRANSFER_CONFIRMATION_REQUIRED")
    if effect.fee_cents or effect.loss_cents:
        confirmation_reasons.append("EXPLICIT_COST_CONFIRMATION_REQUIRED")
    if confirmation_reasons:
        satisfied = facts.confirmation is not None
        return _decision(
            facts,
            "ASK_ONCE",
            confirmation_reasons,
            "VERIFIED",
            eligible=satisfied and checked is not None and checked.status == "READY",
        ).model_copy(
            update={
                "confirmation_required": True,
                "confirmation_satisfied": satisfied,
            }
        )
    return _decision(
        facts, "AUTO_EXECUTE", ["CURRENT_AUTHORIZED_SAFE_EFFECT"], "VERIFIED", eligible=True
    )
