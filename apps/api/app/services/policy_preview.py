"""Read real funds once, then compare explicitly hypothetical policy parameters."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.db.models import Goal, Policy, PolicyVersion, User
from app.domain.boundary import compute_boundary_with_details, compute_policy_change_boundary
from app.domain.boundary_types import BoundaryPolicyVersion
from app.domain.policy_change_types import (
    LivingReserveChangeEstimate,
    PolicyChangeAssumption,
    calculate_assumption_digest,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.boundary import (
    OWNERSHIP_SOURCE,
    BoundaryContext,
    _known_import,
    clone_boundary_context,
    read_goal_month_fact,
)
from app.services.dashboard import _capacity
from app.services.dashboard_helpers import current_epoch_audit
from app.services.financial_read import (
    finalize_financial_context,
    financial_card,
    load_verified_financial_context,
)
from app.services.living_reserve import estimate_living_reserve
from app.services.policy_lifecycle import (
    PolicyLifecycleError,
    _evidence,
    _goal_asset_reference,
    _now,
    _window,
    effective_status,
)
from app.services.policy_preview_types import PolicyChangePreviewResponse
from sqlalchemy import select
from sqlalchemy.orm import Session


def _source(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    expected: UUID,
    now: datetime,
) -> tuple[User, Policy, BoundaryPolicyVersion, str]:
    user = session.get(User, user_id)
    policy = session.scalar(select(Policy).where(Policy.id == policy_id, Policy.user_id == user_id))
    if user is None or not user.is_simulated or policy is None:
        raise PolicyLifecycleError("NOT_FOUND", "模拟策略不存在", 404)
    current = session.scalar(
        select(PolicyVersion)
        .where(
            PolicyVersion.policy_id == policy_id,
            PolicyVersion.user_id == user_id,
        )
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )
    if current is None or current.id != expected:
        raise PolicyLifecycleError("STALE_POLICY_VERSION", "策略版本已变化，请重新查看", 409)
    derived = effective_status(policy, current, now)
    status = derived if derived in {"ACTIVE", "CONFIRMED", "EXPIRED"} else policy.status
    if status not in {"ACTIVE", "CONFIRMED", "SUSPENDED"}:
        raise PolicyLifecycleError("INVALID_POLICY_STATE", "当前策略不能修改或重新激活", 409)
    proofs = _evidence(session, user_id, current.evidence_ids, now, lock=False)
    if current.confirmed_at is None or not any(
        proof.evidence_level == "USER_CONFIRMED_POLICY"
        and proof.source_type == "POLICY_CONFIRMATION"
        and proof.source_ref == str(current.id)
        and proof.content == current.confirmation
        and proof.content.get("accepted") is True
        for proof in proofs
    ):
        raise PolicyLifecycleError("INVALID_POLICY_SOURCE", "当前策略缺少原明确确认来源", 409)
    actual_configuration = validate_configuration(current.configuration)
    if (
        actual_configuration["type"] != policy.policy_type
        or configuration_hash(actual_configuration) != current.content_hash
    ):
        raise PolicyLifecycleError("INVALID_POLICY_SOURCE", "原策略配置与摘要不一致", 409)
    return (
        user,
        policy,
        BoundaryPolicyVersion(
            policy_id=policy.id,
            version_id=current.id,
            configuration=actual_configuration,
            content_hash=current.content_hash,
            confirmed_at=current.confirmed_at,
            valid_from=current.valid_from or current.confirmed_at,
            valid_until=current.valid_until,
            evidence_ids=sorted(proof.id for proof in proofs),
        ),
        status,
    )


def _actual_goal_month(
    session: Session,
    context: BoundaryContext,
    policy_id: UUID,
) -> BoundaryContext:
    goal = session.scalar(
        select(Goal).where(
            Goal.user_id == context.sources.user_id,
            Goal.policy_id == policy_id,
        )
    )
    if goal is None or not any(item.goal_id == goal.id for item in context.snapshot.goals):
        return context
    zone = UTC if context.snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    period = context.sources.now.astimezone(zone).strftime("%Y-%m")
    if any(
        item.goal_id == goal.id and item.period == period
        for item in context.snapshot.goal_month_contributions
    ):
        return context
    proofs = context.sources.candidates(OWNERSHIP_SOURCE, "goal_id", goal.id)
    # The same actual ownership was strongly validated by load_boundary_context.
    if len(proofs) != 1:
        return context
    month = read_goal_month_fact(
        goal, context.sources, zone, _known_import(proofs[0], context.sources)
    )
    if month is None:
        return context
    return replace(
        context,
        snapshot=context.snapshot.model_copy(
            update={
                "goal_month_contributions": [*context.snapshot.goal_month_contributions, month],
            }
        ),
    )


def _delta(before: int | None, after: int | None) -> int | None:
    return after - before if before is not None and after is not None else None


def preview_change(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    expected_version_id: UUID,
    configuration: dict[str, Any],
    now: datetime,
) -> PolicyChangePreviewResponse:
    now = _now(now)
    with session.no_autoflush:
        _capacity(session, user_id)
        user, policy, source_version, source_status = _source(
            session,
            user_id,
            policy_id,
            expected_version_id,
            now,
        )
        try:
            canonical = validate_configuration(configuration)
        except ValueError as error:
            raise PolicyLifecycleError(
                "INVALID_CONFIGURATION", "候选策略配置不完整或非法", 422
            ) from error
        if canonical["type"] != policy.policy_type:
            raise PolicyLifecycleError("POLICY_TYPE_CHANGE", "已有策略不得更换类型", 409)
        _goal_asset_reference(session, user_id, canonical)
        start, end = _window(user, canonical, now)
        assumption = PolicyChangeAssumption.model_validate(
            {
                "user_id": user_id,
                "policy_id": policy_id,
                "source_version_id": expected_version_id,
                "configuration": canonical,
                "configuration_hash": configuration_hash(canonical),
                "timezone": user.timezone,
                "assumed_confirmation_at": now,
                "assumed_valid_from": start,
                "assumed_valid_until": end,
                "source_status": source_status,
            }
        )
        actual, _, _ = load_verified_financial_context(session, user_id, now)
        audit = current_epoch_audit(session, user_id, [])
        actual, before_issues, actual_digest = finalize_financial_context(actual, audit)
        before_computed = compute_boundary_with_details(
            actual.snapshot,
            actual.versions,
            actual.positions,
            actual.products,
        )
        before = financial_card(
            before_computed.boundary,
            before_computed.details,
            before_issues,
            actual_digest,
        )
        hypothetical = clone_boundary_context(actual, user_id, now)
        hypothetical.sources.used.update(source_version.evidence_ids)
        living_estimate = None
        if canonical["type"] == "goal_saving" and source_status != "SUSPENDED":
            zone = UTC if actual.snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
            month_start = now.astimezone(zone).replace(
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            if (
                start < next_month
                and (end is None or end > month_start)
                and canonical["deadline"] >= now.astimezone(zone).date().isoformat()
            ):
                hypothetical = _actual_goal_month(session, hypothetical, policy_id)
        elif canonical["type"] == "living_reserve" and source_status != "SUSPENDED":
            estimate = estimate_living_reserve(session, user_id, now, canonical)
            hypothetical.sources.used.update(estimate.source_evidence_ids)
            hypothetical.sources.issues = [
                issue
                for issue in hypothetical.sources.issues
                if not (
                    issue.code == "INSUFFICIENT_LIVING_HISTORY"
                    and issue.source_ref == str(source_version.version_id)
                )
            ]
            ready = estimate.estimation.status == "READY"
            living_estimate = LivingReserveChangeEstimate(
                status="READY" if ready else "INSUFFICIENT_EVIDENCE",
                amount_cents=estimate.estimation.recommended_reserve_cents if ready else None,
                as_of=now,
                configuration_hash=assumption.configuration_hash,
                estimation_input_digest=estimate.input_digest,
                issues=[],
            )
            for issue in estimate.source_issues:
                hypothetical.sources.issue(issue.code, issue.source_ref, issue.message)
        hypothetical, after_issues, source_digest = finalize_financial_context(hypothetical, audit)
        assumption_digest = calculate_assumption_digest(assumption, living_estimate)
        hypothetical_digest = configuration_hash(
            {
                "algorithm": "policy-change-preview-v1",
                "actual_fact_digest": actual_digest,
                "hypothetical_source_digest": source_digest,
                "assumption_digest": assumption_digest,
            }
        )
        hypothetical = replace(
            hypothetical,
            snapshot=hypothetical.snapshot.model_copy(
                update={"source_digest": hypothetical_digest},
            ),
        )
        after_computed = compute_policy_change_boundary(
            hypothetical.snapshot,
            actual.versions,
            actual.positions,
            actual.products,
            source_version=source_version,
            assumption=assumption,
            living_estimate=living_estimate,
        )
        after = financial_card(
            after_computed.boundary,
            after_computed.details,
            after_issues,
            hypothetical_digest,
        )
        return PolicyChangePreviewResponse(
            user_id=user_id,
            as_of=now,
            timezone=actual.snapshot.timezone,
            policy_id=policy_id,
            expected_version_id=expected_version_id,
            configuration=canonical,
            configuration_hash=assumption.configuration_hash,
            assumed_status=assumption.effective_status,
            assumed_valid_from=start,
            assumed_valid_until=end,
            assumption_digest=after_computed.assumption_digest,
            current_fact_input_digest=actual_digest,
            hypothetical_input_digest=hypothetical_digest,
            before=before,
            after=after,
            delta_safe_idle_cents=_delta(before.safe_idle_cents, after.safe_idle_cents),
            delta_minimum_margin_cents=_delta(
                before.minimum_margin_cents, after.minimum_margin_cents
            ),
            notes=[
                "此比较仅假设确认所示配置，不产生策略确认、执行权限或锁价。",
                "提交修改会重新验证当前版本；预览时点的资金事实没有跨请求冻结。",
            ],
        )
