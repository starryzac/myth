"""Audited finite registered-scope questions, without action preparation or consent."""

import copy
import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self, cast
from uuid import UUID, uuid5

from app.api.v1.zhiyu_questions import owned_read, require_question_user
from app.db.models import EvidenceItem
from app.domain.full_joint_goal_archive import JointArchiveError
from app.domain.full_joint_question_contract import Hash, joint_source_fingerprint
from app.domain.full_joint_scope_archive import PROTOCOL as ARCHIVE_PROTOCOL
from app.domain.full_joint_scope_archive import decode_scope_receipt, encode_scope_receipt
from app.domain.full_joint_scope_questions import (
    PROTOCOL,
    REJECTION_CODES,
    REJECTION_PROTOCOL,
    SOURCE_VERSION,
    RegisteredScopeReference,
    RegisteredScopeWorld,
    RejectionCode,
    ScopeEvaluation,
    ScopeQuestionRejection,
    ScopeQuestionRevision,
    economic_signature,
    evaluate_scopes,
    scope_source_fingerprint,
)
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import IntentModel
from app.services.audit_chain import verify_audit_chain
from app.services.autonomy_envelope import _snapshot
from app.services.demo_console import _epoch
from app.services.full_joint_question_workflow import (
    failure,
    resolve_native_request,
)
from app.services.full_joint_question_workflow import (
    fresh_evaluation as fresh_fixed,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_orchestration import MARKER, _audit_healthy, _valid, serial_user, store
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NAMESPACE = UUID("bc2ef236-d8da-4fb0-a77d-a751f5371a2b")
MAX_RECEIPTS = 256
MAX_RAW_WIRE_BYTES = 64 * 1024 * 1024


class ScopeQuestionStartRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    alternatives: Annotated[list[RegisteredScopeReference], Field(min_length=2, max_length=2)]
    supported_input: Literal["EXPLICIT_REGISTERED_SCOPE_ALTERNATIVES"] = (
        "EXPLICIT_REGISTERED_SCOPE_ALTERNATIVES"
    )

    @model_validator(mode="after")
    def unique_declared_references(self) -> Self:
        if len({ref.full_policy_id for ref in self.alternatives}) != len(self.alternatives):
            raise ValueError("Each explicit confirmed scope reference must be distinct")
        return self


class ScopeQuestionRefreshRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=16)]


class ScopeQuestionAnswerRequest(ScopeQuestionRefreshRequest):
    expected_source_fingerprint: Hash
    expected_question_hash: Hash
    selected_full_policy_id: UUID


class ScopeQuestionCloseRequest(ScopeQuestionRefreshRequest):
    pass


def command_key(identity: UUID) -> str:
    return "joint-scope-question:" + identity.hex


def require_wire_capacity(
    value: dict[str, Any], *, copies: int = 1, reserve_bytes: int = 4096
) -> int:
    """Same bounded complete raw JSON as the current Web reader, never a summary hash."""
    size = len(
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    )
    if size * copies + reserve_bytes > MAX_RAW_WIRE_BYTES:
        raise PolicyLifecycleError(
            "QUESTION_WIRE_CAPACITY_EXCEEDED",
            "完整原件超过本阶段只读恢复容量，请保留原请求定位",
            413,
        )
    return size


