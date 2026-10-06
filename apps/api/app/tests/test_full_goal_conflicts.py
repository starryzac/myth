"""Hand-worked conflict/repair risks; synthetic inputs are not bank evidence."""

from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.full_goal_conflicts import (
    GoalRepairPreviewRequest,
    MonthlyMaxPlanningAdjustment,
    explain_goal_conflict,
    goal_repair_review_state_hash,
    preview_selected_goal_repairs,
)
from app.domain.multi_goal_allocation import MultiGoalAllocationInput, solve_multi_goal_allocation
from app.domain.policy_configuration import configuration_hash
from app.tests.test_multi_goal_allocation import NOW, goal, request, source
from pydantic import ValidationError

EPOCH = UUID(int=700)


def cap_conflict() -> MultiGoalAllocationInput:
    return request(
        goals=[
            goal(
                monthly_min_cents=0,
                monthly_target_cents=5,
                monthly_max_cents=5,
                current_month_contributed_cents=3,
                minimum_guarantee_cents=8,
            )
        ]
    )


def selection(original: MultiGoalAllocationInput, **changes: Any) -> GoalRepairPreviewRequest:
    target = original.goals[0]
    values: dict[str, Any] = {
        "expected_epoch_id": EPOCH,
        "reviewed_state_hash": goal_repair_review_state_hash(original, EPOCH),
        "adjustments": [
            MonthlyMaxPlanningAdjustment(
                goal_id=target.goal_id,
                expected_version_id=target.effective_policy_version_id,
                minimum_new_monthly_max_cents=6,
                maximum_new_monthly_max_cents=20,
            )
        ],
    }
    values.update(changes)
    return GoalRepairPreviewRequest.model_validate(values)


def test_exact_minimal_set_has_only_its_removal_witnesses_and_original_values() -> None:
    original = request(
        goals=[goal(minimum_guarantee_cents=8), goal(11, minimum_guarantee_cents=8), goal(12)],
        cash=10,
    )
    result = explain_goal_conflict(original)
    assert result.conflict.status == "MINIMAL_CONFLICT"
    assert {row.kind for row in result.constraints} == {"MINIMUM_GUARANTEE"}
    assert {row.required_new_cents for row in result.constraints} == {8}
    assert {row.goal_id for row in result.constraints} == {UUID(int=10), UUID(int=11)}
    assert result.goals_outside_this_minimal_set == [UUID(int=12)]
    assert result.all_other_goal_constraints_proven_compatible is False
    assert len(result.conflict.deletion_checks) == 2
    assert all(row.remaining_feasible for row in result.conflict.deletion_checks)
    assert result.removal_witness_scope == "THIS_MINIMAL_SET_MINUS_ONE"
    assert result.original_input_hash == configuration_hash(original.model_dump(mode="json"))


def test_critical_integer_cap_is_smallest_feasible_and_preserves_original_base() -> None:
    original = cap_conflict()
    before = deepcopy(original.model_dump(mode="json"))
    result = preview_selected_goal_repairs(
        original, selection(original), {UUID(int=10): source(10)}
    )
    assert result.repair.status == "PROPOSAL"
    assert result.candidates[0].monthly_max_cents == 11
    assert result.repair.candidates == result.candidates
    assert result.search_input_hash != result.original_input_hash
    assert result.repair.original_input_hash == result.search_input_hash
    assert result.selected_ranges_are_existing_financial_permissions is False
    assert result.original_execution_caps_unchanged is True and result.grants_authority is False
    assert result.repair.hypothetical_allocation is not None
    assert result.repair.hypothetical_allocation.status == "OPTIMAL"
    # Independently try every permitted integer in this tiny range, using the
    # original feasibility solver, without the candidate-generation formula.
    feasible = []
    for cap in range(6, 21):
        changed = original.model_copy(
            update={"goals": [original.goals[0].model_copy(update={"monthly_max_cents": cap})]}
        )
        if solve_multi_goal_allocation(changed).status == "OPTIMAL":
            feasible.append(cap)
    assert min(feasible) == 11
    assert original.model_dump(mode="json") == before


def test_two_needed_versions_and_unaffected_goal_are_not_dropped() -> None:
    original = request(
        goals=[
            goal(
                monthly_min_cents=0,
                monthly_target_cents=5,
                monthly_max_cents=5,
                minimum_guarantee_cents=8,
            ),
            goal(
                11,
                monthly_min_cents=0,
                monthly_target_cents=5,
                monthly_max_cents=5,
                minimum_guarantee_cents=7,
            ),
            goal(12),
        ],
        cash=30,
    )
    options = [
        MonthlyMaxPlanningAdjustment(
            goal_id=row.goal_id,
            expected_version_id=row.effective_policy_version_id,
            minimum_new_monthly_max_cents=6,
            maximum_new_monthly_max_cents=12,
        )
        for row in original.goals[:2]
    ]
    result = preview_selected_goal_repairs(
        original,
        selection(original, adjustments=options),
        {row.goal_id: row.source_refs[0] for row in original.goals},
    )
    assert result.repair.status == "PROPOSAL"
    assert [row.monthly_max_cents for row in result.repair.candidates] == [8, 7]
    assert result.repair.changed_policy_count == 2
    assert result.repair.unaffected_goal_ids == [original.goals[2].goal_id]


