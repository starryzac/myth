"""Explicit v3 bounded change impacts; no candidate row is a source or a grant."""

from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.boundary import compute_boundary, compute_policy_change_boundary
from app.domain.boundary_types import (
    BoundaryModel,
    BoundaryPoint,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
)
from app.domain.full_asset_allocation import FullAssetPlanningInput
from app.domain.full_asset_allocation import _candidate as asset_candidate
from app.domain.full_policy_configuration import (
    FULL_TYPE_MAPPING,
    MVP_TYPE_MAPPING,
    AssetAuthorizationPolicy,
    GoalAllocationPolicy,
    RecoveryPolicy,
    TemplateName,
    validate_full_configuration,
)
from app.domain.full_protection_projection import (
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    FullProtectionProjectionResult,
    project_full_protection,
)
from app.domain.full_recovery_planning import (
    FullRecoveryCandidate,
    FullRecoveryPlanningInput,
    _linked_configuration,
)
from app.domain.full_recovery_planning import (
    _candidate as recovery_candidate,
)
from app.domain.multi_goal_allocation import (
    MultiGoalAllocationInput,
    MultiGoalAllocationResult,
    solve_multi_goal_allocation,
)
from app.domain.policy_change_types import LivingReserveChangeEstimate, PolicyChangeAssumption
from app.domain.policy_configuration import MoneyCents, configuration_hash, validate_configuration
from pydantic import Field, StrictInt

PROTOCOL = "full-policy-multi-template-change-v3"
SourceKind = Literal["MVP_POLICY", "FULL_POLICY"]
PHASES = ("BEFORE_PAYMENT", "AFTER_PAYMENT", "AFTER_PRINCIPAL")


class MultiTemplateChangeInput(BoundaryModel):
    protocol: Literal["full-policy-multi-template-input-v3"] = "full-policy-multi-template-input-v3"
    user_id: UUID
    epoch_id: UUID
    policy_id: UUID
    version_id: UUID
    source_kind: SourceKind
    template_name: TemplateName
    current_configuration: dict[str, Any]
    candidate_configuration: dict[str, Any]
    candidate_valid_from: datetime
    candidate_valid_until: datetime | None = None
    snapshot: BoundarySnapshot
    boundary_versions: list[BoundaryPolicyVersion]
    positions: list[BoundaryPosition]
    boundary_products: list[BoundaryProduct]
    full_projection: FullProtectionProjectionResult
    full_sources: list[FullProtectionPolicySource]
    reserved_cash_by_account: dict[UUID, MoneyCents] = Field(default_factory=dict)
    source_originals: dict[str, Any]
    source_issues: list[str] = Field(default_factory=list)
    mvp_source: BoundaryPolicyVersion | None = None
    mvp_source_status: Literal["ACTIVE", "CONFIRMED", "SUSPENDED"] | None = None
    living_estimate: LivingReserveChangeEstimate | None = None
    asset_input: FullAssetPlanningInput | None = None
    recovery_input: FullRecoveryPlanningInput | None = None
    joint_input: MultiGoalAllocationInput | None = None


class ProductCapacityChange(BoundaryModel):
    product_id: UUID
    product_version: StrictInt
    terms_digest: str
    before_capacity_cents: MoneyCents | None
    after_capacity_cents: MoneyCents | None
    delta_cents: StrictInt | None
    before_reasons: list[str]
    after_reasons: list[str]
    individual_capacity_not_portfolio_sum: Literal[True] = True
    grants_authority: Literal[False] = False


class RecoveryCandidateChange(BoundaryModel):
    position_id: UUID
    before: dict[str, Any]
    after: dict[str, Any]
    conditional_on_time_net_delta_cents: StrictInt | None
    conditional_cash_not_current_cash: Literal[True] = True
    conditional_boundary_scope: Literal["ORIGINAL_MVP_ONLY_NOT_FULL_RECOVERY_CURVE"] = (
        "ORIGINAL_MVP_ONLY_NOT_FULL_RECOVERY_CURVE"
    )


