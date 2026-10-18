"""Audited fixed-rule joint preference observations, never action preparation or consent."""

import copy
import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.api.v1.zhiyu_questions import owned_read, require_question_user
from app.db.models import EvidenceItem
from app.domain.full_joint_goal_execution import FullJointGoalPrepareRequest
from app.domain.full_joint_question_contract import (
    PROTOCOL,
    SOURCE_VERSION,
    JointFixedEvaluation,
    JointQuestionRevision,
    evaluate_fixed,
    joint_source_fingerprint,
)
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import IntentModel
from app.services.audit_chain import verify_audit_chain
from app.services.autonomy_envelope import _snapshot
from app.services.demo_console import _epoch
from app.services.full_joint_goal_execution import capture_full_joint_goal_execution
from app.services.full_policy_lifecycle import read_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_orchestration import MARKER, _audit_healthy, _valid, serial_user, store
from pydantic import Field, StrictInt
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NAMESPACE = UUID("f0528a7d-e513-4bcf-988f-3bcba8a4aee4")
MAX_RECEIPTS = 256


class JointQuestionStartRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    full_policy_id: UUID
    expected_full_policy_version_id: UUID
    supported_input: Literal["REGISTERED_CONFIGURATION"] = "REGISTERED_CONFIGURATION"


class JointQuestionRefreshRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=16)]


class JointQuestionCloseRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=16)]


