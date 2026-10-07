"""Exact, bounded current-period goal allocation from server-verified facts.

Amounts are handled at critical breakpoints of an integral flow polytope, never
by enumerating cents. No function here writes, executes, grants permission or
uses projected income. Source references must be verified by the SQL adapter.
"""

from collections import deque
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, timezone
from fractions import Fraction
from itertools import combinations, permutations, product
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.policy_configuration import CalendarDate, MoneyCents, configuration_hash
from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    StrictInt,
    model_serializer,
    model_validator,
)

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
RepairField = Literal["monthly_min_cents", "monthly_max_cents", "deadline"]
OptimizerVersion = Literal["critical-flow-affine-box-v2"]
AFFINE_BOX_V2: OptimizerVersion = "critical-flow-affine-box-v2"


class SourceReference(BoundaryModel):
    user_id: UUID
    evidence_id: UUID
    content_hash: Hash


SourceReferences = Annotated[list[SourceReference], Field(min_length=1, max_length=1000)]


class AllocationIncomeLot(BoundaryModel):
    fragment_id: UUID
    origin_transaction_id: UUID
    account_id: UUID
    received_cents: MoneyCents
    available_cents: MoneyCents
    occurred_at: datetime
    observed_at: datetime
    owner_goal_id: Literal[None] = None
    bank_evidence_id: UUID
    bank_evidence_hash: Hash
    source_refs: SourceReferences

    @model_validator(mode="after")
    def original_funds(self) -> Self:
        if self.available_cents > self.received_cents or self.occurred_at > self.observed_at:
            raise ValueError("Available new income and observation must match original facts")
        return self


class HardProtectionPoint(BoundaryModel):
    date: CalendarDate
    cash_cents: StrictInt
    obligation_floor_cents: MoneyCents
    living_floor_cents: MoneyCents
    emergency_floor_cents: MoneyCents
    owned_goal_cash_cents: MoneyCents
    other_protection_floor_cents: MoneyCents = 0
    source_refs: SourceReferences

    @property
    def remaining_cents(self) -> int:
        return self.cash_cents - (
            self.obligation_floor_cents
            + self.living_floor_cents
            + self.emergency_floor_cents
            + self.owned_goal_cash_cents
            + self.other_protection_floor_cents
        )


class AllocationGoal(BoundaryModel):
    goal_id: UUID
    account_id: UUID
    policy_id: UUID
    effective_policy_version_id: UUID
    policy_status: Literal["ACTIVE", "CONFIRMED", "SUSPENDED", "REVOKED", "EXPIRED"]
    target_cents: Annotated[MoneyCents, Field(gt=0)]
    current_owned_cents: MoneyCents
    current_month_contributed_cents: MoneyCents
    monthly_min_cents: MoneyCents
    monthly_target_cents: MoneyCents
    monthly_max_cents: MoneyCents
    minimum_guarantee_cents: MoneyCents = 0
    importance: Annotated[StrictInt, Field(ge=0, le=100)] = 50
    deadline: CalendarDate
    allow_partial: StrictBool = False
    allow_deferral: StrictBool = False
    deferral_cost_cents_per_day: MoneyCents = 0
    confirmed_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    negotiable_fields: Annotated[list[RepairField], Field(max_length=3)] = Field(
        default_factory=list
    )
    source_refs: SourceReferences

    @model_validator(mode="after")
    def finite_contract(self) -> Self:
        if not self.monthly_min_cents <= self.monthly_target_cents <= self.monthly_max_cents:
            raise ValueError("Monthly contribution must satisfy min <= target <= max")
        if self.minimum_guarantee_cents > self.target_cents:
            raise ValueError("Minimum guarantee exceeds the target")
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("Policy validity window is reversed")
        if not self.allow_deferral and self.deferral_cost_cents_per_day:
            raise ValueError("Deferral cost requires explicit deferral permission")
        if len(set(self.negotiable_fields)) != len(self.negotiable_fields):
            raise ValueError("Negotiable fields must be unique")
        return self


