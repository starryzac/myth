"""Policy DSL contracts at the public validation and canonical-hash boundary."""

from copy import deepcopy
from datetime import date, datetime
from typing import Any

import pytest
from app.domain.policy_configuration import configuration_hash, validate_configuration


def test_recurring_obligation_normalizes_reviewable_defaults_and_iso_dates() -> None:
    configuration: dict[str, Any] = {
        "type": "recurring_obligation",
        "name": "房租",
        "payee_id": "landlord_001",
        "amount_rule": {"kind": "range", "min_cents": 175_000, "max_cents": 185_000},
        "due_day": 28,
        "valid_from": "2026-10-01",
        "valid_until": "2027-06-30",
    }

    assert validate_configuration(configuration) == {
        **configuration,
        "prepare_days_before": 0,
        "auto_execute": False,
        "priority": {
            "importance": 50,
            "minimum_cents": 0,
            "reducible": False,
            "deferrable": False,
        },
    }


def test_hash_is_canonical_json_even_for_an_incomplete_candidate() -> None:
    expected = "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    assert configuration_hash({"b": 2, "a": 1}) == expected
    assert configuration_hash({"a": 1, "b": 2}) == expected
    assert configuration_hash({"a": 1, "b": 3}) != expected


@pytest.mark.parametrize(
    "amount_rule",
    [
        {"kind": "exact", "amount_cents": 180_000},
        {"kind": "bill_balance", "account_id": "5586c29e-4744-4644-831a-9b75816015f6"},
    ],
)
def test_recurring_obligation_accepts_exact_or_bank_bill_reference(
    amount_rule: dict[str, Any],
) -> None:
    normalized = validate_configuration(
        {
            "type": "recurring_obligation",
            "payee_id": "known-payee",
            "amount_rule": amount_rule,
            "due_day": 10,
        }
    )
    assert normalized["amount_rule"] == amount_rule
    assert normalized["valid_from"] is None
    assert normalized["valid_until"] is None
    assert normalized["auto_execute"] is False


def test_living_reserve_keeps_confirmed_categories_and_explicit_safety_defaults() -> None:
    configuration = {
        "type": "living_reserve",
        "horizon_days": 14,
        "method": {
            "name": "rolling_window_quantile",
            "lookback_days": 56,
            "quantile": 0.8,
            "essential_categories": ["food", "transport"],
        },
    }
    assert validate_configuration(configuration) == {
        "type": "living_reserve",
        "name": None,
        "valid_from": None,
        "valid_until": None,
        "horizon_days": 14,
        "method": {
            "name": "rolling_window_quantile",
            "lookback_days": 56,
            "quantile": 0.8,
            "essential_categories": ["food", "transport"],
            "exclude_one_off": True,
        },
        "extra_buffer_cents": 0,
        "reconfirm_on_boundary_crossing": True,
    }


def test_goal_saving_preserves_monthly_range_and_defaults_cross_goal_permission_to_false() -> None:
    configuration = {
        "type": "goal_saving",
        "name": "买车",
        "target_cents": 3_000_000,
        "deadline": "2027-10-01",
        "monthly_contribution": {
            "min_cents": 180_000,
            "target_cents": 200_000,
            "max_cents": 250_000,
        },
    }
    normalized = validate_configuration(configuration)
    assert normalized["deadline"] == "2027-10-01"
    assert normalized["monthly_contribution"] == configuration["monthly_contribution"]
    assert normalized["cross_goal_reallocation_allowed"] is False
    assert normalized["asset_policy_id"] is None
    assert normalized["priority"] == {
        "importance": 50,
        "minimum_cents": 0,
        "reducible": False,
        "deferrable": False,
    }


@pytest.mark.parametrize("scope", ["general_idle_funds", "goal"])
def test_asset_authorization_supports_mvp_products_and_explicit_goal_ownership(scope: str) -> None:
    configuration: dict[str, Any] = {
        "type": "asset_authorization",
        "scope": scope,
        "allowed_asset_classes": ["CASH", "CASH_MGMT_T0", "CASH_MGMT_T1", "FIXED_DEPOSIT"],
        "max_auto_managed_cents": 300_000,
        "single_action_cap_cents": 150_000,
        "max_redemption_delay_days": 1,
        "max_lock_days": 30,
    }
    if scope == "goal":
        configuration["goal_id"] = "5586C29E-4744-4644-831A-9B75816015F6"
    normalized = validate_configuration(configuration)
    assert normalized["scope"] == scope
    assert normalized["allowed_asset_classes"] == configuration["allowed_asset_classes"]
    assert normalized["max_principal_risk_level"] == 0
    assert normalized["allow_auto_recovery_without_penalty"] is False
    assert normalized["allow_early_withdrawal_with_penalty"] is False
    assert normalized["goal_id"] == (
        "5586c29e-4744-4644-831a-9b75816015f6" if scope == "goal" else None
    )


