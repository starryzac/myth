"""Literal direct v3 risks; synthetic fixtures are not PG or financial acceptance."""

from copy import deepcopy
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from app.domain.boundary_types import LivingReserveFact
from app.domain.full_policy_change_multi import (
    MultiTemplateChangeInput,
    derive_multi_template_impact,
)
from app.domain.full_policy_configuration import (
    DEFAULT_INTERVENTIONS,
    TemplateName,
    validate_full_configuration,
)
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.multi_goal_allocation import HardProtectionPoint
from app.domain.policy_change_types import LivingReserveChangeEstimate
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.tests.boundary_display_cases import NOW, policy, rent, snapshot
from app.tests.test_full_asset_allocation import request as asset_request
from app.tests.test_full_protection_projection import source
from app.tests.test_full_recovery_planning import inputs as recovery_request
from app.tests.test_multi_goal_allocation import request as joint_request


def data(
    config: dict[str, Any] | None = None, candidate: dict[str, Any] | None = None
) -> MultiTemplateChangeInput:
    actual = policy(40, config or {"type": "emergency_buffer", "amount_cents": 2000})
    facts = snapshot(10000)
    full = project_full_protection(
        FullProtectionProjectionInput(
            snapshot=facts,
            boundary_versions=[actual],
            positions=[],
            boundary_products=[],
            policies=[],
        )
    )
    template = {
        "emergency_buffer": "EmergencyBufferPolicy",
        "living_reserve": "LivingReservePolicy",
        "recurring_obligation": "RecurringObligationPolicy",
        "goal_saving": "LongTermGoalPolicy",
    }[actual.configuration["type"]]
    return MultiTemplateChangeInput(
        user_id=UUID(int=900),
        epoch_id=UUID(int=901),
        policy_id=actual.policy_id,
        version_id=actual.version_id,
        source_kind="MVP_POLICY",
        template_name=cast(TemplateName, template),
        current_configuration=actual.configuration,
        candidate_configuration=validate_configuration(
            candidate or {**actual.configuration, "amount_cents": 3000}
        ),
        candidate_valid_from=NOW,
        snapshot=facts,
        boundary_versions=[actual],
        positions=[],
        boundary_products=[],
        full_projection=full,
        full_sources=[],
        source_originals={"purpose": "TOOL_ONLY_SYNTHETIC"},
        mvp_source=actual,
        mvp_source_status="ACTIVE",
    )


def protection(value: MultiTemplateChangeInput, **changes: Any) -> MultiTemplateChangeInput:
    value = value.model_copy(update=changes)
    return value.model_copy(
        update={
            "full_projection": project_full_protection(
                FullProtectionProjectionInput(
                    snapshot=value.snapshot,
                    boundary_versions=value.boundary_versions,
                    positions=value.positions,
                    boundary_products=value.boundary_products,
                    policies=value.full_sources,
                    reserved_cash_by_account=value.reserved_cash_by_account,
                )
            )
        }
    )


def test_emergency_repeated_change_actual_math_preserves_complete_1098_and_no_mutation() -> None:
    original = data()
    before = original.model_dump_json()
    result = derive_multi_template_impact(original)
    assert result.status == "PROJECTED" and result.before is not None and result.after is not None
    assert result.delta_safe_idle_cents == result.delta_minimum_margin_cents == -1000
    assert result.before.safe_idle_cents == 8000 and result.after.safe_idle_cents == 7000
    assert len(result.before.calculation_trace) == len(result.after.calculation_trace) == 1098
    assert (
        result.future_income_in_current_cash_cents == result.future_income_in_execution_cents == 0
    )
    assert not result.grants_authority and not result.writes_policy_or_bank
    assert result.current_position_principal_delta_cents == 0
    assert original.model_dump_json() == before and derive_multi_template_impact(original) == result