class MultiGoalAllocationInput(BoundaryModel):
    schema_version: Literal["multi-goal-current-period-v1"] = "multi-goal-current-period-v1"
    user_id: UUID
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    income_lots: Annotated[list[AllocationIncomeLot], Field(max_length=128)]
    hard_protection_points: Annotated[
        list[HardProtectionPoint], Field(min_length=1, max_length=1098)
    ]
    goals: Annotated[list[AllocationGoal], Field(max_length=8)]
    source_issues: Annotated[list[str], Field(max_length=1000)] = Field(default_factory=list)
    solver_node_budget: Annotated[StrictInt, Field(ge=1, le=1_000_000)] = 200_000
    optimizer_version: OptimizerVersion | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value: dict[str, Any] = handler(self)
        if self.optimizer_version is None:
            value.pop("optimizer_version", None)
        return value

    @model_validator(mode="after")
    def original_context(self) -> Self:
        if len({lot.fragment_id for lot in self.income_lots}) != len(self.income_lots):
            raise ValueError("Income fragments must be unique")
        if len({(lot.origin_transaction_id, lot.account_id) for lot in self.income_lots}) != len(
            self.income_lots
        ):
            raise ValueError("Income origin/account locations must be unique")
        origins: dict[UUID, list[AllocationIncomeLot]] = {}
        for lot in self.income_lots:
            origins.setdefault(lot.origin_transaction_id, []).append(lot)
        for fragments in origins.values():
            first = fragments[0]
            if (
                any(
                    (
                        lot.received_cents,
                        lot.occurred_at,
                        lot.observed_at,
                        lot.bank_evidence_id,
                        lot.bank_evidence_hash,
                    )
                    != (
                        first.received_cents,
                        first.occurred_at,
                        first.observed_at,
                        first.bank_evidence_id,
                        first.bank_evidence_hash,
                    )
                    for lot in fragments
                )
                or sum(lot.available_cents for lot in fragments) > first.received_cents
            ):
                raise ValueError("All fragments must conserve the same original received income")
        if len({goal.goal_id for goal in self.goals}) != len(self.goals):
            raise ValueError("Goals must be unique")
        if len({goal.policy_id for goal in self.goals}) != len(self.goals):
            raise ValueError("A policy may not authorize two goals")
        if any(lot.observed_at > self.as_of for lot in self.income_lots):
            raise ValueError("Future or unobserved income cannot fund current allocations")
        for refs in (
            *(lot.source_refs for lot in self.income_lots),
            *(goal.source_refs for goal in self.goals),
            *(point.source_refs for point in self.hard_protection_points),
        ):
            if any(ref.user_id != self.user_id for ref in refs):
                raise ValueError("All source originals must belong to the same user")
        if any(goal.confirmed_at > self.as_of for goal in self.goals):
            raise ValueError("Future confirmations are not current authority")
        if _today(self) == date.max:
            raise ValueError("The next calendar day must exist for a censored delay lower bound")
        return self


class IncomeUse(BoundaryModel):
    fragment_id: UUID
    origin_transaction_id: UUID
    source_account_id: UUID
    goal_id: UUID
    amount_cents: Annotated[MoneyCents, Field(gt=0)]


class GoalAllocation(BoundaryModel):
    goal_id: UUID
    effective_policy_version_id: UUID
    amount_cents: MoneyCents | None
    minimum_shortfall_cents: MoneyCents | None
    projected_owned_cents: MoneyCents | None
    completion_date: date | None
    delay_lower_bound_days: StrictInt | None
    delay_censored: bool
    deferral_cost_lower_bound_cents: StrictInt | None


class MultiGoalAllocationResult(BoundaryModel):
    algorithm_version: Literal[
        "critical-flow-lexicographic-v1", "critical-flow-affine-box-v2"
    ] = "critical-flow-lexicographic-v1"
    status: Literal["OPTIMAL", "INFEASIBLE", "UNKNOWN"]
    purpose: Literal["CURRENT_PERIOD_PLANNING_ONLY"] = "CURRENT_PERIOD_PLANNING_ONLY"
    grants_authority: Literal[False] = False
    input_hash: Hash
    objective_vector: tuple[int, int, int, int, int, int, int, int] | None
    budget_cents: MoneyCents
    goals: list[GoalAllocation]
    income_uses: list[IncomeUse]
    visited_nodes: StrictInt
    reasons: list[str]
    delay_scope: Literal["ACTIVE_INCOMPLETE_GOALS_CURRENT_DECISION_LOWER_BOUND"] = (
        "ACTIVE_INCOMPLETE_GOALS_CURRENT_DECISION_LOWER_BOUND"
    )


class _CapacityExceeded(Exception):
    pass


class _Counter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def visit(self) -> None:
        if self.used >= self.limit:
            raise _CapacityExceeded
        self.used += 1


def _today(request: MultiGoalAllocationInput) -> date:
    zone = UTC if request.timezone == "UTC" else timezone(timedelta(hours=8))
    return request.as_of.astimezone(zone).date()


def _active(goal: AllocationGoal, request: MultiGoalAllocationInput) -> bool:
    return (
        goal.policy_status in {"ACTIVE", "CONFIRMED"}
        and max(goal.confirmed_at, goal.valid_from) <= request.as_of
        and (goal.valid_until is None or request.as_of < goal.valid_until)
    )


