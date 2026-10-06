"""Direct capability tests; literal synthetic fixtures are not financial runtime evidence."""

from datetime import UTC, date, datetime, timedelta
from itertools import product
from random import Random
from typing import Any
from uuid import UUID

import pytest
from app.domain.multi_goal_allocation import (
    AllocationGoal,
    AllocationIncomeLot,
    GoalRepairCandidate,
    HardProtectionPoint,
    MultiGoalAllocationInput,
    SourceReference,
    find_minimal_goal_conflict,
    propose_minimal_goal_repairs,
    solve_multi_goal_allocation,
)
from pydantic import ValidationError

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
USER = UUID(int=1)
HASH = "a" * 64


def source(identifier: int = 2) -> SourceReference:
    return SourceReference(user_id=USER, evidence_id=UUID(int=identifier), content_hash=HASH)


def goal(identifier: int = 10, **changes: Any) -> AllocationGoal:
    values: dict[str, Any] = {
        "goal_id": UUID(int=identifier),
        "account_id": UUID(int=100 + identifier),
        "policy_id": UUID(int=200 + identifier),
        "effective_policy_version_id": UUID(int=300 + identifier),
        "policy_status": "ACTIVE",
        "target_cents": 50,
        "current_owned_cents": 0,
        "current_month_contributed_cents": 0,
        "monthly_min_cents": 1,
        "monthly_target_cents": 15,
        "monthly_max_cents": 25,
        "deadline": date(2027, 1, 1),
        "confirmed_at": NOW - timedelta(days=2),
        "valid_from": NOW - timedelta(days=2),
        "source_refs": [source(identifier)],
    }
    values.update(changes)
    return AllocationGoal.model_validate(values)


def lot(identifier: int = 400, amount: int = 20, **changes: Any) -> AllocationIncomeLot:
    values: dict[str, Any] = {
        "fragment_id": UUID(int=identifier + 100),
        "origin_transaction_id": UUID(int=identifier),
        "account_id": UUID(int=500),
        "received_cents": amount,
        "available_cents": amount,
        "occurred_at": NOW - timedelta(days=1),
        "observed_at": NOW - timedelta(days=1),
        "bank_evidence_id": UUID(int=identifier + 200),
        "bank_evidence_hash": HASH,
        "source_refs": [source(identifier)],
    }
    values.update(changes)
    return AllocationIncomeLot.model_validate(values)


def request(
    goals: list[AllocationGoal] | None = None,
    lots: list[AllocationIncomeLot] | None = None,
    cash: int = 20,
    **changes: Any,
) -> MultiGoalAllocationInput:
    values: dict[str, Any] = {
        "user_id": USER,
        "as_of": NOW,
        "timezone": "UTC",
        "goals": goals if goals is not None else [goal(), goal(11)],
        "income_lots": lots if lots is not None else [lot(amount=max(0, cash))],
        "hard_protection_points": [
            HardProtectionPoint(
                date=NOW.date(),
                cash_cents=cash,
                obligation_floor_cents=0,
                living_floor_cents=0,
                emergency_floor_cents=0,
                owned_goal_cash_cents=0,
                source_refs=[source()],
            )
        ],
    }
    values.update(changes)
    return MultiGoalAllocationInput.model_validate(values)


