"""Finite FULL policy candidates. Schema validation never confirms facts or authority.

MVP_V1 deliberately uses the original five model classes and canonical hash function.
FULL_V1 adds candidate contracts; its references require later service-side validation.
"""

from typing import Annotated, Any, Literal, Self

from app.domain.policy_configuration import (
    AssetAuthorization,
    CalendarDate,
    Category,
    CommonConfiguration,
    EmergencyBuffer,
    ExactAmount,
    GoalSaving,
    LivingReserve,
    MoneyCents,
    MonthlyContribution,
    NonnegativeInt,
    PositiveInt,
    Priority,
    RangeAmount,
    RecurringObligation,
    Reference,
    StrictModel,
    UUIDReference,
    configuration_hash,
    validate_configuration,
)
from pydantic import BaseModel, Field, StrictBool, StrictInt, StringConstraints, model_validator

DSLVersion = Literal["MVP_V1", "FULL_V1"]
TemplateName = Literal[
    "RecurringObligationPolicy",
    "LivingReservePolicy",
    "EmergencyBufferPolicy",
    "DatedExpensePolicy",
    "LongTermGoalPolicy",
    "PeriodicTransferPolicy",
    "AssetAuthorizationPolicy",
    "RecoveryPolicy",
    "GoalAllocationPolicy",
    "CrossGoalReallocationPolicy",
    "SeasonalReservePolicy",
    "InterventionPolicy",
]
FullAssetClass = Literal[
    "CASH",
    "CASH_MGMT_T0",
    "CASH_MGMT_T1",
    "FIXED_DEPOSIT_7D",
    "FIXED_DEPOSIT_30D",
    "FIXED_DEPOSIT_90D",
    "LOW_RISK_TERM",
]
PositiveMoney = Annotated[MoneyCents, Field(gt=0)]
Importance = Annotated[StrictInt, Field(ge=0, le=100)]
UUIDList = Annotated[list[UUIDReference], Field(max_length=32)]

# These are aliases, not subclasses: MVP schemas, defaults and hashes stay unchanged.
RecurringObligationPolicy = RecurringObligation
LivingReservePolicy = LivingReserve
EmergencyBufferPolicy = EmergencyBuffer


