"""Unexecuted B01-B11 inputs and independent hand-worked display expectations."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.boundary_types import (
    BillFact,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundarySnapshot,
    CashFact,
    GoalMonthFact,
    GoalOwnership,
    SettlementFact,
    SourceIssue,
    UnassignedGoalCash,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration

NOW = datetime(2026, 10, 4, tzinfo=UTC)


@dataclass(frozen=True)
class DisplayCase:
    name: str
    snapshot: BoundarySnapshot
    policies: tuple[BoundaryPolicyVersion, ...] = ()
    positions: tuple[BoundaryPosition, ...] = ()
    products: tuple[BoundaryProduct, ...] = ()
    expected: dict[str, Any] = field(default_factory=dict)


def snapshot(cash: int = 20_000, now: datetime = NOW, **facts: Any) -> BoundarySnapshot:
    return BoundarySnapshot(
        as_of=now,
        timezone="UTC",
        cash_accounts=[
            CashFact(
                account_id=UUID(int=1), account_type="CASH", balance_cents=cash, observed_at=now
            ),
            CashFact(
                account_id=UUID(int=2), account_type="CREDIT_CARD", balance_cents=0, observed_at=now
            ),
        ],
        **facts,
    )


def policy(
    identifier: int, config: dict[str, Any], now: datetime = NOW, **changes: Any
) -> BoundaryPolicyVersion:
    config = validate_configuration(config)
    return BoundaryPolicyVersion(
        policy_id=UUID(int=identifier),
        version_id=UUID(int=identifier + 1000),
        configuration=config,
        content_hash=configuration_hash(config),
        confirmed_at=changes.pop("confirmed_at", now),
        valid_from=changes.pop("valid_from", now),
        **changes,
    )


def rent(
    identifier: int = 20,
    *,
    amount: int = 5_000,
    due: int = 15,
    rule: dict[str, Any] | None = None,
    now: datetime = NOW,
    **changes: Any,
) -> BoundaryPolicyVersion:
    return policy(
        identifier,
        {
            "type": "recurring_obligation",
            "payee_id": "actual-rent-reference",
            "due_day": due,
            "amount_rule": rule or {"kind": "exact", "amount_cents": amount},
        },
        now=now,
        **changes,
    )


def bill(identifier: int, due: date, *, total: int = 10_000, paid: int = 0) -> BillFact:
    return BillFact(
        bill_id=UUID(int=identifier),
        account_id=UUID(int=2),
        statement_date=min(date(2026, 9, 1), due),
        due_date=due,
        total_cents=total,
        paid_cents=paid,
        status="PAID" if paid == total else "PARTIALLY_PAID" if paid else "UNPAID",
        evidence_ids=[UUID(int=identifier + 10_000)],
    )


def cases() -> list[DisplayCase]:
    result = [
        DisplayCase(
            "B01-single-overdue",
            snapshot(bills=[bill(10, date(2026, 9, 20), paid=4_000)]),
            expected={
                "details.next_obligations.next_due_date": "2026-09-20",
                "details.next_obligations.next_remaining_protection_cents": 6_000,
                "details.current_protection.value.amounts_by_reason.obligations": 6_000,
                "boundary.calculation_trace.1.cash_cents": 14_000,
                "boundary.safe_idle_cents": 14_000,
            },
        ),
        DisplayCase(
            "B01-overdue-original-date",
            snapshot(
                bills=[
                    bill(10, date(2026, 9, 20), paid=4_000),
                    bill(11, date(2026, 9, 25), total=1_000),
                ]
            ),
            expected={
                "details.next_obligations.next_due_date": "2026-09-20",
                "details.next_obligations.next_count": 1,
                "details.next_obligations.next_remaining_protection_cents": 6_000,
                "details.next_obligations.items.0.projection_payment_date": "2026-10-04",
                "details.next_obligations.items.0.overdue": True,
                "details.current_protection.value.amounts_by_reason.obligations": 7_000,
                "boundary.safe_idle_cents": 13_000,
                "boundary.calculation_trace.1.cash_cents": 13_000,
            },
        ),
    ]
    exact = rent()
    settlement = SettlementFact(
        policy_id=exact.policy_id,
        period="2026-10",
        paid_cents=2_000,
        settled_at=NOW,
        evidence_ids=[UUID(int=800)],
    )
    result.append(
        DisplayCase(
            "B02-exact-period-only",
            snapshot(occurrence_settlements=[settlement]),
            (exact,),
            expected={
                "details.next_obligations.next_due_date": "2026-10-15",
                "details.next_obligations.next_remaining_protection_cents": 3_000,
                "details.next_obligations.items.0.total_basis": "POLICY_EXACT",
                "details.next_obligations.items.0.actual_final_total_cents": None,
                "details.current_protection.value.amounts_by_reason.obligations": 13_000,
                "boundary.safe_idle_cents": 7_000,
            },
        )
    )
    ranged = rent(rule={"kind": "range", "min_cents": 3_000, "max_cents": 5_000})
    for label, paid, final, first_due, current, next_amount, basis in (
        ("unknown-final", 1_000, None, "2026-10-15", 14_000, 4_000, "UPPER_BOUND"),
        ("actual-final", 1_000, 4_000, "2026-10-15", 13_000, 3_000, "EXACT"),
        ("actual-paid-final", 4_000, 4_000, "2026-11-15", 10_000, 5_000, "UPPER_BOUND"),
        ("paid-without-final", 4_000, None, "2026-10-15", 11_000, 1_000, "UPPER_BOUND"),
    ):
        result.append(
            DisplayCase(
                f"B03-range-{label}",
                snapshot(
                    occurrence_settlements=[
                        SettlementFact(
                            policy_id=ranged.policy_id,
                            period="2026-10",
                            paid_cents=paid,
                            final_total_cents=final,
                            settled_at=NOW,
                        )
                    ]
                ),
                (ranged,),
                expected={
                    "details.next_obligations.next_due_date": first_due,
                    "details.next_obligations.next_remaining_protection_cents": next_amount,
                    "details.next_obligations.basis_summary": basis,
                    "details.next_obligations.items.0.actual_final_total_cents": (
                        final if first_due == "2026-10-15" else None
                    ),
                    "details.current_protection.value.amounts_by_reason.obligations": current,
                },
            )
        )
    old = rent(
        confirmed_at=datetime(2026, 9, 1, tzinfo=UTC),
        valid_from=datetime(2026, 9, 1, tzinfo=UTC),
        valid_until=datetime(2026, 10, 3, tzinfo=UTC),
    )
    for label, historical_paid in (("missing", None), ("proved-zero", 0), ("fully-paid", 5_000)):
        settlements = (
            []
            if historical_paid is None
            else [
                SettlementFact(
                    policy_id=old.policy_id,
                    period="2026-09",
                    paid_cents=historical_paid,
                    settled_at=NOW,
                )
            ]
        )
        result.append(
            DisplayCase(
                f"B04-old-period-{label}",
                snapshot(occurrence_settlements=settlements),
                (old,),
                expected={
                    "details.next_obligations.status": "NOT_PROVEN"
                    if historical_paid is None
                    else "PROVEN",
                    "details.next_obligations.next_due_date": "2026-09-15"
                    if historical_paid == 0
                    else None,
                    "details.next_obligations.next_count": None
                    if historical_paid is None
                    else int(historical_paid == 0),
                    "details.next_obligations.next_remaining_protection_cents": (
                        None if historical_paid is None else 5_000 if historical_paid == 0 else 0
                    ),
                },
            )
        )
    card = rent(rule={"kind": "bill_balance", "account_id": str(UUID(int=2))})
    for label, paid, count, total in (("partial", 4_000, 1, 6_000), ("paid", 10_000, 0, 0)):
        result.append(
            DisplayCase(
                f"B05-bill-balance-{label}",
                snapshot(bills=[bill(10, date(2026, 10, 15), paid=paid)]),
                (card,),
                expected={
                    "details.next_obligations.next_count": count,
                    "details.current_protection.value.amounts_by_reason.obligations": total,
                },
            )
        )
    result.append(
        DisplayCase(
            "B05-bad-bill-source",
            snapshot(
                source_issues=[
                    SourceIssue(
                        code="MISSING_BILL_EVIDENCE",
                        entity_type="CREDIT_CARD_BILL",
                        entity_id=str(UUID(int=10)),
                    )
                ]
            ),
            (card,),
            expected={"details.next_obligations.status": "NOT_PROVEN"},
        )
    )
    result.append(
        DisplayCase(
            "B06-window-inclusive",
            snapshot(
                bills=[
                    bill(10, date(2026, 10, 4), total=100),
                    bill(11, date(2027, 1, 2), total=200),
                    bill(12, date(2027, 1, 3), total=400),
                ]
            ),
            expected={
                "details.window_end": "2027-01-02",
                "details.current_protection.value.amounts_by_reason.obligations": 300,
                "details.next_obligations.items.0.overdue": False,
            },
        )
    )
    cross_day = datetime(2026, 10, 3, 16, tzinfo=UTC)
    for zone, overdue in (("UTC", False), ("Asia/Shanghai", True)):
        base = snapshot(now=cross_day, bills=[bill(10, date(2026, 10, 3), total=100)])
        result.append(
            DisplayCase(
                f"B06-local-day-{zone.replace('/', '-')}",
                base.model_copy(update={"timezone": zone}),
                expected={
                    "details.next_obligations.items.0.overdue": overdue,
                    "details.window_start": "2026-10-04" if overdue else "2026-10-03",
                },
            )
        )
    for month_start, expected_due in (
        (datetime(2026, 11, 1, tzinfo=UTC), "2026-11-30"),
        (datetime(2027, 2, 1, tzinfo=UTC), "2027-02-28"),
        (datetime(2028, 2, 1, tzinfo=UTC), "2028-02-29"),
    ):
        result.append(
            DisplayCase(
                f"B07-short-month-{expected_due}",
                snapshot(now=month_start),
                (rent(due=31, now=month_start),),
                expected={"details.next_obligations.next_due_date": expected_due},
            )
        )
    for label, stop, count in (
        ("midnight", datetime(2026, 10, 15, tzinfo=UTC), 0),
        ("noon", datetime(2026, 10, 15, 12, tzinfo=UTC), 1),
    ):
        result.append(
            DisplayCase(
                f"B07-window-stop-{label}",
                snapshot(),
                (rent(valid_until=stop),),
                expected={"details.next_obligations.next_count": count},
            )
        )
    start_at_noon = datetime(2026, 10, 15, 12, tzinfo=UTC)
    result.append(
        DisplayCase(
            "B07-window-start-noon",
            snapshot(),
            (rent(valid_from=start_at_noon),),
            expected={"details.next_obligations.next_due_date": "2026-10-15"},
        )
    )
    future = policy(
        30,
        {"type": "emergency_buffer", "amount_cents": 900_000},
        valid_from=NOW + timedelta(days=10),
    )
    result.append(
        DisplayCase(
            "B08-current-versus-future-minimum",
            snapshot(1_000_000),
            (future,),
            expected={
                "details.current_protection.value.total_cents": 0,
                "details.current_protection.value.margin_cents": 1_000_000,
                "boundary.safe_idle_cents": 100_000,
                "boundary.minimum_margin_cents": 100_000,
            },
        )
    )
    goal = GoalOwnership(
        goal_id=UUID(int=71),
        policy_id=UUID(int=70),
        account_id=UUID(int=1),
        cash_owned_cents=100_000,
        principal_owned_cents=60_000,
        allocated_cents=160_000,
        evidence_ids=[UUID(int=801)],
    )
    base = snapshot(
        150_000,
        goals=[goal],
        unassigned_goal_cash=[
            UnassignedGoalCash(
                account_id=UUID(int=3),
                amount_cents=20_000,
            )
        ],
    )
    base = base.model_copy(
        update={
            "cash_accounts": [
                *base.cash_accounts,
                CashFact(
                    account_id=UUID(int=3),
                    account_type="GOAL",
                    balance_cents=20_000,
                    observed_at=NOW,
                ),
            ]
        }
    )
    result.append(
        DisplayCase(
            "B09-current-owned-principal",
            base,
            positions=(
                BoundaryPosition(
                    position_id=UUID(int=72),
                    goal_id=goal.goal_id,
                    principal_cents=60_000,
                    status="MATURED",
                    principal_available_at=NOW + timedelta(days=5),
                ),
            ),
            expected={
                "details.current_goal_ownership.cash_owned_cents": 100_000,
                "details.current_goal_ownership.principal_owned_cents": 60_000,
                "details.current_goal_ownership.allocated_cents": 160_000,
                "details.current_goal_ownership.unassigned_goal_cash_cents": 20_000,
                "details.current_protection.value.amounts_by_reason.goal_cash": 120_000,
                "boundary.safe_idle_cents": 50_000,
            },
        )
    )
    target = policy(
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
    goal = GoalOwnership(
        goal_id=UUID(int=71),
        policy_id=target.policy_id,
        cash_owned_cents=100_000,
        principal_owned_cents=0,
        allocated_cents=100_000,
    )
    for label, contributions in (
        ("missing", []),
        (
            "actual",
            [
                GoalMonthFact(
                    goal_id=goal.goal_id,
                    period="2026-10",
                    contributed_cents=0,
                )
            ],
        ),
    ):
        result.append(
            DisplayCase(
                f"B10-goal-contribution-{label}",
                snapshot(1_000_000, goals=[goal], goal_month_contributions=contributions),
                (target,),
                expected={
                    "details.current_goal_ownership.status": "NOT_PROVEN"
                    if not contributions
                    else "PROVEN"
                },
            )
        )
    result.append(
        DisplayCase(
            "B11-proved-empty",
            snapshot(),
            expected={
                "details.next_obligations.status": "PROVEN",
                "details.next_obligations.next_count": 0,
                "details.next_obligations.next_remaining_protection_cents": 0,
                "details.next_obligations.next_due_date": None,
                "details.next_obligations.items_complete": True,
            },
        )
    )
    result.append(
        DisplayCase(
            "B11-mixed-next-group",
            snapshot(bills=[bill(10, date(2026, 10, 15), total=1_000)]),
            (ranged,),
            expected={
                "details.next_obligations.next_count": 2,
                "details.next_obligations.next_remaining_protection_cents": 6_000,
                "details.next_obligations.basis_summary": "MIXED",
                "details.next_obligations.items.1.paid_cents": None,
                "details.next_obligations.items.1.payment_fact": "NO_IMPORT_CURRENT_OR_FUTURE",
            },
        )
    )
    result.append(
        DisplayCase(
            "B11-full-group-before-truncation",
            snapshot(
                40_000, bills=[bill(i, date(2026, 10, 15), total=1_000) for i in range(100, 121)]
            ),
            expected={
                "details.next_obligations.next_count": 21,
                "details.next_obligations.next_remaining_protection_cents": 21_000,
                "details.next_obligations.items_complete": False,
            },
        )
    )
    result.append(
        DisplayCase(
            "B11-signed-current-margin",
            snapshot(
                1_000,
                bills=[
                    bill(10, date(2026, 10, 15), total=2_000),
                ],
            ),
            expected={
                "boundary.status": "LIQUIDITY_RISK",
                "details.current_protection.value.margin_cents": -1_000,
                "boundary.safe_idle_cents": 0,
            },
        )
    )
    return result
