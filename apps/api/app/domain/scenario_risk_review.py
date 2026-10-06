"""Finite hypotheses over retained originals. None of these DTOs are bank facts."""

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.boundary import compute_boundary, compute_policy_change_boundary
from app.domain.boundary_types import (
    BillFact,
    BoundaryModel,
    BoundaryPoint,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
)
from app.domain.full_asset_allocation import (
    FullAssetPlanningInput,
    FullAssetPlanningResult,
    plan_full_assets,
)
from app.domain.policy_change_types import PolicyChangeAssumption
from app.domain.policy_configuration import (
    UUIDReference,
    configuration_hash,
    validate_configuration,
)
from pydantic import Field, StrictInt, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SmallMoney = Annotated[StrictInt, Field(ge=0, le=10_000_000)]


class BillHypothesis(BoundaryModel):
    kind: Literal["BILL"]
    bill_id: UUIDReference
    remaining_due_cents: SmallMoney
    due_offset_days: Annotated[StrictInt, Field(ge=-365, le=365)]


class GoalHypothesis(BoundaryModel):
    kind: Literal["GOAL"]
    goal_id: UUIDReference
    expected_version_id: UUIDReference
    monthly_min_cents: SmallMoney
    monthly_target_cents: SmallMoney
    monthly_max_cents: SmallMoney
    deadline_offset_days: Annotated[StrictInt, Field(ge=-365, le=365)]

    @model_validator(mode="after")
    def ordered_contribution(self) -> "GoalHypothesis":
        if not self.monthly_min_cents <= self.monthly_target_cents <= self.monthly_max_cents:
            raise ValueError("Hypothetical monthly contributions must remain ordered")
        return self


class FullPolicyHypothesis(BoundaryModel):
    kind: Literal["FULL_POLICY"]
    policy_id: UUIDReference
    expected_version_id: UUIDReference
    configuration: dict[str, Any]


class ProductHypothesis(BoundaryModel):
    kind: Literal["PRODUCT"]
    product_id: UUIDReference
    expected_version_number: Annotated[StrictInt, Field(ge=1)]
    asset_policy_id: UUIDReference
    expected_policy_version_id: UUIDReference
    risk_level: Annotated[StrictInt, Field(ge=0, le=5)]
    lock_days: Annotated[StrictInt, Field(ge=0, le=365)]
    redemption_delay_days: Annotated[StrictInt, Field(ge=0, le=365)]
    minimum_purchase_cents: SmallMoney
    early_withdrawal_loss_bps: Annotated[StrictInt, Field(ge=0, le=10000)]


Hypothesis = Annotated[
    BillHypothesis | GoalHypothesis | FullPolicyHypothesis | ProductHypothesis,
    Field(discriminator="kind"),
]


class ScenarioRiskRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    expected_source_hash: Digest
    expected_engine_hash: Digest
    hypothesis: Hypothesis


class ScenarioRiskFlags(BoundaryModel):
    simulation: Literal[True] = True
    read_only: Literal[True] = True
    hypothetical_only: Literal[True] = True
    grants_authority: Literal[False] = False
    executes_funds: Literal[False] = False
    writes_facts: Literal[False] = False
    changes_bank_originals: Literal[False] = False
    resets_history: Literal[False] = False
    receipt_verified: Literal[False] = False
    economic_verified: Literal[False] = False
    future_income_in_current_cash_cents: Literal[0] = 0
    future_income_in_execution_cents: Literal[0] = 0
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"


class ScenarioRiskCurve(BoundaryModel):
    status: Literal["READY", "LIQUIDITY_RISK"]
    safe_idle_cents: StrictInt
    minimum_margin_cents: StrictInt
    calculation_trace: Annotated[list[BoundaryPoint], Field(min_length=1098, max_length=1098)]
    curve_hash: Digest
    financial_capacity_is_authority: Literal[False] = False
    per_account_future_debit_allocation_verified: Literal[False] = False


class ProductSensitivity(BoundaryModel):
    original_product: AssetProductTerms
    hypothetical_product: AssetProductTerms
    before: FullAssetPlanningResult
    after: FullAssetPlanningResult
    original_selected_principal_cents: StrictInt
    hypothetical_selected_principal_cents: StrictInt
    original_early_loss_upper_bound_cents: StrictInt
    hypothetical_early_loss_upper_bound_cents: StrictInt
    loss_basis: Literal["CEILING_SELECTED_PRINCIPAL_TIMES_DECLARED_BPS"] = (
        "CEILING_SELECTED_PRINCIPAL_TIMES_DECLARED_BPS"
    )
    actual_fee_cents: Literal[None] = None
    actual_loss_cents: Literal[None] = None
    early_loss_consumed_by_optimizer: Literal[False] = False
    full_protection_consumed_by_optimizer: Literal[False] = False
    original_positions_unchanged: Literal[True] = True
    quotation_verified: Literal[False] = False


