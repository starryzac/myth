"""Durable, explicit emergency goal cash permission and exact original-key recovery.

Generic FULL policy confirmation remains planning consent. This separate command
requires review of every listed goal, protected destination, dates and both caps.
It creates neither an action nor a bank operation; new execution must separately
check the complete financial facts and lifetime use of this policy identity.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.models import DecisionRun, EvidenceItem, Goal
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import build_trace
from app.domain.full_goal_release_authorization import (
    PROTOCOL,
    SOURCE,
    GoalReleaseAuthorization,
    GoalReleaseBinding,
    GoalReleaseScope,
    Hash,
    ReleaseAuthorizationConfirmation,
    ReleaseAuthorizationPreviewRequest,
    release_authorization_identity,
    release_authorization_request_hash,
)
from app.domain.full_policy_configuration import CrossGoalReallocationPolicy, LongTermGoalPolicy
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import audit_read_scope, current_audit_epoch, verify_audit_chain
from app.services.decision_trace import evidence_copy, get_decision_trace, record_trace
from app.services.full_goals import read_full_goal_model
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class ReleaseAuthorizationPreview(BoundaryModel):
    simulation: Literal[True] = True
    scope: GoalReleaseScope
    scope_hash: Hash
    financial_permission_recorded: Literal[False] = False
    current_financial_amount_verified: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"


class ReleaseAuthorizationResponse(BoundaryModel):
    simulation: Literal[True] = True
    original_authorization: GoalReleaseAuthorization
    evidence_id: UUID
    evidence_hash: Hash
    original_trace_hash: Hash
    idempotent_replay: bool
    current_scope_status: Literal["CURRENT", "STALE", "UNKNOWN", "ARCHIVED"]
    current_financial_amount_verified: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    submits_bank_operation: Literal[False] = False


class ReleaseAuthorizationLookup(BoundaryModel):
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    original: ReleaseAuthorizationResponse | None
    replacement_allowed: Literal[False] = False


@contextmanager
def _reader(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        with audit_read_scope(session):
            yield session


def _error(message: str, code: str = "INVALID_GOAL_RELEASE_AUTHORIZATION") -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def _scope(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    body: ReleaseAuthorizationPreviewRequest,
    now: datetime,
) -> tuple[GoalReleaseScope, list[EvidenceItem]]:
    _read_snapshot(session)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN" or epoch.id != body.expected_epoch_id:
        raise _error("授权必须使用当前开放周期", "STALE_AUDIT_EPOCH")
    audit = verify_audit_chain(session, user_id)
    if audit.status != "VALID":
        raise _error("原始完整审计尚未验真", "GOAL_RELEASE_AUDIT_NOT_VERIFIED")
    policy = read_full_policy(session, user_id, policy_id, now)
    if (
        policy.template_name != "CrossGoalReallocationPolicy"
        or policy.epoch_id != epoch.id
        or policy.current_version.version_id != body.expected_policy_version_id
        or policy.effective_status not in {"ACTIVE", "CONFIRMED"}
        or not policy.planning_confirmation_valid
        or policy.reference_validation != "CURRENT"
    ):
        raise _error("回拨规则当前版本、状态或来源已变化", "STALE_RELEASE_POLICY")
    version = policy.current_version
    config = CrossGoalReallocationPolicy.model_validate(version.configuration)
    if (
        not config.enabled
        or config.valid_from is None
        or config.valid_until is None
        or version.valid_until is None
        or configuration_hash(config.model_dump(mode="json")) != version.content_hash
    ):
        raise _error("只有明确启用、有限日期和额度的回拨规则可确认", "RELEASE_POLICY_DISABLED")
    start = max(version.valid_from, version.confirmed_at)
    until = version.valid_until
    bindings: list[GoalReleaseBinding] = []
    sources: set[UUID] = set(version.evidence_ids)
    for goal_id in sorted(config.source_goal_ids, key=str):
        goal = session.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id))
        if goal is None:
            raise _error("回拨规则的源目标不属于当前用户")
        model = read_full_goal_model(session, user_id, goal_id, now)
        if (
            model.status != "VERIFIED"
            or model.full_configuration is None
            or model.evidence_id is None
            or model.evidence_hash is None
            or model.full_configuration_hash is None
            or model.confirmed_at is None
            or model.epoch_id != epoch.id
            or model.base_policy_version_id != goal.policy_version_id
        ):
            raise _error("每个源目标均需确切的完整目标确认模型", "RELEASE_GOAL_MODEL_MISSING")
        full = LongTermGoalPolicy.model_validate(model.full_configuration)
        if (
            configuration_hash(full.model_dump(mode="json")) != model.full_configuration_hash
            or full.minimum_guarantee_cents != goal.minimum_protection_cents
        ):
            raise _error("源目标最低保障及完整模型摘要不一致")
        start = max(start, model.confirmed_at)
        sources.add(model.evidence_id)
        bindings.append(
            GoalReleaseBinding(
                goal_id=goal.id,
                original_policy_id=model.policy_id,
                original_policy_version_id=model.base_policy_version_id,
                full_model_evidence_id=model.evidence_id,
                full_model_evidence_hash=model.evidence_hash,
                full_configuration_hash=model.full_configuration_hash,
                minimum_guarantee_cents=full.minimum_guarantee_cents,
            )
        )
    if not start <= now < until:
        raise _error("回拨规则与源目标尚未同时生效或已到期", "RELEASE_SCOPE_OUTSIDE_VALIDITY")
    scope = GoalReleaseScope(
        user_id=user_id,
        epoch_id=epoch.id,
        policy_id=policy_id,
        policy_version_id=version.version_id,
        policy_configuration_hash=version.content_hash,
        source_goals=bindings,
        emergency_conditions=sorted(config.emergency_conditions),
        single_action_cap_cents=config.single_action_cap_cents,
        total_cap_cents=config.total_cap_cents,
        valid_from=start,
        valid_until=until,
    )
    originals = list(session.scalars(select(EvidenceItem).where(EvidenceItem.id.in_(sources))))
    if len(originals) != len(sources) or any(
        row.user_id != user_id
        or row.status != "VALID"
        or row.observed_at > now
        or row.content_hash != configuration_hash(row.content)
        for row in originals
    ):
        raise _error("授权来源声明不完整或摘要已变化")
    return scope, originals


def preview_release_authorization(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    body: ReleaseAuthorizationPreviewRequest,
    now: datetime,
) -> ReleaseAuthorizationPreview:
    now = _now(now)
    with audit_read_scope(session):
        scope, _ = _scope(session, user_id, policy_id, body, now)
    return ReleaseAuthorizationPreview(
        scope=scope, scope_hash=configuration_hash(scope.model_dump(mode="json"))
    )


def _original(
    session: Session, user_id: UUID, epoch_id: UUID, key: str, now: datetime, *, replay: bool
) -> ReleaseAuthorizationResponse | None:
    identity = release_authorization_identity(user_id, epoch_id, key)
    row = session.scalar(select(DecisionRun).where(DecisionRun.id == identity))
    if row is None:
        return None
    if row.user_id != user_id:
        raise _error("授权原键所有者不一致")
    result = get_decision_trace(session, user_id, identity, now)
    trace = result.trace
    if (
        result.completeness != "COMPLETE"
        or result.audit_chain_status != "VALID"
        or trace is None
        or trace.algorithm_versions.get("goal_release_authorization") != PROTOCOL
    ):
        raise _error("授权原轨迹与原审计未验真")
    try:
        original = GoalReleaseAuthorization.model_validate_json(
            json.dumps(trace.outcome["goal_release_authorization"])
        )
        evidence_id = uuid5(identity, "authorization-evidence")
        proof = session.get(EvidenceItem, evidence_id)
        if (
            proof is None
            or proof.user_id != user_id
            or proof.evidence_level != "USER_CONFIRMED_POLICY"
            or proof.source_type != SOURCE
            or proof.source_ref != str(identity)
            or proof.status != "VALID"
            or proof.content != original.model_dump(mode="json")
            or proof.content_hash != configuration_hash(proof.content)
            or proof.created_at != original.confirmed_at
            or proof.observed_at != original.confirmed_at
            or proof.valid_from != original.confirmed_at
            or proof.valid_to != original.valid_until
            or original.authorization_id != identity
            or original.user_id != user_id
            or original.epoch_id != epoch_id
            or original.idempotency_key != key
            or trace.inputs["original_request"] != original.original_request.model_dump(mode="json")
            or len([source for source in trace.sources if source.id == evidence_id]) != 1
        ):
            raise ValueError("Original immutable authorization evidence differs")
    except (ValueError, KeyError, TypeError) as error:
        raise _error("专用授权原确认、声明或摘要不一致") from error
    epoch = current_audit_epoch(session, user_id)
    status: Literal["CURRENT", "STALE", "UNKNOWN", "ARCHIVED"]
    if epoch is None or epoch.id != epoch_id or epoch.status != "OPEN":
        status = "ARCHIVED"
    elif now >= original.valid_until:
        status = "STALE"
    else:
        try:
            scope, _ = _scope(session, user_id, original.policy_id, original.original_request, now)
            status = (
                "CURRENT"
                if configuration_hash(scope.model_dump(mode="json")) == original.scope_hash
                else "STALE"
            )
        except PolicyLifecycleError as error:
            status = (
                "STALE"
                if error.code
                in {
                    "STALE_AUDIT_EPOCH",
                    "STALE_RELEASE_POLICY",
                    "RELEASE_POLICY_DISABLED",
                    "RELEASE_SCOPE_OUTSIDE_VALIDITY",
                    "POLICY_NOT_AUTHORIZED",
                }
                else "UNKNOWN"
            )
    return ReleaseAuthorizationResponse(
        original_authorization=original,
        evidence_id=evidence_id,
        evidence_hash=proof.content_hash,
        original_trace_hash=trace.trace_hash,
        idempotent_replay=replay,
        current_scope_status=status,
    )


def read_release_authorization(
    session: Session, user_id: UUID, epoch_id: UUID, key: str, now: datetime
) -> ReleaseAuthorizationLookup:
    _read_snapshot(session)
    now = _now(now)
    with audit_read_scope(session):
        original = _original(session, user_id, epoch_id, key, now, replay=True)
    return ReleaseAuthorizationLookup(
        user_id=user_id,
        epoch_id=epoch_id,
        idempotency_key=key,
        status="RECORDED" if original else "NOT_FOUND_NOT_FINAL",
        original=original,
    )


def confirm_release_authorization(
    engine: Engine,
    user_id: UUID,
    policy_id: UUID,
    body: ReleaseAuthorizationConfirmation,
    now: datetime,
) -> ReleaseAuthorizationResponse:
    now = _now(now)
    identity = release_authorization_identity(user_id, body.expected_epoch_id, body.idempotency_key)
    digest = release_authorization_request_hash(user_id, policy_id, body)
    with audit_command_guard(engine, user_id), Session(engine) as writer, writer.begin():
        _user(writer, user_id)
        with _reader(engine) as reader:
            found = _original(
                reader, user_id, body.expected_epoch_id, body.idempotency_key, now, replay=True
            )
            if found is not None:
                if found.original_authorization.request_hash != digest:
                    raise _error("授权原键不能替换目标、策略或审核范围", "IDEMPOTENCY_CONFLICT")
                return found
            scope, sources = _scope(reader, user_id, policy_id, body, now)
        scope_hash = configuration_hash(scope.model_dump(mode="json"))
        if scope_hash != body.reviewed_scope_hash:
            raise _error("需要明确接受刚刚核对的完整回拨范围", "RELEASE_SCOPE_HASH_MISMATCH")
        original = GoalReleaseAuthorization(
            authorization_id=identity,
            user_id=user_id,
            epoch_id=body.expected_epoch_id,
            policy_id=policy_id,
            policy_version_id=body.expected_policy_version_id,
            scope=scope,
            scope_hash=scope_hash,
            accepted=True,
            idempotency_key=body.idempotency_key,
            original_request=body,
            request_hash=digest,
            confirmed_at=now,
            valid_until=scope.valid_until,
        )
        proof = EvidenceItem(
            id=uuid5(identity, "authorization-evidence"),
            user_id=user_id,
            created_at=now,
            evidence_level="USER_CONFIRMED_POLICY",
            source_type=SOURCE,
            source_ref=str(identity),
            content=original.model_dump(mode="json"),
            content_hash=configuration_hash(original.model_dump(mode="json")),
            observed_at=now,
            valid_from=now,
            valid_to=scope.valid_until,
            status="VALID",
        )
        writer.add(proof)
        writer.flush()
        trace = build_trace(
            run_id=identity,
            user_id=user_id,
            phase="EVALUATION",
            as_of=now,
            parent_run_id=None,
            action_id=None,
            algorithm_versions={
                "trace": "decision-trace-v1",
                "goal_release_authorization": PROTOCOL,
            },
            inputs={
                "original_request": body.model_dump(mode="json"),
                "reviewed_scope_hash": scope_hash,
            },
            sources=[evidence_copy(row) for row in [*sources, proof]],
            policies=[],
            outcome={"goal_release_authorization": original.model_dump(mode="json")},
        )
        record_trace(writer, trace)
    with _reader(engine) as reader:
        response = _original(
            reader, user_id, body.expected_epoch_id, body.idempotency_key, now, replay=False
        )
    if response is None:
        raise _error("已记录授权的原命令无法重读")
    return response
