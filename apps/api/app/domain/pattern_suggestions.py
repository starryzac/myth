"""Finite historical suggestions in integer cents. No financial authority is created."""

from collections import defaultdict
from datetime import date
from fractions import Fraction
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.domain.full_policy_configuration import TemplateName, validate_full_configuration
from app.domain.policy_configuration import (
    CalendarDate,
    Category,
    MoneyCents,
    Reference,
    StrictModel,
    UUIDReference,
    configuration_hash,
)
from pydantic import ConfigDict, Field, StrictInt, model_validator

PERIODIC_RULE = "full-monthly-pattern-variance-v1"
SEASONAL_RULE = "full-same-festival-excess-nearest-rank-v1"
ESSENTIAL_CATEGORIES = ("food", "transport", "daily_necessities")
MAX_MONEY = 9_223_372_036_854_775_807


class SuggestionModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PeriodicParameters(SuggestionModel):
    lookback_days: Annotated[StrictInt, Field(ge=1, le=56)] = 56
    minimum_cycles: Annotated[StrictInt, Field(ge=2, le=12)] = 2
    maximum_day_spread: Annotated[StrictInt, Field(ge=0, le=2)] = 2
    maximum_cv_bps: Annotated[StrictInt, Field(ge=0, le=1000)] = 1000


class SeasonalParameters(SuggestionModel):
    window_id: Reference
    lookback_days: Annotated[StrictInt, Field(ge=1, le=1096)] = 1096
    minimum_historical_windows: Annotated[StrictInt, Field(ge=2, le=10)] = 2
    quantile_bps: Annotated[StrictInt, Field(ge=8000, le=10000)] = 8000
    essential_categories: Annotated[list[Category], Field(min_length=1, max_length=3)] = Field(
        default_factory=lambda: list(ESSENTIAL_CATEGORIES)
    )
    adjustment_cap_cents: Annotated[MoneyCents, Field(le=500_000)] = 500_000

    @model_validator(mode="after")
    def tighten_categories(self) -> Self:
        if len(set(self.essential_categories)) != len(self.essential_categories) or not set(
            self.essential_categories
        ) <= set(ESSENTIAL_CATEGORIES):
            raise ValueError(
                "Only a unique subset of the registered essential categories is allowed"
            )
        return self


class SuggestionSource(SuggestionModel):
    fact_type: str
    fact_id: UUIDReference
    account_id: UUIDReference | None = None
    source_ref: str
    evidence_id: UUIDReference
    evidence_hash: str
    evidence_source_type: str
    evidence_source_ref: str
    evidence_observed_at: str
    fact: dict[str, Any]


class HistoryProof(SuggestionModel):
    verified: bool
    period_start: CalendarDate | None = None
    period_end: CalendarDate | None = None
    account_ids: list[UUID]
    evidence_ids: list[UUID]
    reason_codes: list[str]


class PeriodicFact(SuggestionModel):
    kind: Literal["FIXED_TRANSFER", "RENT", "CREDIT_CARD_BILL"]
    account_id: UUID
    payee_ref: Reference
    occurred_on: CalendarDate
    amount_cents: MoneyCents
    source: SuggestionSource
    supporting_sources: list[SuggestionSource] = Field(default_factory=list)


class PeriodicPattern(SuggestionModel):
    pattern_id: str
    kind: Literal["FIXED_TRANSFER", "RENT", "CREDIT_CARD_BILL"]
    account_id: UUID
    payee_ref: str
    status: Literal["READY", "INSUFFICIENT_HISTORY", "UNSTABLE", "UNKNOWN"]
    reason_codes: list[str]
    cycle_count: int
    sample_count: int
    months: list[str]
    day_spread: int
    suggested_due_day: int | None
    amount_min_cents: int
    amount_max_cents: int
    mean_fraction_cents: str
    variance_fraction_cents_squared: str
    cv_squared_fraction: str | None
    template_name: TemplateName | None
    candidate_configuration: dict[str, Any] | None
    candidate_configuration_hash: str | None
    sources: list[SuggestionSource]
    advice_only: Literal[True] = True
    requires_confirmation: Literal[True] = True
    bank_authority: Literal[False] = False
    future_obligation_guaranteed: Literal[False] = False


