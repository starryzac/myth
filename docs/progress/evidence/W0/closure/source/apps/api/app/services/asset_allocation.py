"""Read-only asset previews over current confirmed policies and reconciled exposure."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.db.models import AssetProduct, Goal, Policy, PolicyVersion
from app.domain.asset_allocation import (
    ALGORITHM_VERSION,
    AssetAllocationResult,
    AssetProductTerms,
    select_asset,
)
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import BoundaryPolicyVersion, SourceIssue
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.asset_exposure_import import load_asset_exposure
from app.services.boundary import BoundarySourceIssue, load_boundary_context
from app.services.policy_lifecycle import PolicyLifecycleError, is_version_authorized
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session


class AssetAllocationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    user_id: UUID
    policy_id: UUID
    as_of: datetime
    allocation: AssetAllocationResult
    source_evidence_ids: list[UUID]
    input_digest: str
    source_issues: list[BoundarySourceIssue]


def _goal_projection_matches(goal: Goal, version: PolicyVersion) -> bool:
    config = validate_configuration(version.configuration)
    if config["type"] != "goal_saving":
        return False
    monthly, priority = config["monthly_contribution"], config["priority"]
    return bool(
        goal.target_cents == config["target_cents"]
        and goal.deadline.isoformat() == config["deadline"]
        and goal.monthly_min_cents == monthly["min_cents"]
        and goal.monthly_target_cents == monthly["target_cents"]
        and goal.monthly_max_cents == monthly["max_cents"]
        and goal.importance == priority["importance"]
        and goal.minimum_protection_cents == priority["minimum_cents"]
        and goal.reducible == priority["reducible"]
        and goal.deferrable == priority["deferrable"]
        and goal.cross_goal_reallocation_allowed == config["cross_goal_reallocation_allowed"]
    )


def preview_asset_allocation(
    session: Session, user_id: UUID, policy_id: UUID, now: datetime
) -> AssetAllocationResponse:
    with session.no_autoflush:
        policy = session.scalar(
            select(Policy).where(
                Policy.id == policy_id,
                Policy.user_id == user_id,
                Policy.policy_type == "asset_authorization",
            )
        )
        if policy is None:
            raise PolicyLifecycleError("NOT_FOUND", "资产授权不存在", 404)
        context = load_boundary_context(session, user_id, now)
        version = session.scalar(
            select(PolicyVersion)
            .where(PolicyVersion.policy_id == policy_id, PolicyVersion.user_id == user_id)
            .order_by(PolicyVersion.version_number.desc())
        )
        if version is None:
            raise PolicyLifecycleError("INVALID_ASSET_AUTHORIZATION", "资产授权缺少版本")
        planning_inputs = {}
        try:
            config = validate_configuration(version.configuration)
            if config["type"] != "asset_authorization":
                raise ValueError("Wrong policy type")
            goal_id = UUID(config["goal_id"]) if config.get("goal_id") else None
            authorized = is_version_authorized(session, user_id, version.id, context.snapshot.as_of)
            reasons = [] if authorized else ["ASSET_POLICY_NOT_EFFECTIVE_NOW"]
            if goal_id is not None:
                goal = session.scalar(
                    select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
                )
                goal_version = (
                    session.scalar(
                        select(PolicyVersion)
                        .where(
                            PolicyVersion.policy_id == goal.policy_id,
                            PolicyVersion.user_id == user_id,
                        )
                        .order_by(PolicyVersion.version_number.desc())
                    )
                    if goal
                    else None
                )
                if (
                    goal is None
                    or goal_version is None
                    or goal.policy_version_id != goal_version.id
                    or goal.asset_policy_id != policy_id
                    or goal_version.configuration.get("asset_policy_id") != str(policy_id)
                    or not _goal_projection_matches(goal, goal_version)
                    or not is_version_authorized(
                        session, user_id, goal_version.id, context.snapshot.as_of
                    )
                ):
                    authorized = False
                    reasons.append("CURRENT_GOAL_ASSET_POLICY_LINK_REQUIRED")
                else:
                    context.sources.used.update(
                        UUID(identifier) for identifier in goal_version.evidence_ids
                    )
            context.sources.used.update(UUID(identifier) for identifier in version.evidence_ids)
            baseline = compute_boundary(
                context.snapshot, context.versions, context.positions, context.products
            )
            if not authorized:
                allocation = AssetAllocationResult(
                    algorithm_version=ALGORITHM_VERSION,
                    policy_id=policy_id,
                    policy_version_id=version.id,
                    scope=config["scope"],
                    goal_id=goal_id,
                    status="INACTIVE_POLICY",
                    baseline_boundary=baseline,
                    selection_hash=configuration_hash(
                        {
                            "baseline": baseline.boundary_hash,
                            "version": str(version.id),
                            "reasons": reasons,
                        }
                    ),
                    reasons=reasons,
                )
            else:
                if version.confirmed_at is None or version.valid_from is None:
                    raise ValueError("Confirmed authorization has no effective time")
                exposure = load_asset_exposure(session, context, config)
                products = []
                for product in session.scalars(
                    select(AssetProduct).order_by(
                        AssetProduct.product_code, AssetProduct.version_number, AssetProduct.id
                    )
                ):
                    products.append(
                        AssetProductTerms(
                            **{
                                field: getattr(product, field)
                                for field in AssetProductTerms.model_fields
                                if field not in {"product_id", "terms_digest"}
                            },
                            product_id=product.id,
                            terms_digest=configuration_hash(product.maturity_rule),
                        )
                    )
                authorization = BoundaryPolicyVersion(
                    policy_id=policy_id,
                    version_id=version.id,
                    configuration=config,
                    content_hash=version.content_hash,
                    confirmed_at=version.confirmed_at,
                    valid_from=version.valid_from,
                    valid_until=version.valid_until,
                    evidence_ids=[UUID(identifier) for identifier in version.evidence_ids],
                )
                planning_inputs = {
                    "authorization": authorization.model_dump(mode="json"),
                    "products": [product.model_dump(mode="json") for product in products],
                    "exposure": exposure.model_dump(mode="json"),
                }
                allocation = select_asset(
                    context.snapshot,
                    context.versions,
                    context.positions,
                    context.products,
                    authorization,
                    products,
                    exposure,
                    source_issues=[
                        SourceIssue(code=item.code, entity_type="source", entity_id=item.source_ref)
                        for item in context.sources.issues
                    ],
                )
        except (TypeError, ValueError, OverflowError) as error:
            raise PolicyLifecycleError(
                "INVALID_ASSET_INPUT", "资产配置输入不一致或超出范围"
            ) from error
        issues = sorted(context.sources.issues, key=lambda item: (item.code, item.source_ref))
        digest = configuration_hash(
            {
                "boundary_sources": context.snapshot.source_digest,
                "evidence": [
                    {
                        "id": str(identifier),
                        "hash": context.sources.evidence[identifier].content_hash,
                    }
                    for identifier in sorted(context.sources.used)
                    if identifier in context.sources.evidence
                ],
                "issues": [item.model_dump(mode="json") for item in issues],
            }
        )
        from app.domain.decision_trace_types import TraceCandidate
        from app.services.decision_recording import (
            capture_boundary,
            capture_versions,
            current_capture,
        )

        capture = current_capture(session)
        if capture is not None:
            capture_boundary(session, "asset_boundary", context)
            capture_versions(session, user_id, [version.id])
            capture.algorithms["asset_allocation"] = allocation.algorithm_version
            capture.inputs["asset_planning"] = {
                **planning_inputs,
                "configuration": config,
                "authorized": authorized,
                "result": allocation.model_dump(mode="json"),
            }
            for index, candidate in enumerate(allocation.candidates):
                data = candidate.model_dump(mode="json")
                capture.candidates.append(
                    TraceCandidate(
                        candidate_key=f"asset:{index}",
                        kind="ASSET_ALLOCATION",
                        status=data.get("status", "EVALUATED"),
                        inputs={"product_id": data.get("product_id")},
                        result=data,
                        reasons=data.get("reasons", []),
                    )
                )
        return AssetAllocationResponse(
            user_id=user_id,
            policy_id=policy_id,
            as_of=context.snapshot.as_of,
            allocation=allocation,
            source_evidence_ids=sorted(context.sources.used),
            input_digest=digest,
            source_issues=issues,
        )