def _grid(rows: list[BoundaryPoint]) -> list[tuple[int, date, str]]:
    if len(rows) != 1098:
        raise ValueError("Complete initial day plus 365 days and three phases are required")
    first = rows[0].date
    expected = [
        (day, first + timedelta(days=day), phase)
        for day in range(366)
        for phase in ("BEFORE_PAYMENT", "AFTER_PAYMENT", "AFTER_PRINCIPAL")
    ]
    if [(row.day, row.date, row.phase) for row in rows] != expected:
        raise ValueError("Original curve grid is not complete and ordered")
    return expected


def retain_full_floors(
    original_mvp: BoundaryResult,
    original_full: BoundaryResult | None,
    hypothetical_mvp: BoundaryResult,
    *,
    source_account_risk: bool = False,
) -> ScenarioRiskCurve | None:
    """Preserve the actual FULL additive obligations/claims in every original phase.

    This is conditional aggregate cash, not a new per-account debit allocator.
    It cannot turn an unknown original or candidate into a known result.
    """
    if original_full is None or any(
        row.status == "INSUFFICIENT_EVIDENCE"
        for row in (original_mvp, original_full, hypothetical_mvp)
    ):
        return None
    expected = _grid(original_mvp.calculation_trace)
    if (
        _grid(original_full.calculation_trace) != expected
        or _grid(hypothetical_mvp.calculation_trace) != expected
    ):
        raise ValueError("Compared original and hypothetical grids differ")
    trace = []
    for base, full, candidate in zip(
        original_mvp.calculation_trace,
        original_full.calculation_trace,
        hypothetical_mvp.calculation_trace,
        strict=True,
    ):
        # FULL sources may add a floor/payment, but cannot release an MVP floor.
        keys = set(base.protected_cents_by_reason) | set(full.protected_cents_by_reason)
        extra = {
            key: full.protected_cents_by_reason.get(key, 0)
            - base.protected_cents_by_reason.get(key, 0)
            for key in keys
        }
        if any(value < 0 for value in extra.values()) or full.cash_cents > base.cash_cents:
            raise ValueError("Original FULL curve is not an additive conservative protection")
        protected = dict(candidate.protected_cents_by_reason)
        for key, value in extra.items():
            protected[key] = protected.get(key, 0) + value
        cash = candidate.cash_cents + full.cash_cents - base.cash_cents
        trace.append(
            BoundaryPoint(
                day=candidate.day,
                date=candidate.date,
                phase=candidate.phase,
                cash_cents=cash,
                protected_cents_by_reason=protected,
                margin_cents=cash - sum(protected.values()),
                obligation_occurrence_ids=sorted(
                    set(candidate.obligation_occurrence_ids)
                    | (set(full.obligation_occurrence_ids) - set(base.obligation_occurrence_ids))
                ),
                principal_position_ids=candidate.principal_position_ids,
            )
        )
    minimum = min(row.margin_cents for row in trace)
    return ScenarioRiskCurve(
        status="LIQUIDITY_RISK" if minimum < 0 or source_account_risk else "READY",
        safe_idle_cents=0 if source_account_risk else max(0, minimum),
        minimum_margin_cents=minimum,
        calculation_trace=trace,
        curve_hash=configuration_hash(
            {
                "protocol": "scenario-retained-full-floors-v1",
                "original_mvp": original_mvp.boundary_hash,
                "original_full": original_full.boundary_hash,
                "hypothetical_mvp": hypothetical_mvp.boundary_hash,
                "source_account_risk": source_account_risk,
                "trace": [point.model_dump(mode="json") for point in trace],
            }
        ),
    )


def bill_curve(
    snapshot: BoundarySnapshot,
    versions: list[BoundaryPolicyVersion],
    positions: list[BoundaryPosition],
    products: list[BoundaryProduct],
    hypothesis: BillHypothesis,
) -> tuple[BillFact, BoundaryResult]:
    snapshot = BoundarySnapshot.model_validate(snapshot.model_dump())
    source = next((row for row in snapshot.bills if row.bill_id == hypothesis.bill_id), None)
    if source is None or not source.evidence_ids:
        raise ValueError("A hypothesis requires the exact original sourced bill")
    due = source.due_date + timedelta(days=hypothesis.due_offset_days)
    if due < source.statement_date:
        raise ValueError("Hypothetical due date must not precede original statement")
    first = snapshot.as_of.astimezone(
        UTC if snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    ).date()
    # The original OVERDUE marker is preserved even if the hypothetical date moves.
    state = (
        "OVERDUE"
        if source.status == "OVERDUE" or due < first and hypothesis.remaining_due_cents > 0
        else "PAID"
        if hypothesis.remaining_due_cents == 0
        else "PARTIALLY_PAID"
        if source.paid_cents > 0
        else "UNPAID"
    )
    changed = BillFact.model_validate(
        {
            **source.model_dump(),
            "total_cents": source.paid_cents + hypothesis.remaining_due_cents,
            "due_date": due,
            "status": state,
            "evidence_ids": [],
        }
    )
    hypothetical = snapshot.model_copy(
        update={
            "horizon_days": 365,
            "bills": [changed if row.bill_id == source.bill_id else row for row in snapshot.bills],
        }
    )
    return source, compute_boundary(hypothetical, versions, positions, products)


