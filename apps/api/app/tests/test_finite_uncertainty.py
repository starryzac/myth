"""Finite domain/minimax risks using the original deterministic engine; synthetic only."""

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import EvidenceItem
from app.domain.autonomy import classify_autonomy, economic_signature
from app.domain.autonomy_types import AuthorityAssessment, AutonomyFacts
from app.domain.boundary_types import CashFact
from app.domain.execution import revalidate_execution
from app.domain.execution_types import CashUse
from app.domain.finite_uncertainty import (
    AccountChoice,
    FiniteChoice,
    FinitePlanningVariable,
    IntentChoice,
    MoneyChoice,
    PlanningEngineOutcome,
    complete_planning_signature,
    evaluate_finite_planning,
)
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionIntent, PaymentIntent, TransferIntent
from app.services.finite_uncertainty import FinitePlanningRequest, _declarations
from app.tests.test_execution_domain import (
    EVIDENCE,
    NOW,
    USER,
    A,
    B,
    context,
    goal_example,
    loss_example,
    purchase_example,
    transfer,
)
from pydantic import ValidationError
from sqlalchemy.orm import Session


def amount_variable(
    identifier: str = "amount", amounts: tuple[int, ...] = (100, 200)
) -> FinitePlanningVariable:
    return FinitePlanningVariable(
        variable_id=identifier,
        field="TRANSFER_AMOUNT",
        choices=[
            FiniteChoice(key=f"v{index}", value=MoneyChoice(kind="money", amount_cents=value))
            for index, value in enumerate(amounts)
        ],
    )


def account_variable(
    field: Any = "TRANSFER_DESTINATION",
    identifier: str = "destination",
    ids: tuple[UUID, ...] = (B, UUID(int=55)),
) -> FinitePlanningVariable:
    return FinitePlanningVariable(
        variable_id=identifier,
        field=field,
        choices=[
            FiniteChoice(key=f"v{index}", value=AccountChoice(kind="account", account_id=value))
            for index, value in enumerate(ids)
        ],
    )


def intent() -> TransferIntent:
    return TransferIntent(
        kind="transfer_internal", source_account_id=A, destination_account_id=B, amount_cents=100
    )


def original_engine(choice: ActionIntent) -> PlanningEngineOutcome:
    if not isinstance(choice, TransferIntent):
        return PlanningEngineOutcome(
            status="UNKNOWN", reasons=["TEST_HAS_NO_SELECTED_ACTION_ADAPTER"]
        )
    effect = transfer().model_copy(
        update={
            "amount_cents": choice.amount_cents,
            "cash_uses": [
                CashUse(account_id=choice.source_account_id, amount_cents=choice.amount_cents)
            ],
            "destination_account_id": choice.destination_account_id,
        }
    )
    state = context()
    state = state.model_copy(
        update={
            "snapshot": state.snapshot.model_copy(
                update={
                    "cash_accounts": [
                        *state.snapshot.cash_accounts,
                        CashFact(
                            account_id=UUID(int=55),
                            account_type="CASH",
                            balance_cents=0,
                            observed_at=NOW,
                        ),
                    ]
                }
            )
        }
    )
    validation = revalidate_execution(effect, state)
    facts = AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type="TRANSFER_INTERNAL",
        initiation="USER_EXPLICIT",
        authority=AuthorityAssessment(status="AUTHORIZED", evidence_ids=[EVIDENCE]),
        effect=effect,
        validation=validation,
        source_evidence_ids=[EVIDENCE],
        source_context_hash="a" * 64,
    )
    decision = classify_autonomy(facts)
    return PlanningEngineOutcome(
        status="KNOWN",
        decision=decision,
        effect=effect,
        validation=validation,
        signature=complete_planning_signature(effect, decision, validation, "AUTHORIZED"),
        source_context_hash="a" * 64,
        source_evidence_ids=[EVIDENCE],
    )


