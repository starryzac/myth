"""Finite integer portfolio previews over original product versions and cash facts."""

from datetime import UTC, date, datetime, timedelta, timezone
from fractions import Fraction
from itertools import combinations
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.asset_allocation import _project, _reserve_snapshot
from app.domain.asset_allocation_types import (
    AssetCashUse,
    AssetProductTerms,
    FixedPrincipalTerms,
    PlannedExit,
    PlannedPrincipalTerms,
)
from app.domain.asset_exposure import AssetExposure
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BoundaryModel,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
)
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.domain.policy_configuration import MoneyCents, configuration_hash
from pydantic import Field, StrictBool, StrictInt, model_validator

YIELD_DENOMINATOR = 10000 * 365


class FullAssetPlanOptions(BoundaryModel):
    comparison_days: Annotated[StrictInt, Field(ge=1, le=365)] = 90
    max_components: Annotated[StrictInt, Field(ge=1, le=4)] = 3
    max_turnover_cents: MoneyCents | None = None
    funds_use_date: date | None = None
    mode: Literal["PORTFOLIO", "FIXED_LADDER"] = "PORTFOLIO"


class FullAssetPlanningInput(BoundaryModel):
    snapshot: BoundarySnapshot
    boundary_versions: list[BoundaryPolicyVersion]
    positions: list[BoundaryPosition]
    boundary_products: list[BoundaryProduct]
    products: Annotated[list[AssetProductTerms], Field(max_length=100)]
    exposure: AssetExposure
    policy_id: UUID
    policy_version_id: UUID
    configuration: AssetAuthorizationPolicy
    planning_confirmation_valid: StrictBool
    confirmed_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    goal_deadline: date | None = None
    goal_policy_version_id: UUID | None = None
    goal_reference_verified: StrictBool = False
    options: FullAssetPlanOptions = Field(default_factory=FullAssetPlanOptions)
    search_node_budget: Annotated[StrictInt, Field(ge=1, le=200000)] = 100000

    @model_validator(mode="after")
    def consistent_scope(self) -> Self:
        goal_id = self.configuration.goal_id
        if self.exposure.scope != self.configuration.scope or self.exposure.goal_id != goal_id:
            raise ValueError("Exposure must belong to the exact planning scope")
        if self.exposure.as_of > self.snapshot.as_of:
            raise ValueError("Future exposure cannot be a current cash fact")
        if goal_id is None and any(
            value is not None for value in (self.goal_deadline, self.goal_policy_version_id)
        ):
            raise ValueError("General planning cannot borrow a goal deadline or authority")
        if len({p.product_id for p in self.products}) != len(self.products) or len(
            {(p.product_code, p.version_number) for p in self.products}
        ) != len(self.products):
            raise ValueError("Original product versions must be unique")
        positions = {p.position_id: p for p in self.positions}
        counted = set(self.exposure.counted_position_ids)
        excluded = set(self.exposure.excluded_manual_position_ids)
        if counted & excluded or not counted | excluded <= set(positions):
            raise ValueError("Managed exposure must identify original included positions")
        if any(
            positions[i].goal_id != goal_id or positions[i].status == "REDEEMED" for i in counted
        ) or sum(positions[i].principal_cents for i in counted) != (
            self.exposure.managed_principal_cents
        ):
            raise ValueError("Managed principal must equal the exact scoped original positions")
        if (
            not {
                p.position_id
                for p in self.positions
                if p.goal_id == goal_id and p.status != "REDEEMED"
            }
            <= counted | excluded
        ):
            raise ValueError("Every scope position needs its original acquisition classification")
        return self


class FullAssetCandidate(BoundaryModel):
    product_id: UUID
    product_code: str
    version_number: StrictInt
    catalog_asset_class: str
    planning_asset_class: str | None
    terms_digest: str
    status: Literal["FEASIBLE", "REJECTED", "RETAIN_CASH"]
    bank_auto_eligible: Literal[False] = False
    financial_cap_cents: MoneyCents | None = None
    maximum_batch_cents: MoneyCents | None = None
    exit_plan: PlannedExit | None = None
    reasons: list[str]


class FullAssetBatch(BoundaryModel):
    product_id: UUID
    product_code: str
    version_number: StrictInt
    terms_digest: str
    amount_cents: MoneyCents
    net_simulated_yield_cents: MoneyCents
    purchase_at: datetime
    principal_available_at: datetime
    exit_plan: PlannedExit
    cash_uses: list[AssetCashUse]
    bank_authority: Literal[False] = False