def _remaining(goal: AllocationGoal) -> int:
    return max(0, goal.target_cents - goal.current_owned_cents)


def _month_remaining(goal: AllocationGoal, kind: str) -> int:
    amount = {
        "min": goal.monthly_min_cents,
        "target": goal.monthly_target_cents,
        "max": goal.monthly_max_cents,
    }[kind]
    return min(_remaining(goal), max(0, amount - goal.current_month_contributed_cents))


def _constraint_ids(request: MultiGoalAllocationInput) -> set[str]:
    return {
        f"{kind}:{goal.goal_id}"
        for goal in request.goals
        for kind in ("MINIMUM_GUARANTEE", "DEADLINE_COMPLETION", "MONTHLY_MAX")
    }


def _bounds(
    goal: AllocationGoal, request: MultiGoalAllocationInput, enabled: set[str]
) -> tuple[int, int]:
    if not _active(goal, request):
        return 0, 0
    low = (
        max(0, goal.minimum_guarantee_cents - goal.current_owned_cents)
        if f"MINIMUM_GUARANTEE:{goal.goal_id}" in enabled
        else 0
    )
    if (
        f"DEADLINE_COMPLETION:{goal.goal_id}" in enabled
        and goal.deadline <= _today(request)
        and not goal.allow_partial
        and not goal.allow_deferral
    ):
        low = max(low, _remaining(goal))
    high = (
        _month_remaining(goal, "max")
        if f"MONTHLY_MAX:{goal.goal_id}" in enabled
        else _remaining(goal)
    )
    return low, high


def _budget(request: MultiGoalAllocationInput) -> int:
    return max(
        0,
        min(
            min(point.remaining_cents for point in request.hard_protection_points),
            sum(lot.available_cents for lot in request.income_lots),
        ),
    )


def _eligible(lot: AllocationIncomeLot, goal: AllocationGoal) -> bool:
    return lot.occurred_at >= max(goal.confirmed_at, goal.valid_from)


def _edges(
    request: MultiGoalAllocationInput, goals: list[AllocationGoal]
) -> list[tuple[UUID, int]]:
    return sorted(
        {
            (lot.account_id, index)
            for index, goal in enumerate(goals)
            for lot in request.income_lots
            if lot.available_cents and _active(goal, request) and _eligible(lot, goal)
        }
    )


def _ranks(
    request: MultiGoalAllocationInput,
    goals: list[AllocationGoal],
    edges: set[tuple[UUID, int]],
    counter: _Counter,
) -> list[int]:
    """Exact Hall capacities for every goal subset, including the immutable hard budget."""
    ranks = [0] * (1 << len(goals))
    eligible_masks = [
        sum(
            1 << index
            for index, goal in enumerate(goals)
            if (lot.account_id, index) in edges and _eligible(lot, goal)
        )
        for lot in request.income_lots
    ]
    budget = _budget(request)
    for mask in range(1, len(ranks)):
        counter.visit()
        ranks[mask] = min(
            budget,
            sum(
                lot.available_cents
                for lot, eligible_mask in zip(request.income_lots, eligible_masks, strict=True)
                if eligible_mask & mask
            ),
        )
    return ranks


def _feasible(values: tuple[int, ...], ranks: list[int]) -> bool:
    return all(
        sum(value for index, value in enumerate(values) if mask & (1 << index)) <= capacity
        for mask, capacity in enumerate(ranks)
    )


def _regions(goal: AllocationGoal, low: int, high: int) -> list[tuple[int, int]]:
    if low > high:
        return []
    # Minimum and target costs are continuous piecewise affine. Completion is
    # discontinuous, so its exact last-cent endpoint gets its own region.
    cuts = sorted(
        {
            low,
            high,
            *[
                x
                for x in (_month_remaining(goal, "min"), _month_remaining(goal, "target"))
                if low < x < high
            ],
        }
    )
    regions = list(zip(cuts, cuts[1:], strict=False)) if len(cuts) > 1 else [(low, high)]
    if high == _remaining(goal) and high > low:
        last_low, last_high = regions.pop()
        if last_low <= last_high - 1:
            regions.append((last_low, last_high - 1))
        regions.append((high, high))
    return regions


def _delay(goal: AllocationGoal, amount: int, today: date) -> tuple[int, bool, date | None]:
    if not _remaining(goal):
        # Historical completion delay is constant and is not reconstructed here.
        return 0, False, None
    if amount == _remaining(goal):
        return max(0, (today - goal.deadline).days), False, today
    # Only a lower bound is known; this is not a predicted actual completion date.
    return max(0, ((today + timedelta(days=1)) - goal.deadline).days), True, None