def failure(code: str, message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def command_key(identity: UUID) -> str:
    return "joint-question:" + identity.hex


def resolve_native_request(
    session: Session, user: UUID, policy_id: UUID, epoch: UUID, identity: UUID, now: datetime
) -> FullJointGoalPrepareRequest:
    full = read_full_policy(session, user, policy_id, now)
    if full.template_name != "GoalAllocationPolicy" or full.epoch_id != epoch:
        raise failure("UNSUPPORTED_BUSINESS_SCOPE", "需要当前本人联合目标完整规则")
    return FullJointGoalPrepareRequest(
        full_policy_id=policy_id,
        expected_full_policy_version_id=full.current_version.version_id,
        expected_epoch_id=epoch,
        idempotency_key="joint-question-native:" + identity.hex,
    )


def fresh_evaluation(
    session: Session, user: UUID, native: FullJointGoalPrepareRequest, now: datetime
) -> tuple[JointFixedEvaluation, dict[str, Any], datetime]:
    _snapshot(session)
    _audit_healthy(session, user)
    captured = capture_full_joint_goal_execution(session, user, native, now, source_dag=True)
    evaluation = evaluate_fixed(captured.preview)
    inputs, originals = captured.inputs, captured.originals
    plan = captured.preview.plan
    if plan is not None and any(
        ref.evidence_id not in originals.sources
        or originals.sources[ref.evidence_id].content_hash != ref.content_hash
        or originals.sources[ref.evidence_id].user_id != user
        for ref in inputs.joint.verified_source_refs
    ):
        raise failure("CURRENT_JOINT_SOURCES_INCOMPLETE", "原整组来源副本不能核验")
    manifest = {
        "protocol": PROTOCOL,
        "source_fingerprint_version": SOURCE_VERSION,
        "user_id": str(user),
        "epoch_id": str(native.expected_epoch_id),
        "shanghai_day": now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        "complete_native_basis": copy.deepcopy(
            inputs.joint.original_actual_input.base.financial_basis
        ),
        "sources": [
            row.model_dump(mode="json")
            for row in sorted(originals.sources.values(), key=lambda row: row.id)
        ],
        "policies": [
            row.model_dump(mode="json")
            for row in sorted(originals.policies.values(), key=lambda row: row.id)
        ],
        "full_scope": inputs.scope.model_dump(mode="json"),
        "registered_goal_ids": [str(value) for value in captured.preview.registered_goal_ids],
        "allocation_input": plan.allocation_input.model_dump(mode="json") if plan else None,
        "vector_signature": evaluation.vector_signature,
        "native_status": captured.preview.status,
        "native_reasons": captured.preview.reasons,
        "unresolved_action_ids": [
            str(value) for value in captured.preview.unresolved_original_action_ids
        ],
    }
    return (
        evaluation,
        manifest,
        min(now + timedelta(minutes=15), plan.expires_at)
        if plan
        else (now + timedelta(minutes=15)),
    )


def chain(receipts: list[JointQuestionRevision], identity: UUID) -> list[JointQuestionRevision]:
    found = sorted(
        (receipt for receipt in receipts if receipt.session_id == identity),
        key=lambda receipt: receipt.revision,
    )
    if not found:
        raise PolicyLifecycleError("NOT_FOUND", "原联合目标偏好观察不存在", 404)
    first = found[0]
    if first.command_kind != "START" or first.session_id != uuid5(
        NAMESPACE, f"{first.user_id}:{first.epoch_id}:{first.command_id}"
    ):
        raise failure("QUESTION_ORIGINAL_INVALID", "原联合观察会话身份不同")
    if len({receipt.command_id for receipt in found}) != len(found):
        raise failure("QUESTION_ORIGINAL_INVALID", "原联合观察命令重复")
    prior = None
    for number, receipt in enumerate(found, 1):
        if (
            receipt.revision != number
            or (receipt.user_id, receipt.epoch_id) != (first.user_id, first.epoch_id)
            or receipt.previous_receipt_hash != (prior.receipt_hash if prior else None)
            or number > 1
            and receipt.command_kind == "START"
            or prior is not None
            and (
                prior.state == "CLOSED"
                or receipt.as_of < prior.as_of
                or receipt.original_request["body"].get("expected_revision") != prior.revision
            )
        ):
            raise failure("QUESTION_ORIGINAL_INVALID", "原联合观察版本链不同")
        prior = receipt
    return found


def all_receipts(session: Session, user: UUID) -> list[JointQuestionRevision]:
    _snapshot(session)
    rows = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.user_id == user,
                EvidenceItem.source_type == "ZHIYU_NEXT_OPERATION",
                EvidenceItem.content["payload"]["result"]["protocol"].astext == PROTOCOL,
            )
            .order_by(EvidenceItem.created_at, EvidenceItem.id)
            .limit(MAX_RECEIPTS + 1)
        )
    )
    if len(rows) > MAX_RECEIPTS:
        raise failure("QUESTION_CAPACITY_EXCEEDED", "完整联合观察原件超过读取容量")
    verified: set[UUID] = set()
    receipts = []
    for row in rows:
        epoch = UUID(row.content["epoch_id"])
        if epoch not in verified:
            if verify_audit_chain(session, user, epoch).status != "VALID":
                raise failure("QUESTION_AUDIT_INVALID", "原联合观察审计无法核验")
            verified.add(epoch)
        original = _valid(session, row, user, epoch)
        receipt = JointQuestionRevision.model_validate_json(
            json.dumps(original["payload"]["result"], allow_nan=False)
        )
        if (
            (receipt.user_id, receipt.epoch_id) != (user, epoch)
            or receipt.original_request != original["payload"]["request"]
            or row.id != uuid5(epoch, f"{MARKER}:OPERATION:{command_key(receipt.command_id)}")
        ):
            raise failure("QUESTION_ORIGINAL_INVALID", "原联合观察请求或回执关联不同")
        receipts.append(receipt)
    for identity in {receipt.session_id for receipt in receipts}:
        chain(receipts, identity)
    return receipts