def fresh_domain(
    session: Session,
    user: UUID,
    epoch: UUID,
    identity: UUID,
    refs: list[RegisteredScopeReference],
    now: datetime,
    *,
    require_expected_versions: bool = False,
) -> tuple[list[RegisteredScopeWorld], dict[str, Any], datetime]:
    """Each native candidate reads the same RR/RO snapshot and whole registered Goal set."""
    worlds: list[RegisteredScopeWorld] = []
    expires = now + timedelta(minutes=15)
    first_basis = None
    for ref in sorted(refs, key=lambda ref: str(ref.full_policy_id)):
        native = resolve_native_request(session, user, ref.full_policy_id, epoch, identity, now)
        if (
            require_expected_versions
            and native.expected_full_policy_version_id != ref.expected_full_policy_version_id
        ):
            raise failure("JOINT_VERSION_CHANGED", "本次显式备选规则的版本已经改变")
        evaluation, manifest, valid_until = fresh_fixed(session, user, native, now)
        compared_basis = {
            "complete_native_basis": manifest["complete_native_basis"],
            "registered_goal_ids": manifest["registered_goal_ids"],
            "shanghai_day": manifest["shanghai_day"],
        }
        if first_basis is None:
            first_basis = compared_basis
        elif first_basis != compared_basis:
            raise failure(
                "JOINT_ALTERNATIVES_SOURCE_DIFFERS", "备选范围必须来自同一完整目标及资金来源"
            )
        worlds.append(
            RegisteredScopeWorld(
                full_policy_id=ref.full_policy_id,
                current_full_policy_version_id=native.expected_full_policy_version_id,
                native_evaluation=evaluation,
                source_manifest=manifest,
                source_fingerprint=joint_source_fingerprint(manifest),
                economic_signature=economic_signature(evaluation),
            )
        )
        expires = min(expires, valid_until)
    manifest = {
        "protocol": PROTOCOL,
        "source_fingerprint_version": SOURCE_VERSION,
        "user_id": str(user),
        "epoch_id": str(epoch),
        "worlds": [
            {
                "full_policy_id": str(world.full_policy_id),
                "current_full_policy_version_id": str(world.current_full_policy_version_id),
                "native_source_fingerprint": world.source_fingerprint,
                "economic_signature": world.economic_signature,
            }
            for world in worlds
        ],
    }
    return worlds, manifest, expires


def chain(receipts: list[ScopeQuestionRevision], identity: UUID) -> list[ScopeQuestionRevision]:
    found = sorted(
        (receipt for receipt in receipts if receipt.session_id == identity),
        key=lambda receipt: receipt.revision,
    )
    if not found:
        raise PolicyLifecycleError("NOT_FOUND", "原联合范围问题不存在", 404)
    first = found[0]
    if first.command_kind != "START" or first.session_id != uuid5(
        NAMESPACE, f"{first.user_id}:{first.epoch_id}:{first.command_id}"
    ):
        raise failure("QUESTION_ORIGINAL_INVALID", "原联合范围问题身份不同")
    if len({receipt.command_id for receipt in found}) != len(found):
        raise failure("QUESTION_ORIGINAL_INVALID", "原联合范围问题命令重复")
    prior = None
    declared_ids = sorted(
        [UUID(ref["full_policy_id"]) for ref in first.original_request["body"]["alternatives"]],
        key=str,
    )
    for number, receipt in enumerate(found, 1):
        if (
            receipt.revision != number
            or (receipt.user_id, receipt.epoch_id) != (first.user_id, first.epoch_id)
            or receipt.previous_receipt_hash != (prior.receipt_hash if prior else None)
            or number > 1
            and receipt.command_kind == "START"
            or [world.full_policy_id for world in receipt.evaluation.worlds] != declared_ids
            or prior is not None
            and (
                prior.state == "CLOSED"
                or receipt.as_of < prior.as_of
                or receipt.original_request["body"].get("expected_revision") != prior.revision
            )
        ):
            raise failure("QUESTION_ORIGINAL_INVALID", "原联合范围版本链不同")
        selected = receipt.evaluation.selected_policy_id
        if (
            prior is None
            and selected is not None
            or receipt.command_kind == "REBASE"
            and selected is not None
        ):
            raise failure("QUESTION_ORIGINAL_INVALID", "开始或重算不能生成用户偏好")
        if prior is not None:
            if receipt.command_kind == "REFRESH" and (
                receipt.source_fingerprint != prior.source_fingerprint
                or receipt.as_of >= prior.valid_until
            ):
                raise failure("QUESTION_ORIGINAL_INVALID", "来源改变或过期须保留显式重算版本")
            if receipt.command_kind == "REBASE" and (
                receipt.source_fingerprint == prior.source_fingerprint
                and receipt.as_of < prior.valid_until
            ):
                raise failure("QUESTION_ORIGINAL_INVALID", "重算必须保留实际变化或过期依据")
            if receipt.command_kind == "ANSWER":
                question = prior.evaluation.pending_question
                body = receipt.original_request["body"]
                if (
                    question is None
                    or prior.state != "AWAITING_INPUT"
                    or receipt.source_fingerprint != prior.source_fingerprint
                    or receipt.as_of >= prior.valid_until
                    or body.get("expected_source_fingerprint") != prior.source_fingerprint
                    or body.get("expected_question_hash") != question.question_hash
                    or body.get("selected_full_policy_id") != str(selected)
                    or selected not in {world.full_policy_id for world in prior.evaluation.worlds}
                ):
                    raise failure("QUESTION_ORIGINAL_INVALID", "原回答须对应当时唯一问题及完整来源")
            elif (
                receipt.command_kind in {"REFRESH", "CLOSE"}
                and selected != prior.evaluation.selected_policy_id
            ):
                raise failure("QUESTION_ORIGINAL_INVALID", "刷新或关闭不能替用户更换选择")
        prior = receipt
    return found


