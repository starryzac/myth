"""FULL-103 schema contracts only; these tests grant no financial authority."""

from copy import deepcopy
from typing import Any

import pytest
from app.domain.full_policy_configuration import (
    DEFAULT_INTERVENTIONS,
    DSLVersion,
    TemplateName,
    template_model,
    template_names,
    template_schema,
    validate_full_configuration,
)
from app.domain.policy_configuration import (
    AssetAuthorization,
    EmergencyBuffer,
    GoalSaving,
    LivingReserve,
    RecurringObligation,
    configuration_hash,
    validate_configuration,
)

FIRST = "5586c29e-4744-4644-831a-9b75816015f6"
SECOND = "931996c0-66c2-4e72-a725-72e80bdd1c4e"
THIRD = "6cc08a71-7dd2-4e77-b51b-331f9dd93f60"

EXAMPLES: dict[TemplateName, dict[str, Any]] = {
    "RecurringObligationPolicy": {
        "type": "recurring_obligation",
        "payee_id": "known-landlord",
        "due_day": 28,
        "amount_rule": {"kind": "exact", "amount_cents": 180000},
    },
    "LivingReservePolicy": {
        "type": "living_reserve",
        "horizon_days": 14,
        "method": {
            "name": "rolling_window_quantile",
            "lookback_days": 56,
            "quantile": 0.8,
            "essential_categories": ["food", "transport"],
        },
    },
    "EmergencyBufferPolicy": {"type": "emergency_buffer", "amount_cents": 500000},
    "DatedExpensePolicy": {
        "type": "dated_expense",
        "window": {"start": "2027-10-01", "end": "2027-10-07"},
        "amount": {"min_cents": 300000, "target_cents": 350000, "max_cents": 400000},
        "priority": {"importance": 45, "minimum_cents": 300000, "reducible": True},
        "must_not_reduce_policy_ids": [FIRST],
    },
    "LongTermGoalPolicy": {
        "type": "long_term_goal",
        "target_cents": 3000000,
        "deadline": "2027-10-01",
        "monthly_contribution": {
            "min_cents": 180000,
            "target_cents": 200000,
            "max_cents": 250000,
        },
        "minimum_guarantee_cents": 100000,
        "allow_partial": True,
        "allow_deferral": True,
        "deferral_cost_cents_per_day": 100,
        "asset_policy_id": FIRST,
        "cross_goal_reallocation_allowed": True,
        "cross_goal_reallocation_policy_id": SECOND,
    },
    "PeriodicTransferPolicy": {
        "type": "periodic_transfer",
        "source_account_id": FIRST,
        "payee_id": "confirmed-family-payee",
        "amount_rule": {"kind": "exact", "amount_cents": 100000},
        "due_day": 31,
        "single_action_cap_cents": 100000,
    },
    "AssetAuthorizationPolicy": {
        "type": "asset_authorization",
        "scope": "goal",
        "goal_id": FIRST,
        "allowed_asset_classes": [
            "CASH",
            "CASH_MGMT_T0",
            "CASH_MGMT_T1",
            "FIXED_DEPOSIT_7D",
            "FIXED_DEPOSIT_30D",
            "FIXED_DEPOSIT_90D",
            "LOW_RISK_TERM",
        ],
        "max_auto_managed_cents": 300000,
        "single_action_cap_cents": 100000,
        "max_redemption_delay_days": 1,
        "max_lock_days": 90,
    },
    "RecoveryPolicy": {
        "type": "recovery",
        "scope": "goal",
        "goal_id": FIRST,
        "asset_policy_id": SECOND,
        "triggers": ["BOUNDARY_SHRINK", "AUTHORIZATION_REVOKED"],
        "single_action_cap_cents": 100000,
        "max_redemption_delay_days": 1,
    },
    "GoalAllocationPolicy": {
        "type": "goal_allocation",
        "goal_ids": [FIRST, SECOND],
        "max_single_allocation_cents": 300000,
    },
    "CrossGoalReallocationPolicy": {
        "type": "cross_goal_reallocation",
        "enabled": True,
        "source_goal_ids": [FIRST, SECOND],
        "emergency_conditions": ["HARD_OBLIGATION_SHORTFALL"],
        "valid_from": "2026-10-01",
        "valid_until": "2027-10-01",
        "single_action_cap_cents": 100000,
        "total_cap_cents": 200000,
    },
    "SeasonalReservePolicy": {
        "type": "seasonal_reserve",
        "holiday_code": "NATIONAL_DAY",
        "window": {"start": "2027-10-01", "end": "2027-10-07"},
        "lookback_days": 730,
        "minimum_historical_windows": 2,
        "quantile": 0.8,
        "essential_categories": ["food", "transport"],
        "adjustment_cap_cents": 100000,
    },
    "InterventionPolicy": {"type": "intervention"},
}


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_every_template_has_a_strict_schema_and_deterministic_nonmutating_candidate(
    name: TemplateName,
) -> None:
    original = deepcopy(EXAMPLES[name])
    normalized = validate_full_configuration(name, EXAMPLES[name])
    assert EXAMPLES[name] == original
    assert validate_full_configuration(name, normalized) == normalized
    assert normalized["type"] == original["type"]
    assert configuration_hash(normalized) == configuration_hash(
        validate_full_configuration(name, original)
    )
    schema = template_schema(name, "FULL_V1")
    assert schema["additionalProperties"] is False
    for nested in schema.get("$defs", {}).values():
        if nested.get("type") == "object":
            assert nested["additionalProperties"] is False
    assert schema["properties"]["type"]["const"] == original["type"]
    assert {field for field in schema["required"]}.issubset(original)


