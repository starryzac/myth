"""Audited persistent two-mode preference receipts over genuine native whole previews."""

import copy
import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.api.v1.zhiyu_questions import owned_read, require_question_user
from app.db.models import EvidenceItem, Goal, PolicyVersion
from app.domain.autonomy import classify_autonomy
from app.domain.full_asset_execution import FullAssetPrepareRequest
from app.domain.full_asset_execution_guard import validate_remaining_combination
from app.domain.full_asset_mode_contract import (
    MODES,
    PROTOCOL,
    SOURCE_FINGERPRINT_VERSION,
    AssetModeEvaluation,
    AssetModeRevision,
    AssetModeWorld,
    AssetVectorChild,
    Mode,
    canonical_effect,
    evaluate_modes,
    mode_source_fingerprint,
    vector_signature,
)
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import configuration_hash
from app.domain.standing_asset_residual import AssetResidualEvaluation
from app.domain.standing_asset_source_lineage import prefix_source_equivalent
from app.domain.standing_owned_asset_read import AcceptedAssetReadProof, owned_source_equivalent
from app.services.action_contracts import IntentModel, PurchaseIntent
from app.services.audit_chain import verify_audit_chain
from app.services.autonomy import _basis, _facts
from app.services.autonomy_envelope import _snapshot
from app.services.decision_recording import start_capture
from app.services.demo_console import _epoch
from app.services.execution_context import load_execution_context
from app.services.full_asset_execution import preview_full_asset_execution
from app.services.full_policy_lifecycle import read_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError, is_version_authorized
from app.services.question_workflow import _all_records, _heads, source_fingerprint
from app.services.standing_owned_asset_scope import current_owned_scope
from app.services.zhiyu_orchestration import MARKER, _audit_healthy, _valid, serial_user, store
from pydantic import Field, StrictInt
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NAMESPACE = UUID("7812e5f0-f26d-44aa-b9ce-45dc1fe6a55c")
MAX_RECEIPTS = 256


class ModeStartRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    full_policy_id: UUID
    expected_full_policy_version_id: UUID


class ModeRefreshRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=16)]


class ModeAnswerRequest(ModeRefreshRequest):
    question_id: UUID
    mode: Mode


class ModeCloseRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=16)]


def failure(code: str, message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def current_native_permission(
    session: Session,
    user: UUID,
    goal: Goal,
    epoch: UUID,
    full_policy_id: UUID,
    full_version_id: UUID,
    full_hash: str,
    now: datetime,
) -> PolicyVersion:
    """Resolve an original goal link or an independently signed native binding.

    This reads permission and never attaches a policy to the goal. More than one
    current explicit binding is an ambiguity, not an invitation to pick a winner.
    """
    from app.api.v1.zhiyu_asset_permissions import (
        decode_permission_binding,
        verify_original_permission_result,
    )
    from app.api.v1.zhiyu_assets import anchored

    if goal.asset_policy_id is not None:
        version = session.scalar(
            select(PolicyVersion)
            .where(PolicyVersion.user_id == user, PolicyVersion.policy_id == goal.asset_policy_id)
            .order_by(PolicyVersion.version_number.desc())
            .limit(1)
        )
        if version is None or not is_version_authorized(session, user, version.id, now):
            raise failure("CURRENT_MVP_PERMISSION_REQUIRED", "目标原资产权限并非当前有效版本")
        return version
    rows = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.user_id == user,
                EvidenceItem.source_type == "ZHIYU_NEXT_BINDING",
                EvidenceItem.content["epoch_id"].astext == str(epoch),
                EvidenceItem.content["key"].astext.startswith("asset-permission:"),
            )
            .order_by(EvidenceItem.id)
            .limit(MAX_RECEIPTS + 1)
        )
    )
    if len(rows) > MAX_RECEIPTS:
        raise failure("NATIVE_PERMISSION_INVENTORY_INCOMPLETE", "原权限分母超过完整读取容量")
    candidates: dict[UUID, PolicyVersion] = {}
    for row in rows:
        native = decode_permission_binding(_valid(session, row, user, epoch)["payload"])
        relationship = native.full_relationship
        configuration = native.canonical_configuration
        if (
            native.user_id != user
            or native.epoch_id != epoch
            or native.confirmed_at > now
            or configuration.get("scope") != "goal"
            or configuration.get("goal_id") != str(goal.id)
            or relationship.policy_id != full_policy_id
            or relationship.version_id != full_version_id
            or relationship.configuration_hash != full_hash
        ):
            continue
        raw = anchored(
            session, user, "OPERATION", str(native.original_confirmation_request.client_request_id)
        )
        if raw is None:
            raise failure("NATIVE_PERMISSION_ORIGINAL_INCOMPLETE", "原权限缺少同事务USER确认原件")
        verify_original_permission_result(
            session, user, native.original_confirmation_request.client_request_id, raw
        )
        version = session.get(PolicyVersion, native.native_version_id)
        if version is not None and is_version_authorized(session, user, version.id, now):
            candidates[version.id] = version
    if len(candidates) != 1:
        raise failure("CURRENT_MVP_PERMISSION_REQUIRED", "需要唯一、当前且独立签署的目标资产权限")
    return next(iter(candidates.values()))


