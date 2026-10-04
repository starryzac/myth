"""Independent whole-position cash ledger for MVP-205 recovery planning."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryResult,
    BoundarySnapshot,
    CashFact,
    GoalMonthFact,
    GoalOwnership,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.domain.recovery import (
    RecoveryAuthorization,
    RecoveryPlan,
    RecoveryPosition,
    RecoveryQuote,
    plan_recovery,
)
from hypothesis import given, settings
from hypothesis import strategies as st

NOW = datetime(2026, 10, 3, 16, tzinfo=UTC)
START = NOW - timedelta(days=30)
USER = UUID(int=1)
CASH = UUID(int=2)
GOAL_ACCOUNT = UUID(int=3)
ASSET_ACCOUNT = UUID(int=4)
GOAL = UUID(int=10)
GOAL_POLICY = UUID(int=11)
PROPERTY_SETTINGS = settings(max_examples=150, derandomize=True, database=None, deadline=None)


def policy(config: dict[str, Any], identity: int, version: int) -> BoundaryPolicyVersion:
    canonical = validate_configuration(config)
    return BoundaryPolicyVersion(
        policy_id=UUID(int=identity),
        version_id=UUID(int=version),
        configuration=canonical,
        content_hash=configuration_hash(canonical),
        confirmed_at=START,
        valid_from=START,
    )


def recovery_context(
    ledger: "LiteralRecovery", lots: tuple["LiteralPosition", ...], *, yield_bps: int = 0
) -> tuple[
    BoundarySnapshot,
    list[BoundaryPolicyVersion],
    list[BoundaryPosition],
    list[RecoveryPosition],
    list[RecoveryAuthorization],
]:
    """Valid, zero-fee trusted DTO fixtures; source proofs are an adapter responsibility."""
    goal_principal = sum(lot.principal for lot in lots if lot.goal_owned)
    snapshot = BoundarySnapshot(
        as_of=NOW,
        timezone="Asia/Shanghai",
        cash_accounts=[
            CashFact(
                account_id=CASH,
                account_type="CASH",
                balance_cents=ledger.cash - ledger.goal_cash,
                observed_at=NOW,
            ),
            CashFact(
                account_id=GOAL_ACCOUNT,
                account_type="GOAL",
                balance_cents=ledger.goal_cash,
                observed_at=NOW,
            ),
            CashFact(
                account_id=ASSET_ACCOUNT,
                account_type="CASH_MANAGEMENT",
                balance_cents=0,
                observed_at=NOW,
            ),
        ],
        goals=[
            GoalOwnership(
                goal_id=GOAL,
                policy_id=GOAL_POLICY,
                account_id=GOAL_ACCOUNT,
                cash_owned_cents=ledger.goal_cash,
                principal_owned_cents=goal_principal,
                allocated_cents=ledger.goal_cash + goal_principal,
            )
        ],
        goal_month_contributions=[
            GoalMonthFact(goal_id=GOAL, period="2026-10", contributed_cents=0)
        ],
    )
    versions = [
        policy({"type": "emergency_buffer", "amount_cents": ledger.other_protection}, 12, 13)
    ]
    positions: list[BoundaryPosition] = []
    recoveries: list[RecoveryPosition] = []
    authorizations: list[RecoveryAuthorization] = []
    originals: dict[bool, BoundaryPolicyVersion] = {}
    for owned in sorted({lot.goal_owned for lot in lots}):
        identity = 30 if owned else 20
        config: dict[str, Any] = {
            "type": "asset_authorization",
            "scope": "goal" if owned else "general_idle_funds",
            "allowed_asset_classes": ["CASH", "CASH_MGMT_T0", "CASH_MGMT_T1", "FIXED_DEPOSIT"],
            "max_auto_managed_cents": 1_000_000,
            "single_action_cap_cents": 1_000_000,
            "max_redemption_delay_days": 1,
            "max_lock_days": 30,
            "max_principal_risk_level": 0,
            "allow_auto_recovery_without_penalty": True,
            "allow_early_withdrawal_with_penalty": False,
        }
        if owned:
            config["goal_id"] = str(GOAL)
        originals[owned] = policy(config, identity, identity + 1)
        current = policy(config, identity, identity + 2)
        versions.append(current)
        authorizations.append(
            RecoveryAuthorization(
                **current.model_dump(),
                user_id=USER,
                policy_status="ACTIVE",
                latest_version_id=current.version_id,
            )
        )
    for lot in lots:
        position_id, product_id = UUID(int=100 + lot.identity), UUID(int=200 + lot.identity)
        rule: dict[str, Any] = {
            "protocol": "planned-principal-return-v1",
            "day_basis": "CALENDAR",
            "guaranteed": True,
            "settlement_delay_days": lot.delay_days,
            "principal_return_bps": 10000,
            "rollover": False,
            "auto_rollover": False,
            "yield_rule": {
                "protocol": "simple-annual-yield-v1",
                "basis": "ACT_365",
                "annual_yield_bps": yield_bps,
                "simulation": True,
                "fee_cents": 0,
                "purchase_fee_bps": 0,
                "redemption_fee_bps": 0,
                "accrual": "UNTIL_REDEMPTION_REQUEST",
            },
        }
        product = AssetProductTerms(
            product_id=product_id,
            product_code=f"R{lot.identity}",
            version_number=2,
            asset_class="CASH_MGMT_T0" if lot.delay_days == 0 else "CASH_MGMT_T1",
            risk_level=0,
            principal_fluctuation=False,
            minimum_purchase_cents=1,
            lock_days=0,
            redemption_delay_days=lot.delay_days,
            annual_yield_bps=yield_bps,
            early_withdrawal_loss_bps=0,
            auto_purchase_allowed=True,
            auto_redeem_allowed=True,
            created_at=START,
            effective_from=START,
            maturity_rule=rule,
            terms_digest=configuration_hash(rule),
        )
        positions.append(
            BoundaryPosition(
                position_id=position_id,
                goal_id=GOAL if lot.goal_owned else None,
                principal_cents=lot.principal,
                status="HELD",
                principal_available_at=NOW + timedelta(days=lot.original_return_day)
                if lot.original_return_day is not None
                else None,
            )
        )
        quote = RecoveryQuote(
            quote_id=UUID(int=300 + lot.identity),
            user_id=USER,
            position_id=position_id,
            product_id=product_id,
            product_version_number=2,
            terms_digest=product.terms_digest,
            kind="REDEEM",
            principal_cents=lot.principal,
            fee_cents=0,
            loss_cents=0,
            net_cents=lot.principal,
            request_at=NOW,
            principal_available_at=NOW + timedelta(days=lot.delay_days),
            expires_at=NOW + timedelta(hours=1),
        )
        recoveries.append(
            RecoveryPosition(
                position_id=position_id,
                account_id=ASSET_ACCOUNT,
                destination_account_id=GOAL_ACCOUNT if lot.goal_owned else CASH,
                goal_id=GOAL if lot.goal_owned else None,
                purchased_at=NOW - timedelta(days=10),
                acquisition="AUTHORIZED_PURCHASE",
                product=product,
                original_authorization=originals[lot.goal_owned],
                quote=quote,
            )
        )
    return snapshot, versions, positions, recoveries, authorizations


def public_recovery(
    ledger: "LiteralRecovery", lots: tuple["LiteralPosition", ...], *, yield_bps: int = 0
) -> RecoveryPlan:
    snapshot, versions, positions, recoveries, authorizations = recovery_context(
        ledger, lots, yield_bps=yield_bps
    )
    return plan_recovery(
        snapshot, versions, positions, [], recoveries, authorizations, user_id=USER
    )


def reported_trace(boundary: BoundaryResult) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        (point.cash_cents, point.protected_cents_by_reason["goal_cash"], point.margin_cents)
        for point in boundary.calculation_trace
    )


@dataclass(frozen=True)
class LiteralPosition:
    identity: int
    principal: int
    delay_days: int
    original_return_day: int | None = None
    goal_owned: bool = False


@dataclass(frozen=True)
class LiteralRecovery:
    cash: int
    other_protection: int
    goal_cash: int = 0


def literal_trace(
    ledger: LiteralRecovery,
    positions: tuple[LiteralPosition, ...],
    selected: tuple[int, ...] = (),
) -> tuple[tuple[int, int, int], ...]:
    """Cash, goal cash and margin at 273 points, with one return per original lot.

    The fixture has constant non-goal protection and no income or yield. A T0
    candidate is an already-settled counterfactual snapshot. A delayed candidate
    replaces, rather than supplements, the original position's return event.
    """
    points: list[tuple[int, int, int]] = []
    for day in range(91):
        for phase in range(3):
            cash, goal_cash = ledger.cash, ledger.goal_cash
            for position in positions:
                recovering = position.identity in selected
                return_day = position.delay_days if recovering else position.original_return_day
                settled_now = recovering and position.delay_days == 0
                returned = settled_now or (
                    return_day is not None
                    and (day > return_day or day == return_day and phase == 2)
                )
                if returned:
                    cash += position.principal
                    goal_cash += position.principal if position.goal_owned else 0
            points.append((cash, goal_cash, cash - ledger.other_protection - goal_cash))
    return tuple(points)


def literal_is_improvement(
    before: tuple[tuple[int, int, int], ...], after: tuple[tuple[int, int, int], ...]
) -> bool:
    return all(new[2] >= old[2] for old, new in zip(before, after, strict=True)) and any(
        old[2] < 0 and new[2] > old[2] for old, new in zip(before, after, strict=True)
    )


def literal_selected(
    ledger: LiteralRecovery, positions: tuple[LiteralPosition, ...]
) -> tuple[int, ...]:
    """Only this finite fixture's deterministic whole-position attempts are compared."""
    selected: tuple[int, ...] = ()
    for position in sorted(positions, key=lambda item: (item.delay_days, item.identity)):
        before = literal_trace(ledger, positions, selected)
        after = literal_trace(ledger, positions, (*selected, position.identity))
        if literal_is_improvement(before, after):
            selected = (*selected, position.identity)
    return selected