def test_emergency_buffer_is_only_an_explicit_amount_and_common_policy_metadata() -> None:
    assert validate_configuration(
        {
            "type": "emergency_buffer",
            "name": "应急缓冲",
            "amount_cents": 500_000,
        }
    ) == {
        "type": "emergency_buffer",
        "name": "应急缓冲",
        "amount_cents": 500_000,
        "valid_from": None,
        "valid_until": None,
    }


@pytest.mark.parametrize(
    "configuration",
    [
        None,
        [],
        {1: "non-string-key"},
        {"tuple": (1, 2)},
        {"object": object()},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": float("-inf")},
    ],
)
def test_hash_rejects_non_json_roots_values_and_nonfinite_numbers(configuration: Any) -> None:
    with pytest.raises(ValueError):
        configuration_hash(configuration)


_VALID: dict[str, dict[str, Any]] = {
    "recurring_obligation": {
        "type": "recurring_obligation",
        "payee_id": "landlord_001",
        "due_day": 28,
        "amount_rule": {"kind": "exact", "amount_cents": 180_000},
        "priority": {"minimum_cents": 100_000},
    },
    "living_reserve": {
        "type": "living_reserve",
        "horizon_days": 14,
        "extra_buffer_cents": 50_000,
        "method": {
            "name": "rolling_window_quantile",
            "lookback_days": 56,
            "quantile": 0.8,
            "essential_categories": ["food", "transport"],
        },
    },
    "goal_saving": {
        "type": "goal_saving",
        "target_cents": 3_000_000,
        "deadline": "2027-10-01",
        "monthly_contribution": {
            "min_cents": 180_000,
            "target_cents": 200_000,
            "max_cents": 250_000,
        },
        "priority": {"minimum_cents": 100_000},
    },
    "asset_authorization": {
        "type": "asset_authorization",
        "scope": "general_idle_funds",
        "allowed_asset_classes": ["CASH", "CASH_MGMT_T0", "CASH_MGMT_T1", "FIXED_DEPOSIT"],
        "max_auto_managed_cents": 300_000,
        "single_action_cap_cents": 150_000,
        "max_redemption_delay_days": 1,
        "max_lock_days": 30,
    },
    "emergency_buffer": {"type": "emergency_buffer", "amount_cents": 500_000},
}


def _changed(kind: str, path: str, value: Any) -> dict[str, Any]:
    configuration = deepcopy(_VALID[kind])
    target = configuration
    keys = path.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    return configuration


@pytest.mark.parametrize("invalid", [True, False, 1.5, "100", -1, 9_223_372_036_854_775_808])
@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("recurring_obligation", "amount_rule.amount_cents"),
        ("recurring_obligation", "priority.minimum_cents"),
        ("living_reserve", "extra_buffer_cents"),
        ("goal_saving", "target_cents"),
        ("goal_saving", "monthly_contribution.min_cents"),
        ("goal_saving", "monthly_contribution.target_cents"),
        ("goal_saving", "monthly_contribution.max_cents"),
        ("goal_saving", "priority.minimum_cents"),
        ("asset_authorization", "max_auto_managed_cents"),
        ("asset_authorization", "single_action_cap_cents"),
        ("emergency_buffer", "amount_cents"),
    ],
)
def test_money_is_nonnegative_integer_cents_without_type_coercion(
    kind: str,
    path: str,
    invalid: Any,
) -> None:
    with pytest.raises(ValueError):
        validate_configuration(_changed(kind, path, invalid))


