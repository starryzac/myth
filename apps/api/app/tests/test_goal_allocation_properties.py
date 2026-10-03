"""Independent small-cent and source-identity oracle for goal allocation previews."""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundarySnapshot,
    CashFact,
    GoalMonthFact,
    GoalOwnership,
)
from app.domain.goal_allocation import IncomeLot, plan_goal_allocation
from app.domain.policy_configuration import configuration_hash, validate_configuration
from hypothesis import given, settings
from hypothesis import strategies as st

NOW = datetime(2026, 10, 3, 16, tzinfo=UTC)
START = datetime(2026, 9, 30, 16, tzinfo=UTC)
SOURCE = UUID(int=1)
TARGET = UUID(int=2)
OTHER_ACCOUNT = UUID(int=3)
GOAL = UUID(int=10)
OTHER_GOAL = UUID(int=11)
POLICY = UUID(int=20)
VERSION = UUID(int=21)
PROPERTY_SETTINGS = settings(max_examples=200, derandomize=True, database=None, deadline=None)


def policy(
    configuration: dict[str, Any],
    *,
    identifier: UUID = POLICY,
    version: UUID = VERSION,
    until: datetime | None = None,
) -> BoundaryPolicyVersion:
    canonical = validate_configuration(configuration)
    return BoundaryPolicyVersion(
        policy_id=identifier,
        version_id=version,
        configuration=canonical,
        content_hash=configuration_hash(canonical),
        confirmed_at=START,
        valid_from=START,
        valid_until=until,
    )


def goal_context(
    *,
    source_cash: int,
    goal_cash: int,
    monthly: tuple[int, int, int],
    contributed: int,
    total_target: int,
    floor: int = 0,
    other_goal_cash: int = 0,
    principal: int = 0,
    same_account: bool = False,
    target_is_cash: bool = False,
    as_of: datetime = NOW,
    one_month: bool = True,
) -> tuple[BoundarySnapshot, list[BoundaryPolicyVersion], list[BoundaryPosition]]:
    goal_account = SOURCE if same_account else TARGET
    account_facts = [
        CashFact(
            account_id=SOURCE,
            account_type="CASH",
            balance_cents=source_cash + (goal_cash if same_account else 0),
            observed_at=as_of,
        ),
        CashFact(
            account_id=OTHER_ACCOUNT,
            account_type="GOAL",
            balance_cents=other_goal_cash,
            observed_at=as_of,
        ),
    ]
    if not same_account:
        account_facts.append(
            CashFact(
                account_id=TARGET,
                account_type="CASH" if target_is_cash else "GOAL",
                balance_cents=goal_cash,
                observed_at=as_of,
            )
        )
    snapshot = BoundarySnapshot(
        as_of=as_of,
        timezone="Asia/Shanghai",
        cash_accounts=account_facts,
        goals=[
            GoalOwnership(
                goal_id=GOAL,
                policy_id=POLICY,
                account_id=goal_account,
                cash_owned_cents=goal_cash,
                principal_owned_cents=principal,
                allocated_cents=goal_cash + principal,
            ),
            GoalOwnership(
                goal_id=OTHER_GOAL,
                policy_id=UUID(int=22),
                account_id=OTHER_ACCOUNT,
                cash_owned_cents=other_goal_cash,
                principal_owned_cents=0,
                allocated_cents=other_goal_cash,
            ),
        ],
        goal_month_contributions=[
            GoalMonthFact(
                goal_id=GOAL,
                period=(as_of + timedelta(hours=8)).strftime("%Y-%m"),
                contributed_cents=contributed,
            )
        ],
    )
    versions = [
        policy(
            {
                "type": "goal_saving",
                "target_cents": total_target,
                "deadline": "2026-10-31" if one_month else "2027-12-31",
                "monthly_contribution": {
                    "min_cents": monthly[0],
                    "target_cents": monthly[1],
                    "max_cents": monthly[2],
                },
            },
            until=datetime(2026, 10, 31, 16, tzinfo=UTC) if one_month else None,
        ),
        policy(
            {"type": "emergency_buffer", "amount_cents": floor},
            identifier=UUID(int=23),
            version=UUID(int=24),
        ),
    ]
    positions = (
        [
            BoundaryPosition(
                position_id=UUID(int=30),
                goal_id=GOAL,
                principal_cents=principal,
                status="HELD",
            )
        ]
        if principal
        else []
    )
    return snapshot, versions, positions