def _score(
    request: MultiGoalAllocationInput, goals: list[AllocationGoal], values: tuple[int, ...]
) -> tuple[int, int, int, int, int, int, int]:
    minima = [
        max(0, _month_remaining(goal, "min") - value) if _active(goal, request) else 0
        for goal, value in zip(goals, values, strict=True)
    ]
    return (
        0,
        0,
        sum(minima),
        sum(goal.importance * shortfall for goal, shortfall in zip(goals, minima, strict=True)),
        sum(
            _delay(goal, value, _today(request))[0]
            for goal, value in zip(goals, values, strict=True)
            if _active(goal, request)
        ),
        sum(
            abs(_month_remaining(goal, "target") - value)
            for goal, value in zip(goals, values, strict=True)
            if _active(goal, request)
        ),
        0,
    )


def _vertices(
    request: MultiGoalAllocationInput,
    goals: list[AllocationGoal],
    enabled: set[str],
    ranks: list[int],
    counter: _Counter,
) -> Iterator[tuple[int, ...]]:
    regions = [_regions(goal, *_bounds(goal, request, enabled)) for goal in goals]
    seen: set[tuple[int, ...]] = set()
    for boxes in product(*regions):
        lower = tuple(box[0] for box in boxes)
        if not _feasible(lower, ranks):
            counter.visit()
            continue
        if all(low == high for low, high in boxes):
            counter.visit()
            if lower not in seen:
                seen.add(lower)
                yield lower
            continue
        # Box intersection and lower-bound translation preserve the integral
        # polymatroid. Each vertex is greedy for an order of positive weights;
        # a prefix permits negative/zero weights to leave remaining coordinates low.
        for order in permutations(range(len(goals))):
            values = list(lower)
            for step in range(len(goals) + 1):
                counter.visit()
                candidate = tuple(values)
                if candidate not in seen:
                    seen.add(candidate)
                    yield candidate
                if step == len(goals):
                    break
                index = order[step]
                values[index] = min(
                    boxes[index][1],
                    min(
                        capacity
                        - sum(
                            value
                            for other, value in enumerate(values)
                            if other != index and mask & (1 << other)
                        )
                        for mask, capacity in enumerate(ranks)
                        if mask & (1 << index)
                    ),
                )


def _affine_box_optima(
    request: MultiGoalAllocationInput,
    goals: list[AllocationGoal],
    enabled: set[str],
    ranks: list[int],
    counter: _Counter,
) -> Iterator[tuple[int, ...]]:
    """Exact lexicographic optimum per affine box of the integral Hall polymatroid.

    Lower translation can make its rank nonmonotone. Taking the residual of
    every containing subset implements its monotone closure without omitting
    any Hall constraint. Box caps preserve the integral polymatroid. Completion
    endpoints are singleton regions, so every score coordinate is affine here.
    """
    regions = [_regions(goal, *_bounds(goal, request, enabled)) for goal in goals]
    seen: set[tuple[int, ...]] = set()
    for boxes in product(*regions):
        counter.visit()
        lower = tuple(box[0] for box in boxes)
        if not _feasible(lower, ranks):
            continue
        score = _score(request, goals, lower)
        costs: list[tuple[tuple[int, ...], int]] = []
        for index, (low, high) in enumerate(boxes):
            if low == high:
                continue
            counter.visit()
            adjacent = tuple(value + int(other == index) for other, value in enumerate(lower))
            next_score = _score(request, goals, adjacent)
            # Appending the actual value tuple's coefficient preserves _best's
            # exact tie order, including leaving zero-score coordinates low.
            cost = tuple(b - a for a, b in zip(score, next_score, strict=True)) + tuple(
                int(other == index) for other in range(len(goals))
            )
            if cost < (0,) * len(cost):
                costs.append((cost, index))
        values = list(lower)
        for _, index in sorted(costs):
            counter.visit()
            values[index] = min(
                boxes[index][1],
                min(
                    capacity
                    - sum(
                        value
                        for other, value in enumerate(values)
                        if other != index and mask & (1 << other)
                    )
                    for mask, capacity in enumerate(ranks)
                    if mask & (1 << index)
                ),
            )
        candidate = tuple(values)
        if candidate not in seen:
            seen.add(candidate)
            yield candidate


