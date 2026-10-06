"""Planning-only portfolios using actual RR/RO financial and immutable catalogues."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.db.models import Goal, Policy, PolicyVersion
from app.domain.asset_exposure import AssetExposure
from app.domain.full_asset_allocation import (
    FullAssetPlanningInput,
    FullAssetPlanningResult,
    FullAssetPlanOptions,
    _catalog_class,
    plan_full_assets,
)
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.domain.policy_configuration import configuration_hash
from app.services.asset_allocation import _goal_projection_matches
from app.services.boundary import BoundarySourceIssue
from app.services.dashboard_helpers import current_epoch_audit
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError, _now, is_version_authorized
from app.services.product_catalog import VerifiedCatalogProducts, verified_catalog_products
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session


class FullAssetAllocationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["verified-full-asset-planning-v1"] = "verified-full-asset-planning-v1"
    simulation: Literal[True] = True
    planning_only: Literal[True] = True
    bank_authority: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    protection_scope: Literal["ORIGINAL_VERIFIED_365_DAY_CURVE"] = "ORIGINAL_VERIFIED_365_DAY_CURVE"
    user_id: UUID
    policy_id: UUID
    as_of: datetime
    state: Literal["COMPUTED", "UNKNOWN"]
    planning_constraints: FullAssetPlanOptions
    allocation: FullAssetPlanningResult | None
    catalogue: VerifiedCatalogProducts
    unavailable_asset_classes: list[str]
    source_evidence_ids: list[UUID]
    source_issues: list[BoundarySourceIssue]
    input_hash: str
    limitations: list[str]


def read_full_asset_allocation(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    now: datetime,
    planning_constraints: FullAssetPlanOptions | None = None,
) -> FullAssetAllocationResponse:
    _read_snapshot(session)
    now = _now(now)
    options = planning_constraints or FullAssetPlanOptions()
    with session.no_autoflush:
        policy = read_full_policy(session, user_id, policy_id, now)
        if policy.template_name != "AssetAuthorizationPolicy":
            raise PolicyLifecycleError("INVALID_FULL_ASSET_POLICY", "需要实际完整资产规划声明", 422)
        config = AssetAuthorizationPolicy.model_validate(policy.current_version.configuration)
        context, _, exposures = load_verified_financial_context(session, user_id, now)
        context.sources.used.update(policy.current_version.evidence_ids)
        catalogue = verified_catalog_products(session, now)
        for issue in catalogue.issues:
            context.sources.issue("IMMUTABLE_CATALOGUE_NOT_PROVEN", issue, "产品目录原件未匹配")
        products = catalogue.products if catalogue.status == "VERIFIED" else []
        if len(products) > 100:
            context.sources.issue("FULL_ASSET_CAPACITY", "catalogue", "组合最多读取100项原产品")
        missing_classes: list[str] = sorted(
            set(config.allowed_asset_classes) - {_catalog_class(product) for product in products}
        )
        exposure = None
        if exposures is not None:
            exposure = next((row for row in exposures if row.goal_id == config.goal_id), None)
            if exposure is None:
                # The complete actual exposure statement was verified above. An absent scope
                # has no automatic holdings or pending orders; this is not a missing statement.
                exposure = AssetExposure(
                    as_of=context.snapshot.as_of,
                    scope=config.scope,
                    goal_id=config.goal_id,
                    managed_principal_cents=0,
                    pending_purchase_cents=0,
                    reserved_cash_by_account={
                        key: amount
                        for row in exposures
                        for key, amount in row.reserved_cash_by_account.items()
                    },
                    reserved_goal_cash_by_goal={
                        key: amount
                        for row in exposures
                        for key, amount in row.reserved_goal_cash_by_goal.items()
                    },
                    excluded_manual_position_ids=sorted(
                        {key for row in exposures for key in row.excluded_manual_position_ids}
                    ),
                    evidence_ids=sorted({key for row in exposures for key in row.evidence_ids}),
                )
        goal_deadline, goal_version_id, goal_verified = None, None, False
        if config.goal_id is not None:
            goal = session.scalar(
                select(Goal).where(Goal.id == config.goal_id, Goal.user_id == user_id)
            )
            base_policy = session.get(Policy, goal.policy_id) if goal is not None else None
            version = (
                session.scalar(
                    select(PolicyVersion)
                    .where(
                        PolicyVersion.user_id == user_id,
                        PolicyVersion.policy_id == goal.policy_id,
                    )
                    .order_by(PolicyVersion.version_number.desc())
                    .limit(1)
                )
                if goal is not None
                else None
            )
            goal_verified = bool(
                goal is not None
                and base_policy is not None
                and base_policy.user_id == user_id
                and version is not None
                and goal.policy_version_id == version.id
                and _goal_projection_matches(goal, version)
                and is_version_authorized(session, user_id, version.id, now)
            )
            if goal_verified and goal is not None and version is not None:
                goal_deadline, goal_version_id = goal.deadline, version.id
                context.sources.used.update(UUID(key) for key in version.evidence_ids)
            else:
                context.sources.issue(
                    "CURRENT_GOAL_REFERENCE_NOT_PROVEN",
                    config.goal_id,
                    "目标原版本、确认或投影未匹配",
                )
        audit = current_epoch_audit(session, user_id, [])
        if not audit.complete or audit.status != "VALID":
            context.sources.issue("AUDIT_NOT_PROVEN", str(audit.epoch_id), "当前完整审计未证明")
        context, issues, digest = finalize_financial_context(context, audit)
        allocation = None
        if not issues and exposure is not None and products:
            try:
                allocation = plan_full_assets(
                    FullAssetPlanningInput(
                        snapshot=context.snapshot.model_copy(update={"horizon_days": 365}),
                        boundary_versions=context.versions,
                        positions=context.positions,
                        boundary_products=context.products,
                        products=products,
                        exposure=exposure,
                        policy_id=policy_id,
                        policy_version_id=policy.current_version.version_id,
                        configuration=config,
                        planning_confirmation_valid=policy.planning_confirmation_valid,
                        confirmed_at=policy.current_version.confirmed_at,
                        valid_from=policy.current_version.valid_from,
                        valid_until=policy.current_version.valid_until,
                        goal_deadline=goal_deadline,
                        goal_policy_version_id=goal_version_id,
                        goal_reference_verified=goal_verified,
                        options=options,
                    )
                )
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                context.sources.issue("INVALID_FULL_ASSET_INPUT", policy_id, str(error))
                context, issues, digest = finalize_financial_context(context, audit)
        return FullAssetAllocationResponse(
            user_id=user_id,
            policy_id=policy_id,
            as_of=now,
            state="COMPUTED" if allocation is not None else "UNKNOWN",
            planning_constraints=options,
            allocation=allocation,
            catalogue=catalogue,
            unavailable_asset_classes=missing_classes,
            source_evidence_ids=sorted(context.sources.used),
            source_issues=issues,
            input_hash=configuration_hash(
                {
                    "financial_sources": digest,
                    "full_policy_version": str(policy.current_version.version_id),
                    "full_policy_hash": policy.current_version.content_hash,
                    "catalogue": catalogue.model_dump(mode="json"),
                    "allocation_input_hash": allocation.input_hash if allocation else None,
                    "planning_constraints": options.model_dump(mode="json"),
                }
            ),
            limitations=[
                "完整声明仅为规划确认，原V1执行器没有此银行权限消费者。",
                "保留原已验证365日保护；尚未将未实现的FULL财务预测扩展当成原事实。",
                "有限域每个当前产品版本至多一个今日批次；旧持仓不卖出或计为恢复现金。",
                "未来收入为0；到期后再次配置必须按当时原策略重新判断。",
                "目录只使用原实际已登记产品，不宣称七类产品已实测齐备。",
            ],
        )