def all_receipts(session: Session, user: UUID) -> list[ScopeQuestionRevision]:
    _snapshot(session)
    rows = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.user_id == user,
                EvidenceItem.source_type == "ZHIYU_NEXT_OPERATION",
                or_(
                    EvidenceItem.content["payload"]["result"]["protocol"].astext == PROTOCOL,
                    EvidenceItem.content["payload"]["result"]["protocol"].astext
                    == ARCHIVE_PROTOCOL,
                ),
            )
            .order_by(EvidenceItem.created_at, EvidenceItem.id)
            .limit(MAX_RECEIPTS + 1)
        )
    )
    if len(rows) > MAX_RECEIPTS:
        raise failure("QUESTION_CAPACITY_EXCEEDED", "原范围问题超过读取容量")
    verified: set[UUID] = set()
    receipts = []
    for row in rows:
        epoch = UUID(row.content["epoch_id"])
        if epoch not in verified:
            if verify_audit_chain(session, user, epoch).status != "VALID":
                raise failure("QUESTION_AUDIT_INVALID", "原范围问题审计无法核验")
            verified.add(epoch)
        original = _valid(session, row, user, epoch)
        result = original["payload"]["result"]
        if result.get("protocol") == ARCHIVE_PROTOCOL:
            receipt = decode_scope_receipt(
                result, user, epoch,
                UUID(original["payload"]["request"]["body"]["client_request_id"]),
            )
        else:
            receipt = ScopeQuestionRevision.model_validate_json(json.dumps(result, allow_nan=False))
        if (
            (receipt.user_id, receipt.epoch_id) != (user, epoch)
            or receipt.original_request != original["payload"]["request"]
            or row.id != uuid5(epoch, f"{MARKER}:OPERATION:{command_key(receipt.command_id)}")
        ):
            raise failure("QUESTION_ORIGINAL_INVALID", "原范围问题请求回执关联不同")
        receipts.append(receipt)
    for identity in {receipt.session_id for receipt in receipts}:
        chain(receipts, identity)
    return receipts


def make_receipt(
    user: UUID,
    epoch: UUID,
    identity: UUID,
    command: UUID,
    kind: Literal["START", "ANSWER", "REFRESH", "REBASE", "CLOSE"],
    original: dict[str, Any],
    evaluation: ScopeEvaluation,
    manifest: dict[str, Any],
    expires: datetime,
    now: datetime,
    prior: ScopeQuestionRevision | None,
    actor: LocalActorPrincipal,
) -> ScopeQuestionRevision:
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
        source_fingerprint=scope_source_fingerprint(manifest),
        evaluation=evaluation,
        state="CLOSED"
        if kind == "CLOSE"
        else {"STABLE": "READY_FOR_REVIEW", "DIVERGENT": "AWAITING_INPUT", "UNKNOWN": "UNKNOWN"}[
            evaluation.status
        ],
    )
    temporary = ScopeQuestionRevision.model_construct(**values, receipt_hash="0" * 64)
    receipt = ScopeQuestionRevision(
        **values,
        receipt_hash=configuration_hash(
            temporary.model_dump(mode="json", exclude={"receipt_hash"})
        ),
    )
    # Current reads carry a small exact reference; complete originals remain
    # independently retrievable under the unchanged single-response wire budget.
    require_wire_capacity(receipt.model_dump(mode="json"))
    return receipt