def test_exact_actual_full_payment_and_claim_burdens_are_kept_at_each_phase() -> None:
    original = data()
    first = NOW.date() + timedelta(days=9)
    dated = source(
        "DatedExpensePolicy",
        {
            "type": "dated_expense",
            "window": {"start": first.isoformat(), "end": first.isoformat()},
            "amount": {"min_cents": 0, "target_cents": 200, "max_cents": 500},
        },
        now=NOW,
    )
    original = protection(
        original, full_sources=[dated], reserved_cash_by_account={UUID(int=1): 100}
    )
    result = derive_multi_template_impact(original)
    assert result.after is not None and result.before is not None
    assert result.before.safe_idle_cents == 7400 and result.after.safe_idle_cents == 6400
    assert result.delta_safe_idle_cents == -1000
    for old, new in zip(
        result.before.calculation_trace, result.after.calculation_trace, strict=True
    ):
        assert old.cash_cents == new.cash_cents
        assert (
            old.protected_cents_by_reason["full_dated_expense"]
            == new.protected_cents_by_reason["full_dated_expense"]
        )
        assert new.protected_cents_by_reason["pending_cash_reservations"] == 100
    assert result.after.calculation_trace[9 * 3].cash_cents == 10000
    assert result.after.calculation_trace[9 * 3 + 1].cash_cents == 9500


def test_missing_living_history_unknown_and_actual_estimate_changes_real_floor() -> None:
    config = {
        "type": "living_reserve",
        "horizon_days": 7,
        "method": {
            "name": "rolling_window_quantile",
            "lookback_days": 28,
            "quantile": 0.5,
            "essential_categories": ["food"],
        },
    }
    original = data(config, {**config, "extra_buffer_cents": 100})
    original = protection(
        original,
        snapshot=original.snapshot.model_copy(
            update={
                "living_reserves": [
                    LivingReserveFact(
                        policy_version_id=original.version_id,
                        amount_cents=1000,
                        estimation_input_digest="a" * 64,
                    )
                ]
            }
        ),
    )
    assert derive_multi_template_impact(original).status == "UNKNOWN"
    original = original.model_copy(
        update={
            "living_estimate": LivingReserveChangeEstimate(
                status="READY",
                amount_cents=1500,
                as_of=NOW,
                configuration_hash=configuration_hash(original.candidate_configuration),
                estimation_input_digest="b" * 64,
            )
        }
    )
    result = derive_multi_template_impact(original)
    assert result.status == "PROJECTED" and result.delta_safe_idle_cents == -500


def test_recurring_today_known_but_prior_occurrence_is_not_rewritten() -> None:
    actual = rent(amount=100, due=15)
    original = data(
        actual.configuration,
        {**actual.configuration, "amount_rule": {"kind": "exact", "amount_cents": 200}},
    )
    result = derive_multi_template_impact(original)
    assert result.status == "PROJECTED" and result.delta_safe_idle_cents == -1200
    assert original.mvp_source is not None
    old_source = original.mvp_source.model_copy(
        update={"confirmed_at": NOW - timedelta(days=40), "valid_from": NOW - timedelta(days=40)}
    )
    old = protection(original, mvp_source=old_source, boundary_versions=[old_source])
    assert derive_multi_template_impact(old).status == "UNKNOWN"


@pytest.mark.parametrize("mutation", ["point", "safe_idle", "drop_product", "raw_full_floor"])
def test_tampered_complete_full_projection_is_unknown(mutation: str) -> None:
    original = data()
    full = original.full_projection.full_annual_projection
    assert full is not None
    values = full.model_dump()
    if mutation == "point":
        values["calculation_trace"] = values["calculation_trace"][:-1]
    elif mutation == "safe_idle":
        values["safe_idle_cents"] += 1
    elif mutation == "drop_product":
        values["max_allocatable_by_product"] = {str(UUID(int=777)): 1}
    else:
        values["calculation_trace"][0]["protected_cents_by_reason"]["emergency"] = 0
    forged = full.__class__.model_validate(values)
    result = derive_multi_template_impact(
        original.model_copy(
            update={
                "full_projection": original.full_projection.model_copy(
                    update={"full_annual_projection": forged}
                )
            }
        )
    )
    assert (
        result.status == "UNKNOWN" and result.after is None and result.delta_safe_idle_cents is None
    )


@pytest.mark.parametrize(
    "field,value",
    [("amount_cents", True), ("amount_cents", 1.25), ("grant", True), ("now", "2030-01-01")],
)
def test_candidate_money_authority_and_clock_overrides_are_rejected(field: str, value: Any) -> None:
    original = data()
    with pytest.raises(ValueError):
        derive_multi_template_impact(
            original.model_copy(
                update={
                    "candidate_configuration": {**original.candidate_configuration, field: value}
                }
            )
        )