def test_literal_t0_is_whole_position_and_not_already_current_cash() -> None:
    ledger = LiteralRecovery(70_000, 100_000)
    positions = (LiteralPosition(1, 50_000, 0),)
    actual = literal_trace(ledger, positions)
    projected = literal_trace(ledger, positions, (1,))
    assert set(actual) == {(70_000, 0, -30_000)}
    assert set(projected) == {(120_000, 0, 20_000)}
    assert literal_selected(ledger, positions) == (1,)
    assert projected[0][0] - actual[0][0] == 50_000  # Never invent a 30,000 partial lot.


def test_literal_t1_improves_negative_points_without_improving_global_minimum() -> None:
    ledger = LiteralRecovery(70_000, 100_000)
    positions = (LiteralPosition(1, 30_000, 1),)
    actual = literal_trace(ledger, positions)
    projected = literal_trace(ledger, positions, (1,))
    assert projected[:5] == ((70_000, 0, -30_000),) * 5
    assert projected[5:] == ((100_000, 0, 0),) * 268
    assert min(row[2] for row in actual) == min(row[2] for row in projected) == -30_000
    assert literal_is_improvement(actual, projected)
    assert literal_selected(ledger, positions) == (1,)


def test_literal_partial_recovery_means_remaining_deficit_not_partial_redemption() -> None:
    ledger = LiteralRecovery(70_000, 100_000)
    positions = (LiteralPosition(1, 10_000, 0), LiteralPosition(2, 20_000, 1))
    t0 = literal_trace(ledger, positions, (1,))
    both = literal_trace(ledger, positions, (1, 2))
    assert set(t0) == {(80_000, 0, -20_000)}
    assert both[:5] == ((80_000, 0, -20_000),) * 5
    assert both[5:] == ((100_000, 0, 0),) * 268
    assert literal_selected(ledger, positions) == (1, 2)