def resolve_native_request(
    session: Session, user: UUID, policy_id: UUID, epoch: UUID, key: str, now: datetime
) -> FullAssetPrepareRequest:
    full = read_full_policy(session, user, policy_id, now)
    if full.template_name != "AssetAuthorizationPolicy":
        raise failure("UNSUPPORTED_BUSINESS_SCOPE", "仅比较完整资产模式，不替代联合目标父效果")
    configuration = full.current_version.configuration
    goal_id = configuration.get("goal_id")
    if configuration.get("scope") != "goal" or not isinstance(goal_id, str):
        raise failure("ACTUAL_GOAL_SCOPE_REQUIRED", "FIXED_LADDER需要实际目标及原资产链接")
    goal = session.get(Goal, UUID(goal_id))
    if goal is None or goal.user_id != user:
        raise failure("CURRENT_GOAL_LINK_REQUIRED", "缺失本人实际目标资产链接")
    version = current_native_permission(
        session,
        user,
        goal,
        epoch,
        policy_id,
        full.current_version.version_id,
        full.current_version.content_hash,
        now,
    )
    return FullAssetPrepareRequest(
        full_policy_id=policy_id,
        expected_full_policy_version_id=full.current_version.version_id,
        mvp_asset_policy_id=version.policy_id,
        expected_mvp_policy_version_id=version.id,
        goal_id=goal.id,
        expected_goal_policy_version_id=goal.policy_version_id,
        expected_epoch_id=epoch,
        idempotency_key=key,
    )


