"""Hand-worked binding/solver risks; fixtures are not bank or financial evidence."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.full_joint_goal_planning import bind_full_joint_input, captured_references
from app.domain.full_protection_projection import (
    FullProtectionProjectionResult,
    project_full_protection,
)
from app.domain.multi_goal_allocation import (
    HardProtectionPoint,
    MultiGoalAllocationInput,
    SourceReference,
    solve_multi_goal_allocation,
)
from app.tests.test_full_projection import CASH, NOW, USER, emergency
from app.tests.test_full_protection_projection import data, dated, periodic
from app.tests.test_full_protection_projection import source as full_source
from app.tests.test_multi_goal_allocation import HASH, goal, lot, source


def fixture(
    *, amount: int = 7, periodic_policy: bool = False
) -> tuple[MultiGoalAllocationInput, FullProtectionProjectionResult, list[SourceReference]]:
    policy = (
        full_source("PeriodicTransferPolicy", periodic(amount=1))
        if periodic_policy
        else full_source("DatedExpensePolicy", dated(amount=amount))
    )
    values = data(policy, cash=20)
    values = values.model_copy(update={"boundary_versions": [emergency(2)]})
    projection = project_full_protection(values)
    points = [
        HardProtectionPoint(
            date=row.date,
            cash_cents=row.cash_cents,
            obligation_floor_cents=row.protected_cents_by_reason.get("obligations", 0),
            living_floor_cents=row.protected_cents_by_reason.get("living", 0),
            emergency_floor_cents=row.protected_cents_by_reason.get("emergency", 0),
            owned_goal_cash_cents=row.protected_cents_by_reason.get("goal_cash", 0),
            other_protection_floor_cents=row.protected_cents_by_reason.get("goal_minimum", 0),
            source_refs=[source()],
        )
        for row in projection.original_annual_projection.calculation_trace
    ]
    candidate = MultiGoalAllocationInput(
        user_id=USER,
        as_of=NOW,
        timezone="Asia/Shanghai",
        income_lots=[
            lot(
                amount=20,
                account_id=CASH,
                occurred_at=NOW - timedelta(days=1),
                observed_at=NOW - timedelta(days=1),
            )
        ],
        goals=[
            goal(
                confirmed_at=NOW - timedelta(days=2),
                valid_from=NOW - timedelta(days=2),
                monthly_min_cents=0,
                allow_partial=True,
            )
        ],
        hard_protection_points=points,
    )
    refs = captured_references(candidate) + [
        SourceReference(user_id=USER, evidence_id=policy.evidence_ids[0], content_hash=HASH)
    ]
    return candidate, projection, refs


def assert_unknown(
    original: MultiGoalAllocationInput,
    projection: FullProtectionProjectionResult,
    refs: list[SourceReference],
    reason: str,
) -> None:
    binding = bind_full_joint_input(original, projection, refs)
    assert binding.status == "UNKNOWN" and reason in binding.reasons
    assert len(binding.candidate.hard_protection_points) == len(original.hard_protection_points)
    assert binding.candidate.goals == original.goals
    result = solve_multi_goal_allocation(binding.candidate)
    assert result.status == "UNKNOWN" and len(result.goals) == len(original.goals)
    assert all(row.amount_cents is None for row in result.goals)
    assert result.income_uses == [] and result.objective_vector is None


def test_dated_full_floor_reduces_budget_and_retains_conditional_cash_and_all_points() -> None:
    original, projection, refs = fixture()
    before = original.model_dump(mode="json")
    full_before = projection.model_dump(mode="json")
    original_result = solve_multi_goal_allocation(original)
    bound = bind_full_joint_input(original, projection, refs)
    result = solve_multi_goal_allocation(bound.candidate)
    assert bound.status == "VERIFIED" and bound.reasons == []
    assert bound.bound_point_count == bound.full_point_count == bound.original_point_count == 1098
    assert original_result.budget_cents == 18 and result.budget_cents == 11
    assert original_result.goals[0].amount_cents == 15 and result.goals[0].amount_cents == 11
    assert len(result.objective_vector or ()) == 8 and result.grants_authority is False
    assert (
        bound.candidate.income_lots == original.income_lots
        and bound.candidate.goals == original.goals
    )
    initial = bound.candidate.hard_protection_points[0]
    paid = bound.candidate.hard_protection_points[7 * 3 + 1]
    assert initial.cash_cents == 20 and initial.other_protection_floor_cents == 7
    assert paid.cash_cents == 13 and paid.other_protection_floor_cents == 0
    assert paid.remaining_cents == initial.remaining_cents == 11
    assert all(row.emergency_floor_cents == 2 for row in bound.candidate.hard_protection_points)
    assert (
        original.model_dump(mode="json") == before
        and projection.model_dump(mode="json") == full_before
    )
    assert bind_full_joint_input(original, projection, refs) == bound


@pytest.mark.parametrize("index", [0, 25, 1097])
@pytest.mark.parametrize(
    "field",
    [
        "cash_cents",
        "date",
        "other_protection_floor_cents",
        "owned_goal_cash_cents",
        "living_floor_cents",
        "emergency_floor_cents",
        "obligation_floor_cents",
    ],
)
def test_original_point_any_component_or_date_drift_cannot_reduce_denominator(
    index: int, field: str
) -> None:
    original, projection, refs = fixture()
    points = list(original.hard_protection_points)
    value: Any = (
        points[index].date + timedelta(days=1)
        if field == "date"
        else getattr(points[index], field) + 1
    )
    points[index] = points[index].model_copy(update={field: value})
    original = original.model_copy(update={"hard_protection_points": points})
    assert_unknown(original, projection, refs, "ORIGINAL_365_DAY_POINT_BINDING_MISMATCH")


@pytest.mark.parametrize(
    "violation",
    [
        "short",
        "reordered",
        "cash_released",
        "minimum_released",
        "missing_full_floor",
        "new_floor",
        "margin",
        "phase",
    ],
)
def test_full_overlay_cannot_drop_points_change_original_floors_or_reuse_unpaid_cash(
    violation: str,
) -> None:
    original, projection, refs = fixture()
    full = projection.full_annual_projection
    assert full is not None
    points = list(full.calculation_trace)
    index = 7 * 3 + 1
    changes: dict[str, Any] = {}
    reason = "FULL_CONDITIONAL_CASH_OR_FLOOR_BINDING_MISMATCH"
    if violation == "short":
        points.pop()
        reason = "FULL_365_DAY_POINT_INVENTORY_INCOMPLETE"
    elif violation == "reordered":
        points[0], points[1] = points[1], points[0]
        reason = "FULL_LAYER_CHANGED_ORIGINAL_PROTECTION"
    elif violation == "cash_released":
        changes["cash_cents"] = 20
    elif violation in {"minimum_released", "new_floor", "missing_full_floor"}:
        amounts = dict(points[index].protected_cents_by_reason)
        if violation == "minimum_released":
            amounts["emergency"] = 0
            reason = "FULL_LAYER_CHANGED_ORIGINAL_PROTECTION"
        elif violation == "new_floor":
            amounts["unsupported_floor"] = 1
            reason = "FULL_LAYER_CHANGED_ORIGINAL_PROTECTION"
        else:
            index = 0
            amounts = dict(points[0].protected_cents_by_reason)
            amounts["full_dated_expense"] = 0
        changes["protected_cents_by_reason"] = amounts
    elif violation == "phase":
        changes["phase"] = "BEFORE_PAYMENT"
        reason = "FULL_LAYER_CHANGED_ORIGINAL_PROTECTION"
    else:
        changes["margin_cents"] = 999
    if changes:
        points[index] = points[index].model_copy(update=changes)
    projection = projection.model_copy(
        update={"full_annual_projection": full.model_copy(update={"calculation_trace": points})}
    )
    assert_unknown(original, projection, refs, reason)


@pytest.mark.parametrize(
    "violation", ["missing", "duplicate", "wrong_owner", "wrong_hash", "bank_hash"]
)
def test_every_actual_source_and_bank_origin_hash_requires_fresh_owner_bound_original(
    violation: str,
) -> None:
    original, projection, refs = fixture()
    if violation == "missing":
        refs = refs[1:]
    elif violation == "duplicate":
        refs.append(refs[0])
    elif violation == "wrong_owner":
        refs[0] = refs[0].model_copy(update={"user_id": UUID(int=999)})
    elif violation == "wrong_hash":
        refs[0] = refs[0].model_copy(update={"content_hash": "b" * 64})
    else:
        bank = original.income_lots[0].bank_evidence_id
        refs = [
            row.model_copy(update={"content_hash": "b" * 64}) if row.evidence_id == bank else row
            for row in refs
        ]
    reason = (
        "FRESH_SOURCE_INVENTORY_MISSING_OR_DUPLICATE"
        if violation == "duplicate"
        else "ORIGINAL_SOURCE_HASH_NOT_FRESHLY_VERIFIED"
    )
    assert_unknown(original, projection, refs, reason)


def test_periodic_future_debits_unknown_despite_sufficient_current_cash() -> None:
    original, projection, refs = fixture(periodic_policy=True)
    assert (
        projection.status == "READY"
        and projection.source_account_checks[0].state == "CURRENT_SOURCE_SUFFICIENT"
    )
    assert_unknown(original, projection, refs, "FULL_FUTURE_ACCOUNT_DEBITS_NOT_PROVEN")
    check = projection.source_account_checks[0].model_copy(
        update={"state": "SOURCE_LIQUIDITY_RISK", "remaining_current_cash_cents": -1}
    )
    projection = projection.model_copy(update={"source_account_checks": [check]})
    assert_unknown(original, projection, refs, "FULL_CURRENT_SOURCE_ACCOUNT_LIQUIDITY_RISK")


def test_missing_history_retains_original_goals_and_does_not_recompute_success_on_empty_full() -> (
    None
):
    original, projection, refs = fixture()
    state = projection.policy_states[0].model_copy(
        update={"state": "UNKNOWN", "reasons": ["OLD_VERSION_UNPAID_UNKNOWN"]}
    )
    projection = projection.model_copy(
        update={
            "status": "UNKNOWN",
            "policy_states": [state],
            "full_annual_projection": None,
            "reasons": ["OLD_VERSION_UNPAID_UNKNOWN"],
            "full_obligations_complete_within_registered_current_scope": False,
        }
    )
    assert_unknown(original, projection, refs, "OLD_VERSION_UNPAID_UNKNOWN")


def test_original_source_issue_unknown_and_dated_deficit_infeasible_without_bank_grant() -> None:
    original, projection, refs = fixture()
    original = original.model_copy(update={"source_issues": ["BANK_PROJECTION_MISMATCH"]})
    assert_unknown(original, projection, refs, "BANK_PROJECTION_MISMATCH")
    original, projection, refs = fixture(amount=30)
    bound = bind_full_joint_input(original, projection, refs)
    assert bound.status == "VERIFIED" and projection.status == "LIQUIDITY_RISK"
    result = solve_multi_goal_allocation(bound.candidate)
    assert result.status == "INFEASIBLE" and result.grants_authority is False
    assert result.income_uses == []