def independently_exhaustive_objective(
    original: MultiGoalAllocationInput,
) -> tuple[int, int, int, int, int, int, int, int] | None:
    """Tiny raw-cent oracle, deliberately not using solver ranks, vertices or objective helpers."""
    goals = sorted(original.goals, key=lambda item: item.goal_id)
    lots = original.income_lots
    columns = [
        (lot_index, goal_index)
        for lot_index in range(len(lots))
        for goal_index in range(len(goals))
    ]
    best: tuple[int, int, int, int, int, int, int, int] | None = None
    budget = min(
        point.cash_cents
        - point.obligation_floor_cents
        - point.living_floor_cents
        - point.emergency_floor_cents
        - point.owned_goal_cash_cents
        - point.other_protection_floor_cents
        for point in original.hard_protection_points
    )
    for allocation in product(*(range(lots[index].available_cents + 1) for index, _ in columns)):
        if sum(allocation) > budget:
            continue
        if any(
            sum(
                amount
                for (index, _), amount in zip(columns, allocation, strict=True)
                if index == row
            )
            > available.available_cents
            for row, available in enumerate(lots)
        ):
            continue
        if any(
            amount and lots[row].occurred_at < max(goals[col].confirmed_at, goals[col].valid_from)
            for (row, col), amount in zip(columns, allocation, strict=True)
        ):
            continue
        amounts = [
            sum(
                amount
                for (_, index), amount in zip(columns, allocation, strict=True)
                if index == col
            )
            for col in range(len(goals))
        ]
        if any(
            amount > max(0, item.monthly_max_cents - item.current_month_contributed_cents)
            or amount > max(0, item.target_cents - item.current_owned_cents)
            or amount < max(0, item.minimum_guarantee_cents - item.current_owned_cents)
            or (
                item.deadline <= NOW.date()
                and not item.allow_partial
                and not item.allow_deferral
                and amount < max(0, item.target_cents - item.current_owned_cents)
            )
            for item, amount in zip(goals, amounts, strict=True)
        ):
            continue
        shortfalls = [
            max(
                0,
                min(
                    max(0, item.target_cents - item.current_owned_cents),
                    max(0, item.monthly_min_cents - item.current_month_contributed_cents),
                )
                - amount,
            )
            for item, amount in zip(goals, amounts, strict=True)
        ]
        delay = sum(
            0
            if item.current_owned_cents >= item.target_cents
            else max(
                0,
                (
                    NOW.date()
                    + (
                        timedelta(days=0)
                        if amount + item.current_owned_cents == item.target_cents
                        else timedelta(days=1)
                    )
                    - item.deadline
                ).days,
            )
            for item, amount in zip(goals, amounts, strict=True)
        )
        target_distance = sum(
            abs(
                min(
                    max(0, item.target_cents - item.current_owned_cents),
                    max(0, item.monthly_target_cents - item.current_month_contributed_cents),
                )
                - amount
            )
            for item, amount in zip(goals, amounts, strict=True)
        )
        moves = len(
            {
                (lots[row].account_id, goals[col].goal_id)
                for (row, col), amount in zip(columns, allocation, strict=True)
                if amount > 0 and lots[row].account_id != goals[col].account_id
            }
        )
        objective = (
            0,
            0,
            sum(shortfalls),
            sum(item.importance * missing for item, missing in zip(goals, shortfalls, strict=True)),
            delay,
            target_distance,
            0,
            moves,
        )
        best = objective if best is None or objective < best else best
    return best


def test_real_amount_scale_has_identical_nodes_and_lexicographic_optimum() -> None:
    small = request()
    multiplier = 1_000_000
    large = request(
        goals=[
            goal(
                identifier,
                target_cents=50 * multiplier,
                monthly_min_cents=multiplier,
                monthly_target_cents=15 * multiplier,
                monthly_max_cents=25 * multiplier,
            )
            for identifier in (10, 11)
        ],
        lots=[lot(amount=20 * multiplier)],
        cash=20 * multiplier,
    )
    result = solve_multi_goal_allocation(small)
    measured = solve_multi_goal_allocation(large)
    assert result.status == measured.status == "OPTIMAL"
    assert result.objective_vector == (0, 0, 0, 0, 0, 10, 0, 2)
    assert measured.objective_vector == (0, 0, 0, 0, 0, 10_000_000, 0, 2)
    assert measured.visited_nodes == result.visited_nodes < 2000
    assert measured.grants_authority is False
    assert sum(use.amount_cents for use in measured.income_uses) == 20_000_000


@pytest.mark.parametrize("seed", range(24))
def test_breakpoint_flow_exactly_matches_independent_small_cent_exhaustion(seed: int) -> None:
    rng = Random(seed)
    fixtures = []
    for identifier in (10, 11):
        maximum = rng.randint(1, 4)
        target = rng.randint(0, maximum)
        minimum = rng.randint(0, target)
        fixtures.append(
            goal(
                identifier,
                target_cents=rng.randint(1, 5),
                monthly_min_cents=minimum,
                monthly_target_cents=target,
                monthly_max_cents=maximum,
                current_owned_cents=rng.randint(0, 2),
                current_month_contributed_cents=rng.randint(0, 2),
                importance=rng.randint(0, 100),
                deadline=date(2026, 10, rng.choice((4, 5, 6, 8))),
                allow_partial=True,
                allow_deferral=True,
                confirmed_at=NOW - timedelta(days=rng.choice((0, 2))),
                valid_from=NOW - timedelta(days=2),
            )
        )
    original = request(
        fixtures,
        [
            lot(400, rng.randint(0, 3), account_id=UUID(int=500)),
            lot(
                401,
                rng.randint(0, 3),
                account_id=rng.choice((UUID(int=500), fixtures[0].account_id)),
            ),
        ],
        cash=rng.randint(0, 6),
    )
    before = original.model_dump(mode="json")
    expected = independently_exhaustive_objective(original)
    actual = solve_multi_goal_allocation(original)
    assert actual.status == "OPTIMAL"
    assert actual.objective_vector == expected
    assert original.model_dump(mode="json") == before


