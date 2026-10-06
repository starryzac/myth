"""Conditional whole-position recovery, preserving actual cash and original prices."""

from datetime import datetime, time, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.asset_allocation_types import FixedPrincipalTerms, PlannedPrincipalTerms
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BoundaryModel,
    BoundaryPoint,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
)
from app.domain.full_policy_configuration import (
    RecoveryPolicy,
    RecoveryTrigger,
    validate_full_configuration,
)
from app.domain.policy_configuration import MoneyCents, configuration_hash, validate_configuration
from app.domain.recovery import _validate_links
from app.domain.recovery_types import RecoveryPosition, RecoveryQuote
from pydantic import Field, StrictBool, StrictInt


class LinkedAssetPlanningPolicy(BoundaryModel):
    policy_id: UUID
    version_id: UUID
    kind: Literal["MVP_POLICY", "FULL_POLICY"]
    configuration: dict[str, Any]
    content_hash: str
    confirmation_valid: StrictBool
    confirmed_at: datetime | None
    valid_from: datetime | None
    valid_until: datetime | None = None
    evidence_ids: list[UUID] = Field(default_factory=list)


class FullRecoveryHolding(BoundaryModel):
    original: RecoveryPosition
    catalogue_version_id: UUID
    product_record_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    early_withdrawal_rule: dict[str, Any]
    maturity_at: datetime | None = None
    quote_source: Literal["BANK_CONFIRMED", "DERIVED_ORIGINAL_TERMS", "MISSING"]
    quote_issue: str | None = None


class FullRecoveryPlanningInput(BoundaryModel):
    user_id: UUID
    policy_id: UUID
    policy_version_id: UUID
    configuration: RecoveryPolicy
    planning_confirmation_valid: StrictBool
    confirmed_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    linked_asset_policy: LinkedAssetPlanningPolicy
    snapshot: BoundarySnapshot
    boundary_versions: list[BoundaryPolicyVersion]
    positions: Annotated[list[BoundaryPosition], Field(max_length=10000)]
    boundary_products: list[BoundaryProduct]
    holdings: Annotated[list[FullRecoveryHolding], Field(max_length=100)]
    goal_deadline_at: datetime | None = None
    goal_reference_verified: StrictBool = False
    planning_deadline_at: datetime | None = None
    observed_state_triggers: list[RecoveryTrigger] = Field(default_factory=list)


class FullRecoveryCandidate(BoundaryModel):
    position_id: UUID
    goal_id: UUID | None
    destination_account_id: UUID
    product_id: UUID
    product_version_number: StrictInt
    terms_digest: str
    catalogue_version_id: UUID
    product_record_hash: str
    original_policy_version_id: UUID | None
    principal_cents: MoneyCents
    original_quote: RecoveryQuote | None
    quote_source: Literal["BANK_CONFIRMED", "DERIVED_ORIGINAL_TERMS", "MISSING"]
    independent_loss_cents: MoneyCents | None
    fee_cents: MoneyCents | None
    net_cents: MoneyCents | None
    earliest_conditional_cash_at: datetime | None
    liquidity_rank: StrictInt | None
    on_time: StrictBool | None
    within_full_planning_limits: StrictBool
    lossless_eligible: StrictBool
    decision: Literal["ASK_ONCE", "BLOCKED", "UNKNOWN", "EXCLUDED_SCOPE"]
    reasons: list[str]
    conditional_impact_boundary: BoundaryResult | None = None
    bank_authority: Literal[False] = False