def fresh_evaluation(
    session: Session,
    user: UUID,
    original: FullAssetPrepareRequest,
    now: datetime,
    selected: Mode | None = None,
    *,
    owned: AcceptedAssetReadProof | None = None,
    maximum_turnover_cents: int | None = None,
) -> tuple[AssetModeEvaluation, dict[str, Any], datetime]:
    _snapshot(session)
    _audit_healthy(session, user)
    active_owned = current_owned_scope(session, user, now)
    if active_owned != owned:
        raise failure("OWNED_ASSET_SCOPE_REQUIRED", "显式原整组合读作用域不匹配")
    if owned is not None:
        raise failure("OWNED_RESIDUAL_PROTOCOL_REQUIRED", "已接受组合必须使用原有限向量重验")
    capture = start_capture(session)
    basis = _basis(session, user, now)
    worlds: list[AssetModeWorld] = []
    expires: list[datetime] = [now + timedelta(minutes=15)]
    full = read_full_policy(session, user, original.full_policy_id, now)
    for mode in MODES:
        request = original.model_copy(update={"planning_mode": mode})
        preview = preview_full_asset_execution(
            session, user, request, now, maximum_turnover_cents=maximum_turnover_cents
        )
        portfolio = preview.portfolio
        children: list[AssetVectorChild] = []
        check = None
        reasons = list(preview.reasons)
        if portfolio is not None and preview.state == "READY_TO_REVIEW":
            for batch in portfolio.batches:
                facts = _facts(
                    session,
                    user,
                    PurchaseIntent(kind="purchase_asset", policy_id=request.mvp_asset_policy_id),
                    now,
                    basis,
                    effect=batch.command.effect,
                    action_id=None,
                )
                children.append(
                    AssetVectorChild(
                        batch_number=batch.batch_number,
                        facts=facts,
                        decision=classify_autonomy(facts),
                    )
                )
            try:
                context = load_execution_context(
                    session,
                    user,
                    portfolio.batches[0].command.effect,
                    now,
                    base_context=basis.context,
                )
                protection = preview.original_full_protection
                check = validate_remaining_combination(
                    portfolio,
                    context,
                    [batch.batch_number for batch in portfolio.batches],
                    now,
                    current_full_configuration_hash=full.current_version.content_hash,
                    full_sources=protection.full_policy_sources,
                    full_source_issues=[issue.code for issue in protection.source_issues],
                    full_inventory_complete=protection.projection.full_obligations_complete_within_registered_current_scope,
                )
            except (ValueError, PolicyLifecycleError):
                reasons.append("NATIVE_WHOLE_PARENT_NOT_PROVEN")
            expires.append(portfolio.expires_at)
        permission = {
            "full_policy_id": str(request.full_policy_id),
            "full_policy_version_id": str(request.expected_full_policy_version_id),
            "full_policy_content_hash": full.current_version.content_hash,
            "full_configuration": full.current_version.configuration,
            "mvp_asset_policy_id": str(request.mvp_asset_policy_id),
            "mvp_asset_policy_version_id": str(request.expected_mvp_policy_version_id),
            "mvp_configuration_hash": portfolio.original_mvp_configuration_hash
            if portfolio
            else None,
            "goal_id": str(request.goal_id),
            "goal_policy_version_id": str(request.expected_goal_policy_version_id),
            "whole_confirmation_required": True,
            "old_confirmation_inherited": False,
            "bank_authority": False,
            "cross_operation_atomicity": "NOT_AVAILABLE",
        }
        provisional = AssetModeWorld.model_construct(
            mode=mode,
            status="KNOWN",
            native_preview=preview,
            ordered_children=children,
            native_whole_check=check,
            permission_boundary=permission,
            reasons=reasons,
            vector_signature=None,
        )
        try:
            if portfolio is None or check is None or reasons:
                raise ValueError("Incomplete native original")
            provisional = provisional.model_copy(
                update={"vector_signature": vector_signature(provisional)}
            )
            world = AssetModeWorld.model_validate_json(provisional.model_dump_json())
        except (ValueError, AssertionError):
            world = AssetModeWorld(
                mode=mode,
                status="UNKNOWN",
                native_preview=preview,
                ordered_children=children,
                native_whole_check=check,
                permission_boundary=permission,
                reasons=sorted(set(reasons + ["FULL_NATIVE_EFFECT_AUTHORITY_OR_GUARD_INCOMPLETE"])),
                vector_signature=None,
            )
        worlds.append(world)
    manifest = {
        "protocol": PROTOCOL,
        "source_fingerprint_version": SOURCE_FINGERPRINT_VERSION,
        "user_id": str(user),
        "epoch_id": str(original.expected_epoch_id),
        "shanghai_day": now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        "complete_native_basis": copy.deepcopy(capture.inputs["autonomy_basis"]),
        "native_source_fingerprint": source_fingerprint(
            {"autonomy_basis": capture.inputs["autonomy_basis"]},
            list(capture.sources.values()),
            list(capture.policies.values()),
        ),
        "original_sources": [
            row.model_dump(mode="json")
            for row in sorted(capture.sources.values(), key=lambda item: item.id)
        ],
        "original_policies": [
            row.model_dump(mode="json")
            for row in sorted(capture.policies.values(), key=lambda item: item.id)
        ],
        "full_policy": full.model_dump(mode="json"),
        "catalogue": worlds[0].native_preview.original_full_planning.catalogue.model_dump(
            mode="json"
        ),
        "full_protection_sources": [
            row.model_dump(mode="json")
            for row in worlds[0].native_preview.original_full_protection.full_policy_sources
        ],
        "current_world_signatures": [world.vector_signature for world in worlds],
        "current_world_status": [world.status for world in worlds],
    }
    return evaluate_modes(worlds, selected), manifest, min(expires)


def all_receipts(session: Session, user: UUID) -> list[AssetModeRevision]:
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
        raise failure("QUESTION_CAPACITY_EXCEEDED", "完整资产问答原件达到明确容量")
    result: list[AssetModeRevision] = []
    verified: set[UUID] = set()
    for row in rows:
        epoch = UUID(row.content["epoch_id"])
        if epoch not in verified:
            if verify_audit_chain(session, user, epoch).status != "VALID":
                raise failure("QUESTION_AUDIT_INVALID", "完整资产问答原审计无法核验")
            verified.add(epoch)
        data = _valid(session, row, user, epoch)
        receipt = AssetModeRevision.model_validate_json(
            json.dumps(data["payload"]["result"], allow_nan=False)
        )
        if (
            receipt.user_id != user
            or receipt.epoch_id != epoch
            or receipt.original_request != data["payload"]["request"]
            or row.id != uuid5(epoch, f"{MARKER}:OPERATION:{receipt.command_id}")
        ):
            raise failure("QUESTION_ORIGINAL_INVALID", "原用户、epoch、请求或回执关联不一致")
        result.append(receipt)
    for session_id in {receipt.session_id for receipt in result}:
        chain(result, session_id)
    return result