def test_every_world_calls_original_engine_and_all_amount_ownership_results_remain() -> None:
    calls = []

    def run(choice: ActionIntent) -> PlanningEngineOutcome:
        calls.append(choice)
        return original_engine(choice)

    result = evaluate_finite_planning(intent(), [account_variable(), amount_variable()], run)
    assert len(calls) == result.expected_world_count == result.evaluated_world_count == 4
    assert result.known_world_count == 4 and result.unknown_or_unsupported_world_count == 0
    assert len(result.distinct_signatures) == 4 and result.status == "DIVERGENT"
    assert (
        result.should_ask
        and result.question is not None
        and result.question.variable_id == "amount"
    )
    assert all(
        world.outcome.decision is not None and world.outcome.decision.level == "ASK_ONCE"
        for world in result.worlds
    )
    assert all(
        world.outcome.decision is not None and not world.outcome.decision.confirmation_satisfied
        for world in result.worlds
    )
    assert result.execution_eligible is False and result.authority_granted is False
    assert result.probability_model == "NONE"
    assert {
        world.outcome.effect.amount_cents for world in result.worlds if world.outcome.effect
    } == {100, 200}
    assert {
        world.outcome.effect.destination_account_id
        for world in result.worlds
        if world.outcome.effect
    } == {B, UUID(int=55)}


def test_minimax_matches_independent_complete_partition_enumeration_and_fixed_tie() -> None:
    result = evaluate_finite_planning(
        intent(),
        [amount_variable("z", (100, 200, 300)), account_variable(identifier="a")],
        original_engine,
    )
    independent: dict[str, int] = {}
    for variable in ("z", "a"):
        buckets: dict[str, set[str | None]] = {}
        for world in result.worlds:
            buckets.setdefault(world.assignments[variable], set()).add(world.outcome.signature)
        independent[variable] = max(len(values) for values in buckets.values())
    assert independent == {"z": 2, "a": 3}
    assert result.question is not None and result.question.variable_id == min(
        independent, key=lambda name: (independent[name], name)
    )
    assert result.question.worst_residual_signature_count == 2
    assert result.full_confirmation_baseline_question_count == 2
    tie = evaluate_finite_planning(
        intent(), [amount_variable("z"), account_variable(identifier="a")], original_engine
    )
    assert tie.question is not None and tie.question.variable_id == "a"
    assert tie.question.partitions and all(
        part.world_keys and part.signatures for part in tie.question.partitions
    )


def test_stability_uses_actual_full_outcomes_not_amount_or_decision_labels() -> None:
    original = original_engine(intent())
    # A synthetic selector returns the identical original engine outcome in both
    # worlds, exercising aggregation only; this is not public service evidence.
    stable = evaluate_finite_planning(intent(), [amount_variable()], lambda _: original)
    assert stable.status == "STABLE" and stable.stable and not stable.should_ask
    assert stable.question is None
    divergent = evaluate_finite_planning(intent(), [amount_variable()], original_engine)
    assert divergent.status == "DIVERGENT"
    assert (
        len({world.outcome.decision.level for world in divergent.worlds if world.outcome.decision})
        == 1
    )
    assert len(divergent.distinct_signatures) == 2


def test_one_unknown_world_prevents_false_stability_and_optimal_question() -> None:
    def partial(choice: ActionIntent) -> PlanningEngineOutcome:
        assert isinstance(choice, TransferIntent)
        return (
            PlanningEngineOutcome(status="UNKNOWN", reasons=["MISSING_BANK_SOURCE"])
            if choice.amount_cents == 200
            else original_engine(choice)
        )

    result = evaluate_finite_planning(intent(), [amount_variable()], partial)
    assert result.expected_world_count == result.evaluated_world_count == 2
    assert result.known_world_count == result.unknown_or_unsupported_world_count == 1
    assert result.status == "UNKNOWN" and not result.complete_within_declared_domain
    assert result.stable is result.should_ask is None and result.question is None