def _unique(values: list[Any], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")


class DateWindow(StrictModel):
    start: CalendarDate
    end: CalendarDate

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end < self.start:
            raise ValueError("window.end must not precede window.start")
        return self


def _within_validity(policy: CommonConfiguration, window: DateWindow) -> None:
    if policy.valid_from is not None and window.start < policy.valid_from:
        raise ValueError("window.start must not precede valid_from")
    if policy.valid_until is not None and window.end > policy.valid_until:
        raise ValueError("window.end must not exceed valid_until")


class DatedExpensePolicy(CommonConfiguration):
    type: Literal["dated_expense"]
    window: DateWindow
    amount: MonthlyContribution
    priority: Priority = Field(default_factory=Priority)
    must_not_reduce_policy_ids: UUIDList = Field(default_factory=list)

    @model_validator(mode="after")
    def expense_constraints(self) -> Self:
        _within_validity(self, self.window)
        _unique(self.must_not_reduce_policy_ids, "must_not_reduce_policy_ids")
        if self.priority.minimum_cents > self.amount.max_cents:
            raise ValueError("priority.minimum_cents must not exceed amount.max_cents")
        return self


class LongTermGoalPolicy(CommonConfiguration):
    type: Literal["long_term_goal"]
    target_cents: PositiveMoney
    deadline: CalendarDate
    monthly_contribution: MonthlyContribution
    importance: Importance = 50
    minimum_guarantee_cents: MoneyCents = 0
    allow_partial: StrictBool = False
    allow_deferral: StrictBool = False
    deferral_cost_cents_per_day: MoneyCents = 0
    asset_policy_id: UUIDReference | None = None
    cross_goal_reallocation_allowed: StrictBool = False
    cross_goal_reallocation_policy_id: UUIDReference | None = None

    @model_validator(mode="after")
    def goal_constraints(self) -> Self:
        if self.valid_from is not None and self.deadline < self.valid_from:
            raise ValueError("deadline must not precede valid_from")
        if self.valid_until is not None and self.deadline > self.valid_until:
            raise ValueError("deadline must not exceed valid_until")
        if self.minimum_guarantee_cents > self.target_cents:
            raise ValueError("minimum_guarantee_cents must not exceed target_cents")
        if not self.allow_deferral and self.deferral_cost_cents_per_day != 0:
            raise ValueError("A deferral cost requires allow_deferral")
        if self.cross_goal_reallocation_allowed != (
            self.cross_goal_reallocation_policy_id is not None
        ):
            raise ValueError("Cross-goal permission requires its explicit policy reference")
        return self


class PeriodicTransferPolicy(CommonConfiguration):
    type: Literal["periodic_transfer"]
    source_account_id: UUIDReference
    payee_id: Reference
    amount_rule: Annotated[ExactAmount | RangeAmount, Field(discriminator="kind")]
    due_day: Annotated[StrictInt, Field(ge=1, le=31)]
    prepare_days_before: NonnegativeInt = 0
    single_action_cap_cents: PositiveMoney
    auto_execute: StrictBool = False

    @model_validator(mode="after")
    def bounded_transfer(self) -> Self:
        upper = (
            self.amount_rule.amount_cents
            if isinstance(self.amount_rule, ExactAmount)
            else self.amount_rule.max_cents
        )
        if upper > self.single_action_cap_cents:
            raise ValueError("The transfer amount must not exceed single_action_cap_cents")
        return self


class AssetAuthorizationPolicy(CommonConfiguration):
    """FULL-only product vocabulary; no change to MVP authorization or execution."""

    type: Literal["asset_authorization"]
    scope: Literal["general_idle_funds", "goal"]
    goal_id: UUIDReference | None = None
    allowed_asset_classes: Annotated[list[FullAssetClass], Field(min_length=1, max_length=7)]
    max_auto_managed_cents: MoneyCents
    single_action_cap_cents: MoneyCents
    max_redemption_delay_days: NonnegativeInt
    max_lock_days: NonnegativeInt
    max_principal_risk_level: Annotated[StrictInt, Field(ge=0, le=5)] = 0
    allow_auto_recovery_without_penalty: StrictBool = False
    allow_early_withdrawal_with_penalty: StrictBool = False

    @model_validator(mode="after")
    def scoped_assets(self) -> Self:
        if (self.scope == "goal") != (self.goal_id is not None):
            raise ValueError("goal scope requires goal_id; general scope forbids it")
        if self.single_action_cap_cents > self.max_auto_managed_cents:
            raise ValueError("single_action_cap_cents must not exceed max_auto_managed_cents")
        _unique(self.allowed_asset_classes, "allowed_asset_classes")
        return self


RecoveryTrigger = Literal[
    "BOUNDARY_SHRINK", "AUTHORIZATION_REVOKED", "POLICY_EXPIRED", "LIQUIDITY_SHORTFALL"
]


class RecoveryPolicy(CommonConfiguration):
    type: Literal["recovery"]
    scope: Literal["general_idle_funds", "goal"]
    goal_id: UUIDReference | None = None
    asset_policy_id: UUIDReference
    triggers: Annotated[list[RecoveryTrigger], Field(min_length=1, max_length=4)]
    single_action_cap_cents: PositiveMoney
    max_redemption_delay_days: NonnegativeInt
    allow_auto_recovery_without_penalty: StrictBool = False
    max_fee_cents: Annotated[MoneyCents, Field(le=0)] = 0
    max_loss_cents: Annotated[MoneyCents, Field(le=0)] = 0

    @model_validator(mode="after")
    def recovery_scope(self) -> Self:
        if (self.scope == "goal") != (self.goal_id is not None):
            raise ValueError("goal scope requires goal_id; general scope forbids it")
        _unique(self.triggers, "triggers")
        return self


class GoalAllocationPolicy(CommonConfiguration):
    type: Literal["goal_allocation"]
    goal_ids: Annotated[list[UUIDReference], Field(min_length=2, max_length=32)]
    method: Literal["lexicographic_v1"] = "lexicographic_v1"
    funds_scope: Literal["NEW_UNASSIGNED_SAFE_FUNDS"] = "NEW_UNASSIGNED_SAFE_FUNDS"
    max_single_allocation_cents: PositiveMoney

    @model_validator(mode="after")
    def unique_goals(self) -> Self:
        _unique(self.goal_ids, "goal_ids")
        return self


EmergencyCondition = Literal[
    "HARD_OBLIGATION_SHORTFALL", "LIVING_RESERVE_SHORTFALL", "EMERGENCY_BUFFER_SHORTFALL"
]


class CrossGoalReallocationPolicy(CommonConfiguration):
    type: Literal["cross_goal_reallocation"]
    enabled: StrictBool = False
    source_goal_ids: UUIDList = Field(default_factory=list)
    emergency_conditions: Annotated[list[EmergencyCondition], Field(max_length=3)] = Field(
        default_factory=list
    )
    destination_scope: Literal["PROTECTED_CASH"] = "PROTECTED_CASH"
    single_action_cap_cents: MoneyCents = 0
    total_cap_cents: MoneyCents = 0

    @model_validator(mode="after")
    def explicit_bounded_emergency_permission(self) -> Self:
        _unique(self.source_goal_ids, "source_goal_ids")
        _unique(self.emergency_conditions, "emergency_conditions")
        if not self.enabled:
            if self.single_action_cap_cents or self.total_cap_cents or self.emergency_conditions:
                raise ValueError(
                    "A disabled reallocation policy must have zero caps and no triggers"
                )
        elif (
            not self.source_goal_ids
            or not self.emergency_conditions
            or self.valid_from is None
            or self.valid_until is None
            or not 0 < self.single_action_cap_cents <= self.total_cap_cents
        ):
            raise ValueError(
                "Enabled reallocation requires goals, emergency triggers, dates and caps"
            )
        return self


class SeasonalReservePolicy(CommonConfiguration):
    type: Literal["seasonal_reserve"]
    holiday_code: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=48)
    ]
    window: DateWindow
    lookback_days: Annotated[PositiveInt, Field(le=3650)]
    minimum_historical_windows: Annotated[PositiveInt, Field(le=10)]
    quantile: Annotated[float, Field(strict=True, gt=0, le=1, allow_inf_nan=False)]
    essential_categories: Annotated[list[Category], Field(min_length=1, max_length=48)]
    adjustment_cap_cents: MoneyCents
    requires_confirmation: StrictBool = True
    advice_only: StrictBool = True

    @model_validator(mode="after")
    def confirmed_advice(self) -> Self:
        _within_validity(self, self.window)
        _unique(self.essential_categories, "essential_categories")
        if (self.window.end - self.window.start).days + 1 > self.lookback_days:
            raise ValueError("lookback_days must cover the seasonal window length")
        if not self.requires_confirmation or not self.advice_only:
            raise ValueError(
                "Seasonal history can only propose an adjustment requiring confirmation"
            )
        return self


