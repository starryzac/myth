"""Four-level public seam using actual financial revalidation, without component mocks."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.autonomy import classify_autonomy, economic_signature
from app.domain.autonomy_types import (
    AuthorityAssessment,
    AutonomyFacts,
    AutonomyWorld,
    FiniteUserVariable,
    PayeeAssessment,
)
from app.domain.boundary_types import CashFact, SourceIssue
from app.domain.execution import revalidate_execution
from app.tests.test_execution_domain import (
    EVIDENCE,
    NOW,
    USER,
    VERSION,
    context,
    grant,
    loss_example,
    payment,
    purchase_example,
    recurring,
    transfer,
)


def authorized_payment() -> AutonomyFacts:
    effect = payment()
    state = context().model_copy(update={"versions": [recurring()]})
    return AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type=effect.action_type,
        initiation="CONFIRMED_POLICY",
        authority=AuthorityAssessment(
            status="AUTHORIZED", policy_version_ids=[VERSION], evidence_ids=[EVIDENCE]
        ),
        effect=effect,
        validation=revalidate_execution(effect, state),
        payee=PayeeAssessment(status="EXISTING_CONFIRMED", evidence_ids=[EVIDENCE]),
        source_evidence_ids=[EVIDENCE],
    )


def test_complete_current_safe_recurring_payment_is_auto_executable() -> None:
    facts = authorized_payment()
    assert facts.validation is not None and facts.validation.status == "READY"
    result = classify_autonomy(facts)
    assert result.level == "AUTO_EXECUTE"
    assert result.execution_eligible is True
    assert result.financial_evaluation == "VERIFIED"
    assert result.confirmation_required is False


@pytest.mark.parametrize(
    "change",
    [
        {"source_evidence_ids": []},
        {"source_issues": [SourceIssue(code="CONFLICT", entity_type="cash")]},
        {"authority": AuthorityAssessment(status="STALE_VERSION", evidence_ids=[EVIDENCE])},
        {"authority": AuthorityAssessment(status="MISSING_EVIDENCE")},
        {"authority": AuthorityAssessment(status="AUTHORIZED", policy_version_ids=[VERSION])},
        {"action_type": "SEND_EXTERNAL"},
        {"payee": PayeeAssessment(status="AGENT_NEW")},
        {"payee": PayeeAssessment(status="MISSING_IDENTITY")},
        {"payee": PayeeAssessment(status="UNSUPPORTED")},
        {"user_id": UUID(int=900)},
        {"validation": None},
        {"hard_block_reasons": ["LIQUIDITY_RISK"]},
    ],
)
def test_non_user_repairable_gates_block_even_an_otherwise_safe_payment(
    change: dict[str, Any],
) -> None:
    result = classify_autonomy(authorized_payment().model_copy(update=change))
    assert result.level == "BLOCKED"
    assert result.execution_eligible is False
    assert result.reasons


def test_full_known_permission_denial_without_effect_is_only_unevaluated_advice() -> None:
    facts = authorized_payment().model_copy(
        update={
            "effect": None,
            "validation": None,
            "authority": AuthorityAssessment(
                status="OUTSIDE_AUTHORITY", evidence_ids=[EVIDENCE], reasons=["AMOUNT_CAP"]
            ),
        }
    )
    result = classify_autonomy(facts)
    assert result.level == "ADVISE_ONLY"
    assert result.execution_eligible is False
    assert result.financial_evaluation == "NOT_EVALUATED"
    assert result.effect_hash is None
    assert result.economic_signature is None


@pytest.mark.parametrize("confirmed", [False, True])
def test_explicit_transfer_stays_ask_after_exact_confirmation(confirmed: bool) -> None:
    effect = transfer()
    consent = grant(effect) if confirmed else None
    facts = AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type=effect.action_type,
        initiation="USER_EXPLICIT",
        effect=effect,
        validation=revalidate_execution(effect, context(), confirmation=consent),
        authority=AuthorityAssessment(status="AUTHORIZED", evidence_ids=[EVIDENCE]),
        source_evidence_ids=[EVIDENCE],
        confirmation=consent,
    )
    result = classify_autonomy(facts)
    assert result.level == "ASK_ONCE"
    assert result.execution_eligible is confirmed
    assert result.confirmation_required is True
    assert result.confirmation_satisfied is confirmed


def test_manual_periodic_payment_preserves_confirmation_requirement_after_grant() -> None:
    facts = authorized_payment()
    assert facts.effect is not None
    result = classify_autonomy(
        facts.model_copy(
            update={
                "confirmation_reasons": ["EXPLICIT_PAYMENT_CONFIRMATION_REQUIRED"],
                "confirmation": grant(facts.effect),
            }
        )
    )
    assert result.level == "ASK_ONCE"
    assert result.execution_eligible is True
    rejected = classify_autonomy(
        facts.model_copy(
            update={
                "confirmation_reasons": ["EXPLICIT_PAYMENT_CONFIRMATION_REQUIRED"],
                "confirmation": grant(facts.effect).model_copy(update={"effect_hash": "f" * 64}),
            }
        )
    )
    assert rejected.level == "BLOCKED"
    assert rejected.execution_eligible is False


def test_agent_cannot_invent_a_transfer_intent_even_with_safe_numbers() -> None:
    effect = transfer()
    facts = AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type=effect.action_type,
        initiation="AGENT_GENERATED",
        effect=effect,
        validation=revalidate_execution(effect, context()),
        authority=AuthorityAssessment(status="AUTHORIZED", evidence_ids=[EVIDENCE]),
        source_evidence_ids=[EVIDENCE],
    )
    assert classify_autonomy(facts).level == "BLOCKED"


def test_purchase_signature_ignores_generated_operation_and_new_position_but_not_terms() -> None:
    effect, _ = purchase_example()
    derived = effect.model_copy(
        update={
            "operation_id": UUID(int=800),
            "business_key": "another-generated-key",
            "position_id": UUID(int=801),
            "valid_from": NOW + timedelta(seconds=1),
            "expires_at": effect.expires_at + timedelta(seconds=1),
        }
    )
    assert economic_signature(effect) == economic_signature(derived)
    assert economic_signature(effect) != economic_signature(
        effect.model_copy(
            update={
                "latest_arrival_at": effect.latest_arrival_at + timedelta(seconds=1)
                if effect.latest_arrival_at is not None
                else None,
            }
        )
    )
    assert economic_signature(effect) != economic_signature(
        effect.model_copy(
            update={
                "product_version_number": 3,
            }
        )
    )


def test_redemption_signature_keeps_original_position_cost_and_arrival_not_quote_identity() -> None:
    effect, _ = loss_example()
    assert economic_signature(effect) == economic_signature(
        effect.model_copy(
            update={
                "quote_id": UUID(int=802),
                "operation_id": UUID(int=803),
            }
        )
    )
    assert economic_signature(effect) != economic_signature(
        effect.model_copy(
            update={
                "position_id": UUID(int=804),
            }
        )
    )
    assert effect.net_cents is not None
    assert economic_signature(effect) != economic_signature(
        effect.model_copy(
            update={
                "loss_cents": effect.loss_cents + 1,
                "net_cents": effect.net_cents - 1,
            }
        )
    )


def transfer_facts(amount: int = 300) -> AutonomyFacts:
    effect = transfer()
    effect = effect.model_copy(
        update={
            "amount_cents": amount,
            "cash_uses": [effect.cash_uses[0].model_copy(update={"amount_cents": amount})],
        }
    )
    return AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type=effect.action_type,
        initiation="USER_EXPLICIT",
        effect=effect,
        validation=revalidate_execution(effect, context()),
        authority=AuthorityAssessment(status="AUTHORIZED", evidence_ids=[EVIDENCE]),
        source_evidence_ids=[EVIDENCE],
        source_context_hash="a" * 64,
    )


def variable(*facts: AutonomyFacts) -> FiniteUserVariable:
    return FiniteUserVariable(
        variable_id="requested-amount",
        kind="USER_PREFERENCE",
        completeness="COMPLETE",
        evidence_ids=[EVIDENCE],
        source_context_hash="a" * 64,
        worlds=[AutonomyWorld(candidate_key=str(i), facts=f) for i, f in enumerate(facts)],
    )


def test_stable_worlds_preserve_base_transfer_confirmation_requirement() -> None:
    facts = transfer_facts()
    result = classify_autonomy(facts, variable(facts, facts))
    assert result.level == "ASK_ONCE"
    assert result.uncertainty_status == "STABLE"
    assert result.execution_eligible is False
    assert result.effect_hash is not None


@pytest.mark.parametrize("other_amount", [600, 1200])
def test_user_amount_divergence_or_safe_unsafe_split_requires_reprepare(other_amount: int) -> None:
    facts = transfer_facts()
    candidates = variable(facts, transfer_facts(other_amount))
    result = classify_autonomy(facts, candidates)
    assert result.level == "ASK_ONCE"
    assert result.uncertainty_status == "DIVERGENT"
    assert result.execution_eligible is False
    assert result.effect_hash is None
    assert result.economic_signature is None
    assert result == classify_autonomy(
        facts,
        candidates.model_copy(
            update={
                "worlds": list(reversed(candidates.worlds)),
            }
        ),
    )


def test_all_financially_rejected_worlds_remain_blocked() -> None:
    facts = transfer_facts(1200)
    result = classify_autonomy(facts, variable(facts, transfer_facts(1300)))
    assert result.level == "BLOCKED"
    assert result.execution_eligible is False


@pytest.mark.parametrize(
    "change",
    [
        {"source_context_hash": "b" * 64},
        {"user_id": UUID(int=99)},
        {"as_of": NOW + timedelta(seconds=1)},
        {"source_issues": [SourceIssue(code="BANK_CONFLICT", entity_type="cash")]},
    ],
)
def test_candidate_bank_identity_or_context_conflict_is_not_user_choice(
    change: dict[str, Any],
) -> None:
    facts = transfer_facts()
    result = classify_autonomy(facts, variable(facts, facts.model_copy(update=change)))
    assert result.level == "BLOCKED"
    assert result.uncertainty_status == "BLOCKED"
    assert result.execution_eligible is False


def test_amount_variable_cannot_smuggle_a_different_destination_in_same_bank_context() -> None:
    third = UUID(int=700)
    state = context()
    state = state.model_copy(
        update={
            "snapshot": state.snapshot.model_copy(
                update={
                    "cash_accounts": [
                        *state.snapshot.cash_accounts,
                        CashFact(
                            account_id=third,
                            account_type="CASH",
                            balance_cents=0,
                            observed_at=NOW,
                        ),
                    ],
                }
            )
        }
    )
    first = transfer_facts()
    assert first.effect is not None
    first = first.model_copy(update={"validation": revalidate_execution(first.effect, state)})
    assert first.effect is not None
    effect = first.effect.model_copy(update={"destination_account_id": third})
    second = first.model_copy(
        update={
            "effect": effect,
            "validation": revalidate_execution(effect, state),
        }
    )
    result = classify_autonomy(first, variable(first, second))
    assert result.level == "BLOCKED"
    assert "UNDECLARED_USER_VARIABLE_CHANGE" in result.reasons


def test_duplicate_source_evidence_is_rejected_as_malformed_internal_input() -> None:
    facts = authorized_payment().model_copy(update={"source_evidence_ids": [EVIDENCE, EVIDENCE]})
    with pytest.raises(ValueError):
        classify_autonomy(facts)


@pytest.mark.parametrize("loss,confirmed", [(0, False), (50, False), (50, True)])
def test_t1_recovery_does_not_confuse_actual_risk_with_permission_to_improve_it(
    loss: int,
    confirmed: bool,
) -> None:
    effect, state = loss_example(delay=1, loss=loss)
    consent = grant(effect) if confirmed else None
    checked = revalidate_execution(effect, state, confirmation=consent)
    assert checked.baseline_boundary.status == "LIQUIDITY_RISK"
    assert checked.projected_boundary is not None
    assert checked.projected_boundary.status == "LIQUIDITY_RISK"
    facts = AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type=effect.action_type,
        initiation="CONFIRMED_POLICY",
        effect=effect,
        validation=checked,
        confirmation=consent,
        authority=AuthorityAssessment(
            status="AUTHORIZED",
            policy_version_ids=[VERSION],
            evidence_ids=[EVIDENCE],
        ),
        source_evidence_ids=[EVIDENCE],
    )
    result = classify_autonomy(facts)
    assert result.level == ("ASK_ONCE" if loss else "AUTO_EXECUTE")
    assert result.execution_eligible is (not loss or confirmed)
    assert result.financial_evaluation == "VERIFIED"


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "BANK_FACT"},
        {"completeness": "INCOMPLETE"},
        {"evidence_ids": []},
    ],
)
def test_finite_bank_fact_or_incomplete_candidate_proof_is_never_a_user_question(
    change: dict[str, Any],
) -> None:
    facts = transfer_facts()
    result = classify_autonomy(facts, variable(facts, facts).model_copy(update=change))
    assert result.level == "BLOCKED"
    assert result.execution_eligible is False


@pytest.mark.parametrize("count", [0, 1, 9])
def test_finite_candidate_resource_bounds_are_strict(count: int) -> None:
    facts = transfer_facts()
    with pytest.raises(ValueError):
        variable(*([facts] * count))


def test_unknown_fields_and_boolean_money_cannot_enter_through_model_copy() -> None:
    facts = transfer_facts()
    with pytest.raises(ValueError):
        AutonomyFacts.model_validate({**facts.model_dump(), "authorized": True})
    assert facts.effect is not None
    with pytest.raises(ValueError):
        classify_autonomy(
            facts.model_copy(
                update={
                    "effect": facts.effect.model_copy(update={"amount_cents": True}),
                }
            )
        )