class MultiTemplateFinancialImpact(BoundaryModel):
    protocol: Literal["full-policy-multi-template-impact-v3"] = (
        "full-policy-multi-template-impact-v3"
    )
    simulation: Literal[True] = True
    hypothetical: Literal[True] = True
    grants_authority: Literal[False] = False
    bank_authority: Literal[False] = False
    writes_policy_or_bank: Literal[False] = False
    future_income_in_current_cash_cents: Literal[0] = 0
    future_income_in_execution_cents: Literal[0] = 0
    horizon_days: Literal[365] = 365
    phase_denominator: Literal[1098] = 1098
    status: Literal["PROJECTED", "PARTIAL", "UNKNOWN"]
    scope: Literal[
        "MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS",
        "INDIVIDUAL_PRODUCT_CAPACITY",
        "WHOLE_POSITION_RECOVERY_CANDIDATES",
        "CURRENT_JOINT_GOAL_ALLOCATION",
        "UNSUPPORTED",
    ]
    template_name: TemplateName
    before: BoundaryResult | None
    after: BoundaryResult | None
    delta_safe_idle_cents: StrictInt | None = None
    delta_minimum_margin_cents: StrictInt | None = None
    delta_product_financial_capacity_cents: dict[str, int] | None = None
    product_capacities: list[ProductCapacityChange] = Field(default_factory=list)
    recovery_candidates: list[RecoveryCandidateChange] = Field(default_factory=list)
    goal_allocation_before: MultiGoalAllocationResult | None = None
    goal_allocation_after: MultiGoalAllocationResult | None = None
    goal_allocation_delta_cents: dict[str, int | None] | None = None
    current_owned_cash_delta_cents: Literal[0] = 0
    current_position_principal_delta_cents: Literal[0] = 0
    future_action_delta: Literal["UNKNOWN_REQUIRES_FRESH_EXECUTION_RECOMPUTATION"] = (
        "UNKNOWN_REQUIRES_FRESH_EXECUTION_RECOMPUTATION"
    )
    reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    input_hash: str


def _same_curve(left: BoundaryResult, right: BoundaryResult) -> bool:
    fields = (
        "status",
        "safe_idle_cents",
        "minimum_margin_cents",
        "deficit_cents",
        "protected_cents_by_reason",
        "max_allocatable_by_product",
        "calculation_trace",
    )
    return all(getattr(left, name) == getattr(right, name) for name in fields)


def _delta(before: int | None, after: int | None) -> int | None:
    return after - before if before is not None and after is not None else None


