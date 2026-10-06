"""Hand-worked hypothetical policy parameters never become authorization facts."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BoundaryPosition,
    GoalMonthFact,
    GoalOwnership,
    LivingReserveFact,
    SettlementFact,
    SourceIssue,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.tests.boundary_display_cases import NOW, bill, policy, rent, snapshot


def _assumption(source: Any, configuration: dict[str, Any], **updates: Any) -> Any:
    from app.domain.policy_change_types import PolicyChangeAssumption

    canonical = validate_configuration(configuration)
    fields = {
        "user_id": UUID(int=900),
        "policy_id": source.policy_id,
        "source_version_id": source.version_id,
        "configuration": canonical,
        "configuration_hash": configuration_hash(canonical),
        "timezone": "UTC",
        "assumed_confirmation_at": NOW,
        "assumed_valid_from": NOW,
        "assumed_valid_until": None,
        "source_status": "ACTIVE",
    }
    fields.update(updates)
    return PolicyChangeAssumption(**fields)


def test_emergency_change_is_explicit_hypothesis_and_keeps_original_facts() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = policy(40, {"type": "emergency_buffer", "amount_cents": 2_000})
    actual = snapshot(10_000)
    original = deepcopy((actual.model_dump(), source.model_dump()))
    before = compute_boundary(actual, [source], [], [])
    result = compute_policy_change_boundary(
        actual,
        [source],
        [],
        [],
        source_version=source,
        assumption=_assumption(source, {"type": "emergency_buffer", "amount_cents": 3_000}),
    )
    assert before.safe_idle_cents == 8_000
    assert result.boundary.safe_idle_cents == 7_000
    assert result.boundary.protected_cents_by_reason["emergency"] == 3_000
    assert (
        result.preview_only is True and result.financial_only is True and result.simulation is True
    )
    assert result.assumption_digest != before.boundary_hash
    assert result.details.boundary_hash == result.boundary.boundary_hash
    assert len(result.boundary.calculation_trace) == 273
    assert (actual.model_dump(), source.model_dump()) == original
    assert compute_boundary(actual, [source], [], []).model_dump() == before.model_dump()


def test_future_replacement_has_a_real_gap_and_does_not_keep_old_protection() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = policy(40, {"type": "emergency_buffer", "amount_cents": 2_000})
    result = compute_policy_change_boundary(
        snapshot(10_000),
        [source],
        [],
        [],
        source_version=source,
        assumption=_assumption(
            source,
            {"type": "emergency_buffer", "amount_cents": 3_000, "valid_from": "2026-12-20"},
            assumed_valid_from=datetime(2026, 12, 20, tzinfo=UTC),
        ),
    )
    assert result.boundary.safe_idle_cents == 7_000
    assert result.boundary.protected_cents_by_reason["emergency"] == 0
    assert result.boundary.calculation_trace[0].cash_cents == 10_000
    assert result.boundary.calculation_trace[-1].protected_cents_by_reason["emergency"] == 3_000


def _goal_config(target: int = 15_000, monthly: int = 1_000) -> dict[str, Any]:
    return {
        "type": "goal_saving",
        "target_cents": target,
        "deadline": "2026-12-31",
        "monthly_contribution": {
            "min_cents": monthly,
            "target_cents": monthly,
            "max_cents": monthly,
        },
    }


def test_goal_change_preserves_cash_principal_and_actual_month_contribution() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = policy(50, _goal_config())
    owned = GoalOwnership(
        goal_id=UUID(int=51),
        policy_id=source.policy_id,
        account_id=UUID(int=1),
        cash_owned_cents=4_000,
        principal_owned_cents=2_000,
        allocated_cents=6_000,
    )
    position = BoundaryPosition(
        position_id=UUID(int=52),
        goal_id=owned.goal_id,
        principal_cents=2_000,
        status="HELD",
        principal_available_at=None,
    )
    actual = snapshot(
        20_000,
        goals=[owned],
        goal_month_contributions=[
            GoalMonthFact(goal_id=owned.goal_id, period="2026-10", contributed_cents=500)
        ],
    )
    original = deepcopy(actual.model_dump())
    before = compute_boundary(actual, [source], [position], [])
    result = compute_policy_change_boundary(
        actual,
        [source],
        [position],
        [],
        source_version=source,
        assumption=_assumption(source, _goal_config(10_000, 2_000)),
    )
    # Three remaining months: 1500 + 2000 + 2000, capped to target minus actual 6000.
    assert before.safe_idle_cents == 13_500
    assert result.boundary.safe_idle_cents == 12_000
    assert result.boundary.protected_cents_by_reason["goal_minimum"] == 4_000
    assert result.boundary.protected_cents_by_reason["goal_cash"] == 4_000
    assert result.details.current_goal_ownership.allocated_cents == 6_000
    assert result.details.current_goal_ownership.principal_owned_cents == 2_000
    assert result.boundary.calculation_trace[-1].cash_cents == 20_000
    lowered = compute_policy_change_boundary(
        actual,
        [source],
        [position],
        [],
        source_version=source,
        assumption=_assumption(source, _goal_config(5_000, 2_000)),
    )
    assert lowered.boundary.safe_idle_cents == 16_000
    assert lowered.boundary.protected_cents_by_reason["goal_cash"] == 4_000
    assert lowered.details.current_goal_ownership.allocated_cents == 6_000
    assert actual.model_dump() == original


def test_activating_this_month_never_fabricates_a_zero_contribution() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = policy(50, _goal_config(), valid_from=datetime(2026, 12, 1, tzinfo=UTC))
    owned = GoalOwnership(
        goal_id=UUID(int=51),
        policy_id=source.policy_id,
        cash_owned_cents=0,
        principal_owned_cents=0,
        allocated_cents=0,
    )
    actual = snapshot(20_000, goals=[owned])
    result = compute_policy_change_boundary(
        actual,
        [source],
        [],
        [],
        source_version=source,
        assumption=_assumption(source, _goal_config(), source_status="CONFIRMED"),
    )
    assert result.boundary.status == "INSUFFICIENT_EVIDENCE"
    assert result.boundary.safe_idle_cents is None
    assert "MISSING_GOAL_MONTH_CONTRIBUTION" in {
        item.code for item in result.boundary.blocking_constraints
    }


def test_suspended_change_stays_suspended_and_keeps_owned_goal_cash() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = policy(50, _goal_config())
    actual = snapshot(
        10_000,
        goals=[
            GoalOwnership(
                goal_id=UUID(int=51),
                policy_id=source.policy_id,
                cash_owned_cents=4_000,
                principal_owned_cents=0,
                allocated_cents=4_000,
            )
        ],
    )
    assumption = _assumption(source, _goal_config(50_000), source_status="SUSPENDED")
    result = compute_policy_change_boundary(
        actual,
        [],
        [],
        [],
        source_version=source,
        assumption=assumption,
    )
    assert assumption.effective_status == "SUSPENDED"
    assert result.boundary.safe_idle_cents == 6_000
    assert result.boundary.protected_cents_by_reason["goal_minimum"] == 0
    assert result.details.current_goal_ownership.allocated_cents == 4_000


def test_future_recurring_details_never_borrow_old_confirmation_identity() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = rent(amount=1_000)
    source = source.model_copy(update={"evidence_ids": [UUID(int=200)]})
    config = {**source.configuration, "amount_rule": {"kind": "exact", "amount_cents": 2_000}}
    result = compute_policy_change_boundary(
        snapshot(10_000),
        [source],
        [],
        [],
        source_version=source,
        assumption=_assumption(
            source,
            {**config, "valid_from": "2020-01-01"},
            assumed_valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        ),
    )
    assert result.boundary.safe_idle_cents == 4_000
    assert result.details.next_obligations.next_remaining_protection_cents == 2_000
    assert result.details.next_obligations.items[0].policy_version_id is None
    assert result.details.next_obligations.items[0].evidence_ids == []
    assert result.boundary.calculation_trace[0].obligation_occurrence_ids == [
        f"policy:{source.policy_id}:2026-10",
        f"policy:{source.policy_id}:2026-11",
        f"policy:{source.policy_id}:2026-12",
    ]


def test_old_generated_recurring_period_is_not_erased_even_when_fully_paid() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = rent(
        amount=1_000,
        confirmed_at=datetime(2026, 9, 1, tzinfo=UTC),
        valid_from=datetime(2026, 9, 1, tzinfo=UTC),
    )
    actual = snapshot(
        10_000,
        occurrence_settlements=[
            SettlementFact(
                policy_id=source.policy_id,
                period="2026-09",
                paid_cents=1_000,
                final_total_cents=1_000,
                settled_at=NOW,
            )
        ],
    )
    result = compute_policy_change_boundary(
        actual,
        [source],
        [],
        [],
        source_version=source,
        assumption=_assumption(source, {**source.configuration, "name": "Only rename"}),
    )
    assert result.boundary.safe_idle_cents is None
    assert "HISTORICAL_OBLIGATION_RECONCILIATION_REQUIRED" in {
        item.code for item in result.boundary.blocking_constraints
    }


def test_bill_balance_change_does_not_replace_actual_card_debt() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = rent(
        rule={"kind": "bill_balance", "account_id": str(UUID(int=2))},
        confirmed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    actual = snapshot(10_000, bills=[bill(70, date(2026, 10, 15), total=3_000)])
    result = compute_policy_change_boundary(
        actual,
        [source],
        [],
        [],
        source_version=source,
        assumption=_assumption(source, {**source.configuration, "name": "Reviewed card"}),
    )
    assert result.boundary.safe_idle_cents == 7_000
    assert result.boundary.protected_cents_by_reason["obligations"] == 3_000
    assert result.details.next_obligations.items[0].bill_id == UUID(int=70)


def _living_config() -> dict[str, Any]:
    return {
        "type": "living_reserve",
        "horizon_days": 7,
        "method": {
            "name": "rolling_window_quantile",
            "lookback_days": 30,
            "quantile": 0.9,
            "essential_categories": ["food"],
        },
    }


def test_new_living_estimate_is_separate_and_never_borrows_old_amount() -> None:
    from app.domain.boundary import compute_policy_change_boundary
    from app.domain.policy_change_types import LivingReserveChangeEstimate

    source = policy(80, _living_config())
    actual = snapshot(
        10_000,
        living_reserves=[
            LivingReserveFact(
                policy_version_id=source.version_id,
                amount_cents=1_000,
                estimation_input_digest="actual",
            )
        ],
    )
    assumption = _assumption(source, {**_living_config(), "extra_buffer_cents": 500})
    missing = compute_policy_change_boundary(
        actual,
        [source],
        [],
        [],
        source_version=source,
        assumption=assumption,
    )
    assert missing.boundary.safe_idle_cents is None
    estimate = LivingReserveChangeEstimate(
        status="READY",
        amount_cents=2_000,
        as_of=NOW,
        configuration_hash=assumption.configuration_hash,
        estimation_input_digest="a" * 64,
    )
    result = compute_policy_change_boundary(
        actual,
        [source],
        [],
        [],
        source_version=source,
        assumption=assumption,
        living_estimate=estimate,
    )
    assert result.boundary.safe_idle_cents == 8_000
    assert result.boundary.protected_cents_by_reason["living"] == 2_000
    assert actual.living_reserves[0].amount_cents == 1_000
    assert result.assumption_digest != missing.assumption_digest


def test_bank_unknown_or_actual_source_issue_cannot_be_cleared_by_change() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = policy(40, {"type": "emergency_buffer", "amount_cents": 2_000})
    actual = snapshot(
        10_000,
        source_issues=[
            SourceIssue(
                code="BANK_PROJECTION_INTEGRITY_ERROR",
                entity_type="bank",
                entity_id="bank-source",
            )
        ],
    )
    result = compute_policy_change_boundary(
        actual,
        [source],
        [
            BoundaryPosition(
                position_id=UUID(int=75),
                principal_cents=2_000,
                status="UNKNOWN",
            )
        ],
        [],
        source_version=source,
        assumption=_assumption(source, {"type": "emergency_buffer", "amount_cents": 0}),
    )
    assert result.boundary.status == "INSUFFICIENT_EVIDENCE"
    assert result.boundary.safe_idle_cents is None
    assert {item.code for item in result.boundary.blocking_constraints} >= {
        "BANK_PROJECTION_INTEGRITY_ERROR",
        "UNKNOWN_POSITION",
    }
    assert result.details.source_issues == actual.source_issues


@pytest.mark.parametrize(
    "updates",
    [
        {"configuration_hash": "0" * 64},
        {"source_status": "REVOKED"},
        {"source_status": "EXPIRED"},
        {"simulation": False},
        {"preview_only": False},
        {"assumed_confirmation_at": NOW.replace(tzinfo=None)},
        {"assumed_valid_from": NOW + timedelta(days=1)},
        {"accepted": True},
        {"version_id": UUID(int=999)},
        {"evidence_ids": [UUID(int=999)]},
    ],
)
def test_assumption_is_strict_and_has_no_confirmation_or_bank_authority(
    updates: dict[str, Any],
) -> None:
    source = policy(40, {"type": "emergency_buffer", "amount_cents": 2_000})
    with pytest.raises(ValueError):
        _assumption(source, source.configuration, **updates)


@pytest.mark.parametrize("changed", ["clock", "type", "version", "expired", "money", "source"])
def test_actual_facts_and_source_binding_are_revalidated(changed: str) -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = policy(40, {"type": "emergency_buffer", "amount_cents": 2_000})
    actual = snapshot(10_000)
    assumption = _assumption(source, source.configuration)
    policies = [source]
    if changed == "clock":
        actual = actual.model_copy(update={"as_of": NOW + timedelta(seconds=1)})
    elif changed == "type":
        assumption = _assumption(source, _living_config())
    elif changed == "version":
        assumption = _assumption(source, source.configuration, source_version_id=UUID(int=999))
    elif changed == "expired":
        source = source.model_copy(update={"valid_until": NOW})
        policies = [source]
    elif changed == "money":
        actual = actual.model_copy(
            update={
                "cash_accounts": [
                    actual.cash_accounts[0].model_copy(update={"balance_cents": True})
                ]
            }
        )
    else:
        source = source.model_copy(update={"content_hash": "0" * 64})
    with pytest.raises(ValueError):
        compute_policy_change_boundary(
            actual,
            policies,
            [],
            [],
            source_version=source,
            assumption=assumption,
        )


def test_mutating_an_assumption_requires_new_validation_and_new_digest() -> None:
    from app.domain.policy_change_types import calculate_assumption_digest

    source = policy(40, {"type": "emergency_buffer", "amount_cents": 2_000})
    assumption = _assumption(source, source.configuration)
    original = calculate_assumption_digest(assumption)
    assumption.configuration["amount_cents"] = True
    with pytest.raises(ValueError):
        calculate_assumption_digest(assumption)
    fresh = _assumption(source, {"type": "emergency_buffer", "amount_cents": 3_000})
    assert calculate_assumption_digest(fresh) != original


def test_expired_new_window_has_expired_effective_status_even_when_column_stays_suspended() -> None:
    source = policy(40, {"type": "emergency_buffer", "amount_cents": 2_000})
    assumption = _assumption(
        source,
        {
            "type": "emergency_buffer",
            "amount_cents": 3_000,
            "valid_from": "2026-09-01",
            "valid_until": "2026-09-30",
        },
        source_status="SUSPENDED",
        assumed_valid_from=datetime(2026, 9, 1, tzinfo=UTC),
        assumed_valid_until=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert assumption.source_status == "SUSPENDED"
    assert assumption.effective_status == "EXPIRED"


def test_asset_policy_change_keeps_actual_contract_without_granting_authority() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    config = {
        "type": "asset_authorization",
        "scope": "general_idle_funds",
        "allowed_asset_classes": ["CASH_MGMT_T1"],
        "max_auto_managed_cents": 50_000,
        "single_action_cap_cents": 10_000,
        "max_redemption_delay_days": 1,
        "max_lock_days": 30,
    }
    source = policy(90, config)
    actual = snapshot(10_000)
    held = BoundaryPosition(
        position_id=UUID(int=91),
        principal_cents=3_000,
        status="HELD",
        principal_available_at=NOW + timedelta(days=1),
        availability_evidence_ids=[UUID(int=92)],
    )
    result = compute_policy_change_boundary(
        actual,
        [],
        [held],
        [],
        source_version=source,
        assumption=_assumption(
            source,
            {
                **config,
                "allowed_asset_classes": ["CASH"],
                "max_auto_managed_cents": 0,
                "single_action_cap_cents": 0,
            },
        ),
    )
    before = compute_boundary(actual, [], [held], [])
    assert result.boundary.safe_idle_cents == before.safe_idle_cents == 10_000
    assert result.boundary.calculation_trace == before.calculation_trace
    assert result.boundary.calculation_trace[-1].cash_cents == 13_000
    assert result.preview_only is True and result.financial_only is True


def test_local_valid_until_is_inclusive_but_known_from_never_moves_backwards() -> None:
    from app.domain.boundary import compute_policy_change_boundary

    source = policy(40, {"type": "emergency_buffer", "amount_cents": 2_000})
    actual = snapshot(10_000).model_copy(update={"timezone": "Asia/Shanghai"})
    configuration = {
        "type": "emergency_buffer",
        "amount_cents": 3_000,
        "valid_from": "2026-10-04",
        "valid_until": "2026-10-04",
    }
    assumption = _assumption(
        source,
        configuration,
        timezone="Asia/Shanghai",
        assumed_valid_from=datetime(2026, 10, 3, 16, tzinfo=UTC),
        assumed_valid_until=datetime(2026, 10, 4, 16, tzinfo=UTC),
    )
    result = compute_policy_change_boundary(
        actual,
        [source],
        [],
        [],
        source_version=source,
        assumption=assumption,
    )
    assert result.boundary.safe_idle_cents == 7_000
    assert result.boundary.calculation_trace[0].protected_cents_by_reason["emergency"] == 3_000
    assert result.boundary.calculation_trace[3].protected_cents_by_reason["emergency"] == 0
    ended = datetime(2026, 10, 4, 16, tzinfo=UTC)
    expired = _assumption(
        source,
        configuration,
        timezone="Asia/Shanghai",
        assumed_confirmation_at=ended,
        assumed_valid_from=datetime(2026, 10, 3, 16, tzinfo=UTC),
        assumed_valid_until=ended,
    )
    assert expired.effective_status == "EXPIRED"
    closed = compute_policy_change_boundary(
        actual.model_copy(update={"as_of": ended}),
        [source],
        [],
        [],
        source_version=source,
        assumption=expired,
    )
    assert closed.boundary.safe_idle_cents == 10_000


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_cents": True},
        {"amount_cents": 1.5},
        {"status": "INSUFFICIENT_EVIDENCE"},
        {"as_of": NOW + timedelta(seconds=1)},
        {"configuration_hash": "0" * 64},
    ],
)
def test_hypothetical_living_estimate_is_strict_and_bound_to_its_parameters(
    changes: dict[str, Any],
) -> None:
    from app.domain.policy_change_types import (
        LivingReserveChangeEstimate,
        calculate_assumption_digest,
    )

    source = policy(80, _living_config())
    assumption = _assumption(source, _living_config())
    values: dict[str, Any] = {
        "status": "READY",
        "amount_cents": 2_000,
        "as_of": NOW,
        "configuration_hash": assumption.configuration_hash,
        "estimation_input_digest": "a" * 64,
    }
    values.update(changes)
    with pytest.raises(ValueError):
        calculate_assumption_digest(assumption, LivingReserveChangeEstimate(**values))