def test_insufficient_range_and_immutable_base_do_not_generate_fake_repair() -> None:
    original = cap_conflict()
    option = (
        selection(original).adjustments[0].model_copy(update={"maximum_new_monthly_max_cents": 10})
    )
    result = preview_selected_goal_repairs(
        original, selection(original, adjustments=[option]), {UUID(int=10): source(10)}
    )
    assert result.repair.status == "NO_PERMITTED_REPAIR" and not result.candidates
    assert result.range_outcomes[0].proposed_monthly_max_cents is None
    for actual in (
        request(goals=original.goals, cash=-1),
        original.model_copy(update={"source_issues": ["MISSING_BANK_ORIGINAL"]}),
    ):
        rejected = preview_selected_goal_repairs(actual, selection(actual), {})
        assert rejected.repair.status in {"UNKNOWN", "BASE_INFEASIBLE"}
        assert rejected.candidates == [] and rejected.range_outcomes == []
    blocked = explain_goal_conflict(request(goals=original.goals, cash=-1))
    assert blocked.conflict.status == "BASE_INFEASIBLE"
    assert blocked.constraints == [] and blocked.immutable_base_blocks[0].shortfall_cents == 1


@pytest.mark.parametrize("violation", ["version", "source", "state", "epoch"])
def test_original_identity_and_review_binding_cannot_be_substituted(violation: str) -> None:
    original, refs = cap_conflict(), {UUID(int=10): source(10)}
    chosen = selection(original)
    if violation == "version":
        chosen = chosen.model_copy(
            update={
                "adjustments": [
                    chosen.adjustments[0].model_copy(update={"expected_version_id": UUID(int=999)})
                ]
            }
        )
    elif violation == "source":
        refs = {UUID(int=10): source(999)}
    elif violation == "state":
        chosen = chosen.model_copy(update={"reviewed_state_hash": "0" * 64})
    else:
        chosen = chosen.model_copy(update={"expected_epoch_id": UUID(int=999)})
    with pytest.raises(ValueError):
        preview_selected_goal_repairs(original, chosen, refs)


def test_only_read_clock_is_removed_but_epoch_day_permissions_and_facts_remain() -> None:
    original = cap_conflict()
    same = original.model_copy(update={"as_of": NOW + timedelta(seconds=1)})
    assert configuration_hash(same.model_dump(mode="json")) != configuration_hash(
        original.model_dump(mode="json")
    )
    assert goal_repair_review_state_hash(same, EPOCH) == goal_repair_review_state_hash(
        original, EPOCH
    )
    changes = [
        original.model_copy(update={"as_of": NOW + timedelta(days=1)}),
        original.model_copy(
            update={
                "hard_protection_points": [
                    original.hard_protection_points[0].model_copy(update={"cash_cents": 21})
                ]
            }
        ),
        original.model_copy(
            update={
                "income_lots": [original.income_lots[0].model_copy(update={"observed_at": NOW})]
            }
        ),
        original.model_copy(
            update={"goals": [original.goals[0].model_copy(update={"policy_status": "EXPIRED"})]}
        ),
    ]
    assert all(
        goal_repair_review_state_hash(row, EPOCH) != goal_repair_review_state_hash(original, EPOCH)
        for row in changes
    )
    assert goal_repair_review_state_hash(original, UUID(int=701)) != goal_repair_review_state_hash(
        original, EPOCH
    )
    pending = original.model_copy(
        update={
            "goals": [
                original.goals[0].model_copy(update={"valid_from": NOW + timedelta(seconds=1)})
            ]
        }
    )
    assert goal_repair_review_state_hash(pending, EPOCH) != goal_repair_review_state_hash(
        pending.model_copy(update={"as_of": NOW + timedelta(seconds=1)}), EPOCH
    )


@pytest.mark.parametrize("value", [True, 6.0, "6", -1, 2**63])
def test_range_money_is_strict_integer(value: Any) -> None:
    raw = selection(cap_conflict()).model_dump(mode="json")["adjustments"][0]
    raw["minimum_new_monthly_max_cents"] = value
    import json

    with pytest.raises(ValidationError):
        MonthlyMaxPlanningAdjustment.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("state", ["future", "expired", "completed"])
def test_inactive_or_completed_goal_is_not_given_a_new_cap_candidate(state: str) -> None:
    original = cap_conflict()
    goal_row = original.goals[0]
    changes: dict[str, Any]
    if state == "future":
        changes = {"valid_from": NOW + timedelta(days=1), "policy_status": "CONFIRMED"}
    elif state == "expired":
        changes = {"valid_until": NOW, "policy_status": "EXPIRED"}
    else:
        changes = {"current_owned_cents": goal_row.target_cents}
    original = original.model_copy(update={"goals": [goal_row.model_copy(update=changes)]})
    result = preview_selected_goal_repairs(
        original, selection(original), {goal_row.goal_id: source(10)}
    )
    assert result.repair.status == "NOT_NEEDED" and result.candidates == []
    assert result.range_outcomes[0].proposed_monthly_max_cents is None
    assert result.range_outcomes[0].reason == (
        "CURRENT_CAP_IS_NOT_A_HARD_CONFLICT"
        if state == "completed"
        else "GOAL_NOT_CURRENTLY_AUTHORIZED"
    )