@pytest.mark.parametrize("name", list(EXAMPLES))
@pytest.mark.parametrize("field", ["approved", "status", "user_id", "authority_granted"])
def test_no_template_accepts_unknown_authority_or_state_fields(
    name: TemplateName, field: str
) -> None:
    with pytest.raises(ValueError):
        validate_full_configuration(name, {**EXAMPLES[name], field: "ACTIVE"})


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_each_template_enforces_common_calendar_dates_and_window_order(name: TemplateName) -> None:
    with pytest.raises(ValueError):
        validate_full_configuration(
            name, {**EXAMPLES[name], "valid_from": "2026-10-05", "valid_until": "2026-10-04"}
        )
    with pytest.raises(ValueError):
        validate_full_configuration(name, {**EXAMPLES[name], "valid_from": "20261005"})


def changed(name: TemplateName, path: str, value: Any) -> dict[str, Any]:
    candidate = deepcopy(EXAMPLES[name])
    parts = path.split(".")
    target = candidate
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    return candidate


@pytest.mark.parametrize(
    ("name", "path", "value"),
    [
        ("DatedExpensePolicy", "window.end", "2027-09-30"),
        ("DatedExpensePolicy", "valid_from", "2027-10-02"),
        ("DatedExpensePolicy", "valid_until", "2027-10-06"),
        ("DatedExpensePolicy", "amount.target_cents", 400001),
        ("DatedExpensePolicy", "priority.minimum_cents", 400001),
        ("DatedExpensePolicy", "must_not_reduce_policy_ids", [FIRST, FIRST]),
        ("DatedExpensePolicy", "window.accepted", True),
        ("DatedExpensePolicy", "amount.currency", "USD"),
        ("LongTermGoalPolicy", "deadline", "2026-02-30"),
        ("LongTermGoalPolicy", "valid_from", "2027-10-02"),
        ("LongTermGoalPolicy", "valid_until", "2027-09-30"),
        ("LongTermGoalPolicy", "minimum_guarantee_cents", 3000001),
        ("LongTermGoalPolicy", "allow_deferral", False),
        ("LongTermGoalPolicy", "cross_goal_reallocation_policy_id", None),
        ("LongTermGoalPolicy", "cross_goal_reallocation_allowed", False),
        ("LongTermGoalPolicy", "importance", 101),
        ("LongTermGoalPolicy", "current_owned_cents", 500000),
        ("LongTermGoalPolicy", "effective_policy_version_id", THIRD),
        ("LongTermGoalPolicy", "monthly_contribution.authorized", True),
        ("PeriodicTransferPolicy", "amount_rule.amount_cents", 100001),
        ("PeriodicTransferPolicy", "due_day", 32),
        ("PeriodicTransferPolicy", "payee_id", " "),
        ("PeriodicTransferPolicy", "source_account_id", "new-source"),
        ("PeriodicTransferPolicy", "amount_rule", {"kind": "bill_balance", "account_id": FIRST}),
        ("PeriodicTransferPolicy", "amount_rule.confirmed", True),
        ("AssetAuthorizationPolicy", "goal_id", None),
        ("AssetAuthorizationPolicy", "single_action_cap_cents", 300001),
        ("AssetAuthorizationPolicy", "allowed_asset_classes", ["STOCK"]),
        ("AssetAuthorizationPolicy", "allowed_asset_classes", ["CASH", "CASH"]),
        ("RecoveryPolicy", "goal_id", None),
        ("RecoveryPolicy", "max_fee_cents", 1),
        ("RecoveryPolicy", "max_loss_cents", 1),
        ("RecoveryPolicy", "triggers", ["BOUNDARY_SHRINK", "BOUNDARY_SHRINK"]),
        ("RecoveryPolicy", "triggers", ["MODEL_CONFIDENCE_HIGH"]),
        ("RecoveryPolicy", "allow_auto_recovery_without_penalty", "true"),
        ("GoalAllocationPolicy", "goal_ids", [FIRST]),
        ("GoalAllocationPolicy", "goal_ids", [FIRST, FIRST.upper()]),
        ("GoalAllocationPolicy", "funds_scope", "FUTURE_INCOME"),
        ("GoalAllocationPolicy", "funds_scope", "EXISTING_GOAL_FUNDS"),
        ("GoalAllocationPolicy", "method", "RETURN_MAXIMIZATION"),
        ("CrossGoalReallocationPolicy", "enabled", "true"),
        ("CrossGoalReallocationPolicy", "valid_until", None),
        ("CrossGoalReallocationPolicy", "valid_from", None),
        ("CrossGoalReallocationPolicy", "source_goal_ids", []),
        ("CrossGoalReallocationPolicy", "emergency_conditions", []),
        ("CrossGoalReallocationPolicy", "emergency_conditions", ["HIGHER_YIELD"]),
        ("CrossGoalReallocationPolicy", "single_action_cap_cents", 200001),
        ("CrossGoalReallocationPolicy", "destination_scope", "OTHER_GOAL"),
        ("SeasonalReservePolicy", "window.end", "2027-09-30"),
        ("SeasonalReservePolicy", "valid_until", "2027-10-06"),
        ("SeasonalReservePolicy", "lookback_days", 6),
        ("SeasonalReservePolicy", "minimum_historical_windows", 0),
        ("SeasonalReservePolicy", "essential_categories", ["food", "food"]),
        ("SeasonalReservePolicy", "requires_confirmation", False),
        ("SeasonalReservePolicy", "advice_only", False),
        ("SeasonalReservePolicy", "quantile", "0.8"),
        ("InterventionPolicy", "must_ask_on", list(DEFAULT_INTERVENTIONS[:-1])),
        ("InterventionPolicy", "must_ask_on", [*DEFAULT_INTERVENTIONS[:-1], "NEW_PAYEE"]),
        ("InterventionPolicy", "minimum_reask_interval_seconds", -1),
        ("InterventionPolicy", "safety_events_bypass_throttle", False),
        ("InterventionPolicy", "silent_when_action_set_unchanged", False),
        ("InterventionPolicy", "deduplicate_by_boundary_event", False),
    ],
)
def test_cross_field_scope_amount_time_and_permission_constraints_reject(
    name: TemplateName, path: str, value: Any
) -> None:
    with pytest.raises(ValueError):
        validate_full_configuration(name, changed(name, path, value))