def full_data(template: Any, current: dict[str, Any], **extra: Any) -> MultiTemplateChangeInput:
    original = data()
    values = dict(
        user_id=original.user_id,
        epoch_id=original.epoch_id,
        policy_id=UUID(int=2),
        version_id=UUID(int=3),
        source_kind="FULL_POLICY",
        template_name=template,
        current_configuration=validate_full_configuration(template, current),
        candidate_configuration=validate_full_configuration(template, current),
        candidate_valid_from=NOW,
        snapshot=snapshot(10000),
        boundary_versions=[],
        positions=[],
        boundary_products=[],
        full_projection=original.full_projection,
        full_sources=[],
        source_originals={"purpose": "TOOL_ONLY_SYNTHETIC"},
    )
    values.update(extra)
    return protection(MultiTemplateChangeInput.model_validate(values))


def test_asset_all_actual_products_compare_finite_caps_and_minimum_without_fake_portfolio() -> None:
    request = asset_request(cash=10000, cap=9000)
    original = full_data(
        "AssetAuthorizationPolicy",
        request.configuration.model_dump(mode="json"),
        snapshot=request.snapshot,
        boundary_versions=request.boundary_versions,
        positions=request.positions,
        boundary_products=request.boundary_products,
        asset_input=request,
        candidate_configuration={
            **request.configuration.model_dump(mode="json"),
            "single_action_cap_cents": 4000,
        },
    )
    result = derive_multi_template_impact(original)
    assert result.status == "PARTIAL" and result.scope == "INDIVIDUAL_PRODUCT_CAPACITY"
    assert len(result.product_capacities) == len(request.products) == 2
    assert all(
        row.before_capacity_cents == 9000
        and row.after_capacity_cents == 4000
        and row.delta_cents == -5000
        for row in result.product_capacities
    )
    assert result.before == result.after and result.delta_safe_idle_cents == 0
    assert "WHOLE_PORTFOLIO" in result.reasons[0]


def test_asset_changed_scope_and_missing_original_confirmation_cannot_borrow_current_exposure() -> (
    None
):
    request = asset_request(cash=10000, cap=10000)
    original = full_data(
        "AssetAuthorizationPolicy",
        request.configuration.model_dump(mode="json"),
        snapshot=request.snapshot,
        boundary_products=request.boundary_products,
        asset_input=request,
    )
    candidate = {**original.candidate_configuration, "scope": "goal", "goal_id": str(UUID(int=9))}
    assert (
        derive_multi_template_impact(
            original.model_copy(update={"candidate_configuration": candidate})
        ).status
        == "UNKNOWN"
    )
    assert (
        derive_multi_template_impact(
            original.model_copy(
                update={
                    "asset_input": request.model_copy(update={"planning_confirmation_valid": False})
                }
            )
        ).status
        == "UNKNOWN"
    )


def test_current_asset_future_confirmation_cannot_supply_today_capacity() -> None:
    request = asset_request(cash=10000, cap=10000)
    original = full_data(
        "AssetAuthorizationPolicy",
        request.configuration.model_dump(mode="json"),
        snapshot=request.snapshot,
        boundary_products=request.boundary_products,
        asset_input=request.model_copy(
            update={"confirmed_at": request.snapshot.as_of + timedelta(seconds=1)}
        ),
    )
    result = derive_multi_template_impact(original)
    assert result.status == "UNKNOWN" and not result.product_capacities


def test_recovery_real_quote_cap_comparison_retains_late_and_all_holding_denominator() -> None:
    request = recovery_request()
    original = full_data(
        "RecoveryPolicy",
        request.configuration.model_dump(mode="json"),
        user_id=request.user_id,
        policy_id=request.policy_id,
        version_id=request.policy_version_id,
        snapshot=request.snapshot,
        boundary_versions=request.boundary_versions,
        positions=request.positions,
        boundary_products=request.boundary_products,
        recovery_input=request,
        candidate_configuration={
            **request.configuration.model_dump(mode="json"),
            "single_action_cap_cents": 10000,
        },
    )
    result = derive_multi_template_impact(original)
    assert (
        result.status == "PARTIAL" and len(result.recovery_candidates) == len(request.holdings) == 1
    )
    row = result.recovery_candidates[0]
    assert row.before["lossless_eligible"] and not row.after["lossless_eligible"]
    assert row.conditional_on_time_net_delta_cents == -50000
    assert result.before == result.after and result.delta_minimum_margin_cents == 0
    missing = original.model_copy(
        update={"recovery_input": request.model_copy(update={"holdings": []})}
    )
    assert derive_multi_template_impact(missing).status == "UNKNOWN"