def chain(receipts: list[AssetModeRevision], identity: UUID) -> list[AssetModeRevision]:
    found = sorted(
        (receipt for receipt in receipts if receipt.session_id == identity),
        key=lambda receipt: receipt.revision,
    )
    if not found:
        raise PolicyLifecycleError("NOT_FOUND", "原完整资产问答会话不存在", 404)
    if found[0].command_kind != "START" or len({receipt.command_id for receipt in found}) != len(
        found
    ):
        raise failure("QUESTION_ORIGINAL_INVALID", "问答原命令分母不一致")
    if found[0].session_id != uuid5(
        NAMESPACE, f"{found[0].user_id}:{found[0].epoch_id}:{found[0].command_id}"
    ):
        raise failure("QUESTION_ORIGINAL_INVALID", "原会话身份必须由首个本人命令派生")
    prior = None
    for number, receipt in enumerate(found, 1):
        if (
            receipt.revision != number
            or receipt.previous_receipt_hash != (prior.receipt_hash if prior else None)
            or prior is not None
            and prior.state == "CLOSED"
            or receipt.epoch_id != found[0].epoch_id
            or receipt.user_id != found[0].user_id
            or number > 1
            and receipt.command_kind == "START"
            or prior is not None
            and receipt.as_of < prior.as_of
            or prior is not None
            and receipt.original_request["body"].get("expected_revision") != prior.revision
            or prior is not None
            and receipt.original_request["kind"] == "FULL_ASSET_MODE_ANSWER"
            and receipt.original_request["body"].get("question_id") != str(prior.question_id)
        ):
            raise failure("QUESTION_ORIGINAL_INVALID", "完整问答版本链不一致")
        prior = receipt
    return found


def require_no_pending_vector(
    session: Session, user: UUID, epoch: UUID, except_id: UUID | None = None
) -> None:
    receipts = all_receipts(session, user)
    for identity in {receipt.session_id for receipt in receipts} - {except_id}:
        latest = chain(receipts, identity)[-1]
        if latest.epoch_id == epoch and latest.state == "PENDING_ANSWER":
            raise failure("ACTIVE_QUESTION_SESSION_EXISTS", "请先恢复已有完整资产待答问题")


def no_other_question(
    session: Session, user: UUID, epoch: UUID, now: datetime, identity: UUID
) -> None:
    require_no_pending_vector(session, user, epoch, identity)
    if any(
        head.state == "PENDING_ANSWER" and head.epoch_id == epoch
        for head in _heads(_all_records(session, user, now))
    ):
        raise failure("ACTIVE_QUESTION_SESSION_EXISTS", "请先恢复已有原MVP待答问题")


def make_receipt(
    user: UUID,
    epoch: UUID,
    identity: UUID,
    command: UUID,
    kind: Literal["START", "ANSWER", "REFRESH", "REBASE", "CLOSE"],
    original: dict[str, Any],
    evaluation: AssetModeEvaluation,
    manifest: dict[str, Any],
    expires: datetime,
    now: datetime,
    prior: AssetModeRevision | None,
    answer_applied: bool,
    actor: LocalActorPrincipal,
) -> AssetModeRevision:
    revision = prior.revision + 1 if prior else 1
    state = (
        "CLOSED"
        if kind == "CLOSE"
        else "UNKNOWN"
        if evaluation.status == "UNKNOWN"
        else "PENDING_ANSWER"
        if evaluation.should_ask
        else "READY_FOR_REVIEW"
    )
    fingerprint = mode_source_fingerprint(manifest)
    values: dict[str, Any] = dict(
        user_id=user,
        epoch_id=epoch,
        session_id=identity,
        revision=revision,
        previous_receipt_hash=prior.receipt_hash if prior else None,
        command_id=command,
        command_kind=kind,
        authenticated_actor=actor,
        original_request=original,
        as_of=now,
        valid_until=expires,
        source_manifest=manifest,
        source_fingerprint=fingerprint,
        evaluation=evaluation,
        state=state,
        question_id=uuid5(identity, f"asset_mode:{revision}:{fingerprint}")
        if state == "PENDING_ANSWER"
        else None,
        answer_applied=answer_applied,
    )
    value = AssetModeRevision.model_construct(**values, receipt_hash="0" * 64)
    values["receipt_hash"] = configuration_hash(
        value.model_dump(mode="json", exclude={"receipt_hash"})
    )
    return AssetModeRevision(**values)