class FullRecoveryPlanningResult(BoundaryModel):
    algorithm_version: Literal["full-whole-position-recovery-v1"] = (
        "full-whole-position-recovery-v1"
    )
    user_id: UUID
    policy_id: UUID
    policy_version_id: UUID
    linked_asset_policy_id: UUID
    linked_asset_policy_version_id: UUID
    linked_asset_configuration_hash: str
    as_of: datetime
    planning_only: Literal[True] = True
    bank_authority: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    status: Literal[
        "NO_RECOVERY_NEEDED",
        "NOT_TRIGGERED",
        "CONDITIONAL_RECOVERY_PLAN",
        "LIQUIDITY_RISK",
        "UNKNOWN",
        "INACTIVE_POLICY",
    ]
    scope: Literal["general_idle_funds", "goal"]
    goal_id: UUID | None
    actual_boundary: BoundaryResult
    lossless_conditional_boundary: BoundaryResult | None
    deadline_at: datetime | None
    actual_scope_cash_cents: MoneyCents | None
    required_recovery_cents: MoneyCents | None
    conditional_on_time_recovery_cents: MoneyCents | None
    lossless_steps: list[FullRecoveryCandidate]
    candidates: list[FullRecoveryCandidate]
    uncovered_checkpoints: list[BoundaryPoint]
    first_sustained_safe_point: BoundaryPoint | None
    observed_triggers: list[RecoveryTrigger]
    reasons: list[str]
    input_hash: str


def independent_early_loss(principal_cents: int, loss_bps: int) -> int:
    """Ceil to integer cents from original principal; no float or production quote formula."""
    if (
        type(principal_cents) is not int
        or not 0 < principal_cents <= 9223372036854775807
        or type(loss_bps) is not int
        or not 0 <= loss_bps <= 10000
    ):
        raise ValueError("Loss requires strict original integer principal and basis points")
    return (principal_cents * loss_bps + 9999) // 10000


def _linked_configuration(data: FullRecoveryPlanningInput) -> dict[str, Any]:
    source = data.linked_asset_policy
    config = (
        validate_full_configuration("AssetAuthorizationPolicy", source.configuration)
        if source.kind == "FULL_POLICY"
        else validate_configuration(source.configuration)
    )
    if (
        config["type"] != "asset_authorization"
        or configuration_hash(config) != source.content_hash
        or source.policy_id != data.configuration.asset_policy_id
        or config["scope"] != data.configuration.scope
        or config.get("goal_id")
        != (str(data.configuration.goal_id) if data.configuration.goal_id else None)
    ):
        raise ValueError(
            "Linked current asset version must match the exact recovery scope and hash"
        )
    return config


def _project_net(
    snapshot: BoundarySnapshot,
    positions: list[BoundaryPosition],
    holding: FullRecoveryHolding,
    available_at: datetime,
) -> tuple[BoundarySnapshot, list[BoundaryPosition]]:
    """A hypothetical committed request, explicitly separate from actual originals."""
    original = holding.original
    quote = original.quote
    if quote is None:
        raise ValueError("A bound quote is required")
    immediate = available_at <= snapshot.as_of
    costs = quote.fee_cents + quote.loss_cents
    accounts = [
        row.model_copy(update={"balance_cents": row.balance_cents + quote.net_cents})
        if immediate and row.account_id == original.destination_account_id
        else row
        for row in snapshot.cash_accounts
    ]
    goals = []
    for goal in snapshot.goals:
        if goal.goal_id != original.goal_id:
            goals.append(goal)
            continue
        goals.append(
            goal.model_copy(
                update={
                    "cash_owned_cents": goal.cash_owned_cents
                    + (quote.net_cents if immediate else 0),
                    "principal_owned_cents": goal.principal_owned_cents
                    - (quote.principal_cents if immediate else costs),
                    "allocated_cents": goal.allocated_cents - costs,
                }
            )
        )
    updated = []
    for row in positions:
        if row.position_id != original.position_id:
            updated.append(row)
            continue
        updated.append(
            row.model_copy(
                update=(
                    {"status": "REDEEMED"}
                    if immediate
                    else {
                        "status": "REDEEMING",
                        "principal_cents": quote.net_cents,
                        "principal_available_at": available_at,
                        "availability_evidence_ids": quote.evidence_ids,
                    }
                )
            )
        )
    return snapshot.model_copy(update={"cash_accounts": accounts, "goals": goals}), updated