def income_lot(
    amount: int,
    available: int | None = None,
    *,
    identifier: int = 100,
    occurred_at: datetime = NOW - timedelta(hours=1),
) -> IncomeLot:
    return IncomeLot(
        origin_transaction_id=UUID(int=identifier),
        account_id=SOURCE,
        amount_cents=amount,
        available_cents=amount if available is None else available,
        occurred_at=occurred_at,
        observed_at=occurred_at,
    )


@dataclass(frozen=True)
class SourceCents:
    transaction_id: UUID
    occurred_at: datetime
    original_cents: int
    available_cents: int


def source_tokens(sources: tuple[SourceCents, ...]) -> tuple[tuple[UUID, int], ...]:
    """Each available cent has a stable origin and can occur only once in a plan.

    These fixtures start from already verified residual balances. They do not
    reconstruct a bank ledger or decide source eligibility.
    """
    return tuple(
        (source.transaction_id, cent)
        for source in sorted(sources, key=lambda item: (item.occurred_at, item.transaction_id))
        for cent in range(source.original_cents - source.available_cents, source.original_cents)
    )


def literal_ledger_cap(
    *,
    total_cash: int,
    other_protection: int,
    selected_goal_cash: int,
    selected_goal_principal: int,
    total_target: int,
    monthly_minimum: int,
    monthly_target: int,
    monthly_maximum: int,
    contributed: int,
    sources: tuple[SourceCents, ...],
) -> int:
    """Exhaust cents in a one-month, constant-other-protection test fixture.

    No production planner, boundary function, policy parser or returned trace is
    consulted. This oracle does not handle general calendars or multiple goals.
    """
    initial_owned = selected_goal_cash + selected_goal_principal
    allowed: list[int] = []
    for amount in range(len(source_tokens(sources)) + 1):
        if contributed + amount > monthly_target or contributed + amount > monthly_maximum:
            continue
        if initial_owned + amount > total_target:
            continue
        missing_month_minimum = max(0, monthly_minimum - contributed - amount)
        missing_total_target = max(0, total_target - initial_owned - amount)
        remaining_minimum = min(missing_month_minimum, missing_total_target)
        remaining_cash = total_cash - other_protection - selected_goal_cash - amount
        if remaining_cash >= remaining_minimum:
            allowed.append(amount)
    return max(allowed, default=0)


def test_example_d_is_literal_accounting_not_a_new_income_forecast() -> None:
    # financial-semantics-review.md example D, converted from yuan to cents.
    before_cash = 600_000
    arrived_salary = 500_000
    existing_goal_cash = 200_000
    other_protection = 300_000
    already_contributed = 100_000
    contribution = 100_000
    assert before_cash + arrived_salary == 1_100_000
    assert (
        150_000 - already_contributed,
        200_000 - already_contributed,
        250_000 - already_contributed,
    ) == (50_000, 100_000, 150_000)
    assert existing_goal_cash + contribution == 300_000
    assert already_contributed + contribution == 200_000
    assert arrived_salary - contribution == 400_000
    assert 1_100_000 - other_protection - existing_goal_cash - 50_000 == 550_000
    assert 1_100_000 - other_protection - 300_000 == 500_000