def persist(session: Session, receipt: AssetModeRevision, now: datetime) -> None:
    # Existing audited OPERATION receipt; preference evidence is USER_DECLARED, never AUTH.
    store(
        session,
        receipt.user_id,
        "OPERATION",
        str(receipt.command_id),
        {"request": receipt.original_request, "result": receipt.model_dump(mode="json")},
        now,
    )


def mutate(
    engine: Engine,
    user: UUID,
    identity: UUID | None,
    body: ModeStartRequest | ModeAnswerRequest | ModeRefreshRequest | ModeCloseRequest,
    path: str,
    now: datetime,
    actor: LocalActorPrincipal,
) -> AssetModeRevision:
    require_question_user(actor, user, now)
    kind: Literal["START", "ANSWER", "REFRESH", "REBASE", "CLOSE"] = (
        "START"
        if isinstance(body, ModeStartRequest)
        else "ANSWER"
        if isinstance(body, ModeAnswerRequest)
        else "CLOSE"
        if isinstance(body, ModeCloseRequest)
        else "REFRESH"
    )
    original = {
        "kind": "FULL_ASSET_MODE_" + kind,
        "path": path,
        "body": body.model_dump(mode="json"),
    }
    with serial_user(engine, user):
        with owned_read(engine) as read:
            epoch = _epoch(read, user)
            if body.expected_epoch_id != epoch:
                raise failure("STALE_QUESTION_EPOCH", "偏好命令需要当前原epoch")
            receipts = all_receipts(read, user)
            repeated = [
                receipt for receipt in receipts if receipt.command_id == body.client_request_id
            ]
            if repeated:
                if (
                    len(repeated) != 1
                    or repeated[0].original_request != original
                    or identity is not None
                    and repeated[0].session_id != identity
                ):
                    raise failure("REQUEST_CONFLICT", "原问答键不能替换请求")
                return repeated[0]
            prior = None
            if isinstance(body, ModeStartRequest):
                identity = uuid5(NAMESPACE, f"{user}:{epoch}:{body.client_request_id}")
                policy_id = body.full_policy_id
            else:
                assert identity is not None
                history = chain(receipts, identity)
                prior = history[-1]
                if (
                    prior.state == "CLOSED"
                    or prior.epoch_id != epoch
                    or prior.revision != body.expected_revision
                    or (prior.revision >= 16 and kind != "CLOSE")
                ):
                    raise failure("STALE_QUESTION_REVISION", "原问题已经改变或关闭，请回读当前原件")
                policy_id = UUID(history[0].original_request["body"]["full_policy_id"])
            assert identity is not None
            if kind == "CLOSE":
                assert prior is not None
                evaluation, manifest, expires = (
                    prior.evaluation,
                    copy.deepcopy(prior.source_manifest),
                    prior.valid_until,
                )
                answer_applied = False
            else:
                native = resolve_native_request(
                    read, user, policy_id, epoch, "x05-mode:" + identity.hex, now
                )
                if (
                    isinstance(body, ModeStartRequest)
                    and native.expected_full_policy_version_id
                    != body.expected_full_policy_version_id
                ):
                    raise failure("FULL_ASSET_VERSION_CHANGED", "完整资产原版本已经改变")
                evaluation, manifest, expires = fresh_evaluation(read, user, native, now)
                changed = prior is not None and (
                    prior.source_fingerprint != mode_source_fingerprint(manifest)
                    or now >= prior.valid_until
                )
                selected = None if changed or prior is None else prior.evaluation.selected_mode
                answer_applied = False
                if isinstance(body, ModeAnswerRequest):
                    assert prior is not None
                    if prior.state != "PENDING_ANSWER" or body.question_id != prior.question_id:
                        raise failure("INVALID_PENDING_ANSWER", "回答必须绑定确切当前问题")
                    if not changed:
                        selected, answer_applied = body.mode, True
                if changed:
                    kind = "REBASE"
                evaluation = evaluate_modes(evaluation.worlds, selected)
            if (
                len(receipts) >= MAX_RECEIPTS
                or len(receipts) >= MAX_RECEIPTS - 1
                and kind != "CLOSE"
            ):
                raise failure("QUESTION_CAPACITY_EXCEEDED", "保留原关闭回执容量")
            if evaluation.should_ask and kind != "CLOSE":
                no_other_question(read, user, epoch, now, identity)
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
                answer_applied,
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
        "old_confirmation_inherited": False,
        "current_read_required": True,
        "receipt_is_current_authority": False,
        "requires_separate_whole_financial_confirmation": True,
    }
    if epoch != latest.epoch_id or latest.state == "CLOSED":
        return result
    try:
        policy = UUID(history[0].original_request["body"]["full_policy_id"])
        native = resolve_native_request(
            session, user, policy, epoch, "x05-mode:" + identity.hex, now
        )
        evaluation, manifest, _ = fresh_evaluation(session, user, native, now)
        fingerprint = mode_source_fingerprint(manifest)
        changed = fingerprint != latest.source_fingerprint or now >= latest.valid_until
        if changed:
            result["effective_state"] = "STALE_RECOMPUTATION_REQUIRED"
        else:
            evaluation = evaluate_modes(evaluation.worlds, latest.evaluation.selected_mode)
        result["current_source_fingerprint"] = fingerprint
        result["current_evaluation"] = evaluation.model_dump(mode="json")
    except PolicyLifecycleError as error:
        result["effective_state"] = "STALE_RECOMPUTATION_REQUIRED"
        result["current_read_error"] = {"code": error.code, "message": error.message}
    return result