InterventionReason = Literal[
    "ACTION_SET_CHANGED",
    "AUTONOMOUS_AMOUNT_DECREASE",
    "RECOVERY_REQUIRED",
    "GOAL_MINIMUM_SHORTFALL",
    "NEW_PAYEE",
    "OUT_OF_AUTHORIZATION",
    "NEW_ASSET_CLASS",
    "NEW_LOSS",
    "NEW_RISK",
    "VALUE_PREFERENCE_CHANGE",
    "UNAUTHORIZED_CONFLICT_REPAIR",
]
MANDATORY_INTERVENTIONS: tuple[InterventionReason, ...] = (
    "NEW_PAYEE",
    "OUT_OF_AUTHORIZATION",
    "NEW_ASSET_CLASS",
    "NEW_LOSS",
    "NEW_RISK",
    "VALUE_PREFERENCE_CHANGE",
    "UNAUTHORIZED_CONFLICT_REPAIR",
)
DEFAULT_INTERVENTIONS: tuple[InterventionReason, ...] = (
    "ACTION_SET_CHANGED",
    "AUTONOMOUS_AMOUNT_DECREASE",
    "RECOVERY_REQUIRED",
    "GOAL_MINIMUM_SHORTFALL",
    *MANDATORY_INTERVENTIONS,
)