def test_unsupported_cross_action_variable_keeps_complete_cartesian_denominator() -> None:
    action = FinitePlanningVariable(
        variable_id="action",
        field="ACTION_INTENT",
        choices=[
            FiniteChoice(key="transfer", value=IntentChoice(kind="intent", intent=intent())),
            FiniteChoice(
                key="pay",
                value=IntentChoice(
                    kind="intent", intent=PaymentIntent(kind="pay_recurring", policy_id=UUID(int=9))
                ),
            ),
        ],
    )
    result = evaluate_finite_planning(intent(), [action, amount_variable()], original_engine)
    assert result.expected_world_count == result.evaluated_world_count == 4
    assert result.unknown_or_unsupported_world_count == 2 and result.known_world_count == 2
    assert sum(world.outcome.status == "UNSUPPORTED" for world in result.worlds) == 2
    assert result.question is None and result.stable is None


def test_incomplete_or_capacity_keeps_all_declared_worlds_unknown_without_engine_calls() -> None:
    calls: list[ActionIntent] = []

    def count(value: ActionIntent) -> PlanningEngineOutcome:
        calls.append(value)
        return original_engine(value)

    incomplete = amount_variable().model_copy(update={"completeness": "INCOMPLETE"})
    result = evaluate_finite_planning(intent(), [incomplete], count)
    assert (
        not calls and result.expected_world_count == result.unknown_or_unsupported_world_count == 2
    )
    variables = [
        amount_variable(amounts=tuple(range(1, 9))),
        account_variable(ids=tuple(UUID(int=100 + i) for i in range(8))),
        account_variable(
            field="TRANSFER_SOURCE",
            identifier="source",
            ids=tuple(UUID(int=200 + i) for i in range(8)),
        ),
    ]
    result = evaluate_finite_planning(intent(), variables, count)
    assert not calls and result.status == "CAPACITY_EXCEEDED"
    assert result.expected_world_count == result.unknown_or_unsupported_world_count == 512
    assert result.evaluated_world_count == 0 and result.should_ask is None


def test_known_financial_rejection_does_not_become_a_source_missing_success() -> None:
    result = evaluate_finite_planning(
        intent(), [amount_variable(amounts=(2000, 3000))], original_engine
    )
    assert result.status == "ALL_WORLDS_BLOCKED" and not result.should_ask
    assert result.complete_within_declared_domain and result.known_world_count == 2
    assert all(
        world.outcome.validation and world.outcome.validation.status == "BLOCKED"
        for world in result.worlds
    )


def test_original_signature_contains_type_goal_risk_cost_and_arrival_consequences() -> None:
    base = transfer()
    assert economic_signature(base) != economic_signature(
        base.model_copy(
            update={"amount_cents": 301, "cash_uses": [CashUse(account_id=A, amount_cents=301)]}
        )
    )
    goal, _ = goal_example()
    assert economic_signature(goal) != economic_signature(
        goal.model_copy(update={"goal_id": UUID(int=666)})
    )
    purchase, _ = purchase_example()
    assert purchase.latest_arrival_at is not None
    assert economic_signature(purchase) != economic_signature(
        purchase.model_copy(
            update={"latest_arrival_at": purchase.latest_arrival_at + timedelta(seconds=1)}
        )
    )
    assert economic_signature(base) != economic_signature(goal)
    # Cost/risk contracts remain original fields; no user cost grant is introduced.
    loss, _ = loss_example(loss=50)
    larger_loss, _ = loss_example(loss=51)
    assert economic_signature(loss) != economic_signature(larger_loss)
    fee = loss.model_copy(
        update={
            "fee_cents": 1,
            "net_cents": loss.net_cents - 1 if loss.net_cents is not None else None,
        }
    )
    assert economic_signature(loss) != economic_signature(fee)
    later, _ = loss_example(delay=1)
    assert economic_signature(loss) != economic_signature(later)


