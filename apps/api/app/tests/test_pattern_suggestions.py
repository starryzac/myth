"""Hand-calculated pure risk examples; these are not actual bank performance evidence."""

from datetime import date
from typing import Any
from uuid import UUID

import pytest
from app.domain.pattern_suggestions import (
    HistoryProof,
    PeriodicFact,
    PeriodicParameters,
    SeasonalParameters,
    SeasonalSpend,
    SuggestionSource,
    periodic_patterns,
    public_calendar,
    seasonal_suggestion,
)
from app.domain.policy_configuration import configuration_hash
from pydantic import ValidationError


def proof(**changes: Any) -> HistoryProof:
    values: dict[str, Any] = dict(
        verified=True,
        period_start=date(2024, 1, 1),
        period_end=date(2026, 2, 13),
        account_ids=[UUID(int=1)],
        evidence_ids=[UUID(int=2)],
        reason_codes=[],
    )
    values.update(changes)
    return HistoryProof(**values)


def source(identifier: int) -> SuggestionSource:
    return SuggestionSource(
        fact_type="transactions",
        fact_id=UUID(int=identifier),
        account_id=UUID(int=1),
        source_ref=f"actual-fixture:{identifier}",
        evidence_id=UUID(int=1000 + identifier),
        evidence_hash="a" * 64,
        evidence_source_type="SIMULATED_BANK_TRANSACTION",
        evidence_source_ref=f"bank:{identifier}",
        evidence_observed_at="2026-02-13T00:00:00+00:00",
        fact={"amount_cents": 100},
    )


def facts(**changes: Any) -> list[PeriodicFact]:
    rows = []
    for index, (day, amount) in enumerate(
        [(date(2025, 12, 12), 100), (date(2026, 1, 13), 110)], 10
    ):
        values: dict[str, Any] = dict(
            kind="FIXED_TRANSFER",
            account_id=UUID(int=1),
            payee_ref="bank-payee:known",
            occurred_on=day,
            amount_cents=amount,
            source=source(index),
        )
        values.update(changes)
        rows.append(PeriodicFact(**values))
    return rows


def test_periodic_real_full_signature_integer_variance_and_no_authority() -> None:
    result = periodic_patterns(facts(), PeriodicParameters(), proof())[0]
    assert result.status == "READY" and result.cycle_count == 2
    assert result.months == ["2025-12", "2026-01"]
    assert result.mean_fraction_cents == "105"
    assert result.variance_fraction_cents_squared == "25"
    assert result.cv_squared_fraction == "1/441"
    assert result.day_spread == 1 and result.suggested_due_day == 12
    assert result.candidate_configuration is not None
    assert result.candidate_configuration["type"] == "periodic_transfer"
    assert result.candidate_configuration["amount_rule"] == {
        "kind": "range",
        "min_cents": 100,
        "max_cents": 110,
    }
    assert result.candidate_configuration["auto_execute"] is False
    assert result.candidate_configuration_hash == configuration_hash(result.candidate_configuration)
    assert not result.bank_authority and not result.future_obligation_guaranteed
    assert result.sources == [row.source for row in facts()]


@pytest.mark.parametrize(
    "case", ["one", "gap", "duplicate_month", "date", "amount", "payee", "type"]
)
def test_periodic_no_trimming_or_cross_payee_type_join(case: str) -> None:
    rows = facts()
    if case == "one":
        rows.pop()
    elif case == "gap":
        rows[1] = rows[1].model_copy(update={"occurred_on": date(2026, 2, 13)})
    elif case == "duplicate_month":
        rows[1] = rows[1].model_copy(update={"occurred_on": date(2025, 12, 13)})
    elif case == "date":
        rows[1] = rows[1].model_copy(update={"occurred_on": date(2026, 1, 20)})
    elif case == "amount":
        rows[1] = rows[1].model_copy(update={"amount_cents": 500})
    elif case == "payee":
        rows[1] = rows[1].model_copy(update={"payee_ref": "another-real-payee"})
    else:
        rows[1] = rows[1].model_copy(update={"kind": "RENT"})
    result = periodic_patterns(rows, PeriodicParameters(), proof())
    assert result and all(item.status != "READY" for item in result)
    assert all(item.candidate_configuration is None for item in result)
    assert sum(item.sample_count for item in result) == len(rows)


def test_invalid_coverage_retains_all_observed_patterns_but_no_candidate() -> None:
    result = periodic_patterns(
        facts(), PeriodicParameters(), proof(verified=False, reason_codes=["DELETED_ORIGINAL"])
    )[0]
    assert result.status == "UNKNOWN" and result.sample_count == 2
    assert result.reason_codes == ["DELETED_ORIGINAL"]
    assert result.candidate_configuration is None


def test_bill_uses_actual_dynamic_bill_balance_contract_without_fixed_amount_grant() -> None:
    result = periodic_patterns(facts(kind="CREDIT_CARD_BILL"), PeriodicParameters(), proof())[0]
    assert result.status == "READY" and result.candidate_configuration is not None
    assert result.candidate_configuration["amount_rule"] == {
        "kind": "bill_balance",
        "account_id": str(UUID(int=1)),
    }
    assert result.template_name == "RecurringObligationPolicy"