def make_receipt(
    user: UUID,
    epoch: UUID,
    identity: UUID,
    command: UUID,
    kind: Literal["START", "REFRESH", "REBASE", "CLOSE"],
    original: dict[str, Any],
    evaluation: JointFixedEvaluation,
    manifest: dict[str, Any],
    expires: datetime,
    now: datetime,
    prior: JointQuestionRevision | None,
    actor: LocalActorPrincipal,
) -> JointQuestionRevision:
    values: dict[str, Any] = dict(
        user_id=user,
        epoch_id=epoch,
        session_id=identity,
        revision=prior.revision + 1 if prior else 1,
        previous_receipt_hash=prior.receipt_hash if prior else None,
        command_id=command,
        command_kind=kind,
        authenticated_actor=actor,
        original_request=original,
        as_of=now,
        valid_until=expires,
        source_manifest=manifest,
        source_fingerprint=joint_source_fingerprint(manifest),
        evaluation=evaluation,
        state="CLOSED"
        if kind == "CLOSE"
        else ("UNKNOWN" if evaluation.status == "UNKNOWN" else "READY_FOR_REVIEW"),
    )
    receipt = JointQuestionRevision.model_construct(**values, receipt_hash="0" * 64)
    values["receipt_hash"] = configuration_hash(
        receipt.model_dump(mode="json", exclude={"receipt_hash"})
    )
    return JointQuestionRevision(**values)


def persist(session: Session, receipt: JointQuestionRevision, now: datetime) -> None:
    store(
        session,
        receipt.user_id,
        "OPERATION",
        command_key(receipt.command_id),
        {"request": receipt.original_request, "result": receipt.model_dump(mode="json")},
        now,
    )


def require_append_capacity(
    receipts: list[JointQuestionRevision],
    epoch: UUID,
    prior: JointQuestionRevision | None,
    kind: str,
) -> None:
    """Every current open session retains its own immutable closing receipt slot."""
    heads: dict[UUID, JointQuestionRevision] = {}
    for receipt in receipts:
        previous = heads.get(receipt.session_id)
        if previous is None or receipt.revision > previous.revision:
            heads[receipt.session_id] = receipt
    open_count = sum(head.epoch_id == epoch and head.state != "CLOSED" for head in heads.values())
    remaining_close_slots = (
        open_count - 1 if kind == "CLOSE" else (open_count + 1 if prior is None else open_count)
    )
    if remaining_close_slots < 0 or len(receipts) + 1 + remaining_close_slots > MAX_RECEIPTS:
        raise failure("QUESTION_CAPACITY_EXCEEDED", "每个当前未关闭观察须保留自己的关闭槽")


def mutate(
    engine: Engine,
    user: UUID,
    identity: UUID | None,
    body: JointQuestionStartRequest | JointQuestionRefreshRequest | JointQuestionCloseRequest,
    path: str,
    now: datetime,
    actor: LocalActorPrincipal,
) -> JointQuestionRevision:
    require_question_user(actor, user, now)
    kind: Literal["START", "REFRESH", "REBASE", "CLOSE"] = (
        "START"
        if isinstance(body, JointQuestionStartRequest)
        else "CLOSE"
        if isinstance(body, JointQuestionCloseRequest)
        else "REFRESH"
    )
    original = {
        "kind": "FULL_JOINT_FIXED_" + kind,
        "path": path,
        "body": body.model_dump(mode="json"),
    }
    with serial_user(engine, user):
        with owned_read(engine) as read:
            epoch = _epoch(read, user)
            if body.expected_epoch_id != epoch:
                raise failure("STALE_QUESTION_EPOCH", "联合偏好命令需要当前原轮次")
            receipts = all_receipts(read, user)
            repeated = [
                receipt
                for receipt in receipts
                if (receipt.epoch_id == epoch and receipt.command_id == body.client_request_id)
            ]
            if repeated:
                if (
                    len(repeated) != 1
                    or repeated[0].original_request != original
                    or (identity is not None and repeated[0].session_id != identity)
                ):
                    raise failure("REQUEST_CONFLICT", "原联合偏好键不能更换请求")
                return repeated[0]
            prior = None
            if isinstance(body, JointQuestionStartRequest):
                identity = uuid5(NAMESPACE, f"{user}:{epoch}:{body.client_request_id}")
                policy_id = body.full_policy_id
            else:
                assert identity is not None
                history = chain(receipts, identity)
                prior = history[-1]
                if (
                    prior.state == "CLOSED"
                    or prior.epoch_id != epoch
                    or (
                        prior.revision != body.expected_revision
                        or prior.revision >= 16
                        and kind != "CLOSE"
                    )
                ):
                    raise failure("STALE_QUESTION_REVISION", "请回读联合偏好当前原件")
                policy_id = UUID(history[0].original_request["body"]["full_policy_id"])
            assert identity is not None
            require_append_capacity(receipts, epoch, prior, kind)
            if kind == "CLOSE":
                assert prior is not None
                evaluation, manifest, expires = (
                    prior.evaluation,
                    copy.deepcopy(prior.source_manifest),
                    prior.valid_until,
                )
            else:
                native = resolve_native_request(read, user, policy_id, epoch, identity, now)
                if isinstance(body, JointQuestionStartRequest) and (
                    native.expected_full_policy_version_id != body.expected_full_policy_version_id
                ):
                    raise failure("JOINT_VERSION_CHANGED", "当前完整联合规则版本已经改变")
                evaluation, manifest, expires = fresh_evaluation(read, user, native, now)
                if prior is not None and (
                    prior.source_fingerprint != joint_source_fingerprint(manifest)
                    or now >= prior.valid_until
                ):
                    kind = "REBASE"
            receipt = make_receipt(
                user,
                epoch,
                identity,
                body.client_request_id,
                kind,
                original,
                evaluation,
                manifest,
                expires,
                now,
                prior,
                actor,
            )
        with Session(engine) as write, write.begin():
            _audit_healthy(write, user)
            persist(write, receipt, now)
        return receipt