@pytest.mark.parametrize(
    "extra", ["confidence", "probability", "bank_balance_cents", "grant", "now", "worlds"]
)
def test_variable_cannot_claim_probability_bank_fact_grant_or_clock(extra: str) -> None:
    raw = amount_variable().model_dump(mode="json")
    with pytest.raises(ValidationError):
        FinitePlanningVariable.model_validate_json(json.dumps({**raw, extra: True}))


def test_duplicate_values_fields_wrong_kind_and_boolean_money_rejected() -> None:
    with pytest.raises(ValidationError):
        amount_variable(amounts=(100, 100))
    with pytest.raises(ValidationError):
        MoneyChoice(kind="money", amount_cents=True)
    with pytest.raises(ValidationError):
        account_variable(field="TRANSFER_AMOUNT")
    with pytest.raises(ValidationError):
        FinitePlanningRequest(
            base_action_id=UUID(int=1), variables=[amount_variable("a"), amount_variable("b")]
        )
    with pytest.raises(ValidationError):
        FinitePlanningVariable.model_validate(
            {"variable_id": "bank", "field": "BANK_BALANCE", "choices": amount_variable().choices}
        )


def test_known_world_cannot_be_only_a_success_label_or_signature_string() -> None:
    with pytest.raises(ValidationError):
        PlanningEngineOutcome(status="KNOWN", signature="a" * 64)


def declaration_row(variable: FinitePlanningVariable) -> EvidenceItem:
    content = {
        "protocol": "full-finite-user-variable-v1",
        "simulation": True,
        "user_id": str(USER),
        "variable": variable.model_dump(mode="json", exclude={"source", "evidence_id"}),
    }
    return EvidenceItem(
        id=variable.evidence_id,
        user_id=USER,
        evidence_level="USER_DECLARED",
        source_type="FULL_FINITE_USER_VARIABLE",
        source_ref="original-declared-variable",
        content=content,
        content_hash=configuration_hash(content),
        status="VALID",
        observed_at=NOW,
        valid_from=NOW,
    )


def test_user_request_remains_a_request_and_has_no_evidence_or_bank_grade() -> None:
    def forbidden(*_: Any) -> None:
        raise AssertionError("USER_REQUEST declaration must not invent an Evidence query")

    session = cast(Session, SimpleNamespace(get=forbidden))
    ref = _declarations(session, USER, [amount_variable()], NOW, "c" * 64)[0]
    assert ref.status == "READ_ONLY_USER_REQUEST" and ref.source_ref == f"request:{'c' * 64}#amount"
    assert ref.evidence_id is None and ref.original_evidence_level is None
    assert ref.original_content_hash is None
    assert ref.authorization is False and ref.bank_fact is False


@pytest.mark.parametrize(
    "bad", [None, "bank_level", "owner", "hash", "choices", "expired", "future", "missing"]
)
def test_registered_source_must_be_exact_original_user_declaration_not_bank_fact(
    bad: str | None,
) -> None:
    variable = amount_variable().model_copy(
        update={"source": "REGISTERED_EVIDENCE", "evidence_id": UUID(int=901)}
    )
    row = declaration_row(variable)
    if bad == "bank_level":
        row.evidence_level = "BANK_CONFIRMED"
    elif bad == "owner":
        row.user_id = UUID(int=999)
    elif bad == "hash":
        row.content_hash = "f" * 64
    elif bad == "choices":
        row.content["variable"]["choices"][0]["value"]["amount_cents"] = 101
        row.content_hash = configuration_hash(row.content)
    elif bad == "expired":
        row.valid_to = NOW
    elif bad == "future":
        row.observed_at = NOW + timedelta(seconds=1)
    session = cast(Session, SimpleNamespace(get=lambda *_: None if bad == "missing" else row))
    ref = _declarations(session, USER, [variable], NOW, "c" * 64)[0]
    assert ref.status == ("VERIFIED_DECLARATION" if bad is None else "MISSING_OR_INVALID")
    assert ref.authorization is False and ref.bank_fact is False
    if bad in {"owner", "missing"}:
        assert ref.source_ref == "MISSING" and ref.original_content_hash is None
