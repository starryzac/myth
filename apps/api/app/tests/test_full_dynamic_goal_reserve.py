"""Direct pacing capability risks; literal facts are not real bank/runtime evidence."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from app.api.v1.dynamic_goal_reserve import DynamicReserveQuery
from app.domain.dynamic_goal_reserve import DynamicGoalReserveInput, compute_dynamic_goal_reserve
from app.tests.test_multi_goal_allocation import NOW, goal, lot, request
from pydantic import ValidationError


def facts(**changes: Any) -> DynamicGoalReserveInput:
    config: dict[str, Any] = {
        "target_cents": 300000,
        "deadline": date(2026, 12, 31),
        "monthly_min_cents": 50000,
        "monthly_target_cents": 100000,
        "monthly_max_cents": 150000,
    }
    config.update(changes)
    context = request(goals=[goal(**config)], lots=[lot(amount=200000)], cash=200000)
    return DynamicGoalReserveInput(facts=context, contribution_period="2026-10")


def test_pace_tracks_remaining_goal_and_calendar_slots_with_explicit_nominal_delta() -> None:
    usual = compute_dynamic_goal_reserve(facts())
    assert usual.status == "READY" and usual.remaining_calendar_month_slots == 3
    assert usual.uncapped_gross_pace_cents == usual.dynamic_month_total_cents == 100000
    assert usual.suggested_additional_cents == 100000
    ahead = compute_dynamic_goal_reserve(facts(current_owned_cents=240000))
    assert ahead.remaining_goal_cents == 60000 and ahead.progress_basis_points == 8000
    assert ahead.uncapped_gross_pace_cents == 20000 and ahead.dynamic_month_total_cents == 50000
    assert ahead.pace_delta_from_nominal_cents == -50000
    behind = compute_dynamic_goal_reserve(facts(deadline=date(2026, 10, 31)))
    assert (
        behind.dynamic_month_total_cents == 150000 and behind.pace_delta_from_nominal_cents == 50000
    )
    assert behind.actual_completion_date is None and behind.future_income_included_cents == 0


def test_actual_month_contributions_reduce_new_amount_without_target_inflation_or_repeat() -> None:
    original = facts()
    assert compute_dynamic_goal_reserve(original) == compute_dynamic_goal_reserve(original)
    first = compute_dynamic_goal_reserve(
        facts(current_owned_cents=50000, current_month_contributed_cents=50000)
    )
    assert first.dynamic_month_total_cents == 100000 and first.suggested_additional_cents == 50000
    done = compute_dynamic_goal_reserve(
        facts(current_owned_cents=100000, current_month_contributed_cents=100000)
    )
    assert done.dynamic_month_total_cents == 100000 and done.suggested_additional_cents == 0
    assert original.facts.goals[0].current_owned_cents == 0
    assert sum(row.available_cents for row in original.facts.income_lots) == 200000


def test_remaining_target_clips_minimum_and_old_excess_ownership_is_preserved() -> None:
    remainder = compute_dynamic_goal_reserve(facts(current_owned_cents=270000))
    assert (
        remainder.remaining_goal_cents
        == remainder.dynamic_month_total_cents
        == remainder.suggested_additional_cents
        == 30000
    )
    complete = compute_dynamic_goal_reserve(facts(current_owned_cents=400000))
    assert complete.status == "COMPLETE" and complete.suggested_additional_cents == 0
    assert complete.current_owned_cents == 400000 and complete.excess_owned_cents == 100000
    assert complete.progress_basis_points == 10000 and complete.actual_completion_date is None


def test_actual_current_contribution_over_new_max_never_rewrites_history_or_reserves_more() -> None:
    value = compute_dynamic_goal_reserve(
        facts(current_owned_cents=200000, current_month_contributed_cents=200000)
    )
    assert value.status == "MONTHLY_MAX_ALREADY_EXCEEDED" and value.suggested_additional_cents == 0
    assert value.current_month_contributed_cents == 200000
    assert value.dynamic_month_total_cents == 150000


@pytest.mark.parametrize("partial", [True, False])
def test_partial_flag_only_changes_soft_min_and_never_hard_financial_guarantee(
    partial: bool,
) -> None:
    original = facts(allow_partial=partial)
    current = request(goals=original.facts.goals, lots=[lot(amount=20000)], cash=200000)
    value = compute_dynamic_goal_reserve(
        DynamicGoalReserveInput(facts=current, contribution_period="2026-10")
    )
    assert value.minimum_shortfall_cents == 30000
    assert value.suggested_additional_cents == (20000 if partial else 0)
    assert value.status == ("PARTIAL" if partial else "MINIMUM_SHORTFALL")
    guaranteed = facts(allow_partial=partial, minimum_guarantee_cents=250000)
    blocked = compute_dynamic_goal_reserve(guaranteed)
    assert (
        blocked.status == "HARD_GUARANTEE_SHORTFALL" and blocked.suggested_additional_cents is None
    )
    assert blocked.guarantee_shortfall_cents == 100000


def test_original_protection_budget_and_preconfirmation_income_remain_hard() -> None:
    original = facts(allow_partial=True)
    constrained = request(goals=original.facts.goals, lots=[lot(amount=200000)], cash=10000)
    limited = compute_dynamic_goal_reserve(
        DynamicGoalReserveInput(facts=constrained, contribution_period="2026-10")
    )
    assert (
        limited.independently_protected_budget_cents == limited.suggested_additional_cents == 10000
    )
    risk = constrained.model_copy(
        update={
            "hard_protection_points": [
                constrained.hard_protection_points[0].model_copy(update={"cash_cents": -1})
            ]
        }
    )
    failure = compute_dynamic_goal_reserve(
        DynamicGoalReserveInput(facts=risk, contribution_period="2026-10")
    )
    assert failure.status == "LIQUIDITY_RISK" and failure.suggested_additional_cents is None
    old = request(
        goals=original.facts.goals,
        lots=[
            lot(
                amount=200000,
                occurred_at=NOW - timedelta(days=5),
                observed_at=NOW - timedelta(days=5),
            )
        ],
        cash=200000,
    )
    stale = compute_dynamic_goal_reserve(
        DynamicGoalReserveInput(facts=old, contribution_period="2026-10")
    )
    assert stale.eligible_available_income_cents == stale.suggested_additional_cents == 0


def test_hard_guarantee_can_raise_pace_inside_max_without_becoming_future_income() -> None:
    result = compute_dynamic_goal_reserve(
        facts(deadline=date(2029, 1, 1), minimum_guarantee_cents=70000, allow_partial=True)
    )
    assert result.status == "READY" and result.uncapped_gross_pace_cents is not None
    assert result.uncapped_gross_pace_cents < 50000
    assert result.dynamic_month_total_cents == result.suggested_additional_cents == 70000
    assert result.future_income_included_cents == 0 and result.grants_authority is False


def test_deadline_completion_hard_gate_and_declared_deferral_are_distinct() -> None:
    hard = compute_dynamic_goal_reserve(facts(deadline=date(2026, 10, 4)))
    assert hard.status == "DEADLINE_BLOCKED" and hard.suggested_additional_cents is None
    permitted = compute_dynamic_goal_reserve(
        facts(deadline=date(2026, 10, 1), allow_deferral=True, deferral_cost_cents_per_day=7)
    )
    assert permitted.status == "OVERDUE_READY" and permitted.suggested_additional_cents == 150000
    assert permitted.overdue_days == 4 and permitted.deferral_cost_to_date_cents == 28
    assert permitted.actual_completion_date is None


@pytest.mark.parametrize("status", ["EXPIRED", "SUSPENDED"])
def test_expired_inactive_and_unknown_never_emit_fake_zero_amounts(status: str) -> None:
    inactive = compute_dynamic_goal_reserve(facts(policy_status=status))
    assert inactive.status in {"EXPIRED_POLICY", "INACTIVE_POLICY"}
    assert inactive.suggested_additional_cents is None and inactive.current_owned_cents is None
    candidate = facts()
    unknown = DynamicGoalReserveInput(
        facts=candidate.facts.model_copy(update={"source_issues": ["MISSING_ORIGINAL"]}),
        contribution_period="2026-10",
    )
    result = compute_dynamic_goal_reserve(unknown)
    assert result.status == "INSUFFICIENT_EVIDENCE" and result.dynamic_month_total_cents is None


def test_month_rollover_uses_user_calendar_and_requires_actual_new_month_statement() -> None:
    when = datetime(2028, 1, 31, 15, 59, tzinfo=UTC)
    g = goal(
        target_cents=100,
        monthly_min_cents=0,
        monthly_target_cents=10,
        monthly_max_cents=100,
        deadline=date(2028, 2, 29),
        confirmed_at=when - timedelta(days=1),
        valid_from=when - timedelta(days=1),
    )
    income = lot(
        amount=100, occurred_at=when - timedelta(hours=1), observed_at=when - timedelta(hours=1)
    )
    january = request(goals=[g], lots=[income], cash=100, as_of=when, timezone="Asia/Shanghai")
    result = compute_dynamic_goal_reserve(
        DynamicGoalReserveInput(facts=january, contribution_period="2028-01")
    )
    assert result.remaining_calendar_month_slots == 2 and result.suggested_additional_cents == 50
    february = january.model_copy(update={"as_of": when + timedelta(minutes=1)})
    with pytest.raises(ValidationError):
        DynamicGoalReserveInput(facts=february, contribution_period="2028-01")
    after = compute_dynamic_goal_reserve(
        DynamicGoalReserveInput(facts=february, contribution_period="2028-02")
    )
    assert after.remaining_calendar_month_slots == 1 and after.suggested_additional_cents == 100


@pytest.mark.parametrize(
    "field", ["user_id", "available_cents", "as_of", "owned_cents", "future_income"]
)
def test_public_read_query_rejects_financial_overrides(field: str) -> None:
    with pytest.raises(ValidationError):
        DynamicReserveQuery.model_validate({field: "injected"})