def test_goal_rule_actual_cap_recomputes_original_all_goal_and_income_denominators() -> None:
    joint = joint_request(cash=20)
    current = {
        "type": "goal_allocation",
        "goal_ids": [str(g.goal_id) for g in joint.goals],
        "max_single_allocation_cents": 20,
    }
    original = full_data(
        "GoalAllocationPolicy",
        current,
        user_id=joint.user_id,
        snapshot=snapshot(20, now=joint.as_of),
        candidate_valid_from=joint.as_of,
        candidate_configuration=validate_full_configuration(
            "GoalAllocationPolicy", {**current, "max_single_allocation_cents": 10}
        ),
    )
    full = original.full_projection.full_annual_projection
    assert full is not None
    points = [
        HardProtectionPoint(
            date=p.date,
            cash_cents=p.cash_cents,
            obligation_floor_cents=0,
            living_floor_cents=0,
            emergency_floor_cents=0,
            owned_goal_cash_cents=0,
            source_refs=joint.hard_protection_points[0].source_refs,
        )
        for p in full.calculation_trace
    ]
    original = original.model_copy(
        update={"joint_input": joint.model_copy(update={"hard_protection_points": points})}
    )
    prior = original.model_dump_json()
    result = derive_multi_template_impact(original)
    assert (
        result.status == "PARTIAL"
        and result.goal_allocation_before is not None
        and result.goal_allocation_after is not None
    )
    assert {g.goal_id for g in result.goal_allocation_after.goals} == {
        g.goal_id for g in joint.goals
    }
    assert sum(g.amount_cents or 0 for g in result.goal_allocation_before.goals) == 20
    assert sum(g.amount_cents or 0 for g in result.goal_allocation_after.goals) == 10
    assert original.model_dump_json() == prior
    forged = original.model_copy(update={"joint_input": joint})
    assert derive_multi_template_impact(forged).status == "UNKNOWN"


@pytest.mark.parametrize(
    "template,config,reason",
    [
        (
            "InterventionPolicy",
            {"type": "intervention", "must_ask_on": list(DEFAULT_INTERVENTIONS)},
            "NO_MONETARY",
        ),
        (
            "SeasonalReservePolicy",
            {
                "type": "seasonal_reserve",
                "holiday_code": "NEW_YEAR",
                "window": {"start": "2026-12-01", "end": "2026-12-31"},
                "lookback_days": 365,
                "minimum_historical_windows": 1,
                "quantile": 0.5,
                "essential_categories": ["food"],
                "adjustment_cap_cents": 500,
            },
            "ADOPTION",
        ),
        ("CrossGoalReallocationPolicy", {"type": "cross_goal_reallocation"}, "DEDICATED_RELEASE"),
    ],
)
def test_unsupported_money_never_uses_valid_schema_as_a_zero_success(
    template: Any, config: dict[str, Any], reason: str
) -> None:
    result = derive_multi_template_impact(full_data(template, config))
    assert result.status == "UNKNOWN" and result.after is None
    assert (
        result.delta_safe_idle_cents is None
        and result.delta_product_financial_capacity_cents is None
    )
    assert any(reason in item for item in result.reasons)


def test_original_source_issue_kept_unknown_and_input_hash_binds_all_actual_originals() -> None:
    original = data()
    assert (
        derive_multi_template_impact(
            original.model_copy(update={"source_issues": ["BANK_UNKNOWN"]})
        ).status
        == "UNKNOWN"
    )
    changed = deepcopy(original.source_originals)
    changed["raw_original_failure"] = "RETAINED"
    assert (
        derive_multi_template_impact(original).input_hash
        != derive_multi_template_impact(
            original.model_copy(update={"source_originals": changed})
        ).input_hash
    )