def test_available_source_cents_have_unique_fifo_identity_after_partial_consumption() -> None:
    now = datetime(2026, 10, 4, tzinfo=UTC)
    sources = (
        SourceCents(UUID(int=2), now, 5, 3),
        SourceCents(UUID(int=1), now, 4, 2),
    )
    assert source_tokens(sources) == (
        (UUID(int=1), 2),
        (UUID(int=1), 3),
        (UUID(int=2), 2),
        (UUID(int=2), 3),
        (UUID(int=2), 4),
    )
    assert len(set(source_tokens(sources))) == 5
    assert sum(item.original_cents for item in sources) == 9
    assert len(source_tokens(tuple(reversed(sources)))) == 5


def test_small_literal_ledger_does_not_subtract_the_same_minimum_twice() -> None:
    sources = (SourceCents(UUID(int=1), datetime(2026, 10, 4, tzinfo=UTC), 10, 10),)
    # Initial general idle is zero, but its five-cent minimum is earmarked for
    # this goal. Moving precisely five cents changes ownership, not total cash.
    assert (
        literal_ledger_cap(
            total_cash=25,
            other_protection=10,
            selected_goal_cash=10,
            selected_goal_principal=0,
            total_target=100,
            monthly_minimum=5,
            monthly_target=10,
            monthly_maximum=15,
            contributed=0,
            sources=sources,
        )
        == 5
    )


def test_public_preview_matches_example_d_and_preserves_unused_salary() -> None:
    snapshot, versions, positions = goal_context(
        source_cash=900_000,
        goal_cash=200_000,
        monthly=(150_000, 200_000, 250_000),
        contributed=100_000,
        total_target=1_000_000,
        floor=300_000,
    )
    lots = [income_lot(500_000)]
    before = snapshot.model_dump(mode="json")
    result = plan_goal_allocation(GOAL, VERSION, snapshot, versions, positions, [], lots)
    assert result.status == "READY"
    assert result.preview_only is True and result.financial_only is True
    assert result.suggested_cents == result.max_safe_cents == 100_000
    assert result.remaining_min_cents == 50_000
    assert result.remaining_target_cents == 100_000
    assert result.remaining_max_cents == 150_000
    assert result.eligible_new_funds_cents == 500_000
    assert result.minimum_shortfall_cents == 0
    assert len(result.lot_allocations) == 1
    assert result.lot_allocations[0].amount_cents == 100_000
    assert result.lot_allocations[0].remaining_available_cents == 400_000
    assert result.baseline_boundary.safe_idle_cents == 550_000
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.safe_idle_cents == 500_000
    assert result.candidate_boundary.calculation_trace[0].cash_cents == 1_100_000
    assert result.candidate_boundary.protected_cents_by_reason["goal_cash"] == 300_000
    assert snapshot.model_dump(mode="json") == before
    replay = plan_goal_allocation(GOAL, VERSION, snapshot, versions, positions, [], lots)
    assert replay.model_dump(mode="json") == result.model_dump(mode="json")


@dataclass(frozen=True)
class FundedCase:
    monthly: tuple[int, int, int]
    contributed: int
    goal_cash: int
    principal: int
    total_target: int
    source_cash: int
    other_goal_cash: int
    floor: int
    original: int
    available: int
    same_account: bool


@st.composite
def funded_cases(draw: st.DrawFn) -> FundedCase:
    contributed = draw(st.integers(0, 10))
    remaining_minimum = draw(st.integers(1, 10))
    remaining_target = remaining_minimum + draw(st.integers(0, 15))
    remaining_maximum = remaining_target + draw(st.integers(0, 15))
    goal_cash = draw(st.integers(0, 20))
    principal = draw(st.integers(0, 20))
    gap = remaining_minimum + draw(st.integers(0, 25))
    floor = draw(st.integers(0, 15))
    source_cash = floor + remaining_minimum + draw(st.integers(0, 25))
    available = draw(st.integers(remaining_minimum, source_cash))
    return FundedCase(
        monthly=(
            contributed + remaining_minimum,
            contributed + remaining_target,
            contributed + remaining_maximum,
        ),
        contributed=contributed,
        goal_cash=goal_cash,
        principal=principal,
        total_target=goal_cash + principal + gap,
        source_cash=source_cash,
        other_goal_cash=draw(st.integers(1, 30)),
        floor=floor,
        original=available + draw(st.integers(0, 20)),
        available=available,
        same_account=draw(st.booleans()),
    )