def goal_curve(
    user_id: UUID,
    snapshot: BoundarySnapshot,
    versions: list[BoundaryPolicyVersion],
    positions: list[BoundaryPosition],
    products: list[BoundaryProduct],
    hypothesis: GoalHypothesis,
) -> tuple[BoundaryPolicyVersion, dict[str, Any], BoundaryResult]:
    goal = next((row for row in snapshot.goals if row.goal_id == hypothesis.goal_id), None)
    version = next(
        (row for row in versions if row.version_id == hypothesis.expected_version_id), None
    )
    if (
        goal is None
        or version is None
        or version.policy_id != goal.policy_id
        or not version.evidence_ids
    ):
        raise ValueError("The original goal, current source version and ownership are required")
    config = validate_configuration(version.configuration)
    if config["type"] != "goal_saving":
        raise ValueError("Only the original goal-savings parameters are consumed here")
    deadline = date.fromisoformat(config["deadline"]) + timedelta(
        days=hypothesis.deadline_offset_days
    )
    candidate = validate_configuration(
        {
            **config,
            "deadline": deadline.isoformat(),
            "monthly_contribution": {
                "min_cents": hypothesis.monthly_min_cents,
                "target_cents": hypothesis.monthly_target_cents,
                "max_cents": hypothesis.monthly_max_cents,
            },
        }
    )
    zone = UTC if snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    start = (
        datetime.combine(date.fromisoformat(candidate["valid_from"]), time.min, zone).astimezone(
            UTC
        )
        if candidate["valid_from"]
        else snapshot.as_of
    )
    end = (
        datetime.combine(
            date.fromisoformat(candidate["valid_until"]) + timedelta(days=1), time.min, zone
        ).astimezone(UTC)
        if candidate["valid_until"]
        else None
    )
    assumption = PolicyChangeAssumption(
        user_id=user_id,
        policy_id=version.policy_id,
        source_version_id=version.version_id,
        configuration=candidate,
        configuration_hash=configuration_hash(candidate),
        timezone=snapshot.timezone,
        assumed_confirmation_at=snapshot.as_of,
        assumed_valid_from=start,
        assumed_valid_until=end,
        source_status="ACTIVE" if version.valid_from <= snapshot.as_of else "CONFIRMED",
    )
    result = compute_policy_change_boundary(
        snapshot.model_copy(update={"horizon_days": 365}),
        versions,
        positions,
        products,
        source_version=version,
        assumption=assumption,
    ).boundary
    return version, candidate, result


def product_sensitivity(
    original: FullAssetPlanningInput,
    hypothesis: ProductHypothesis,
) -> ProductSensitivity:
    original = FullAssetPlanningInput.model_validate(original.model_dump())
    if (
        original.policy_id != hypothesis.asset_policy_id
        or original.policy_version_id != hypothesis.expected_policy_version_id
    ):
        raise ValueError("The original current planning policy differs")
    product = next(
        (
            row
            for row in original.products
            if row.product_id == hypothesis.product_id
            and row.version_number == hypothesis.expected_version_number
        ),
        None,
    )
    if product is None:
        raise ValueError("A hypothesis requires the exact immutable registered product version")
    terms = {**product.maturity_rule, "settlement_delay_days": hypothesis.redemption_delay_days}
    changed = AssetProductTerms.model_validate(
        {
            **product.model_dump(),
            "risk_level": hypothesis.risk_level,
            "lock_days": hypothesis.lock_days,
            "redemption_delay_days": hypothesis.redemption_delay_days,
            "minimum_purchase_cents": hypothesis.minimum_purchase_cents,
            "early_withdrawal_loss_bps": hypothesis.early_withdrawal_loss_bps,
            "maturity_rule": terms,
            "terms_digest": configuration_hash(terms),
        }
    )
    before = plan_full_assets(original)
    after = plan_full_assets(
        original.model_copy(
            update={
                "products": [
                    changed if row.product_id == product.product_id else row
                    for row in original.products
                ]
            }
        )
    )
    first = sum(row.amount_cents for row in before.batches if row.product_id == product.product_id)
    second = sum(row.amount_cents for row in after.batches if row.product_id == product.product_id)
    return ProductSensitivity(
        original_product=product,
        hypothetical_product=changed,
        before=before,
        after=after,
        original_selected_principal_cents=first,
        hypothetical_selected_principal_cents=second,
        original_early_loss_upper_bound_cents=(first * product.early_withdrawal_loss_bps + 9999)
        // 10000,
        hypothetical_early_loss_upper_bound_cents=(
            second * changed.early_withdrawal_loss_bps + 9999
        )
        // 10000,
    )
