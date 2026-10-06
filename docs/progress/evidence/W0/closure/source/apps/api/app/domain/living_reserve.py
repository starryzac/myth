"""Deterministic reserve estimates from explicitly covered local calendar days."""

from datetime import date, timedelta
from fractions import Fraction
from typing import Annotated, Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.policy_configuration import MoneyCents, validate_configuration
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

ALGORITHM_VERSION = "living-reserve-nearest-rank-v1"
MAX_LOOKBACK_DAYS = 366
MAX_ACCOUNTS = 100
MAX_TRANSACTIONS = 100_000


class ReserveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ReserveTransaction(ReserveModel):
    transaction_id: UUID
    account_id: UUID
    occurred_on: date
    direction: Literal["CREDIT", "DEBIT"]
    amount_cents: Annotated[MoneyCents, Field(gt=0)]
    category: Annotated[str, Field(min_length=1, max_length=48)]
    category_confirmed: StrictBool
    is_one_off: StrictBool
    economic_role: Literal["CONSUMPTION", "NON_CONSUMPTION", "UNKNOWN"]


class HistoryCoverage(ReserveModel):
    account_id: UUID
    covered_dates: Annotated[list[date], Field(max_length=MAX_LOOKBACK_DAYS)]

    @field_validator("covered_dates")
    @classmethod
    def unique_dates(cls, values: list[date]) -> list[date]:
        if len(values) != len(set(values)):
            raise ValueError("Coverage dates must be unique per account")
        return values


class ReserveEstimateInput(ReserveModel):
    reference_date: date
    timezone: str
    account_ids: Annotated[list[UUID], Field(max_length=MAX_ACCOUNTS)]
    transactions: Annotated[list[ReserveTransaction], Field(max_length=MAX_TRANSACTIONS)]
    coverage: Annotated[list[HistoryCoverage], Field(max_length=MAX_ACCOUNTS)]

    @model_validator(mode="after")
    def unique_identifiers(self) -> Self:
        transaction_ids = [item.transaction_id for item in self.transactions]
        coverage_accounts = [item.account_id for item in self.coverage]
        for name, identifiers in (
            ("transaction", transaction_ids),
            ("account", self.account_ids),
            ("coverage account", coverage_accounts),
        ):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"Duplicate {name} identifiers are not permitted")
        return self

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("A valid IANA timezone is required") from error
        return value


class DailyReserveAmount(ReserveModel):
    day: date
    covered: bool
    amount_cents: MoneyCents | None


class ReserveWindow(ReserveModel):
    start_date: date
    end_date: date
    amount_cents: MoneyCents


class ExcludedReserveTransaction(ReserveModel):
    transaction_id: UUID
    account_id: UUID
    occurred_on: date
    amount_cents: MoneyCents
    reasons: list[str]


class CoverageGap(ReserveModel):
    account_id: UUID
    missing_dates: list[date]


class LivingReserveEstimate(ReserveModel):
    algorithm_version: str
    status: Literal["READY", "INSUFFICIENT_HISTORY"]
    reference_date: date
    timezone: str
    history_start: date
    history_end: date
    lookback_days: int
    horizon_days: int
    quantile: float
    quantile_fraction: str
    rank: int | None
    window_count: int
    base_reserve_cents: MoneyCents | None
    extra_buffer_cents: MoneyCents
    recommended_reserve_cents: MoneyCents | None
    selected_categories: list[str]
    account_ids: list[UUID]
    normalized_configuration: dict[str, Any]
    daily_amounts: list[DailyReserveAmount]
    windows: list[ReserveWindow]
    included_transaction_ids: list[UUID]
    excluded_transactions: list[ExcludedReserveTransaction]
    coverage_gaps: list[CoverageGap]
    issues: list[str]