@pytest.mark.parametrize(
    "field,value",
    [
        ("minimum_cycles", 1),
        ("maximum_day_spread", 3),
        ("maximum_cv_bps", 1001),
        ("lookback_days", 57),
        ("maximum_cv_bps", True),
    ],
)
def test_periodic_can_only_tighten_registered_limits(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        PeriodicParameters.model_validate({field: value})


def seasonal_spends() -> list[SeasonalSpend]:
    # 2024: 800 holiday - 0 baseline, 8 days => 100/day * 9 = 900.
    # 2025: 2400 holiday - 800 baseline, 8 days => 200/day * 9 = 1800.
    return [
        SeasonalSpend(
            transaction_id=UUID(int=identifier),
            occurred_on=date.fromisoformat(day),
            amount_cents=amount,
            category="food",
            sources=[source(identifier)],
        )
        for identifier, day, amount in [
            (20, "2024-02-10", 800),
            (21, "2025-01-27", 800),
            (22, "2025-01-28", 2400),
        ]
    ]


def seasonal_parameters(**changes: Any) -> SeasonalParameters:
    return SeasonalParameters(window_id="CN-2026-SPRING_FESTIVAL", **changes)


def test_same_festival_integer_normalization_nearest_rank_and_confirm_only() -> None:
    result = seasonal_suggestion(
        seasonal_parameters(), date(2026, 2, 14), proof(), seasonal_spends(), {}
    )
    assert result.status == "READY" and result.window_count == 2
    assert [item.scaled_excess_cents for item in result.comparisons] == [900, 1800]
    assert result.rank == 2 and result.quantile_fraction == "4/5"
    assert result.required_adjustment_cents == result.proposed_adjustment_cents == 1800
    assert result.cap_limited is False
    assert result.candidate_configuration is not None
    assert result.candidate_configuration["advice_only"] is True
    assert result.candidate_configuration["requires_confirmation"] is True
    assert result.candidate_configuration_hash == configuration_hash(result.candidate_configuration)
    assert not result.hard_protection_changed and not result.bank_authority


def test_active_window_only_suggests_remaining_local_days_and_keeps_original_calendar() -> None:
    result = seasonal_suggestion(
        seasonal_parameters(adjustment_cap_cents=500),
        date(2026, 2, 20),
        proof(period_end=date(2026, 2, 19)),
        seasonal_spends(),
        {},
    )
    assert result.status == "READY" and result.target is not None
    assert result.target.start == date(2026, 2, 15)
    assert result.effective_window_start == date(2026, 2, 20)
    assert result.required_adjustment_cents == 800
    assert result.proposed_adjustment_cents == 500 and result.cap_limited is True


@pytest.mark.parametrize(
    "case", ["missing", "short", "category", "not_similar", "year", "past", "lookback"]
)
def test_missing_windows_never_become_zero_or_fake_ready(case: str) -> None:
    parameters = seasonal_parameters()
    coverage = proof()
    invalid = {}
    now = date(2026, 2, 14)
    if case == "missing":
        coverage = proof(verified=False, reason_codes=["MISSING_HISTORY_COVERAGE"])
    elif case == "short":
        coverage = proof(period_start=date(2025, 1, 1))
    elif case == "category":
        invalid = {date(2024, 2, 10): ["INVALID_CATEGORY_CONFIRMATION"]}
    elif case == "not_similar":
        parameters = SeasonalParameters(window_id="CN-2026-NATIONAL_DAY")
    elif case == "year":
        parameters = SeasonalParameters(window_id="CN-2027-SPRING_FESTIVAL")
    elif case == "past":
        now = date(2026, 3, 1)
    else:
        parameters = seasonal_parameters(lookback_days=365)
    result = seasonal_suggestion(parameters, now, coverage, seasonal_spends(), invalid)
    assert result.status != "READY" and result.reason_codes
    assert result.required_adjustment_cents is None and result.rank is None
    assert result.candidate_configuration is None and result.proposed_adjustment_cents is None


def test_complete_zero_history_can_prove_zero_excess_but_never_automatic_reserve_change() -> None:
    result = seasonal_suggestion(seasonal_parameters(), date(2026, 2, 14), proof(), [], {})
    assert result.status == "READY" and result.window_count == 2
    assert result.required_adjustment_cents == 0 and result.hard_protection_changed is False


def test_calendar_year_publication_and_combined_window_are_not_invented() -> None:
    assert all(item.year != 2026 for item in public_calendar(date(2025, 11, 3)))
    windows = public_calendar(date(2026, 2, 14))
    assert len(windows) == 20
    spring = next(item for item in windows if item.window_id == "CN-2026-SPRING_FESTIVAL")
    assert (spring.start, spring.end) == (date(2026, 2, 15), date(2026, 2, 23))
    assert spring.notice_reference == "国办发明电〔2025〕7号"
    assert all("gov.cn" in item.source_url for item in windows)
    assert (
        len([item for item in windows if item.year == 2025 and "AUTUMN" in item.holiday_code]) == 1
    )
    assert not any(item.window_id == "CN-2025-NATIONAL_DAY" for item in windows)


@pytest.mark.parametrize(
    "field,value",
    [
        ("minimum_historical_windows", 1),
        ("quantile_bps", 7999),
        ("adjustment_cap_cents", 500001),
        ("lookback_days", 1097),
        ("essential_categories", ["rent"]),
        ("essential_categories", ["food", "food"]),
        ("now", "2099-01-01"),
    ],
)
def test_seasonal_client_cannot_expand_source_or_rule_scope(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        SeasonalParameters.model_validate({"window_id": "CN-2026-SPRING_FESTIVAL", field: value})