@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, "100", 9223372036854775808])
@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("DatedExpensePolicy", "amount.min_cents"),
        ("LongTermGoalPolicy", "minimum_guarantee_cents"),
        ("LongTermGoalPolicy", "deferral_cost_cents_per_day"),
        ("PeriodicTransferPolicy", "single_action_cap_cents"),
        ("RecoveryPolicy", "max_loss_cents"),
        ("GoalAllocationPolicy", "max_single_allocation_cents"),
        ("CrossGoalReallocationPolicy", "total_cap_cents"),
        ("SeasonalReservePolicy", "adjustment_cap_cents"),
    ],
)
def test_new_money_fields_use_exact_bounded_integer_cents(
    name: TemplateName, path: str, invalid: Any
) -> None:
    with pytest.raises(ValueError):
        validate_full_configuration(name, changed(name, path, invalid))


def test_disabled_reallocation_has_no_permission_and_cannot_carry_live_caps() -> None:
    disabled = validate_full_configuration(
        "CrossGoalReallocationPolicy", {"type": "cross_goal_reallocation"}
    )
    assert disabled["enabled"] is False
    assert disabled["single_action_cap_cents"] == disabled["total_cap_cents"] == 0
    assert disabled["emergency_conditions"] == []
    with pytest.raises(ValueError):
        validate_full_configuration(
            "CrossGoalReallocationPolicy", {**disabled, "single_action_cap_cents": 1}
        )


