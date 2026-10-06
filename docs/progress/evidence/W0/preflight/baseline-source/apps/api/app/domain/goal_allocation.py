"""Deterministic single-goal previews; source provenance is verified by the adapter."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal, Self
from uuid import UUID

from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BoundaryModel,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
    SourceIssue,
)
from app.domain.income_ledger import location_id
from app.domain.policy_configuration import MoneyCents, configuration_hash, validate_configuration
from pydantic import Field, model_validator

ALGORITHM_VERSION = "single-goal-allocation-v1"
AllocationStatus = Literal[
    "READY", "MINIMUM_SHORTFALL", "LIQUIDITY_RISK", "INSUFFICIENT_EVIDENCE", "INACTIVE_POLICY"
]


class IncomeLot(BoundaryModel):
    origin_transaction_id: UUID
    account_id: UUID
    fragment_id: UUID | None = None
    amount_cents: MoneyCents
    available_cents: MoneyCents
    occurred_at: datetime
    observed_at: datetime
    direction: Literal["CREDIT"] = "CREDIT"
    source_kind: Literal["BANK_CONFIRMED"] = "BANK_CONFIRMED"
    economic_role: Literal["INCOME"] = "INCOME"
    evidence_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def available_is_part_of_origin(self) -> Self:
        expected = location_id(self.origin_transaction_id, self.account_id)
        if self.fragment_id is not None and self.fragment_id != expected:
            raise ValueError("Income fragment must bind its origin and current account")
        if self.amount_cents == 0 or self.available_cents > self.amount_cents:
            raise ValueError("Income origin must be positive and available cannot exceed original")
        if self.observed_at < self.occurred_at:
            raise ValueError("Income cannot be observed before it occurred")
        return self


class LotAllocation(BoundaryModel):
    origin_transaction_id: UUID
    account_id: UUID
    fragment_id: UUID
    amount_cents: MoneyCents
    remaining_available_cents: MoneyCents


class GoalAllocationResult(BoundaryModel):
    algorithm_version: str = ALGORITHM_VERSION
    goal_id: UUID
    policy_version_id: UUID
    status: AllocationStatus
    financial_only: Literal[True] = True
    preview_only: Literal[True] = True
    suggested_cents: MoneyCents | None = None
    max_safe_cents: MoneyCents | None = None
    remaining_min_cents: MoneyCents | None = None
    remaining_target_cents: MoneyCents | None = None
    remaining_max_cents: MoneyCents | None = None
    eligible_new_funds_cents: MoneyCents | None = None
    minimum_shortfall_cents: MoneyCents | None = None
    lot_allocations: list[LotAllocation] = Field(default_factory=list)
    baseline_boundary: BoundaryResult
    candidate_boundary: BoundaryResult | None = None
    allocation_hash: str
    reasons: list[str] = Field(default_factory=list)


def plan_goal_allocation(
    goal_id: UUID,
    policy_version_id: UUID,
    snapshot: BoundarySnapshot,
    active_policy_versions: Sequence[BoundaryPolicyVersion],
    positions: Sequence[BoundaryPosition],
    products: Sequence[BoundaryProduct],
    lots: Sequence[IncomeLot],
    *,
    source_issues: Sequence[SourceIssue] = (),
) -> GoalAllocationResult:
    """Preview one goal with the complete financial context; never consume an origin."""
    if not isinstance(goal_id, UUID) or not isinstance(policy_version_id, UUID):
        raise ValueError("Goal and version identifiers must be UUID values")
    if len(lots) > 10000 or len(source_issues) > 1000:
        raise ValueError("Allocation capacity is 10000 income lots and 1000 source issues")
    snapshot = BoundarySnapshot.model_validate(snapshot.model_dump())
    lots = sorted(
        (IncomeLot.model_validate(lot.model_dump(warnings=False)) for lot in lots),
        key=lambda lot: (lot.occurred_at, lot.origin_transaction_id, lot.account_id),
    )
    source_issues = [SourceIssue.model_validate(issue.model_dump()) for issue in source_issues]
    baseline = compute_boundary(snapshot, active_policy_versions, positions, products)
    _validate_lots(snapshot, lots)
    input_hash = _allocation_hash(goal_id, policy_version_id, baseline, lots, source_issues)

    def blocked(status: AllocationStatus, reasons: list[str]) -> GoalAllocationResult:
        return GoalAllocationResult(
            goal_id=goal_id,
            policy_version_id=policy_version_id,
            status=status,
            baseline_boundary=baseline,
            allocation_hash=input_hash,
            reasons=sorted(set(reasons)),
        )

    if source_issues:
        return blocked("INSUFFICIENT_EVIDENCE", [issue.code for issue in source_issues])
    if baseline.status != "READY":
        return blocked(baseline.status, ["BASELINE_" + baseline.status])
    if any(g.cash_owned_cents and g.account_id is None for g in snapshot.goals):
        return blocked("INSUFFICIENT_EVIDENCE", ["MISSING_GOAL_CASH_ACCOUNT_MAPPING"])
    if any(lot.observed_at > snapshot.as_of for lot in lots):
        return blocked("INSUFFICIENT_EVIDENCE", ["FUTURE_INCOME_FACT"])
    observed_balances = {
        account.account_id: account.observed_at for account in snapshot.cash_accounts
    }
    if any(lot.occurred_at > observed_balances[lot.account_id] for lot in lots):
        return blocked("INSUFFICIENT_EVIDENCE", ["INCOME_NOT_COVERED_BY_CASH_BALANCE"])
    policy = next((p for p in active_policy_versions if p.version_id == policy_version_id), None)
    goal = next((g for g in snapshot.goals if g.goal_id == goal_id), None)
    if goal is None:
        return blocked("INSUFFICIENT_EVIDENCE", ["MISSING_GOAL_OWNERSHIP"])
    if policy is None or policy.policy_id != goal.policy_id:
        return blocked("INACTIVE_POLICY", ["EXACT_GOAL_POLICY_VERSION_REQUIRED"])
    if (
        snapshot.as_of < max(policy.confirmed_at, policy.valid_from)
        or policy.valid_until is not None
        and snapshot.as_of >= policy.valid_until
    ):
        return blocked("INACTIVE_POLICY", ["GOAL_POLICY_NOT_EFFECTIVE_NOW"])
    config = validate_configuration(policy.configuration)
    if config["type"] != "goal_saving":
        return blocked("INACTIVE_POLICY", ["GOAL_POLICY_TYPE_REQUIRED"])
    zone = UTC if snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    if snapshot.as_of.astimezone(zone).date() > date.fromisoformat(config["deadline"]):
        return blocked("INACTIVE_POLICY", ["GOAL_DEADLINE_PASSED"])
    destination = next((a for a in snapshot.cash_accounts if a.account_id == goal.account_id), None)
    if destination is None or destination.account_type not in {"CASH", "GOAL"}:
        return blocked("INSUFFICIENT_EVIDENCE", ["MISSING_CASH_GOAL_DESTINATION"])
    period = _period(snapshot)
    contributed = next(
        (
            c.contributed_cents
            for c in snapshot.goal_month_contributions
            if c.goal_id == goal_id and c.period == period
        ),
        None,
    )
    if contributed is None:
        return blocked("INSUFFICIENT_EVIDENCE", ["MISSING_CURRENT_GOAL_CONTRIBUTION"])
    total_remaining = max(0, config["target_cents"] - goal.allocated_cents)
    monthly = config["monthly_contribution"]
    minimum, target, maximum = (
        min(total_remaining, max(0, monthly[f"{key}_cents"] - contributed))
        for key in ("min", "target", "max")
    )
    eligible_lots = [
        lot for lot in lots if lot.occurred_at >= max(policy.confirmed_at, policy.valid_from)
    ]
    eligible = sum(lot.available_cents for lot in eligible_lots)
    lower, upper = 0, min(target, maximum, total_remaining, eligible)
    while lower < upper:
        trial = (lower + upper + 1) // 2
        projected, _ = _project(snapshot, goal_id, trial, eligible_lots)
        checked = compute_boundary(projected, active_policy_versions, positions, products)
        if checked.status == "READY":
            lower = trial
        else:
            upper = trial - 1
    maximum_safe = lower
    minimum_shortfall = max(0, minimum - maximum_safe)
    amount = 0 if minimum_shortfall else maximum_safe
    candidate_snapshot, allocations = _project(snapshot, goal_id, amount, eligible_lots)
    candidate = compute_boundary(candidate_snapshot, active_policy_versions, positions, products)
    return GoalAllocationResult(
        goal_id=goal_id,
        policy_version_id=policy_version_id,
        status="MINIMUM_SHORTFALL" if minimum_shortfall else "READY",
        suggested_cents=amount,
        max_safe_cents=maximum_safe,
        remaining_min_cents=minimum,
        remaining_target_cents=target,
        remaining_max_cents=maximum,
        eligible_new_funds_cents=eligible,
        minimum_shortfall_cents=minimum_shortfall,
        lot_allocations=allocations,
        baseline_boundary=baseline,
        candidate_boundary=candidate,
        allocation_hash=configuration_hash(
            {
                "input_hash": input_hash,
                "suggested_cents": amount,
                "max_safe_cents": maximum_safe,
                "minimum_shortfall_cents": minimum_shortfall,
                "candidate_boundary_hash": candidate.boundary_hash,
                "lot_allocations": [
                    allocation.model_dump(mode="json") for allocation in allocations
                ],
            }
        ),
        reasons=[
            "MINIMUM_SHORTFALL" if minimum_shortfall else "TARGET_CUMULATIVE_MONTHLY_CONTRIBUTION",
            "PREVIEW_DOES_NOT_CONSUME_INCOME",
        ],
    )


def _validate_lots(snapshot: BoundarySnapshot, lots: Sequence[IncomeLot]) -> None:
    identities = [
        lot.fragment_id or location_id(lot.origin_transaction_id, lot.account_id) for lot in lots
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate income location identities are not permitted")
    origins: dict[UUID, IncomeLot] = {}
    origin_totals: dict[UUID, int] = {}
    for lot in lots:
        original = origins.setdefault(lot.origin_transaction_id, lot)
        if (original.amount_cents, original.occurred_at, original.observed_at) != (
            lot.amount_cents,
            lot.occurred_at,
            lot.observed_at,
        ):
            raise ValueError("Fragments cannot change their immutable original income facts")
        origin_totals[lot.origin_transaction_id] = (
            origin_totals.get(lot.origin_transaction_id, 0) + lot.available_cents
        )
        if origin_totals[lot.origin_transaction_id] > original.amount_cents:
            raise ValueError("Available locations cannot duplicate the original income")
    accounts = {account.account_id: account for account in snapshot.cash_accounts}
    totals: dict[UUID, int] = {}
    owned: dict[UUID, int] = {}
    for goal in snapshot.goals:
        if goal.account_id is not None:
            owned[goal.account_id] = owned.get(goal.account_id, 0) + goal.cash_owned_cents
    for lot in lots:
        account = accounts.get(lot.account_id)
        if account is None or account.account_type != "CASH":
            raise ValueError("Every income lot must reference an included CASH account")
        totals[lot.account_id] = totals.get(lot.account_id, 0) + lot.available_cents
    if any(
        amount > accounts[identity].balance_cents - owned.get(identity, 0)
        for identity, amount in totals.items()
    ):
        raise ValueError("All available origin lots must fit their account's unowned cash")


def _period(snapshot: BoundarySnapshot) -> str:
    zone = UTC if snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    return snapshot.as_of.astimezone(zone).strftime("%Y-%m")


def _allocation_hash(
    goal_id: UUID,
    policy_version_id: UUID,
    baseline: BoundaryResult,
    lots: Sequence[IncomeLot],
    issues: Sequence[SourceIssue],
) -> str:
    def lot_json(lot: IncomeLot) -> dict[str, Any]:
        value = lot.model_dump(mode="json")
        value["evidence_ids"] = sorted(value["evidence_ids"])
        return value

    return configuration_hash(
        {
            "algorithm": ALGORITHM_VERSION,
            "goal_id": str(goal_id),
            "policy_version_id": str(policy_version_id),
            "boundary_hash": baseline.boundary_hash,
            "lots": [
                lot_json(lot)
                for lot in sorted(
                    lots, key=lambda item: (item.origin_transaction_id, item.account_id)
                )
            ],
            "source_issues": sorted(
                (issue.model_dump(mode="json") for issue in issues),
                key=lambda issue: (issue["code"], issue["entity_type"], issue["entity_id"] or ""),
            ),
        }
    )


def _project(
    snapshot: BoundarySnapshot, goal_id: UUID, amount: int, lots: Sequence[IncomeLot]
) -> tuple[BoundarySnapshot, list[LotAllocation]]:
    goal = next(g for g in snapshot.goals if g.goal_id == goal_id)
    if goal.account_id is None:
        raise ValueError("Goal destination account is required")
    remaining = amount
    allocations = []
    cash_changes: dict[UUID, int] = {goal.account_id: amount}
    for lot in lots:
        used = min(remaining, lot.available_cents)
        if used:
            allocations.append(
                LotAllocation(
                    origin_transaction_id=lot.origin_transaction_id,
                    account_id=lot.account_id,
                    fragment_id=lot.fragment_id
                    or location_id(lot.origin_transaction_id, lot.account_id),
                    amount_cents=used,
                    remaining_available_cents=lot.available_cents - used,
                )
            )
            cash_changes[lot.account_id] = cash_changes.get(lot.account_id, 0) - used
            remaining -= used
    if remaining:
        raise ValueError("Candidate exceeds source lots")
    return snapshot.model_copy(
        update={
            "cash_accounts": [
                account.model_copy(
                    update={
                        "balance_cents": account.balance_cents
                        + cash_changes.get(account.account_id, 0)
                    }
                )
                for account in snapshot.cash_accounts
            ],
            "goals": [
                item.model_copy(
                    update={
                        "cash_owned_cents": item.cash_owned_cents + amount,
                        "allocated_cents": item.allocated_cents + amount,
                    }
                )
                if item.goal_id == goal_id
                else item
                for item in snapshot.goals
            ],
            "goal_month_contributions": [
                contribution.model_copy(
                    update={"contributed_cents": contribution.contributed_cents + amount}
                )
                if contribution.goal_id == goal_id and contribution.period == _period(snapshot)
                else contribution
                for contribution in snapshot.goal_month_contributions
            ],
        }
    ), allocations