def test_priority_breaks_minimum_shortfall_tie_without_moving_owned_goal_funds() -> None:
    original = request(
        [
            goal(
                10,
                target_cents=100,
                current_owned_cents=80,
                monthly_min_cents=5,
                monthly_target_cents=5,
                monthly_max_cents=5,
                importance=100,
            ),
            goal(
                11, monthly_min_cents=5, monthly_target_cents=5, monthly_max_cents=5, importance=1
            ),
        ],
        [lot(amount=5)],
        cash=5,
    )
    result = solve_multi_goal_allocation(original)
    assert result.objective_vector is not None
    assert result.objective_vector[:4] == (0, 0, 5, 5)
    assert [row.amount_cents for row in result.goals] == [5, 0]
    assert [row.projected_owned_cents for row in result.goals] == [85, 0]


def test_same_account_ownership_allocation_minimizes_actual_cash_movements() -> None:
    one, two = (
        goal(10, monthly_min_cents=5, monthly_target_cents=5),
        goal(11, monthly_min_cents=5, monthly_target_cents=5),
    )
    result = solve_multi_goal_allocation(
        request(
            [one, two],
            [lot(400, 5, account_id=one.account_id), lot(401, 5, account_id=two.account_id)],
            cash=10,
        )
    )
    assert result.status == "OPTIMAL"
    assert result.objective_vector == (0, 0, 0, 0, 0, 0, 0, 0)
    assert len(result.income_uses) == 2


def test_original_fragment_identity_and_source_location_survive_internal_split() -> None:
    first = lot(amount=10, available_cents=5)
    second = AllocationIncomeLot.model_validate(
        {
            **first.model_dump(),
            "fragment_id": UUID(int=501),
            "account_id": UUID(int=501),
            "available_cents": 5,
        }
    )
    target = goal(monthly_min_cents=10, monthly_target_cents=10, monthly_max_cents=10)
    result = solve_multi_goal_allocation(request([target], [first, second], cash=10))
    assert result.status == "OPTIMAL"
    assert {use.fragment_id for use in result.income_uses} == {
        first.fragment_id,
        second.fragment_id,
    }
    assert {use.source_account_id for use in result.income_uses} == {
        first.account_id,
        second.account_id,
    }
    assert sum(use.amount_cents for use in result.income_uses) == 10
    with pytest.raises(ValueError, match="conserve"):
        request([target], [first, second.model_copy(update={"available_cents": 6})], cash=11)


def test_income_before_confirmation_and_reserved_portion_never_fund_the_goal() -> None:
    target = goal(confirmed_at=NOW)
    old = lot(amount=100, available_cents=1)
    result = solve_multi_goal_allocation(request([target], [old], cash=100))
    assert result.status == "OPTIMAL" and result.goals[0].amount_cents == 0
    assert result.income_uses == []
    eligible = lot(amount=100, available_cents=1, occurred_at=NOW, observed_at=NOW)
    actual = solve_multi_goal_allocation(request([target], [eligible], cash=100))
    assert actual.goals[0].amount_cents == 1
    assert actual.income_uses[0].amount_cents == 1


def test_all_hard_protection_points_and_other_goal_floors_bound_the_pool() -> None:
    base = request(cash=100)
    stricter = HardProtectionPoint(
        date=date(2026, 10, 20),
        cash_cents=100,
        obligation_floor_cents=10,
        living_floor_cents=10,
        emergency_floor_cents=10,
        owned_goal_cash_cents=30,
        other_protection_floor_cents=30,
        source_refs=[source()],
    )
    original = MultiGoalAllocationInput.model_validate(
        {**base.model_dump(), "hard_protection_points": [*base.hard_protection_points, stricter]}
    )
    result = solve_multi_goal_allocation(original)
    assert result.status == "OPTIMAL" and result.budget_cents == 10
    assert sum(row.amount_cents or 0 for row in result.goals) <= 10
    negative = request(cash=-1, lots=[lot(amount=10)])
    assert solve_multi_goal_allocation(negative).status == "INFEASIBLE"
    assert find_minimal_goal_conflict(negative).status == "BASE_INFEASIBLE"


def test_suspended_policy_is_preserved_in_denominator_with_no_allocation() -> None:
    original = request([goal(policy_status="SUSPENDED")])
    result = solve_multi_goal_allocation(original)
    assert result.status == "OPTIMAL"
    assert len(result.goals) == 1 and result.goals[0].amount_cents == 0
    assert result.income_uses == []


def test_capacity_and_missing_sources_never_publish_an_incumbent_as_optimal() -> None:
    for original in (
        request(solver_node_budget=1),
        request(source_issues=["UNKNOWN_BANK_REQUEST"]),
    ):
        result = solve_multi_goal_allocation(original)
        assert result.status == "UNKNOWN"
        assert result.objective_vector is None
        assert len(result.goals) == 2
        assert all(row.amount_cents is None for row in result.goals)
        assert result.income_uses == []