def all_rejections(session: Session, user: UUID) -> list[ScopeQuestionRejection]:
    _snapshot(session)
    rows = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.user_id == user,
                EvidenceItem.source_type == "ZHIYU_NEXT_OPERATION",
                EvidenceItem.content["payload"]["result"]["protocol"].astext == REJECTION_PROTOCOL,
            )
            .order_by(EvidenceItem.created_at, EvidenceItem.id)
            .limit(MAX_RECEIPTS + 1)
        )
    )
    if len(rows) > MAX_RECEIPTS:
        raise failure("QUESTION_CAPACITY_EXCEEDED", "原范围拒绝记录超过读取容量")
    verified: set[UUID] = set()
    records = []
    for row in rows:
        epoch = UUID(row.content["epoch_id"])
        if epoch not in verified:
            if verify_audit_chain(session, user, epoch).status != "VALID":
                raise failure("QUESTION_AUDIT_INVALID", "原范围拒绝审计无法核验")
            verified.add(epoch)
        original = _valid(session, row, user, epoch)
        record = ScopeQuestionRejection.model_validate_json(
            json.dumps(original["payload"]["result"], allow_nan=False)
        )
        if (
            (record.user_id, record.epoch_id) != (user, epoch)
            or record.original_request != original["payload"]["request"]
            or row.id != uuid5(epoch, f"{MARKER}:OPERATION:{command_key(record.command_id)}")
        ):
            raise failure("QUESTION_ORIGINAL_INVALID", "原非金融拒绝请求回执关联不同")
        records.append(record)
    return records


def require_total_capacity(
    receipts: list[ScopeQuestionRevision], rejections: list[ScopeQuestionRejection]
) -> None:
    if len(receipts) + len(rejections) > MAX_RECEIPTS:
        raise failure("QUESTION_CAPACITY_EXCEEDED", "观察和拒绝共同超过原命令容量")


def require_rejection_capacity(
    receipts: list[ScopeQuestionRevision], epoch: UUID, rejected_count: int
) -> None:
    heads: dict[UUID, ScopeQuestionRevision] = {}
    for receipt in receipts:
        previous = heads.get(receipt.session_id)
        if previous is None or receipt.revision > previous.revision:
            heads[receipt.session_id] = receipt
    reserved = sum(head.epoch_id == epoch and head.state != "CLOSED" for head in heads.values())
    if len(receipts) + rejected_count + 1 + reserved > MAX_RECEIPTS:
        raise failure("QUESTION_CAPACITY_EXCEEDED", "拒绝也计入总容量并保留每个关闭槽")


def make_rejection(
    user: UUID,
    epoch: UUID,
    identity: UUID | None,
    original: dict[str, Any],
    now: datetime,
    actor: LocalActorPrincipal,
    problem: PolicyLifecycleError,
) -> ScopeQuestionRejection:
    if problem.code not in REJECTION_CODES:
        raise problem
    values: dict[str, Any] = dict(
        user_id=user,
        epoch_id=epoch,
        session_id=identity,
        command_id=UUID(original["body"]["client_request_id"]),
        authenticated_actor=actor,
        original_request=original,
        request_hash=configuration_hash(
            {"user_id": str(user), "epoch_id": str(epoch), "original_request": original}
        ),
        as_of=now,
        code=cast(RejectionCode, problem.code),
        message=problem.message,
        http_status=problem.status_code,
    )
    temporary = ScopeQuestionRejection.model_construct(**values, rejection_hash="0" * 64)
    return ScopeQuestionRejection(
        **values,
        rejection_hash=configuration_hash(
            temporary.model_dump(mode="json", exclude={"rejection_hash"})
        ),
    )


