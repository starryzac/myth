"""Five-set risks through actual original financial calculations; synthetic only."""

from typing import Any

import pytest
from app.domain.autonomy_envelope import SET_NAMES, EnvelopeEvaluation, evaluate_envelope
from app.domain.autonomy_types import AuthorityAssessment, AutonomyFacts
from app.domain.boundary_types import SourceIssue
from app.domain.execution import revalidate_execution
from app.tests.test_autonomy_domain import authorized_payment
from app.tests.test_execution_domain import EVIDENCE, VERSION, context, grant, recurring, transfer


def proven_payment() -> AutonomyFacts:
    return authorized_payment().model_copy(update={"source_context_hash": "a" * 64})


def memberships(result: EnvelopeEvaluation) -> dict[str, str]:
    assert tuple(item.name for item in result.sets) == SET_NAMES
    return {item.name: item.membership for item in result.sets}


def test_original_auto_requires_all_five_actual_sets_and_retains_original_effect() -> None:
    facts = proven_payment()
    result = evaluate_envelope(facts, audit_verified=True)
    assert set(memberships(result).values()) == {"IN"}
    assert result.intersection == "IN" and result.automatic_execution_allowed
    assert result.execution_eligible and result.original_decision is not None
    assert result.original_decision.level == "AUTO_EXECUTE"
    assert result.authority_granted is False
    assert result.sets[0].source_context_hash == facts.source_context_hash
    assert result.sets[0].evidence_ids == facts.source_evidence_ids


@pytest.mark.parametrize("confirmed", [False, True])
def test_financially_safe_explicit_transfer_never_becomes_automatic(confirmed: bool) -> None:
    effect = transfer()
    confirmation = grant(effect) if confirmed else None
    facts = proven_payment().model_copy(
        update={
            "action_type": effect.action_type,
            "effect": effect,
            "initiation": "USER_EXPLICIT",
            "authority": AuthorityAssessment(status="AUTHORIZED", evidence_ids=[EVIDENCE]),
            "payee": {"status": "NOT_APPLICABLE"},
            "validation": revalidate_execution(effect, context(), confirmation=confirmation),
            "confirmation": confirmation,
        }
    )
    result = evaluate_envelope(facts, audit_verified=True)
    sets = memberships(result)
    assert sets["FinanciallySafeSet"] == sets["LiquidityCompatibleSet"] == "IN"
    assert sets["UserAuthorizedSet"] == ("IN" if confirmed else "OUT")
    assert result.execution_eligible is confirmed
    assert result.automatic_execution_allowed is False
    assert result.original_decision is not None and result.original_decision.level == "ASK_ONCE"


def test_authority_out_does_not_become_execution_when_financial_projection_is_safe() -> None:
    facts = proven_payment().model_copy(
        update={
            "authority": AuthorityAssessment(
                status="OUTSIDE_AUTHORITY",
                policy_version_ids=[VERSION],
                evidence_ids=[EVIDENCE],
                reasons=["ORIGINAL_PERMISSION_DENIED"],
            )
        }
    )
    result = evaluate_envelope(facts, audit_verified=True)
    assert memberships(result)["UserAuthorizedSet"] == "OUT"
    assert result.intersection == "OUT" and not result.execution_eligible


def test_actual_financial_rejection_does_not_claim_unrun_liquidity_branch() -> None:
    facts = proven_payment()
    assert facts.effect is not None
    effect = facts.effect.model_copy(update={"payee_id": "wrong-original-payee"})
    validation = revalidate_execution(
        effect, context().model_copy(update={"versions": [recurring()]})
    )
    assert validation.status == "BLOCKED" and validation.reasons == ["PAYEE_OR_POLICY_MISMATCH"]
    result = evaluate_envelope(
        facts.model_copy(update={"effect": effect, "validation": validation}), audit_verified=True
    )
    sets = memberships(result)
    assert sets["FinanciallySafeSet"] == "OUT"
    assert sets["LiquidityCompatibleSet"] == sets["UserAuthorizedSet"] == "UNKNOWN"
    assert result.execution_eligible is False


def test_actual_unavailable_cash_rejects_liquidity_and_never_fabricates_safe_amount() -> None:
    facts = proven_payment()
    assert facts.effect is not None
    state = context()
    state = state.model_copy(
        update={
            "versions": [recurring()],
            "snapshot": state.snapshot.model_copy(
                update={
                    "cash_accounts": [
                        row.model_copy(update={"balance_cents": 0})
                        for row in state.snapshot.cash_accounts
                    ]
                }
            ),
        }
    )
    validation = revalidate_execution(facts.effect, state)
    assert validation.reasons == ["UNAVAILABLE_SOURCE_CASH"]
    result = evaluate_envelope(
        facts.model_copy(update={"validation": validation}), audit_verified=True
    )
    assert memberships(result)["LiquidityCompatibleSet"] == "OUT"
    assert result.intersection == "OUT" and not result.execution_eligible


@pytest.mark.parametrize(
    "change",
    [
        {"source_evidence_ids": []},
        {"source_context_hash": None},
        {"source_issues": [SourceIssue(code="BANK_MISMATCH", entity_type="bank")]},
        {"authority": AuthorityAssessment(status="MISSING_EVIDENCE")},
    ],
)
def test_missing_original_proof_rejects_evidence_and_keeps_finance_unknown(
    change: dict[str, Any],
) -> None:
    result = evaluate_envelope(proven_payment().model_copy(update=change), audit_verified=True)
    sets = memberships(result)
    assert sets["EvidenceSufficientSet"] == "OUT"
    assert sets["FinanciallySafeSet"] == "UNKNOWN"
    assert not result.execution_eligible


def test_current_typed_audit_failure_is_not_replaced_by_successful_money_strings() -> None:
    result = evaluate_envelope(proven_payment(), audit_verified=False)
    assert memberships(result)["EvidenceSufficientSet"] == "OUT"
    assert not result.automatic_execution_allowed


def test_unsupported_action_rejects_supported_set_without_creating_effect_or_validation() -> None:
    facts = proven_payment().model_copy(
        update={"action_type": "PAY_DATED_EXPENSE", "effect": None, "validation": None}
    )
    result = evaluate_envelope(facts, audit_verified=True)
    assert memberships(result)["SupportedActionSet"] == "OUT"
    assert memberships(result)["FinanciallySafeSet"] == "UNKNOWN"
    assert not result.execution_eligible


@pytest.mark.parametrize(
    "change",
    [
        {"validation": None},
        {"hard_block_reasons": ["UNRECOGNIZED_ORIGINAL_GATE_FAILURE"]},
        {"effect": None, "validation": None},
    ],
)
def test_no_missing_original_final_gate_can_be_promoted_by_other_in_sets(
    change: dict[str, Any],
) -> None:
    result = evaluate_envelope(proven_payment().model_copy(update=change), audit_verified=True)
    assert result.intersection != "IN" and not result.execution_eligible