def _candidate(
    data: FullRecoveryPlanningInput,
    holding: FullRecoveryHolding,
    linked: dict[str, Any],
    deadline: datetime | None,
) -> FullRecoveryCandidate:
    original, now = holding.original, data.snapshot.as_of
    product, quote = original.product, original.quote
    position = next(row for row in data.positions if row.position_id == original.position_id)
    reasons: list[str] = []
    limits: list[str] = []
    unknown = False
    rank = None
    independent_loss = None
    earliest = None
    cost = None
    if quote is None or holding.quote_source == "MISSING":
        unknown = True
        reasons.append(holding.quote_issue or "ORIGINAL_PRICE_MISSING")
    else:
        if (
            not quote.request_at <= now < quote.expires_at
            or quote.principal_available_at - quote.request_at
            != timedelta(days=product.redemption_delay_days)
            or (holding.quote_source == "BANK_CONFIRMED" and not quote.evidence_ids)
        ):
            unknown = True
            reasons.append("ORIGINAL_QUOTE_WINDOW_OR_SOURCE_NOT_PROVEN")
        earliest = max(
            quote.principal_available_at, now + timedelta(days=product.redemption_delay_days)
        )
        cost = quote.fee_cents + quote.loss_cents
        independent_loss = 0
        try:
            if product.principal_fluctuation or product.risk_level > 0:
                reasons.append("PRINCIPAL_RISK_NOT_A_SAFE_RECOVERY")
            if product.maturity_rule.get("protocol") == "planned-principal-return-v1":
                terms = PlannedPrincipalTerms.model_validate(product.maturity_rule)
                if (
                    terms.settlement_delay_days != product.redemption_delay_days
                    or now < original.purchased_at + timedelta(days=product.lock_days)
                    or quote.kind != "REDEEM"
                    or cost != 0
                    or product.asset_class not in {"CASH_MGMT_T0", "CASH_MGMT_T1"}
                    or product.redemption_delay_days
                    != (0 if product.asset_class == "CASH_MGMT_T0" else 1)
                ):
                    raise ValueError("Original liquid contract does not match the quote")
                rank = 0 if product.asset_class == "CASH_MGMT_T0" else 1
            else:
                fixed = FixedPrincipalTerms.model_validate(product.maturity_rule)
                maturity = original.purchased_at + timedelta(days=fixed.term_days)
                if (
                    fixed.term_days < product.lock_days
                    or fixed.settlement_delay_days != product.redemption_delay_days
                    or holding.maturity_at != maturity
                    or product.asset_class
                    not in {
                        "FIXED_DEPOSIT",
                        "FIXED_DEPOSIT_7D",
                        "FIXED_DEPOSIT_30D",
                        "FIXED_DEPOSIT_90D",
                    }
                ):
                    raise ValueError("Original fixed maturity and settlement must match")
                if quote.kind == "EARLY_WITHDRAW":
                    independent_loss = independent_early_loss(
                        position.principal_cents, product.early_withdrawal_loss_bps
                    )
                    if (
                        holding.quote_source != "BANK_CONFIRMED"
                        or now >= maturity
                        or holding.early_withdrawal_rule
                        != {
                            "allowed": True,
                            "requires_confirmation_if_loss": True,
                            "loss_basis": "principal_cents",
                            "simulation": True,
                        }
                        or quote.loss_cents != independent_loss
                        or quote.fee_cents != 0
                    ):
                        raise ValueError(
                            "Original issued early price or independent integer loss differs"
                        )
                    rank = 3
                    reasons.append("EARLY_WITHDRAWAL_REQUIRES_ORIGINAL_LOSS_CONSENT")
                elif (
                    quote.kind == "MATURE"
                    and cost == 0
                    and now >= maturity + timedelta(days=fixed.settlement_delay_days)
                ):
                    rank = 2
                else:
                    raise ValueError("Unmatured fixed principal is not a zero-loss cash return")
        except (ValueError, TypeError, OverflowError):
            unknown = True
            independent_loss = None
            reasons.append("COMPLETE_ORIGINAL_TERMS_OR_PRICE_NOT_PROVEN")
        if position.principal_cents > data.configuration.single_action_cap_cents:
            limits.append("FULL_SINGLE_ACTION_CAP_EXCEEDED")
        if product.redemption_delay_days > data.configuration.max_redemption_delay_days:
            limits.append("FULL_DELAY_CAP_EXCEEDED")
        if quote.fee_cents > data.configuration.max_fee_cents:
            limits.append("FULL_FEE_CAP_EXCEEDED")
        if quote.loss_cents > data.configuration.max_loss_cents:
            limits.append("FULL_LOSS_CAP_EXCEEDED")
        if quote.kind == "EARLY_WITHDRAW" and not linked.get(
            "allow_early_withdrawal_with_penalty", False
        ):
            limits.append("CURRENT_EARLY_WITHDRAWAL_NOT_ALLOWED")
        mapped_class = product.asset_class
        if mapped_class == "FIXED_DEPOSIT" and linked.get("allowed_asset_classes"):
            if "FIXED_DEPOSIT" not in linked["allowed_asset_classes"]:
                term = product.maturity_rule.get("term_days")
                mapped_class = f"FIXED_DEPOSIT_{term}D"
        if mapped_class not in linked["allowed_asset_classes"]:
            limits.append("CURRENT_ASSET_CLASS_NOT_ALLOWED")
        if position.principal_cents > linked["single_action_cap_cents"]:
            limits.append("CURRENT_ASSET_SINGLE_ACTION_CAP_EXCEEDED")
        if product.redemption_delay_days > linked["max_redemption_delay_days"]:
            limits.append("CURRENT_ASSET_DELAY_CAP_EXCEEDED")
        if product.lock_days > linked["max_lock_days"]:
            limits.append("CURRENT_ASSET_LOCK_CAP_EXCEEDED")
        if product.risk_level > linked["max_principal_risk_level"]:
            limits.append("CURRENT_ASSET_RISK_CAP_EXCEEDED")
    same_scope = original.goal_id == data.configuration.goal_id
    if not same_scope:
        reasons.append("OTHER_GOAL_OR_GENERAL_OWNERSHIP_CANNOT_BE_MOVED")
    if position.status not in {"HELD", "MATURED"} or original.reserved_principal_cents:
        reasons.append("PENDING_OR_RESERVED_PRINCIPAL_CANNOT_BE_REQUESTED_AGAIN")
    if original.acquisition != "AUTHORIZED_PURCHASE" or original.original_authorization is None:
        reasons.append("ORIGINAL_PURCHASE_AUTHORIZATION_NOT_PROVEN")
        if original.acquisition == "UNKNOWN":
            unknown = True
    authority = original.original_authorization
    if authority is not None and (
        authority.confirmed_at > original.purchased_at
        or authority.valid_from > original.purchased_at
        or (authority.valid_until is not None and original.purchased_at >= authority.valid_until)
        or authority.configuration.get("scope")
        != ("goal" if original.goal_id else "general_idle_funds")
        or authority.configuration.get("goal_id")
        != (str(original.goal_id) if original.goal_id else None)
    ):
        unknown = True
        reasons.append("ORIGINAL_ACQUISITION_VERSION_WINDOW_OR_SCOPE_NOT_PROVEN")
    if not product.auto_redeem_allowed:
        reasons.append("ORIGINAL_PRODUCT_REDEMPTION_PERMISSION_MISSING")
    if not data.configuration.allow_auto_recovery_without_penalty:
        reasons.append("FULL_ZERO_LOSS_RECOVERY_NOT_CONFIRMED")
    on_time = earliest <= deadline if earliest is not None and deadline is not None else None
    if on_time is False:
        reasons.append("PRINCIPAL_CANNOT_ARRIVE_BEFORE_REQUIRED_TIME")
    reasons.extend(limits)
    blocking = [
        reason
        for reason in reasons
        if reason
        not in {
            "PRINCIPAL_CANNOT_ARRIVE_BEFORE_REQUIRED_TIME",
            "EARLY_WITHDRAWAL_REQUIRES_ORIGINAL_LOSS_CONSENT",
        }
    ]
    lossless = (
        same_scope and not unknown and not blocking and cost == 0 and rank is not None and rank < 3
    )
    impact = None
    if same_scope and not unknown and quote is not None and earliest is not None and rank == 3:
        projected, projected_positions = _project_net(
            data.snapshot, data.positions, holding, earliest
        )
        impact = compute_boundary(
            projected, data.boundary_versions, projected_positions, data.boundary_products
        )
    return FullRecoveryCandidate(
        position_id=original.position_id,
        goal_id=original.goal_id,
        destination_account_id=original.destination_account_id,
        product_id=product.product_id,
        product_version_number=product.version_number,
        terms_digest=product.terms_digest,
        catalogue_version_id=holding.catalogue_version_id,
        product_record_hash=holding.product_record_hash,
        original_policy_version_id=original.original_authorization.version_id
        if original.original_authorization
        else None,
        principal_cents=position.principal_cents,
        original_quote=quote,
        quote_source=holding.quote_source,
        independent_loss_cents=independent_loss,
        fee_cents=quote.fee_cents if quote is not None and not unknown else None,
        net_cents=quote.net_cents if quote is not None and not unknown else None,
        earliest_conditional_cash_at=earliest if not unknown else None,
        liquidity_rank=rank,
        on_time=on_time if not unknown else None,
        within_full_planning_limits=not limits,
        lossless_eligible=lossless,
        decision="EXCLUDED_SCOPE"
        if not same_scope
        else ("UNKNOWN" if unknown else ("ASK_ONCE" if lossless or rank == 3 else "BLOCKED")),
        reasons=reasons or ["CONDITIONAL_ZERO_LOSS_ONLY_BANK_GRANT_NOT_PROVIDED"],
        conditional_impact_boundary=impact,
    )