def _curve(data: MultiTemplateChangeInput, base: BoundaryResult, digest: str) -> BoundaryResult:
    """Keep each actual FULL payment/floor and its exact original phase; add no income."""
    original = data.full_projection.original_annual_projection
    full = data.full_projection.full_annual_projection
    assert full is not None
    trace: list[BoundaryPoint] = []
    for candidate, old, protected in zip(
        base.calculation_trace, original.calculation_trace, full.calculation_trace, strict=True
    ):
        if protected.cash_cents > old.cash_cents or not set(old.protected_cents_by_reason) <= set(
            protected.protected_cents_by_reason
        ):
            raise ValueError("FULL_BURDEN_CANNOT_ADD_CASH_OR_DROP_ORIGINAL_FLOORS")
        extra = {
            key: amount - old.protected_cents_by_reason.get(key, 0)
            for key, amount in protected.protected_cents_by_reason.items()
        }
        if any(amount < 0 for amount in extra.values()):
            raise ValueError("FULL_BURDEN_CANNOT_RELEASE_ORIGINAL_PROTECTION")
        floors = dict(candidate.protected_cents_by_reason)
        for key, amount in extra.items():
            floors[key] = floors.get(key, 0) + amount
        cash = candidate.cash_cents + protected.cash_cents - old.cash_cents
        trace.append(
            BoundaryPoint(
                day=candidate.day,
                date=candidate.date,
                phase=candidate.phase,
                cash_cents=cash,
                protected_cents_by_reason=floors,
                margin_cents=cash - sum(floors.values()),
                obligation_occurrence_ids=sorted(
                    set(candidate.obligation_occurrence_ids)
                    | (
                        set(protected.obligation_occurrence_ids)
                        - set(old.obligation_occurrence_ids)
                    )
                ),
                principal_position_ids=candidate.principal_position_ids,
            )
        )
    risk = any(
        row.state == "SOURCE_LIQUIDITY_RISK" for row in data.full_projection.source_account_checks
    )
    worst = min(row.margin_cents for row in trace)
    capacities: dict[str, int | None] = {}
    for product in data.boundary_products:
        stop = (
            product.fixed_return.term_days + product.fixed_return.settlement_delay_days
            if product.fixed_return
            else 366
        )
        margins = [
            row.margin_cents
            for row in trace
            if row.day < stop or (row.day == stop and row.phase != "AFTER_PRINCIPAL")
        ]
        capacities[str(product.product_id)] = max(0, min(margins)) if worst >= 0 and not risk else 0
    return BoundaryResult(
        algorithm_version=PROTOCOL,
        status="LIQUIDITY_RISK" if worst < 0 or risk else "READY",
        safe_idle_cents=max(0, worst) if not risk else 0,
        minimum_margin_cents=worst,
        deficit_cents=max(0, -worst),
        protected_cents_by_reason=trace[0].protected_cents_by_reason,
        max_allocatable_by_product=capacities,
        calculation_trace=trace,
        blocking_constraints=full.blocking_constraints,
        boundary_hash=configuration_hash(
            {
                "protocol": PROTOCOL,
                "input_hash": digest,
                "trace": [p.model_dump(mode="json") for p in trace],
            }
        ),
        calculation_notes=[
            "UNCONFIRMED_HYPOTHESIS",
            "ACTUAL_FULL_FLOORS_AND_PAYMENTS_RETAINED",
            "FUTURE_INCOME_ZERO",
            "NO_ACTION_OR_AUTHORITY_CREATED",
        ],
    )


def _goal_variant(
    original: MultiGoalAllocationInput, config: GoalAllocationPolicy
) -> MultiGoalAllocationResult:
    ids = set(config.goal_ids)
    if not ids <= {row.goal_id for row in original.goals}:
        raise ValueError("CANDIDATE_GOAL_NOT_IN_COMPLETE_CURRENT_GOAL_DENOMINATOR")
    # The cap protects all original income and floors. No lot is cut or renamed.
    points = [
        row.model_copy(
            update={
                "other_protection_floor_cents": (
                    row.other_protection_floor_cents
                    + max(0, row.remaining_cents - config.max_single_allocation_cents)
                )
            }
        )
        for row in original.hard_protection_points
    ]
    goals = [
        row if row.goal_id in ids else row.model_copy(update={"policy_status": "SUSPENDED"})
        for row in original.goals
    ]
    return solve_multi_goal_allocation(
        original.model_copy(
            update={
                "hard_protection_points": points,
                "goals": goals,
            }
        )
    )