def read_view(session: Session, user: UUID, identity: UUID, now: datetime) -> dict[str, Any]:
    history = chain(all_receipts(session, user), identity)
    latest = history[-1]
    epoch = _epoch(session, user)
    result: dict[str, Any] = {
        "protocol": PROTOCOL,
        "current_revision": latest.model_dump(mode="json"),
        "effective_state": "ARCHIVED" if epoch != latest.epoch_id else latest.state,
        "current_source_fingerprint": None,
        "current_evaluation": None,
        "bank_authority": False,
        "execution_eligible": False,
        "old_confirmation_inherited": False,
        "current_read_required": True,
        "receipt_is_current_authority": False,
        "requires_separate_whole_financial_confirmation": True,
    }
    if epoch != latest.epoch_id or latest.state == "CLOSED":
        return result
    try:
        policy_id = UUID(history[0].original_request["body"]["full_policy_id"])
        native = resolve_native_request(session, user, policy_id, epoch, identity, now)
        evaluation, manifest, _ = fresh_evaluation(session, user, native, now)
        fingerprint = joint_source_fingerprint(manifest)
        if fingerprint != latest.source_fingerprint or now >= latest.valid_until:
            result["effective_state"] = "STALE_RECOMPUTATION_REQUIRED"
        result["current_source_fingerprint"] = fingerprint
        result["current_evaluation"] = evaluation.model_dump(mode="json")
    except PolicyLifecycleError as error:
        result["effective_state"] = "STALE_RECOMPUTATION_REQUIRED"
        result["current_read_error"] = {"code": error.code, "message": error.message}
    return result


def read_command(session: Session, user: UUID, epoch: UUID, command: UUID) -> dict[str, Any]:
    found = [
        receipt
        for receipt in all_receipts(session, user)
        if (receipt.epoch_id == epoch and receipt.command_id == command)
    ]
    if len(found) > 1:
        raise failure("QUESTION_ORIGINAL_INVALID", "原联合观察命令重复")
    return {
        "protocol": PROTOCOL,
        "status": "RECORDED" if found else "NOT_FOUND_NOT_FINAL",
        "epoch_id": str(epoch),
        "client_request_id": str(command),
        "original_request": found[0].original_request if found else None,
        "original_receipt": found[0].model_dump(mode="json") if found else None,
        "not_found_is_final": False,
        "replacement_allowed": False,
        "bank_authority": False,
        "execution_eligible": False,
        "old_confirmation_inherited": False,
        "current_read_required": True,
        "receipt_is_current_authority": False,
    }
