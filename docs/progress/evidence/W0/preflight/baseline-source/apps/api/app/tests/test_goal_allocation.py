"""Public single-goal preview examples; all values are literal integer cents."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundarySnapshot,
    CashFact,
    GoalMonthFact,
    GoalOwnership,
    SourceIssue,
    UnassignedGoalCash,
)
from app.domain.goal_allocation import IncomeLot, plan_goal_allocation
from app.domain.policy_configuration import configuration_hash, validate_configuration

NOW = datetime(2026, 10, 4, 9, tzinfo=UTC)
CASH, DESTINATION, GOAL, POLICY, VERSION = (UUID(int=n) for n in range(1, 6))


def policy(config: dict[str, Any], **changes: Any) -> BoundaryPolicyVersion:
    normalized = validate_configuration(config)
    values: dict[str, Any] = {
        "policy_id": POLICY,
        "version_id": VERSION,
        "configuration": normalized,
        "content_hash": configuration_hash(normalized),
        "confirmed_at": NOW - timedelta(days=3),
        "valid_from": NOW - timedelta(days=3),
    }
    values.update(changes)
    return BoundaryPolicyVersion(**values)


def example_d() -> tuple[BoundarySnapshot, list[BoundaryPolicyVersion], list[IncomeLot]]:
    goal_policy = policy(
        {
            "type": "goal_saving",
            "target_cents": 2_000_000,
            "deadline": "2026-10-31",
            "monthly_contribution": {
                "min_cents": 150_000,
                "target_cents": 200_000,
                "max_cents": 250_000,
            },
        }
    )
    emergency = policy(
        {"type": "emergency_buffer", "amount_cents": 300_000},
        policy_id=UUID(int=100),
        version_id=UUID(int=101),
    )
    snapshot = BoundarySnapshot(
        as_of=NOW,
        timezone="Asia/Shanghai",
        cash_accounts=[
            CashFact(account_id=CASH, account_type="CASH", balance_cents=900_000, observed_at=NOW),
            CashFact(
                account_id=DESTINATION,
                account_type="GOAL",
                balance_cents=200_000,
                observed_at=NOW,
            ),
        ],
        goals=[
            GoalOwnership(
                goal_id=GOAL,
                policy_id=POLICY,
                account_id=DESTINATION,
                cash_owned_cents=200_000,
                principal_owned_cents=0,
                allocated_cents=200_000,
            )
        ],
        goal_month_contributions=[
            GoalMonthFact(goal_id=GOAL, period="2026-10", contributed_cents=100_000)
        ],
    )
    lots = [
        IncomeLot(
            origin_transaction_id=UUID(int=10),
            account_id=CASH,
            amount_cents=500_000,
            available_cents=500_000,
            occurred_at=NOW - timedelta(days=1),
            observed_at=NOW,
        )
    ]
    return snapshot, [goal_policy, emergency], lots


def test_example_d_targets_cumulative_month_amount_and_preserves_partial_income_lot() -> None:
    snapshot, policies, lots = example_d()
    before = snapshot.model_dump()
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.status == "READY"
    assert result.financial_only is True and result.preview_only is True
    assert result.suggested_cents == result.max_safe_cents == 100_000
    assert (
        result.remaining_min_cents,
        result.remaining_target_cents,
        result.remaining_max_cents,
    ) == (
        50_000,
        100_000,
        150_000,
    )
    assert result.eligible_new_funds_cents == 500_000
    assert result.lot_allocations[0].amount_cents == 100_000
    assert result.lot_allocations[0].remaining_available_cents == 400_000
    assert result.baseline_boundary.safe_idle_cents == 550_000
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.safe_idle_cents == 500_000
    assert result.candidate_boundary.calculation_trace[0].cash_cents == 1_100_000
    assert result.candidate_boundary.protected_cents_by_reason["goal_cash"] == 300_000
    assert len(result.candidate_boundary.calculation_trace) == 273
    assert snapshot.model_dump() == before


def test_whole_window_limits_contribution_after_policy_expiry_without_releasing_ownership() -> None:
    snapshot, policies, lots = example_d()
    snapshot = snapshot.model_copy(
        update={
            "cash_accounts": [
                snapshot.cash_accounts[0].model_copy(update={"balance_cents": 450_000}),
                snapshot.cash_accounts[1],
            ]
        }
    )
    policies[0] = policies[0].model_copy(update={"valid_until": NOW + timedelta(days=1)})
    policies.append(
        policy(
            {"type": "emergency_buffer", "amount_cents": 100_000},
            policy_id=UUID(int=102),
            version_id=UUID(int=103),
            valid_from=NOW + timedelta(days=20),
        )
    )
    lots[0] = lots[0].model_copy(update={"available_cents": 450_000})
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.status == "READY"
    assert result.suggested_cents == result.max_safe_cents == 50_000
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.minimum_margin_cents == 0
    assert (
        result.candidate_boundary.calculation_trace[-1].protected_cents_by_reason["goal_cash"]
        == 250_000
    )


def test_insufficient_new_income_does_not_lower_the_monthly_minimum() -> None:
    snapshot, policies, lots = example_d()
    lots[0] = lots[0].model_copy(update={"available_cents": 20_000})
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.status == "MINIMUM_SHORTFALL"
    assert result.suggested_cents == 0
    assert result.max_safe_cents == 20_000
    assert result.minimum_shortfall_cents == 30_000
    assert result.lot_allocations == []
    assert result.candidate_boundary == result.baseline_boundary


@pytest.mark.parametrize("failure", ["source", "boundary", "cash", "month", "destination"])
def test_incomplete_or_unsafe_baseline_cannot_produce_a_precise_suggestion(failure: str) -> None:
    snapshot, policies, lots = example_d()
    issues = []
    if failure == "source":
        issues = [SourceIssue(code="MISSING_ORIGIN_LEDGER", entity_type="income")]
    elif failure == "boundary":
        snapshot = snapshot.model_copy(
            update={"source_issues": [SourceIssue(code="STALE_CASH", entity_type="account")]}
        )
    elif failure == "cash":
        snapshot = snapshot.model_copy(
            update={
                "cash_accounts": [
                    snapshot.cash_accounts[0].model_copy(update={"balance_cents": 100_000}),
                    snapshot.cash_accounts[1],
                ]
            }
        )
        lots[0] = lots[0].model_copy(update={"available_cents": 100_000})
    elif failure == "month":
        snapshot = snapshot.model_copy(update={"goal_month_contributions": []})
    else:
        snapshot = snapshot.model_copy(
            update={
                "goals": [snapshot.goals[0].model_copy(update={"account_id": None})],
                "cash_accounts": [
                    snapshot.cash_accounts[0],
                    snapshot.cash_accounts[1].model_copy(update={"account_type": "CASH"}),
                ],
            }
        )
    result = plan_goal_allocation(
        GOAL, VERSION, snapshot, policies, [], [], lots, source_issues=issues
    )
    assert result.status == ("LIQUIDITY_RISK" if failure == "cash" else "INSUFFICIENT_EVIDENCE")
    assert result.suggested_cents is None
    assert result.max_safe_cents is None
    assert result.candidate_boundary is None
    assert result.lot_allocations == []


@pytest.mark.parametrize("failure", ["wrong_version", "future", "expired"])
def test_selected_exact_version_must_be_active_at_the_instant_of_planning(failure: str) -> None:
    snapshot, policies, lots = example_d()
    version = VERSION
    if failure == "wrong_version":
        version = UUID(int=999)
    elif failure == "future":
        policies[0] = policies[0].model_copy(update={"valid_from": NOW + timedelta(seconds=1)})
    else:
        policies[0] = policies[0].model_copy(update={"valid_until": NOW})
    result = plan_goal_allocation(GOAL, version, snapshot, policies, [], [], lots)
    assert result.status == "INACTIVE_POLICY"
    assert result.suggested_cents is None
    assert result.lot_allocations == []


def test_income_before_confirmation_is_not_newly_authorized_even_on_the_same_local_day() -> None:
    snapshot, policies, lots = example_d()
    policies[0] = policies[0].model_copy(update={"confirmed_at": NOW - timedelta(hours=1)})
    lots[0] = lots[0].model_copy(update={"occurred_at": NOW - timedelta(hours=2)})
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.status == "MINIMUM_SHORTFALL"
    assert result.eligible_new_funds_cents == result.max_safe_cents == result.suggested_cents == 0


def test_future_income_fact_blocks_precise_planning() -> None:
    snapshot, policies, lots = example_d()
    lots[0] = lots[0].model_copy(update={"observed_at": NOW + timedelta(seconds=1)})
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.suggested_cents is None


@pytest.mark.parametrize(
    "invalid", ["duplicate", "account_total", "goal_cash", "non_cash", "missing"]
)
def test_all_origin_lots_are_checked_before_selecting_any_contribution(invalid: str) -> None:
    snapshot, policies, lots = example_d()
    if invalid == "duplicate":
        lots.append(lots[0].model_copy(update={"available_cents": 0}))
    elif invalid == "account_total":
        lots.append(lots[0].model_copy(update={"origin_transaction_id": UUID(int=11)}))
    elif invalid == "goal_cash":
        snapshot = snapshot.model_copy(
            update={
                "goals": [snapshot.goals[0].model_copy(update={"account_id": CASH})],
                "cash_accounts": [
                    snapshot.cash_accounts[0].model_copy(update={"balance_cents": 600_000}),
                    snapshot.cash_accounts[1].model_copy(update={"account_type": "CASH"}),
                ],
            }
        )
    elif invalid == "non_cash":
        lots[0] = lots[0].model_copy(update={"account_id": DESTINATION, "available_cents": 0})
    else:
        lots[0] = lots[0].model_copy(update={"account_id": UUID(int=999), "available_cents": 0})
    with pytest.raises(ValueError):
        plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)


@pytest.mark.parametrize(
    "changes",
    [
        {"available_cents": True},
        {"available_cents": 1.0},
        {"available_cents": -1},
        {"available_cents": 500_001},
        {"amount_cents": 0},
        {"direction": "DEBIT"},
        {"economic_role": "OPENING"},
        {"source_kind": "USER_CONFIRMED"},
        {"observed_at": NOW.replace(tzinfo=None)},
    ],
)
def test_revalidation_rejects_mutated_or_untrusted_lot_fields(changes: dict[str, Any]) -> None:
    snapshot, policies, lots = example_d()
    lots[0] = lots[0].model_copy(update=changes)
    with pytest.raises(ValueError):
        plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)


def test_input_capacity_is_bounded_before_financial_search() -> None:
    snapshot, policies, lots = example_d()
    with pytest.raises(ValueError, match="10000"):
        plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots * 10001)


def test_protected_current_minimum_is_available_even_when_general_idle_cash_is_zero() -> None:
    snapshot, policies, lots = example_d()
    snapshot = snapshot.model_copy(
        update={
            "cash_accounts": [
                snapshot.cash_accounts[0].model_copy(update={"balance_cents": 350_000}),
                snapshot.cash_accounts[1],
            ]
        }
    )
    lots[0] = lots[0].model_copy(update={"available_cents": 50_000})
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.baseline_boundary.safe_idle_cents == 0
    assert result.status == "READY" and result.suggested_cents == 50_000
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.safe_idle_cents == 0
    assert result.candidate_boundary.protected_cents_by_reason["goal_minimum"] == 0


def test_total_goal_gap_caps_even_the_minimum_as_the_goal_nears_completion() -> None:
    snapshot, policies, lots = example_d()
    policies[0] = policy({**policies[0].configuration, "target_cents": 220_000})
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.status == "READY"
    assert result.suggested_cents == result.remaining_min_cents == 20_000
    assert result.remaining_target_cents == result.remaining_max_cents == 20_000
    assert result.minimum_shortfall_cents == 0


def test_same_cash_account_can_gain_virtual_ownership_without_a_cash_transfer() -> None:
    snapshot, policies, lots = example_d()
    snapshot = snapshot.model_copy(
        update={
            "cash_accounts": [
                snapshot.cash_accounts[0].model_copy(update={"balance_cents": 1_100_000})
            ],
            "goals": [snapshot.goals[0].model_copy(update={"account_id": CASH})],
        }
    )
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.suggested_cents == 100_000
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.calculation_trace[0].cash_cents == 1_100_000
    assert result.candidate_boundary.protected_cents_by_reason["goal_cash"] == 300_000
    assert result.candidate_boundary.minimum_margin_cents == 500_000


def test_unassigned_goal_cash_stays_protected_and_is_not_claimed_as_new_ownership() -> None:
    snapshot, policies, lots = example_d()
    snapshot = snapshot.model_copy(
        update={
            "cash_accounts": [
                snapshot.cash_accounts[0],
                snapshot.cash_accounts[1].model_copy(update={"balance_cents": 250_000}),
            ],
            "unassigned_goal_cash": [
                UnassignedGoalCash(account_id=DESTINATION, amount_cents=50_000)
            ],
        }
    )
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.suggested_cents == 100_000
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.protected_cents_by_reason["goal_cash"] == 350_000
    assert result.candidate_boundary.minimum_margin_cents == 500_000


def test_fifo_and_preview_hash_are_stable_under_input_permutation_without_consumption() -> None:
    snapshot, policies, lots = example_d()
    lots = [
        lots[0].model_copy(
            update={"origin_transaction_id": UUID(int=12), "available_cents": 100_000}
        ),
        lots[0].model_copy(
            update={"origin_transaction_id": UUID(int=11), "available_cents": 40_000}
        ),
        lots[0].model_copy(
            update={
                "origin_transaction_id": UUID(int=13),
                "occurred_at": NOW - timedelta(days=2),
                "available_cents": 30_000,
            }
        ),
    ]
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    repeated = plan_goal_allocation(
        GOAL, VERSION, snapshot, list(reversed(policies)), [], [], list(reversed(lots))
    )
    assert result.model_dump(mode="json") == repeated.model_dump(mode="json")
    assert [use.origin_transaction_id.int for use in result.lot_allocations] == [13, 11, 12]
    assert [use.amount_cents for use in result.lot_allocations] == [30_000, 40_000, 30_000]
    assert [use.remaining_available_cents for use in result.lot_allocations] == [0, 0, 70_000]
    assert lots[0].available_cents == 100_000


def test_successful_contribution_preserves_remaining_lot_without_replaying_month_target() -> None:
    snapshot, policies, lots = example_d()
    snapshot = snapshot.model_copy(
        update={
            "cash_accounts": [
                snapshot.cash_accounts[0].model_copy(update={"balance_cents": 800_000}),
                snapshot.cash_accounts[1].model_copy(update={"balance_cents": 300_000}),
            ],
            "goals": [
                snapshot.goals[0].model_copy(
                    update={"cash_owned_cents": 300_000, "allocated_cents": 300_000}
                )
            ],
            "goal_month_contributions": [
                GoalMonthFact(goal_id=GOAL, period="2026-10", contributed_cents=200_000)
            ],
        }
    )
    lots[0] = lots[0].model_copy(update={"available_cents": 400_000})
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.status == "READY" and result.suggested_cents == 0
    assert result.eligible_new_funds_cents == 400_000
    assert result.remaining_max_cents == 50_000
    assert result.lot_allocations == []


def test_complete_empty_income_ledger_does_not_turn_existing_balance_into_new_income() -> None:
    snapshot, policies, _ = example_d()
    snapshot = snapshot.model_copy(
        update={
            "goal_month_contributions": [
                GoalMonthFact(goal_id=GOAL, period="2026-10", contributed_cents=150_000)
            ]
        }
    )
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], [])
    assert result.status == "READY" and result.suggested_cents == 0
    assert result.eligible_new_funds_cents == 0
    assert result.lot_allocations == []


def test_income_must_already_be_covered_by_its_source_account_balance_observation() -> None:
    snapshot, policies, lots = example_d()
    snapshot = snapshot.model_copy(
        update={
            "cash_accounts": [
                snapshot.cash_accounts[0].model_copy(
                    update={"observed_at": NOW - timedelta(days=2)}
                ),
                snapshot.cash_accounts[1],
            ]
        }
    )
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.suggested_cents is None
    assert "INCOME_NOT_COVERED_BY_CASH_BALANCE" in result.reasons


@pytest.mark.parametrize(
    "deadline, status, amount",
    [("2026-10-03", "INACTIVE_POLICY", None), ("2026-10-04", "READY", 100_000)],
)
def test_goal_deadline_is_inclusive_in_its_local_calendar(
    deadline: str, status: str, amount: int | None
) -> None:
    snapshot, policies, lots = example_d()
    # UTC is still October 3; Asia/Shanghai has already reached October 4.
    as_of = datetime(2026, 10, 3, 23, tzinfo=UTC)
    snapshot = snapshot.model_copy(
        update={
            "as_of": as_of,
            "cash_accounts": [
                account.model_copy(update={"observed_at": as_of})
                for account in snapshot.cash_accounts
            ],
        }
    )
    policies[0] = policy({**policies[0].configuration, "deadline": deadline})
    lots[0] = lots[0].model_copy(update={"observed_at": as_of})
    result = plan_goal_allocation(GOAL, VERSION, snapshot, policies, [], [], lots)
    assert result.baseline_boundary.status == "READY"
    assert result.status == status
    assert result.suggested_cents == amount
    if amount is None:
        assert result.lot_allocations == []
        assert "GOAL_DEADLINE_PASSED" in result.reasons
        assert result.baseline_boundary.protected_cents_by_reason["goal_cash"] == 200_000