def _product_variant(
    data: MultiTemplateChangeInput,
    config: AssetAuthorizationPolicy,
    start: datetime,
    end: datetime | None,
) -> dict[UUID, tuple[int | None, list[str]]]:
    assert data.asset_input is not None
    actual = data.asset_input
    if config.scope != actual.configuration.scope or config.goal_id != actual.configuration.goal_id:
        raise ValueError("CANDIDATE_SCOPE_REQUIRES_A_NEW_COMPLETE_EXPOSURE_AND_GOAL_BINDING")
    variant = actual.model_copy(
        update={"configuration": config, "valid_from": start, "valid_until": end}
    )
    now = data.snapshot.as_of
    zone = ZoneInfo(data.snapshot.timezone)
    use_date = now.astimezone(zone).date() + timedelta(days=actual.options.comparison_days)
    if actual.options.funds_use_date is not None:
        use_date = min(use_date, actual.options.funds_use_date)
    if config.goal_id is not None:
        if not actual.goal_reference_verified or actual.goal_deadline is None:
            raise ValueError("ACTUAL_CURRENT_GOAL_REFERENCE_NOT_PROVEN")
        use_date = min(use_date, actual.goal_deadline)
    latest: dict[str, int] = {}
    for product in actual.products:
        if (
            product.created_at <= now
            and product.effective_from <= now
            and (product.effective_until is None or now < product.effective_until)
        ):
            latest[product.product_code] = max(
                latest.get(product.product_code, 0), product.version_number
            )
    exposure = actual.exposure
    owned = {
        a.account_id: sum(
            g.cash_owned_cents for g in actual.snapshot.goals if g.account_id == a.account_id
        )
        for a in actual.snapshot.cash_accounts
    }
    funding = sum(
        a.balance_cents
        - owned[a.account_id]
        - exposure.reserved_cash_by_account.get(a.account_id, 0)
        for a in actual.snapshot.cash_accounts
        if a.account_type == "CASH"
    )
    if config.goal_id is not None:
        goal = next((g for g in actual.snapshot.goals if g.goal_id == config.goal_id), None)
        if goal is None:
            raise ValueError("ACTUAL_GOAL_CASH_NOT_PROVEN")
        funding = goal.cash_owned_cents - exposure.reserved_goal_cash_by_goal.get(config.goal_id, 0)
    if funding < 0:
        raise ValueError("ORIGINAL_RESERVED_CASH_EXCEEDS_ACTUAL_SCOPE_CASH")
    remaining = max(
        0,
        config.max_auto_managed_cents
        - exposure.managed_principal_cents
        - exposure.pending_purchase_cents,
    )
    full = data.full_projection.full_annual_projection
    assert full is not None
    result = {}
    for product in actual.products:
        candidate = asset_candidate(product, variant, latest, use_date)
        reasons = list(candidate.reasons)
        if now < start or end is not None and now >= end:
            reasons.append("HYPOTHETICAL_CONFIGURATION_NOT_EFFECTIVE_NOW")
        cap: int | None = 0
        if not reasons and candidate.status == "FEASIBLE" and candidate.exit_plan is not None:
            available = candidate.exit_plan.principal_available_at.astimezone(zone).date()
            margins = [
                p.margin_cents
                for p in full.calculation_trace
                if p.date < available or (p.date == available and p.phase != "AFTER_PRINCIPAL")
            ]
            if available > full.calculation_trace[-1].date or not margins:
                cap = None
                reasons.append("ORIGINAL_365_RETURN_WINDOW_NOT_COVERED")
            else:
                cap = max(
                    0,
                    min(
                        funding,
                        remaining,
                        config.single_action_cap_cents,
                        min(margins) if config.goal_id is None else funding,
                    ),
                )
                if full.status != "READY":
                    cap = 0
                if cap < max(1, product.minimum_purchase_cents):
                    reasons.append("BELOW_ORIGINAL_PRODUCT_MINIMUM")
                    cap = 0
        result[product.product_id] = cap, sorted(set(reasons))
    return result