class FullAssetPlanningResult(BoundaryModel):
    algorithm_version: Literal["finite-integer-original-product-portfolio-v1"] = (
        "finite-integer-original-product-portfolio-v1"
    )
    policy_id: UUID
    policy_version_id: UUID
    scope: Literal["general_idle_funds", "goal"]
    goal_id: UUID | None
    planning_only: Literal[True] = True
    bank_authority: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    future_income_included_cents: Literal[0] = 0
    status: Literal[
        "OPTIMAL",
        "NO_PURCHASE",
        "UNKNOWN",
        "INACTIVE_POLICY",
        "INSUFFICIENT_EVIDENCE",
        "LIQUIDITY_RISK",
    ]
    input_hash: str
    baseline_boundary: BoundaryResult
    projected_boundary: BoundaryResult | None = None
    candidates: list[FullAssetCandidate] = Field(default_factory=list)
    batches: list[FullAssetBatch] = Field(default_factory=list)
    total_purchase_cents: MoneyCents | None = None
    retained_scope_cash_cents: MoneyCents | None = None
    net_simulated_yield_cents: MoneyCents | None = None
    purchase_count: StrictInt | None = None
    search_nodes: StrictInt = 0
    funds_use_date: date | None = None
    ladder_status: Literal[
        "NOT_REQUESTED",
        "MATCHED_MULTI_MATURITY",
        "SINGLE_MATURITY_AVAILABLE",
        "NO_VALID_FIXED_BATCHES",
    ] = "NOT_REQUESTED"
    reasons: list[str]