def persist_rejection(session: Session, record: ScopeQuestionRejection, now: datetime) -> None:
    store(
        session,
        record.user_id,
        "OPERATION",
        command_key(record.command_id),
        {"request": record.original_request, "result": record.model_dump(mode="json")},
        now,
    )


def archived_result(receipt: ScopeQuestionRevision) -> dict[str, Any]:
    try:
        return encode_scope_receipt(receipt)
    except JointArchiveError as cause:
        raise PolicyLifecycleError(
            "QUESTION_WIRE_CAPACITY_EXCEEDED",
            "完整原件超过本阶段原件归档容量，请保留原请求定位", 413,
        ) from cause


def persist(
    session: Session, receipt: ScopeQuestionRevision, now: datetime, archived: dict[str, Any]
) -> None:
    store(
        session,
        receipt.user_id,
        "OPERATION",
        command_key(receipt.command_id),
        {"request": receipt.original_request, "result": archived},
        now,
    )


def require_append_capacity(
    receipts: list[ScopeQuestionRevision],
    epoch: UUID,
    prior: ScopeQuestionRevision | None,
    kind: str,
    *,
    rejection_count: int = 0,
) -> None:
    heads: dict[UUID, ScopeQuestionRevision] = {}
    for receipt in receipts:
        head = heads.get(receipt.session_id)
        if head is None or receipt.revision > head.revision:
            heads[receipt.session_id] = receipt
    open_count = sum(head.epoch_id == epoch and head.state != "CLOSED" for head in heads.values())
    reserve = open_count - 1 if kind == "CLOSE" else open_count + 1 if prior is None else open_count
    if reserve < 0 or len(receipts) + rejection_count + 1 + reserve > MAX_RECEIPTS:
        raise failure("QUESTION_CAPACITY_EXCEEDED", "每个当前未关闭范围问题须保留关闭槽")