def _best(
    request: MultiGoalAllocationInput,
    goals: list[AllocationGoal],
    edges: set[tuple[UUID, int]],
    counter: _Counter,
) -> tuple[tuple[int, int, int, int, int, int, int], tuple[int, ...]] | None:
    ranks = _ranks(request, goals, edges, counter)
    if request.optimizer_version == AFFINE_BOX_V2:
        counter.visit()
        bounds = [_bounds(goal, request, _constraint_ids(request)) for goal in goals]
        # Every box lower is componentwise >= this immutable global lower.
        # If an original required goal has no permitted funding edge, no box
        # can repair it. Keep all Hall subsets and paid-edge graph enumeration.
        if any(low > high for low, high in bounds) or not _feasible(
            tuple(low for low, _ in bounds), ranks
        ):
            return None
    targets = tuple(
        _month_remaining(goal, "target") if _active(goal, request) else 0 for goal in goals
    )
    if (
        all(
            low <= amount <= high
            for goal, amount in zip(goals, targets, strict=True)
            for low, high in [_bounds(goal, request, _constraint_ids(request))]
        )
        and _feasible(targets, ranks)
        and _score(request, goals, targets) == (0, 0, 0, 0, 0, 0, 0)
    ):
        return (0, 0, 0, 0, 0, 0, 0), targets
    best = None
    vertices = _affine_box_optima if request.optimizer_version == AFFINE_BOX_V2 else _vertices
    for values in vertices(request, goals, _constraint_ids(request), ranks, counter):
        candidate = (_score(request, goals, values), values)
        if best is None or candidate < best:
            best = candidate
    return best


def _matching(
    request: MultiGoalAllocationInput,
    goals: list[AllocationGoal],
    edges: set[tuple[UUID, int]],
    values: tuple[int, ...],
) -> list[IncomeUse]:
    """Deterministic integral augmenting paths materialize original income uses."""
    lots = sorted(request.income_lots, key=lambda lot: lot.fragment_id)
    sink = 1 + len(lots) + len(goals)
    residual: dict[tuple[int, int], int] = {}
    adjacency: dict[int, set[int]] = {}

    def edge(start: int, end: int, capacity: int) -> None:
        residual[start, end] = capacity
        residual[end, start] = 0
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)

    for index, lot in enumerate(lots, 1):
        edge(0, index, lot.available_cents)
        for goal_index, goal in enumerate(goals):
            if (lot.account_id, goal_index) in edges and _eligible(lot, goal):
                edge(index, 1 + len(lots) + goal_index, values[goal_index])
    for index, amount in enumerate(values):
        edge(1 + len(lots) + index, sink, amount)
    sent = 0
    while True:
        parents = {0: -1}
        queue = deque([0])
        while queue and sink not in parents:
            node = queue.popleft()
            for neighbor in sorted(adjacency.get(node, set())):
                if neighbor not in parents and residual[node, neighbor] > 0:
                    parents[neighbor] = node
                    queue.append(neighbor)
        if sink not in parents:
            break
        path = []
        node = sink
        while node:
            previous = parents[node]
            path.append((previous, node))
            node = previous
        amount = min(residual[pair] for pair in path)
        for start, end in path:
            residual[start, end] -= amount
            residual[end, start] += amount
        sent += amount
    if sent != sum(values):
        raise ValueError("Hall-feasible allocations did not conserve original income")
    return [
        IncomeUse(
            fragment_id=lot.fragment_id,
            origin_transaction_id=lot.origin_transaction_id,
            source_account_id=lot.account_id,
            goal_id=goal.goal_id,
            amount_cents=amount,
        )
        for index, lot in enumerate(lots, 1)
        for goal_index, goal in enumerate(goals)
        if (amount := residual.get((1 + len(lots) + goal_index, index), 0)) > 0
    ]


def _result(
    request: MultiGoalAllocationInput,
    status: Literal["OPTIMAL", "INFEASIBLE", "UNKNOWN"],
    counter: _Counter,
    reasons: list[str],
    *,
    values: tuple[int, ...] | None = None,
    uses: list[IncomeUse] | None = None,
) -> MultiGoalAllocationResult:
    goals = sorted(request.goals, key=lambda goal: goal.goal_id)
    rows = []
    for index, goal in enumerate(goals):
        amount = values[index] if values is not None else None
        delay, censored, completed = (
            _delay(goal, amount, _today(request)) if amount is not None else (None, True, None)
        )
        rows.append(
            GoalAllocation(
                goal_id=goal.goal_id,
                effective_policy_version_id=goal.effective_policy_version_id,
                amount_cents=amount,
                minimum_shortfall_cents=(
                    max(0, _month_remaining(goal, "min") - amount)
                    if amount is not None and _active(goal, request)
                    else (0 if amount is not None else None)
                ),
                projected_owned_cents=(
                    goal.current_owned_cents + amount if amount is not None else None
                ),
                completion_date=completed,
                delay_lower_bound_days=delay,
                delay_censored=censored,
                deferral_cost_lower_bound_cents=(
                    delay * goal.deferral_cost_cents_per_day if delay is not None else None
                ),
            )
        )
    movement_count = len(
        {
            (use.source_account_id, use.goal_id)
            for use in uses or []
            if use.source_account_id
            != next(goal.account_id for goal in goals if goal.goal_id == use.goal_id)
        }
    )
    return MultiGoalAllocationResult(
        algorithm_version=request.optimizer_version or "critical-flow-lexicographic-v1",
        status=status,
        input_hash=configuration_hash(request.model_dump(mode="json")),
        objective_vector=(*_score(request, goals, values), movement_count)
        if values is not None
        else None,
        budget_cents=_budget(request),
        goals=rows,
        income_uses=uses or [],
        visited_nodes=counter.used,
        reasons=reasons,
    )