def test_literal_goal_principal_return_preserves_general_deficit() -> None:
    ledger = LiteralRecovery(70_000, 80_000, 20_000)
    positions = (LiteralPosition(1, 30_000, 0, goal_owned=True),)
    actual = literal_trace(ledger, positions)
    projected = literal_trace(ledger, positions, (1,))
    assert set(actual) == {(70_000, 20_000, -30_000)}
    assert set(projected) == {(100_000, 50_000, -30_000)}
    assert not literal_is_improvement(actual, projected)
    assert literal_selected(ledger, positions) == ()


def test_literal_early_return_replaces_original_day_five_event() -> None:
    ledger = LiteralRecovery(70_000, 100_000)
    positions = (LiteralPosition(1, 30_000, 0, original_return_day=5),)
    actual = literal_trace(ledger, positions)
    projected = literal_trace(ledger, positions, (1,))
    assert actual[16] == (70_000, 0, -30_000)
    assert actual[17] == (100_000, 0, 0)
    assert set(projected) == {(100_000, 0, 0)}
    assert max(point[0] for point in projected) == 100_000


def test_literal_stop_does_not_redeem_more_just_to_increase_positive_margin() -> None:
    ledger = LiteralRecovery(70_000, 100_000)
    positions = (LiteralPosition(1, 50_000, 0), LiteralPosition(2, 80_000, 0))
    assert literal_selected(ledger, positions) == (1,)
    assert literal_selected(replace(ledger, cash=100_000), positions) == ()