def derive_multi_template_impact(data: MultiTemplateChangeInput) -> MultiTemplateFinancialImpact:
    data = MultiTemplateChangeInput.model_validate(data.model_dump())
    digest = configuration_hash(data.model_dump(mode="json"))
    full = data.full_projection.full_annual_projection
    reasons = list(data.source_issues)
    canonical = (
        validate_configuration(data.candidate_configuration)
        if data.source_kind == "MVP_POLICY"
        else validate_full_configuration(data.template_name, data.candidate_configuration)
    )
    if canonical != data.candidate_configuration:
        raise ValueError("EXACT_CANONICAL_CANDIDATE_REQUIRED")
    if canonical["type"] != data.current_configuration["type"]:
        raise ValueError("CANDIDATE_TEMPLATE_CHANGE_NOT_ALLOWED")
    mapping = MVP_TYPE_MAPPING if data.source_kind == "MVP_POLICY" else FULL_TYPE_MAPPING
    if mapping.get(data.template_name) != canonical["type"]:
        raise ValueError("ORIGINAL_TEMPLATE_AND_CANONICAL_TYPE_DIFFER")
    baseline = compute_boundary(
        data.snapshot.model_copy(update={"horizon_days": 365}),
        data.boundary_versions,
        data.positions,
        data.boundary_products,
    )
    first = data.snapshot.as_of.astimezone(ZoneInfo(data.snapshot.timezone)).date()
    expected = [(day, first + timedelta(days=day), phase) for day in range(366) for phase in PHASES]
    if not _same_curve(baseline, data.full_projection.original_annual_projection):
        reasons.append("COMPLETE_ORIGINAL_MVP_ANNUAL_RECOMPUTATION_MISMATCH")
    recomputed = project_full_protection(
        FullProtectionProjectionInput(
            snapshot=data.snapshot,
            boundary_versions=data.boundary_versions,
            positions=data.positions,
            boundary_products=data.boundary_products,
            policies=data.full_sources,
            reserved_cash_by_account=data.reserved_cash_by_account,
            full_source_inventory_complete=not bool(data.source_issues),
            full_source_issues=data.source_issues,
        )
    )
    if recomputed != data.full_projection:
        reasons.append("COMPLETE_ACTUAL_FULL_PROJECTION_RECOMPUTATION_MISMATCH")
    if (
        full is None
        or data.full_projection.status == "UNKNOWN"
        or not data.full_projection.full_obligations_complete_within_registered_current_scope
    ):
        reasons.append("COMPLETE_CURRENT_FULL_PROTECTION_NOT_PROVEN")
    elif [(p.day, p.date, p.phase) for p in full.calculation_trace] != expected or (
        full.safe_idle_cents is None
        or full.minimum_margin_cents is None
        or set(full.max_allocatable_by_product)
        != {str(p.product_id) for p in data.boundary_products}
        or any(v is None for v in full.max_allocatable_by_product.values())
    ):
        reasons.append("COMPLETE_1098_PHASE_AND_PRODUCT_DENOMINATOR_NOT_PROVEN")

    def unknown(*extra: str) -> MultiTemplateFinancialImpact:
        return MultiTemplateFinancialImpact(
            status="UNKNOWN",
            scope="UNSUPPORTED",
            template_name=data.template_name,
            before=full,
            after=None,
            reasons=sorted(set(reasons + list(extra))),
            input_hash=digest,
        )

    if reasons:
        return unknown()
    assert full is not None
    common: dict[str, Any] = dict(
        template_name=data.template_name,
        before=full,
        input_hash=digest,
        limitations=[
            "CURRENT_PHYSICAL_OWNERSHIP_AND_PRINCIPAL_UNCHANGED",
            "FUTURE_ACTION_GENERATION_AND_CONFIRMATION_DIFFERENCE_NOT_PROVEN",
            "PREVIEW_HASH_IS_NOT_CONFIGURATION_CONFIRMATION_OR_BANK_AUTHORITY",
        ],
    )
    if data.source_kind == "MVP_POLICY" and data.template_name != "AssetAuthorizationPolicy":
        if (
            data.mvp_source is None
            or data.mvp_source_status is None
            or (data.mvp_source.policy_id, data.mvp_source.version_id)
            != (data.policy_id, data.version_id)
            or data.mvp_source.configuration != data.current_configuration
        ):
            return unknown("CURRENT_MVP_SOURCE_NOT_BOUND")
        assumption = PolicyChangeAssumption(
            user_id=data.user_id,
            policy_id=data.policy_id,
            source_version_id=data.version_id,
            configuration=canonical,
            configuration_hash=configuration_hash(canonical),
            timezone=data.snapshot.timezone,
            assumed_confirmation_at=data.snapshot.as_of,
            assumed_valid_from=data.candidate_valid_from,
            assumed_valid_until=data.candidate_valid_until,
            source_status=data.mvp_source_status,
        )
        changed = compute_policy_change_boundary(
            data.snapshot.model_copy(update={"horizon_days": 365}),
            data.boundary_versions,
            data.positions,
            data.boundary_products,
            source_version=data.mvp_source,
            assumption=assumption,
            living_estimate=data.living_estimate,
        ).boundary
        if changed.status == "INSUFFICIENT_EVIDENCE":
            return unknown(*(p.code for p in changed.blocking_constraints))
        after = _curve(data, changed, digest)
        assert full.safe_idle_cents is not None and full.minimum_margin_cents is not None
        assert after.safe_idle_cents is not None and after.minimum_margin_cents is not None
        return MultiTemplateFinancialImpact(
            **common,
            status="PROJECTED",
            scope="MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS",
            after=after,
            delta_safe_idle_cents=after.safe_idle_cents - full.safe_idle_cents,
            delta_minimum_margin_cents=after.minimum_margin_cents - full.minimum_margin_cents,
            delta_product_financial_capacity_cents={
                k: value
                for k, v in after.max_allocatable_by_product.items()
                if (value := _delta(full.max_allocatable_by_product[k], v)) is not None
            },
        )
    if data.template_name == "AssetAuthorizationPolicy" and data.asset_input is not None:
        asset_actual = data.asset_input
        if (
            asset_actual.policy_id != data.policy_id
            or asset_actual.policy_version_id != data.version_id
            or (
                asset_actual.snapshot != data.snapshot.model_copy(update={"horizon_days": 365})
                or asset_actual.positions != data.positions
                or asset_actual.boundary_versions != data.boundary_versions
                or asset_actual.boundary_products != data.boundary_products
                or not asset_actual.planning_confirmation_valid
                or data.snapshot.as_of < max(asset_actual.confirmed_at, asset_actual.valid_from)
                or (
                    asset_actual.valid_until is not None
                    and data.snapshot.as_of >= asset_actual.valid_until
                )
                or asset_actual.configuration.model_dump(mode="json") != data.current_configuration
            )
        ):
            return unknown("ACTUAL_ASSET_INPUT_BINDING_MISMATCH")
        try:
            product_before = _product_variant(
                data, asset_actual.configuration, asset_actual.valid_from, asset_actual.valid_until
            )
            product_after = _product_variant(
                data,
                AssetAuthorizationPolicy.model_validate(canonical),
                data.candidate_valid_from,
                data.candidate_valid_until,
            )
        except ValueError as error:
            return unknown(str(error))
        product_rows = [
            ProductCapacityChange(
                product_id=p.product_id,
                product_version=p.version_number,
                terms_digest=p.terms_digest,
                before_capacity_cents=product_before[p.product_id][0],
                after_capacity_cents=product_after[p.product_id][0],
                before_reasons=product_before[p.product_id][1],
                after_reasons=product_after[p.product_id][1],
                delta_cents=_delta(product_before[p.product_id][0], product_after[p.product_id][0]),
            )
            for p in asset_actual.products
        ]
        return MultiTemplateFinancialImpact(
            **common,
            status="PARTIAL",
            scope="INDIVIDUAL_PRODUCT_CAPACITY",
            after=full,
            delta_safe_idle_cents=0,
            delta_minimum_margin_cents=0,
            product_capacities=product_rows,
            reasons=["WHOLE_PORTFOLIO_AND_FUTURE_POSITION_DISPOSITION_NOT_COMPUTED"],
        )
    if data.template_name == "RecoveryPolicy" and data.recovery_input is not None:
        recovery_actual = data.recovery_input
        recovery_config = RecoveryPolicy.model_validate(canonical)
        if (
            recovery_actual.user_id != data.user_id
            or recovery_actual.policy_id != data.policy_id
            or (
                recovery_actual.policy_version_id != data.version_id
                or recovery_actual.snapshot
                != data.snapshot.model_copy(update={"horizon_days": 365})
                or recovery_actual.positions != data.positions
                or recovery_actual.boundary_versions != data.boundary_versions
                or recovery_actual.boundary_products != data.boundary_products
                or not recovery_actual.planning_confirmation_valid
                or not recovery_actual.linked_asset_policy.confirmation_valid
                or data.snapshot.as_of
                < max(recovery_actual.confirmed_at, recovery_actual.valid_from)
                or (
                    recovery_actual.valid_until is not None
                    and data.snapshot.as_of >= recovery_actual.valid_until
                )
                or recovery_actual.configuration.model_dump(mode="json")
                != data.current_configuration
            )
        ):
            return unknown("ACTUAL_RECOVERY_INPUT_BINDING_MISMATCH")
        if {p.position_id for p in data.positions if p.status != "REDEEMED"} != {
            h.original.position_id for h in recovery_actual.holdings
        }:
            return unknown("COMPLETE_ORIGINAL_RECOVERY_HOLDING_DENOMINATOR_NOT_PROVEN")
        if (recovery_config.scope, recovery_config.goal_id, recovery_config.asset_policy_id) != (
            recovery_actual.configuration.scope,
            recovery_actual.configuration.goal_id,
            recovery_actual.configuration.asset_policy_id,
        ):
            return unknown("CANDIDATE_RECOVERY_LINK_OR_SCOPE_REQUIRES_NEW_FULL_SOURCE_CAPTURE")
        linked_source = recovery_actual.linked_asset_policy
        if (
            linked_source.confirmed_at is None
            or linked_source.valid_from is None
            or (
                data.snapshot.as_of < max(linked_source.confirmed_at, linked_source.valid_from)
                or linked_source.valid_until is not None
                and data.snapshot.as_of >= linked_source.valid_until
            )
        ):
            return unknown("CURRENT_LINKED_ASSET_VERSION_NOT_EFFECTIVE_NOW")
        recovery_variant = recovery_actual.model_copy(update={"configuration": recovery_config})
        linked = _linked_configuration(recovery_actual)
        negatives = [p for p in full.calculation_trace if p.margin_cents < 0]
        deadline = recovery_actual.goal_deadline_at or (
            max(
                data.snapshot.as_of,
                datetime.combine(
                    negatives[0].date, datetime.min.time(), ZoneInfo(data.snapshot.timezone)
                ),
            )
            if negatives
            else None
        )
        recovery_rows = []
        for holding in recovery_actual.holdings:
            recovery_before = recovery_candidate(recovery_actual, holding, linked, deadline)
            recovery_after = recovery_candidate(recovery_variant, holding, linked, deadline)
            if data.snapshot.as_of < data.candidate_valid_from or (
                data.candidate_valid_until is not None
                and data.snapshot.as_of >= data.candidate_valid_until
            ):
                recovery_after = recovery_after.model_copy(
                    update={
                        "lossless_eligible": False,
                        "decision": "BLOCKED",
                        "reasons": [*recovery_after.reasons, "CANDIDATE_NOT_EFFECTIVE_NOW"],
                    }
                )

            def amount(value: FullRecoveryCandidate) -> int | None:
                if value.decision == "UNKNOWN" or value.on_time is None:
                    return None
                return value.net_cents if value.lossless_eligible and value.on_time else 0

            old_amount, new_amount = amount(recovery_before), amount(recovery_after)
            recovery_rows.append(
                RecoveryCandidateChange(
                    position_id=holding.original.position_id,
                    before=recovery_before.model_dump(mode="json"),
                    after=recovery_after.model_dump(mode="json"),
                    conditional_on_time_net_delta_cents=new_amount - old_amount
                    if old_amount is not None and new_amount is not None
                    else None,
                )
            )
        return MultiTemplateFinancialImpact(
            **common,
            status="PARTIAL",
            scope="WHOLE_POSITION_RECOVERY_CANDIDATES",
            after=full,
            delta_safe_idle_cents=0,
            delta_minimum_margin_cents=0,
            recovery_candidates=recovery_rows,
            reasons=["TRIGGERED_MULTI_POSITION_SCHEDULE_AND_FUTURE_BANK_EFFECT_NOT_COMPUTED"],
        )
    if data.template_name == "GoalAllocationPolicy" and data.joint_input is not None:
        joint_actual = data.joint_input
        if (
            joint_actual.user_id != data.user_id
            or joint_actual.as_of != data.snapshot.as_of
            or joint_actual.source_issues
        ):
            return unknown("ACTUAL_COMPLETE_JOINT_INPUT_NOT_PROVEN")
        if len(joint_actual.hard_protection_points) != 1098 or any(
            (p.date, p.cash_cents, p.remaining_cents) != (q.date, q.cash_cents, q.margin_cents)
            for p, q in zip(
                joint_actual.hard_protection_points, full.calculation_trace, strict=True
            )
        ):
            return unknown("COMPLETE_JOINT_FULL_1098_PHASE_BINDING_MISMATCH")
        if data.snapshot.as_of < data.candidate_valid_from or (
            data.candidate_valid_until is not None
            and data.snapshot.as_of >= data.candidate_valid_until
        ):
            return unknown("CANDIDATE_GOAL_RULE_NOT_EFFECTIVE_IN_CURRENT_PERIOD")
        try:
            goal_before = _goal_variant(
                joint_actual, GoalAllocationPolicy.model_validate(data.current_configuration)
            )
            goal_after = _goal_variant(joint_actual, GoalAllocationPolicy.model_validate(canonical))
        except ValueError as error:
            return unknown(str(error))
        if goal_before.status == "UNKNOWN" or goal_after.status == "UNKNOWN":
            return unknown(*goal_before.reasons, *goal_after.reasons)
        old_goal_amounts = {p.goal_id: p.amount_cents for p in goal_before.goals}
        deltas = {
            str(p.goal_id): _delta(old_goal_amounts[p.goal_id], p.amount_cents)
            for p in goal_after.goals
        }
        return MultiTemplateFinancialImpact(
            **common,
            status="PARTIAL",
            scope="CURRENT_JOINT_GOAL_ALLOCATION",
            after=full,
            delta_safe_idle_cents=0,
            delta_minimum_margin_cents=0,
            goal_allocation_before=goal_before,
            goal_allocation_after=goal_after,
            goal_allocation_delta_cents=deltas,
            reasons=["ALLOCATION_HYPOTHESIS_NOT_CURRENT_OWNERSHIP_OR_MULTI_PERIOD_OPTIMUM"],
        )
    specific = {
        "DatedExpensePolicy": "USE_UNCHANGED_DATED_V1_OR_VERIFIED_HISTORY_V2_PREVIEW",
        "PeriodicTransferPolicy": "USE_UNCHANGED_PERIODIC_V1_NEW_HISTORY_RECONSTRUCTION_NOT_PROVEN",
        "SeasonalReservePolicy": "CANDIDATE_CONFIG_HAS_NO_EXPLICIT_ORIGINAL_ADOPTION_FOR_NEW_HASH",
        "InterventionPolicy": "QUESTION_INTERVENTION_RULE_HAS_NO_MONETARY_IMPACT_FORMULA",
        "CrossGoalReallocationPolicy": "DEDICATED_RELEASE_SCOPE_CONSENT_AND_ACTUAL_EFFECT_REQUIRED",
        "LongTermGoalPolicy": "FULL_GOAL_MODEL_DUAL_HASH_CHANGE_NOT_THIS_MVP_CANDIDATE",
        "AssetAuthorizationPolicy": "COMPLETE_ACTUAL_ASSET_PLANNING_INPUT_NOT_CONNECTED",
        "RecoveryPolicy": "COMPLETE_ACTUAL_RECOVERY_HOLDING_INPUT_NOT_CONNECTED",
        "GoalAllocationPolicy": "COMPLETE_FULL_BOUND_JOINT_INPUT_NOT_CONNECTED",
    }
    return unknown(specific.get(data.template_name, "TEMPLATE_FINANCIAL_CONSUMER_NOT_IMPLEMENTED"))