def solve_multi_goal_allocation(request: MultiGoalAllocationInput) -> MultiGoalAllocationResult:
    """Find the exact eight-level optimum or return UNKNOWN without actionable amounts."""
    request = MultiGoalAllocationInput.model_validate(request.model_dump())
    counter = _Counter(request.solver_node_budget)
    if request.source_issues:
        return _result(request, "UNKNOWN", counter, ["UNVERIFIED_ORIGINAL_SOURCES"])
    if any(point.remaining_cents < 0 for point in request.hard_protection_points):
        return _result(request, "INFEASIBLE", counter, ["HARD_PROTECTION_UNFUNDED"])
    goals = sorted(request.goals, key=lambda goal: goal.goal_id)
    edges = _edges(request, goals)
    try:
        optimum = _best(request, goals, set(edges), counter)
        if optimum is None:
            return _result(
                request, "INFEASIBLE", counter, ["NO_FEASIBLE_CURRENT_PERIOD_ALLOCATION"]
            )
        zero_cost = {
            (account, index) for account, index in edges if account == goals[index].account_id
        }
        paid = [edge for edge in edges if edge not in zero_cost]
        # Restricting permitted flow edges enumerates fixed movement charges
        # exactly. Source amounts never become one-cent enumeration domains.
        for count in range(len(paid) + 1):
            for selected in combinations(paid, count):
                allowed = zero_cost | set(selected)
                best = _best(request, goals, allowed, counter)
                if best is not None and best[0] == optimum[0]:
                    uses = _matching(request, goals, allowed, best[1])
                    return _result(
                        request,
                        "OPTIMAL",
                        counter,
                        [
                            "EXACT_CURRENT_PERIOD_LEXICOGRAPHIC_OPTIMUM",
                            "UNFINISHED_DELAY_IS_CENSORED",
                        ],
                        values=best[1],
                        uses=uses,
                    )
        raise ValueError("The complete source graph must contain its own optimum")
    except _CapacityExceeded:
        return _result(request, "UNKNOWN", counter, ["COMBINATORIAL_STATE_CAPACITY_EXCEEDED"])


class ConflictRemovalCheck(BoundaryModel):
    removed_constraint_id: str
    remaining_feasible: Literal[True] = True
    counterfactual_only: Literal[True] = True
    witness_amounts_cents: dict[str, int]


class MinimalGoalConflict(BoundaryModel):
    status: Literal["MINIMAL_CONFLICT", "NO_CONFLICT", "BASE_INFEASIBLE", "UNKNOWN"]
    scope: Literal["GOAL_POLICIES_WITH_IMMUTABLE_FINANCIAL_BASE"] = (
        "GOAL_POLICIES_WITH_IMMUTABLE_FINANCIAL_BASE"
    )
    grants_authority: Literal[False] = False
    input_hash: Hash
    constraint_ids: list[str]
    deletion_checks: list[ConflictRemovalCheck]
    reasons: list[str]


def _feasibility(
    request: MultiGoalAllocationInput, enabled: set[str]
) -> tuple[bool, dict[str, int]]:
    goals = sorted(request.goals, key=lambda goal: goal.goal_id)
    bounds = [_bounds(goal, request, enabled) for goal in goals]
    lower = tuple(bound[0] for bound in bounds)
    ranks = _ranks(request, goals, set(_edges(request, goals)), _Counter(1_000_000))
    return (
        all(low <= high for low, high in bounds) and _feasible(lower, ranks),
        {str(goal.goal_id): amount for goal, amount in zip(goals, lower, strict=True)},
    )


