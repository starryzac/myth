"""Finite calendar discovery from verified originals; suggestions never create obligations."""

from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from fractions import Fraction
from typing import Annotated, Any, Literal
from uuid import UUID

from app.domain.full_policy_configuration import TemplateName, validate_full_configuration
from app.domain.pattern_suggestions import HistoryProof, PeriodicFact, SuggestionModel
from app.domain.policy_configuration import CalendarDate, configuration_hash
from pydantic import Field, StrictInt

PROTOCOL: Literal["full-calendar-periodic-discovery-v2"] = "full-calendar-periodic-discovery-v2"
Cadence = Literal["MONTHLY_DATE", "MONTH_END", "WEEKLY"]
CADENCES: tuple[Cadence, ...] = ("MONTHLY_DATE", "MONTH_END", "WEEKLY")


class CalendarPeriodicParameters(SuggestionModel):
    lookback_days: Annotated[StrictInt, Field(ge=1, le=365)] = 365
    minimum_cycles: Annotated[StrictInt, Field(ge=3, le=12)] = 3
    maximum_day_spread: Annotated[StrictInt, Field(ge=0, le=2)] = 2
    maximum_cv_bps: Annotated[StrictInt, Field(ge=0, le=1000)] = 1000


class CalendarSample(SuggestionModel):
    occurred_on: CalendarDate
    amount_cents: int
    original: PeriodicFact


class CalendarSchedule(SuggestionModel):
    cadence: Cadence
    due_day: int | None
    weekday: int | None
    days_before_month_end: int | None
    day_spread: int
    observed_cycle_keys: list[str]
    next_occurrence: CalendarDate | None
    next_occurrence_is_hypothesis: Literal[True] = True


class CalendarPeriodicPattern(SuggestionModel):
    pattern_id: str
    kind: Literal["FIXED_TRANSFER", "RENT", "CREDIT_CARD_BILL"]
    account_id: UUID
    payee_ref: str
    status: Literal["READY_DISCOVERY", "INSUFFICIENT_HISTORY", "UNSTABLE", "UNKNOWN"]
    reason_codes: list[str]
    schedule: CalendarSchedule
    sample_count: int
    cycle_count: int
    samples: list[CalendarSample]
    amount_min_cents: int
    amount_max_cents: int
    mean_fraction_cents: str
    variance_fraction_cents_squared: str
    cv_squared_fraction: str | None
    template_name: TemplateName | None
    candidate_configuration: dict[str, Any] | None
    candidate_configuration_hash: str | None
    candidate_support: Literal["AVAILABLE_FOR_USER_REVIEW", "NOT_READY", "DSL_UNSUPPORTED"]
    source_scope: Literal["COVERED_TRANSACTIONS", "OBSERVED_BILLS_ONLY"]
    advice_only: Literal[True] = True
    requires_confirmation: Literal[True] = True
    auto_execute: Literal[False] = False
    bank_authority: Literal[False] = False
    future_obligation_guaranteed: Literal[False] = False