def mutate(
    engine: Engine,
    user: UUID,
    identity: UUID | None,
    body: ScopeQuestionStartRequest | ScopeQuestionRefreshRequest,
    path: str,
    now: datetime,
    actor: LocalActorPrincipal,
) -> ScopeQuestionRevision:
    require_question_user(actor, user, now)
    kind: Literal["START", "ANSWER", "REFRESH", "REBASE", "CLOSE"] = (
        "START"
        if isinstance(body, ScopeQuestionStartRequest)
        else "ANSWER"
        if isinstance(body, ScopeQuestionAnswerRequest)
        else "CLOSE"
        if isinstance(body, ScopeQuestionCloseRequest)
        else "REFRESH"
    )
    original = {
        "kind": "FULL_JOINT_SCOPE_" + kind,
        "path": path,
        "body": body.model_dump(mode="json"),
    }
    with serial_user(engine, user):
        epoch: UUID | None = None
        replayed_rejection: ScopeQuestionRejection | None = None
        try:
            with owned_read(engine) as read:
                epoch = _epoch(read, user)
                if body.expected_epoch_id != epoch:
                    raise failure("STALE_QUESTION_EPOCH", "范围问题需要当前原轮次")
                receipts = all_receipts(read, user)
                rejections = all_rejections(read, user)
                require_total_capacity(receipts, rejections)
                repeated = [
                    receipt
                    for receipt in receipts
                    if (receipt.epoch_id, receipt.command_id) == (epoch, body.client_request_id)
                ]
                refused = [
                    record
                    for record in rejections
                    if (record.epoch_id, record.command_id) == (epoch, body.client_request_id)
                ]
                if refused:
                    if (
                        len(refused) != 1
                        or repeated
                        or refused[0].original_request != original
                        or identity is not None
                        and refused[0].session_id != identity
                    ):
                        raise failure("REQUEST_CONFLICT", "原拒绝命令键不能更换请求")
                    replayed_rejection = refused[0]
                    raise PolicyLifecycleError(
                        replayed_rejection.code,
                        replayed_rejection.message,
                        replayed_rejection.http_status,
                    )
                if repeated:
                    if (
                        len(repeated) != 1
                        or repeated[0].original_request != original
                        or identity is not None
                        and repeated[0].session_id != identity
                    ):
                        raise failure("REQUEST_CONFLICT", "原范围问题键不能换请求")
                    return repeated[0]
                prior = None
                if isinstance(body, ScopeQuestionStartRequest):
                    identity = uuid5(NAMESPACE, f"{user}:{epoch}:{body.client_request_id}")
                    refs = body.alternatives
                else:
                    assert identity is not None
                    history = chain(receipts, identity)
                    prior = history[-1]
                    if (
                        prior.state == "CLOSED"
                        or prior.epoch_id != epoch
                        or prior.revision != body.expected_revision
                        or prior.revision >= 16
                        and kind != "CLOSE"
                    ):
                        raise failure("STALE_QUESTION_REVISION", "请回读范围问题当前原件")
                    refs = [
                        RegisteredScopeReference.model_validate_json(json.dumps(ref))
                        for ref in history[0].original_request["body"]["alternatives"]
                    ]
                assert identity is not None
                require_append_capacity(
                    receipts, epoch, prior, kind, rejection_count=len(rejections)
                )
                if kind == "CLOSE":
                    assert prior is not None
                    evaluation, manifest, expires = (
                        prior.evaluation,
                        copy.deepcopy(prior.source_manifest),
                        prior.valid_until,
                    )
                else:
                    worlds, manifest, expires = fresh_domain(
                        read,
                        user,
                        epoch,
                        identity,
                        refs,
                        now,
                        require_expected_versions=isinstance(body, ScopeQuestionStartRequest),
                    )
                    fingerprint = scope_source_fingerprint(manifest)
                    changed = prior is not None and (
                        fingerprint != prior.source_fingerprint or now >= prior.valid_until
                    )
                    selected = (
                        None if prior is None or changed else prior.evaluation.selected_policy_id
                    )
                    if changed:
                        kind = "REBASE"
                    elif isinstance(body, ScopeQuestionAnswerRequest):
                        assert prior is not None
                        question = prior.evaluation.pending_question
                        if (
                            prior.state != "AWAITING_INPUT"
                            or question is None
                            or body.expected_source_fingerprint != prior.source_fingerprint
                            or body.expected_question_hash != question.question_hash
                            or body.selected_full_policy_id
                            not in {world.full_policy_id for world in worlds}
                        ):
                            raise failure(
                                "STALE_QUESTION_ANSWER", "回答须绑定当前唯一问题和同一原来源"
                            )
                        selected = body.selected_full_policy_id
                    evaluation = evaluate_scopes(worlds, manifest, selected)
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
                chain([*receipts, receipt], identity)
                archived = archived_result(receipt)
        except PolicyLifecycleError as problem:
            if (
                problem.code not in REJECTION_CODES
                or epoch is None
                or epoch != body.expected_epoch_id
                or replayed_rejection is not None
            ):
                raise
            with owned_read(engine) as retained:
                if _epoch(retained, user) != epoch:
                    raise failure(
                        "STALE_QUESTION_EPOCH", "原拒绝只能记录在已核实当前轮次"
                    ) from problem
                successes = all_receipts(retained, user)
                failures = all_rejections(retained, user)
                require_total_capacity(successes, failures)
                require_rejection_capacity(successes, epoch, len(failures))
            rejected = make_rejection(user, epoch, identity, original, now, actor, problem)
            with Session(engine) as rejection_write, rejection_write.begin():
                _audit_healthy(rejection_write, user)
                if _epoch(rejection_write, user) != epoch:
                    raise failure("STALE_QUESTION_EPOCH", "拒绝写入前轮次已改变") from problem
                persist_rejection(rejection_write, rejected, now)
            raise
        with Session(engine) as write, write.begin():
            _audit_healthy(write, user)
            persist(write, receipt, now, archived)
        return receipt