def find_minimal_goal_conflict(request: MultiGoalAllocationInput) -> MinimalGoalConflict:
    """Deletion-minimal policy constraints, with SAT witnesses after each single removal."""
    request = MultiGoalAllocationInput.model_validate(request.model_dump())
    input_hash = configuration_hash(request.model_dump(mode="json"))
    if request.source_issues:
        return MinimalGoalConflict(
            input_hash=input_hash,
            status="UNKNOWN",
            constraint_ids=[],
            deletion_checks=[],
            reasons=["UNVERIFIED_SOURCES"],
        )
    if any(point.remaining_cents < 0 for point in request.hard_protection_points):
        return MinimalGoalConflict(
            input_hash=input_hash,
            status="BASE_INFEASIBLE",
            constraint_ids=[],
            deletion_checks=[],
            reasons=["HARD_FINANCIAL_PROTECTION_CANNOT_BE_RELAXED"],
        )
    enabled = _constraint_ids(request)
    if _feasibility(request, enabled)[0]:
        return MinimalGoalConflict(
            input_hash=input_hash,
            status="NO_CONFLICT",
            constraint_ids=[],
            deletion_checks=[],
            reasons=[],
        )
    for identifier in sorted(enabled):
        candidate = enabled - {identifier}
        if not _feasibility(request, candidate)[0]:
            enabled = candidate
    checks = []
    for identifier in sorted(enabled):
        feasible, witness = _feasibility(request, enabled - {identifier})
        if not feasible:
            raise ValueError("A reported minimal conflict must become feasible after each removal")
        checks.append(
            ConflictRemovalCheck(removed_constraint_id=identifier, witness_amounts_cents=witness)
        )
    return MinimalGoalConflict(
        input_hash=input_hash,
        status="MINIMAL_CONFLICT",
        constraint_ids=sorted(enabled),
        deletion_checks=checks,
        reasons=[],
    )


class GoalRepairCandidate(BoundaryModel):
    candidate_id: UUID
    goal_id: UUID
    source_policy_version_id: UUID
    monthly_min_cents: MoneyCents | None = None
    monthly_max_cents: MoneyCents | None = None
    deadline: CalendarDate | None = None
    permission_ref: SourceReference

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if all(
            value is None
            for value in (self.monthly_min_cents, self.monthly_max_cents, self.deadline)
        ):
            raise ValueError("A repair candidate must contain a specific change")
        return self


class MinimalGoalRepair(BoundaryModel):
    status: Literal["PROPOSAL", "NOT_NEEDED", "NO_PERMITTED_REPAIR", "BASE_INFEASIBLE", "UNKNOWN"]
    original_input_hash: Hash
    grants_authority: Literal[False] = False
    requires_new_version_confirmation: Literal[True] = True
    candidates: list[GoalRepairCandidate]
    unaffected_goal_ids: list[UUID]
    changed_policy_count: StrictInt | None
    parameter_deviation_numerator: StrictInt | None
    parameter_deviation_denominator: StrictInt | None
    priority_loss_cents: StrictInt | None
    hypothetical_allocation: MultiGoalAllocationResult | None
    reasons: list[str]


def _repair_goal(
    goal: AllocationGoal, candidate: GoalRepairCandidate, request: MultiGoalAllocationInput
) -> tuple[AllocationGoal, Fraction, int]:
    if candidate.source_policy_version_id != goal.effective_policy_version_id:
        raise ValueError("Repair candidates must bind the exact current source version")
    if (
        candidate.permission_ref not in goal.source_refs
        or candidate.permission_ref.user_id != request.user_id
    ):
        raise ValueError("Repair permission must bind a verified current policy source")
    changes: dict[str, int | date] = {}
    deviation = Fraction(0)
    priority_loss = 0
    for field in ("monthly_min_cents", "monthly_max_cents", "deadline"):
        value = getattr(candidate, field)
        if value is None:
            continue
        if field not in goal.negotiable_fields:
            raise ValueError("Repair cannot change an unregistered negotiable field")
        original = getattr(goal, field)
        if value == original:
            continue
        if field == "deadline":
            if not goal.allow_deferral or value < original:
                raise ValueError("A deadline repair requires explicit deferral and may only extend")
            valid_end = goal.valid_until.date() if goal.valid_until is not None else date.max
            if value > valid_end:
                raise ValueError("A repair cannot extend beyond the original authorized window")
            deviation += Fraction(
                abs((value - original).days), max(1, abs((original - _today(request)).days))
            )
        else:
            deviation += Fraction(abs(value - original), max(1, original))
            if field == "monthly_min_cents":
                priority_loss += goal.importance * max(0, original - value)
        changes[field] = value
    if not changes:
        raise ValueError("A repair candidate must actually change a parameter")
    modified = AllocationGoal.model_validate({**goal.model_dump(), **changes})
    return modified, deviation, priority_loss