@pytest.mark.parametrize(
    ("kind", "path", "value"),
    [
        ("recurring_obligation", "due_day", 0),
        ("recurring_obligation", "due_day", 32),
        ("recurring_obligation", "due_day", True),
        ("recurring_obligation", "due_day", 28.0),
        ("recurring_obligation", "prepare_days_before", -1),
        ("recurring_obligation", "payee_id", "  "),
        (
            "recurring_obligation",
            "amount_rule",
            {"kind": "range", "min_cents": 20, "max_cents": 10},
        ),
        (
            "recurring_obligation",
            "amount_rule",
            {"kind": "range", "min_cents": True, "max_cents": 10},
        ),
        (
            "recurring_obligation",
            "amount_rule",
            {"kind": "range", "min_cents": 0, "max_cents": 10.5},
        ),
        ("recurring_obligation", "amount_rule", {"kind": "bill_balance", "account_id": "unknown"}),
        ("recurring_obligation", "amount_rule", {"kind": "model_prediction", "amount_cents": 100}),
        ("recurring_obligation", "priority.importance", 101),
        ("recurring_obligation", "priority.importance", -1),
        ("recurring_obligation", "priority.reducible", "true"),
        ("recurring_obligation", "auto_execute", 1),
        ("living_reserve", "horizon_days", 0),
        ("living_reserve", "horizon_days", 57),
        ("living_reserve", "method.lookback_days", 0),
        ("living_reserve", "method.lookback_days", "56"),
        ("living_reserve", "method.quantile", 0),
        ("living_reserve", "method.quantile", 1.01),
        ("living_reserve", "method.quantile", True),
        ("living_reserve", "method.quantile", "0.8"),
        ("living_reserve", "method.quantile", float("nan")),
        ("living_reserve", "method.quantile", float("inf")),
        ("living_reserve", "method.essential_categories", []),
        ("living_reserve", "method.essential_categories", [""]),
        ("living_reserve", "method.exclude_one_off", "false"),
        ("goal_saving", "target_cents", 0),
        ("goal_saving", "monthly_contribution.min_cents", 200_001),
        ("goal_saving", "monthly_contribution.max_cents", 199_999),
        ("goal_saving", "asset_policy_id", "goal_asset_policy_car"),
        ("goal_saving", "cross_goal_reallocation_allowed", "false"),
        ("asset_authorization", "scope", "all_accounts"),
        ("asset_authorization", "scope", "goal"),
        ("asset_authorization", "goal_id", "5586c29e-4744-4644-831a-9b75816015f6"),
        ("asset_authorization", "allowed_asset_classes", []),
        ("asset_authorization", "allowed_asset_classes", ["STOCK"]),
        ("asset_authorization", "single_action_cap_cents", 300_001),
        ("asset_authorization", "max_redemption_delay_days", -1),
        ("asset_authorization", "max_lock_days", -1),
        ("asset_authorization", "max_principal_risk_level", 6),
        ("asset_authorization", "max_principal_risk_level", True),
        ("asset_authorization", "allow_early_withdrawal_with_penalty", 1),
    ],
)
def test_invalid_windows_ranges_permissions_and_template_fields_are_rejected(
    kind: str,
    path: str,
    value: Any,
) -> None:
    with pytest.raises(ValueError):
        validate_configuration(_changed(kind, path, value))


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("recurring_obligation", "amount_rule.approved"),
        ("recurring_obligation", "priority.approved"),
        ("living_reserve", "method.approved"),
        ("goal_saving", "monthly_contribution.approved"),
        ("goal_saving", "priority.approved"),
        *((kind, "approved") for kind in _VALID),
    ],
)
def test_unknown_fields_are_forbidden_at_every_template_level(kind: str, path: str) -> None:
    with pytest.raises(ValueError):
        validate_configuration(_changed(kind, path, True))


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-30",
        "2026-2-03",
        "20261003",
        "2026-10-03T00:00:00Z",
        1790985600,
        datetime(2026, 10, 3),
    ],
)
def test_dates_are_calendar_dates_without_timestamp_or_datetime_coercion(value: Any) -> None:
    with pytest.raises(ValueError):
        validate_configuration(_changed("emergency_buffer", "valid_from", value))


def test_date_windows_are_ordered_and_goal_deadline_cannot_precede_effective_start() -> None:
    with pytest.raises(ValueError):
        validate_configuration(
            {**_VALID["emergency_buffer"], "valid_from": "2026-10-04", "valid_until": "2026-10-03"}
        )
    with pytest.raises(ValueError):
        validate_configuration({**_VALID["goal_saving"], "valid_from": "2027-10-02"})
    normalized = validate_configuration(
        {**_VALID["emergency_buffer"], "valid_from": date(2026, 10, 3), "valid_until": "2026-10-03"}
    )
    assert normalized["valid_from"] == normalized["valid_until"] == "2026-10-03"


@pytest.mark.parametrize("kind", list(_VALID))
def test_normalization_is_deterministic_idempotent_and_does_not_mutate_input(kind: str) -> None:
    configuration = deepcopy(_VALID[kind])
    original = deepcopy(configuration)
    normalized = validate_configuration(configuration)
    assert configuration == original
    assert validate_configuration(normalized) == normalized
    assert configuration_hash(validate_configuration(configuration)) == configuration_hash(
        normalized
    )


@pytest.mark.parametrize("configuration", [None, [], {}, {"type": "twelve_template_optimizer"}])
def test_only_supported_object_configurations_can_be_validated(configuration: Any) -> None:
    with pytest.raises(ValueError):
        validate_configuration(configuration)