def test_literal_known_fee_is_not_a_zero_loss_recovery() -> None:
    principal, fee, loss, shortage = 30_000, 100, 0, 30_000
    net = principal - fee - loss
    assert net == 29_900
    assert shortage - net == 100
    assert fee + loss > 0  # Must remain a non-executed, explicitly bound ASK_ONCE quote.


def test_public_t1_retains_actual_risk_while_improving_future_negative_points() -> None:
    ledger = LiteralRecovery(70_000, 100_000)
    lots = (LiteralPosition(1, 30_000, 1),)
    result = public_recovery(ledger, lots)
    assert len(result.steps) == 1
    assert result.steps[0].quote.principal_cents == 30_000
    assert result.actual_boundary.status == "LIQUIDITY_RISK"
    assert result.actual_boundary.minimum_margin_cents == -30_000
    assert result.projected_boundary is not None
    assert result.projected_boundary.minimum_margin_cents == -30_000
    assert [point.margin_cents for point in result.projected_boundary.calculation_trace] == (
        [-30_000] * 5 + [0] * 268
    )
    assert len(result.uncovered_checkpoints) == 5
    assert result.first_sustained_safe_point is not None
    assert result.first_sustained_safe_point.day == 1
    assert result.first_sustained_safe_point.phase == "AFTER_PRINCIPAL"
    assert result.preview_only is True


@st.composite
def small_recovery_cases(draw: st.DrawFn) -> tuple[LiteralRecovery, tuple[LiteralPosition, ...]]:
    cash, deficit = draw(st.integers(0, 100)), draw(st.integers(1, 60))
    lots = tuple(
        LiteralPosition(
            identity=i,
            principal=draw(st.integers(1, 70)),
            delay_days=draw(st.integers(0, 1)),
            original_return_day=draw(st.one_of(st.none(), st.integers(2, 60))),
        )
        for i in (1, 2, 3)
    )
    return LiteralRecovery(cash, cash + deficit), lots


@PROPERTY_SETTINGS
@given(small_recovery_cases())
def test_whole_position_sequence_matches_independent_pointwise_improvement_oracle(
    case: tuple[LiteralRecovery, tuple[LiteralPosition, ...]],
) -> None:
    ledger, lots = case
    expected = literal_selected(ledger, lots)
    assert expected  # Every generated fixture contains a genuine negative-point improvement.
    snapshot, versions, positions, recoveries, authorizations = recovery_context(ledger, lots)
    before_snapshot = snapshot.model_dump()
    before_positions = [position.model_dump() for position in positions]
    result = plan_recovery(
        snapshot, versions, positions, [], recoveries, authorizations, user_id=USER
    )
    assert tuple(step.position_id.int - 100 for step in result.steps) == expected
    assert reported_trace(result.actual_boundary) == literal_trace(ledger, lots)
    assert result.projected_boundary is not None
    assert reported_trace(result.projected_boundary) == literal_trace(ledger, lots, expected)
    cumulative: tuple[int, ...] = ()
    for step in result.steps:
        identity = step.position_id.int - 100
        origin = next(lot for lot in lots if lot.identity == identity)
        assert step.quote.principal_cents == step.quote.net_cents == origin.principal
        assert step.quote.fee_cents == step.quote.loss_cents == 0
        assert step.autonomy_level == "AUTO_EXECUTE"
        assert literal_is_improvement(
            literal_trace(ledger, lots, cumulative),
            literal_trace(ledger, lots, (*cumulative, identity)),
        )
        cumulative = (*cumulative, identity)
    assert snapshot.model_dump() == before_snapshot
    assert [position.model_dump() for position in positions] == before_positions


@PROPERTY_SETTINGS
@given(cash=st.integers(0, 100), deficit=st.integers(1, 80), principal=st.integers(1, 120))
def test_delayed_recovery_keeps_global_minimum_but_can_improve_future_deficit(
    cash: int,
    deficit: int,
    principal: int,
) -> None:
    ledger = LiteralRecovery(cash, cash + deficit)
    lots = (LiteralPosition(1, principal, 1),)
    result = public_recovery(ledger, lots)
    assert len(result.steps) == 1
    assert result.steps[0].quote.principal_cents == principal
    assert result.actual_boundary.minimum_margin_cents == -deficit
    assert result.actual_boundary.status == "LIQUIDITY_RISK"
    assert result.projected_boundary is not None
    assert result.projected_boundary.minimum_margin_cents == -deficit
    assert reported_trace(result.projected_boundary) == literal_trace(ledger, lots, (1,))
    assert [point.margin_cents for point in result.projected_boundary.calculation_trace[:5]] == [
        -deficit
    ] * 5
    assert len(result.uncovered_checkpoints) == (5 if principal >= deficit else 273)
    if principal >= deficit:
        assert result.first_sustained_safe_point is not None
        assert (result.first_sustained_safe_point.day, result.first_sustained_safe_point.phase) == (
            1,
            "AFTER_PRINCIPAL",
        )
    else:
        assert result.first_sustained_safe_point is None
    assert result.preview_only is True
    assert result.status != "NO_RECOVERY_NEEDED"