def propose_minimal_goal_repairs(
    request: MultiGoalAllocationInput,
    candidates: list[GoalRepairCandidate],
) -> MinimalGoalRepair:
    """Select only registered goal repairs; never relax financial floors or original ownership."""
    request = MultiGoalAllocationInput.model_validate(request.model_dump())
    if len(candidates) > 64:
        raise ValueError("At most 64 explicit repair candidates are supported")
    candidates = [
        GoalRepairCandidate.model_validate(candidate.model_dump()) for candidate in candidates
    ]
    if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
        raise ValueError("Repair candidate identities must be unique")
    original_hash = configuration_hash(request.model_dump(mode="json"))
    goals = {goal.goal_id: goal for goal in request.goals}
    prepared: dict[UUID, list[tuple[GoalRepairCandidate, AllocationGoal, Fraction, int]]] = {}
    for candidate in sorted(candidates, key=lambda candidate: candidate.candidate_id):
        goal = goals.get(candidate.goal_id)
        if goal is None:
            raise ValueError("A repair candidate references an unknown goal")
        modified, deviation, priority_loss = _repair_goal(goal, candidate, request)
        prepared.setdefault(goal.goal_id, []).append(
            (candidate, modified, deviation, priority_loss)
        )

    def response(
        status: Literal[
            "PROPOSAL", "NOT_NEEDED", "NO_PERMITTED_REPAIR", "BASE_INFEASIBLE", "UNKNOWN"
        ],
        reasons: list[str],
        selected: list[GoalRepairCandidate] | None = None,
        deviation: Fraction | None = None,
        priority_loss: int | None = None,
        allocation: MultiGoalAllocationResult | None = None,
    ) -> MinimalGoalRepair:
        chosen = selected or []
        changed = {candidate.goal_id for candidate in chosen}
        return MinimalGoalRepair(
            status=status,
            original_input_hash=original_hash,
            candidates=chosen,
            unaffected_goal_ids=sorted(set(goals) - changed),
            changed_policy_count=len(changed) if deviation is not None else None,
            parameter_deviation_numerator=deviation.numerator if deviation is not None else None,
            parameter_deviation_denominator=deviation.denominator
            if deviation is not None
            else None,
            priority_loss_cents=priority_loss,
            hypothetical_allocation=allocation,
            reasons=reasons,
        )

    if request.source_issues:
        return response("UNKNOWN", ["UNVERIFIED_ORIGINAL_SOURCES"])
    if any(point.remaining_cents < 0 for point in request.hard_protection_points):
        return response("BASE_INFEASIBLE", ["HARD_FINANCIAL_PROTECTION_CANNOT_BE_RELAXED"])
    if _feasibility(request, _constraint_ids(request))[0]:
        return response("NOT_NEEDED", [], deviation=Fraction(0), priority_loss=0)
    counter = _Counter(request.solver_node_budget)
    try:
        # Every smaller policy-count layer is exhausted before a larger one.
        for count in range(1, len(prepared) + 1):
            best = None
            for selected_goals in combinations(sorted(prepared), count):
                for choices in product(*(prepared[identifier] for identifier in selected_goals)):
                    counter.visit()
                    modified_goals = {**goals, **{row[1].goal_id: row[1] for row in choices}}
                    hypothetical = MultiGoalAllocationInput.model_validate(
                        {**request.model_dump(), "goals": list(modified_goals.values())}
                    )
                    if not _feasibility(hypothetical, _constraint_ids(hypothetical))[0]:
                        continue
                    deviation = sum((row[2] for row in choices), Fraction(0))
                    loss = sum(row[3] for row in choices)
                    key = (deviation, loss, tuple(row[0].candidate_id for row in choices))
                    if best is None or key < best[0]:
                        best = (key, choices, hypothetical)
            if best is not None:
                allocation = solve_multi_goal_allocation(best[2])
                if allocation.status != "OPTIMAL":
                    return response("UNKNOWN", ["REPAIR_ALLOCATION_NOT_PROVED_OPTIMAL"])
                return response(
                    "PROPOSAL",
                    ["HYPOTHETICAL_ONLY_REQUIRES_NEW_VERSION_CONFIRMATION"],
                    selected=[row[0] for row in best[1]],
                    deviation=best[0][0],
                    priority_loss=best[0][1],
                    allocation=allocation,
                )
        return response("NO_PERMITTED_REPAIR", ["NO_REGISTERED_GOAL_REPAIR_RESTORES_FEASIBILITY"])
    except _CapacityExceeded:
        return response("UNKNOWN", ["COMBINATORIAL_REPAIR_CAPACITY_EXCEEDED"])