def test_transfer_range_cap_and_full_goal_defaults_are_explicit() -> None:
    candidate = deepcopy(EXAMPLES["PeriodicTransferPolicy"])
    candidate["amount_rule"] = {"kind": "range", "min_cents": 50000, "max_cents": 100000}
    assert validate_full_configuration("PeriodicTransferPolicy", candidate)["auto_execute"] is False
    with pytest.raises(ValueError):
        validate_full_configuration(
            "PeriodicTransferPolicy", {**candidate, "single_action_cap_cents": 99999}
        )
    goal = deepcopy(EXAMPLES["LongTermGoalPolicy"])
    for field in (
        "allow_partial",
        "allow_deferral",
        "deferral_cost_cents_per_day",
        "cross_goal_reallocation_allowed",
        "cross_goal_reallocation_policy_id",
    ):
        del goal[field]
    normalized = validate_full_configuration("LongTermGoalPolicy", goal)
    assert normalized["allow_partial"] is False
    assert normalized["allow_deferral"] is False
    assert normalized["deferral_cost_cents_per_day"] == 0
    assert normalized["cross_goal_reallocation_allowed"] is False
    assert normalized["cross_goal_reallocation_policy_id"] is None


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("RecurringObligationPolicy", RecurringObligation),
        ("LivingReservePolicy", LivingReserve),
        ("EmergencyBufferPolicy", EmergencyBuffer),
        ("LongTermGoalPolicy", GoalSaving),
        ("AssetAuthorizationPolicy", AssetAuthorization),
    ],
)
def test_mvp_schema_model_normalization_and_hash_remain_exact(
    name: TemplateName, model: type[Any]
) -> None:
    candidate = deepcopy(EXAMPLES[name])
    if name == "LongTermGoalPolicy":
        candidate = {
            "type": "goal_saving",
            "target_cents": 3000000,
            "deadline": "2027-10-01",
            "monthly_contribution": EXAMPLES[name]["monthly_contribution"],
        }
    elif name == "AssetAuthorizationPolicy":
        candidate["allowed_asset_classes"] = [
            "CASH",
            "CASH_MGMT_T0",
            "CASH_MGMT_T1",
            "FIXED_DEPOSIT",
        ]
    assert template_model(name, "MVP_V1") is model
    assert template_schema(name, "MVP_V1") == model.model_json_schema()
    old = validate_configuration(candidate)
    new = validate_full_configuration(name, candidate, version="MVP_V1")
    assert old == new
    assert configuration_hash(old) == configuration_hash(new)


def test_full_asset_schema_cannot_enable_old_mvp_product_or_expand_mvp_types() -> None:
    candidate = deepcopy(EXAMPLES["AssetAuthorizationPolicy"])
    with pytest.raises(ValueError):
        validate_full_configuration("AssetAuthorizationPolicy", candidate, version="MVP_V1")
    candidate["allowed_asset_classes"] = ["FIXED_DEPOSIT"]
    with pytest.raises(ValueError):
        validate_full_configuration("AssetAuthorizationPolicy", candidate)
    with pytest.raises(ValueError):
        validate_configuration(EXAMPLES["LongTermGoalPolicy"])


@pytest.mark.parametrize("version", ["MVP_V1", "FULL_V1"])
def test_template_version_and_json_root_cannot_silently_fallback(version: DSLVersion) -> None:
    assert len(template_names()) == 12
    with pytest.raises(ValueError):
        validate_full_configuration(
            "EmergencyBufferPolicy", {"type": "intervention"}, version=version
        )
    invalid_roots: tuple[Any, ...] = (
        None,
        [],
        {"type": "emergency_buffer", "amount_cents": float("nan")},
    )
    for invalid in invalid_roots:
        with pytest.raises(ValueError):
            validate_full_configuration("EmergencyBufferPolicy", invalid, version=version)
    with pytest.raises(ValueError):
        template_schema("DatedExpensePolicy", "MVP_V1")
    with pytest.raises(ValueError):
        template_model("UnknownTemplate", "FULL_V1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        template_model("EmergencyBufferPolicy", "FULL_V2")  # type: ignore[arg-type]
