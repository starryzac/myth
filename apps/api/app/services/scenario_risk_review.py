"""Current-source, read-only review of four independent counterfactual families."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db.models import Goal, Policy, PolicyVersion
from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.asset_exposure import AssetExposure
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import BillFact, BoundaryPolicyVersion
from app.domain.full_asset_allocation import FullAssetPlanningInput, FullAssetPlanOptions
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.domain.policy_configuration import configuration_hash
from app.domain.scenario_risk_review import (
    BillHypothesis,
    FullPolicyHypothesis,
    GoalHypothesis,
    ProductSensitivity,
    ScenarioRiskCurve,
    ScenarioRiskFlags,
    ScenarioRiskRequest,
    bill_curve,
    goal_curve,
    product_sensitivity,
    retain_full_floors,
)
from app.services.asset_allocation import _goal_projection_matches
from app.services.audit_chain import row_copy
from app.services.boundary import BoundaryContext, BoundarySourceIssue
from app.services.dashboard_helpers import current_epoch_audit
from app.services.dashboard_types import DashboardAuditCard
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_goals import FullGoalModelResponse, read_full_goal_model
from app.services.full_policy_change_impact import (
    FullPolicyChangeFinancialPreview,
    preview_full_policy_financial_impact,
)
from app.services.full_policy_lifecycle import (
    FullPolicyView,
    FullPreviewRequest,
    _read_snapshot,
    list_full_policies,
)
from app.services.full_protection_projection import (
    FullAnnualProtectionResponse,
    compute_full_annual_protection,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now, is_version_authorized
from app.services.product_catalog import VerifiedCatalogProducts, verified_catalog_products
from app.services.scenario_simulation import ENGINE_FILES as ORIGINAL_ENGINE_FILES
from app.services.scenario_simulation import source_fingerprint
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

ENGINE_FILES = (
    *ORIGINAL_ENGINE_FILES,
    "domain/scenario_risk_review.py",
    "services/scenario_risk_review.py",
    "api/v1/scenario_risk_review.py",
    "domain/full_protection_projection.py",
    "services/full_protection_projection.py",
    "domain/full_policy_change_impact.py",
    "services/full_policy_change_impact.py",
    "domain/full_asset_allocation.py",
    "domain/asset_allocation.py",
    "domain/asset_allocation_types.py",
    "domain/full_policy_configuration.py",
    "services/full_policy_lifecycle.py",
    "services/product_catalog.py",
    "services/full_goals.py",
    "domain/full_seasonal_protection.py",
    "domain/full_future_dated_history.py",
    "services/full_future_dated_history.py",
    "services/full_payment_permissions.py",
    "domain/policy_change_types.py",
)

LIMITATIONS = [
    "ONE_HYPOTHESIS_AT_A_TIME_NOT_A_JOINT_OPTIMAL_PLAN",
    "HYPOTHETICAL_REMAINING_BILL_IS_NOT_BANK_PAYMENT_OR_A_NEW_STATEMENT",
    "GOAL_ENGINE_CONSUMES_BASE_MONTHLY_AND_DEADLINE_NOT_ALL_FULL_ATTRIBUTES",
    "FULL_FINANCIAL_CANDIDATE_SUPPORT_IS_REPORTED_BY_THE_ORIGINAL_PREVIEW",
    "PRODUCT_OPTIMIZER_USES_ORIGINAL_MVP_365_SCOPE_NOT_FULL_FLOOR_EXECUTION",
    "EARLY_LOSS_BOUND_IS_HYPOTHETICAL_NOT_A_QUOTE_AND_NOT_CONSUMED_BY_OPTIMIZER",
    "CURRENT_GOAL_OWNERSHIP_AND_ALL_ORIGINAL_HELD_PRINCIPAL_DATES_ARE_UNCHANGED",
    "PAGE_RESET_CLEARS_LOCAL_PARAMETERS_ONLY_NO_DATABASE_RESET",
]


class RiskGoalChoice(ScenarioRiskFlags):
    goal_id: UUID
    ownership_original: dict[str, Any]
    source_version: BoundaryPolicyVersion
    full_model: FullGoalModelResponse | None
    full_model_issue: str | None


class RiskFullPolicyChoice(ScenarioRiskFlags):
    policy_id: UUID
    version_id: UUID
    template_name: str
    configuration: dict[str, Any]
    configuration_hash: str
    effective_status: str
    planning_confirmation_valid: bool


class ScenarioRiskContext(ScenarioRiskFlags):
    protocol: Literal["scenario-risk-context-v1"] = "scenario-risk-context-v1"
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    local_date: str
    source_hash: str
    engine_hash: str
    engine_files: dict[str, str]
    bills: list[BillFact]
    goals: list[RiskGoalChoice]
    full_policies: list[RiskFullPolicyChoice]
    products: list[AssetProductTerms]
    catalogue_status: Literal["VERIFIED", "UNKNOWN"]
    baseline_full: ScenarioRiskCurve | None
    original_full_protection: FullAnnualProtectionResponse
    source_issues: list[BoundarySourceIssue]
    inventory_counts: dict[str, int]
    limitations: list[str] = Field(default_factory=lambda: list(LIMITATIONS))


class ScenarioRiskComparison(ScenarioRiskFlags):
    protocol: Literal["scenario-risk-comparison-v1"] = "scenario-risk-comparison-v1"
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    source_hash: str
    engine_hash: str
    original_request: ScenarioRiskRequest
    request_hash: str
    comparison_hash: str
    status: Literal["PROJECTED", "UNKNOWN"]
    family: Literal["BILL", "GOAL", "FULL_POLICY", "PRODUCT"]
    original_selected: dict[str, Any]
    hypothetical_selected: dict[str, Any]
    baseline_full: ScenarioRiskCurve | None
    hypothetical_full: ScenarioRiskCurve | None
    delta_safe_idle_cents: int | None
    full_policy_preview: FullPolicyChangeFinancialPreview | None = None
    product_preview: ProductSensitivity | None = None
    reasons: list[str]
    limitations: list[str] = Field(default_factory=lambda: list(LIMITATIONS))


@dataclass(frozen=True)
class Captured:
    financial: BoundaryContext
    exposures: list[AssetExposure] | None
    audit: DashboardAuditCard
    annual: FullAnnualProtectionResponse
    full_views: list[FullPolicyView]
    catalogue: VerifiedCatalogProducts
    review: ScenarioRiskContext


def engine_sources() -> tuple[str, dict[str, str]]:
    root = Path(__file__).resolve().parents[1]
    files = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in ENGINE_FILES}
    return configuration_hash(files), files


def _stable(expected: str) -> None:
    if engine_sources()[0] != expected:
        raise PolicyLifecycleError(
            "SCENARIO_REVIEW_ENGINE_CHANGED", "计算期间原引擎源变动，请重新读取", 409
        )


def _source_hash(
    context: BoundaryContext,
    audit: DashboardAuditCard,
    full: list[FullPolicyView],
    catalog: VerifiedCatalogProducts,
    models: list[RiskGoalChoice],
) -> str:
    if len(context.sources.evidence) > 10000:
        raise PolicyLifecycleError(
            "SCENARIO_REVIEW_CAPACITY", "完整原证据超过本次10000项容量，不截断", 409
        )
    return configuration_hash(
        {
            "protocol": "scenario-risk-source-v1",
            "original_financial_sources": source_fingerprint(context, audit),
            "full_current_originals": [row.model_dump(mode="json") for row in full],
            "immutable_catalogue": catalog.model_dump(mode="json"),
            "goal_current_originals": [row.model_dump(mode="json") for row in models],
            # Raw consulted original metadata includes hashes/status/times and payloads.
            # No blanket removal of timestamps or original evidence content.
            "evidence_originals": [
                row_copy(row) for _, row in sorted(context.sources.evidence.items())
            ],
        }
    )


def _capture(session: Session, user_id: UUID, now: datetime) -> Captured:
    _read_snapshot(session)
    now = _now(now)
    with session.no_autoflush:
        context, _, exposures = load_verified_financial_context(session, user_id, now)
        audit = current_epoch_audit(session, user_id, [])
        if audit.epoch_id is None:
            raise PolicyLifecycleError("SCENARIO_REVIEW_EPOCH_MISSING", "需要当前原轮次", 409)
        if not audit.complete or audit.status != "VALID":
            context.sources.issue(
                "CURRENT_AUDIT_NOT_PROVEN", audit.epoch_id, "原审计不完整，金额保持未知"
            )
        context, issues, _ = finalize_financial_context(context, audit)
        annual = compute_full_annual_protection(session, user_id, now)
        views = list_full_policies(session, user_id, now).items
        catalog = verified_catalog_products(session, now)
        if len(views) > 200 or len(catalog.products) > 100:
            raise PolicyLifecycleError(
                "SCENARIO_REVIEW_CAPACITY", "完整策略/产品超过200/100容量，不截断", 409
            )
        goals = []
        for ownership in context.snapshot.goals:
            model, issue = None, None
            try:
                model = read_full_goal_model(session, user_id, ownership.goal_id, now)
            except PolicyLifecycleError as error:
                issue = error.code
            if model is None:
                # A retained ownership row alone does not identify a current,
                # eligible policy version. Keep the complete goal denominator.
                continue
            version = next(
                (
                    row
                    for row in context.versions
                    if row.policy_id == ownership.policy_id
                    and (model is None or row.version_id == model.base_policy_version_id)
                ),
                None,
            )
            if version is None:
                continue
            goals.append(
                RiskGoalChoice(
                    goal_id=ownership.goal_id,
                    ownership_original=ownership.model_dump(mode="json"),
                    source_version=version,
                    full_model=model,
                    full_model_issue=issue,
                )
            )
        digest, files = engine_sources()
        original = annual.projection.original_annual_projection
        full_curve = annual.projection.full_annual_projection
        source_risk = any(
            row.state == "SOURCE_LIQUIDITY_RISK" for row in annual.projection.source_account_checks
        )
        baseline = retain_full_floors(
            original, full_curve, original, source_account_risk=source_risk
        )
        review = ScenarioRiskContext(
            user_id=user_id,
            epoch_id=audit.epoch_id,
            as_of=now,
            timezone=context.snapshot.timezone,
            local_date=now.astimezone(ZoneInfo(context.snapshot.timezone)).date().isoformat(),
            source_hash=_source_hash(context, audit, views, catalog, goals),
            engine_hash=digest,
            engine_files=files,
            bills=context.snapshot.bills,
            goals=goals,
            full_policies=[
                RiskFullPolicyChoice(
                    policy_id=row.policy_id,
                    version_id=row.current_version.version_id,
                    template_name=row.template_name,
                    configuration=row.current_version.configuration,
                    configuration_hash=row.current_version.content_hash,
                    effective_status=row.effective_status,
                    planning_confirmation_valid=row.planning_confirmation_valid,
                )
                for row in views
            ],
            products=catalog.products if catalog.status == "VERIFIED" else [],
            catalogue_status=catalog.status,
            baseline_full=baseline,
            original_full_protection=annual,
            source_issues=sorted(
                issues + annual.source_issues, key=lambda row: (row.code, row.source_ref)
            ),
            inventory_counts={
                "bills": len(context.snapshot.bills),
                "goals": len(context.snapshot.goals),
                "goal_choices": len(goals),
                "full_policies": len(views),
                "catalogue_products": len(catalog.products),
                "eligible_product_choices": len(catalog.products)
                if catalog.status == "VERIFIED"
                else 0,
            },
        )
        _stable(digest)
        return Captured(context, exposures, audit, annual, views, catalog, review)


def read_scenario_risk_context(
    session: Session, user_id: UUID, now: datetime
) -> ScenarioRiskContext:
    return _capture(session, user_id, now).review


def _product_input(
    session: Session,
    data: Captured,
    user_id: UUID,
    policy_id: UUID,
    now: datetime,
) -> FullAssetPlanningInput | None:
    view = next((row for row in data.full_views if row.policy_id == policy_id), None)
    if view is None or view.template_name != "AssetAuthorizationPolicy":
        raise PolicyLifecycleError("SCENARIO_REVIEW_ASSET_SCOPE", "需要原完整资产规划声明", 409)
    if (
        data.catalogue.status != "VERIFIED"
        or data.exposures is None
        or data.review.baseline_full is None
        or data.review.source_issues
    ):
        return None
    config = AssetAuthorizationPolicy.model_validate(view.current_version.configuration)
    exposure = next((row for row in data.exposures if row.goal_id == config.goal_id), None)
    if exposure is None:
        exposure = AssetExposure(
            as_of=now,
            scope=config.scope,
            goal_id=config.goal_id,
            managed_principal_cents=0,
            pending_purchase_cents=0,
            reserved_cash_by_account={
                key: value
                for row in data.exposures
                for key, value in row.reserved_cash_by_account.items()
            },
            reserved_goal_cash_by_goal={
                key: value
                for row in data.exposures
                for key, value in row.reserved_goal_cash_by_goal.items()
            },
            excluded_manual_position_ids=sorted(
                {key for row in data.exposures for key in row.excluded_manual_position_ids}
            ),
            evidence_ids=sorted({key for row in data.exposures for key in row.evidence_ids}),
        )
    deadline, version_id, verified = None, None, False
    if config.goal_id is not None:
        goal = session.get(Goal, config.goal_id)
        policy = session.get(Policy, goal.policy_id) if goal is not None else None
        version = (
            session.scalar(
                select(PolicyVersion)
                .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == goal.policy_id)
                .order_by(PolicyVersion.version_number.desc())
                .limit(1)
            )
            if goal is not None
            else None
        )
        verified = bool(
            goal is not None
            and goal.user_id == user_id
            and policy is not None
            and policy.user_id == user_id
            and version is not None
            and goal.policy_version_id == version.id
            and _goal_projection_matches(goal, version)
            and is_version_authorized(session, user_id, version.id, now)
        )
        if not verified or goal is None or version is None:
            return None
        deadline, version_id = goal.deadline, version.id
    return FullAssetPlanningInput(
        snapshot=data.financial.snapshot.model_copy(update={"horizon_days": 365}),
        boundary_versions=data.financial.versions,
        positions=data.financial.positions,
        boundary_products=data.financial.products,
        products=data.catalogue.products,
        exposure=exposure,
        policy_id=policy_id,
        policy_version_id=view.current_version.version_id,
        configuration=config,
        planning_confirmation_valid=view.planning_confirmation_valid,
        confirmed_at=view.current_version.confirmed_at,
        valid_from=view.current_version.valid_from,
        valid_until=view.current_version.valid_until,
        goal_deadline=deadline,
        goal_policy_version_id=version_id,
        goal_reference_verified=verified,
        options=FullAssetPlanOptions(),
    )


def compare_scenario_risk(
    session: Session,
    user_id: UUID,
    now: datetime,
    body: ScenarioRiskRequest,
) -> ScenarioRiskComparison:
    data = _capture(session, user_id, now)
    review = data.review
    if (body.expected_epoch_id, body.expected_source_hash, body.expected_engine_hash) != (
        review.epoch_id,
        review.source_hash,
        review.engine_hash,
    ):
        raise PolicyLifecycleError(
            "STALE_SCENARIO_RISK_SOURCE", "轮次、原件或引擎已变，请重新读取后审阅", 409
        )
    original: dict[str, Any] = {}
    hypothetical: dict[str, Any] = {}
    curve = None
    full_preview = None
    product = None
    reasons: list[str] = []
    assumption = body.hypothesis
    try:
        if isinstance(assumption, (BillHypothesis, GoalHypothesis)):
            if isinstance(assumption, BillHypothesis):
                source, candidate = bill_curve(
                    data.financial.snapshot,
                    data.financial.versions,
                    data.financial.positions,
                    data.financial.products,
                    assumption,
                )
                original = source.model_dump(mode="json")
                hypothetical = {
                    **assumption.model_dump(mode="json"),
                    "original_paid_cents_retained": source.paid_cents,
                    "original_overdue_retained": source.status == "OVERDUE",
                    "hypothetical_due_date": (
                        source.due_date + timedelta(days=assumption.due_offset_days)
                    ).isoformat(),
                }
            else:
                if not any(
                    row.goal_id == assumption.goal_id
                    and row.source_version.version_id == assumption.expected_version_id
                    for row in review.goals
                ):
                    raise ValueError("The current source-bound goal version is not eligible")
                selected, config, candidate = goal_curve(
                    user_id,
                    data.financial.snapshot,
                    data.financial.versions,
                    data.financial.positions,
                    data.financial.products,
                    assumption,
                )
                original = selected.model_dump(mode="json")
                hypothetical = {
                    "configuration": config,
                    "configuration_hash": configuration_hash(config),
                    "hypothetical_only": True,
                }
            base = compute_boundary(
                data.financial.snapshot.model_copy(update={"horizon_days": 365}),
                data.financial.versions,
                data.financial.positions,
                data.financial.products,
            )
            if base.model_dump(
                mode="json"
            ) != data.annual.projection.original_annual_projection.model_dump(mode="json"):
                reasons.append("ORIGINAL_ANNUAL_SOURCE_BOUNDARY_DIFFERS")
            else:
                curve = retain_full_floors(
                    base,
                    data.annual.projection.full_annual_projection,
                    candidate,
                    source_account_risk=any(
                        row.state == "SOURCE_LIQUIDITY_RISK"
                        for row in data.annual.projection.source_account_checks
                    ),
                )
            if curve is None:
                reasons.append("ORIGINAL_OR_HYPOTHETICAL_FULL_CASH_UNKNOWN")
        elif isinstance(assumption, FullPolicyHypothesis):
            full_preview = preview_full_policy_financial_impact(
                session,
                user_id,
                assumption.policy_id,
                FullPreviewRequest(
                    expected_version_id=assumption.expected_version_id,
                    configuration=assumption.configuration,
                ),
                now,
            )
            original = full_preview.before_configuration
            hypothetical = full_preview.after_configuration
            reasons.extend(full_preview.financial_impact.reasons)
        else:
            if assumption.product_id not in {row.product_id for row in review.products}:
                raise ValueError("Original registered product is not available in this review")
            candidate_input = _product_input(
                session, data, user_id, assumption.asset_policy_id, now
            )
            if candidate_input is None:
                reasons.append("ORIGINAL_ASSET_PLANNING_SOURCE_UNKNOWN")
            else:
                product = product_sensitivity(candidate_input, assumption)
                original = product.original_product.model_dump(mode="json")
                hypothetical = product.hypothetical_product.model_dump(mode="json")
                reasons.extend(product.after.reasons)
    except (TypeError, ValueError, KeyError, OverflowError) as error:
        raise PolicyLifecycleError(
            "INVALID_SCENARIO_RISK_HYPOTHESIS", "原实体、版本或假设参数不匹配", 409
        ) from error
    projected = (
        curve is not None
        or full_preview is not None
        and full_preview.financial_impact.status == "PROJECTED"
        or product is not None
        and product.after.status in {"OPTIMAL", "NO_PURCHASE", "LIQUIDITY_RISK"}
    )
    delta = (
        curve.safe_idle_cents - review.baseline_full.safe_idle_cents
        if curve is not None and review.baseline_full is not None
        else full_preview.financial_impact.delta_safe_idle_cents
        if full_preview is not None
        else None
    )
    request_hash = configuration_hash(body.model_dump(mode="json"))
    result = ScenarioRiskComparison(
        user_id=user_id,
        epoch_id=review.epoch_id,
        as_of=now,
        source_hash=review.source_hash,
        engine_hash=review.engine_hash,
        original_request=body,
        request_hash=request_hash,
        comparison_hash=configuration_hash(
            {
                "protocol": "scenario-risk-comparison-v1",
                "request_hash": request_hash,
                "source_hash": review.source_hash,
                "engine_hash": review.engine_hash,
                "as_of": now.isoformat(),
                "curve": curve.model_dump(mode="json") if curve else None,
                "full": full_preview.model_dump(mode="json") if full_preview else None,
                "product": product.model_dump(mode="json") if product else None,
            }
        ),
        status="PROJECTED" if projected else "UNKNOWN",
        family=assumption.kind,
        original_selected=original,
        hypothetical_selected=hypothetical,
        baseline_full=review.baseline_full,
        hypothetical_full=curve,
        delta_safe_idle_cents=delta,
        full_policy_preview=full_preview,
        product_preview=product,
        reasons=sorted(set(reasons)),
    )
    _stable(review.engine_hash)
    return result
