"""Strict remaining-cash protection; this module never authorizes execution."""

import json
from calendar import monthrange
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

from app.domain.boundary_types import (
    BlockingConstraint,
    BoundaryPoint,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration

ALGORITHM_VERSION = "strict-cash-boundary-v1"
_AUDIT_FIELDS = {
    "evidence_ids",
    "availability_evidence_ids",
    "source_digest",
    "estimation_input_digest",
    "terms_digest",
    "content_hash",
}


def _financial(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _financial(child) for key, child in value.items() if key not in _AUDIT_FIELDS}
    if isinstance(value, list):
        return sorted(
            (_financial(child) for child in value),
            key=lambda child: json.dumps(child, sort_keys=True),
        )
    return value


def _active(policy: BoundaryPolicyVersion, day: date, zone: timezone) -> bool:
    start = datetime.combine(day, time.min, zone)
    end = start + timedelta(days=1)
    return policy.valid_from < end and (policy.valid_until is None or policy.valid_until > start)


def _months(start: date, end: date) -> list[date]:
    count = (end.year - start.year) * 12 + end.month - start.month + 1
    if count > 120:
        raise ValueError("At most 120 historical policy months are supported")
    return [
        date(start.year + (start.month - 1 + offset) // 12, (start.month - 1 + offset) % 12 + 1, 1)
        for offset in range(max(0, count))
    ]


def _unique(values: Sequence[Any], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {name} identities are not permitted")


def _insufficient(
    financial_hash: str,
    products: Sequence[BoundaryProduct],
    blockers: list[BlockingConstraint],
    notes: list[str],
) -> BoundaryResult:
    return BoundaryResult(
        algorithm_version=ALGORITHM_VERSION,
        status="INSUFFICIENT_EVIDENCE",
        safe_idle_cents=None,
        minimum_margin_cents=None,
        deficit_cents=None,
        protected_cents_by_reason={},
        max_allocatable_by_product={
            str(p.product_id): None for p in sorted(products, key=lambda p: p.product_id)
        },
        blocking_constraints=sorted(
            blockers, key=lambda b: (b.code, b.entity_id or "", str(b.date))
        ),
        calculation_trace=[],
        boundary_hash=financial_hash,
        calculation_notes=sorted(notes),
    )


def compute_boundary(
    snapshot: BoundarySnapshot,
    active_policy_versions: Sequence[BoundaryPolicyVersion],
    positions: Sequence[BoundaryPosition],
    products: Sequence[BoundaryProduct],
) -> BoundaryResult:
    """Return financial necessary conditions across today and ninety following dates."""
    snapshot = BoundarySnapshot.model_validate(snapshot.model_dump())
    if len(active_policy_versions) > 100 or len(positions) > 10000 or len(products) > 100:
        raise ValueError("Boundary capacity is 100 policies, 10000 positions and 100 products")
    active_policy_versions = sorted(
        (BoundaryPolicyVersion.model_validate(p.model_dump()) for p in active_policy_versions),
        key=lambda p: p.policy_id,
    )
    positions = sorted(
        (BoundaryPosition.model_validate(p.model_dump()) for p in positions),
        key=lambda p: p.position_id,
    )
    products = sorted(
        (BoundaryProduct.model_validate(p.model_dump()) for p in products),
        key=lambda p: p.product_id,
    )
    for values, name in (
        ([a.account_id for a in snapshot.cash_accounts], "account"),
        ([p.policy_id for p in active_policy_versions], "policy"),
        ([p.version_id for p in active_policy_versions], "version"),
        ([b.bill_id for b in snapshot.bills], "bill"),
        ([(s.policy_id, s.period) for s in snapshot.occurrence_settlements], "settlement"),
        ([g.goal_id for g in snapshot.goals], "goal"),
        ([g.policy_id for g in snapshot.goals], "goal policy"),
        ([(g.goal_id, g.period) for g in snapshot.goal_month_contributions], "goal contribution"),
        ([p.product_id for p in products], "product"),
        ([p.position_id for p in positions], "position"),
        ([item.policy_version_id for item in snapshot.living_reserves], "living estimate"),
        ([g.account_id for g in snapshot.unassigned_goal_cash], "unassigned goal account"),
    ):
        _unique(values, name)
    financial_hash = configuration_hash(
        {
            "algorithm": ALGORITHM_VERSION,
            "snapshot": _financial(snapshot.model_dump(mode="json")),
            "policies": _financial(
                [item.model_dump(mode="json") for item in active_policy_versions]
            ),
            "positions": _financial([item.model_dump(mode="json") for item in positions]),
            "products": _financial([item.model_dump(mode="json") for item in products]),
        }
    )
    zone = UTC if snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    try:
        first = snapshot.as_of.astimezone(zone).date()
        last = first + timedelta(days=91) - timedelta(days=1)
    except (ValueError, OverflowError) as error:
        raise ValueError("Boundary date interval is outside supported calendar dates") from error
    cash = sum(
        item.balance_cents for item in snapshot.cash_accounts if item.account_type != "CREDIT_CARD"
    )
    configs = {
        item.policy_id: validate_configuration(item.configuration)
        for item in active_policy_versions
    }
    for policy in active_policy_versions:
        if configuration_hash(configs[policy.policy_id]) != policy.content_hash:
            raise ValueError("Policy configuration hash does not match")
        if policy.valid_until is not None and policy.valid_until < policy.valid_from:
            raise ValueError("Policy validity window is reversed")
    for bill in snapshot.bills:
        if bill.paid_cents > bill.total_cents or bill.due_date < bill.statement_date:
            raise ValueError("Bill dates or paid amount are inconsistent")
        if (bill.status == "PAID") != (bill.paid_cents == bill.total_cents):
            raise ValueError("Bill status and paid amount are inconsistent")
        if (bill.status == "UNPAID" and bill.paid_cents != 0) or (
            bill.status == "PARTIALLY_PAID" and not 0 < bill.paid_cents < bill.total_cents
        ):
            raise ValueError("Bill partial-payment status is inconsistent")
    if any(item.settled_at > snapshot.as_of for item in snapshot.occurrence_settlements):
        raise ValueError("Future settlements are not completed facts")
    if any(item.period > first.strftime("%Y-%m") for item in snapshot.goal_month_contributions):
        raise ValueError("Future goal contributions are not completed facts")
    accounts = {a.account_id: a for a in snapshot.cash_accounts}
    for bill in snapshot.bills:
        if (
            bill.account_id not in accounts
            or accounts[bill.account_id].account_type != "CREDIT_CARD"
        ):
            raise ValueError("Bill must reference an included credit card account")
    goal_cash_by_account: dict[Any, int] = {}
    for goal in snapshot.goals:
        principal = sum(
            p.principal_cents
            for p in positions
            if p.goal_id == goal.goal_id and p.status != "REDEEMED"
        )
        if principal != goal.principal_owned_cents:
            raise ValueError("Goal principal ownership does not match positions")
        if goal.account_id is not None:
            if (
                goal.account_id not in accounts
                or accounts[goal.account_id].account_type == "CREDIT_CARD"
            ):
                raise ValueError("Goal cash account is missing or not cash")
            goal_cash_by_account[goal.account_id] = (
                goal_cash_by_account.get(goal.account_id, 0) + goal.cash_owned_cents
            )
    for residual in snapshot.unassigned_goal_cash:
        if (
            residual.account_id not in accounts
            or accounts[residual.account_id].account_type != "GOAL"
        ):
            raise ValueError("Unassigned goal cash must belong to a GOAL account")
        goal_cash_by_account[residual.account_id] = (
            goal_cash_by_account.get(residual.account_id, 0) + residual.amount_cents
        )
    if (
        sum(g.cash_owned_cents for g in snapshot.goals)
        + sum(g.amount_cents for g in snapshot.unassigned_goal_cash)
        > cash
    ):
        raise ValueError("Owned goal cash exceeds actual cash")
    if any(
        amount > accounts[identifier].balance_cents
        for identifier, amount in goal_cash_by_account.items()
    ):
        raise ValueError("Owned goal cash exceeds its account balance")
    life = {item.policy_version_id: item.amount_cents for item in snapshot.living_reserves}
    obligations = {
        f"bill:{item.bill_id}": (max(first, item.due_date), item.total_cents - item.paid_cents)
        for item in snapshot.bills
        if item.due_date <= last and item.total_cents > item.paid_cents
    }
    notes: list[str] = []
    settlements = {
        (item.policy_id, item.period): item.paid_cents for item in snapshot.occurrence_settlements
    }
    goal_minimum: dict[Any, int] = {}
    ownership = {item.policy_id: item for item in snapshot.goals}
    contributions = {
        (item.goal_id, item.period): item.contributed_cents
        for item in snapshot.goal_month_contributions
    }
    evidence_blockers = [
        BlockingConstraint(code=item.code, entity_id=item.entity_id)
        for item in snapshot.source_issues
    ]
    if not snapshot.cash_accounts:
        evidence_blockers.append(BlockingConstraint(code="MISSING_CASH_ACCOUNTS"))
    for account in snapshot.cash_accounts:
        if account.observed_at > snapshot.as_of:
            evidence_blockers.append(
                BlockingConstraint(code="FUTURE_CASH_FACT", entity_id=str(account.account_id))
            )
        if (
            account.account_type == "GOAL"
            and goal_cash_by_account.get(account.account_id, 0) != account.balance_cents
        ):
            evidence_blockers.append(
                BlockingConstraint(
                    code="UNASSIGNED_GOAL_CASH_NOT_ACCOUNTED", entity_id=str(account.account_id)
                )
            )
    for bill in snapshot.bills:
        if bill.statement_date > first:
            evidence_blockers.append(
                BlockingConstraint(code="FUTURE_BILL_FACT", entity_id=str(bill.bill_id))
            )
    for policy in active_policy_versions:
        if policy.confirmed_at > snapshot.as_of:
            evidence_blockers.append(
                BlockingConstraint(
                    code="FUTURE_POLICY_CONFIRMATION", entity_id=str(policy.version_id)
                )
            )
    for position in positions:
        if position.status == "UNKNOWN":
            evidence_blockers.append(
                BlockingConstraint(code="UNKNOWN_POSITION", entity_id=str(position.position_id))
            )
        if position.goal_id is not None and position.goal_id not in {
            g.goal_id for g in snapshot.goals
        }:
            evidence_blockers.append(
                BlockingConstraint(code="MISSING_GOAL_OWNERSHIP", entity_id=str(position.goal_id))
            )
    for policy in active_policy_versions:
        config = configs[policy.policy_id]
        if config["type"] != "recurring_obligation" and not any(
            _active(policy, first + timedelta(days=i), zone) for i in range(91)
        ):
            continue
        if config["type"] == "living_reserve" and policy.version_id not in life:
            evidence_blockers.append(
                BlockingConstraint(code="MISSING_LIVING_ESTIMATE", entity_id=str(policy.version_id))
            )
            continue
        if config["type"] == "recurring_obligation":
            rule = config["amount_rule"]
            if rule["kind"] == "bill_balance":
                notes.append(
                    f"UNISSUED_BILL_NOT_AN_EXISTING_LIABILITY:{policy.policy_id}:新账单到达须重新计算"
                )
                continue
            amount = rule["amount_cents"] if rule["kind"] == "exact" else rule["max_cents"]
            earliest = max(policy.confirmed_at, policy.valid_from).astimezone(zone).date()
            for month in _months(earliest, last):
                due = month.replace(
                    day=min(config["due_day"], monthrange(month.year, month.month)[1])
                )
                if due < earliest or due > last or not _active(policy, due, zone):
                    continue
                period = month.strftime("%Y-%m")
                key = f"policy:{policy.policy_id}:{period}"
                if due < first and (policy.policy_id, period) not in settlements:
                    evidence_blockers.append(
                        BlockingConstraint(code="MISSING_OCCURRENCE_SETTLEMENT", entity_id=key)
                    )
                paid = settlements.get((policy.policy_id, period), 0)
                if paid > amount:
                    raise ValueError("Occurrence settlement exceeds its configured obligation")
                if amount > paid:
                    obligations[key] = (max(first, due), amount - paid)
        elif config["type"] == "goal_saving":
            if policy.policy_id not in ownership:
                evidence_blockers.append(
                    BlockingConstraint(
                        code="MISSING_GOAL_OWNERSHIP", entity_id=str(policy.policy_id)
                    )
                )
                continue
            goal = ownership[policy.policy_id]
            deadline = date.fromisoformat(config["deadline"])
            month_min = config["monthly_contribution"]["min_cents"]
            total = 0
            for month in _months(first, min(last, deadline)):
                month_end = month.replace(day=monthrange(month.year, month.month)[1])
                start = max(first, month)
                end = min(last, deadline, month_end)
                if start > end or not any(
                    _active(policy, start + timedelta(days=i), zone)
                    for i in range((end - start).days + 1)
                ):
                    continue
                current_month = month.year == first.year and month.month == first.month
                if current_month and (goal.goal_id, first.strftime("%Y-%m")) not in contributions:
                    evidence_blockers.append(
                        BlockingConstraint(
                            code="MISSING_GOAL_MONTH_CONTRIBUTION", entity_id=str(goal.goal_id)
                        )
                    )
                contributed = (
                    contributions.get((goal.goal_id, first.strftime("%Y-%m")), 0)
                    if current_month
                    else 0
                )
                total += max(0, month_min - contributed)
            shortfall = max(0, config["priority"]["minimum_cents"] - goal.allocated_cents)
            goal_minimum[policy.policy_id] = min(
                max(0, config["target_cents"] - goal.allocated_cents), max(total, shortfall)
            )
    if evidence_blockers:
        return _insufficient(financial_hash, products, evidence_blockers, notes)
    trace: list[BoundaryPoint] = []
    goal_cash = sum(item.cash_owned_cents for item in snapshot.goals) + sum(
        item.amount_cents for item in snapshot.unassigned_goal_cash
    )
    for day in range(91):
        current = first + timedelta(days=day)
        protected = {
            "obligations": sum(amount for _, amount in obligations.values()),
            "living": sum(
                life[item.version_id]
                for item in active_policy_versions
                if configs[item.policy_id]["type"] == "living_reserve"
                and _active(item, current, zone)
            ),
            "emergency": sum(
                configs[item.policy_id]["amount_cents"]
                for item in active_policy_versions
                if configs[item.policy_id]["type"] == "emergency_buffer"
                and _active(item, current, zone)
            ),
            "goal_cash": goal_cash,
            "goal_minimum": sum(
                goal_minimum.get(item.policy_id, 0)
                for item in active_policy_versions
                if _active(item, current, zone)
            ),
        }
        trace.append(
            BoundaryPoint(
                day=day,
                date=current,
                phase="BEFORE_PAYMENT",
                cash_cents=cash,
                protected_cents_by_reason=dict(protected),
                margin_cents=cash - sum(protected.values()),
                obligation_occurrence_ids=sorted(obligations),
                principal_position_ids=[],
            )
        )
        payable = [key for key, (due, _) in obligations.items() if due == current]
        for key in payable:
            cash -= obligations.pop(key)[1]
        protected["obligations"] = sum(amount for _, amount in obligations.values())
        for phase in ("AFTER_PAYMENT", "AFTER_PRINCIPAL"):
            arriving = []
            if phase == "AFTER_PRINCIPAL":
                arriving = [
                    item
                    for item in positions
                    if item.status not in {"REDEEMED", "UNKNOWN"}
                    and item.principal_available_at is not None
                    and item.principal_available_at > snapshot.as_of
                    and item.principal_available_at.astimezone(zone).date() == current
                ]
                cash += sum(item.principal_cents for item in arriving)
                goal_cash += sum(
                    item.principal_cents for item in arriving if item.goal_id is not None
                )
                protected["goal_cash"] = goal_cash
            trace.append(
                BoundaryPoint(
                    day=day,
                    date=current,
                    phase=phase,
                    cash_cents=cash,
                    protected_cents_by_reason=dict(protected),
                    margin_cents=cash - sum(protected.values()),
                    obligation_occurrence_ids=sorted(obligations),
                    principal_position_ids=sorted(item.position_id for item in arriving),
                )
            )
    worst = min(trace, key=lambda item: item.margin_cents)
    margin = worst.margin_cents
    blockers = (
        []
        if margin >= 0
        else [
            BlockingConstraint(
                code="CASH_DEFICIT",
                date=worst.date,
                required_cents=sum(worst.protected_cents_by_reason.values()),
                available_cents=worst.cash_cents,
            )
        ]
    )
    product_caps: dict[str, int | None] = {}
    for product in products:
        return_day = (
            product.fixed_return.term_days + product.fixed_return.settlement_delay_days
            if product.fixed_return
            else 91
        )
        occupying = [
            point.margin_cents
            for point in trace
            if point.day < return_day
            or (point.day == return_day and point.phase != "AFTER_PRINCIPAL")
        ]
        product_caps[str(product.product_id)] = max(0, min(occupying)) if margin >= 0 else 0
    return BoundaryResult(
        algorithm_version=ALGORITHM_VERSION,
        status="READY" if margin >= 0 else "LIQUIDITY_RISK",
        safe_idle_cents=max(0, margin),
        minimum_margin_cents=margin,
        deficit_cents=max(0, -margin),
        protected_cents_by_reason=trace[0].protected_cents_by_reason,
        max_allocatable_by_product=product_caps,
        blocking_constraints=blockers,
        calculation_trace=trace,
        boundary_hash=financial_hash,
        calculation_notes=sorted(notes),
    )
