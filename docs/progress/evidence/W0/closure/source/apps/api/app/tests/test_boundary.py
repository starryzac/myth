"""Public-boundary examples from ADR 0005, using independently hand-worked cents."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BillFact,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundarySnapshot,
    CashFact,
    FixedReturnTerms,
    GoalMonthFact,
    GoalOwnership,
    LivingReserveFact,
    SettlementFact,
    SourceIssue,
    UnassignedGoalCash,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration

NOW = datetime(2026, 10, 4, tzinfo=UTC)
ACCOUNT = UUID(int=1)


def snapshot(cash: int = 1_000_000) -> BoundarySnapshot:
    return BoundarySnapshot(
        as_of=NOW,
        timezone="Asia/Shanghai",
        cash_accounts=[
            CashFact(account_id=ACCOUNT, account_type="CASH", balance_cents=cash, observed_at=NOW),
            CashFact(
                account_id=UUID(int=2), account_type="CREDIT_CARD", balance_cents=0, observed_at=NOW
            ),
        ],
    )


def test_uncommitted_cash_has_91_days_and_no_invented_income_or_execution_authority() -> None:
    result = compute_boundary(snapshot(), [], [], [])
    assert result.status == "READY"
    assert result.financial_only is True
    assert result.safe_idle_cents == 1_000_000
    assert result.minimum_margin_cents == 1_000_000
    assert result.deficit_cents == 0
    assert {point.day for point in result.calculation_trace} == set(range(91))
    assert len(result.calculation_trace) == 273
    assert len(result.boundary_hash) == 64


def policy(identifier: int, config: dict[str, Any], **changes: Any) -> BoundaryPolicyVersion:
    normalized = validate_configuration(config)
    values: dict[str, Any] = {
        "policy_id": UUID(int=identifier),
        "version_id": UUID(int=identifier + 1000),
        "configuration": normalized,
        "content_hash": configuration_hash(normalized),
        "confirmed_at": NOW,
        "valid_from": NOW,
    }
    values.update(changes)
    return BoundaryPolicyVersion(**values)


def life(identifier: int = 20, **changes: Any) -> BoundaryPolicyVersion:
    return policy(
        identifier,
        {
            "type": "living_reserve",
            "horizon_days": 14,
            "method": {
                "name": "rolling_window_quantile",
                "lookback_days": 56,
                "quantile": 0.8,
                "essential_categories": ["food"],
            },
        },
        **changes,
    )


def test_bill_payment_reduces_cash_and_remaining_debt_once_with_rolling_floors() -> None:
    config = life()
    emergency = policy(30, {"type": "emergency_buffer", "amount_cents": 60_000})
    base = snapshot().model_copy(
        update={
            "bills": [
                BillFact(
                    bill_id=UUID(int=10),
                    account_id=UUID(int=2),
                    statement_date=date(2026, 10, 1),
                    due_date=date(2026, 10, 10),
                    total_cents=300_000,
                    paid_cents=0,
                    status="UNPAID",
                )
            ],
            "living_reserves": [
                LivingReserveFact(
                    policy_version_id=config.version_id,
                    amount_cents=140_000,
                    estimation_input_digest="test",
                )
            ],
        }
    )
    result = compute_boundary(base, [config, emergency], [], [])
    assert result.safe_idle_cents == 500_000
    assert result.minimum_margin_cents == 500_000
    assert all(point.margin_cents == 500_000 for point in result.calculation_trace)
    before, after = [
        point for point in result.calculation_trace if point.date == date(2026, 10, 10)
    ][:2]
    assert before.cash_cents == 1_000_000 and after.cash_cents == 700_000
    assert before.protected_cents_by_reason["obligations"] == 300_000
    assert after.protected_cents_by_reason["obligations"] == 0
    assert result.calculation_trace[-1].protected_cents_by_reason["living"] == 140_000


def test_product_maturity_caps_preserve_the_same_day_cash_order() -> None:
    early = policy(30, {"type": "emergency_buffer", "amount_cents": 50_000})
    later = policy(
        31,
        {"type": "emergency_buffer", "amount_cents": 100_000},
        valid_from=NOW + timedelta(days=20),
    )
    products = [
        BoundaryProduct(
            product_id=UUID(int=identity),
            version_number=1,
            asset_class="FIXED_DEPOSIT",
            terms_digest="fixed-contract",
            fixed_return=None
            if days is None
            else FixedReturnTerms(term_days=days, settlement_delay_days=0),
        )
        for identity, days in ((40, None), (41, 7), (42, 30))
    ]
    result = compute_boundary(snapshot(200_000), [early, later], [], products)
    assert result.safe_idle_cents == 50_000
    assert result.max_allocatable_by_product == {
        str(UUID(int=40)): 50_000,
        str(UUID(int=41)): 150_000,
        str(UUID(int=42)): 50_000,
    }
    position = BoundaryPosition(
        position_id=UUID(int=50),
        principal_cents=100_000,
        status="REDEEMING",
        principal_available_at=NOW + timedelta(hours=1),
    )
    with_late_cash = compute_boundary(snapshot(200_000), [early], [position], [])
    assert with_late_cash.safe_idle_cents == 150_000
    assert with_late_cash.calculation_trace[0].cash_cents == 200_000
    assert with_late_cash.calculation_trace[2].cash_cents == 300_000


def test_monthly_occurrences_use_short_month_maximum_and_exact_settlement_identity() -> None:
    recurring = policy(
        60,
        {
            "type": "recurring_obligation",
            "payee_id": "rent",
            "amount_rule": {"kind": "range", "min_cents": 50_000, "max_cents": 100_000},
            "due_day": 31,
            "prepare_days_before": 5,
        },
    )
    card = policy(
        61,
        {
            "type": "recurring_obligation",
            "payee_id": "credit-card",
            "amount_rule": {"kind": "bill_balance", "account_id": str(UUID(int=2))},
            "due_day": 20,
        },
    )
    base = snapshot().model_copy(
        update={
            "occurrence_settlements": [
                SettlementFact(
                    policy_id=recurring.policy_id,
                    period="2026-10",
                    paid_cents=40_000,
                    settled_at=NOW,
                )
            ]
        }
    )
    result = compute_boundary(base, [recurring, card], [], [])
    assert result.safe_idle_cents == 740_000
    assert result.calculation_trace[0].protected_cents_by_reason["obligations"] == 260_000
    november = [p for p in result.calculation_trace if p.date == date(2026, 11, 30)]
    assert november[0].cash_cents - november[1].cash_cents == 100_000
    assert any("UNISSUED_BILL" in note for note in result.calculation_notes)


def test_goal_minimum_is_capped_and_retained_across_months_without_virtual_contributions() -> None:
    goal = policy(
        70,
        {
            "type": "goal_saving",
            "target_cents": 1_000_000,
            "deadline": "2026-12-31",
            "monthly_contribution": {
                "min_cents": 100_000,
                "target_cents": 100_000,
                "max_cents": 100_000,
            },
        },
    )
    base = snapshot().model_copy(
        update={
            "goals": [
                GoalOwnership(
                    goal_id=UUID(int=71),
                    policy_id=goal.policy_id,
                    cash_owned_cents=200_000,
                    principal_owned_cents=100_000,
                    allocated_cents=300_000,
                )
            ],
            "goal_month_contributions": [
                GoalMonthFact(goal_id=UUID(int=71), period="2026-10", contributed_cents=40_000)
            ],
        }
    )
    held = BoundaryPosition(
        position_id=UUID(int=72), goal_id=UUID(int=71), principal_cents=100_000, status="HELD"
    )
    result = compute_boundary(base, [goal], [held], [])
    assert result.safe_idle_cents == 540_000
    assert all(
        point.protected_cents_by_reason["goal_minimum"] == 260_000
        for point in result.calculation_trace
    )
    smaller = policy(70, {**goal.configuration, "target_cents": 450_000})
    capped = compute_boundary(base, [smaller], [held], [])
    assert capped.protected_cents_by_reason["goal_minimum"] == 150_000


@pytest.mark.parametrize(
    "missing",
    ["source", "cash", "living", "goal", "contribution", "past_settlement", "unknown_position"],
)
def test_missing_evidence_has_no_precise_boundary(missing: str) -> None:
    base = snapshot()
    policies: list[BoundaryPolicyVersion] = []
    positions: list[BoundaryPosition] = []
    if missing == "source":
        base = base.model_copy(
            update={"source_issues": [SourceIssue(code="MISSING_BALANCE", entity_type="account")]}
        )
    elif missing == "cash":
        base = base.model_copy(update={"cash_accounts": []})
    elif missing == "living":
        policies = [life()]
    elif missing in {"goal", "contribution"}:
        policies = [
            policy(
                70,
                {
                    "type": "goal_saving",
                    "target_cents": 1_000_000,
                    "deadline": "2026-12-31",
                    "monthly_contribution": {
                        "min_cents": 100_000,
                        "target_cents": 100_000,
                        "max_cents": 100_000,
                    },
                },
            )
        ]
        if missing == "contribution":
            base = base.model_copy(
                update={
                    "goals": [
                        GoalOwnership(
                            goal_id=UUID(int=71),
                            policy_id=UUID(int=70),
                            cash_owned_cents=0,
                            principal_owned_cents=0,
                            allocated_cents=0,
                        )
                    ]
                }
            )
    elif missing == "past_settlement":
        policies = [
            policy(
                60,
                {
                    "type": "recurring_obligation",
                    "payee_id": "rent",
                    "due_day": 1,
                    "amount_rule": {"kind": "exact", "amount_cents": 100_000},
                },
                confirmed_at=NOW - timedelta(days=5),
                valid_from=NOW - timedelta(days=5),
            )
        ]
    else:
        positions = [
            BoundaryPosition(position_id=UUID(int=80), principal_cents=100, status="UNKNOWN")
        ]
    result = compute_boundary(base, policies, positions, [])
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.safe_idle_cents is None and result.minimum_margin_cents is None
    assert result.deficit_cents is None and result.blocking_constraints
    assert result.calculation_trace == []


@pytest.mark.parametrize(
    "duplicate",
    [
        "account",
        "policy",
        "version",
        "bill",
        "settlement",
        "goal",
        "contribution",
        "product",
        "position",
    ],
)
def test_duplicate_financial_identities_are_rejected(duplicate: str) -> None:
    base = snapshot()
    policies = []
    positions = []
    products = []
    if duplicate == "account":
        base.cash_accounts.append(base.cash_accounts[0])
    elif duplicate in {"policy", "version"}:
        first = policy(30, {"type": "emergency_buffer", "amount_cents": 1})
        policies = [
            first,
            first
            if duplicate == "policy"
            else first.model_copy(update={"policy_id": UUID(int=31)}),
        ]
    elif duplicate == "bill":
        item = BillFact(
            bill_id=UUID(int=10),
            account_id=UUID(int=2),
            statement_date=date(2026, 10, 1),
            due_date=date(2026, 10, 20),
            total_cents=1,
            paid_cents=0,
            status="UNPAID",
        )
        base.bills.extend([item, item])
    elif duplicate == "settlement":
        item_settlement = SettlementFact(
            policy_id=UUID(int=30), period="2026-10", paid_cents=0, settled_at=NOW
        )
        base.occurrence_settlements.extend([item_settlement, item_settlement])
    elif duplicate == "goal":
        item_goal = GoalOwnership(
            goal_id=UUID(int=71),
            policy_id=UUID(int=70),
            cash_owned_cents=0,
            principal_owned_cents=0,
            allocated_cents=0,
        )
        base.goals.extend([item_goal, item_goal])
    elif duplicate == "contribution":
        item_contribution = GoalMonthFact(
            goal_id=UUID(int=71), period="2026-10", contributed_cents=0
        )
        base.goal_month_contributions.extend([item_contribution, item_contribution])
    elif duplicate == "position":
        item_position = BoundaryPosition(
            position_id=UUID(int=80), principal_cents=100, status="HELD"
        )
        positions = [item_position, item_position]
    else:
        item_product = BoundaryProduct(
            product_id=UUID(int=90),
            version_number=1,
            asset_class="CASH_MGMT_T0",
            terms_digest="financial",
        )
        products = [item_product, item_product]
    with pytest.raises(ValueError, match="Duplicate"):
        compute_boundary(base, policies, positions, products)


@pytest.mark.parametrize(
    "case",
    [
        "bill_paid",
        "bill_status",
        "bill_dates",
        "policy_hash",
        "policy_window",
        "goal_principal",
        "goal_cash",
        "unassigned_account",
        "future_contribution",
        "future_settlement",
    ],
)
def test_contradictory_financial_facts_are_rejected(case: str) -> None:
    base = snapshot()
    policies = []
    if case.startswith("bill"):
        changes: dict[str, Any] = (
            {"paid_cents": 101}
            if case == "bill_paid"
            else {"status": "PAID"}
            if case == "bill_status"
            else {"due_date": date(2026, 9, 30)}
        )
        base.bills.append(
            BillFact.model_validate(
                {
                    "bill_id": UUID(int=10),
                    "account_id": UUID(int=2),
                    "statement_date": date(2026, 10, 1),
                    "due_date": date(2026, 10, 20),
                    "total_cents": 100,
                    "paid_cents": 0,
                    "status": "UNPAID",
                }
                | changes
            )
        )
    elif case.startswith("policy"):
        policies = [
            policy(
                30,
                {"type": "emergency_buffer", "amount_cents": 1},
                **(
                    {"content_hash": "wrong"}
                    if case == "policy_hash"
                    else {"valid_until": NOW - timedelta(days=1)}
                ),
            )
        ]
    elif case.startswith("goal"):
        cash, principal = (0, 100) if case == "goal_principal" else (1_000_001, 0)
        base.goals.append(
            GoalOwnership(
                goal_id=UUID(int=71),
                policy_id=UUID(int=70),
                cash_owned_cents=cash,
                principal_owned_cents=principal,
                allocated_cents=cash + principal,
            )
        )
    elif case == "unassigned_account":
        base.unassigned_goal_cash.append(UnassignedGoalCash(account_id=ACCOUNT, amount_cents=100))
    elif case == "future_contribution":
        base.goal_month_contributions.append(
            GoalMonthFact(goal_id=UUID(int=71), period="2026-11", contributed_cents=100)
        )
    else:
        base.occurrence_settlements.append(
            SettlementFact(
                policy_id=UUID(int=60),
                period="2026-10",
                paid_cents=100,
                settled_at=NOW + timedelta(days=1),
            )
        )
    with pytest.raises(ValueError):
        compute_boundary(base, policies, [], [])


def test_unmapped_goal_cash_cannot_become_general_idle_money() -> None:
    base = snapshot().model_copy(
        update={
            "cash_accounts": [
                CashFact(
                    account_id=ACCOUNT, account_type="GOAL", balance_cents=1000, observed_at=NOW
                )
            ]
        }
    )
    assert compute_boundary(base, [], [], []).status == "INSUFFICIENT_EVIDENCE"
    base.unassigned_goal_cash.append(UnassignedGoalCash(account_id=ACCOUNT, amount_cents=1000))
    assert compute_boundary(base, [], [], []).safe_idle_cents == 0


@pytest.mark.parametrize("future", ["cash", "policy", "bill"])
def test_future_observations_or_confirmations_are_not_current_evidence(future: str) -> None:
    base = snapshot()
    policies = []
    if future == "cash":
        base.cash_accounts[0] = base.cash_accounts[0].model_copy(
            update={"observed_at": NOW + timedelta(seconds=1)}
        )
    elif future == "policy":
        policies = [
            policy(
                30,
                {"type": "emergency_buffer", "amount_cents": 1},
                confirmed_at=NOW + timedelta(seconds=1),
            )
        ]
    else:
        base.bills.append(
            BillFact(
                bill_id=UUID(int=10),
                account_id=UUID(int=2),
                statement_date=date(2026, 10, 5),
                due_date=date(2026, 10, 20),
                total_cents=100,
                paid_cents=0,
                status="UNPAID",
            )
        )
    assert compute_boundary(base, policies, [], []).status == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("month,day", [(12, 31), (10, 2)])
def test_calendar_overflow_is_a_controlled_input_error(month: int, day: int) -> None:
    base = snapshot().model_copy(update={"as_of": datetime(9999, month, day, tzinfo=UTC)})
    emergency = policy(30, {"type": "emergency_buffer", "amount_cents": 1})
    with pytest.raises(ValueError, match="date"):
        compute_boundary(base, [emergency], [], [])


def test_policy_confirmed_today_does_not_create_unconfirmed_past_occurrences() -> None:
    recurring = policy(
        60,
        {
            "type": "recurring_obligation",
            "payee_id": "rent",
            "due_day": 1,
            "amount_rule": {"kind": "exact", "amount_cents": 100_000},
        },
        valid_from=NOW - timedelta(days=365),
    )
    result = compute_boundary(snapshot(), [recurring], [], [])
    assert result.status == "READY"
    assert result.safe_idle_cents == 700_000
    assert len(result.calculation_trace[0].obligation_occurrence_ids) == 3


def test_first_half_month_goal_minimum_remains_until_explicit_validity_end() -> None:
    goal = policy(
        70,
        {
            "type": "goal_saving",
            "target_cents": 1_000_000,
            "deadline": "2026-10-20",
            "monthly_contribution": {
                "min_cents": 100_000,
                "target_cents": 100_000,
                "max_cents": 100_000,
            },
        },
        valid_until=NOW + timedelta(days=40),
    )
    base = snapshot().model_copy(
        update={
            "goals": [
                GoalOwnership(
                    goal_id=UUID(int=71),
                    policy_id=goal.policy_id,
                    cash_owned_cents=100_000,
                    principal_owned_cents=0,
                    allocated_cents=100_000,
                )
            ],
            "goal_month_contributions": [
                GoalMonthFact(goal_id=UUID(int=71), period="2026-10", contributed_cents=0)
            ],
        }
    )
    result = compute_boundary(base, [goal], [], [])
    by_day = {p.day: p for p in result.calculation_trace if p.phase == "BEFORE_PAYMENT"}
    assert by_day[30].protected_cents_by_reason["goal_minimum"] == 100_000
    assert by_day[41].protected_cents_by_reason["goal_minimum"] == 0
    assert by_day[41].protected_cents_by_reason["goal_cash"] == 100_000


def test_current_protection_breakdown_is_separate_from_a_later_worst_margin() -> None:
    future = policy(
        30,
        {"type": "emergency_buffer", "amount_cents": 900_000},
        valid_from=NOW + timedelta(days=10),
    )
    result = compute_boundary(snapshot(), [future], [], [])
    assert result.protected_cents_by_reason["emergency"] == 0
    assert result.minimum_margin_cents == 100_000


def test_bill_cannot_bind_to_a_missing_or_noncard_account() -> None:
    base = snapshot()
    base.bills.append(
        BillFact(
            bill_id=UUID(int=10),
            account_id=ACCOUNT,
            statement_date=date(2026, 10, 1),
            due_date=date(2026, 10, 20),
            total_cents=100,
            paid_cents=0,
            status="UNPAID",
        )
    )
    with pytest.raises(ValueError, match="card account"):
        compute_boundary(base, [], [], [])


def test_expired_recurring_policy_does_not_erase_an_already_generated_unpaid_occurrence() -> None:
    recurring = policy(
        60,
        {
            "type": "recurring_obligation",
            "payee_id": "rent",
            "due_day": 15,
            "amount_rule": {"kind": "exact", "amount_cents": 50_000},
        },
        confirmed_at=datetime(2026, 9, 1, tzinfo=UTC),
        valid_from=datetime(2026, 9, 1, tzinfo=UTC),
        valid_until=NOW - timedelta(hours=8),
    )
    base = snapshot(100_000).model_copy(
        update={
            "occurrence_settlements": [
                SettlementFact(
                    policy_id=recurring.policy_id, period="2026-09", paid_cents=0, settled_at=NOW
                )
            ]
        }
    )
    result = compute_boundary(base, [recurring], [], [])
    assert result.safe_idle_cents == 50_000
    assert result.protected_cents_by_reason["obligations"] == 50_000
    assert result.calculation_trace[-1].cash_cents == 50_000
