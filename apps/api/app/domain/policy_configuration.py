"""Strict, deterministic MVP policy configurations; validation grants no authority."""

import hashlib
import json
from datetime import date
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

MoneyCents = Annotated[StrictInt, Field(ge=0, le=9_223_372_036_854_775_807)]
NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
Category = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=48)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


def _calendar_date(value: Any) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("A YYYY-MM-DD calendar date is required") from error
        if parsed.isoformat() == value:
            return parsed
    raise ValueError("A YYYY-MM-DD calendar date is required")


CalendarDate = Annotated[date, BeforeValidator(_calendar_date)]


def _uuid_reference(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise ValueError("A UUID reference is required")


UUIDReference = Annotated[UUID, BeforeValidator(_uuid_reference)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CommonConfiguration(StrictModel):
    name: Name | None = None
    valid_from: CalendarDate | None = None
    valid_until: CalendarDate | None = None

    @model_validator(mode="after")
    def valid_window(self) -> Self:
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError("valid_until must not precede valid_from")
        return self


class Priority(StrictModel):
    importance: Annotated[StrictInt, Field(ge=0, le=100)] = 50
    minimum_cents: MoneyCents = 0
    reducible: StrictBool = False
    deferrable: StrictBool = False


class RangeAmount(StrictModel):
    kind: Literal["range"]
    min_cents: MoneyCents
    max_cents: MoneyCents

    @model_validator(mode="after")
    def ordered_range(self) -> Self:
        if self.min_cents > self.max_cents:
            raise ValueError("min_cents must not exceed max_cents")
        return self


class ExactAmount(StrictModel):
    kind: Literal["exact"]
    amount_cents: MoneyCents


class BillBalanceAmount(StrictModel):
    kind: Literal["bill_balance"]
    account_id: UUIDReference


class RecurringObligation(CommonConfiguration):
    type: Literal["recurring_obligation"]
    payee_id: Reference
    amount_rule: Annotated[
        RangeAmount | ExactAmount | BillBalanceAmount, Field(discriminator="kind")
    ]
    due_day: Annotated[StrictInt, Field(ge=1, le=31)]
    prepare_days_before: NonnegativeInt = 0
    auto_execute: StrictBool = False
    priority: Priority = Field(default_factory=Priority)


class ReserveMethod(StrictModel):
    name: Literal["rolling_window_quantile"]
    lookback_days: PositiveInt
    quantile: Annotated[float, Field(strict=True, gt=0, le=1, allow_inf_nan=False)]
    essential_categories: Annotated[list[Category], Field(min_length=1)]
    exclude_one_off: StrictBool = True


class LivingReserve(CommonConfiguration):
    type: Literal["living_reserve"]
    horizon_days: PositiveInt
    method: ReserveMethod
    extra_buffer_cents: MoneyCents = 0
    reconfirm_on_boundary_crossing: StrictBool = True

    @model_validator(mode="after")
    def enough_history_window(self) -> Self:
        if self.horizon_days > self.method.lookback_days:
            raise ValueError("horizon_days must not exceed lookback_days")
        return self


class MonthlyContribution(StrictModel):
    min_cents: MoneyCents
    target_cents: MoneyCents
    max_cents: MoneyCents

    @model_validator(mode="after")
    def ordered_contribution(self) -> Self:
        if not self.min_cents <= self.target_cents <= self.max_cents:
            raise ValueError("Monthly contribution must satisfy min <= target <= max")
        return self


class GoalSaving(CommonConfiguration):
    type: Literal["goal_saving"]
    target_cents: Annotated[MoneyCents, Field(gt=0)]
    deadline: CalendarDate
    monthly_contribution: MonthlyContribution
    priority: Priority = Field(default_factory=Priority)
    cross_goal_reallocation_allowed: StrictBool = False
    asset_policy_id: UUIDReference | None = None

    @model_validator(mode="after")
    def reachable_date_window(self) -> Self:
        if self.valid_from is not None and self.deadline < self.valid_from:
            raise ValueError("Goal deadline must not precede valid_from")
        return self


class AssetAuthorization(CommonConfiguration):
    type: Literal["asset_authorization"]
    scope: Literal["general_idle_funds", "goal"]
    goal_id: UUIDReference | None = None
    allowed_asset_classes: Annotated[
        list[Literal["CASH", "CASH_MGMT_T0", "CASH_MGMT_T1", "FIXED_DEPOSIT"]], Field(min_length=1)
    ]
    max_auto_managed_cents: MoneyCents
    single_action_cap_cents: MoneyCents
    max_redemption_delay_days: NonnegativeInt
    max_lock_days: NonnegativeInt
    max_principal_risk_level: Annotated[StrictInt, Field(ge=0, le=5)] = 0
    allow_auto_recovery_without_penalty: StrictBool = False
    allow_early_withdrawal_with_penalty: StrictBool = False

    @model_validator(mode="after")
    def scoped_limits(self) -> Self:
        if (self.scope == "goal") != (self.goal_id is not None):
            raise ValueError(
                "goal scope requires goal_id; general_idle_funds must not have goal_id"
            )
        if self.single_action_cap_cents > self.max_auto_managed_cents:
            raise ValueError("single_action_cap_cents must not exceed max_auto_managed_cents")
        return self


class EmergencyBuffer(CommonConfiguration):
    type: Literal["emergency_buffer"]
    amount_cents: MoneyCents


_configuration_adapter: TypeAdapter[
    RecurringObligation | LivingReserve | GoalSaving | AssetAuthorization | EmergencyBuffer
] = TypeAdapter(
    Annotated[
        RecurringObligation | LivingReserve | GoalSaving | AssetAuthorization | EmergencyBuffer,
        Field(discriminator="type"),
    ]
)


def validate_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    """Return JSON-compatible explicit defaults without consulting time or a database."""
    if not isinstance(configuration, dict):
        raise ValueError("Configuration must be an object")
    return _configuration_adapter.validate_python(configuration).model_dump(mode="json")


def _require_json_value(value: Any) -> None:
    if value is None or type(value) in (str, int, float, bool):
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _require_json_value(child)
        return
    if isinstance(value, list):
        for child in value:
            _require_json_value(child)
        return
    raise ValueError("Only JSON values are permitted")


def configuration_hash(configuration: dict[str, Any]) -> str:
    """Hash the provided JSON document; callers validate before confirming authority."""
    if not isinstance(configuration, dict):
        raise ValueError("Configuration must be an object")
    try:
        _require_json_value(configuration)
        canonical = json.dumps(
            configuration,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("Configuration must be a JSON-compatible object") from error
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