def _weekday_span(days: list[int]) -> tuple[int, int]:
    """Shortest circular weekday arc, retaining all samples and deterministic ties."""
    candidates = []
    for anchor in sorted(set(days)):
        shifted = sorted((day - anchor) % 7 for day in days)
        candidates.append((shifted[-1], anchor, (anchor + shifted[(len(days) - 1) // 2]) % 7))
    span, _, weekday = min(candidates)
    return span, weekday


def _schedule(rows: list[PeriodicFact], cadence: Cadence, today: date) -> CalendarSchedule:
    dates = [row.occurred_on for row in rows]
    due_day = None
    weekday = None
    offset = None
    next_day: date | None
    if cadence == "WEEKLY":
        numbers = [(day - timedelta(days=day.weekday())).toordinal() // 7 for day in dates]
        keys = [(day - timedelta(days=day.weekday())).isoformat() for day in dates]
        spread, weekday = _weekday_span([day.weekday() for day in dates])
        next_day = today + timedelta(days=(weekday - today.weekday()) % 7)
    else:
        numbers = [day.year * 12 + day.month for day in dates]
        keys = [day.strftime("%Y-%m") for day in dates]
        values = sorted(
            monthrange(day.year, day.month)[1] - day.day if cadence == "MONTH_END" else day.day
            for day in dates
        )
        spread = values[-1] - values[0]
        typical = values[(len(values) - 1) // 2]
        if cadence == "MONTH_END":
            offset = typical
        else:
            due_day = typical
        year, month = today.year, today.month
        last = monthrange(year, month)[1]
        next_day = (
            None
            if offset is not None and typical >= last
            else date(year, month, last - typical if offset is not None else min(typical, last))
        )
        if next_day is not None and next_day < today:
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            last = monthrange(year, month)[1]
            # The offset of a 31-day month might not exist in a shorter month.
            next_day = (
                None
                if offset is not None and typical >= last
                else date(year, month, last - typical if offset is not None else min(typical, last))
            )
    assert len(numbers) == len(rows)
    return CalendarSchedule(
        cadence=cadence,
        due_day=due_day,
        weekday=weekday,
        days_before_month_end=offset,
        day_spread=spread,
        observed_cycle_keys=keys,
        next_occurrence=next_day,
    )


def _candidate(
    rows: list[PeriodicFact], schedule: CalendarSchedule
) -> tuple[TemplateName | None, dict[str, Any] | None]:
    if schedule.cadence == "WEEKLY" or (
        schedule.cadence == "MONTH_END"
        and (schedule.days_before_month_end != 0 or schedule.day_spread != 0)
    ):
        return None, None
    first = rows[0]
    template: TemplateName = (
        "PeriodicTransferPolicy" if first.kind == "FIXED_TRANSFER" else "RecurringObligationPolicy"
    )
    rule: dict[str, Any] = {
        "kind": "range",
        "min_cents": min(row.amount_cents for row in rows),
        "max_cents": max(row.amount_cents for row in rows),
    }
    if first.kind == "CREDIT_CARD_BILL":
        rule = {"kind": "bill_balance", "account_id": str(first.account_id)}
    configuration: dict[str, Any] = {
        "type": "periodic_transfer" if first.kind == "FIXED_TRANSFER" else "recurring_obligation",
        "name": "银行原历史周期候选（需用户确认）",
        "payee_id": first.payee_ref,
        "due_day": 31 if schedule.cadence == "MONTH_END" else schedule.due_day,
        "amount_rule": rule,
        "auto_execute": False,
    }
    if first.kind == "FIXED_TRANSFER":
        configuration.update(
            source_account_id=str(first.account_id),
            single_action_cap_cents=max(row.amount_cents for row in rows),
        )
    return template, validate_full_configuration(template, configuration)


def calendar_periodic_patterns(
    facts: list[PeriodicFact],
    parameters: CalendarPeriodicParameters,
    proof: HistoryProof,
    today: date,
) -> list[CalendarPeriodicPattern]:
    groups: dict[tuple[str, UUID, str], list[PeriodicFact]] = defaultdict(list)
    for fact in facts:
        groups[(fact.kind, fact.account_id, fact.payee_ref)].append(fact)
    result = []
    for (kind, account, payee), rows in sorted(groups.items()):
        rows.sort(key=lambda row: (row.occurred_on, row.source.fact_id))
        count = len(rows)
        total = sum(row.amount_cents for row in rows)
        variance_numerator = count * sum(row.amount_cents**2 for row in rows) - total**2
        variance = Fraction(variance_numerator, count**2)
        cv = Fraction(variance_numerator, total**2) if total else None
        for cadence in CADENCES:
            schedule = _schedule(rows, cadence, today)
            keys = schedule.observed_cycle_keys
            numbers = (
                [date.fromisoformat(key).toordinal() // 7 for key in keys]
                if cadence == "WEEKLY"
                else [int(key[:4]) * 12 + int(key[5:]) for key in keys]
            )
            reasons = []
            if len(set(keys)) < parameters.minimum_cycles:
                reasons.append("INSUFFICIENT_CYCLES")
            if len(set(keys)) != count:
                reasons.append("MULTIPLE_OCCURRENCES_IN_CYCLE")
            if any(b - a != 1 for a, b in zip(numbers, numbers[1:], strict=False)):
                reasons.append("NONCONSECUTIVE_CYCLES")
            if schedule.day_spread > parameters.maximum_day_spread:
                reasons.append("DATE_SPREAD_EXCEEDED")
            if (
                not total
                or variance_numerator * 100_000_000 > parameters.maximum_cv_bps**2 * total**2
            ):
                reasons.append("AMOUNT_VARIANCE_EXCEEDED")
            if len({row.source.fact_id for row in rows}) != count:
                reasons.append("DUPLICATE_ORIGINAL_FACT")
            if any(row.occurred_on >= today for row in rows):
                reasons.append("NONCLOSED_OR_FUTURE_SAMPLE")
            if not proof.verified:
                reasons.extend(proof.reason_codes or ["MISSING_HISTORY_COVERAGE"])
            status: Literal["READY_DISCOVERY", "INSUFFICIENT_HISTORY", "UNSTABLE", "UNKNOWN"] = (
                "UNKNOWN"
                if not proof.verified or "NONCLOSED_OR_FUTURE_SAMPLE" in reasons
                else "INSUFFICIENT_HISTORY"
                if "INSUFFICIENT_CYCLES" in reasons
                else "UNSTABLE"
                if reasons
                else "READY_DISCOVERY"
            )
            template = None
            candidate = None
            support: Literal["AVAILABLE_FOR_USER_REVIEW", "NOT_READY", "DSL_UNSUPPORTED"] = (
                "NOT_READY"
            )
            if status == "READY_DISCOVERY":
                template, candidate = _candidate(rows, schedule)
                support = "AVAILABLE_FOR_USER_REVIEW" if candidate else "DSL_UNSUPPORTED"
                if candidate is None:
                    reasons.append("CURRENT_POLICY_DSL_CANNOT_EXPRESS_THIS_SCHEDULE")
            else:
                schedule = schedule.model_copy(update={"next_occurrence": None})
            result.append(
                CalendarPeriodicPattern(
                    pattern_id=configuration_hash(
                        {
                            "protocol": PROTOCOL,
                            "kind": kind,
                            "account_id": str(account),
                            "payee": payee,
                            "cadence": cadence,
                        }
                    ),
                    kind=rows[0].kind,
                    account_id=account,
                    payee_ref=payee,
                    status=status,
                    reason_codes=sorted(set(reasons)),
                    schedule=schedule,
                    sample_count=count,
                    cycle_count=len(set(keys)),
                    samples=[
                        CalendarSample(
                            occurred_on=row.occurred_on, amount_cents=row.amount_cents, original=row
                        )
                        for row in rows
                    ],
                    amount_min_cents=min(row.amount_cents for row in rows),
                    amount_max_cents=max(row.amount_cents for row in rows),
                    mean_fraction_cents=str(Fraction(total, count)),
                    variance_fraction_cents_squared=str(variance),
                    cv_squared_fraction=str(cv) if cv is not None else None,
                    template_name=template,
                    candidate_configuration=candidate,
                    candidate_configuration_hash=configuration_hash(candidate)
                    if candidate
                    else None,
                    candidate_support=support,
                    source_scope="OBSERVED_BILLS_ONLY"
                    if kind == "CREDIT_CARD_BILL"
                    else "COVERED_TRANSACTIONS",
                )
            )
    return result