@pytest.mark.property
@PROPERTY_SETTINGS
@given(case=funded_cases())
def test_positive_maximum_is_safe_and_next_cent_breaks_ledger_or_business_limit(
    case: FundedCase,
) -> None:
    snapshot, versions, positions = goal_context(
        source_cash=case.source_cash,
        goal_cash=case.goal_cash,
        monthly=case.monthly,
        contributed=case.contributed,
        total_target=case.total_target,
        floor=case.floor,
        other_goal_cash=case.other_goal_cash,
        principal=case.principal,
        same_account=case.same_account,
    )
    original_snapshot = snapshot.model_dump(mode="json")
    lot = income_lot(case.original, case.available)
    source = SourceCents(lot.origin_transaction_id, lot.occurred_at, case.original, case.available)
    total_cash = case.source_cash + case.goal_cash + case.other_goal_cash
    expected = literal_ledger_cap(
        total_cash=total_cash,
        other_protection=case.floor + case.other_goal_cash,
        selected_goal_cash=case.goal_cash,
        selected_goal_principal=case.principal,
        total_target=case.total_target,
        monthly_minimum=case.monthly[0],
        monthly_target=case.monthly[1],
        monthly_maximum=case.monthly[2],
        contributed=case.contributed,
        sources=(source,),
    )
    result = plan_goal_allocation(GOAL, VERSION, snapshot, versions, positions, [], [lot])
    assert expected > 0
    assert result.status == "READY"
    assert result.max_safe_cents == result.suggested_cents == expected
    assert result.goal_id == GOAL and result.policy_version_id == VERSION
    assert result.eligible_new_funds_cents == case.available
    assert result.minimum_shortfall_cents == 0
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.status == "READY"
    assert all(point.margin_cents >= 0 for point in result.candidate_boundary.calculation_trace)
    assert result.candidate_boundary.calculation_trace[0].cash_cents == total_cash
    assert result.candidate_boundary.protected_cents_by_reason["goal_cash"] == (
        case.goal_cash + case.other_goal_cash + expected
    )
    assert sum(item.amount_cents for item in result.lot_allocations) == expected
    assert result.lot_allocations[0].remaining_available_cents == case.available - expected
    assert snapshot.model_dump(mode="json") == original_snapshot

    excess = expected + 1
    owned_after_excess = case.goal_cash + case.principal + excess
    month_after_excess = case.contributed + excess
    business_limit = (
        excess > case.available
        or owned_after_excess > case.total_target
        or month_after_excess > case.monthly[1]
        or month_after_excess > case.monthly[2]
    )
    still_protected = min(
        max(0, case.monthly[0] - month_after_excess),
        max(0, case.total_target - owned_after_excess),
    )
    cash_margin = case.source_cash - case.floor - excess - still_protected
    assert business_limit or cash_margin < 0


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    minimum=st.integers(1, 20),
    shortage_fraction=st.integers(1, 20),
    target_extra=st.integers(0, 20),
    previously_used=st.integers(0, 20),
    floor=st.integers(0, 15),
    old_free_cash=st.integers(0, 20),
    goal_cash=st.integers(0, 20),
    same_account=st.booleans(),
)
def test_missing_one_or_more_new_cents_never_rewrites_monthly_minimum(
    minimum: int,
    shortage_fraction: int,
    target_extra: int,
    previously_used: int,
    floor: int,
    old_free_cash: int,
    goal_cash: int,
    same_account: bool,
) -> None:
    shortage = min(shortage_fraction, minimum)
    available = minimum - shortage
    snapshot, versions, positions = goal_context(
        source_cash=floor + minimum + old_free_cash,
        goal_cash=goal_cash,
        monthly=(minimum, minimum + target_extra, minimum + target_extra + 10),
        contributed=0,
        total_target=goal_cash + minimum + target_extra + 20,
        floor=floor,
        other_goal_cash=10,
        same_account=same_account,
    )
    original_policy = versions[0].model_dump(mode="json")
    lot = income_lot(max(1, available + previously_used), available)
    result = plan_goal_allocation(GOAL, VERSION, snapshot, versions, positions, [], [lot])
    assert result.status == "MINIMUM_SHORTFALL"
    assert result.remaining_min_cents == minimum
    assert result.minimum_shortfall_cents == shortage
    assert result.max_safe_cents == available
    assert result.suggested_cents == 0
    assert result.lot_allocations == []
    assert result.candidate_boundary == result.baseline_boundary
    assert versions[0].model_dump(mode="json") == original_policy


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    available=st.tuples(st.integers(1, 20), st.integers(1, 20), st.integers(1, 20)),
    used=st.tuples(st.integers(0, 20), st.integers(0, 20), st.integers(0, 20)),
    ages=st.tuples(st.integers(0, 3), st.integers(0, 3), st.integers(0, 3)),
    order=st.permutations((0, 1, 2)),
    requested=st.integers(1, 60),
    same_account=st.booleans(),
)
def test_fifo_uses_unique_residual_cents_and_input_permutations_do_not_change_plan(
    available: tuple[int, int, int],
    used: tuple[int, int, int],
    ages: tuple[int, int, int],
    order: tuple[int, ...],
    requested: int,
    same_account: bool,
) -> None:
    wanted = min(requested, sum(available))
    snapshot, versions, positions = goal_context(
        source_cash=sum(available) + 10,
        goal_cash=20,
        monthly=(0, wanted, wanted + 10),
        contributed=0,
        total_target=20 + wanted + 20,
        floor=10,
        other_goal_cash=50,
        same_account=same_account,
    )
    lots = [
        income_lot(
            remaining + spent,
            remaining,
            identifier=100 + index,
            occurred_at=NOW - timedelta(hours=1, minutes=ages[index]),
        )
        for index, (remaining, spent) in enumerate(zip(available, used, strict=True))
    ]
    sources = tuple(
        SourceCents(
            lot.origin_transaction_id, lot.occurred_at, lot.amount_cents, lot.available_cents
        )
        for lot in lots
    )
    tokens = source_tokens(sources)
    selected_tokens = tokens[:wanted]
    counts = Counter(identifier for identifier, _ in selected_tokens)
    expected = [
        (
            source.transaction_id,
            counts[source.transaction_id],
            source.available_cents - counts[source.transaction_id],
        )
        for source in sorted(sources, key=lambda item: (item.occurred_at, item.transaction_id))
        if counts[source.transaction_id]
    ]
    assert len(set(selected_tokens)) == wanted
    assert len(tokens) == sum(available)
    before = [lot.model_dump(mode="json") for lot in lots]
    result = plan_goal_allocation(GOAL, VERSION, snapshot, versions, positions, [], lots)
    assert result.status == "READY"
    assert result.suggested_cents == result.max_safe_cents == wanted
    assert result.eligible_new_funds_cents == sum(available)
    assert [
        (item.origin_transaction_id, item.amount_cents, item.remaining_available_cents)
        for item in result.lot_allocations
    ] == expected
    assert all(item.account_id == SOURCE for item in result.lot_allocations)
    assert [lot.model_dump(mode="json") for lot in lots] == before
    reordered = snapshot.model_copy(
        update={
            "cash_accounts": list(reversed(snapshot.cash_accounts)),
            "goals": list(reversed(snapshot.goals)),
        }
    )
    replay = plan_goal_allocation(
        GOAL,
        VERSION,
        reordered,
        list(reversed(versions)),
        positions,
        [],
        [lots[index] for index in order],
    )
    assert result.model_dump(mode="json") == replay.model_dump(mode="json")


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    original=st.integers(1, 100),
    available_fraction=st.integers(1, 100),
    relative_minutes=st.integers(-3, 3),
    same_account=st.booleans(),
)
def test_current_version_cutoff_does_not_inherit_earlier_income(
    original: int, available_fraction: int, relative_minutes: int, same_account: bool
) -> None:
    available = min(original, available_fraction)
    snapshot, versions, positions = goal_context(
        source_cash=200,
        goal_cash=20,
        monthly=(0, 100, 150),
        contributed=0,
        total_target=500,
        same_account=same_account,
    )
    current_version_start = NOW - timedelta(minutes=30)
    versions[0] = versions[0].model_copy(
        update={"confirmed_at": current_version_start, "valid_from": current_version_start}
    )
    lot = income_lot(
        original,
        available,
        occurred_at=current_version_start + timedelta(minutes=relative_minutes),
    )
    result = plan_goal_allocation(GOAL, VERSION, snapshot, versions, positions, [], [lot])
    expected = available if relative_minutes >= 0 else 0
    assert result.status == "READY"
    assert result.eligible_new_funds_cents == expected
    assert result.suggested_cents == result.max_safe_cents == expected
    assert sum(item.amount_cents for item in result.lot_allocations) == expected


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    target=st.integers(1, 30),
    carried_available=st.integers(1, 40),
    old_cash=st.integers(0, 15),
    same_account=st.booleans(),
)
def test_month_rollover_resets_month_total_without_rebuilding_source_balance(
    target: int, carried_available: int, old_cash: int, same_account: bool
) -> None:
    october = datetime(2026, 10, 30, 16, tzinfo=UTC)  # Shanghai October 31.
    november = datetime(2026, 10, 31, 16, tzinfo=UTC)  # Shanghai November 1.
    occurred = datetime(2026, 10, 30, 12, tzinfo=UTC)
    original_salary = target + carried_available
    source_cash = carried_available + old_cash + 5
    lot = income_lot(original_salary, carried_available, occurred_at=occurred)

    def context(
        when: datetime, cash: int, owned: int, month_contribution: int
    ) -> tuple[BoundarySnapshot, list[BoundaryPolicyVersion], list[BoundaryPosition]]:
        return goal_context(
            source_cash=cash,
            goal_cash=owned,
            monthly=(0, target, target + 10),
            contributed=month_contribution,
            total_target=target * 4 + carried_available + 50,
            floor=5,
            other_goal_cash=30,
            same_account=same_account,
            as_of=when,
            one_month=False,
        )

    october_snapshot, versions, positions = context(october, source_cash, target, target)
    last_month = plan_goal_allocation(
        GOAL, VERSION, october_snapshot, versions, positions, [], [lot]
    )
    assert last_month.status == "READY"
    assert last_month.suggested_cents == 0
    assert last_month.eligible_new_funds_cents == carried_available

    november_snapshot, versions, positions = context(november, source_cash, target, 0)
    next_month = plan_goal_allocation(
        GOAL, VERSION, november_snapshot, versions, positions, [], [lot]
    )
    expected = min(target, carried_available)
    assert next_month.status == "READY"
    assert next_month.remaining_target_cents == target
    assert next_month.eligible_new_funds_cents == carried_available
    assert next_month.suggested_cents == expected
    assert next_month.lot_allocations[0].origin_transaction_id == lot.origin_transaction_id
    assert next_month.lot_allocations[0].remaining_available_cents == carried_available - expected

    # Explicit post-receipt fixture, not a claim that the preview executed a
    # transfer: 301 will have to atomically establish all of these facts.
    remaining_lot = income_lot(original_salary, carried_available - expected, occurred_at=occurred)
    after_snapshot, versions, positions = context(
        november, source_cash - expected, target + expected, expected
    )
    after_receipt = plan_goal_allocation(
        GOAL, VERSION, after_snapshot, versions, positions, [], [remaining_lot]
    )
    assert after_receipt.status == "READY"
    assert after_receipt.suggested_cents == 0
    assert after_receipt.eligible_new_funds_cents == carried_available - expected
    assert after_receipt.lot_allocations == []
    before_tokens = source_tokens(
        (SourceCents(lot.origin_transaction_id, occurred, original_salary, carried_available),)
    )
    after_tokens = source_tokens(
        (
            SourceCents(
                lot.origin_transaction_id, occurred, original_salary, carried_available - expected
            ),
        )
    )
    assert set(before_tokens[:expected]).isdisjoint(after_tokens)
    assert before_tokens[expected:] == after_tokens