class InterventionPolicy(CommonConfiguration):
    type: Literal["intervention"]
    must_ask_on: Annotated[list[InterventionReason], Field(min_length=7, max_length=11)] = Field(
        default_factory=lambda: list(DEFAULT_INTERVENTIONS)
    )
    deduplicate_by_boundary_event: StrictBool = True
    minimum_reask_interval_seconds: Annotated[NonnegativeInt, Field(le=86400)] = 0
    safety_events_bypass_throttle: StrictBool = True
    silent_when_action_set_unchanged: StrictBool = True

    @model_validator(mode="after")
    def preserve_required_questions(self) -> Self:
        _unique(self.must_ask_on, "must_ask_on")
        if not set(MANDATORY_INTERVENTIONS).issubset(self.must_ask_on):
            raise ValueError(
                "Mandatory authority, loss, risk and preference questions cannot be removed"
            )
        if not (
            self.safety_events_bypass_throttle
            and self.silent_when_action_set_unchanged
            and self.deduplicate_by_boundary_event
        ):
            raise ValueError(
                "Intervention safety bypass, action stability and deduplication are required"
            )
        return self


_MVP_MODELS: dict[TemplateName, type[BaseModel]] = {
    "RecurringObligationPolicy": RecurringObligation,
    "LivingReservePolicy": LivingReserve,
    "EmergencyBufferPolicy": EmergencyBuffer,
    "LongTermGoalPolicy": GoalSaving,
    "AssetAuthorizationPolicy": AssetAuthorization,
}
_FULL_MODELS: dict[TemplateName, type[BaseModel]] = {
    "RecurringObligationPolicy": RecurringObligationPolicy,
    "LivingReservePolicy": LivingReservePolicy,
    "EmergencyBufferPolicy": EmergencyBufferPolicy,
    "DatedExpensePolicy": DatedExpensePolicy,
    "LongTermGoalPolicy": LongTermGoalPolicy,
    "PeriodicTransferPolicy": PeriodicTransferPolicy,
    "AssetAuthorizationPolicy": AssetAuthorizationPolicy,
    "RecoveryPolicy": RecoveryPolicy,
    "GoalAllocationPolicy": GoalAllocationPolicy,
    "CrossGoalReallocationPolicy": CrossGoalReallocationPolicy,
    "SeasonalReservePolicy": SeasonalReservePolicy,
    "InterventionPolicy": InterventionPolicy,
}
# Document the original JSON tags separately from canonical template names.
MVP_TYPE_MAPPING: dict[TemplateName, str] = {
    "RecurringObligationPolicy": "recurring_obligation",
    "LivingReservePolicy": "living_reserve",
    "EmergencyBufferPolicy": "emergency_buffer",
    "LongTermGoalPolicy": "goal_saving",
    "AssetAuthorizationPolicy": "asset_authorization",
}
FULL_TYPE_MAPPING: dict[TemplateName, str] = {
    **MVP_TYPE_MAPPING,
    "DatedExpensePolicy": "dated_expense",
    "LongTermGoalPolicy": "long_term_goal",
    "PeriodicTransferPolicy": "periodic_transfer",
    "RecoveryPolicy": "recovery",
    "GoalAllocationPolicy": "goal_allocation",
    "CrossGoalReallocationPolicy": "cross_goal_reallocation",
    "SeasonalReservePolicy": "seasonal_reserve",
    "InterventionPolicy": "intervention",
}


def template_names() -> tuple[TemplateName, ...]:
    """Return the original twelve-template order without a mutable registry export."""
    return tuple(_FULL_MODELS)


def template_model(template_name: TemplateName, version: DSLVersion) -> type[BaseModel]:
    if version not in ("MVP_V1", "FULL_V1"):
        raise ValueError("Unknown DSL version")
    model = (_MVP_MODELS if version == "MVP_V1" else _FULL_MODELS).get(template_name)
    if model is None:
        raise ValueError("Template is not available in this DSL version")
    return model


def template_schema(template_name: TemplateName, version: DSLVersion) -> dict[str, Any]:
    """Generate the exact model schema; relational constraints are enforced by validation."""
    return template_model(template_name, version).model_json_schema()


def validate_full_configuration(
    template_name: TemplateName,
    configuration: dict[str, Any],
    *,
    version: DSLVersion = "FULL_V1",
) -> dict[str, Any]:
    """Validate JSON candidates only. No state, source, confirmation or SQL is consulted."""
    model = template_model(template_name, version)
    # Reject non-JSON Python values, non-string keys, NaN and infinity before normalization.
    configuration_hash(configuration)
    candidate = model.model_validate(configuration)
    if version == "MVP_V1":
        # Keep the original discriminator validation and normalization path exactly.
        return validate_configuration(configuration)
    return candidate.model_dump(mode="json")
