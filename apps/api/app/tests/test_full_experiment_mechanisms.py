"""Synthetic direct mechanism risks; not seven-arm runtime or financial measurements."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.domain.full_experiment_mechanisms import (
    Candidate,
    ConstraintFacts,
    MechanismInput,
    Question,
    Rule,
    World,
    decide_full_mechanism,
    value_digest,
)
from pydantic import ValidationError

OWNER = UUID(int=1)
EPOCH = UUID(int=2)
VERSION = UUID(int=3)
NOW = datetime(2026, 10, 6, tzinfo=UTC)


def candidate(identity: str = "safe", **changes: object) -> Candidate:
    body: dict[str, object] = {
        "candidate_id": identity,
        "user_id": OWNER,
        "epoch_id": EPOCH,
        "kind": "PURCHASE_ASSET",
        "amount_cents": 300000,
        "cash_debit_cents": 300000,
        "authority_cap_cents": 1000000,
        "policy_version_id": VERSION,
        "goal_allocation_permitted": True,
        "evidence_level": "BANK_CONFIRMED",
        "required_evidence_level": "BANK_CONFIRMED",
        "available_at": NOW,
        "funds_use_at": NOW + timedelta(days=30),
        "loss_cents": 0,
        "simulated_annual_yield_basis_points": 100,
        "comparison_days": 30,
        "confirmation_required": False,
        "model_confidence_basis_points": 9500,
        "model_confidence_original_sha256": "a" * 64,
        "original_refs": ["b" * 64],
    }
    body.update(changes)
    return Candidate.model_validate(body)


def original(rows: list[Candidate] | None = None, **changes: object) -> MechanismInput:
    body: dict[str, object] = {
        "user_id": OWNER,
        "epoch_id": EPOCH,
        "opportunity_id": "op-1",
        "as_of": NOW,
        "source_status": "CURRENT_COMPLETE",
        "source_inventory_sha256": "c" * 64,
        "source_refs": ["d" * 64],
        "facts": ConstraintFacts(
            settled_cash_cents=1000000,
            active_reserved_cents=100000,
            hard_obligation_cents=100000,
            emergency_cents=100000,
            dynamic_living_cents=200000,
            registered_static_living_cents=0,
            other_goal_protection_cents=200000,
            current_policy_version_id=VERSION,
        ),
        "candidates": rows if rows is not None else [candidate()],
        "worlds": [
            World(
                world_id="w0", complete_action_signature_sha256="e" * 64, original_refs=["f" * 64]
            )
        ],
        "questions": [],
        "rule": Rule(arm_id="P"),
    }
    body.update(changes)
    return MechanismInput.model_validate(body)


@pytest.mark.parametrize(
    "arm,expected,confirm",
    [
        ("B0", "safe", "PER_ACTION"),
        ("B1", "rich", "ORIGINAL_REQUIREMENT"),
        ("B2", "rich", "ORIGINAL_REQUIREMENT"),
        ("B3", "safe", "PER_ACTION"),
        ("B4", "rich", "ORIGINAL_REQUIREMENT"),
        ("B5", "safe", "ORIGINAL_REQUIREMENT"),
        ("P", "safe", "ORIGINAL_REQUIREMENT"),
    ],
)
def test_seven_actual_finite_rules(arm: str, expected: str, confirm: str) -> None:
    rows = [
        candidate(),
        candidate(
            "rich",
            amount_cents=500000,
            cash_debit_cents=500000,
            simulated_annual_yield_basis_points=1000,
            model_confidence_basis_points=9900,
        ),
    ]
    rule = Rule.model_validate(
        {
            "arm_id": arm,
            "threshold_cents": 100000,
            "fixed_budget_cents": 200000,
            "fixed_candidate_id": "rich",
            "manual_candidate_id": "safe",
        }
    )
    result = decide_full_mechanism(original(rows, rule=rule))
    assert result.candidate_id == expected and result.confirmation_directive == confirm
    expected_amount = (
        800000
        if arm == "B1"
        else 700000
        if arm == "B2"
        else (500000 if expected == "rich" else 300000)
    )
    assert result.proposal_amount_cents == expected_amount
    assert not result.bank_authority and result.execution_status == "NOT_RUN"
    assert result.actual_safety_metrics is None and result.opportunity_denominator_retained


@pytest.mark.parametrize(
    "ablation,changes",
    [
        ("EVIDENCE_LEVEL", {"evidence_level": "INFERRED"}),
        ("POLICY_VERSION", {"policy_version_id": UUID(int=9)}),
        ("DYNAMIC_LIVING_RESERVE", {"amount_cents": 500000, "cash_debit_cents": 500000}),
        ("MULTI_GOAL_CONSTRAINTS", {"amount_cents": 500000, "cash_debit_cents": 500000}),
        ("LIQUIDITY_FILTER", {"available_at": NOW + timedelta(days=31)}),
    ],
)
def test_five_constraint_removals_really_change_selection(
    ablation: str, changes: dict[str, object]
) -> None:
    other = candidate("removed-check", simulated_annual_yield_basis_points=1000, **changes)
    data = original([candidate(), other])
    assert decide_full_mechanism(data).candidate_id == "safe"
    changed = data.model_copy(
        update={"rule": Rule.model_validate({"arm_id": "P", "ablation": ablation})}
    )
    assert decide_full_mechanism(changed).candidate_id == "removed-check"


def test_goal_property_removal_is_visible_but_never_grants_cross_goal_permission() -> None:
    row = candidate(
        "cross",
        source_goal_id=UUID(int=8),
        destination_goal_id=UUID(int=9),
        simulated_annual_yield_basis_points=1000,
    )
    assert decide_full_mechanism(original([row])).status == "NO_CANDIDATE"
    value = original([row], rule=Rule(arm_id="P", ablation="MULTI_GOAL_CONSTRAINTS"))
    result = decide_full_mechanism(value)
    assert result.candidate_id == "cross" and not result.bank_authority
    assert "MULTI_GOAL_CONSTRAINTS" in result.assessments[0].ignored_constraints


def test_b5_yield_first_really_ignores_liquidity() -> None:
    late = candidate(
        "late", available_at=NOW + timedelta(days=31), simulated_annual_yield_basis_points=1000
    )
    rows = [candidate(), late]
    assert decide_full_mechanism(original(rows)).candidate_id == "safe"
    assert decide_full_mechanism(original(rows, rule=Rule(arm_id="B5"))).candidate_id == "late"


def test_b4_confidence_only_requires_original_confidence_and_keeps_physical_cash() -> None:
    inferred = candidate(
        "model",
        policy_version_id=UUID(int=9),
        evidence_level="INFERRED",
        authority_cap_cents=1,
        model_confidence_basis_points=9999,
    )
    result = decide_full_mechanism(original([inferred], rule=Rule(arm_id="B4")))
    assert result.candidate_id == "model" and result.actual_safety_metrics is None
    no_confidence = inferred.model_copy(
        update={"model_confidence_basis_points": None, "model_confidence_original_sha256": None}
    )
    assert decide_full_mechanism(original([no_confidence], rule=Rule(arm_id="B4"))).status == (
        "NO_CANDIDATE"
    )
    too_much = inferred.model_copy(update={"cash_debit_cents": 900001})
    assert decide_full_mechanism(original([too_much], rule=Rule(arm_id="B4"))).status == (
        "NO_CANDIDATE"
    )
    with pytest.raises(ValidationError, match="actual retained original"):
        Candidate.model_validate(
            {**inferred.model_dump(), "model_confidence_original_sha256": None}
        )


def test_minimax_and_removed_minimum_question_are_different() -> None:
    worlds = [
        World(
            world_id=f"w{i}",
            complete_action_signature_sha256=("a" if i < 2 else "b") * 64,
            original_refs=["f" * 64],
        )
        for i in range(4)
    ]
    questions = [
        Question(
            question_id="a-bad",
            response_partitions=[["w0", "w1", "w2"], ["w3"]],
            original_refs=["f" * 64],
        ),
        Question(
            question_id="z-good",
            response_partitions=[["w0", "w1"], ["w2", "w3"]],
            original_refs=["f" * 64],
        ),
    ]
    value = original(worlds=worlds, questions=questions)
    actual = decide_full_mechanism(value)
    assert actual.status == "QUESTION" and actual.question_id == "z-good"
    assert actual.question_worst_remaining_disagreement_pairs == 0
    removed = decide_full_mechanism(
        value.model_copy(update={"rule": Rule(arm_id="P", ablation="MINIMUM_QUESTION")})
    )
    assert (
        removed.question_id == "a-bad" and removed.question_worst_remaining_disagreement_pairs == 2
    )
    assert removed.candidate_id is None


def test_removed_safe_recovery_changes_discovery_and_never_replaces_unknown_identity() -> None:
    redeem = candidate("redeem", kind="REDEEM_ASSET", cash_debit_cents=0)
    assert decide_full_mechanism(original([redeem])).candidate_id == "redeem"
    disabled = Rule(arm_id="P", ablation="SAFE_RECOVERY")
    assert decide_full_mechanism(original([redeem], rule=disabled)).status == "NO_CANDIDATE"
    value = original(original_unknown_action_id=UUID(int=8), original_unknown_key="old-key")
    assert decide_full_mechanism(value).recovery_directive == "RECONCILE_SAME_OPERATION"
    stopped = decide_full_mechanism(value.model_copy(update={"rule": disabled}))
    assert stopped.recovery_directive == "STOP_UNRESOLVED"
    assert stopped.original_unknown_action_id == UUID(int=8) and stopped.original_unknown_key == (
        "old-key"
    )
    assert stopped.status == "UNRESOLVED" and stopped.candidate_id is None


def test_audit_removal_changes_actual_experiment_record_not_original_financial_chain() -> None:
    value = original(prior_experiment_audit_hash="f" * 64)
    before = value.model_dump(mode="json")
    actual = decide_full_mechanism(value)
    assert actual.experiment_audit_record is not None
    record = dict(actual.experiment_audit_record)
    digest = record.pop("record_sha256")
    assert digest == value_digest(record)
    removed = decide_full_mechanism(
        value.model_copy(update={"rule": Rule(arm_id="P", ablation="AUDIT_CHAIN")})
    )
    assert removed.experiment_audit_record is None
    assert removed.candidate_id == actual.candidate_id
    assert value.model_dump(mode="json") == before


def test_future_display_never_changes_decision_budget_and_missing_stays_unknown() -> None:
    value = original()
    changed = value.model_copy(
        update={"facts": value.facts.model_copy(update={"future_income_display_cents": 2**63 - 1})}
    )
    assert decide_full_mechanism(value).candidate_id == decide_full_mechanism(changed).candidate_id
    assert decide_full_mechanism(changed).future_income_included_cents == 0
    for arm in ("P", "B3", "B4", "B5", "B1"):
        result = decide_full_mechanism(original(source_status="UNKNOWN", rule=Rule(arm_id=arm)))
        assert result.status == "UNKNOWN" and result.candidate_id is None


@pytest.mark.parametrize(
    "changes",
    [
        {"settled_cash_cents": True},
        {"active_reserved_cents": -1},
        {"hard_obligation_cents": 1.5},
        {"future_income_display_cents": 2**63},
    ],
)
def test_strict_amounts(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ConstraintFacts.model_validate({**original().facts.model_dump(), **changes})


def test_owner_epoch_capacity_extra_naive_and_incomplete_partitions_rejected() -> None:
    with pytest.raises(ValidationError, match="owner/epoch"):
        original([candidate(user_id=UUID(int=9))])
    with pytest.raises(ValidationError, match="owner/epoch"):
        original([candidate(epoch_id=UUID(int=9))])
    with pytest.raises(ValidationError):
        original([candidate(f"c{i}") for i in range(257)])
    with pytest.raises(ValidationError):
        original(actor="USER")
    with pytest.raises(ValidationError, match="aware"):
        candidate(available_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError, match="partition"):
        original(
            questions=[
                Question(
                    question_id="q", response_partitions=[["w0"], ["w0"]], original_refs=["f" * 64]
                )
            ]
        )
    with pytest.raises(ValidationError, match="exactly one P"):
        Rule(arm_id="B5", ablation="LIQUIDITY_FILTER")


def test_private_json_roundtrip_and_strict_bad_money() -> None:
    value = original()
    read = MechanismInput.model_validate_json(value.model_dump_json())
    assert decide_full_mechanism(read) == decide_full_mechanism(value)
    body = value.model_dump(mode="json")
    assert isinstance(body["facts"], dict)
    body["facts"]["settled_cash_cents"] = True
    import json

    with pytest.raises(ValidationError):
        MechanismInput.model_validate_json(json.dumps(body))


def test_threshold_and_fixed_rules_compute_exact_amount_and_drop_old_economic_hash() -> None:
    row = candidate(original_effect_hash="f" * 64)
    for arm, expected in (("B1", 800000), ("B2", 700000)):
        rule = Rule.model_validate(
            {
                "arm_id": arm,
                "threshold_cents": 100000,
                "fixed_budget_cents": 200000,
                "fixed_candidate_id": "safe",
            }
        )
        result = decide_full_mechanism(original([row], rule=rule))
        assert result.proposal_amount_cents == expected and result.original_effect_hash is None
        assert result.proposal_net_simulated_yield_cents == expected * 100 * 30 // 3650000
        assert result.actual_safety_metrics is None and not result.bank_authority


@pytest.mark.parametrize("kind", ["ALLOCATE_GOAL", "PAY_RECURRING", "REDEEM_ASSET"])
def test_threshold_baseline_skips_outside_registered_mechanism_without_losing_opportunity(
    kind: str,
) -> None:
    row = candidate(kind=kind)
    result = decide_full_mechanism(original([row], rule=Rule(arm_id="B1")))
    assert result.status == "NO_CANDIDATE" and result.opportunity_denominator_retained
    assert (
        "SKIP_BY_REGISTERED_GENERAL_PURCHASE_MECHANISM" in result.assessments[0].rejection_reasons
    )