def periodic_patterns(
    facts: list[PeriodicFact], parameters: PeriodicParameters, proof: HistoryProof
) -> list[PeriodicPattern]:
    """One occurrence per consecutive natural month; no trimming or guessed missing month."""
    groups: dict[tuple[str, UUID, str], list[PeriodicFact]] = defaultdict(list)
    for fact in facts:
        groups[(fact.kind, fact.account_id, fact.payee_ref)].append(fact)
    result = []
    for (_, account_id, payee), rows in sorted(groups.items()):
        rows.sort(key=lambda row: (row.occurred_on, row.source.fact_id))
        kind = rows[0].kind
        months = [row.occurred_on.year * 12 + row.occurred_on.month for row in rows]
        days = sorted(row.occurred_on.day for row in rows)
        count = len(rows)
        total = sum(row.amount_cents for row in rows)
        variance_numerator = count * sum(row.amount_cents**2 for row in rows) - total**2
        variance = Fraction(variance_numerator, count**2)
        cv_squared = Fraction(variance_numerator, total**2) if total else None
        reasons = []
        if len(set(months)) < parameters.minimum_cycles:
            reasons.append("INSUFFICIENT_CYCLES")
        if len(set(months)) != count:
            reasons.append("MULTIPLE_OCCURRENCES_IN_MONTH")
        if any(right - left != 1 for left, right in zip(months, months[1:], strict=False)):
            reasons.append("NONCONSECUTIVE_MONTHS")
        if days[-1] - days[0] > parameters.maximum_day_spread:
            reasons.append("DATE_SPREAD_EXCEEDED")
        if not total or variance_numerator * 100_000_000 > parameters.maximum_cv_bps**2 * total**2:
            reasons.append("AMOUNT_VARIANCE_EXCEEDED")
        if not proof.verified:
            reasons.extend(proof.reason_codes or ["MISSING_HISTORY_COVERAGE"])
        status: Literal["READY", "INSUFFICIENT_HISTORY", "UNSTABLE", "UNKNOWN"] = (
            "UNKNOWN"
            if not proof.verified
            else "INSUFFICIENT_HISTORY"
            if "INSUFFICIENT_CYCLES" in reasons
            else "UNSTABLE"
            if reasons
            else "READY"
        )
        candidate = None
        template: TemplateName | None = None
        due_day = days[(count - 1) // 2] if status == "READY" else None
        if status == "READY":
            amount_rule: dict[str, Any] = {
                "kind": "range",
                "min_cents": min(row.amount_cents for row in rows),
                "max_cents": max(row.amount_cents for row in rows),
            }
            template = (
                "PeriodicTransferPolicy"
                if kind == "FIXED_TRANSFER"
                else ("RecurringObligationPolicy")
            )
            if kind == "CREDIT_CARD_BILL":
                amount_rule = {"kind": "bill_balance", "account_id": str(account_id)}
            config: dict[str, Any] = {
                "type": "periodic_transfer" if kind == "FIXED_TRANSFER" else "recurring_obligation",
                "name": "历史周期规律候选",
                "payee_id": payee,
                "due_day": due_day,
                "amount_rule": amount_rule,
                "auto_execute": False,
            }
            if kind == "FIXED_TRANSFER":
                config.update(
                    source_account_id=str(account_id),
                    single_action_cap_cents=max(row.amount_cents for row in rows),
                )
            candidate = validate_full_configuration(template, config)
        result.append(
            PeriodicPattern(
                pattern_id=configuration_hash(
                    {
                        "rule": PERIODIC_RULE,
                        "kind": kind,
                        "account": str(account_id),
                        "payee": payee,
                    }
                ),
                kind=kind,
                account_id=account_id,
                payee_ref=payee,
                status=status,
                reason_codes=reasons,
                cycle_count=len(set(months)),
                sample_count=count,
                months=[f"{row.occurred_on:%Y-%m}" for row in rows],
                day_spread=days[-1] - days[0],
                suggested_due_day=due_day,
                amount_min_cents=min(row.amount_cents for row in rows),
                amount_max_cents=max(row.amount_cents for row in rows),
                mean_fraction_cents=str(Fraction(total, count)),
                variance_fraction_cents_squared=str(variance),
                cv_squared_fraction=str(cv_squared) if cv_squared is not None else None,
                template_name=template,
                candidate_configuration=candidate,
                candidate_configuration_hash=configuration_hash(candidate) if candidate else None,
                sources=[
                    source for row in rows for source in [row.source, *row.supporting_sources]
                ],
            )
        )
    return result


class PublicWindow(SuggestionModel):
    window_id: str
    year: int
    holiday_code: str
    start: CalendarDate
    end: CalendarDate
    notice_reference: str
    notice_date: CalendarDate
    source_url: str
    source_verification: Literal["OFFICIAL_NOTICE_MANUAL_EXTRACTION"] = (
        "OFFICIAL_NOTICE_MANUAL_EXTRACTION"
    )


# Exact notified dates only. Unspecified adjoining weekends are not inferred. Combined
# holidays remain combined and are never split into two artificial comparison samples.
_NOTICES = (
    (
        2024,
        "国办发明电〔2023〕7号",
        "2023-10-25",
        "https://www.beijing.gov.cn/fuwu/bmfw/sy/jrts/202310/t20231025_3286455.html",
        (
            ("NEW_YEAR", "01-01", "01-01"),
            ("SPRING_FESTIVAL", "02-10", "02-17"),
            ("QINGMING", "04-04", "04-06"),
            ("LABOUR_DAY", "05-01", "05-05"),
            ("DRAGON_BOAT", "06-10", "06-10"),
            ("MID_AUTUMN", "09-15", "09-17"),
            ("NATIONAL_DAY", "10-01", "10-07"),
        ),
    ),
    (
        2025,
        "国办发明电〔2024〕12号",
        "2024-11-12",
        "https://www.gov.cn/gongbao/2024/issue_11726/material/gwygb202433.pdf",
        (
            ("NEW_YEAR", "01-01", "01-01"),
            ("SPRING_FESTIVAL", "01-28", "02-04"),
            ("QINGMING", "04-04", "04-06"),
            ("LABOUR_DAY", "05-01", "05-05"),
            ("DRAGON_BOAT", "05-31", "06-02"),
            ("NATIONAL_DAY_MID_AUTUMN", "10-01", "10-08"),
        ),
    ),
    (
        2026,
        "国办发明电〔2025〕7号",
        "2025-11-04",
        "https://www.beijing.gov.cn/zhengce/zhengcefagui/202511/t20251104_4258873.html",
        (
            ("NEW_YEAR", "01-01", "01-03"),
            ("SPRING_FESTIVAL", "02-15", "02-23"),
            ("QINGMING", "04-04", "04-06"),
            ("LABOUR_DAY", "05-01", "05-05"),
            ("DRAGON_BOAT", "06-19", "06-21"),
            ("MID_AUTUMN", "09-25", "09-27"),
            ("NATIONAL_DAY", "10-01", "10-07"),
        ),
    ),
)


def public_calendar(reference_date: date) -> list[PublicWindow]:
    """A finite server-owned calendar; future notices cannot be known by an earlier clock."""
    return [
        PublicWindow(
            window_id=f"CN-{year}-{code}",
            year=year,
            holiday_code=code,
            start=date.fromisoformat(f"{year}-{start}"),
            end=date.fromisoformat(f"{year}-{end}"),
            notice_reference=reference,
            notice_date=date.fromisoformat(notice_date),
            source_url=url,
        )
        for year, reference, notice_date, url, windows in _NOTICES
        if date.fromisoformat(notice_date) <= reference_date
        for code, start, end in windows
    ]


class SeasonalSpend(SuggestionModel):
    transaction_id: UUID
    occurred_on: CalendarDate
    amount_cents: MoneyCents
    category: Category
    sources: list[SuggestionSource]


class SeasonalComparison(SuggestionModel):
    window: PublicWindow
    baseline_start: CalendarDate
    baseline_end: CalendarDate
    status: Literal["VERIFIED", "MISSING"]
    reason_codes: list[str]
    holiday_total_cents: int | None
    baseline_total_cents: int | None
    positive_excess_fraction_cents_per_day: str | None
    scaled_excess_cents: int | None
    transaction_ids: list[UUID]


class SeasonalSuggestion(SuggestionModel):
    status: Literal["READY", "UNKNOWN", "INSUFFICIENT_HISTORY"]
    reason_codes: list[str]
    target: PublicWindow | None
    effective_window_start: CalendarDate | None
    effective_window_end: CalendarDate | None
    window_count: int
    required_window_count: int
    comparisons: list[SeasonalComparison]
    quantile_fraction: str
    rank: int | None
    required_adjustment_cents: int | None
    proposed_adjustment_cents: int | None
    cap_limited: bool | None
    candidate_configuration: dict[str, Any] | None
    candidate_configuration_hash: str | None
    advice_only: Literal[True] = True
    requires_confirmation: Literal[True] = True
    bank_authority: Literal[False] = False
    hard_protection_changed: Literal[False] = False


def seasonal_suggestion(
    parameters: SeasonalParameters,
    reference_date: date,
    proof: HistoryProof,
    spends: list[SeasonalSpend],
    invalid_days: dict[date, list[str]],
) -> SeasonalSuggestion:
    from datetime import timedelta

    windows = public_calendar(reference_date)
    target = next((window for window in windows if window.window_id == parameters.window_id), None)
    reasons = []
    if target is None:
        reasons.append("UNSUPPORTED_OR_NOT_YET_PUBLISHED_WINDOW")
    elif target.end < reference_date:
        reasons.append("TARGET_WINDOW_ENDED")
    if not proof.verified:
        reasons.extend(proof.reason_codes or ["MISSING_HISTORY_COVERAGE"])
    effective_start = max(reference_date, target.start) if target else None
    target_days = (target.end - effective_start).days + 1 if target and effective_start else 0
    comparisons: list[SeasonalComparison] = []
    if target and target_days > 0:
        earliest = reference_date - timedelta(days=parameters.lookback_days)
        for window in windows:
            if window.holiday_code != target.holiday_code or window.end >= reference_date:
                continue
            length = (window.end - window.start).days + 1
            baseline_start = window.start - timedelta(days=length)
            baseline_end = window.start - timedelta(days=1)
            missing = []
            if baseline_start < earliest:
                missing.append("WINDOW_OUTSIDE_LOOKBACK")
            if (
                not proof.verified
                or proof.period_start is None
                or proof.period_end is None
                or proof.period_start > baseline_start
                or proof.period_end < window.end
            ):
                missing.append("WINDOW_NOT_COMPLETELY_COVERED")
            if any(
                other.window_id != window.window_id
                and other.start <= baseline_end
                and other.end >= baseline_start
                for other in windows
            ):
                missing.append("BASELINE_OVERLAPS_PUBLIC_HOLIDAY")
            for day, codes in invalid_days.items():
                if baseline_start <= day <= window.end:
                    missing.extend(codes)
            selected = [
                spend
                for spend in spends
                if baseline_start <= spend.occurred_on <= window.end
                and spend.category in parameters.essential_categories
            ]
            holiday = sum(
                spend.amount_cents for spend in selected if spend.occurred_on >= window.start
            )
            baseline = sum(
                spend.amount_cents for spend in selected if spend.occurred_on < window.start
            )
            excess = Fraction(max(0, holiday - baseline), length)
            scaled = (excess.numerator * target_days + excess.denominator - 1) // excess.denominator
            if holiday > MAX_MONEY or baseline > MAX_MONEY or scaled > MAX_MONEY:
                missing.append("MONEY_CAPACITY_EXCEEDED")
            comparisons.append(
                SeasonalComparison(
                    window=window,
                    baseline_start=baseline_start,
                    baseline_end=baseline_end,
                    status="MISSING" if missing else "VERIFIED",
                    reason_codes=sorted(set(missing)),
                    holiday_total_cents=None if missing else holiday,
                    baseline_total_cents=None if missing else baseline,
                    positive_excess_fraction_cents_per_day=None if missing else str(excess),
                    scaled_excess_cents=None if missing else scaled,
                    transaction_ids=[spend.transaction_id for spend in selected],
                )
            )
    values = sorted(
        comparison.scaled_excess_cents
        for comparison in comparisons
        if comparison.status == "VERIFIED" and comparison.scaled_excess_cents is not None
    )
    if not comparisons:
        reasons.append("NO_COMPARABLE_PUBLIC_WINDOWS")
    if len(values) < parameters.minimum_historical_windows:
        reasons.append("INSUFFICIENT_HISTORICAL_WINDOWS")
    rank = None
    required = None
    proposed = None
    candidate = None
    if not reasons:
        rank = (parameters.quantile_bps * len(values) + 9999) // 10000
        required = values[rank - 1]
        proposed = min(required, parameters.adjustment_cap_cents)
        assert target is not None and effective_start is not None
        candidate = validate_full_configuration(
            "SeasonalReservePolicy",
            {
                "type": "seasonal_reserve",
                "name": "同节日完整历史窗口临时准备金候选",
                "holiday_code": target.holiday_code,
                "window": {"start": effective_start.isoformat(), "end": target.end.isoformat()},
                "lookback_days": parameters.lookback_days,
                "minimum_historical_windows": parameters.minimum_historical_windows,
                "quantile": parameters.quantile_bps / 10000,
                "essential_categories": parameters.essential_categories,
                "adjustment_cap_cents": proposed,
                "requires_confirmation": True,
                "advice_only": True,
            },
        )
    return SeasonalSuggestion(
        status="READY"
        if candidate
        else "UNKNOWN"
        if any(
            reason
            in {
                "UNSUPPORTED_OR_NOT_YET_PUBLISHED_WINDOW",
                "TARGET_WINDOW_ENDED",
                "CALENDAR_TIMEZONE_UNSUPPORTED",
            }
            for reason in reasons
        )
        else "INSUFFICIENT_HISTORY",
        reason_codes=sorted(set(reasons)),
        target=target,
        effective_window_start=effective_start,
        effective_window_end=target.end if target else None,
        window_count=len(values),
        required_window_count=parameters.minimum_historical_windows,
        comparisons=comparisons,
        quantile_fraction=str(Fraction(parameters.quantile_bps, 10000)),
        rank=rank,
        required_adjustment_cents=required,
        proposed_adjustment_cents=proposed,
        cap_limited=required > proposed if required is not None and proposed is not None else None,
        candidate_configuration=candidate,
        candidate_configuration_hash=configuration_hash(candidate) if candidate else None,
    )