@pytest.mark.parametrize(
    "same_account,target_is_cash", [(False, False), (False, True), (True, False)]
)
def test_total_target_completion_caps_minimum_for_all_supported_cash_routes(
    same_account: bool, target_is_cash: bool
) -> None:
    snapshot, versions, positions = goal_context(
        source_cash=300_000,
        goal_cash=980_000,
        monthly=(50_000, 100_000, 150_000),
        contributed=0,
        total_target=1_000_000,
        floor=280_000,
        other_goal_cash=1_000_000,
        same_account=same_account,
        target_is_cash=target_is_cash,
    )
    result = plan_goal_allocation(
        GOAL, VERSION, snapshot, versions, positions, [], [income_lot(100_000)]
    )
    assert result.status == "READY"
    assert result.remaining_min_cents == 20_000
    assert result.remaining_target_cents == 20_000
    assert result.remaining_max_cents == 20_000
    assert result.max_safe_cents == result.suggested_cents == 20_000
    assert result.minimum_shortfall_cents == 0
    assert result.baseline_boundary.safe_idle_cents == 0
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.safe_idle_cents == 0
    assert result.candidate_boundary.calculation_trace[0].cash_cents == 2_280_000
    assert result.candidate_boundary.protected_cents_by_reason["goal_cash"] == 2_000_000
    assert result.lot_allocations[0].remaining_available_cents == 80_000


