"""Hand-worked FULL planning risk cases; no observed bank or acceptance outcomes."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import BoundaryPosition, CashFact, GoalOwnership
from app.domain.full_policy_configuration import validate_full_configuration
from app.domain.full_protection_projection import (
    FullProtectedReference,
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    ProtectionTemplate,
    project_full_protection,
)
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_projection import CASH, NOW, context, emergency
from pydantic import ValidationError


def source(
    template: ProtectionTemplate,
    configuration: dict[str, Any],
    *,
    now: datetime = NOW,
    identity: int = 50,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> FullProtectionPolicySource:
    canonical = validate_full_configuration(template, configuration)
    digest = configuration_hash(canonical)
    return FullProtectionPolicySource(
        policy_id=UUID(int=identity),
        version_id=UUID(int=identity + 1),
        template_name=template,
        configuration=canonical,
        content_hash=digest,
        confirmation={"accepted": True, "reviewed_hash": digest},
        confirmed_at=now,
        valid_from=valid_from or now,
        valid_until=valid_until,
        effective_status="CONFIRMED" if valid_from is not None and valid_from > now else "ACTIVE",
        planning_confirmation_valid=True,
        references_current=True,
        evidence_ids=[UUID(int=identity + 2)],
    )


def dated(*, day: int = 7, amount: int = 200) -> dict[str, Any]:
    first = date(2026, 10, 5) + timedelta(days=day)
    return {
        "type": "dated_expense",
        "window": {"start": first.isoformat(), "end": (first + timedelta(days=3)).isoformat()},
        "amount": {"min_cents": 0, "target_cents": amount // 2, "max_cents": amount},
    }


def periodic(*, amount: int = 10, due_day: int = 31) -> dict[str, Any]:
    return {
        "type": "periodic_transfer",
        "source_account_id": str(CASH),
        "payee_id": "synthetic-confirmed-payee",
        "amount_rule": {"kind": "exact", "amount_cents": amount},
        "due_day": due_day,
        "prepare_days_before": 10,
        "single_action_cap_cents": amount,
    }


def data(*policies: FullProtectionPolicySource, cash: int = 1000) -> FullProtectionProjectionInput:
    original = context(cash=cash)
    return FullProtectionProjectionInput(
        snapshot=original.snapshot,
        boundary_versions=original.versions,
        positions=original.positions,
        boundary_products=original.products,
        policies=list(policies),
    )


def test_dated_upper_bound_is_reserved_today_and_paid_once_at_earliest_conditional_day() -> None:
    inputs = data(source("DatedExpensePolicy", dated()))
    result = project_full_protection(inputs)
    curve = result.full_annual_projection
    assert curve is not None and result.status == "READY"
    assert curve.safe_idle_cents == 800 and len(curve.calculation_trace) == 366 * 3
    before, payment, principal = curve.calculation_trace[7 * 3 : 8 * 3]
    assert (
        before.cash_cents == 1000 and before.protected_cents_by_reason["full_dated_expense"] == 200
    )
    assert payment.cash_cents == principal.cash_cents == 800
    assert payment.protected_cents_by_reason["full_dated_expense"] == 0
    assert before.margin_cents == payment.margin_cents == principal.margin_cents == 800
    assert result.occurrences[0].latest_due_date == date(2026, 10, 15)
    assert result.occurrences[0].original_paid_cents is None
    assert result.occurrences[0].settlement_status == "UNSUPPORTED_CONSERVATIVE_UNPAID"
    assert result.occurrences[0].bank_authority is False
    assert result.original_execution_view == compute_boundary(inputs.snapshot, [], [], [])
    assert result.original_annual_projection == compute_boundary(
        inputs.snapshot.model_copy(update={"horizon_days": 365}), [], [], []
    )


def test_known_future_confirmation_protects_today_without_granting_bank_authority() -> None:
    row = source("DatedExpensePolicy", dated(day=200), valid_from=NOW + timedelta(days=200))
    result = project_full_protection(data(row))
    assert result.full_annual_projection is not None
    assert result.full_annual_projection.safe_idle_cents == 800
    assert result.original_execution_view.safe_idle_cents == 1000
    assert result.original_annual_projection.safe_idle_cents == 1000
    assert result.bank_authority is False and result.execution_support == "NOT_IMPLEMENTED"
    assert result.future_income_in_current_cash_cents == 0


def test_periodic_upper_bound_and_prepare_days_do_not_delay_current_protection() -> None:
    config = periodic()
    config["amount_rule"] = {"kind": "range", "min_cents": 5, "max_cents": 10}
    result = project_full_protection(data(source("PeriodicTransferPolicy", config)))
    assert len(result.occurrences) == 12
    assert result.occurrences[0].earliest_due_date == date(2026, 10, 31)
    assert result.occurrences[0].prepare_start_date == date(2026, 10, 21)
    assert all(row.amount_basis == "REGISTERED_MAX" for row in result.occurrences)
    assert result.full_annual_projection is not None
    assert result.full_annual_projection.protected_cents_by_reason["full_periodic_transfer"] == 120
    assert result.full_annual_projection.safe_idle_cents == 880
    assert result.full_annual_projection.calculation_trace[-1].cash_cents == 880


def test_leap_month_end_and_exclusive_expiry_retain_final_due_date_exactly_once() -> None:
    now = datetime(2028, 2, 1, tzinfo=UTC)
    inputs = data()
    inputs = inputs.model_copy(
        update={
            "snapshot": inputs.snapshot.model_copy(
                update={
                    "as_of": now,
                    "cash_accounts": [
                        CashFact(
                            account_id=CASH,
                            account_type="CASH",
                            balance_cents=1000,
                            observed_at=now,
                        )
                    ],
                }
            ),
            "policies": [
                source(
                    "PeriodicTransferPolicy",
                    periodic(amount=100),
                    now=now,
                    valid_until=datetime(2028, 3, 31, 16, tzinfo=UTC),
                )
            ],
        }
    )
    result = project_full_protection(inputs)
    assert [row.earliest_due_date for row in result.occurrences] == [
        date(2028, 2, 29),
        date(2028, 3, 31),
    ]
    assert result.full_annual_projection is not None
    assert result.full_annual_projection.safe_idle_cents == 800


def test_source_account_risk_is_visible_despite_sufficient_aggregate_cash() -> None:
    inputs = data(source("PeriodicTransferPolicy", periodic()))
    inputs = inputs.model_copy(
        update={
            "snapshot": inputs.snapshot.model_copy(
                update={
                    "cash_accounts": [
                        CashFact(
                            account_id=CASH, account_type="CASH", balance_cents=50, observed_at=NOW
                        ),
                        CashFact(
                            account_id=UUID(int=70),
                            account_type="CASH",
                            balance_cents=950,
                            observed_at=NOW,
                        ),
                    ]
                }
            )
        }
    )
    result = project_full_protection(inputs)
    assert result.status == "LIQUIDITY_RISK"
    assert result.full_annual_projection is not None
    assert result.full_annual_projection.minimum_margin_cents == 880
    assert result.full_annual_projection.safe_idle_cents == 0
    assert result.source_account_checks[0].remaining_current_cash_cents == -70
    assert result.source_account_checks[0].state == "SOURCE_LIQUIDITY_RISK"
    assert result.source_account_checks[0].future_account_debits_complete is False


def test_goal_ownership_and_pending_reservations_are_not_new_cash_or_released_protection() -> None:
    inputs = data(source("DatedExpensePolicy", dated()))
    inputs = inputs.model_copy(
        update={
            "snapshot": inputs.snapshot.model_copy(
                update={
                    "goals": [
                        GoalOwnership(
                            goal_id=UUID(int=80),
                            policy_id=UUID(int=81),
                            account_id=CASH,
                            cash_owned_cents=300,
                            principal_owned_cents=0,
                            allocated_cents=300,
                        )
                    ]
                }
            ),
            "reserved_cash_by_account": {CASH: 80},
        }
    )
    result = project_full_protection(inputs)
    assert result.full_annual_projection is not None
    assert result.full_annual_projection.safe_idle_cents == 420
    assert result.full_annual_projection.protected_cents_by_reason["goal_cash"] == 300
    assert (
        result.full_annual_projection.protected_cents_by_reason["pending_cash_reservations"] == 80
    )


def test_existing_mvp_emergency_floor_and_hash_are_unchanged() -> None:
    inputs = data(source("DatedExpensePolicy", dated()))
    inputs = inputs.model_copy(update={"boundary_versions": [emergency(100)]})
    result = project_full_protection(inputs)
    assert result.original_annual_projection.safe_idle_cents == 900
    assert result.full_annual_projection is not None
    assert result.full_annual_projection.safe_idle_cents == 700
    assert result.full_annual_projection.protected_cents_by_reason["emergency"] == 100
    assert (
        result.full_annual_projection.boundary_hash
        != result.original_annual_projection.boundary_hash
    )


def test_future_principal_never_cures_an_earlier_full_payment_deficit() -> None:
    inputs = data(source("DatedExpensePolicy", dated(day=10, amount=200)), cash=100)
    inputs = inputs.model_copy(
        update={
            "positions": [
                BoundaryPosition(
                    position_id=UUID(int=90),
                    principal_cents=100,
                    status="REDEEMING",
                    principal_available_at=NOW + timedelta(days=20),
                    availability_evidence_ids=[UUID(int=91)],
                )
            ]
        }
    )
    result = project_full_protection(inputs)
    assert result.status == "LIQUIDITY_RISK" and result.full_annual_projection is not None
    assert result.full_annual_projection.deficit_cents == 100
    assert result.full_annual_projection.calculation_trace[10 * 3 + 1].cash_cents == -100
    assert result.full_annual_projection.calculation_trace[20 * 3 + 2].cash_cents == 0


def test_must_not_reduce_requires_actual_current_reference_and_binds_it() -> None:
    config = dated()
    config["must_not_reduce_policy_ids"] = [str(UUID(int=100))]
    row = source("DatedExpensePolicy", config)
    inputs = data(row)
    absent = project_full_protection(inputs)
    assert absent.status == "UNKNOWN" and absent.full_annual_projection is None
    ref = FullProtectedReference(
        policy_id=UUID(int=100),
        version_id=UUID(int=101),
        kind="MVP_POLICY",
        content_hash="a" * 64,
        current_confirmed=True,
        evidence_ids=[UUID(int=102)],
    )
    verified = project_full_protection(
        inputs.model_copy(
            update={"policies": [row.model_copy(update={"protected_references": [ref]})]}
        )
    )
    assert verified.status == "READY" and verified.input_hash != absent.input_hash
    changed = project_full_protection(
        inputs.model_copy(
            update={
                "policies": [
                    row.model_copy(
                        update={
                            "protected_references": [
                                ref.model_copy(update={"current_confirmed": False})
                            ]
                        }
                    )
                ]
            }
        )
    )
    assert (
        changed.status == "UNKNOWN"
        and changed.original_execution_view == verified.original_execution_view
    )


@pytest.mark.parametrize(
    "field,value",
    [("references_current", False), ("planning_confirmation_valid", False), ("version_number", 2)],
)
def test_unverified_current_or_prior_version_history_is_unknown_not_zero(
    field: str, value: Any
) -> None:
    row = source("DatedExpensePolicy", dated()).model_copy(update={field: value})
    result = project_full_protection(data(row))
    assert result.status == "UNKNOWN" and result.full_annual_projection is None
    assert result.full_obligations_complete_within_registered_current_scope is False
    assert result.original_annual_projection.safe_idle_cents == 1000


def test_expired_old_unpaid_history_and_old_periodic_months_remain_unknown() -> None:
    dated_row = source("DatedExpensePolicy", dated()).model_copy(
        update={"effective_status": "EXPIRED", "valid_from": NOW - timedelta(days=2)}
    )
    periodic_row = source("PeriodicTransferPolicy", periodic(), valid_from=NOW - timedelta(days=35))
    for row in (dated_row, periodic_row):
        result = project_full_protection(data(row))
        assert result.status == "UNKNOWN" and result.full_annual_projection is None
        assert "HISTORICAL_FULL_UNPAID_COVERAGE_UNSUPPORTED" in result.policy_states[0].reasons


def test_seasonal_quantile_has_no_adopted_amount_and_never_invents_extra_floor() -> None:
    row = source(
        "SeasonalReservePolicy",
        {
            "type": "seasonal_reserve",
            "holiday_code": "SYNTHETIC_WINDOW",
            "window": {"start": "2027-01-01", "end": "2027-01-07"},
            "lookback_days": 365,
            "minimum_historical_windows": 1,
            "quantile": 0.8,
            "essential_categories": ["food"],
            "adjustment_cap_cents": 500,
        },
    )
    result = project_full_protection(data(row))
    assert result.status == "READY" and result.policy_states[0].state == "ADVICE_ONLY"
    assert result.seasonal_status == "ADVICE_ONLY_NO_ADOPTED_AMOUNT"
    assert result.seasonal_adopted_adjustment_cents is None
    assert (
        result.full_annual_projection is not None
        and result.full_annual_projection.safe_idle_cents == 1000
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"content_hash": "a" * 64},
        {"confirmation": {"accepted": False}},
        {"confirmed_at": NOW + timedelta(seconds=1)},
    ],
)
def test_exact_original_hash_confirmation_and_clock_are_required(mutation: dict[str, Any]) -> None:
    row = source("DatedExpensePolicy", dated()).model_copy(update=mutation)
    with pytest.raises(ValueError):
        project_full_protection(data(row))


def test_partial_inventory_and_unregistered_cash_reservations_cannot_yield_safe_amount() -> None:
    result = project_full_protection(
        data().model_copy(update={"full_source_inventory_complete": False})
    )
    assert result.status == "UNKNOWN" and result.full_annual_projection is None
    with pytest.raises(ValueError, match="Reservations"):
        project_full_protection(
            data().model_copy(update={"reserved_cash_by_account": {UUID(int=999): 1}})
        )
    with pytest.raises(ValidationError):
        project_full_protection(
            data().model_copy(update={"reserved_cash_by_account": {CASH: True}})
        )


def test_duplicate_current_versions_are_rejected_and_original_inputs_are_immutable() -> None:
    row = source("DatedExpensePolicy", dated())
    original = data(row)
    before = original.model_dump(mode="json")
    project_full_protection(original)
    assert original.model_dump(mode="json") == before
    with pytest.raises(ValueError, match="one actual current"):
        project_full_protection(data(row, row))
