"""Independent integer ledger and ACT/365 oracle for one-product previews."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import pytest
from app.domain.asset_allocation import AssetProductTerms, select_asset
from app.domain.asset_exposure import AssetExposure
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundarySnapshot,
    CashFact,
    GoalMonthFact,
    GoalOwnership,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from hypothesis import given, settings
from hypothesis import strategies as st

NOW = datetime(2026, 10, 3, 16, tzinfo=UTC)
START = NOW - timedelta(days=3)
CASH = UUID(int=1)
GOAL_ACCOUNT = UUID(int=2)
OTHER_ACCOUNT = UUID(int=3)
GOAL = UUID(int=10)
OTHER_GOAL = UUID(int=11)
ASSET_POLICY = UUID(int=20)
ASSET_VERSION = UUID(int=21)
GOAL_POLICY = UUID(int=22)
GOAL_VERSION = UUID(int=23)
PROPERTY_SETTINGS = settings(max_examples=200, derandomize=True, database=None, deadline=None)


def configured_policy(
    config: dict[str, Any],
    policy_id: UUID,
    version_id: UUID,
    *,
    valid_from: datetime = START,
    valid_until: datetime | None = None,
) -> BoundaryPolicyVersion:
    canonical = validate_configuration(config)
    return BoundaryPolicyVersion(
        policy_id=policy_id,
        version_id=version_id,
        configuration=canonical,
        content_hash=configuration_hash(canonical),
        confirmed_at=START,
        valid_from=valid_from,
        valid_until=valid_until,
    )


def product_terms(product: "LiteralProduct", identifier: int = 100) -> AssetProductTerms:
    fixed = product.fixed_term_days is not None
    rule: dict[str, Any] = {
        "protocol": "fixed-principal-return-v1" if fixed else "planned-principal-return-v1",
        "day_basis": "CALENDAR",
        "guaranteed": True,
        "settlement_delay_days": product.delay_days,
        "principal_return_bps": 10000,
        "rollover": False,
        "auto_rollover": False,
        "yield_rule": {
            "protocol": "simple-annual-yield-v1",
            "basis": "ACT_365",
            "annual_yield_bps": product.rate_bps,
            "simulation": True,
            "fee_cents": 0,
            "purchase_fee_bps": 0,
            "redemption_fee_bps": 0,
            "accrual": "UNTIL_MATURITY" if fixed else "UNTIL_REDEMPTION_REQUEST",
        },
    }
    if fixed:
        rule["term_days"] = product.fixed_term_days
    return AssetProductTerms(
        product_id=UUID(int=identifier),
        product_code=product.name,
        version_number=2,
        asset_class="FIXED_DEPOSIT"
        if fixed
        else ("CASH_MGMT_T1" if product.delay_days else "CASH_MGMT_T0"),
        risk_level=0,
        principal_fluctuation=False,
        minimum_purchase_cents=product.minimum_cents,
        lock_days=product.lock_days,
        redemption_delay_days=product.delay_days,
        annual_yield_bps=product.rate_bps,
        early_withdrawal_loss_bps=20 if fixed else 0,
        auto_purchase_allowed=True,
        auto_redeem_allowed=not fixed,
        created_at=START,
        effective_from=START,
        maturity_rule=rule,
        terms_digest=configuration_hash(rule),
    )


def asset_context(
    *,
    scope: Literal["general_idle_funds", "goal"] = "general_idle_funds",
    source_cash: int,
    goal_cash: int = 0,
    other_goal_cash: int = 0,
    initial_floor: int = 0,
    later_floor: int | None = None,
    change_day: int = 2,
    managed_limit: int = 1_000_000,
    single_cap: int = 1_000_000,
    occupied: int = 0,
    expiry_day: int | None = None,
    window: int = 90,
) -> tuple[
    BoundarySnapshot,
    list[BoundaryPolicyVersion],
    list[BoundaryPosition],
    BoundaryPolicyVersion,
    AssetExposure,
]:
    config: dict[str, Any] = {
        "type": "asset_authorization",
        "scope": scope,
        "allowed_asset_classes": ["CASH", "CASH_MGMT_T0", "CASH_MGMT_T1", "FIXED_DEPOSIT"],
        "max_auto_managed_cents": managed_limit,
        "single_action_cap_cents": single_cap,
        "max_redemption_delay_days": 90,
        "max_lock_days": 90,
        "max_principal_risk_level": 0,
        "allow_auto_recovery_without_penalty": True,
        "allow_early_withdrawal_with_penalty": False,
    }
    if scope == "goal":
        config["goal_id"] = str(GOAL)
    auth = configured_policy(
        config,
        ASSET_POLICY,
        ASSET_VERSION,
        valid_until=NOW + timedelta(days=expiry_day) if expiry_day else None,
    )
    goal_config: dict[str, Any] = {
        "type": "goal_saving",
        "target_cents": max(1, goal_cash + occupied + 100),
        "deadline": (NOW + timedelta(hours=8, days=window + 1)).date().isoformat(),
        "monthly_contribution": {"min_cents": 0, "target_cents": 0, "max_cents": 0},
        "asset_policy_id": str(ASSET_POLICY) if scope == "goal" else None,
    }
    policies = [auth, configured_policy(goal_config, GOAL_POLICY, GOAL_VERSION)]
    policies.append(
        configured_policy(
            {"type": "emergency_buffer", "amount_cents": initial_floor},
            UUID(int=30),
            UUID(int=31),
            valid_until=NOW + timedelta(days=change_day) if later_floor is not None else None,
        )
    )
    if later_floor is not None:
        policies.append(
            configured_policy(
                {"type": "emergency_buffer", "amount_cents": later_floor},
                UUID(int=32),
                UUID(int=33),
                valid_from=NOW + timedelta(days=change_day),
            )
        )
    snapshot = BoundarySnapshot(
        as_of=NOW,
        timezone="Asia/Shanghai",
        cash_accounts=[
            CashFact(
                account_id=CASH, account_type="CASH", balance_cents=source_cash, observed_at=NOW
            ),
            CashFact(
                account_id=GOAL_ACCOUNT,
                account_type="GOAL",
                balance_cents=goal_cash,
                observed_at=NOW,
            ),
            CashFact(
                account_id=OTHER_ACCOUNT,
                account_type="GOAL",
                balance_cents=other_goal_cash,
                observed_at=NOW,
            ),
        ],
        goals=[
            GoalOwnership(
                goal_id=GOAL,
                policy_id=GOAL_POLICY,
                account_id=GOAL_ACCOUNT,
                cash_owned_cents=goal_cash,
                principal_owned_cents=occupied if scope == "goal" else 0,
                allocated_cents=goal_cash + (occupied if scope == "goal" else 0),
            ),
            GoalOwnership(
                goal_id=OTHER_GOAL,
                policy_id=UUID(int=24),
                account_id=OTHER_ACCOUNT,
                cash_owned_cents=other_goal_cash,
                principal_owned_cents=0,
                allocated_cents=other_goal_cash,
            ),
        ],
        goal_month_contributions=[
            GoalMonthFact(goal_id=GOAL, period="2026-10", contributed_cents=0)
        ],
    )
    positions = (
        [
            BoundaryPosition(
                position_id=UUID(int=50),
                goal_id=GOAL if scope == "goal" else None,
                principal_cents=occupied,
                status="HELD",
            )
        ]
        if occupied
        else []
    )
    exposure = AssetExposure(
        as_of=NOW,
        scope=scope,
        goal_id=GOAL if scope == "goal" else None,
        managed_principal_cents=occupied,
        pending_purchase_cents=0,
        counted_position_ids=[p.position_id for p in positions],
    )
    return snapshot, policies, positions, auth, exposure


@dataclass(frozen=True)
class LiteralProduct:
    name: str
    rate_bps: int
    delay_days: int
    lock_days: int = 0
    fixed_term_days: int | None = None
    minimum_cents: int = 1


@dataclass(frozen=True)
class LiteralExit:
    earning_days: int
    available_day: int


@dataclass(frozen=True)
class LiteralLedger:
    """A fixed 90-day cash fixture; only one future floor change is supported."""

    cash_cents: int
    selected_goal_cash: int
    other_goal_cash: int
    initial_floor: int
    later_floor: int
    floor_change_day: int


def literal_exit(
    product: LiteralProduct, window: int, authorization_expiry_day: int | None = None
) -> LiteralExit | None:
    """One predetermined normal exit, never a search over possible exit dates."""
    if window <= 0:
        return None
    if product.fixed_term_days is not None:
        returned = product.fixed_term_days + product.delay_days
        if returned > window or product.fixed_term_days < product.lock_days:
            return None
        return LiteralExit(product.fixed_term_days, returned)
    request = window - product.delay_days
    if authorization_expiry_day is not None:
        request = min(request, authorization_expiry_day - 1)
    if request < product.lock_days or request < 0:
        return None
    return LiteralExit(request, request + product.delay_days)


def literal_yield(principal: int, rate_bps: int, earning_days: int) -> int:
    return principal * rate_bps * earning_days // 3_650_000


def literal_margins(
    ledger: LiteralLedger,
    scope: Literal["general_idle_funds", "goal"],
    amount: int,
    exit_plan: LiteralExit,
) -> tuple[int, ...]:
    """Purchase first; the return-day cash appears only after the payment phase."""
    margins: list[int] = []
    for day in range(91):
        floor = ledger.initial_floor if day < ledger.floor_change_day else ledger.later_floor
        for after_principal in (False, False, True):
            returned = day > exit_plan.available_day or (
                day == exit_plan.available_day and after_principal
            )
            cash = ledger.cash_cents - (0 if returned else amount)
            selected_cash = ledger.selected_goal_cash
            if scope == "goal" and not returned:
                selected_cash -= amount
            margins.append(cash - selected_cash - ledger.other_goal_cash - floor)
    return tuple(margins)


def literal_cap(
    ledger: LiteralLedger,
    scope: Literal["general_idle_funds", "goal"],
    product: LiteralProduct,
    exit_plan: LiteralExit,
    *,
    source_cents: int,
    managed_limit: int,
    occupied_cents: int,
    single_cap: int,
) -> int:
    """Enumerate each cent; no production financial code is used for expected."""
    feasible: list[int] = []
    for amount in range(source_cents + 1):
        if amount > single_cap or amount + occupied_cents > managed_limit:
            continue
        if scope == "goal" and amount > ledger.selected_goal_cash:
            continue
        if amount and amount < product.minimum_cents:
            continue
        if min(literal_margins(ledger, scope, amount, exit_plan)) >= 0:
            feasible.append(amount)
    return max(feasible, default=0)


@pytest.mark.parametrize(
    ("window", "expected"),
    [(90, (3698, 4389, 1808)), (5, (205, 197, None)), (6, (246, 246, None)), (7, (287, 295, None))],
)
def test_literal_act365_examples(window: int, expected: tuple[int, int, int | None]) -> None:
    products = (
        LiteralProduct("t0", 150, 0),
        LiteralProduct("t1", 180, 1),
        LiteralProduct("fixed", 220, 0, 30, 30),
    )
    actual: list[int | None] = []
    for product in products:
        plan = literal_exit(product, window)
        actual.append(
            None if plan is None else literal_yield(1_000_000, product.rate_bps, plan.earning_days)
        )
    assert tuple(actual) == expected


def test_literal_goal_purchase_preserves_ownership_even_when_general_idle_is_zero() -> None:
    ledger = LiteralLedger(100, 80, 0, 20, 20, 1)
    product = LiteralProduct("fixed", 220, 0, 30, 30)
    plan = LiteralExit(30, 30)
    assert (
        literal_cap(
            ledger,
            "general_idle_funds",
            product,
            plan,
            source_cents=20,
            managed_limit=100,
            occupied_cents=0,
            single_cap=100,
        )
        == 0
    )
    assert (
        literal_cap(
            ledger,
            "goal",
            product,
            plan,
            source_cents=80,
            managed_limit=100,
            occupied_cents=0,
            single_cap=100,
        )
        == 80
    )
    assert set(literal_margins(ledger, "goal", 80, plan)) == {0}
    # Placement does not create a contribution or increase the goal's allocated total.
    assert (80 - 80) + (0 + 80) == 80


def test_literal_future_cash_cannot_be_day_zero_returned_and_earn_for_ninety_days() -> None:
    ledger = LiteralLedger(100, 0, 0, 0, 100, 2)
    product = LiteralProduct("t0", 150, 0)
    legitimate_plan = LiteralExit(90, 90)
    assert (
        literal_cap(
            ledger,
            "general_idle_funds",
            product,
            legitimate_plan,
            source_cents=100,
            managed_limit=100,
            occupied_cents=0,
            single_cap=100,
        )
        == 0
    )
    assert min(literal_margins(ledger, "general_idle_funds", 1, legitimate_plan)) == -1
    # The erroneous immediate-return model would permit 100, but can earn no days.
    immediate_plan = LiteralExit(0, 0)
    assert (
        literal_cap(
            ledger,
            "general_idle_funds",
            product,
            immediate_plan,
            source_cents=100,
            managed_limit=100,
            occupied_cents=0,
            single_cap=100,
        )
        == 100
    )
    assert literal_yield(100, 150, immediate_plan.earning_days) == 0


def test_literal_expiring_authorization_shortens_active_exit_but_not_contract_maturity() -> None:
    assert literal_exit(LiteralProduct("t0", 150, 0), 90, 3) == LiteralExit(2, 2)
    assert literal_exit(LiteralProduct("t1", 180, 1), 90, 3) == LiteralExit(2, 3)
    assert literal_exit(LiteralProduct("fixed", 220, 0, 30, 30), 90, 3) == LiteralExit(30, 30)
    assert literal_yield(1_000_000, 150, 2) == 82
    assert literal_yield(1_000_000, 180, 2) == 98


def test_literal_zero_yield_retains_cash_without_forcing_a_purchase() -> None:
    assert literal_yield(100, 150, 5) == 0
    assert literal_yield(100, 180, 4) == 0


def test_public_ninety_day_choice_matches_literal_act365_values() -> None:
    snapshot, versions, positions, auth, exposure = asset_context(source_cash=1_000_000)
    products = [
        product_terms(LiteralProduct("t0", 150, 0), 100),
        product_terms(LiteralProduct("t1", 180, 1), 101),
        product_terms(LiteralProduct("fixed", 220, 0, 30, 30), 102),
    ]
    before = snapshot.model_dump()
    result = select_asset(snapshot, versions, positions, [], auth, products, exposure)
    assert result.status == "READY"
    assert result.selected_product_id == UUID(int=101)
    assert result.suggested_cents == 1_000_000
    assert result.net_simulated_yield_cents == 4389
    assert result.retained_cash_cents == 0
    assert {c.product_code: c.net_simulated_yield_cents for c in result.candidates} == {
        "t0": 3698,
        "t1": 4389,
        "fixed": 1808,
    }
    assert snapshot.model_dump() == before
    assert select_asset(snapshot, versions, positions, [], auth, products, exposure) == result


def test_public_t0_ninety_day_earnings_cannot_spend_cash_needed_on_day_two() -> None:
    snapshot, versions, positions, auth, exposure = asset_context(
        source_cash=100,
        initial_floor=0,
        later_floor=100,
        change_day=2,
        managed_limit=100,
        single_cap=100,
    )
    product = product_terms(LiteralProduct("t0", 10000, 0))
    result = select_asset(snapshot, versions, positions, [], auth, [product], exposure)
    assert result.status == "READY"
    assert result.selected_asset_class == "CASH"
    assert result.selected_product_id is None
    assert result.suggested_cents == 0
    assert result.net_simulated_yield_cents == 0
    assert result.candidates[0].max_allocatable_cents in (None, 0)


def test_public_old_managed_principal_and_pending_cash_remain_unavailable() -> None:
    snapshot, versions, positions, auth, exposure = asset_context(
        source_cash=100,
        initial_floor=10,
        managed_limit=100,
        single_cap=100,
        occupied=25,
    )
    exposure = exposure.model_copy(
        update={
            "pending_purchase_cents": 10,
            "reserved_cash_by_account": {CASH: 10},
            "counted_action_ids": [UUID(int=60)],
        }
    )
    product = product_terms(LiteralProduct("t0", 10000, 0))
    result = select_asset(snapshot, versions, positions, [], auth, [product], exposure)
    assert result.status == "READY"
    assert result.remaining_managed_cents == 65  # 100 - 25 settled principal - 10 pending.
    assert result.candidates[0].max_allocatable_cents == 65
    assert result.suggested_cents == 65
    assert result.baseline_boundary.safe_idle_cents == 90
    assert result.reservation_adjusted_boundary is not None
    assert result.reservation_adjusted_boundary.safe_idle_cents == 80
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.calculation_trace[0].cash_cents == 25
    assert result.candidate_boundary.calculation_trace[0].margin_cents == 15


def test_public_goal_ownership_inside_cash_account_is_preserved_during_placement() -> None:
    snapshot, versions, positions, auth, exposure = asset_context(
        scope="goal",
        source_cash=20,
        goal_cash=80,
        initial_floor=20,
        managed_limit=100,
        single_cap=100,
    )
    snapshot = snapshot.model_copy(
        update={
            "cash_accounts": [
                account.model_copy(
                    update={"balance_cents": 100 if account.account_id == CASH else 0}
                )
                for account in snapshot.cash_accounts
            ],
            "goals": [
                goal.model_copy(update={"account_id": CASH}) if goal.goal_id == GOAL else goal
                for goal in snapshot.goals
            ],
        }
    )
    before = snapshot.model_dump()
    result = select_asset(
        snapshot,
        versions,
        positions,
        [],
        auth,
        [product_terms(LiteralProduct("t0", 10000, 0))],
        exposure,
    )
    assert result.status == "READY"
    assert result.baseline_boundary.safe_idle_cents == 0
    assert result.suggested_cents == 80
    assert result.net_simulated_yield_cents == 19
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.calculation_trace[0].cash_cents == 20
    assert {point.margin_cents for point in result.candidate_boundary.calculation_trace} == {0}
    assert result.source_cash_uses[0].account_id == CASH
    assert result.source_cash_uses[0].amount_cents == 80
    assert snapshot.model_dump() == before


@st.composite
def general_cent_cases(draw: st.DrawFn) -> tuple[LiteralLedger, LiteralProduct, int, int, int]:
    initial = draw(st.integers(0, 12))
    later = draw(st.integers(0, 18))
    cash = max(initial, later) + draw(st.integers(1, 20))
    goal_cash, other_cash = draw(st.integers(0, 12)), draw(st.integers(0, 12))
    ledger = LiteralLedger(
        cash + goal_cash + other_cash,
        goal_cash,
        other_cash,
        initial,
        later,
        draw(st.integers(1, 89)),
    )
    product = draw(
        st.sampled_from(
            (
                LiteralProduct("t0", 10000, 0),
                LiteralProduct("t1", 10000, 1),
                LiteralProduct("fixed", 10000, 0, 30, 30),
            )
        )
    )
    occupied = draw(st.integers(0, 20))
    managed = occupied + draw(st.integers(1, 30))
    single = draw(st.integers(1, managed))
    return ledger, product, occupied, managed, single


@PROPERTY_SETTINGS
@given(general_cent_cases())
def test_general_maximum_matches_exhaustive_integer_cash_ledger(
    case: tuple[LiteralLedger, LiteralProduct, int, int, int],
) -> None:
    ledger, product, occupied, managed, single = case
    source = ledger.cash_cents - ledger.selected_goal_cash - ledger.other_goal_cash
    snapshot, versions, positions, auth, exposure = asset_context(
        source_cash=source,
        goal_cash=ledger.selected_goal_cash,
        other_goal_cash=ledger.other_goal_cash,
        initial_floor=ledger.initial_floor,
        later_floor=ledger.later_floor,
        change_day=ledger.floor_change_day,
        managed_limit=managed,
        single_cap=single,
        occupied=occupied,
    )
    plan = literal_exit(product, 90)
    assert plan is not None
    expected = literal_cap(
        ledger,
        "general_idle_funds",
        product,
        plan,
        source_cents=source,
        managed_limit=managed,
        occupied_cents=occupied,
        single_cap=single,
    )
    assert expected > 0  # Generated financial-cap examples are never vacuous zero cases.
    result = select_asset(
        snapshot, versions, positions, [], auth, [product_terms(product)], exposure
    )
    assert result.status == "READY"
    candidate = result.candidates[0]
    assert candidate.status == "FEASIBLE"
    assert candidate.max_allocatable_cents == expected
    earnings = literal_yield(expected, product.rate_bps, plan.earning_days)
    assert candidate.net_simulated_yield_cents == earnings
    assert candidate.exit_plan is not None
    assert candidate.exit_plan.principal_available_at == NOW + timedelta(days=plan.available_day)
    assert candidate.exit_plan.earning_days == plan.earning_days
    assert result.suggested_cents == (expected if earnings else 0)
    assert result.selected_asset_class == (
        "CASH" if earnings == 0 else product_terms(product).asset_class
    )
    assert (
        expected + 1 > source
        or expected + 1 > single
        or expected + 1 + occupied > managed
        or min(literal_margins(ledger, "general_idle_funds", expected + 1, plan)) < 0
    )
    assert result.candidate_boundary is not None
    expected_trace = literal_margins(
        ledger, "general_idle_funds", expected if earnings else 0, plan
    )
    assert (
        tuple(point.margin_cents for point in result.candidate_boundary.calculation_trace)
        == expected_trace
    )


@PROPERTY_SETTINGS
@given(
    goal_cash=st.integers(1, 40),
    floor=st.integers(0, 20),
    other_cash=st.integers(1, 25),
    occupied=st.integers(0, 20),
    room=st.integers(1, 45),
    requested_cap=st.integers(1, 65),
    window=st.integers(5, 90),
)
def test_zero_general_idle_does_not_block_owned_goal_principal_placement(
    goal_cash: int,
    floor: int,
    other_cash: int,
    occupied: int,
    room: int,
    requested_cap: int,
    window: int,
) -> None:
    managed, single = occupied + room, min(requested_cap, occupied + room)
    snapshot, versions, positions, auth, exposure = asset_context(
        scope="goal",
        source_cash=floor,
        goal_cash=goal_cash,
        other_goal_cash=other_cash,
        initial_floor=floor,
        managed_limit=managed,
        single_cap=single,
        occupied=occupied,
        window=window,
    )
    before = snapshot.model_dump()
    product = LiteralProduct("t1", 10000, 1)
    plan = LiteralExit(window - 1, window)
    ledger = LiteralLedger(floor + goal_cash + other_cash, goal_cash, other_cash, floor, floor, 1)
    expected = literal_cap(
        ledger,
        "goal",
        product,
        plan,
        source_cents=goal_cash,
        managed_limit=managed,
        occupied_cents=occupied,
        single_cap=single,
    )
    assert expected == min(goal_cash, room, single) > 0
    result = select_asset(
        snapshot, versions, positions, [], auth, [product_terms(product)], exposure
    )
    assert result.status == "READY"
    assert result.baseline_boundary.safe_idle_cents == 0
    assert result.candidates[0].max_allocatable_cents == expected
    earnings = literal_yield(expected, 10000, window - 1)
    selected = expected if earnings else 0
    assert result.suggested_cents == selected
    assert result.remaining_managed_cents == room
    assert result.candidate_boundary is not None
    assert {point.margin_cents for point in result.candidate_boundary.calculation_trace} == {0}
    assert result.candidate_boundary.calculation_trace[0].cash_cents == ledger.cash_cents - selected
    assert (
        result.source_cash_uses == []
        if not selected
        else (
            len(result.source_cash_uses) == 1
            and result.source_cash_uses[0].account_id == GOAL_ACCOUNT
            and result.source_cash_uses[0].amount_cents == selected
        )
    )
    assert snapshot.model_dump() == before


@PROPERTY_SETTINGS
@given(principal=st.integers(1_000, 10_000), window=st.integers(5, 90), expiry=st.integers(1, 96))
def test_one_explicit_exit_per_product_drives_both_safe_dates_and_act365_ranking(
    principal: int,
    window: int,
    expiry: int,
) -> None:
    snapshot, versions, positions, auth, exposure = asset_context(
        scope="goal",
        source_cash=20,
        goal_cash=principal,
        initial_floor=20,
        managed_limit=principal,
        single_cap=principal,
        expiry_day=expiry,
        window=window,
    )
    products = [
        LiteralProduct("t0", 150, 0),
        LiteralProduct("t1", 180, 1),
        LiteralProduct("fixed", 220, 0, 30, 30),
    ]
    dto_products = [product_terms(product, 100 + i) for i, product in enumerate(products)]
    result = select_asset(snapshot, versions, positions, [], auth, dto_products, exposure)
    assert result.status == "READY"
    ranked: list[tuple[int, int, int, str, UUID]] = []
    for product, dto in zip(products, dto_products, strict=True):
        plan = literal_exit(product, window, expiry)
        candidate = next(item for item in result.candidates if item.product_id == dto.product_id)
        if plan is None:
            assert candidate.status == "REJECTED"
            continue
        expected_yield = literal_yield(principal, product.rate_bps, plan.earning_days)
        if plan.available_day == 0 and candidate.status == "REJECTED":
            # Immediate T0 purchase/return is equivalent to retaining cash. It may
            # be omitted instead of creating a fictitious already-returned position.
            assert expected_yield == 0
            continue
        assert candidate.status == "FEASIBLE"
        assert candidate.max_allocatable_cents == principal
        assert candidate.net_simulated_yield_cents == expected_yield
        assert candidate.exit_plan is not None
        assert candidate.exit_plan.principal_available_at == NOW + timedelta(
            days=plan.available_day
        )
        assert candidate.exit_plan.earning_days == plan.earning_days
        if product.fixed_term_days is None:
            assert candidate.exit_plan.request_at == NOW + timedelta(days=plan.earning_days)
            assert candidate.exit_plan.request_at < NOW + timedelta(days=expiry)
        else:
            assert candidate.exit_plan.request_at is None
        ranked.append(
            (
                -expected_yield,
                product.lock_days + product.delay_days,
                product.delay_days,
                product.name,
                dto.product_id,
            )
        )
    expected_choice = min(ranked)
    assert result.selected_product_id == (expected_choice[-1] if expected_choice[0] < 0 else None)
    assert result.suggested_cents == (principal if expected_choice[0] < 0 else 0)
    assert result.net_simulated_yield_cents == -expected_choice[0]


@PROPERTY_SETTINGS
@given(
    first=st.integers(1000, 30_000),
    second=st.integers(1000, 30_000),
    goal_cash=st.integers(0, 3000),
    other_cash=st.integers(0, 3000),
    ordering=st.permutations((0, 1, 2)),
)
def test_account_goal_policy_and_product_permutations_preserve_complete_selection_hash(
    first: int,
    second: int,
    goal_cash: int,
    other_cash: int,
    ordering: tuple[int, ...],
) -> None:
    snapshot, versions, positions, auth, exposure = asset_context(
        source_cash=first,
        goal_cash=goal_cash,
        other_goal_cash=other_cash,
    )
    snapshot = snapshot.model_copy(
        update={
            "cash_accounts": [
                *snapshot.cash_accounts,
                CashFact(
                    account_id=UUID(int=4),
                    account_type="CASH",
                    balance_cents=second,
                    observed_at=NOW,
                ),
            ]
        }
    )
    products = [
        product_terms(LiteralProduct("t0", 150, 0), 100),
        product_terms(LiteralProduct("t1", 180, 1), 101),
        product_terms(LiteralProduct("fixed", 220, 0, 30, 30), 102),
    ]
    baseline = select_asset(snapshot, versions, positions, [], auth, products, exposure)
    reordered = snapshot.model_copy(
        update={
            "cash_accounts": list(reversed(snapshot.cash_accounts)),
            "goals": list(reversed(snapshot.goals)),
            "goal_month_contributions": list(reversed(snapshot.goal_month_contributions)),
        }
    )
    result = select_asset(
        reordered,
        list(reversed(versions)),
        list(reversed(positions)),
        [],
        auth,
        [products[i] for i in ordering],
        exposure,
    )
    assert result == baseline
    assert result.status == "READY"
    assert result.selected_product_id == UUID(int=101)
    assert result.suggested_cents == first + second
    assert [(use.account_id, use.amount_cents) for use in result.source_cash_uses] == [
        (CASH, first),
        (UUID(int=4), second),
    ]


@PROPERTY_SETTINGS
@given(
    source=st.integers(10, 70),
    floor=st.integers(0, 8),
    occupied=st.integers(1, 20),
    room=st.integers(1, 60),
    rate_low=st.integers(1, 4999),
    rate_high=st.integers(5000, 10000),
)
def test_yield_changes_ranking_values_without_changing_cash_cap_or_principal_boundary(
    source: int,
    floor: int,
    occupied: int,
    room: int,
    rate_low: int,
    rate_high: int,
) -> None:
    snapshot, versions, positions, auth, exposure = asset_context(
        source_cash=source,
        initial_floor=floor,
        managed_limit=occupied + room,
        single_cap=occupied + room,
        occupied=occupied,
    )
    products = [product_terms(LiteralProduct("t0", rate, 0)) for rate in (rate_low, rate_high)]
    results = [
        select_asset(snapshot, versions, positions, [], auth, [product], exposure)
        for product in products
    ]
    expected_cap = min(source - floor, room)
    assert expected_cap > 0
    left, right = (result.candidates[0] for result in results)
    assert left.max_allocatable_cents == right.max_allocatable_cents == expected_cap
    assert left.financial_cap_cents == right.financial_cap_cents
    assert left.candidate_boundary_hash == right.candidate_boundary_hash
    assert results[0].baseline_boundary == results[1].baseline_boundary
    assert left.net_simulated_yield_cents == literal_yield(expected_cap, rate_low, 90)
    assert right.net_simulated_yield_cents == literal_yield(expected_cap, rate_high, 90)
    assert results[0].selection_hash != results[1].selection_hash