def test_other_goal_cash_principal_and_uncompleted_minimum_cannot_fund_selected_goal() -> None:
    snapshot, versions, positions = goal_context(
        source_cash=150,
        goal_cash=20,
        monthly=(50, 100, 150),
        contributed=0,
        total_target=200,
        floor=30,
        other_goal_cash=500,
    )
    other = snapshot.goals[1].model_copy(
        update={"principal_owned_cents": 1_000, "allocated_cents": 1_500}
    )
    snapshot = snapshot.model_copy(
        update={
            "goals": [snapshot.goals[0], other],
            "goal_month_contributions": [
                *snapshot.goal_month_contributions,
                GoalMonthFact(goal_id=OTHER_GOAL, period="2026-10", contributed_cents=0),
            ],
        }
    )
    positions.append(
        BoundaryPosition(
            position_id=UUID(int=31),
            goal_id=OTHER_GOAL,
            principal_cents=1_000,
            status="HELD",
        )
    )
    versions.append(
        policy(
            {
                "type": "goal_saving",
                "target_cents": 2_000,
                "deadline": "2026-10-31",
                "monthly_contribution": {"min_cents": 70, "target_cents": 70, "max_cents": 70},
            },
            identifier=UUID(int=22),
            version=UUID(int=25),
            until=datetime(2026, 10, 31, 16, tzinfo=UTC),
        )
    )
    before = snapshot.model_dump(mode="json")
    result = plan_goal_allocation(
        GOAL, VERSION, snapshot, versions, positions, [], [income_lot(100)]
    )
    assert result.status == "READY"
    assert result.max_safe_cents == result.suggested_cents == 50
    assert result.baseline_boundary.safe_idle_cents == 0
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.safe_idle_cents == 0
    assert result.candidate_boundary.protected_cents_by_reason["goal_minimum"] == 70
    assert result.candidate_boundary.protected_cents_by_reason["goal_cash"] == 570
    assert result.candidate_boundary.calculation_trace[0].cash_cents == 670
    assert result.lot_allocations[0].remaining_available_cents == 50
    assert snapshot.model_dump(mode="json") == before