def revision_reference(receipt: ScopeQuestionRevision) -> dict[str, Any]:
    return {
        "protocol": "full-joint-scope-revision-reference-v1",
        "user_id": str(receipt.user_id),
        "epoch_id": str(receipt.epoch_id),
        "session_id": str(receipt.session_id),
        "command_id": str(receipt.command_id),
        "revision": receipt.revision,
        "previous_receipt_hash": receipt.previous_receipt_hash,
        "receipt_hash": receipt.receipt_hash,
    }


def read_view(session: Session, user: UUID, identity: UUID, now: datetime) -> dict[str, Any]:
    history = chain(all_receipts(session, user), identity)
    latest = history[-1]
    epoch = _epoch(session, user)
    result: dict[str, Any] = {
        "protocol": PROTOCOL,
        "view_protocol": "full-joint-scope-current-read-v2",
        "current_revision_ref": revision_reference(latest),
        "effective_state": "ARCHIVED" if epoch != latest.epoch_id else latest.state,
        "current_source_fingerprint": None,
        "current_evaluation": None,
        "bank_authority": False,
        "execution_eligible": False,
        "old_confirmation_inherited": False,
        "receipt_is_current_authority": False,
        "current_read_required": True,
        "requires_separate_whole_financial_confirmation": True,
    }
    if epoch != latest.epoch_id or latest.state == "CLOSED":
        require_wire_capacity(result, reserve_bytes=0)
        return result
    try:
        refs = [
            RegisteredScopeReference.model_validate_json(json.dumps(ref))
            for ref in history[0].original_request["body"]["alternatives"]
        ]
        worlds, manifest, _ = fresh_domain(session, user, epoch, identity, refs, now)
        fingerprint = scope_source_fingerprint(manifest)
        changed = fingerprint != latest.source_fingerprint or now >= latest.valid_until
        if changed:
            result["effective_state"] = "STALE_RECOMPUTATION_REQUIRED"
        evaluation = evaluate_scopes(
            worlds, manifest, None if changed else latest.evaluation.selected_policy_id
        )
        result["current_source_fingerprint"] = fingerprint
        result["current_evaluation"] = evaluation.model_dump(mode="json")
    except PolicyLifecycleError as error:
        result["effective_state"] = "STALE_RECOMPUTATION_REQUIRED"
        result["current_read_error"] = {"code": error.code, "message": error.message}
    require_wire_capacity(result, reserve_bytes=0)
    return result


def read_command(session: Session, user: UUID, epoch: UUID, command: UUID) -> dict[str, Any]:
    receipts = all_receipts(session, user)
    rejections = all_rejections(session, user)
    require_total_capacity(receipts, rejections)
    found = [
        receipt
        for receipt in receipts
        if (receipt.epoch_id, receipt.command_id) == (epoch, command)
    ]
    refused = [
        record for record in rejections if (record.epoch_id, record.command_id) == (epoch, command)
    ]
    if len(found) + len(refused) > 1:
        raise failure("QUESTION_ORIGINAL_INVALID", "原范围问题命令重复")
    result = {
        "protocol": PROTOCOL,
        "status": "RECORDED"
        if found
        else "RECORDED_REJECTION"
        if refused
        else "NOT_FOUND_NOT_FINAL",
        "epoch_id": str(epoch),
        "client_request_id": str(command),
        "original_request": found[0].original_request
        if found
        else refused[0].original_request
        if refused
        else None,
        "original_receipt": found[0].model_dump(mode="json") if found else None,
        "original_rejection": refused[0].model_dump(mode="json") if refused else None,
        "not_found_is_final": False,
        "replacement_allowed": False,
        "bank_authority": False,
        "execution_eligible": False,
        "old_confirmation_inherited": False,
        "receipt_is_current_authority": False,
        "current_read_required": True,
    }
    require_wire_capacity(result, reserve_bytes=0)
    return result