def plan_full_recovery(data: FullRecoveryPlanningInput) -> FullRecoveryPlanningResult:
    data = FullRecoveryPlanningInput.model_validate(data.model_dump())
    now = data.snapshot.as_of
    linked = _linked_configuration(data)
    _validate_links(
        data.snapshot, data.positions, [row.original for row in data.holdings], [], data.user_id
    )
    actual = compute_boundary(
        data.snapshot, data.boundary_versions, data.positions, data.boundary_products
    )
    goal_id = data.configuration.goal_id
    goal = next((row for row in data.snapshot.goals if row.goal_id == goal_id), None)
    required = (
        actual.deficit_cents if goal_id is None else (goal.principal_owned_cents if goal else None)
    )
    scope_cash = (
        sum(row.balance_cents for row in data.snapshot.cash_accounts if row.account_type == "CASH")
        if goal_id is None
        else (goal.cash_owned_cents if goal else None)
    )
    negatives = [point for point in actual.calculation_trace if point.margin_cents < 0]
    natural_deadline = (
        data.goal_deadline_at
        if goal_id is not None
        else (
            max(now, datetime.combine(negatives[0].date, time(), ZoneInfo(data.snapshot.timezone)))
            if negatives
            else None
        )
    )
    deadline = data.planning_deadline_at or natural_deadline
    if deadline is not None and (
        deadline < now or (natural_deadline and deadline > natural_deadline)
    ):
        raise ValueError("A planning deadline may only tighten the original required time")
    observed_set: set[RecoveryTrigger] = set(data.observed_state_triggers)
    if negatives or (goal_id is not None and required):
        observed_set.add("LIQUIDITY_SHORTFALL")
    observed = sorted(observed_set)
    reasons: list[str] = []
    source = data.linked_asset_policy
    active = (
        data.planning_confirmation_valid
        and data.confirmed_at <= now
        and data.valid_from <= now
        and (data.valid_until is None or now < data.valid_until)
    )
    linked_active = (
        source.confirmation_valid
        and source.confirmed_at is not None
        and source.confirmed_at <= now
        and source.valid_from is not None
        and source.valid_from <= now
        and (source.valid_until is None or now < source.valid_until)
    )
    metadata_complete = {row.position_id for row in data.positions if row.status != "REDEEMED"} <= {
        row.original.position_id for row in data.holdings
    }
    unknown = (
        actual.status == "INSUFFICIENT_EVIDENCE"
        or not linked_active
        or not metadata_complete
        or (
            goal_id is not None
            and (not data.goal_reference_verified or goal is None or deadline is None)
        )
    )
    if not linked_active:
        reasons.append("CURRENT_LINKED_ASSET_CONFIRMATION_NOT_PROVEN")
    if not metadata_complete:
        reasons.append("COMPLETE_POSITION_METADATA_NOT_PROVEN")
    if any(
        row.status != "REDEEMED"
        and row.principal_available_at is not None
        and row.principal_available_at <= now
        for row in data.positions
    ):
        unknown = True
        reasons.append("ORIGINAL_BANK_SETTLEMENT_RECONCILIATION_REQUIRED")
    candidates = [_candidate(data, row, linked, deadline) for row in data.holdings]
    candidates.sort(
        key=lambda row: (
            row.liquidity_rank if row.liquidity_rank is not None else 99,
            row.independent_loss_cents
            if row.independent_loss_cents is not None
            else 9223372036854775807,
            row.earliest_conditional_cash_at or datetime.max.replace(tzinfo=now.tzinfo),
            str(row.position_id),
        )
    )
    trigger = bool(set(observed) & set(data.configuration.triggers))
    working, positions, conditional = data.snapshot, data.positions, actual
    selected: list[FullRecoveryCandidate] = []
    on_time_total = 0
    if active and not unknown and trigger and required:
        holdings = {row.original.position_id: row for row in data.holdings}
        for candidate in candidates:
            if not candidate.lossless_eligible or candidate.earliest_conditional_cash_at is None:
                continue
            projected, updated = _project_net(
                working,
                positions,
                holdings[candidate.position_id],
                candidate.earliest_conditional_cash_at,
            )
            proposed = compute_boundary(
                projected, data.boundary_versions, updated, data.boundary_products
            )
            improves = (
                any(
                    after.margin_cents > before.margin_cents
                    for before, after in zip(
                        conditional.calculation_trace, proposed.calculation_trace, strict=True
                    )
                    if before.margin_cents < 0
                )
                if goal_id is None
                else on_time_total < required
            )
            if proposed.status == "INSUFFICIENT_EVIDENCE" or not improves:
                continue
            selected.append(candidate)
            working, positions, conditional = projected, updated, proposed
            if candidate.on_time and candidate.net_cents is not None:
                on_time_total += candidate.net_cents
    uncovered = [point for point in conditional.calculation_trace if point.margin_cents < 0]
    last_bad = max(
        (i for i, point in enumerate(conditional.calculation_trace) if point.margin_cents < 0),
        default=-1,
    )
    first_safe = (
        conditional.calculation_trace[last_bad + 1]
        if last_bad + 1 < len(conditional.calculation_trace)
        else None
    )
    if unknown:
        status: Literal[
            "NO_RECOVERY_NEEDED",
            "NOT_TRIGGERED",
            "CONDITIONAL_RECOVERY_PLAN",
            "LIQUIDITY_RISK",
            "UNKNOWN",
            "INACTIVE_POLICY",
        ] = "UNKNOWN"
    elif not active:
        status = "INACTIVE_POLICY"
    elif not required:
        status = "NO_RECOVERY_NEEDED"
    elif not trigger:
        status = "NOT_TRIGGERED"
        reasons.append("NO_REGISTERED_TRIGGER_OBSERVED")
    elif selected and not uncovered and (goal_id is None or on_time_total >= required):
        status = "CONDITIONAL_RECOVERY_PLAN"
    else:
        status = "LIQUIDITY_RISK"
        reasons.append("PARTIAL_LATE_OR_LOSS_ONLY_NOT_A_COMPLETE_CASH_SOLUTION")
    return FullRecoveryPlanningResult(
        user_id=data.user_id,
        policy_id=data.policy_id,
        policy_version_id=data.policy_version_id,
        linked_asset_policy_id=source.policy_id,
        linked_asset_policy_version_id=source.version_id,
        linked_asset_configuration_hash=source.content_hash,
        as_of=now,
        status=status,
        scope=data.configuration.scope,
        goal_id=goal_id,
        actual_boundary=actual,
        lossless_conditional_boundary=conditional if not unknown else None,
        deadline_at=deadline,
        actual_scope_cash_cents=scope_cash if not unknown else None,
        required_recovery_cents=required if not unknown else None,
        conditional_on_time_recovery_cents=on_time_total if not unknown else None,
        lossless_steps=selected,
        candidates=candidates,
        uncovered_checkpoints=uncovered,
        first_sustained_safe_point=first_safe if not unknown else None,
        observed_triggers=observed,
        reasons=reasons,
        input_hash=configuration_hash(data.model_dump(mode="json")),
    )
