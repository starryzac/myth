"""Independent cent-ledger checks at the public MVP-202 boundary seam.

The oracle consumes explicit test events, never production DTOs, traces or helpers.
Its scope is deliberately a small finite ledger, not a second policy engine.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
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
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

AS_OF = datetime(2026, 10, 3, 16, tzinfo=UTC)
TODAY = date(2026, 10, 4)
CASH_ACCOUNT = UUID(int=1)
CARD_ACCOUNT = UUID(int=2)
GOAL_ACCOUNT = UUID(int=3)
GOAL = UUID(int=50)
PROPERTY_SETTINGS = settings(max_examples=200, derandomize=True, database=None, deadline=None)


def cash_fact(amount: int, account: UUID = CASH_ACCOUNT) -> CashFact:
    return CashFact(
        account_id=account,
        account_type="CASH" if account != CARD_ACCOUNT else "CREDIT_CARD",
        balance_cents=amount,
        observed_at=AS_OF,
    )


def floor_policy(amount: int, *, identity: int = 10, start_day: int = 0) -> BoundaryPolicyVersion:
    configuration: dict[str, Any] = validate_configuration(
        {"type": "emergency_buffer", "amount_cents": amount}
    )
    return BoundaryPolicyVersion(
        policy_id=UUID(int=identity),
        version_id=UUID(int=identity + 100),
        configuration=configuration,
        content_hash=configuration_hash(configuration),
        confirmed_at=AS_OF - timedelta(seconds=1),
        valid_from=AS_OF + timedelta(days=start_day),
    )


def product(identity: int, return_day: int | None) -> BoundaryProduct:
    return BoundaryProduct(
        product_id=UUID(int=identity),
        version_number=1,
        asset_class="CASH_MGMT_T0" if return_day is None else "FIXED_DEPOSIT",
        fixed_return=None
        if return_day is None
        else FixedReturnTerms(term_days=return_day, settlement_delay_days=0),
        terms_digest=f"independent-fixture-{identity}-{return_day}",
    )


def bill(amount: int, *, paid: int = 0, day: int = 1, identity: int = 20) -> BillFact:
    return BillFact(
        bill_id=UUID(int=identity),
        account_id=CARD_ACCOUNT,
        statement_date=TODAY - timedelta(days=1),
        due_date=TODAY + timedelta(days=day),
        total_cents=amount,
        paid_cents=paid,
        status="PAID" if paid == amount else "UNPAID" if paid == 0 else "PARTIALLY_PAID",
    )


def snapshot(cash: int, *, bills: list[BillFact] | None = None) -> BoundarySnapshot:
    return BoundarySnapshot(
        as_of=AS_OF,
        timezone="Asia/Shanghai",
        cash_accounts=[cash_fact(cash), cash_fact(0, CARD_ACCOUNT)],
        bills=[] if bills is None else bills,
    )


def goal_policy(monthly_minimum: int) -> BoundaryPolicyVersion:
    configuration: dict[str, Any] = validate_configuration(
        {
            "type": "goal_saving",
            "target_cents": 1_000,
            "deadline": "2026-12-31",
            "monthly_contribution": {
                "min_cents": monthly_minimum,
                "target_cents": monthly_minimum,
                "max_cents": monthly_minimum,
            },
        }
    )
    return BoundaryPolicyVersion(
        policy_id=UUID(int=12),
        version_id=UUID(int=112),
        configuration=configuration,
        content_hash=configuration_hash(configuration),
        confirmed_at=AS_OF - timedelta(seconds=1),
        valid_from=AS_OF,
    )


def owned_snapshot(
    general_cash: int, goal_cash: int, principal: int, *, contributed: int = 0
) -> BoundarySnapshot:
    return BoundarySnapshot(
        as_of=AS_OF,
        timezone="Asia/Shanghai",
        cash_accounts=[
            cash_fact(general_cash),
            CashFact(
                account_id=GOAL_ACCOUNT,
                account_type="GOAL",
                balance_cents=goal_cash,
                observed_at=AS_OF,
            ),
        ],
        goals=[
            GoalOwnership(
                goal_id=GOAL,
                policy_id=UUID(int=12),
                account_id=GOAL_ACCOUNT,
                cash_owned_cents=goal_cash,
                principal_owned_cents=principal,
                allocated_cents=goal_cash + principal,
            )
        ],
        goal_month_contributions=[
            GoalMonthFact(goal_id=GOAL, period="2026-10", contributed_cents=contributed)
        ],
    )


@dataclass(frozen=True)
class LedgerEvent:
    day: int
    phase: int
    label: str
    cash_delta: int = 0
    protected_delta: int = 0


def cent_ledger_margins(
    *,
    cash: int,
    protected: int,
    events: tuple[LedgerEvent, ...] = (),
    purchase: int = 0,
    return_day: int | None = None,
) -> tuple[tuple[str, int], ...]:
    """Inspect every ledger mutation, including immediately after a purchase.

    Phase 0 establishes the day's protection; phase 1 pays obligations; phase 2
    credits principal. An unconditional candidate maturity uses phase 2 too.
    Between events both balances are constant, so these checkpoints cover all
    unchanged dates without implementing the production 91-day projection.
    """
    cash -= purchase
    checkpoints = [("immediate purchase", cash - protected)]
    scheduled = list(events)
    if return_day is not None and return_day <= 90:
        scheduled.append(LedgerEvent(return_day, 2, "candidate principal", purchase))
    for event in sorted(scheduled, key=lambda item: (item.day, item.phase, item.label)):
        cash += event.cash_delta
        protected += event.protected_delta
        checkpoints.append((event.label, cash - protected))
    return tuple(checkpoints)


def exhaustive_cent_cap(
    *,
    cash: int,
    protected: int,
    events: tuple[LedgerEvent, ...],
    return_day: int | None = None,
) -> int:
    """Try each cent in a small fixture; do not algebraically copy engine caps."""
    safe = [
        amount
        for amount in range(cash + 1)
        if all(
            margin >= 0
            for _, margin in cent_ledger_margins(
                cash=cash,
                protected=protected,
                events=events,
                purchase=amount,
                return_day=return_day,
            )
        )
    ]
    return max(safe, default=0)


def test_independent_ledger_retains_payment_and_goal_principal_conservation() -> None:
    # ADR 0005's literal 1,000,000-cent example; these are accounting entries,
    # not values obtained from a boundary result.
    assert cent_ledger_margins(
        cash=1_000_000,
        protected=750_000,
        events=(
            LedgerEvent(1, 1, "protected rent", -200_000, -200_000),
            LedgerEvent(2, 2, "owned goal principal", 80_000, 80_000),
            LedgerEvent(3, 1, "actual living consumption", -70_000),
        ),
    ) == (
        ("immediate purchase", 250_000),
        ("protected rent", 250_000),
        ("owned goal principal", 250_000),
        ("actual living consumption", 180_000),
    )


def test_independent_ledger_does_not_use_same_day_principal_before_payment() -> None:
    assert cent_ledger_margins(
        cash=200_000,
        protected=170_000,
        purchase=40_000,
        events=(
            LedgerEvent(0, 2, "late principal", 100_000),
            LedgerEvent(0, 1, "protected bill", -150_000, -150_000),
        ),
    ) == (
        ("immediate purchase", -10_000),
        ("protected bill", -10_000),
        ("late principal", 90_000),
    )


def test_independent_small_ledger_exhausts_caps_for_different_maturities() -> None:
    # Cent-scaled version of ADR 0005's independent product example.
    events = (LedgerEvent(20, 0, "larger protected floor", protected_delta=10),)
    assert exhaustive_cent_cap(cash=20, protected=5, events=events) == 5
    assert exhaustive_cent_cap(cash=20, protected=5, events=events, return_day=7) == 15
    assert exhaustive_cent_cap(cash=20, protected=5, events=events, return_day=20) == 5
    assert exhaustive_cent_cap(cash=20, protected=5, events=events, return_day=30) == 5


def test_public_boundary_matches_independent_product_and_same_day_literals() -> None:
    from app.domain.boundary import compute_boundary

    result = compute_boundary(
        snapshot(200_000),
        [floor_policy(50_000), floor_policy(100_000, identity=11, start_day=20)],
        [],
        [product(30, None), product(31, 7), product(32, 20), product(33, 30)],
    )
    assert result.status == "READY"
    assert result.financial_only is True
    assert result.safe_idle_cents == 50_000
    assert result.minimum_margin_cents == 50_000
    assert result.max_allocatable_by_product == {
        str(UUID(int=30)): 50_000,
        str(UUID(int=31)): 150_000,
        str(UUID(int=32)): 50_000,
        str(UUID(int=33)): 50_000,
    }

    # Principal available later today cannot rescue the immediate purchase.
    same_day = compute_boundary(
        snapshot(200_000, bills=[bill(150_000, day=0)]),
        [floor_policy(20_000)],
        [
            BoundaryPosition(
                position_id=UUID(int=40),
                principal_cents=100_000,
                status="MATURED",
                principal_available_at=AS_OF + timedelta(hours=1),
                availability_evidence_ids=[UUID(int=41)],
            )
        ],
        [product(30, None), product(31, 0)],
    )
    assert same_day.status == "READY"
    assert same_day.safe_idle_cents == 30_000
    assert set(same_day.max_allocatable_by_product.values()) == {30_000}


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    debt=st.integers(min_value=1, max_value=30),
    floor=st.integers(min_value=1, max_value=20),
    increase=st.integers(min_value=1, max_value=20),
    free=st.integers(min_value=1, max_value=30),
    bill_day=st.integers(min_value=0, max_value=90),
    increase_day=st.integers(min_value=1, max_value=90),
    return_day=st.integers(min_value=0, max_value=95),
    owned_principal=st.integers(min_value=1, max_value=20),
    principal_day=st.integers(min_value=0, max_value=90),
)
def test_positive_financial_caps_and_one_cent_excess_against_exhaustive_ledger(
    debt: int,
    floor: int,
    increase: int,
    free: int,
    bill_day: int,
    increase_day: int,
    return_day: int,
    owned_principal: int,
    principal_day: int,
) -> None:
    from app.domain.boundary import compute_boundary

    initial_cash = debt + floor + increase + free
    events = (
        LedgerEvent(bill_day, 1, "pay protected bill", -debt, -debt),
        LedgerEvent(increase_day, 0, "new floor", protected_delta=increase),
        LedgerEvent(principal_day, 2, "existing principal", owned_principal),
    )
    candidates = [product(30, None), product(31, return_day)]
    result = compute_boundary(
        snapshot(initial_cash, bills=[bill(debt, day=bill_day)]),
        [floor_policy(floor), floor_policy(increase, identity=11, start_day=increase_day)],
        [
            BoundaryPosition(
                position_id=UUID(int=40),
                principal_cents=owned_principal,
                status="HELD",
                principal_available_at=AS_OF + timedelta(days=principal_day, hours=1),
                availability_evidence_ids=[UUID(int=41)],
            )
        ],
        candidates,
    )
    assert result.status == "READY"
    assert result.safe_idle_cents is not None and result.safe_idle_cents > 0
    assert result.safe_idle_cents == exhaustive_cent_cap(
        cash=initial_cash, protected=debt + floor, events=events
    )
    for candidate, maturity in zip(candidates, (None, return_day), strict=True):
        actual = result.max_allocatable_by_product[str(candidate.product_id)]
        assert actual is not None and actual > 0
        assert actual == exhaustive_cent_cap(
            cash=initial_cash, protected=debt + floor, events=events, return_day=maturity
        )
        at_cap = cent_ledger_margins(
            cash=initial_cash,
            protected=debt + floor,
            events=events,
            purchase=actual,
            return_day=maturity,
        )
        over_cap = cent_ledger_margins(
            cash=initial_cash,
            protected=debt + floor,
            events=events,
            purchase=actual + 1,
            return_day=maturity,
        )
        assert all(margin >= 0 for _, margin in at_cap)
        assert any(margin < 0 for _, margin in over_cap)


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    cash=st.integers(min_value=1, max_value=200),
    protected=st.integers(min_value=0, max_value=100),
    additional=st.integers(min_value=1, max_value=100),
    starts_on=st.integers(min_value=0, max_value=90),
    returns_on=st.integers(min_value=0, max_value=95),
)
def test_increasing_protection_never_increases_financial_caps(
    cash: int, protected: int, additional: int, starts_on: int, returns_on: int
) -> None:
    from app.domain.boundary import compute_boundary

    products = [product(30, None), product(31, returns_on)]
    before = compute_boundary(snapshot(cash), [floor_policy(protected)], [], products)
    after = compute_boundary(
        snapshot(cash),
        [floor_policy(protected), floor_policy(additional, identity=11, start_day=starts_on)],
        [],
        products,
    )
    assert before.safe_idle_cents is not None and after.safe_idle_cents is not None
    assert after.safe_idle_cents <= before.safe_idle_cents
    assert before.minimum_margin_cents == cash - protected
    assert after.minimum_margin_cents == cash - protected - additional
    assert after.deficit_cents == max(0, protected + additional - cash)
    if cash < protected + additional:
        assert after.status == "LIQUIDITY_RISK"
    for candidate in products:
        earlier = before.max_allocatable_by_product[str(candidate.product_id)]
        later = after.max_allocatable_by_product[str(candidate.product_id)]
        assert earlier is not None and later is not None
        assert later <= earlier


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    paid=st.integers(min_value=1, max_value=100),
    unpaid=st.integers(min_value=0, max_value=100),
    floor=st.integers(min_value=0, max_value=100),
    free=st.integers(min_value=1, max_value=100),
    due_on=st.integers(min_value=0, max_value=90),
)
def test_paying_a_protected_bill_reduces_cash_and_debt_together(
    paid: int, unpaid: int, floor: int, free: int, due_on: int
) -> None:
    from app.domain.boundary import compute_boundary

    debt = paid + unpaid
    initial_cash = debt + floor + free
    products = [product(30, None), product(31, 7)]
    before = compute_boundary(
        snapshot(initial_cash, bills=[bill(debt, day=due_on)]),
        [floor_policy(floor)],
        [],
        products,
    )
    after = compute_boundary(
        snapshot(initial_cash - paid, bills=[bill(debt, paid=paid, day=due_on)]),
        [floor_policy(floor)],
        [],
        products,
    )
    assert before.status == after.status == "READY"
    assert before.safe_idle_cents == after.safe_idle_cents == free
    assert before.minimum_margin_cents == after.minimum_margin_cents == free
    assert before.max_allocatable_by_product == after.max_allocatable_by_product
    assert set(after.max_allocatable_by_product.values()) == {free}


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    goal_cash=st.integers(min_value=0, max_value=100),
    principal=st.integers(min_value=1, max_value=100),
    floor=st.integers(min_value=0, max_value=100),
    free=st.integers(min_value=1, max_value=100),
    arrives_on=st.integers(min_value=0, max_value=90),
)
def test_owned_goal_principal_becoming_cash_never_enlarges_general_idle(
    goal_cash: int, principal: int, floor: int, free: int, arrives_on: int
) -> None:
    from app.domain.boundary import compute_boundary

    policies = [floor_policy(floor), goal_policy(0)]
    products = [product(30, None), product(31, 7)]
    before = compute_boundary(
        owned_snapshot(floor + free, goal_cash, principal),
        policies,
        [
            BoundaryPosition(
                position_id=UUID(int=40),
                goal_id=GOAL,
                principal_cents=principal,
                status="HELD",
                principal_available_at=AS_OF + timedelta(days=arrives_on, hours=1),
                availability_evidence_ids=[UUID(int=41)],
            )
        ],
        products,
    )
    already_received = compute_boundary(
        owned_snapshot(floor + free, goal_cash + principal, 0), policies, [], products
    )
    assert before.status == already_received.status == "READY"
    assert before.safe_idle_cents == already_received.safe_idle_cents == free
    assert before.minimum_margin_cents == already_received.minimum_margin_cents == free
    assert before.max_allocatable_by_product == already_received.max_allocatable_by_product
    assert set(before.max_allocatable_by_product.values()) == {free}


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    previously_contributed=st.integers(min_value=0, max_value=30),
    contribution=st.integers(min_value=1, max_value=30),
    still_due_this_month=st.integers(min_value=0, max_value=30),
    floor=st.integers(min_value=0, max_value=50),
    free=st.integers(min_value=1, max_value=50),
)
def test_real_goal_contribution_moves_ownership_without_double_protection(
    previously_contributed: int,
    contribution: int,
    still_due_this_month: int,
    floor: int,
    free: int,
) -> None:
    from app.domain.boundary import compute_boundary

    # Three explicitly committed months: October, November, December. Target
    # 1,000 is above every generated total, so only the monthly floor is binding.
    monthly = previously_contributed + contribution + still_due_this_month
    goal_cash = previously_contributed + 20
    unpaid_minimum = contribution + still_due_this_month + monthly + monthly
    general_cash = floor + unpaid_minimum + free
    policies = [floor_policy(floor), goal_policy(monthly)]
    products = [product(30, None), product(31, 7)]
    before = compute_boundary(
        owned_snapshot(general_cash, goal_cash, 0, contributed=previously_contributed),
        policies,
        [],
        products,
    )
    after = compute_boundary(
        owned_snapshot(
            general_cash - contribution,
            goal_cash + contribution,
            0,
            contributed=previously_contributed + contribution,
        ),
        policies,
        [],
        products,
    )
    assert before.status == after.status == "READY"
    assert before.safe_idle_cents == after.safe_idle_cents == free
    assert before.minimum_margin_cents == after.minimum_margin_cents == free
    assert before.max_allocatable_by_product == after.max_allocatable_by_product
    assert set(after.max_allocatable_by_product.values()) == {free}


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    value=st.integers(min_value=1, max_value=100),
    order=st.permutations((0, 1, 2)),
    reverse_facts=st.booleans(),
)
def test_equivalent_input_permutations_preserve_complete_financial_result_and_hash(
    value: int, order: tuple[int, ...], reverse_facts: bool
) -> None:
    from app.domain.boundary import compute_boundary

    evidence = [UUID(int=80), UUID(int=81)]
    original = owned_snapshot(1_000 + value, 10, 5)
    original = original.model_copy(
        update={
            "cash_accounts": [
                *original.cash_accounts,
                cash_fact(0, CARD_ACCOUNT),
                CashFact(
                    account_id=UUID(int=5),
                    account_type="GOAL",
                    balance_cents=7,
                    observed_at=AS_OF,
                ),
            ],
            "bills": [bill(value, identity=20, day=1), bill(10, identity=21, day=20)],
            "goals": [
                *original.goals,
                GoalOwnership(
                    goal_id=UUID(int=51),
                    policy_id=UUID(int=13),
                    account_id=UUID(int=5),
                    cash_owned_cents=7,
                    principal_owned_cents=0,
                    allocated_cents=7,
                ),
            ],
            "goal_month_contributions": [
                *original.goal_month_contributions,
                GoalMonthFact(goal_id=UUID(int=51), period="2026-10", contributed_cents=0),
            ],
        }
    )
    policies = [floor_policy(20), floor_policy(10, identity=11, start_day=20), goal_policy(0)]
    policies = [policy.model_copy(update={"evidence_ids": evidence}) for policy in policies]
    positions = [
        BoundaryPosition(
            position_id=UUID(int=40),
            principal_cents=4,
            status="HELD",
            principal_available_at=AS_OF + timedelta(days=1),
            evidence_ids=evidence,
            availability_evidence_ids=evidence,
        ),
        BoundaryPosition(
            position_id=UUID(int=42),
            goal_id=GOAL,
            principal_cents=5,
            status="HELD",
            principal_available_at=AS_OF + timedelta(days=20),
            evidence_ids=evidence,
            availability_evidence_ids=evidence,
        ),
    ]
    products = [product(30, None), product(31, 7), product(32, 30)]
    before = compute_boundary(original, policies, positions, products)
    assert before.status == "READY" and before.safe_idle_cents is not None
    assert before.safe_idle_cents > 0

    reordered = original.model_copy(
        update={
            "cash_accounts": list(reversed(original.cash_accounts)),
            "bills": list(reversed(original.bills)),
            "goals": list(reversed(original.goals)),
            "goal_month_contributions": list(reversed(original.goal_month_contributions)),
        }
    )
    reordered_policies = [policies[index] for index in order]
    reordered_positions = list(reversed(positions))
    if reverse_facts:
        reordered_policies = [
            policy.model_copy(update={"evidence_ids": list(reversed(policy.evidence_ids))})
            for policy in reordered_policies
        ]
        reordered_positions = [
            position.model_copy(
                update={
                    "evidence_ids": list(reversed(position.evidence_ids)),
                    "availability_evidence_ids": list(reversed(position.availability_evidence_ids)),
                }
            )
            for position in reordered_positions
        ]
    after = compute_boundary(
        reordered, reordered_policies, reordered_positions, [products[index] for index in order]
    )
    assert before.model_dump(mode="json") == after.model_dump(mode="json")


@pytest.mark.property
@PROPERTY_SETTINGS
@given(
    amount=st.integers(min_value=0, max_value=9_223_372_036_854_775_807),
    field=st.sampled_from(
        [
            "future_income_cents",
            "predicted_refund_cents",
            "future_interest_cents",
            "display_yield_cents",
        ]
    ),
)
def test_financial_dto_rejects_future_source_income_and_uncredited_yield(
    amount: int, field: str
) -> None:
    # Source projection/hash invariance belongs to the service integration seam.
    # This test proves the financial seam cannot even accept those source fields;
    # it deliberately does not build a fake source projector inside the test.
    with pytest.raises(ValidationError) as error:
        BoundarySnapshot.model_validate({**snapshot(100).model_dump(), field: amount})
    assert error.value.errors()[0]["type"] == "extra_forbidden"
    assert error.value.errors()[0]["loc"] == (field,)

    position = BoundaryPosition(position_id=UUID(int=40), principal_cents=10, status="HELD")
    with pytest.raises(ValidationError) as position_error:
        BoundaryPosition.model_validate({**position.model_dump(), "accrued_yield_cents": amount})
    assert position_error.value.errors()[0]["type"] == "extra_forbidden"

    with pytest.raises(ValidationError) as product_error:
        BoundaryProduct.model_validate(
            {**product(30, None).model_dump(), "annual_yield_bps": amount}
        )
    assert product_error.value.errors()[0]["type"] == "extra_forbidden"
