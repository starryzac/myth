"""Strict remaining-cash protection; this module never authorizes execution."""

import json
from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from app.domain.boundary_details_types import (
    BoundaryComputation,
    BoundaryDisplayDetails,
    CurrentGoalOwnership,
    CurrentProtection,
    GoalOwnershipItem,
    NextObligations,
    ObligationOccurrence,
    PaymentFact,
    ProtectionValue,
    TotalBasis,
    UnassignedGoalCashItem,
)
from app.domain.boundary_types import (
    BlockingConstraint,
    BoundaryPoint,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
)
from app.domain.policy_change_types import (
    LivingReserveChangeEstimate,
    PolicyChangeAssumption,
    PolicyChangeBoundaryComputation,
    calculate_assumption_digest,
    validated_assumption,
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


@dataclass(frozen=True)
class _FinancialPolicyParameters:
    policy_id: UUID
    source_version_id: UUID
    known_from: datetime
    valid_from: datetime
    valid_until: datetime | None
    evidence_ids: tuple[UUID, ...]
    hypothetical: bool = False


def _active(policy: _FinancialPolicyParameters, day: date, zone: timezone) -> bool:
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


@dataclass(frozen=True)
class _OccurrencePlan:
    occurrence_id: str
    kind: Literal["CREDIT_CARD_BILL", "RECURRING_ORDINARY"]
    bill_id: UUID | None
    account_id: UUID | None
    policy_id: UUID | None
    policy_version_id: UUID | None
    period: str | None
    payee_id: str | None
    due_date: date
    projection_payment_date: date
    overdue: bool
    protected_total_cents: int
    remaining_protection_cents: int
    total_basis: TotalBasis
    actual_final_total_cents: int | None
    paid_cents: int | None
    payment_fact: PaymentFact
    evidence_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class _BoundaryCore:
    boundary: BoundaryResult
    obligation_plan: tuple[_OccurrencePlan, ...]
    snapshot: BoundarySnapshot
    positions: tuple[BoundaryPosition, ...]
    first: date
    last: date


def _generated_before_change(
    version: BoundaryPolicyVersion, stop: datetime, zone: timezone
) -> bool:
    """Mirror conservative real lifecycle history detection without producing new facts."""
    if version.confirmed_at >= stop:
        return False
    configuration = validate_configuration(version.configuration)
    if configuration["type"] != "recurring_obligation" or (
        configuration["amount_rule"]["kind"] == "bill_balance"
    ):
        return False
    start = max(version.confirmed_at, version.valid_from)
    end = min(stop, version.valid_until or stop)
    if end <= start:
        return False
    first, last = start.astimezone(zone).date(), end.astimezone(zone).date()
    count = (last.year - first.year) * 12 + last.month - first.month + 1
    if count > 120:
        return True
    try:
        for month in _months(first, last):
            due = month.replace(
                day=min(configuration["due_day"], monthrange(month.year, month.month)[1])
            )
            day_start = datetime.combine(due, time.min, zone)
            if day_start < end and day_start + timedelta(days=1) > start:
                return True
    except (ValueError, OverflowError):
        return True
    return False


def _compute_boundary_core(
    snapshot: BoundarySnapshot,
    active_policy_versions: Sequence[BoundaryPolicyVersion],
    positions: Sequence[BoundaryPosition],
    products: Sequence[BoundaryProduct],
    *,
    _change: tuple[
        BoundaryPolicyVersion, PolicyChangeAssumption, LivingReserveChangeEstimate | None
    ]
    | None = None,
) -> _BoundaryCore:
    """Compute financial v1 once and retain its original obligation metadata."""
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
        last = first + timedelta(days=snapshot.horizon_days + 1) - timedelta(days=1)
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
    obligations: dict[str, tuple[date, int]] = {}
    obligation_plan: list[_OccurrencePlan] = []
    for item in snapshot.bills:
        if item.due_date <= last and item.total_cents > item.paid_cents:
            key = f"bill:{item.bill_id}"
            payment_date = max(first, item.due_date)
            remaining = item.total_cents - item.paid_cents
            obligations[key] = (payment_date, remaining)
            obligation_plan.append(
                _OccurrencePlan(
                    occurrence_id=key,
                    kind="CREDIT_CARD_BILL",
                    bill_id=item.bill_id,
                    account_id=item.account_id,
                    policy_id=None,
                    policy_version_id=None,
                    period=None,
                    payee_id=None,
                    due_date=item.due_date,
                    projection_payment_date=payment_date,
                    overdue=item.due_date < first,
                    protected_total_cents=item.total_cents,
                    remaining_protection_cents=remaining,
                    total_basis="BILL_ACTUAL",
                    actual_final_total_cents=item.total_cents,
                    paid_cents=item.paid_cents,
                    payment_fact="BILL_CONFIRMED",
                    evidence_ids=tuple(sorted(set(item.evidence_ids))),
                )
            )
    notes: list[str] = []
    settlements = {
        (item.policy_id, item.period): item.paid_cents for item in snapshot.occurrence_settlements
    }
    settlement_facts = {
        (item.policy_id, item.period): item for item in snapshot.occurrence_settlements
    }
    final_totals = {
        (item.policy_id, item.period): item.final_total_cents
        for item in snapshot.occurrence_settlements
        if item.final_total_cents is not None
    }
    for (policy_id, _), final in final_totals.items():
        config = configs.get(policy_id)
        if config is None or config["type"] != "recurring_obligation":
            raise ValueError("Final occurrence total requires its confirmed recurring policy")
        rule = config["amount_rule"]
        if rule["kind"] == "bill_balance":
            raise ValueError("Actual bills cannot also have ordinary occurrence totals")
        minimum = rule["amount_cents"] if rule["kind"] == "exact" else rule["min_cents"]
        maximum = rule["amount_cents"] if rule["kind"] == "exact" else rule["max_cents"]
        if not minimum <= final <= maximum:
            raise ValueError("Final occurrence total is outside its confirmed amount rule")
    policy_parameters = [
        _FinancialPolicyParameters(
            policy_id=policy.policy_id,
            source_version_id=policy.version_id,
            known_from=policy.confirmed_at,
            valid_from=policy.valid_from,
            valid_until=policy.valid_until,
            evidence_ids=tuple(policy.evidence_ids),
        )
        for policy in active_policy_versions
    ]
    hypothetical_blockers: list[BlockingConstraint] = []
    if _change is not None:
        source, assumption, estimate = _change
        original_config = validate_configuration(source.configuration)
        if configuration_hash(original_config) != source.content_hash:
            raise ValueError("Actual source policy configuration hash does not match")
        if (
            source.policy_id != assumption.policy_id
            or source.version_id != assumption.source_version_id
            or original_config["type"] != assumption.configuration["type"]
        ):
            raise ValueError("Hypothetical change differs from the actual source identity or type")
        if assumption.assumed_confirmation_at != snapshot.as_of or (
            assumption.timezone != snapshot.timezone
        ):
            raise ValueError("Hypothetical change must use the actual snapshot clock and timezone")
        if source.confirmed_at > snapshot.as_of:
            raise ValueError("Actual source policy confirmation is not known at the snapshot")
        if source.valid_until is not None and source.valid_until < source.valid_from:
            raise ValueError("Actual source policy window is reversed")
        if source.valid_until is not None and snapshot.as_of >= source.valid_until:
            raise ValueError("Expired actual source policies cannot be changed")
        if assumption.source_status != "SUSPENDED" and (
            (assumption.source_status == "CONFIRMED") != (source.valid_from > snapshot.as_of)
        ):
            raise ValueError("Actual source status differs from its effective window")
        included = [p for p in active_policy_versions if p.policy_id == source.policy_id]
        if included and included[0].model_dump() != source.model_dump():
            raise ValueError("Actual source differs from its supplied current boundary version")
        digest = calculate_assumption_digest(assumption, estimate)
        financial_hash = configuration_hash(
            {
                "algorithm": "policy-change-boundary-v1",
                "actual_boundary_input": financial_hash,
                "actual_source": _financial(source.model_dump(mode="json")),
                "assumption_digest": digest,
                "snapshot_source_digest": snapshot.source_digest,
            }
        )
        notes.append(f"HYPOTHETICAL_POLICY_CHANGE:{source.policy_id}:{digest}")
        ambiguous_history = _generated_before_change(source, snapshot.as_of, zone) or (
            original_config["type"] == "recurring_obligation"
            and any(item.policy_id == source.policy_id for item in snapshot.occurrence_settlements)
        )
        if ambiguous_history:
            hypothetical_blockers.append(
                BlockingConstraint(
                    code="HISTORICAL_OBLIGATION_RECONCILIATION_REQUIRED",
                    entity_id=str(source.policy_id),
                )
            )
        policy_parameters = [p for p in policy_parameters if p.policy_id != source.policy_id]
        configs = {**configs, source.policy_id: assumption.configuration}
        if assumption.effective_status not in {"SUSPENDED", "EXPIRED"} and not ambiguous_history:
            parameters = _FinancialPolicyParameters(
                policy_id=source.policy_id,
                source_version_id=source.version_id,
                known_from=assumption.assumed_confirmation_at,
                valid_from=max(assumption.assumed_valid_from, assumption.assumed_confirmation_at),
                valid_until=assumption.assumed_valid_until,
                evidence_ids=(),
                hypothetical=True,
            )
            policy_parameters.append(parameters)
            policy_parameters.sort(key=lambda p: p.policy_id)
            if assumption.configuration["type"] == "living_reserve" and any(
                _active(parameters, first + timedelta(days=i), zone)
                for i in range(snapshot.horizon_days + 1)
            ):
                life.pop(source.version_id, None)
                if estimate is not None and estimate.status == "READY":
                    assert estimate.amount_cents is not None
                    life[source.version_id] = estimate.amount_cents
                else:
                    hypothetical_blockers.append(
                        BlockingConstraint(
                            code="MISSING_HYPOTHETICAL_LIVING_ESTIMATE",
                            entity_id=str(source.version_id),
                        )
                    )
                    if estimate is not None:
                        hypothetical_blockers.extend(
                            BlockingConstraint(code=item.code, entity_id=item.entity_id)
                            for item in estimate.issues
                        )
    goal_minimum: dict[Any, int] = {}
    ownership = {item.policy_id: item for item in snapshot.goals}
    contributions = {
        (item.goal_id, item.period): item.contributed_cents
        for item in snapshot.goal_month_contributions
    }
    evidence_blockers = [
        BlockingConstraint(code=item.code, entity_id=item.entity_id)
        for item in snapshot.source_issues
    ] + hypothetical_blockers
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
    for parameters in policy_parameters:
        config = configs[parameters.policy_id]
        if config["type"] != "recurring_obligation" and not any(
            _active(parameters, first + timedelta(days=i), zone)
            for i in range(snapshot.horizon_days + 1)
        ):
            continue
        if config["type"] == "living_reserve" and parameters.source_version_id not in life:
            evidence_blockers.append(
                BlockingConstraint(
                    code="MISSING_LIVING_ESTIMATE", entity_id=str(parameters.source_version_id)
                )
            )
            continue
        if config["type"] == "recurring_obligation":
            rule = config["amount_rule"]
            if rule["kind"] == "bill_balance":
                notes.append(
                    f"UNISSUED_BILL_NOT_AN_EXISTING_LIABILITY:{parameters.policy_id}:新账单到达须重新计算"
                )
                continue
            amount = rule["amount_cents"] if rule["kind"] == "exact" else rule["max_cents"]
            earliest = max(parameters.known_from, parameters.valid_from).astimezone(zone).date()
            for month in _months(earliest, last):
                due = month.replace(
                    day=min(config["due_day"], monthrange(month.year, month.month)[1])
                )
                if due < earliest or due > last or not _active(parameters, due, zone):
                    continue
                period = month.strftime("%Y-%m")
                key = f"policy:{parameters.policy_id}:{period}"
                if due < first and (parameters.policy_id, period) not in settlements:
                    evidence_blockers.append(
                        BlockingConstraint(code="MISSING_OCCURRENCE_SETTLEMENT", entity_id=key)
                    )
                occurrence_amount = final_totals.get((parameters.policy_id, period), amount)
                paid = settlements.get((parameters.policy_id, period), 0)
                if paid > occurrence_amount:
                    raise ValueError("Occurrence settlement exceeds its configured obligation")
                if occurrence_amount > paid:
                    payment_date = max(first, due)
                    remaining = occurrence_amount - paid
                    obligations[key] = (payment_date, remaining)
                    fact = settlement_facts.get((parameters.policy_id, period))
                    total_basis: TotalBasis = (
                        "SETTLEMENT_FINAL"
                        if fact is not None and fact.final_total_cents is not None
                        else "POLICY_EXACT"
                        if rule["kind"] == "exact"
                        else "POLICY_RANGE_MAX"
                    )
                    payment_fact: PaymentFact = (
                        "SETTLEMENT_CONFIRMED"
                        if fact is not None
                        else "MISSING_HISTORICAL_IMPORT"
                        if due < first
                        else "NO_IMPORT_CURRENT_OR_FUTURE"
                    )
                    obligation_plan.append(
                        _OccurrencePlan(
                            occurrence_id=key,
                            kind="RECURRING_ORDINARY",
                            bill_id=None,
                            account_id=None,
                            policy_id=parameters.policy_id,
                            policy_version_id=(
                                None if parameters.hypothetical else parameters.source_version_id
                            ),
                            period=period,
                            payee_id=config["payee_id"],
                            due_date=due,
                            projection_payment_date=payment_date,
                            overdue=due < first,
                            protected_total_cents=occurrence_amount,
                            remaining_protection_cents=remaining,
                            total_basis=total_basis,
                            actual_final_total_cents=(
                                fact.final_total_cents if fact is not None else None
                            ),
                            paid_cents=fact.paid_cents if fact is not None else None,
                            payment_fact=payment_fact,
                            evidence_ids=tuple(
                                sorted(
                                    set(parameters.evidence_ids).union(
                                        fact.evidence_ids if fact is not None else []
                                    )
                                )
                            ),
                        )
                    )
        elif config["type"] == "goal_saving":
            if parameters.policy_id not in ownership:
                evidence_blockers.append(
                    BlockingConstraint(
                        code="MISSING_GOAL_OWNERSHIP", entity_id=str(parameters.policy_id)
                    )
                )
                continue
            goal = ownership[parameters.policy_id]
            deadline = date.fromisoformat(config["deadline"])
            month_min = config["monthly_contribution"]["min_cents"]
            total = 0
            for month in _months(first, min(last, deadline)):
                month_end = month.replace(day=monthrange(month.year, month.month)[1])
                start = max(first, month)
                end = min(last, deadline, month_end)
                if start > end or not any(
                    _active(parameters, start + timedelta(days=i), zone)
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
            goal_minimum[parameters.policy_id] = min(
                max(0, config["target_cents"] - goal.allocated_cents), max(total, shortfall)
            )
    if evidence_blockers:
        return _BoundaryCore(
            _insufficient(financial_hash, products, evidence_blockers, notes),
            tuple(obligation_plan),
            snapshot,
            tuple(positions),
            first,
            last,
        )
    trace: list[BoundaryPoint] = []
    goal_cash = sum(item.cash_owned_cents for item in snapshot.goals) + sum(
        item.amount_cents for item in snapshot.unassigned_goal_cash
    )
    for day in range(snapshot.horizon_days + 1):
        current = first + timedelta(days=day)
        protected = {
            "obligations": sum(amount for _, amount in obligations.values()),
            "living": sum(
                life[item.source_version_id]
                for item in policy_parameters
                if configs[item.policy_id]["type"] == "living_reserve"
                and _active(item, current, zone)
            ),
            "emergency": sum(
                configs[item.policy_id]["amount_cents"]
                for item in policy_parameters
                if configs[item.policy_id]["type"] == "emergency_buffer"
                and _active(item, current, zone)
            ),
            "goal_cash": goal_cash,
            "goal_minimum": sum(
                goal_minimum.get(item.policy_id, 0)
                for item in policy_parameters
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
            else snapshot.horizon_days + 1
        )
        occupying = [
            point.margin_cents
            for point in trace
            if point.day < return_day
            or (point.day == return_day and point.phase != "AFTER_PRINCIPAL")
        ]
        product_caps[str(product.product_id)] = max(0, min(occupying)) if margin >= 0 else 0
    result = BoundaryResult(
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

    return _BoundaryCore(result, tuple(obligation_plan), snapshot, tuple(positions), first, last)


def compute_boundary(
    snapshot: BoundarySnapshot,
    active_policy_versions: Sequence[BoundaryPolicyVersion],
    positions: Sequence[BoundaryPosition],
    products: Sequence[BoundaryProduct],
) -> BoundaryResult:
    """Return financial necessary conditions across today and ninety following dates."""
    return _compute_boundary_core(snapshot, active_policy_versions, positions, products).boundary


def _next_obligations(core: _BoundaryCore) -> NextObligations:
    if core.boundary.status == "INSUFFICIENT_EVIDENCE":
        return NextObligations(
            status="NOT_PROVEN",
            next_due_date=None,
            next_count=None,
            next_remaining_protection_cents=None,
            basis_summary=None,
            items=[],
            items_complete=False,
        )
    ordered = sorted(core.obligation_plan, key=lambda item: (item.due_date, item.occurrence_id))
    if not ordered:
        return NextObligations(
            status="PROVEN",
            next_due_date=None,
            next_count=0,
            next_remaining_protection_cents=0,
            basis_summary=None,
            items=[],
            items_complete=True,
        )
    due = ordered[0].due_date
    group = [item for item in ordered if item.due_date == due]
    upper_bounds = sum(item.total_basis == "POLICY_RANGE_MAX" for item in group)
    basis: Literal["EXACT", "UPPER_BOUND", "MIXED"] = (
        "EXACT" if upper_bounds == 0 else "UPPER_BOUND" if upper_bounds == len(group) else "MIXED"
    )
    return NextObligations(
        status="PROVEN",
        next_due_date=due,
        next_count=len(group),
        next_remaining_protection_cents=sum(item.remaining_protection_cents for item in group),
        basis_summary=basis,
        items=[
            ObligationOccurrence.model_validate(
                {
                    **vars(item),
                    "evidence_ids": list(item.evidence_ids),
                }
            )
            for item in group[:20]
        ],
        items_complete=len(group) <= 20,
    )


def _current_protection(core: _BoundaryCore) -> CurrentProtection:
    if core.boundary.status == "INSUFFICIENT_EVIDENCE":
        return CurrentProtection(status="NOT_PROVEN", value=None)
    point = core.boundary.calculation_trace[0]
    return CurrentProtection(
        status="PROVEN",
        value=ProtectionValue(
            date=point.date,
            amounts_by_reason=dict(point.protected_cents_by_reason),
            total_cents=sum(point.protected_cents_by_reason.values()),
            cash_cents=point.cash_cents,
            margin_cents=point.margin_cents,
        ),
    )


def _current_goal_ownership(core: _BoundaryCore) -> CurrentGoalOwnership:
    if core.boundary.status == "INSUFFICIENT_EVIDENCE":
        return CurrentGoalOwnership(
            status="NOT_PROVEN",
            items=[],
            cash_owned_cents=None,
            principal_owned_cents=None,
            allocated_cents=None,
            unassigned_goal_cash=[],
            unassigned_goal_cash_cents=None,
        )
    items = [
        GoalOwnershipItem(
            goal_id=goal.goal_id,
            policy_id=goal.policy_id,
            account_id=goal.account_id,
            cash_owned_cents=goal.cash_owned_cents,
            principal_owned_cents=goal.principal_owned_cents,
            allocated_cents=goal.allocated_cents,
            evidence_ids=sorted(set(goal.evidence_ids)),
            principal_position_ids=[
                position.position_id
                for position in core.positions
                if position.goal_id == goal.goal_id and position.status != "REDEEMED"
            ],
        )
        for goal in sorted(core.snapshot.goals, key=lambda item: item.goal_id)
    ]
    unassigned = [
        UnassignedGoalCashItem(
            account_id=item.account_id,
            amount_cents=item.amount_cents,
            evidence_ids=sorted(set(item.evidence_ids)),
        )
        for item in sorted(core.snapshot.unassigned_goal_cash, key=lambda item: item.account_id)
    ]
    return CurrentGoalOwnership(
        status="PROVEN",
        items=items,
        cash_owned_cents=sum(item.cash_owned_cents for item in items),
        principal_owned_cents=sum(item.principal_owned_cents for item in items),
        allocated_cents=sum(item.allocated_cents for item in items),
        unassigned_goal_cash=unassigned,
        unassigned_goal_cash_cents=sum(item.amount_cents for item in unassigned),
    )


def compute_boundary_with_details(
    snapshot: BoundarySnapshot,
    active_policy_versions: Sequence[BoundaryPolicyVersion],
    positions: Sequence[BoundaryPosition],
    products: Sequence[BoundaryProduct],
) -> BoundaryComputation:
    """Build independent display facts from the single validated financial v1 computation."""
    core = _compute_boundary_core(snapshot, active_policy_versions, positions, products)
    return BoundaryComputation(
        boundary=core.boundary,
        details=BoundaryDisplayDetails(
            as_of=core.snapshot.as_of,
            timezone=core.snapshot.timezone,
            window_start=core.first,
            window_end=core.last,
            input_digest=core.snapshot.source_digest,
            boundary_hash=core.boundary.boundary_hash,
            next_obligations=_next_obligations(core),
            current_protection=_current_protection(core),
            current_goal_ownership=_current_goal_ownership(core),
            blocking_constraints=list(core.boundary.blocking_constraints),
            source_issues=list(core.snapshot.source_issues),
        ),
    )


def compute_policy_change_boundary(
    snapshot: BoundarySnapshot,
    active_policy_versions: Sequence[BoundaryPolicyVersion],
    positions: Sequence[BoundaryPosition],
    products: Sequence[BoundaryProduct],
    *,
    source_version: BoundaryPolicyVersion,
    assumption: PolicyChangeAssumption,
    living_estimate: LivingReserveChangeEstimate | None = None,
) -> PolicyChangeBoundaryComputation:
    """Project server-built assumptions over actual facts, with no new version or authority."""
    # Keep the full original financial validation, including old settlement amount rules.
    # The output of this validation is never used as an authority or cached across calls.
    _compute_boundary_core(snapshot, active_policy_versions, positions, products)
    source_version = BoundaryPolicyVersion.model_validate(source_version.model_dump())
    assumption = validated_assumption(assumption)
    if living_estimate is not None:
        living_estimate = LivingReserveChangeEstimate.model_validate(living_estimate.model_dump())
    digest = calculate_assumption_digest(assumption, living_estimate)
    core = _compute_boundary_core(
        snapshot,
        active_policy_versions,
        positions,
        products,
        _change=(source_version, assumption, living_estimate),
    )
    return PolicyChangeBoundaryComputation(
        assumption_digest=digest,
        boundary=core.boundary,
        details=BoundaryDisplayDetails(
            as_of=core.snapshot.as_of,
            timezone=core.snapshot.timezone,
            window_start=core.first,
            window_end=core.last,
            input_digest=core.snapshot.source_digest,
            boundary_hash=core.boundary.boundary_hash,
            next_obligations=_next_obligations(core),
            current_protection=_current_protection(core),
            current_goal_ownership=_current_goal_ownership(core),
            blocking_constraints=list(core.boundary.blocking_constraints),
            source_issues=list(core.snapshot.source_issues),
        ),
    )