def estimate_living_reserve(
    configuration: dict[str, Any], inputs: ReserveEstimateInput
) -> LivingReserveEstimate:
    """Estimate a reviewable reserve, without reading a clock or creating authority."""
    inputs = ReserveEstimateInput.model_validate(inputs.model_dump())
    normalized = validate_configuration(configuration)
    if normalized["type"] != "living_reserve":
        raise ValueError("A living_reserve configuration is required")
    lookback = normalized["method"]["lookback_days"]
    if lookback > MAX_LOOKBACK_DAYS:
        raise ValueError(f"lookback_days must not exceed {MAX_LOOKBACK_DAYS}")
    if inputs.reference_date.toordinal() <= lookback:
        raise ValueError("reference_date must permit the entire lookback interval")
    horizon = normalized["horizon_days"]
    quantile = normalized["method"]["quantile"]
    fraction = Fraction(str(quantile))
    days = [inputs.reference_date - timedelta(days=offset) for offset in range(lookback, 0, -1)]
    accounts = sorted(inputs.account_ids)
    coverage = {item.account_id: set(item.covered_dates) for item in inputs.coverage}
    gaps = [
        CoverageGap(
            account_id=account,
            missing_dates=[day for day in days if day not in coverage.get(account, set())],
        )
        for account in accounts
        if not set(days).issubset(coverage.get(account, set()))
    ]
    covered_days = {
        day
        for day in days
        if accounts and all(day in coverage.get(account, set()) for account in accounts)
    }
    issues = (
        []
        if accounts and not gaps
        else ["NO_ACCOUNT_SCOPE" if not accounts else "INCOMPLETE_HISTORY_COVERAGE"]
    )
    daily_totals = {day: 0 for day in days}
    included: list[UUID] = []
    excluded: list[ExcludedReserveTransaction] = []
    unknown_days: set[date] = set()
    categories = sorted(set(normalized["method"]["essential_categories"]))
    for transaction in sorted(inputs.transactions, key=lambda item: item.transaction_id):
        reasons: list[str] = []
        if transaction.account_id not in accounts:
            reasons.append("ACCOUNT_OUT_OF_SCOPE")
        if transaction.occurred_on not in daily_totals:
            reasons.append("OUTSIDE_HISTORY")
        if transaction.direction != "DEBIT":
            reasons.append("NOT_DEBIT")
        if transaction.economic_role == "NON_CONSUMPTION":
            reasons.append("NON_CONSUMPTION")
        elif transaction.economic_role == "UNKNOWN":
            reasons.append("UNKNOWN_ECONOMIC_ROLE")
            if transaction.account_id in accounts and transaction.occurred_on in daily_totals:
                unknown_days.add(transaction.occurred_on)
        if not transaction.category_confirmed:
            reasons.append("CATEGORY_UNCONFIRMED")
        if transaction.category not in categories:
            reasons.append("CATEGORY_NOT_SELECTED")
        if normalized["method"]["exclude_one_off"] and transaction.is_one_off:
            reasons.append("ONE_OFF")
        if reasons:
            excluded.append(
                ExcludedReserveTransaction(
                    transaction_id=transaction.transaction_id,
                    account_id=transaction.account_id,
                    occurred_on=transaction.occurred_on,
                    amount_cents=transaction.amount_cents,
                    reasons=reasons,
                )
            )
        else:
            included.append(transaction.transaction_id)
            daily_totals[transaction.occurred_on] += transaction.amount_cents
    if unknown_days:
        issues.append("UNKNOWN_ECONOMIC_ROLE")
    ready = not issues
    daily = [
        DailyReserveAmount(
            day=day,
            covered=day in covered_days,
            amount_cents=daily_totals[day]
            if day in covered_days and day not in unknown_days
            else None,
        )
        for day in days
    ]
    windows = [
        ReserveWindow(
            start_date=days[index],
            end_date=days[index + horizon - 1],
            amount_cents=sum(daily_totals[day] for day in days[index : index + horizon]),
        )
        for index in range(lookback - horizon + 1)
        if ready
    ]
    count = len(windows)
    rank = (
        (count * fraction.numerator + fraction.denominator - 1) // fraction.denominator
        if ready
        else None
    )
    base = sorted(window.amount_cents for window in windows)[rank - 1] if rank is not None else None
    return LivingReserveEstimate(
        algorithm_version=ALGORITHM_VERSION,
        status="READY" if ready else "INSUFFICIENT_HISTORY",
        reference_date=inputs.reference_date,
        timezone=inputs.timezone,
        history_start=days[0],
        history_end=days[-1],
        lookback_days=lookback,
        horizon_days=horizon,
        quantile=quantile,
        quantile_fraction=str(fraction),
        rank=rank,
        window_count=count,
        base_reserve_cents=base,
        extra_buffer_cents=normalized["extra_buffer_cents"],
        recommended_reserve_cents=base + normalized["extra_buffer_cents"]
        if base is not None
        else None,
        selected_categories=categories,
        account_ids=accounts,
        normalized_configuration=normalized,
        daily_amounts=daily,
        windows=windows,
        included_transaction_ids=included,
        excluded_transactions=excluded,
        coverage_gaps=gaps,
        issues=issues,
    )