def read_command(session: Session, user: UUID, epoch: UUID, command_id: UUID) -> dict[str, Any]:
    found = [
        receipt
        for receipt in all_receipts(session, user)
        if receipt.epoch_id == epoch and receipt.command_id == command_id
    ]
    if len(found) > 1:
        raise failure("QUESTION_ORIGINAL_INVALID", "原命令分母重复")
    return {
        "protocol": PROTOCOL,
        "status": "RECORDED" if found else "NOT_FOUND_NOT_FINAL",
        "epoch_id": str(epoch),
        "client_request_id": str(command_id),
        "original_request": found[0].original_request if found else None,
        "original_receipt": found[0].model_dump(mode="json") if found else None,
        "not_found_is_final": False,
        "replacement_allowed": False,
        "bank_authority": False,
        "old_confirmation_inherited": False,
        "current_read_required": True,
        "receipt_is_current_authority": False,
    }


def read_owned_view(
    session: Session,
    user: UUID,
    identity: UUID,
    now: datetime,
    owned: AcceptedAssetReadProof,
    evaluation: AssetResidualEvaluation,
    manifest: dict[str, Any],
    accepted_question_originals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Current two-original-world proof; no old answer or financial consent reuse."""
    history = chain(all_receipts(session, user), identity)
    latest = history[-1]
    result: dict[str, Any] = {
        "protocol": PROTOCOL,
        "current_revision": latest.model_dump(mode="json"),
        "effective_state": "STALE_RECOMPUTATION_REQUIRED",
        "current_source_fingerprint": mode_source_fingerprint(manifest),
        "current_evaluation": evaluation.model_dump(mode="json"),
        "bank_authority": False,
        "old_confirmation_inherited": False,
        "current_read_required": True,
        "receipt_is_current_authority": False,
        "requires_separate_whole_financial_confirmation": True,
        "owned_vector_recomputation": "NOT_PROVEN",
    }
    equivalent = (
        prefix_source_equivalent(owned, latest.source_manifest, manifest)
        if owned.settled_prefix
        else owned_source_equivalent(owned, latest.source_manifest, manifest)
    )
    if (
        latest.model_dump(mode="json") not in accepted_question_originals
        or latest.state != "READY_FOR_REVIEW"
        or not latest.as_of <= now < latest.valid_until
        or latest.epoch_id != owned.epoch_id
        or latest.user_id != user
        or evaluation.status != "STABLE"
        or evaluation.stable is not True
        or not equivalent
        or latest.evaluation.selected_mode is not None
    ):
        return result
    for world in evaluation.worlds:
        original = world.input.original_world
        if world.status != "KNOWN" or len(original.ordered_children) != len(owned.effects):
            return result
        for child, effect in zip(original.ordered_children, owned.effects, strict=True):
            fixed = child.model_copy(
                update={"facts": child.facts.model_copy(update={"effect": effect})}
            )
            if canonical_effect(child) != canonical_effect(fixed):
                return result
    result["effective_state"] = "READY_FOR_REVIEW"
    result["owned_vector_recomputation"] = "CURRENT_ORIGINAL_FINITE_MODES_AND_RECEIPT_PREFIX"
    return result