def test_censored_delay_is_not_a_fake_completion_and_cost_does_not_replace_delay_tier() -> None:
    target = goal(
        target_cents=100,
        deadline=date(2026, 10, 4),
        allow_partial=True,
        allow_deferral=True,
        deferral_cost_cents_per_day=999,
    )
    result = solve_multi_goal_allocation(request([target], cash=10))
    assert result.status == "OPTIMAL"
    row = result.goals[0]
    assert row.completion_date is None and row.delay_censored is True
    assert row.delay_lower_bound_days == 2
    assert row.deferral_cost_lower_bound_cents == 1998
    assert result.objective_vector is not None and result.objective_vector[4] == 2


def test_deletion_minimal_conflict_has_single_removal_feasible_original_witnesses() -> None:
    original = request(
        [goal(10, minimum_guarantee_cents=8), goal(11, minimum_guarantee_cents=8)], cash=10
    )
    assert solve_multi_goal_allocation(original).status == "INFEASIBLE"
    conflict = find_minimal_goal_conflict(original)
    assert conflict.status == "MINIMAL_CONFLICT"
    assert conflict.constraint_ids == [
        f"MINIMUM_GUARANTEE:{UUID(int=10)}",
        f"MINIMUM_GUARANTEE:{UUID(int=11)}",
    ]
    assert len(conflict.deletion_checks) == 2
    assert all(
        check.remaining_feasible and check.counterfactual_only for check in conflict.deletion_checks
    )
    assert all(sum(check.witness_amounts_cents.values()) == 8 for check in conflict.deletion_checks)
    assert conflict.grants_authority is False


def repair(target: AllocationGoal, identifier: int, **changes: Any) -> GoalRepairCandidate:
    return GoalRepairCandidate.model_validate(
        {
            "candidate_id": UUID(int=identifier),
            "goal_id": target.goal_id,
            "source_policy_version_id": target.effective_policy_version_id,
            "permission_ref": target.source_refs[0],
            **changes,
        }
    )


def test_registered_repair_minimizes_policy_count_then_exact_deviation_and_priority_loss() -> None:
    target = goal(
        minimum_guarantee_cents=8,
        monthly_min_cents=5,
        monthly_target_cents=5,
        monthly_max_cents=5,
        negotiable_fields=["monthly_min_cents", "monthly_max_cents"],
    )
    unaffected = goal(11)
    original = request([target, unaffected], cash=20)
    smaller_loss = repair(target, 601, monthly_max_cents=10)
    larger_loss = repair(target, 600, monthly_min_cents=3, monthly_max_cents=8)
    before = original.model_dump(mode="json")
    result = propose_minimal_goal_repairs(original, [larger_loss, smaller_loss])
    assert result.status == "PROPOSAL"
    assert result.candidates == [smaller_loss]
    assert result.changed_policy_count == 1
    assert result.parameter_deviation_numerator == result.parameter_deviation_denominator == 1
    assert result.priority_loss_cents == 0
    assert result.unaffected_goal_ids == [unaffected.goal_id]
    assert result.requires_new_version_confirmation is True
    assert result.grants_authority is False
    assert original.model_dump(mode="json") == before
    assert result.hypothetical_allocation is not None
    assert result.hypothetical_allocation.status == "OPTIMAL"


def test_repair_never_relaxes_hard_finance_and_refuses_unregistered_or_stale_changes() -> None:
    target = goal(
        minimum_guarantee_cents=8,
        monthly_min_cents=3,
        monthly_target_cents=5,
        monthly_max_cents=5,
        negotiable_fields=["monthly_max_cents"],
    )
    original = request([target], cash=20)
    with pytest.raises(ValueError, match="unregistered"):
        propose_minimal_goal_repairs(original, [repair(target, 600, monthly_min_cents=1)])
    with pytest.raises(ValueError, match="exact current"):
        propose_minimal_goal_repairs(
            original,
            [
                repair(target, 600, monthly_max_cents=8).model_copy(
                    update={"source_policy_version_id": UUID(int=999)}
                )
            ],
        )
    risk = request([target], cash=-1, lots=[lot(amount=20)])
    proposal = propose_minimal_goal_repairs(risk, [repair(target, 600, monthly_max_cents=8)])
    assert proposal.status == "BASE_INFEASIBLE" and proposal.candidates == []
    assert proposal.hypothetical_allocation is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("available_cents", True),
        ("available_cents", 1.5),
        ("available_cents", -1),
        ("owner_goal_id", UUID(int=10)),
        ("observed_at", NOW + timedelta(days=1)),
    ],
)
def test_malicious_or_future_income_inputs_are_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        invalid = AllocationIncomeLot.model_validate({**lot().model_dump(), field: value})
        request(lots=[invalid])
