"""Actual original redemption adapters with fresh FULL scope and signed USER consent.

Original prepare/confirm/bank/projection state machines remain the only writers
of financial effects. Missing installed private guards fail before that pipeline.
"""

import inspect
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.models import ActionPlan, AuditEpoch, BankOperation, DecisionRun, EvidenceItem
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import build_trace
from app.domain.execution import ACTION_PLAN_TYPES, execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.full_policy_configuration import RecoveryPolicy
from app.domain.full_recovery_execution import (
    ALGORITHM,
    CONSENT_SOURCE,
    GUARDS_VERSION,
    MARKER,
    FullRecoveryConfirmation,
    FullRecoveryExecuteRequest,
    FullRecoveryExecutionInput,
    FullRecoveryExecutionProof,
    FullRecoveryPrepareRequest,
    FullRecoveryUserConsent,
    build_full_recovery_effect,
    derive_full_recovery_execution_proof,
    read_frozen_full_recovery_proof,
    recovery_bank_key,
    verify_full_recovery_consent,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.multi_goal_allocation import SourceReference
from app.domain.policy_configuration import configuration_hash
from app.services import execution, execution_bank
from app.services.action_contracts import (
    ActionResponse,
    ConfirmActionRequest,
    PrepareActionRequest,
    RedeemIntent,
)
from app.services.audit_chain import audit_read_scope
from app.services.decision_recording import capture_evidence, current_capture
from app.services.decision_trace import evidence_copy, get_decision_trace, record_trace
from app.services.execution import confirm_action, execute_action, get_action, prepare_action
from app.services.execution_planning import plan_execution_effect
from app.services.execution_sources import read_execution_confirmation
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.full_recovery_planning import read_full_recovery_planning
from app.services.policy_lifecycle import PolicyLifecycleError, _evidence, _now, _user
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class FullRecoveryExecutionPreview(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    preview_only: Literal[True] = True
    user_id: UUID
    original_request: FullRecoveryPrepareRequest
    proof: FullRecoveryExecutionProof
    execution_effect: ExecutionEffect | None = None
    limitations: list[str]


class FullRecoveryExecutionLookup(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    current_authority: Literal[False] = False
    not_found_is_final: Literal[False] = False
    user_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    original_request: FullRecoveryPrepareRequest | None = None
    original_action_request: dict[str, Any] | None = None
    client_request_hash: str | None = None
    server_request_hash: str | None = None
    action: ActionResponse | None = None
    epoch_state: Literal["OPEN", "SEALED", "MISSING"] = "MISSING"
    original_consent: FullRecoveryUserConsent | None = None
    consent_is_current_authority: Literal[False] = False


def _fail(code: str, message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def _principal(principal: LocalActorPrincipal, user_id: UUID, now: datetime) -> None:
    try:
        require_local_user(principal, user_id, now)
    except ValueError as error:
        raise _fail("FULL_RECOVERY_CURRENT_USER_REQUIRED", str(error)) from error


@contextmanager
def _reader(engine: Engine) -> Iterator[Session]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as read, audit_read_scope(read):
                yield read


def _same_engine(engine: Engine, session: Session) -> None:
    if session.get_bind().engine is not engine:
        raise _fail("FULL_RECOVERY_ENGINE_MISMATCH", "原写事务与独立原件读取不可跨引擎")


def require_installed_full_recovery_guards() -> None:
    if (
        "_full_recovery_request" not in inspect.signature(prepare_action).parameters
        or getattr(execution, "FULL_RECOVERY_GUARDS_VERSION", None) != GUARDS_VERSION
        or getattr(execution_bank, "FULL_RECOVERY_GUARDS_VERSION", None) != GUARDS_VERSION
    ):
        raise _fail("FULL_RECOVERY_EXECUTION_NOT_IMPLEMENTED", "原生产执行和银行严格接缝尚未安装")


def read_full_recovery_execution_inputs(
    session: Session,
    user_id: UUID,
    body: FullRecoveryPrepareRequest,
    action_id: UUID,
    now: datetime,
    *,
    original_effect: ExecutionEffect | None = None,
) -> FullRecoveryExecutionInput:
    """Actual current financial/bank/audit/catalogue originals in one clean RR/RO."""
    _read_snapshot(session)
    now = _now(now)
    body = FullRecoveryPrepareRequest.model_validate_json(body.model_dump_json())
    full = read_full_policy(session, user_id, body.policy_id, now)
    plan_read = read_full_recovery_planning(session, user_id, body.policy_id, now)
    plan = plan_read.plan
    if plan is None or plan_read.source_issues or plan_read.state != "COMPUTED":
        raise _fail("FULL_RECOVERY_SOURCE_UNKNOWN", "完整实际恢复原件未证明")
    if (plan.user_id, plan.policy_id, plan.policy_version_id, plan.as_of) != (
        user_id,
        body.policy_id,
        full.current_version.version_id,
        now,
    ):
        raise _fail("FULL_RECOVERY_SOURCE_BINDING_INVALID", "实际规划与当前声明身份不一致")
    candidates = [row for row in plan.candidates if row.position_id == body.position_id]
    if len(candidates) != 1:
        raise _fail("FULL_RECOVERY_POSITION_NOT_PROVEN", "需要完整分母中的唯一原持仓")
    epoch = session.get(AuditEpoch, full.epoch_id)
    if (
        epoch is None
        or epoch.user_id != user_id
        or epoch.status != "OPEN"
        or epoch.id != body.expected_epoch_id
    ):
        raise _fail("FULL_RECOVERY_EPOCH_CHANGED", "只能在原用户当前开放轮次准备或受理")
    effect = original_effect or plan_execution_effect(
        session,
        user_id,
        action_id,
        RedeemIntent(kind="redeem_asset", position_id=body.position_id),
        now,
    )
    if (effect.user_id, effect.operation_id) != (user_id, action_id):
        raise _fail("FULL_RECOVERY_EFFECT_IDENTITY_INVALID", "原经济后果身份不一致")
    full_protection = compute_full_annual_protection(session, user_id, now)
    if (full_protection.user_id, full_protection.as_of) != (user_id, now):
        raise _fail("FULL_RECOVERY_PROTECTION_BINDING_INVALID", "完整保护来源身份不一致")
    ids = {
        *plan_read.source_evidence_ids,
        *full.current_version.evidence_ids,
        *full_protection.source_evidence_ids,
    }
    originals = _evidence(session, user_id, [str(key) for key in ids], now, lock=False)
    return FullRecoveryExecutionInput(
        user_id=user_id,
        epoch_id=epoch.id,
        request=body,
        as_of=now,
        full_policy_id=full.policy_id,
        full_policy_version_id=full.current_version.version_id,
        full_configuration=RecoveryPolicy.model_validate(full.current_version.configuration),
        full_configuration_hash=full.current_version.content_hash,
        planning_confirmation_valid=full.planning_confirmation_valid,
        reference_validation=full.reference_validation,
        full_effective_status=full.effective_status,
        confirmed_at=full.current_version.confirmed_at,
        valid_from=full.current_version.valid_from,
        valid_until=full.current_version.valid_until,
        linked_asset_policy_id=plan.linked_asset_policy_id,
        linked_asset_policy_version_id=plan.linked_asset_policy_version_id,
        linked_asset_configuration_hash=plan.linked_asset_configuration_hash,
        planning_status=plan.status,
        planning_response_hash=plan_read.input_hash,
        planning_input_hash=plan.input_hash,
        candidate_position_ids=[row.position_id for row in plan.candidates],
        selected_position_ids=[row.position_id for row in plan.lossless_steps],
        candidate=candidates[0],
        deadline_at=plan.deadline_at,
        original_effect=effect,
        source_refs=[
            SourceReference(user_id=row.user_id, evidence_id=row.id, content_hash=row.content_hash)
            for row in sorted(originals, key=lambda row: str(row.id))
        ],
        protection_inventory_complete=(
            full_protection.projection.full_obligations_complete_within_registered_current_scope
        ),
        source_issues=[row.code for row in full_protection.source_issues],
    )


def preview_full_recovery_execution(
    session: Session, user_id: UUID, body: FullRecoveryPrepareRequest, now: datetime
) -> FullRecoveryExecutionPreview:
    # This deterministic identity is a read-only preview, never an ActionPlan or bank operation.
    identity = uuid5(user_id, recovery_bank_key(body.idempotency_key))
    data = read_full_recovery_execution_inputs(session, user_id, body, identity, now)
    proof = derive_full_recovery_execution_proof(data)
    effect = None
    if proof.status == "VERIFIED_SCOPE":
        effect, proof = build_full_recovery_effect(data)
    return FullRecoveryExecutionPreview(
        user_id=user_id,
        original_request=body,
        proof=proof,
        execution_effect=effect,
        limitations=[
            "PROOF_NARROWS_SCOPE_ONLY_ORIGINAL_MVP_AND_FULL_FINANCIAL_GATE_REQUIRED",
            "WHOLE_AUTHORIZED_POSITION_ZERO_COST_T0_T1_ONLY",
            "MATURE_REQUIRES_ORIGINAL_RECONCILIATION_LOSS_CAP_REMAINS_ZERO",
            "CURRENT_INSTANT_DEADLINE_CANNOT_BE_EXTENDED_FOR_CONFIRMATION",
            "NO_PARTIAL_OR_COMBINED_NEW_BANK_PROTOCOL",
        ],
    )


def _fresh(
    engine: Engine,
    user_id: UUID,
    body: FullRecoveryPrepareRequest,
    action_id: UUID,
    now: datetime,
    effect: ExecutionEffect | None = None,
) -> FullRecoveryExecutionInput:
    with _reader(engine) as read:
        return read_full_recovery_execution_inputs(
            read, user_id, body, action_id, now, original_effect=effect
        )


def _capture(
    session: Session, data: FullRecoveryExecutionInput, proof: FullRecoveryExecutionProof
) -> None:
    capture = current_capture(session)
    if capture is None:
        raise _fail("FULL_RECOVERY_CAPTURE_MISSING", "需要原写事务中的真实录制")
    capture.inputs[MARKER] = {
        "inputs": data.model_dump(mode="json"),
        "proof": proof.model_dump(mode="json"),
    }
    capture.algorithms[MARKER] = ALGORITHM
    capture_evidence(session, data.user_id, [row.evidence_id for row in data.source_refs])


def produce_full_recovery_effect(
    engine: Engine,
    locked_session: Session,
    user_id: UUID,
    action_id: UUID,
    body: FullRecoveryPrepareRequest,
    now: datetime,
) -> tuple[ExecutionEffect, FullRecoveryExecutionProof, dict[str, Any]]:
    """Original producer calls under its User lock/capture; never directly persists money."""
    _same_engine(engine, locked_session)
    data = _fresh(engine, user_id, body, action_id, now)
    try:
        effect, proof = build_full_recovery_effect(data)
    except (ValueError, TypeError) as error:
        raise _fail("FULL_RECOVERY_SCOPE_NOT_READY", str(error)) from error
    marker = {
        "protocol": ALGORITHM,
        "user_id": str(user_id),
        "epoch_id": str(data.epoch_id),
        "request": body.model_dump(mode="json"),
        "request_hash": configuration_hash(body.model_dump(mode="json")),
        "effect_hash": execution_effect_hash(effect),
        "original_proof": proof.model_dump(mode="json"),
    }
    _capture(locked_session, data, proof)
    return effect, proof, marker


def has_full_recovery_binding(session: Session, action: ActionPlan) -> bool:
    """Recognition never grants authority; stripped live metadata cannot select legacy fallback."""
    if MARKER in action.request or action.idempotency_key.startswith("action:full-recovery:"):
        return True
    run = session.get(DecisionRun, action.decision_run_id)
    if run is None:
        if action.action_type == ACTION_PLAN_TYPES["REDEEM_ASSET"]:
            raise _fail("FULL_RECOVERY_ORIGINAL_TRACE_MISSING", "赎回缺失原PREPARE不可降级")
        return False
    if (
        run.user_id != action.user_id
        or run.subject_action_plan_id != action.id
        or configuration_hash(run.input_snapshot) != run.snapshot_hash
    ):
        raise _fail("FULL_RECOVERY_ORIGINAL_TRACE_INVALID", "原PREPARE身份或摘要改变")
    raw = run.input_snapshot.get("decision_trace", {})
    if not isinstance(raw, dict):
        raise _fail("FULL_RECOVERY_ORIGINAL_TRACE_INVALID", "原轨迹形状错误")
    algorithms, inputs = raw.get("algorithm_versions", {}), raw.get("inputs", {})
    request = inputs.get("action_request", {}) if isinstance(inputs, dict) else {}
    planning = inputs.get("planning", {}) if isinstance(inputs, dict) else {}
    return bool(
        isinstance(algorithms, dict)
        and algorithms.get(MARKER) == ALGORITHM
        or isinstance(request, dict)
        and MARKER in request
        or isinstance(planning, dict)
        and MARKER in planning
    )


def read_original_full_recovery_request(
    session: Session, user_id: UUID, action: ActionPlan, command: BankCommand, now: datetime
) -> FullRecoveryPrepareRequest:
    try:
        actual = get_action(session, user_id, action.id, now)
        recorded = get_decision_trace(session, user_id, action.decision_run_id, now)
        trace = recorded.trace
        if (
            recorded.completeness != "COMPLETE"
            or recorded.audit_chain_status != "VALID"
            or trace is None
            or trace.phase != "PREPARE"
            or trace.user_id != user_id
            or trace.action_id != action.id
            or trace.run_id != action.decision_run_id
            or trace.algorithm_versions.get(MARKER) != ALGORITHM
        ):
            raise ValueError("Complete original typed PREPARE and audit are required")
        original = trace.inputs["action_request"]
        marker = original[MARKER]
        data = FullRecoveryExecutionInput.model_validate_json(
            json.dumps(trace.inputs["planning"][MARKER]["inputs"])
        )
        proof = derive_full_recovery_execution_proof(data, command.effect)
        if read_frozen_full_recovery_proof(trace) != proof:
            raise ValueError("Actual retained source denominator or historical proof differs")
        body = FullRecoveryPrepareRequest.model_validate_json(json.dumps(marker["request"]))
        frozen = BankCommand.model_validate_json(json.dumps(original["execution"]))
        effect = command.effect
        if (
            body != data.request
            or marker["protocol"] != ALGORITHM
            or marker["user_id"] != str(user_id)
            or marker["epoch_id"] != str(data.epoch_id)
            or marker["request_hash"] != configuration_hash(body.model_dump(mode="json"))
            or marker["effect_hash"] != command.effect_hash
            or marker["original_proof"] != proof.model_dump(mode="json")
            or trace.inputs["planning"][MARKER]["proof"] != proof.model_dump(mode="json")
            or proof.status != "VERIFIED_SCOPE"
            or frozen != command
            or original != action.request
            or configuration_hash(original) != action.request_hash
            or actual.effect_hash != command.effect_hash
            or action.idempotency_key != recovery_bank_key(body.idempotency_key)
            or effect.operation_id != action.id
            or effect.user_id != user_id
            or action.user_id != user_id
            or action.action_type != ACTION_PLAN_TYPES[effect.action_type]
            or action.amount_cents != effect.amount_cents
            or action.goal_id != effect.goal_id
            or action.policy_version_id != effect.policy_version_id
            or action.source_account_id != effect.position_account_id
            or action.destination_account_id != effect.destination_account_id
            or action.expires_at != effect.expires_at
        ):
            raise ValueError("Retained original request/command/proof or Action projection differs")
        return body
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise _fail("FULL_RECOVERY_ORIGINAL_BINDING_INVALID", str(error)) from error


def _consent(
    session: Session, action: ActionPlan, command: BankCommand, epoch_id: UUID, now: datetime
) -> FullRecoveryUserConsent | None:
    identity, run_id = (
        uuid5(action.id, "full-recovery-user-consent"),
        uuid5(action.id, "full-recovery-user-consent-trace"),
    )
    proof = session.get(EvidenceItem, identity)
    if proof is None:
        if session.get(DecisionRun, run_id) is not None:
            raise _fail("FULL_RECOVERY_CONSENT_INVALID", "原逐次确认证据缺失不可忽略")
        return None
    try:
        consent = FullRecoveryUserConsent.model_validate_json(json.dumps(proof.content))
        verify_full_recovery_consent(consent, command.effect, epoch_id)
        original = read_execution_confirmation(session, command.effect, consent.confirmed_at)
        recorded = get_decision_trace(session, action.user_id, run_id, now)
        trace = recorded.trace
        if (
            proof.user_id != action.user_id
            or proof.source_type != CONSENT_SOURCE
            or proof.source_ref != str(action.id)
            or proof.status != "VALID"
            or proof.evidence_level != "USER_CONFIRMED_ACTION"
            or proof.content_hash != configuration_hash(proof.content)
            or proof.created_at != consent.confirmed_at
            or proof.observed_at != consent.confirmed_at
            or proof.valid_from != consent.confirmed_at
            or proof.valid_to != command.effect.expires_at
            or consent.confirmed_at > now
            or original is None
            or original.evidence_id != consent.original_confirmation_evidence_id
            or recorded.completeness != "COMPLETE"
            or recorded.audit_chain_status != "VALID"
            or trace is None
            or trace.user_id != action.user_id
            or trace.action_id != action.id
            or trace.run_id != run_id
            or trace.parent_run_id != action.decision_run_id
            or trace.as_of != consent.confirmed_at
            or trace.phase != "EVALUATION"
            or trace.algorithm_versions.get(MARKER) != ALGORITHM
            or trace.outcome != {"full_recovery_user_consent": proof.content}
            or trace.inputs
            != {
                "expected_epoch_id": str(epoch_id),
                "reviewed_effect_hash": command.effect_hash,
                "accepted": True,
            }
            or trace.sources != [evidence_copy(proof)]
        ):
            raise ValueError("Actual retained consent metadata/trace/original confirmation differs")
        return consent
    except (ValueError, KeyError, TypeError) as error:
        raise _fail("FULL_RECOVERY_CONSENT_INVALID", str(error)) from error


def recheck_full_recovery_proof(
    engine: Engine,
    locked_session: Session,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
    *,
    own_action_id: UUID | None = None,
    require_user_consent: bool = False,
) -> FullRecoveryExecutionProof:
    _same_engine(engine, locked_session)
    body = read_original_full_recovery_request(locked_session, action.user_id, action, command, now)
    if own_action_id not in {None, action.id}:
        raise _fail("FULL_RECOVERY_WRONG_OWN_CLAIM", "不可借其他动作隐藏预留")
    data = _fresh(engine, action.user_id, body, action.id, now, command.effect)
    proof = derive_full_recovery_execution_proof(data, command.effect)
    if proof.status != "VERIFIED_SCOPE":
        raise _fail("FULL_RECOVERY_CURRENT_SCOPE_REJECTED", ",".join(proof.reasons))
    if (
        require_user_consent
        and _consent(locked_session, action, command, body.expected_epoch_id, now) is None
    ):
        raise _fail("FULL_RECOVERY_USER_CONSENT_REQUIRED", "首次受理须原签名USER逐次确认")
    if current_capture(locked_session) is not None:
        _capture(locked_session, data, proof)
    return proof


def verify_full_recovery_prepare_replay(
    session: Session,
    user_id: UUID,
    action: ActionPlan,
    body: FullRecoveryPrepareRequest,
    now: datetime,
) -> ActionResponse:
    command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
    original = read_original_full_recovery_request(session, user_id, action, command, now)
    if original != body:
        raise _fail("IDEMPOTENCY_CONFLICT", "原恢复键不能改变完整身份请求")
    return get_action(session, user_id, action.id, now)


def lookup_full_recovery_execution(
    session: Session, user_id: UUID, key: str, now: datetime
) -> FullRecoveryExecutionLookup:
    _read_snapshot(session)
    now = _now(now)
    try:
        bank_key = recovery_bank_key(key)
    except ValueError as error:
        raise _fail("INVALID_FULL_RECOVERY_KEY", str(error)) from error
    action = session.scalar(
        select(ActionPlan).where(
            ActionPlan.user_id == user_id, ActionPlan.idempotency_key == bank_key
        )
    )
    empty = FullRecoveryExecutionLookup(
        user_id=user_id, idempotency_key=key, status="NOT_FOUND_NOT_FINAL"
    )
    if action is None:
        return empty
    command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
    body = read_original_full_recovery_request(session, user_id, action, command, now)
    epoch = session.get(AuditEpoch, body.expected_epoch_id)
    if epoch is None or epoch.user_id != user_id or epoch.status not in {"OPEN", "SEALED"}:
        raise _fail("FULL_RECOVERY_ORIGINAL_EPOCH_INVALID", "原轮次身份未证明")
    return FullRecoveryExecutionLookup(
        user_id=user_id,
        idempotency_key=key,
        status="RECORDED",
        original_request=body,
        original_action_request=action.request,
        client_request_hash=configuration_hash(body.model_dump(mode="json")),
        server_request_hash=action.request_hash,
        action=get_action(session, user_id, action.id, now),
        epoch_state="OPEN" if epoch.status == "OPEN" else "SEALED",
        original_consent=_consent(session, action, command, body.expected_epoch_id, now),
    )


def prepare_full_recovery_execution(
    engine: Engine,
    user_id: UUID,
    body: FullRecoveryPrepareRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> ActionResponse:
    now = _now(now)
    body = FullRecoveryPrepareRequest.model_validate_json(body.model_dump_json())
    _principal(principal, user_id, now)
    require_installed_full_recovery_guards()
    request = PrepareActionRequest(
        idempotency_key="full-recovery:" + body.idempotency_key,
        intent=RedeemIntent(kind="redeem_asset", position_id=body.position_id),
    )
    installed = cast(Callable[..., ActionResponse], prepare_action)
    return installed(engine, user_id, request, now, _full_recovery_request=body)


def _original_action(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> tuple[ActionPlan, BankCommand, FullRecoveryPrepareRequest]:
    action = session.get(ActionPlan, action_id)
    if action is None or action.user_id != user_id:
        raise PolicyLifecycleError("NOT_FOUND", "原恢复动作不存在", 404)
    command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
    body = read_original_full_recovery_request(session, user_id, action, command, now)
    return action, command, body


def confirm_full_recovery_action(
    engine: Engine,
    user_id: UUID,
    action_id: UUID,
    body: FullRecoveryConfirmation,
    principal: LocalActorPrincipal,
    now: datetime,
) -> ActionResponse:
    now = _now(now)
    body = FullRecoveryConfirmation.model_validate_json(body.model_dump_json())
    _principal(principal, user_id, now)
    require_installed_full_recovery_guards()
    with audit_command_guard(engine, user_id):
        with _reader(engine) as read:
            action, command, request = _original_action(read, user_id, action_id, now)
            if (body.expected_epoch_id, body.reviewed_effect_hash) != (
                request.expected_epoch_id,
                command.effect_hash,
            ):
                raise _fail("FULL_RECOVERY_CONFIRMATION_MISMATCH", "必须明确确认原轮次及经济后果")
            if _consent(read, action, command, request.expected_epoch_id, now) is not None:
                return get_action(read, user_id, action_id, now)
            data = read_full_recovery_execution_inputs(
                read, user_id, request, action_id, now, original_effect=command.effect
            )
            proof = derive_full_recovery_execution_proof(data, command.effect)
            if proof.status != "VERIFIED_SCOPE":
                raise _fail("FULL_RECOVERY_CURRENT_SCOPE_REJECTED", ",".join(proof.reasons))
        result = confirm_action(
            engine,
            user_id,
            action_id,
            ConfirmActionRequest(effect_hash=body.reviewed_effect_hash, accepted=True),
            now,
        )
        with Session(engine) as writer, writer.begin():
            _user(writer, user_id)
            with _reader(engine) as read:
                action, command, request = _original_action(read, user_id, action_id, now)
                if _consent(read, action, command, request.expected_epoch_id, now) is not None:
                    return result
                current = read_full_recovery_execution_inputs(
                    read, user_id, request, action_id, now, original_effect=command.effect
                )
                checked = derive_full_recovery_execution_proof(current, command.effect)
                grant = read_execution_confirmation(read, command.effect, now)
                if checked.status != "VERIFIED_SCOPE" or grant is None:
                    raise _fail(
                        "FULL_RECOVERY_CONFIRMATION_NOT_FINAL", "原确认保留，新范围变化不受理"
                    )
            consent = FullRecoveryUserConsent(
                user_id=user_id,
                epoch_id=request.expected_epoch_id,
                action_id=action_id,
                original_effect_hash=command.effect_hash,
                original_confirmation_evidence_id=grant.evidence_id,
                principal_at_confirmation=principal,
                confirmed_at=now,
            )
            content = consent.model_dump(mode="json")
            original = EvidenceItem(
                id=uuid5(action_id, "full-recovery-user-consent"),
                user_id=user_id,
                evidence_level="USER_CONFIRMED_ACTION",
                source_type=CONSENT_SOURCE,
                source_ref=str(action_id),
                content=content,
                content_hash=configuration_hash(content),
                created_at=now,
                observed_at=now,
                valid_from=now,
                valid_to=command.effect.expires_at,
                status="VALID",
            )
            writer.add(original)
            writer.flush()
            trace = build_trace(
                run_id=uuid5(action_id, "full-recovery-user-consent-trace"),
                user_id=user_id,
                phase="EVALUATION",
                as_of=now,
                action_id=action_id,
                parent_run_id=action.decision_run_id,
                algorithm_versions={"trace": "decision-trace-v1", MARKER: ALGORITHM},
                inputs=body.model_dump(mode="json"),
                sources=[evidence_copy(original)],
                policies=[],
                outcome={"full_recovery_user_consent": content},
            )
            record_trace(writer, trace)
        return result


def execute_full_recovery_action(
    engine: Engine,
    user_id: UUID,
    action_id: UUID,
    body: FullRecoveryExecuteRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> ActionResponse:
    now = _now(now)
    body = FullRecoveryExecuteRequest.model_validate_json(body.model_dump_json())
    _principal(principal, user_id, now)
    require_installed_full_recovery_guards()
    with audit_command_guard(engine, user_id):
        with _reader(engine) as read:
            action, command, request = _original_action(read, user_id, action_id, now)
            if (body.expected_epoch_id, body.reviewed_effect_hash) != (
                request.expected_epoch_id,
                command.effect_hash,
            ):
                raise _fail("FULL_RECOVERY_EXECUTE_MISMATCH", "必须沿原动作身份恢复")
            consent = _consent(read, action, command, request.expected_epoch_id, now)
            if consent is None:
                raise _fail("FULL_RECOVERY_USER_CONSENT_REQUIRED", "原USER逐次同意尚未保存")
            operation = read.scalar(
                select(BankOperation).where(
                    BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
                )
            )
            if operation is None:
                data = read_full_recovery_execution_inputs(
                    read, user_id, request, action_id, now, original_effect=command.effect
                )
                proof = derive_full_recovery_execution_proof(data, command.effect)
                if proof.status != "VERIFIED_SCOPE":
                    raise _fail("FULL_RECOVERY_CURRENT_SCOPE_REJECTED", ",".join(proof.reasons))
            # An existing operation is reconciled under its immutable key by original execute.
        return execute_action(engine, user_id, action_id, now)
