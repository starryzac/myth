"""One current owner/OPEN/RR read of original FULL declaration dependencies."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from app.db.full_models import FullPolicy
from app.db.models import Policy, PolicyVersion, User
from app.domain.full_policy_configuration import TemplateName
from app.domain.full_policy_dependencies import (
    MAX_POLICIES,
    DependencyPolicyOriginal,
    DependencyReviewInput,
    FullPolicyDependencyReview,
    review_dependencies,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import audit_read_scope, current_audit_epoch, verify_audit_chain
from app.services.full_policy_lifecycle import (
    PolicyState,
    _binding,
    _read_snapshot,
    _reference_bindings,
    _references,
    derived_state,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now, effective_status
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _reference_statuses(
    session: Session, refs: list[dict[str, Any]], user_id: UUID, now: datetime
) -> dict[str, str]:
    """Display derived lifecycle state without editing the original reference or authority."""
    result: dict[str, str] = {}
    for ref in refs:
        if ref["kind"] != "MVP_POLICY":
            continue
        if type(ref.get("version_ids")) is not list or not ref["version_ids"]:
            raise ValueError("MVP dependency has no current version original")
        identity = UUID(ref["id"])
        version_id = UUID(ref["version_ids"][-1])
        policy = session.get(Policy, identity)
        version = session.get(PolicyVersion, version_id)
        if (
            policy is None
            or version is None
            or policy.user_id != user_id
            or version.user_id != user_id
            or version.policy_id != identity
            or version.created_at > now
            or ref["snapshot"]["status"] != policy.status
            or configuration_hash(version.configuration) != version.content_hash
            or configuration_hash(
                {"id": str(identity), "version_id": str(version_id), "hash": version.content_hash}
            )
            != ref["binding_hash"]
        ):
            raise ValueError("MVP dependency lifecycle originals differ")
        result[str(identity)] = effective_status(policy, version, now)
    return result


def read_full_policy_dependencies(
    session: Session, user_id: UUID, policy_id: UUID, now: datetime
) -> FullPolicyDependencyReview:
    _read_snapshot(session)
    now = _now(now)
    with session.no_autoflush, audit_read_scope(session):
        user = session.get(User, user_id)
        selected = session.scalar(
            select(FullPolicy).where(FullPolicy.user_id == user_id, FullPolicy.id == policy_id)
        )
        if user is None or not user.is_simulated or selected is None:
            raise PolicyLifecycleError("NOT_FOUND", "当前用户的完整策略不存在", 404)
        epoch = current_audit_epoch(session, user_id)
        if epoch is None or epoch.status != "OPEN":
            raise PolicyLifecycleError("DEPENDENCY_OPEN_EPOCH_MISSING", "当前OPEN周期不存在", 409)
        if selected.epoch_id != epoch.id:
            raise PolicyLifecycleError(
                "ARCHIVED_DEPENDENCY_SOURCE", "历史策略只沿原历史入口读取，不能作为当前依赖", 409
            )
        current_query = select(FullPolicy.id).where(
            FullPolicy.user_id == user_id, FullPolicy.epoch_id == epoch.id
        )
        count = session.scalar(select(func.count()).select_from(current_query.subquery()))
        archived = session.scalar(
            select(func.count())
            .select_from(FullPolicy)
            .where(FullPolicy.user_id == user_id, FullPolicy.epoch_id != epoch.id)
        )
        if type(count) is not int or type(archived) is not int:
            raise PolicyLifecycleError("DEPENDENCY_DENOMINATOR_MISSING", "原策略分母不可读取", 409)
        issues = []
        ids = []
        if count <= MAX_POLICIES:
            ids = list(session.scalars(current_query.order_by(FullPolicy.id)))
        else:
            # Preserve the actual denominator; do not return a truncated graph as complete.
            issues.append("DEPENDENCY_POLICY_CAPACITY_EXCEEDED")
        audit = verify_audit_chain(session, user_id, epoch.id)
        originals = []
        if audit.status == "VALID":
            for identity in ids:
                try:
                    policy, versions, _, live = _binding(session, user_id, identity, now)
                    if not live:
                        raise PolicyLifecycleError("DEPENDENCY_NOT_CURRENT", "原策略不属于当前周期")
                    version = versions[-1]
                    recorded = version.impact_analysis["reference_snapshots"]
                    if type(recorded) is not list:
                        raise ValueError("Recorded dependency originals are absent")
                    effective = derived_state(
                        cast(PolicyState, policy.status),
                        version.confirmed_at,
                        version.valid_from,
                        version.valid_until,
                        now,
                    )
                    source_issues = []
                    refs = None
                    reference_statuses: dict[str, str] = {}
                    try:
                        refs, _ = _references(session, user_id, version.configuration, now)
                        reference_statuses = _reference_statuses(session, refs, user_id, now)
                    except (PolicyLifecycleError, KeyError, TypeError, ValueError) as error:
                        refs = None
                        source_issues.append(
                            "CURRENT_REFERENCE_SOURCE_UNAVAILABLE:"
                            + (
                                error.code
                                if isinstance(error, PolicyLifecycleError)
                                else type(error).__name__
                            )
                        )
                    reference_status = (
                        "CURRENT"
                        if refs is not None
                        and _reference_bindings(recorded) == _reference_bindings(refs)
                        else "CHANGED_OR_UNAVAILABLE"
                    )
                    originals.append(
                        DependencyPolicyOriginal(
                            policy_id=policy.id,
                            epoch_id=epoch.id,
                            version_id=version.id,
                            configuration_hash=version.content_hash,
                            configuration=version.configuration,
                            name=policy.name,
                            template_name=cast(TemplateName, policy.template_name),
                            effective_status=effective,
                            reference_validation=reference_status,
                            planning_confirmation_valid=reference_status == "CURRENT"
                            and effective in {"ACTIVE", "CONFIRMED"},
                            recorded_references=recorded,
                            current_references=refs,
                            reference_effective_statuses=reference_statuses,
                            source_issues=source_issues,
                        )
                    )
                except (PolicyLifecycleError, KeyError, TypeError, ValueError) as error:
                    issues.append(
                        "CURRENT_POLICY_ORIGINAL_UNAVAILABLE:"
                        + str(identity)
                        + ":"
                        + (
                            error.code
                            if isinstance(error, PolicyLifecycleError)
                            else type(error).__name__
                        )
                    )
        try:
            return review_dependencies(
                DependencyReviewInput(
                    user_id=user_id,
                    epoch_id=epoch.id,
                    selected_policy_id=policy_id,
                    as_of=now,
                    current_policy_count=count,
                    current_policy_ids=ids,
                    archived_policy_count=archived,
                    policies=originals,
                    audit_status="VALID" if audit.status == "VALID" else "INVALID",
                    audit_source_hash=configuration_hash(audit.model_dump(mode="json")),
                    source_issues=issues,
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PolicyLifecycleError(
                "DEPENDENCY_ORIGINAL_INVALID", "当前依赖原件不完整或身份/摘要不一致", 409
            ) from error