def _ceil(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _catalog_class(product: AssetProductTerms) -> str | None:
    if product.asset_class == "FIXED_DEPOSIT":
        days = product.maturity_rule.get("term_days")
        return {7: "FIXED_DEPOSIT_7D", 30: "FIXED_DEPOSIT_30D", 90: "FIXED_DEPOSIT_90D"}.get(
            days if type(days) is int else -1
        )
    return product.asset_class


def _candidate(
    product: AssetProductTerms,
    request: FullAssetPlanningInput,
    latest: dict[str, int],
    use_date: date,
) -> FullAssetCandidate:
    now, config = request.snapshot.as_of, request.configuration
    kind = _catalog_class(product)
    reasons: list[str] = []
    current = (
        product.created_at <= now
        and product.effective_from <= now
        and (product.effective_until is None or now < product.effective_until)
    )
    if not current:
        reasons.append("PRODUCT_NOT_CURRENTLY_KNOWN_AND_EFFECTIVE")
    elif latest[product.product_code] != product.version_number:
        reasons.append("SUPERSEDED_PRODUCT_VERSION")
    if kind not in config.allowed_asset_classes:
        reasons.append("ASSET_CLASS_NOT_IN_CONFIRMED_PLANNING_SCOPE")
    if product.principal_fluctuation:
        reasons.append("PRINCIPAL_FLUCTUATION_NEVER_AUTO")
    if product.risk_level > config.max_principal_risk_level:
        reasons.append("RISK_LEVEL_EXCEEDS_CONFIRMED_CAP")
    if product.lock_days > config.max_lock_days:
        reasons.append("LOCK_EXCEEDS_CONFIRMED_CAP")
    if product.redemption_delay_days > config.max_redemption_delay_days:
        reasons.append("REDEMPTION_DELAY_EXCEEDS_CONFIRMED_CAP")
    if not product.auto_purchase_allowed and kind != "CASH":
        reasons.append("PRODUCT_AUTO_PURCHASE_DISABLED")
    plan = None
    if kind != "CASH":
        try:
            zone = UTC if request.snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
            limit = datetime.combine(use_date, datetime.min.time(), zone)
            terms: FixedPrincipalTerms | PlannedPrincipalTerms
            if product.maturity_rule.get("protocol") == "fixed-principal-return-v1":
                terms = FixedPrincipalTerms.model_validate(product.maturity_rule)
                expected = {
                    "FIXED_DEPOSIT_7D": 7,
                    "FIXED_DEPOSIT_30D": 30,
                    "FIXED_DEPOSIT_90D": 90,
                }.get(kind or "")
                if (
                    (expected is not None and expected != terms.term_days)
                    or kind
                    not in {
                        "FIXED_DEPOSIT_7D",
                        "FIXED_DEPOSIT_30D",
                        "FIXED_DEPOSIT_90D",
                        "LOW_RISK_TERM",
                    }
                    or terms.term_days < product.lock_days
                ):
                    raise ValueError("FIXED_TERMS_CONTRADICT_CATALOG")
                if terms.yield_rule.accrual != "UNTIL_MATURITY":
                    raise ValueError("FIXED_YIELD_ACCRUAL_INVALID")
                earning, request_at = terms.term_days, None
                exit_kind: Literal["FIXED_MATURITY", "PLANNED_REDEMPTION"] = "FIXED_MATURITY"
                liquidity = terms.term_days + terms.settlement_delay_days
            else:
                terms = PlannedPrincipalTerms.model_validate(product.maturity_rule)
                if kind not in {"CASH_MGMT_T0", "CASH_MGMT_T1"} or (
                    terms.yield_rule.accrual != "UNTIL_REDEMPTION_REQUEST"
                ):
                    raise ValueError("PLANNED_TERMS_CONTRADICT_CATALOG")
                if request.options.mode == "FIXED_LADDER":
                    raise ValueError("FIXED_LADDER_REQUIRES_FIXED_MATURITY")
                if (
                    not product.auto_redeem_allowed
                    or not config.allow_auto_recovery_without_penalty
                ):
                    raise ValueError("ZERO_LOSS_PLANNED_RECOVERY_NOT_CONFIRMED")
                horizon = min(request.options.comparison_days, max(0, (limit - now).days))
                earning = horizon - terms.settlement_delay_days
                if request.valid_until is not None:
                    delta = request.valid_until - now
                    micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
                    earning = min(earning, (micros - 1) // (86400 * 1000000))
                if earning < product.lock_days:
                    raise ValueError("NO_CONFIRMED_REDEMPTION_AFTER_LOCK")
                request_at = now + timedelta(days=earning)
                exit_kind = "PLANNED_REDEMPTION"
                liquidity = product.lock_days + terms.settlement_delay_days
            if terms.yield_rule.annual_yield_bps != product.annual_yield_bps or (
                terms.settlement_delay_days != product.redemption_delay_days
            ):
                raise ValueError("PRODUCT_TERMS_CONTRADICT_CATALOG")
            available = now + timedelta(days=earning + terms.settlement_delay_days)
            if earning <= 0 or available > limit:
                raise ValueError("PRINCIPAL_NOT_AVAILABLE_BEFORE_FUNDS_USE")
            plan = PlannedExit(
                kind=exit_kind,
                request_at=request_at,
                principal_available_at=available,
                earning_days=earning,
                liquidity_days=liquidity,
                terms_digest=product.terms_digest,
            )
        except (ValueError, TypeError, OverflowError) as error:
            reasons.append(str(error))
    return FullAssetCandidate(
        product_id=product.product_id,
        product_code=product.product_code,
        version_number=product.version_number,
        catalog_asset_class=product.asset_class,
        planning_asset_class=kind,
        terms_digest=product.terms_digest,
        status="REJECTED" if reasons else "RETAIN_CASH" if kind == "CASH" else "FEASIBLE",
        exit_plan=plan,
        reasons=sorted(set(reasons)),
    )


def plan_full_assets(request: FullAssetPlanningInput) -> FullAssetPlanningResult:
    request = FullAssetPlanningInput.model_validate(request.model_dump())
    snapshot, config, exposure = request.snapshot, request.configuration, request.exposure
    goal_id = config.goal_id
    baseline = compute_boundary(
        snapshot, request.boundary_versions, request.positions, request.boundary_products
    )
    digest = configuration_hash(request.model_dump(mode="json"))

    def result(status: str, reason: str, **extra: object) -> FullAssetPlanningResult:
        return FullAssetPlanningResult.model_validate(
            {
                "policy_id": request.policy_id,
                "policy_version_id": request.policy_version_id,
                "scope": config.scope,
                "goal_id": goal_id,
                "status": status,
                "input_hash": digest,
                "baseline_boundary": baseline,
                "reasons": [reason],
                **extra,
            }
        )

    if baseline.status != "READY":
        return result(baseline.status, "ORIGINAL_FINANCIAL_CONTEXT_NOT_READY")
    if (
        not request.planning_confirmation_valid
        or snapshot.as_of < max(request.confirmed_at, request.valid_from)
        or (request.valid_until is not None and snapshot.as_of >= request.valid_until)
    ):
        return result("INACTIVE_POLICY", "CURRENT_FULL_PLANNING_CONFIRMATION_REQUIRED")
    zone = UTC if snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    today = snapshot.as_of.astimezone(zone).date()
    use_date = today + timedelta(days=request.options.comparison_days)
    if request.options.funds_use_date is not None:
        use_date = min(use_date, request.options.funds_use_date)
    if goal_id is not None:
        if (
            not request.goal_reference_verified
            or request.goal_deadline is None
            or (request.goal_policy_version_id is None)
        ):
            return result("INACTIVE_POLICY", "ACTUAL_CURRENT_GOAL_REFERENCE_REQUIRED")
        use_date = min(use_date, request.goal_deadline)
    elif request.options.mode == "FIXED_LADDER":
        return result("INACTIVE_POLICY", "FIXED_LADDER_REQUIRES_AN_ACTUAL_GOAL")
    if use_date <= today:
        return result(
            "NO_PURCHASE",
            "FUNDS_USE_DATE_LEAVES_NO_SAFE_LOCK_INTERVAL",
            total_purchase_cents=0,
            net_simulated_yield_cents=0,
            purchase_count=0,
        )
    if use_date > today + timedelta(days=snapshot.horizon_days):
        return result("UNKNOWN", "FUNDS_USE_DATE_EXCEEDS_VERIFIED_PROTECTION_HORIZON")
    if any(account.observed_at > exposure.as_of for account in snapshot.cash_accounts):
        return result("INSUFFICIENT_EVIDENCE", "EXPOSURE_PRECEDES_ACTUAL_ACCOUNT_FACTS")
    working = _reserve_snapshot(snapshot, exposure)
    reserved = compute_boundary(
        working, request.boundary_versions, request.positions, request.boundary_products
    )
    if reserved.status != "READY":
        return result(reserved.status, "CURRENT_RESERVATIONS_PROTECT_ORIGINAL_CASH")
    owned_by_account = {
        account.account_id: sum(
            g.cash_owned_cents for g in working.goals if g.account_id == account.account_id
        )
        for account in working.cash_accounts
    }
    funding = {
        a.account_id: a.balance_cents - owned_by_account[a.account_id]
        for a in working.cash_accounts
        if a.account_type == "CASH"
    }
    if goal_id is not None:
        goal = next((g for g in working.goals if g.goal_id == goal_id), None)
        if goal is None or goal.account_id is None:
            return result("INSUFFICIENT_EVIDENCE", "ORIGINAL_GOAL_CASH_OWNERSHIP_MISSING")
        funding = {
            goal.account_id: goal.cash_owned_cents
            - exposure.reserved_goal_cash_by_goal.get(goal_id, 0)
        }
    scope_cash = sum(funding.values())
    remaining = max(
        0,
        config.max_auto_managed_cents
        - exposure.managed_principal_cents
        - exposure.pending_purchase_cents,
    )
    total_cap = min(
        scope_cash,
        remaining,
        request.options.max_turnover_cents
        if request.options.max_turnover_cents is not None
        else remaining,
    )
    latest: dict[str, int] = {}
    for p in request.products:
        if (
            p.created_at <= snapshot.as_of
            and p.effective_from <= snapshot.as_of
            and (p.effective_until is None or snapshot.as_of < p.effective_until)
        ):
            latest[p.product_code] = max(latest.get(p.product_code, 0), p.version_number)
    products = sorted(
        request.products, key=lambda p: (p.product_code, p.version_number, p.product_id)
    )
    candidates = [_candidate(p, request, latest, use_date) for p in products]
    feasible: list[tuple[AssetProductTerms, FullAssetCandidate]] = []
    zone = UTC if snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    for p, c in zip(products, candidates, strict=True):
        if c.status != "FEASIBLE" or c.exit_plan is None:
            continue
        cap = min(total_cap, config.single_action_cap_cents)
        if goal_id is None:
            cap = min(
                cap,
                min(
                    point.margin_cents
                    for point in reserved.calculation_trace
                    if point.date < c.exit_plan.principal_available_at.astimezone(zone).date()
                    or (
                        point.date == c.exit_plan.principal_available_at.astimezone(zone).date()
                        and point.phase != "AFTER_PRINCIPAL"
                    )
                ),
            )
        reason = []
        if cap < max(1, p.minimum_purchase_cents):
            reason.append("BELOW_MINIMUM_OR_CONFIRMED_FINANCIAL_CAP")
        c = c.model_copy(
            update={
                "financial_cap_cents": max(0, cap),
                "maximum_batch_cents": max(0, cap),
                "status": "REJECTED" if reason else "FEASIBLE",
                "reasons": reason,
            }
        )
        candidates[products.index(p)] = c
        if not reason:
            feasible.append((p, c))
    common = {"candidates": candidates, "funds_use_date": use_date}
    if len(feasible) > 8:
        return result("UNKNOWN", "MORE_THAN_EIGHT_FEASIBLE_ORIGINAL_PRODUCTS", **common)
    constraints: dict[tuple[int, ...], int] = {tuple(range(len(feasible))): total_cap}
    if goal_id is None:
        for point in reserved.calculation_trace:
            pending = tuple(
                i
                for i, (_, c) in enumerate(feasible)
                if c.exit_plan is not None
                and (
                    point.date < c.exit_plan.principal_available_at.astimezone(zone).date()
                    or (
                        point.date == c.exit_plan.principal_available_at.astimezone(zone).date()
                        and point.phase != "AFTER_PRINCIPAL"
                    )
                )
            )
            if pending:
                constraints[pending] = min(constraints.get(pending, total_cap), point.margin_cents)
    # All portfolio constraints are nested sets of still-locked original principal.
    sets = list(constraints)
    if any(not (set(a) <= set(b) or set(b) <= set(a)) for a in sets for b in sets):
        return result("UNKNOWN", "NON_NESTED_PRINCIPAL_CONSTRAINTS_UNSUPPORTED", **common)
    rates = [
        Fraction(p.annual_yield_bps * c.exit_plan.earning_days, YIELD_DENOMINATOR)
        if c.exit_plan is not None
        else Fraction(0)
        for p, c in feasible
    ]
    liquidities = [
        c.exit_plan.liquidity_days if c.exit_plan is not None else 0 for _, c in feasible
    ]
    best_amounts = [0] * len(feasible)
    best_key = (0, 0, 0, 0, 0)
    nodes = 0

    def score(values: list[int]) -> tuple[int, int, int, int, int]:
        yields = sum((values[i] * rates[i]).__floor__() for i in range(len(values)))
        lock = sum(values[i] * liquidities[i] for i in range(len(values)))
        delay = sum(values[i] * feasible[i][0].redemption_delay_days for i in range(len(values)))
        return (-yields, sum(value > 0 for value in values), sum(values), lock, delay)

    def within(values: list[int]) -> bool:
        return all(sum(values[i] for i in indices) <= cap for indices, cap in constraints.items())

    exhausted = False
    for count in range(1, min(request.options.max_components, len(feasible)) + 1):
        for subset in combinations(range(len(feasible)), count):
            lows = [
                max(1, feasible[i][0].minimum_purchase_cents) if i in subset else 0
                for i in range(len(feasible))
            ]
            highs = [
                (feasible[i][1].maximum_batch_cents or 0) if i in subset else 0
                for i in range(len(feasible))
            ]
            stack = [(lows, highs)]
            while stack:
                nodes += 1
                if nodes > request.search_node_budget:
                    exhausted = True
                    break
                low, high = stack.pop()
                if not within(low) or any(a > b for a, b in zip(low, high, strict=True)):
                    continue
                trial = list(low)
                for i in sorted(
                    subset,
                    key=lambda k: (
                        -rates[k],
                        liquidities[k],
                        k,
                    ),
                ):
                    extra = min(
                        [
                            high[i] - trial[i],
                            *(
                                cap - sum(trial[k] for k in indices)
                                for indices, cap in constraints.items()
                                if i in indices
                            ),
                        ]
                    )
                    trial[i] += max(0, extra)
                individual_upper = [(high[i] * rates[i]).__floor__() for i in range(len(high))]
                upper = min(
                    sum(individual_upper),
                    sum((trial[i] * rates[i] for i in subset), Fraction(0)).__floor__(),
                )
                if upper < -best_key[0]:
                    continue
                # Round each original batch's interest once; leave unused pennies as cash.
                candidate = list(trial)
                for i in subset:
                    earning = (trial[i] * rates[i]).__floor__()
                    candidate[i] = (
                        max(low[i], _ceil(Fraction(earning, 1) / rates[i])) if rates[i] else low[i]
                    )
                key = score(candidate)
                if key < best_key:
                    best_key, best_amounts = key, candidate
                # Every batch must supply its share of the best complete rounded yield.
                # This bounds intervals by interest thresholds, not one-cent enumeration.
                for i in subset:
                    required = max(0, -best_key[0] - sum(individual_upper) + individual_upper[i])
                    if rates[i]:
                        low[i] = max(low[i], _ceil(Fraction(required) / rates[i]))
                if not within(low) or any(a > b for a, b in zip(low, high, strict=True)):
                    continue
                if upper == -best_key[0]:
                    if count > best_key[1]:
                        continue
                    if count == 1:
                        # One batch's least principal attaining its maximal rounded yield is exact.
                        continue
                    raw_low = sum((low[i] * rates[i] for i in subset), Fraction(0))
                    max_rate = max((rates[i] for i in subset), default=Fraction(0))
                    additional = (
                        _ceil(max(Fraction(0), Fraction(upper) - raw_low) / max_rate)
                        if max_rate
                        else 0
                    )
                    minimum_turnover = sum(low) + additional
                    if count == best_key[1] and minimum_turnover > best_key[2]:
                        continue
                    if count == best_key[1] and minimum_turnover == best_key[2]:
                        lock_lower = sum(low[i] * liquidities[i] for i in subset)
                        lock_lower += additional * min(liquidities[i] for i in subset)
                        delay_lower = sum(
                            low[i] * feasible[i][0].redemption_delay_days for i in subset
                        )
                        delay_lower += additional * min(
                            feasible[i][0].redemption_delay_days for i in subset
                        )
                        if (lock_lower, delay_lower) >= best_key[3:]:
                            continue
                widths = [i for i in subset if high[i] > low[i]]
                if not widths:
                    continue
                i = max(widths, key=lambda k: ((high[k] - low[k]) * rates[k], high[k] - low[k], -k))
                midpoint = (low[i] + high[i]) // 2
                left_high, right_low = list(high), list(low)
                left_high[i], right_low[i] = midpoint, midpoint + 1
                stack.append((right_low, high))
                stack.append((low, left_high))
            if exhausted:
                break
        if exhausted:
            break
    if exhausted:
        return result(
            "UNKNOWN", "EXACT_INTEGER_SEARCH_CAPACITY_EXCEEDED", search_nodes=nodes, **common
        )
    projected, projected_positions = working, list(request.positions)
    batches: list[FullAssetBatch] = []
    for i, amount in enumerate(best_amounts):
        if not amount:
            continue
        product, selected_candidate = feasible[i]
        plan = selected_candidate.exit_plan
        if plan is None:
            raise ValueError("A selected original product has no exit plan")
        projected, projected_positions, uses = _project(
            projected, projected_positions, product, plan, amount, funding, goal_id
        )
        for use in uses:
            funding[use.account_id] -= use.amount_cents
        batches.append(
            FullAssetBatch(
                product_id=product.product_id,
                product_code=product.product_code,
                version_number=product.version_number,
                terms_digest=product.terms_digest,
                amount_cents=amount,
                net_simulated_yield_cents=(amount * rates[i]).__floor__(),
                purchase_at=snapshot.as_of,
                principal_available_at=plan.principal_available_at,
                exit_plan=plan,
                cash_uses=uses,
            )
        )
    checked = compute_boundary(
        projected, request.boundary_versions, projected_positions, request.boundary_products
    )
    if checked.status != "READY":
        return result("UNKNOWN", "FINAL_ORIGINAL_COMBINED_BOUNDARY_DID_NOT_VERIFY", **common)
    ladder: str = "NOT_REQUESTED"
    if request.options.mode == "FIXED_LADDER":
        dates = {batch.principal_available_at for batch in batches}
        ladder = (
            "MATCHED_MULTI_MATURITY"
            if len(dates) > 1
            else ("SINGLE_MATURITY_AVAILABLE" if dates else "NO_VALID_FIXED_BATCHES")
        )
    total = sum(batch.amount_cents for batch in batches)
    return result(
        "OPTIMAL" if batches else "NO_PURCHASE",
        "EXACT_REGISTERED_FINITE_DOMAIN",
        projected_boundary=checked,
        batches=batches,
        total_purchase_cents=total,
        retained_scope_cash_cents=scope_cash - total,
        net_simulated_yield_cents=-best_key[0],
        purchase_count=len(batches),
        search_nodes=nodes,
        ladder_status=ladder,
        **common,
    )
