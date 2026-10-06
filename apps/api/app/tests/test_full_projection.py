"""Annual capability risks with hand-worked synthetic cents; these are not bank observations."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BillFact,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundarySnapshot,
    CashFact,
    GoalOwnership,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.boundary import BoundaryContext, Sources
from app.services.dashboard_types import DashboardAuditCard
from app.services.full_projection import project_verified_context
from pydantic import ValidationError

NOW = datetime(2026, 10, 4, 20, tzinfo=UTC)
USER = UUID(int=1)
CASH = UUID(int=2)
CARD = UUID(int=3)


def context(
    *,
    cash: int = 1000,
    positions: list[BoundaryPosition] | None = None,
    versions: list[BoundaryPolicyVersion] | None = None,
    bills: list[BillFact] | None = None,
) -> BoundaryContext:
    snapshot = BoundarySnapshot(
        as_of=NOW,
        timezone="Asia/Shanghai",
        cash_accounts=[
            CashFact(account_id=CASH, account_type="CASH", balance_cents=cash, observed_at=NOW),
            CashFact(account_id=CARD, account_type="CREDIT_CARD", balance_cents=0, observed_at=NOW),
        ],
        bills=bills or [],
    )
    return BoundaryContext(snapshot, versions or [], positions or [], [], Sources(USER, NOW, []))


def audit(status: str = "VALID") -> DashboardAuditCard:
    return DashboardAuditCard(
        epoch_id=UUID(int=4),
        status=status,
        complete=status == "VALID",
        anchored_run_statuses={},
    )


def emergency(amount: int, effective_day: int = 0) -> BoundaryPolicyVersion:
    config = validate_configuration({"type": "emergency_buffer", "amount_cents": amount})
    return BoundaryPolicyVersion(
        policy_id=UUID(int=10),
        version_id=UUID(int=11),
        configuration=config,
        content_hash=configuration_hash(config),
        confirmed_at=NOW,
        valid_from=NOW + timedelta(days=effective_day),
    )


def test_annual_curve_is_365_future_days_plus_initial_without_changing_mvp_boundary() -> None:
    current = context()
    old_result = compute_boundary(current.snapshot, [], [], []).model_dump(mode="json")
    response = project_verified_context(current, audit())
    assert response.initial_checkpoint.date == date(2026, 10, 5)
    assert response.initial_checkpoint.day == 0
    assert len(response.daily_checkpoints) == 365
    assert [point.day for point in response.daily_checkpoints] == list(range(1, 366))
    assert response.daily_checkpoints[-1].date == date(2027, 10, 5)
    assert len(response.annual_projection.calculation_trace) == 366 * 3
    assert response.execution_view.model_dump(mode="json") == old_result
    assert len(response.execution_view.calculation_trace) == 91 * 3
    assert response.grants_authority is False
    assert response.future_points_are_settled_cash is False
    assert response.annual_projection.financial_only is True
    assert response.future_income.included_in_execution_cents == 0
    assert response.future_income.included_in_planning_cents == 0


def test_commitment_known_today_after_mvp_window_reduces_only_annual_projection() -> None:
    response = project_verified_context(context(versions=[emergency(700, 200)]), audit())
    assert response.execution_view.safe_idle_cents == 1000
    assert response.annual_projection.safe_idle_cents == 300
    before = response.daily_checkpoints[198]
    later = response.daily_checkpoints[200]
    assert before.after_principal is not None and later.after_principal is not None
    assert before.after_principal.protected_cents_by_reason["emergency"] == 0
    assert later.after_principal.protected_cents_by_reason["emergency"] == 700


def test_confirmed_principal_return_after_day_90_preserves_same_day_payment_order() -> None:
    principal = BoundaryPosition(
        position_id=UUID(int=20),
        principal_cents=300,
        status="REDEEMING",
        principal_available_at=NOW + timedelta(days=200),
        availability_evidence_ids=[UUID(int=21)],
    )
    bill = BillFact(
        bill_id=UUID(int=22),
        account_id=CARD,
        statement_date=date(2026, 10, 5),
        due_date=date(2027, 4, 23),
        total_cents=200,
        paid_cents=0,
        status="UNPAID",
    )
    response = project_verified_context(context(positions=[principal], bills=[bill]), audit())
    day = response.daily_checkpoints[199]
    assert day.day == 200 and day.date == bill.due_date
    assert day.before_payment is not None
    assert day.after_payment is not None
    assert day.after_principal is not None
    assert day.before_payment.cash_cents == 1000
    assert day.before_payment.protected_cents_by_reason["obligations"] == 200
    assert day.after_payment.cash_cents == 800
    assert day.after_principal.cash_cents == 1100
    assert day.after_principal.principal_position_ids == [principal.position_id]
    assert day.minimum_intraday_margin_cents == 800
    assert response.execution_view.calculation_trace[-1].cash_cents == 1000


def test_principal_without_verified_return_date_never_becomes_cash() -> None:
    principal = BoundaryPosition(position_id=UUID(int=20), principal_cents=300, status="HELD")
    response = project_verified_context(context(positions=[principal]), audit())
    assert all(point.cash_cents == 1000 for point in response.annual_projection.calculation_trace)
    assert response.unavailable_principal[0].position_id == principal.position_id


def test_future_goal_principal_retains_ownership_and_never_enlarges_general_idle() -> None:
    current = context()
    goal_id = UUID(int=30)
    current.positions.append(
        BoundaryPosition(
            position_id=UUID(int=20),
            goal_id=goal_id,
            principal_cents=200,
            status="REDEEMING",
            principal_available_at=NOW + timedelta(days=200),
            availability_evidence_ids=[UUID(int=21)],
        )
    )
    current.snapshot.goals.append(
        GoalOwnership(
            goal_id=goal_id,
            policy_id=UUID(int=31),
            account_id=CASH,
            cash_owned_cents=300,
            principal_owned_cents=200,
            allocated_cents=500,
        )
    )
    response = project_verified_context(current, audit())
    day = response.daily_checkpoints[199]
    assert day.after_payment is not None and day.after_principal is not None
    assert day.after_payment.protected_cents_by_reason["goal_cash"] == 300
    assert day.after_principal.protected_cents_by_reason["goal_cash"] == 500
    assert day.after_payment.margin_cents == day.after_principal.margin_cents == 700
    assert (
        response.execution_view.safe_idle_cents == response.annual_projection.safe_idle_cents == 700
    )


@pytest.mark.parametrize("audit_status", ["INTEGRITY_ERROR", "LEGACY_UNAUDITED"])
def test_unverified_audit_retains_all_days_with_unknown_numeric_values(audit_status: str) -> None:
    response = project_verified_context(context(), audit(audit_status))
    assert response.annual_projection.status == "INSUFFICIENT_EVIDENCE"
    assert response.annual_projection.safe_idle_cents is None
    assert len(response.daily_checkpoints) == 365
    assert all(point.status == "NOT_PROVEN" for point in response.daily_checkpoints)
    assert all(point.after_principal is None for point in response.daily_checkpoints)
    assert {issue.code for issue in response.source_issues} >= {"AUDIT_NOT_VERIFIED"}


def test_unknown_position_retains_denominator_without_invented_principal_or_cash() -> None:
    unknown = BoundaryPosition(position_id=UUID(int=20), principal_cents=300, status="UNKNOWN")
    response = project_verified_context(context(positions=[unknown]), audit())
    assert response.annual_projection.status == "INSUFFICIENT_EVIDENCE"
    assert all(point.minimum_intraday_margin_cents is None for point in response.daily_checkpoints)


@pytest.mark.parametrize("horizon", [True, False, 0, 366, -1, 1.0, "365"])
def test_horizon_remains_strict_bounded_integer(horizon: Any) -> None:
    with pytest.raises(ValidationError):
        BoundarySnapshot.model_validate(
            {**context().snapshot.model_dump(), "horizon_days": horizon}
        )


@pytest.mark.parametrize("horizon", [1, 90, 364, 365])
def test_engine_honors_explicit_horizon_without_changing_default(horizon: int) -> None:
    snapshot = BoundarySnapshot.model_validate(
        {**context().snapshot.model_dump(), "horizon_days": horizon}
    )
    result = compute_boundary(snapshot, [], [], [])
    assert len(result.calculation_trace) == (horizon + 1) * 3
    assert {point.day for point in result.calculation_trace} == set(range(horizon + 1))


def test_future_cash_fact_does_not_become_authorized_funds() -> None:
    current = context()
    future = CashFact(
        account_id=UUID(int=99),
        account_type="CASH",
        balance_cents=999_999,
        observed_at=NOW + timedelta(days=1),
    )
    current.snapshot.cash_accounts.append(future)
    response = project_verified_context(current, audit())
    assert response.execution_view.status == "INSUFFICIENT_EVIDENCE"
    assert response.execution_view.safe_idle_cents is None
    assert any(
        blocker.code == "FUTURE_CASH_FACT"
        for blocker in response.annual_projection.blocking_constraints
    )


def test_unsupported_future_income_fields_cannot_change_the_current_financial_input() -> None:
    original = context().snapshot.model_dump()
    for key in ("future_income_cents", "expected_salary_cents", "future_income"):
        with pytest.raises(ValidationError):
            BoundarySnapshot.model_validate({**original, key: 1_000_000})
    current = context()
    # Source issues are conservative; source metadata can never supply missing money.
    current.sources.issue("MISSING_INCOME_SOURCE", "income", "unverified future income")
    response = project_verified_context(current, audit())
    assert response.execution_view.safe_idle_cents is None
    assert response.future_income.included_in_execution_cents == 0
