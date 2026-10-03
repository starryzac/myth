"""Living reserve estimation through the public pure-function boundary."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.living_reserve import (
    HistoryCoverage,
    ReserveEstimateInput,
    ReserveTransaction,
    estimate_living_reserve,
)

ACCOUNT = UUID(int=1)


def configuration(**changes: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "living_reserve",
        "horizon_days": 2,
        "method": {
            "name": "rolling_window_quantile",
            "lookback_days": 3,
            "quantile": 0.5,
            "essential_categories": ["food"],
            "exclude_one_off": True,
        },
        "extra_buffer_cents": 50,
    }
    result.update(changes)
    return result


def test_hand_worked_rolling_windows_return_explainable_integer_recommendation() -> None:
    days = [date(2026, 10, day) for day in (1, 2, 3)]
    result = estimate_living_reserve(
        configuration(),
        ReserveEstimateInput(
            reference_date=date(2026, 10, 4),
            timezone="Asia/Shanghai",
            account_ids=[ACCOUNT],
            coverage=[HistoryCoverage(account_id=ACCOUNT, covered_dates=days)],
            transactions=[
                ReserveTransaction(
                    transaction_id=UUID(int=index),
                    account_id=ACCOUNT,
                    occurred_on=day,
                    direction="DEBIT",
                    amount_cents=amount,
                    category="food",
                    category_confirmed=True,
                    is_one_off=False,
                    economic_role="CONSUMPTION",
                )
                for index, (day, amount) in enumerate(zip(days, [100, 200, 300], strict=True), 10)
            ],
        ),
    )
    assert result.status == "READY"
    assert result.history_start == date(2026, 10, 1)
    assert result.history_end == date(2026, 10, 3)
    assert [item.amount_cents for item in result.daily_amounts] == [100, 200, 300]
    assert [item.amount_cents for item in result.windows] == [300, 500]
    assert result.window_count == 2
    assert result.quantile_fraction == "1/2"
    assert result.rank == 1
    assert result.base_reserve_cents == 300
    assert result.recommended_reserve_cents == 350
    assert result.excluded_transactions == []
    assert result.algorithm_version


def standard_configuration() -> dict[str, Any]:
    return configuration(
        horizon_days=14,
        method={
            "name": "rolling_window_quantile",
            "lookback_days": 56,
            "quantile": 0.8,
            "essential_categories": ["food", "transport", "necessities"],
            "exclude_one_off": True,
        },
        extra_buffer_cents=50_000,
    )


def history_input(
    *,
    lookback: int = 56,
    transactions: list[ReserveTransaction] | None = None,
    coverage: list[HistoryCoverage] | None = None,
    accounts: list[UUID] | None = None,
) -> ReserveEstimateInput:
    anchor = date(2026, 10, 4)
    return ReserveEstimateInput(
        reference_date=anchor,
        timezone="Asia/Shanghai",
        account_ids=[ACCOUNT] if accounts is None else accounts,
        transactions=[] if transactions is None else transactions,
        coverage=[
            HistoryCoverage(
                account_id=ACCOUNT,
                covered_dates=[
                    anchor - timedelta(days=offset) for offset in range(1, lookback + 1)
                ],
            )
        ]
        if coverage is None
        else coverage,
    )


def test_complete_56_day_empty_history_has_43_windows_and_a_true_zero_base() -> None:
    result = estimate_living_reserve(standard_configuration(), history_input())
    assert result.status == "READY"
    assert result.history_start == date(2026, 8, 9)
    assert result.history_end == date(2026, 10, 3)
    assert len(result.daily_amounts) == 56
    assert all(item.covered and item.amount_cents == 0 for item in result.daily_amounts)
    assert result.window_count == 43
    assert result.rank == 35
    assert result.base_reserve_cents == 0
    assert result.recommended_reserve_cents == 50_000


@pytest.mark.parametrize("missing", ["all", "one_day", "account", "empty_scope"])
def test_incomplete_coverage_does_not_turn_missing_days_into_zero_spending(missing: str) -> None:
    if missing == "all":
        inputs = history_input(coverage=[])
    elif missing == "one_day":
        inputs = history_input(lookback=55)
    elif missing == "account":
        inputs = history_input(accounts=[ACCOUNT, UUID(int=2)])
    else:
        inputs = history_input(accounts=[], coverage=[])
    result = estimate_living_reserve(standard_configuration(), inputs)
    assert result.status == "INSUFFICIENT_HISTORY"
    assert result.base_reserve_cents is None
    assert result.recommended_reserve_cents is None
    assert result.rank is None
    assert result.windows == []
    assert result.window_count == 0
    assert result.issues
    assert any(not item.covered and item.amount_cents is None for item in result.daily_amounts)
    if missing == "one_day":
        assert result.coverage_gaps[0].missing_dates == [date(2026, 8, 9)]


def transaction(identifier: int, **changes: Any) -> ReserveTransaction:
    values: dict[str, Any] = {
        "transaction_id": UUID(int=identifier),
        "account_id": ACCOUNT,
        "occurred_on": date(2026, 10, 2),
        "direction": "DEBIT",
        "amount_cents": 100,
        "category": "food",
        "category_confirmed": True,
        "is_one_off": False,
        "economic_role": "CONSUMPTION",
    }
    values.update(changes)
    return ReserveTransaction(**values)


def test_confirmed_one_off_and_nonconsumption_are_excluded_with_individual_reasons() -> None:
    transactions = [
        transaction(10, occurred_on=date(2026, 10, 1)),
        transaction(11),
        transaction(12, occurred_on=date(2026, 10, 3)),
        transaction(20, amount_cents=450_000, is_one_off=True),
        transaction(21, amount_cents=500, category="transport"),
        transaction(22, amount_cents=700, category="rent"),
        transaction(23, amount_cents=900, economic_role="NON_CONSUMPTION"),
        transaction(24, amount_cents=1000, direction="CREDIT"),
        transaction(25, amount_cents=1100, category_confirmed=False),
        transaction(26, occurred_on=date(2026, 10, 4)),
        transaction(27, occurred_on=date(2026, 10, 5)),
        transaction(28, occurred_on=date(2026, 9, 30)),
        transaction(29, account_id=UUID(int=2)),
    ]
    inputs = history_input(lookback=3, transactions=transactions)
    result = estimate_living_reserve(configuration(), inputs)
    assert result.status == "READY"
    assert result.base_reserve_cents == 200
    assert result.included_transaction_ids == [UUID(int=number) for number in (10, 11, 12)]
    reasons = {item.transaction_id.int: item.reasons for item in result.excluded_transactions}
    assert reasons == {
        20: ["ONE_OFF"],
        21: ["CATEGORY_NOT_SELECTED"],
        22: ["CATEGORY_NOT_SELECTED"],
        23: ["NON_CONSUMPTION"],
        24: ["NOT_DEBIT"],
        25: ["CATEGORY_UNCONFIRMED"],
        26: ["OUTSIDE_HISTORY"],
        27: ["OUTSIDE_HISTORY"],
        28: ["OUTSIDE_HISTORY"],
        29: ["ACCOUNT_OUT_OF_SCOPE"],
    }
    assert result.excluded_transactions[0].amount_cents == 450_000
    changed = configuration()
    changed["method"]["exclude_one_off"] = False
    with_one_off = estimate_living_reserve(changed, inputs)
    assert with_one_off.base_reserve_cents == 450_200
    changed["method"]["essential_categories"] = ["transport"]
    assert estimate_living_reserve(changed, inputs).base_reserve_cents == 500
    changed["method"]["essential_categories"] = ["rent"]
    assert estimate_living_reserve(changed, inputs).base_reserve_cents == 700


def test_unknown_economic_role_cannot_be_excluded_to_invent_a_precise_reserve() -> None:
    result = estimate_living_reserve(
        configuration(),
        history_input(lookback=3, transactions=[transaction(10, economic_role="UNKNOWN")]),
    )
    assert result.status == "INSUFFICIENT_HISTORY"
    assert result.base_reserve_cents is None
    assert result.recommended_reserve_cents is None
    assert result.rank is None
    assert result.windows == []
    assert "UNKNOWN_ECONOMIC_ROLE" in result.issues
    assert result.excluded_transactions[0].reasons == ["UNKNOWN_ECONOMIC_ROLE"]


def test_nearest_rank_uses_exact_decimal_quantile_instead_of_float_roundoff() -> None:
    config = configuration(horizon_days=1, extra_buffer_cents=0)
    config["method"].update(lookback_days=100, quantile=0.07)
    anchor = date(2026, 10, 4)
    result = estimate_living_reserve(
        config,
        history_input(
            lookback=100,
            transactions=[
                transaction(index, occurred_on=anchor - timedelta(days=index), amount_cents=index)
                for index in range(1, 101)
            ],
        ),
    )
    assert result.window_count == 100
    assert result.quantile_fraction == "7/100"
    assert result.rank == 7
    assert result.base_reserve_cents == 7


def test_order_of_transactions_accounts_and_coverage_does_not_change_json_result() -> None:
    transactions = [
        transaction(11, occurred_on=date(2026, 10, 1), amount_cents=200),
        transaction(12, amount_cents=400),
        transaction(13, amount_cents=450_000, is_one_off=True),
        transaction(14, account_id=UUID(int=2), occurred_on=date(2026, 10, 3), amount_cents=600),
    ]
    days = [date(2026, 10, day) for day in (1, 2, 3)]
    forward = history_input(
        lookback=3,
        transactions=transactions,
        accounts=[ACCOUNT, UUID(int=2)],
        coverage=[
            HistoryCoverage(account_id=account, covered_dates=days)
            for account in (ACCOUNT, UUID(int=2))
        ],
    )
    reverse = history_input(
        lookback=3,
        transactions=list(reversed(transactions)),
        accounts=[UUID(int=2), ACCOUNT],
        coverage=[
            HistoryCoverage(account_id=account, covered_dates=list(reversed(days)))
            for account in (UUID(int=2), ACCOUNT)
        ],
    )
    first = estimate_living_reserve(configuration(), forward)
    assert first.base_reserve_cents == 600
    assert first.model_dump(mode="json") == estimate_living_reserve(
        configuration(), reverse
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    "duplicate", ["transaction", "account", "coverage_account", "coverage_day"]
)
def test_duplicate_identifiers_or_coverage_entries_are_rejected(duplicate: str) -> None:
    with pytest.raises(ValueError):
        if duplicate == "transaction":
            inputs = history_input(transactions=[transaction(10), transaction(10)])
        elif duplicate == "account":
            inputs = history_input(accounts=[ACCOUNT, ACCOUNT])
        elif duplicate == "coverage_account":
            entry = HistoryCoverage(account_id=ACCOUNT, covered_dates=[date(2026, 10, 1)])
            inputs = history_input(coverage=[entry, entry])
        else:
            inputs = history_input(
                coverage=[
                    HistoryCoverage(
                        account_id=ACCOUNT, covered_dates=[date(2026, 10, 1), date(2026, 10, 1)]
                    )
                ]
            )
        estimate_living_reserve(standard_configuration(), inputs)


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_cents": -1},
        {"amount_cents": True},
        {"amount_cents": 1.0},
        {"amount_cents": 0},
        {"amount_cents": 9_223_372_036_854_775_808},
        {"category_confirmed": 1},
        {"is_one_off": "false"},
        {"economic_role": "TRANSFER"},
        {"direction": "UNKNOWN"},
        {"occurred_on": "2026-10-01"},
        {"unexpected": "ignored"},
    ],
)
def test_untrusted_transaction_values_are_not_coerced_or_ignored(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        transaction(10, **changes)


def test_lookback_resource_limit_is_checked_before_building_windows() -> None:
    config = configuration()
    config["method"]["lookback_days"] = 367
    with pytest.raises(ValueError, match="366"):
        estimate_living_reserve(config, history_input())


def test_mutated_input_collection_is_revalidated_at_the_estimator_boundary() -> None:
    inputs = history_input(transactions=[transaction(10)])
    inputs.transactions.append(transaction(10))
    with pytest.raises(ValueError, match="Duplicate transaction"):
        estimate_living_reserve(standard_configuration(), inputs)


def test_date_underflow_is_a_controlled_input_error() -> None:
    inputs = ReserveEstimateInput(
        reference_date=date.min,
        timezone="Asia/Shanghai",
        account_ids=[ACCOUNT],
        transactions=[],
        coverage=[],
    )
    with pytest.raises(ValueError, match="reference_date"):
        estimate_living_reserve(standard_configuration(), inputs)


@pytest.mark.parametrize("oversized", ["accounts", "transactions", "coverage_dates"])
def test_input_collection_capacity_limits_are_enforced(oversized: str) -> None:
    with pytest.raises(ValueError):
        if oversized == "accounts":
            history_input(accounts=[UUID(int=index) for index in range(101)])
        elif oversized == "transactions":
            history_input(transactions=[transaction(10)] * 100_001)
        else:
            history_input(
                coverage=[
                    HistoryCoverage(
                        account_id=ACCOUNT,
                        covered_dates=[
                            date(2026, 10, 4) - timedelta(days=index) for index in range(367)
                        ],
                    )
                ]
            )


def test_window_and_recommendation_overflow_do_not_return_invalid_money() -> None:
    with pytest.raises(ValueError):
        estimate_living_reserve(
            configuration(),
            history_input(
                lookback=3, transactions=[transaction(10, amount_cents=9_223_372_036_854_775_807)]
            ),
        )


def test_missing_coverage_is_not_inferred_from_transactions_on_both_ends() -> None:
    result = estimate_living_reserve(
        configuration(),
        history_input(
            lookback=3,
            coverage=[],
            transactions=[
                transaction(10, occurred_on=date(2026, 10, 1)),
                transaction(11, occurred_on=date(2026, 10, 3)),
            ],
        ),
    )
    assert result.status == "INSUFFICIENT_HISTORY"
    assert result.base_reserve_cents is None
    assert result.recommended_reserve_cents is None
    assert all(item.amount_cents is None for item in result.daily_amounts)