@PROPERTY_SETTINGS
@given(
    cash=st.integers(0, 100),
    deficit=st.integers(1, 60),
    surplus=st.integers(0, 60),
    later_principal=st.integers(1, 60),
    original_day=st.integers(2, 40),
)
def test_early_return_replaces_original_event_and_ordering_cannot_change_hash(
    cash: int,
    deficit: int,
    surplus: int,
    later_principal: int,
    original_day: int,
) -> None:
    ledger = LiteralRecovery(cash, cash + deficit)
    lots = (
        LiteralPosition(1, deficit + surplus, 0, original_day),
        LiteralPosition(2, later_principal, 1, original_day + 1),
    )
    expected = literal_selected(ledger, lots)
    assert expected == (1,)  # T0 already removed every negative point; do not redeem the T1.
    snapshot, versions, positions, recoveries, authorizations = recovery_context(ledger, lots)
    result = plan_recovery(
        snapshot, versions, positions, [], recoveries, authorizations, user_id=USER
    )
    assert tuple(step.position_id.int - 100 for step in result.steps) == (1,)
    assert result.projected_boundary is not None
    assert reported_trace(result.projected_boundary) == literal_trace(ledger, lots, expected)
    trace = result.projected_boundary.calculation_trace
    assert trace[original_day * 3 + 2].cash_cents == cash + deficit + surplus
    assert trace[-1].cash_cents == cash + deficit + surplus + later_principal
    assert result.first_sustained_safe_point is not None
    assert (result.first_sustained_safe_point.day, result.first_sustained_safe_point.phase) == (
        0,
        "BEFORE_PAYMENT",
    )
    reordered = snapshot.model_copy(
        update={
            "cash_accounts": list(reversed(snapshot.cash_accounts)),
            "goals": list(reversed(snapshot.goals)),
            "goal_month_contributions": list(reversed(snapshot.goal_month_contributions)),
        }
    )
    replay = plan_recovery(
        reordered,
        list(reversed(versions)),
        list(reversed(positions)),
        [],
        list(reversed(recoveries)),
        list(reversed(authorizations)),
        user_id=USER,
    )
    assert replay == result


@PROPERTY_SETTINGS
@given(
    source_cash=st.integers(0, 100),
    goal_cash=st.integers(0, 100),
    deficit=st.integers(2, 60),
    general_principal=st.integers(1, 59),
    goal_extra=st.integers(1, 100),
    delay=st.integers(0, 1),
)
def test_goal_principal_and_yield_cannot_fill_the_remaining_general_deficit(
    source_cash: int,
    goal_cash: int,
    deficit: int,
    general_principal: int,
    goal_extra: int,
    delay: int,
) -> None:
    general = min(general_principal, deficit - 1)
    ledger = LiteralRecovery(source_cash + goal_cash, source_cash + deficit, goal_cash)
    lots = (
        LiteralPosition(1, general, delay),
        LiteralPosition(2, deficit + goal_extra, 0, goal_owned=True),
    )
    expected = literal_selected(ledger, lots)
    assert expected == (1,)
    low, high = (public_recovery(ledger, lots, yield_bps=rate) for rate in (0, 10000))
    assert tuple(step.position_id.int - 100 for step in low.steps) == (1,)
    assert tuple(step.position_id.int - 100 for step in high.steps) == (1,)
    assert low.steps[0].quote.principal_cents == high.steps[0].quote.principal_cents == general
    assert low.actual_boundary == high.actual_boundary
    assert low.projected_boundary is not None
    assert low.projected_boundary == high.projected_boundary
    assert reported_trace(low.projected_boundary) == literal_trace(ledger, lots, expected)
    assert low.projected_boundary.calculation_trace[-1].margin_cents == general - deficit < 0
    assert low.first_sustained_safe_point is None
    assert high.first_sustained_safe_point is None
    assert low.plan_hash != high.plan_hash  # Exact terms remain bound even when finance is equal.
