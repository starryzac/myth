"""Actual-source dynamic goal producer and fresh phase/bank proof adapters.

No callback supplies money or permission. Only the original locked execution
pipeline may persist the returned original ALLOCATE_GOAL command.
"""

import inspect
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

from app.db.models import ActionPlan, AuditEpoch, DecisionRun, EvidenceItem
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import ACTION_PLAN_TYPES, execution_effect_hash
from app.domain.execution_types import BankCommand, CashUse, ConfirmationGrant, ExecutionEffect
from app.domain.full_dynamic_goal_execution import (
    ALGORITHM,
    MARKER,
    FullDynamicGoalInput,
    FullDynamicGoalPrepareRequest,
    FullDynamicGoalProof,
    build_full_dynamic_goal_effect,
    derive_full_dynamic_goal_proof,
    dynamic_goal_bank_key,
    native_income_original_matches,
    read_frozen_full_dynamic_goal_proof,
)
from app.domain.multi_goal_allocation import SourceReference
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionResponse, GoalIntent, PrepareActionRequest
from app.services.boundary import OWNERSHIP_SOURCE, _known_import
from app.services.dashboard_helpers import current_epoch_audit
from app.services.decision_recording import capture_evidence, current_capture
from app.services.decision_trace import get_decision_trace
from app.services.execution import get_action, prepare_action
from app.services.execution_context import load_execution_context
from app.services.execution_sources import read_execution_confirmation
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_goals import (
    _binding,
    _read_snapshot,
    read_full_goal_model,
    verify_full_goal_model_original,
)
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, _evidence, _now
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class FullDynamicGoalPreview(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    preview_only: Literal[True] = True
    user_id: UUID
    request: FullDynamicGoalPrepareRequest
    proof: FullDynamicGoalProof
    execution_pipeline: Literal["ORIGINAL_ALLOCATE_GOAL_REQUIRES_INSTALLED_TYPED_HOOK"] = (
        "ORIGINAL_ALLOCATE_GOAL_REQUIRES_INSTALLED_TYPED_HOOK"
    )
    limitations: list[str]


class FullDynamicGoalLookup(BoundaryModel):
    """Original-key recovery; recorded consent is never current authority."""

    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    current_authority: Literal[False] = False
    not_found_is_final: Literal[False] = False
    user_id: UUID
    idempotency_key: str
    status: Literal["NOT_FOUND", "RECORDED"]
    original_request: FullDynamicGoalPrepareRequest | None = None
    original_action_request: dict[str, Any] | None = None
    client_request_hash: str | None = None
    server_request_hash: str | None = None
    epoch_state: Literal["OPEN", "SEALED", "MISSING"] = "MISSING"
    historical: bool = False
    action: ActionResponse | None = None
    confirmation: ConfirmationGrant | None = None
    confirmation_status: Literal["ABSENT", "VERIFIED_AT_CONFIRMATION", "MISSING"] = "ABSENT"
    confirmation_verified_at: datetime | None = None
    confirmation_is_current_authority: Literal[False] = False


def _fail(code: str, message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def _same_engine(engine: Engine, session: Session) -> None:
    if session.get_bind().engine is not engine:
        raise _fail("DYNAMIC_GOAL_ENGINE_MISMATCH", "独立原件读取不能跨数据库引擎")


def read_full_dynamic_goal_inputs(
    session: Session,
    user_id: UUID,
    body: FullDynamicGoalPrepareRequest,
    now: datetime,
    *,
    own_effect: ExecutionEffect | None = None,
) -> FullDynamicGoalInput:
    """One actual RR/RO snapshot; all financial sources are re-read per call."""
    _read_snapshot(session)
    now = _now(now)
    body = FullDynamicGoalPrepareRequest.model_validate_json(body.model_dump_json())
    with session.no_autoflush:
        goal, policy, version, epoch = _binding(session, user_id, body.goal_id)
        model = read_full_goal_model(session, user_id, body.goal_id, now)
        if (
            model.status != "VERIFIED"
            or model.evidence_id is None
            or model.evidence_hash is None
            or model.policy_effective_status is None
            or (version.id, epoch.id, model.evidence_id, model.evidence_hash)
            != (
                body.expected_policy_version_id,
                body.expected_epoch_id,
                body.expected_model_evidence_id,
                body.expected_model_evidence_hash,
            )
        ):
            raise _fail("DYNAMIC_GOAL_CURRENT_BINDING_CHANGED", "当前目标版本、模型或轮次不匹配")
        original = session.get(EvidenceItem, model.evidence_id)
        if original is None or goal.account_id is None:
            raise _fail("DYNAMIC_GOAL_SOURCE_MISSING", "缺少原模型或目标账户")
        verify_full_goal_model_original(original, goal, policy, version, epoch, now)
        base, bank_matched, _ = load_verified_financial_context(session, user_id, now)
        income = read_income_state(session, user_id, now)
        statement = session.get(EvidenceItem, income.evidence_id)
        if statement is None or not native_income_original_matches(
            statement.content, income.ledger
        ):
            raise _fail("DYNAMIC_GOAL_V2_LEDGER_REQUIRED", "新消费者需要原生完整v2收入原件")
        ownership = base.sources.candidates(OWNERSHIP_SOURCE, "goal_id", body.goal_id)
        if len(ownership) != 1 or _known_import(ownership[0], base.sources) != income.ledger.as_of:
            raise _fail("DYNAMIC_GOAL_SNAPSHOT_MISMATCH", "收入与原目标归属必须来自同一原快照")
        base.sources.used.update({original.id, income.evidence_id})
        base.sources.used.update(row.bank_evidence_id for row in income.ledger.origins)
        audit = current_epoch_audit(session, user_id, [])
        if not bank_matched or not audit.complete or audit.status != "VALID":
            raise _fail("DYNAMIC_GOAL_SOURCE_NOT_VERIFIED", "银行投影或完整原审计未证明")
        _, issues, _ = finalize_financial_context(base, audit)
        if issues:
            raise _fail("DYNAMIC_GOAL_SOURCE_NOT_VERIFIED", ",".join(row.code for row in issues))
        if own_effect is None:
            cash = next(
                (row for row in base.snapshot.cash_accounts if row.account_type == "CASH"), None
            )
            if cash is None:
                raise _fail("DYNAMIC_GOAL_SOURCE_MISSING", "没有当前真实现金源账户")
            # Identity-only context probe: never evaluated, returned, captured as
            # an action, hashed into a request, reserved, or sent to the bank.
            probe = ExecutionEffect(
                operation_id=UUID(int=0),
                user_id=user_id,
                business_key="context-probe",
                action_type="ALLOCATE_GOAL",
                amount_cents=1,
                cash_uses=[CashUse(account_id=cash.account_id, amount_cents=1)],
                destination_account_id=goal.account_id,
                goal_id=goal.id,
                policy_id=policy.id,
                policy_version_id=version.id,
                policy_version_ids=[version.id],
                valid_from=now,
                expires_at=now + timedelta(minutes=15),
            )
        else:
            probe = own_effect
        context = load_execution_context(
            session,
            user_id,
            probe,
            now,
            own_action_id=own_effect.operation_id if own_effect is not None else None,
            base_context=base,
        )
        full = compute_full_annual_protection(session, user_id, now)
        if (full.user_id, full.as_of) != (user_id, now):
            raise _fail("DYNAMIC_GOAL_FULL_BINDING_MISMATCH", "完整版保护来源身份或时点不一致")
        identities = {
            *base.sources.used,
            original.id,
            income.evidence_id,
            *full.source_evidence_ids,
            *(key for row in context.lots for key in row.evidence_ids),
            *(key for row in context.versions for key in row.evidence_ids),
        }
        originals = _evidence(session, user_id, [str(key) for key in identities], now, lock=False)
        if not full.projection.full_obligations_complete_within_registered_current_scope:
            raise _fail("DYNAMIC_GOAL_FULL_INVENTORY_UNKNOWN", "完整已登记保护分母未核实")
        return FullDynamicGoalInput(
            context=context,
            request=body,
            epoch_id=epoch.id,
            model_original=original.content,
            model_evidence_id=original.id,
            model_evidence_hash=original.content_hash,
            income=income.ledger,
            income_evidence_id=income.evidence_id,
            income_evidence_hash=income.evidence_hash,
            source_refs=[
                SourceReference(
                    user_id=row.user_id, evidence_id=row.id, content_hash=row.content_hash
                )
                for row in sorted(originals, key=lambda row: str(row.id))
            ],
            protection_policies=full.full_policy_sources,
            protection_inventory_complete=True,
            protection_issues=[row.code for row in full.source_issues],
            own_effect=own_effect,
        )


def preview_full_dynamic_goal_execution(
    session: Session, user_id: UUID, body: FullDynamicGoalPrepareRequest, now: datetime
) -> FullDynamicGoalPreview:
    actual = read_full_dynamic_goal_inputs(session, user_id, body, now)
    return FullDynamicGoalPreview(
        user_id=user_id,
        request=body,
        proof=derive_full_dynamic_goal_proof(actual),
        limitations=[
            "只使用已确认原月度min/target/max与当前已核新收入，不新增资金许可。",
            "OVERDUE/PARTIAL及原最低额、期限、窗口否决保留；全多目标调度未实现。",
            "实际prepare/confirm/execute仍须原银行与完整保护网关，预览无副作用。",
        ],
    )


def _fresh(
    engine: Engine,
    user_id: UUID,
    body: FullDynamicGoalPrepareRequest,
    now: datetime,
    *,
    own_effect: ExecutionEffect | None = None,
) -> FullDynamicGoalInput:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as read:
                return read_full_dynamic_goal_inputs(
                    read, user_id, body, now, own_effect=own_effect
                )


def produce_full_dynamic_goal_effect(
    engine: Engine,
    locked_session: Session,
    user_id: UUID,
    action_id: UUID,
    body: FullDynamicGoalPrepareRequest,
    now: datetime,
) -> tuple[ExecutionEffect, FullDynamicGoalProof, dict[str, Any]]:
    """Caller holds original User lock and has started original decision capture."""
    _same_engine(engine, locked_session)
    data = _fresh(engine, user_id, body, now)
    try:
        effect, proof = build_full_dynamic_goal_effect(data, action_id)
    except (ValueError, TypeError) as error:
        raise _fail("DYNAMIC_GOAL_NOT_READY", str(error)) from error
    marker = {
        "protocol": ALGORITHM,
        "user_id": str(user_id),
        "epoch_id": str(data.epoch_id),
        "request": body.model_dump(mode="json"),
        "request_hash": configuration_hash(body.model_dump(mode="json")),
        "effect_hash": execution_effect_hash(effect),
        "original_proof": proof.model_dump(mode="json"),
    }
    capture = current_capture(locked_session)
    if capture is None:
        raise _fail("DYNAMIC_GOAL_CAPTURE_MISSING", "需要原事务中的真实PREPARE录制")
    capture.inputs[MARKER] = {
        "inputs": data.model_dump(mode="json"),
        "proof": proof.model_dump(mode="json"),
    }
    capture.algorithms[MARKER] = ALGORITHM
    capture_evidence(locked_session, user_id, [row.evidence_id for row in data.source_refs])
    return effect, proof, marker


def has_full_dynamic_goal_binding(session: Session, action: ActionPlan) -> bool:
    """Recognize the real retained PREPARE even if a live marker/key was stripped.

    Recognition is not verification or permission. Any suspicious association
    selects the strict gate; it never falls back to the legacy amount planner.
    """
    if MARKER in action.request or action.idempotency_key.startswith("action:full-dynamic-goal:"):
        return True
    run = session.get(DecisionRun, action.decision_run_id)
    if run is None:
        if action.action_type == ACTION_PLAN_TYPES["ALLOCATE_GOAL"]:
            raise _fail("DYNAMIC_GOAL_ORIGINAL_TRACE_MISSING", "目标动作不能缺失原主决策")
        return False
    if (
        run.user_id != action.user_id
        or configuration_hash(run.input_snapshot) != run.snapshot_hash
        or run.subject_action_plan_id != action.id
    ):
        raise _fail("DYNAMIC_GOAL_ORIGINAL_TRACE_INVALID", "原主决策身份、摘要或动作关联已改变")
    raw = run.input_snapshot.get("decision_trace", {})
    if not isinstance(raw, dict):
        raise _fail("DYNAMIC_GOAL_ORIGINAL_TRACE_INVALID", "原主决策轨迹不是完整对象")
    algorithms, inputs = raw.get("algorithm_versions", {}), raw.get("inputs", {})
    original_request = inputs.get("action_request", {}) if isinstance(inputs, dict) else {}
    planning = inputs.get("planning", {}) if isinstance(inputs, dict) else {}
    return (
        isinstance(algorithms, dict)
        and algorithms.get(MARKER) == ALGORITHM
        or isinstance(original_request, dict)
        and MARKER in original_request
        or isinstance(planning, dict)
        and MARKER in planning
    )


def read_original_dynamic_goal_request(
    session: Session, user_id: UUID, action: ActionPlan, command: BankCommand, now: datetime
) -> FullDynamicGoalPrepareRequest:
    """Verify original typed trace, Action projection and current immutable bytes."""
    try:
        actual = get_action(session, user_id, action.id, now)
        recorded = get_decision_trace(session, user_id, action.decision_run_id, now)
        trace = recorded.trace
        if (
            recorded.completeness != "COMPLETE"
            or recorded.audit_chain_status != "VALID"
            or trace is None
            or trace.phase != "PREPARE"
            or trace.action_id != action.id
            or trace.run_id != action.decision_run_id
            or trace.user_id != user_id
            or trace.algorithm_versions.get(MARKER) != ALGORITHM
        ):
            raise ValueError("Actual complete original PREPARE/audit is mandatory")
        original_request = trace.inputs["action_request"]
        marker = original_request[MARKER]
        body = FullDynamicGoalPrepareRequest.model_validate_json(json.dumps(marker["request"]))
        saved = FullDynamicGoalInput.model_validate_json(
            json.dumps(trace.inputs["planning"][MARKER]["inputs"])
        )
        original_proof = derive_full_dynamic_goal_proof(saved, command.effect)
        if read_frozen_full_dynamic_goal_proof(trace) != original_proof:
            raise ValueError("Frozen actual source/algorithm proof differs")
        frozen_command = BankCommand.model_validate_json(json.dumps(original_request["execution"]))
        effect = command.effect
        if (
            marker["protocol"] != ALGORITHM
            or marker["user_id"] != str(user_id)
            or marker["epoch_id"] != str(saved.epoch_id)
            or marker["request_hash"] != configuration_hash(body.model_dump(mode="json"))
            or marker["effect_hash"] != command.effect_hash
            or marker["original_proof"] != original_proof.model_dump(mode="json")
            or original_proof.status != "VERIFIED_RANGE"
            or saved.request != body
            or configuration_hash(original_request) != action.request_hash
            or original_request != action.request
            or frozen_command != command
            or actual.effect_hash != command.effect_hash
            or action.idempotency_key != dynamic_goal_bank_key(body.idempotency_key)
            or effect.operation_id != action.id
            or effect.user_id != user_id
            or action.user_id != user_id
            or action.action_type != ACTION_PLAN_TYPES[effect.action_type]
            or action.amount_cents != effect.amount_cents
            or action.goal_id != effect.goal_id
            or action.policy_version_id != effect.policy_version_id
            or action.source_account_id != effect.cash_uses[0].account_id
            or action.destination_account_id
            != (
                effect.destination_account_id
                if effect.destination_account_id != action.source_account_id
                else None
            )
            or action.expires_at != effect.expires_at
        ):
            raise ValueError("Original dynamic Action/command/request/proof projection differs")
        return body
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise _fail("DYNAMIC_GOAL_ORIGINAL_BINDING_INVALID", str(error)) from error


def recheck_full_dynamic_goal_proof(
    engine: Engine,
    locked_session: Session,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
    *,
    own_action_id: UUID | None = None,
) -> FullDynamicGoalProof:
    _same_engine(engine, locked_session)
    body = read_original_dynamic_goal_request(locked_session, action.user_id, action, command, now)
    if own_action_id not in {None, action.id}:
        raise _fail("DYNAMIC_GOAL_WRONG_OWN_RESERVATION", "不能借其他动作的预留")
    data = _fresh(
        engine,
        action.user_id,
        body,
        now,
        own_effect=command.effect if own_action_id is not None else None,
    )
    proof = derive_full_dynamic_goal_proof(data, command.effect)
    if proof.status != "VERIFIED_RANGE":
        raise _fail("DYNAMIC_GOAL_CURRENT_RANGE_REJECTED", ",".join(proof.reasons))
    capture = current_capture(locked_session)
    if capture is not None:
        capture.inputs[MARKER] = {
            "inputs": data.model_dump(mode="json"),
            "proof": proof.model_dump(mode="json"),
        }
        capture.algorithms[MARKER] = ALGORITHM
        capture_evidence(
            locked_session, action.user_id, [row.evidence_id for row in data.source_refs]
        )
    # Returned original amount/uses are never replaced with the fresh suggestion.
    return proof


def read_current_full_dynamic_goal_proof(
    session: Session,
    user_id: UUID,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
    *,
    own_action_id: UUID | None = None,
) -> FullDynamicGoalProof:
    """GET/readonly composition in the same actual RR/RO snapshot; no grant."""
    _read_snapshot(session)
    body = read_original_dynamic_goal_request(session, user_id, action, command, now)
    if own_action_id not in {None, action.id}:
        raise _fail("DYNAMIC_GOAL_WRONG_OWN_RESERVATION", "不能借其他动作的预留")
    inputs = read_full_dynamic_goal_inputs(
        session,
        user_id,
        body,
        now,
        own_effect=command.effect if own_action_id is not None else None,
    )
    return derive_full_dynamic_goal_proof(inputs, command.effect)


def verify_full_dynamic_goal_prepare_replay(
    session: Session,
    user_id: UUID,
    action: ActionPlan,
    body: FullDynamicGoalPrepareRequest,
    now: datetime,
) -> ActionResponse:
    command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
    original = read_original_dynamic_goal_request(session, user_id, action, command, now)
    if original.model_dump(mode="json") != body.model_dump(mode="json"):
        raise _fail("IDEMPOTENCY_CONFLICT", "同一动态目标键不能改变原完整身份请求")
    # Receipt replay is historical identity recovery, not a fresh planner/grant.
    return get_action(session, user_id, action.id, now)


def lookup_full_dynamic_goal_execution(
    session: Session, user_id: UUID, idempotency_key: str, now: datetime
) -> FullDynamicGoalLookup:
    """Read the exact retained action, original six-field body and consent.

    Consent is verified at its actual original observation, so a later expired
    window is not misrepresented as present execution permission. All original
    Action/PREPARE/receipt checks run first; missing current originals cannot be
    silently replaced with an alleged archived proof.
    """
    _read_snapshot(session)
    now = _now(now)
    if not 0 < len(idempotency_key) <= 120 or not idempotency_key.strip():
        raise _fail("INVALID_DYNAMIC_GOAL_KEY", "需要原非空且不超过120字符的动态目标键")
    action = session.scalar(
        select(ActionPlan).where(
            ActionPlan.user_id == user_id,
            ActionPlan.idempotency_key == dynamic_goal_bank_key(idempotency_key),
        )
    )
    empty = FullDynamicGoalLookup(
        user_id=user_id, idempotency_key=idempotency_key, status="NOT_FOUND"
    )
    if action is None:
        return empty
    try:
        command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        body = read_original_dynamic_goal_request(session, user_id, action, command, now)
        if body.idempotency_key != idempotency_key:
            raise ValueError("Original request key differs from the lookup key")
        original = get_action(session, user_id, action.id, now)
        epoch = session.get(AuditEpoch, body.expected_epoch_id)
        if epoch is None or epoch.user_id != user_id or epoch.status not in {"OPEN", "SEALED"}:
            raise ValueError("Original epoch belongs to another owner or is invalid")
        state: Literal["OPEN", "SEALED", "MISSING"] = "MISSING"
        state = "OPEN" if epoch.status == "OPEN" else "SEALED"
        evidence_ref = action.request.get("confirmation_evidence_id")
        evidence = session.get(EvidenceItem, UUID(evidence_ref)) if evidence_ref else None
        confirmation = None
        confirmation_status: Literal["ABSENT", "VERIFIED_AT_CONFIRMATION", "MISSING"] = "ABSENT"
        verified_at = None
        if evidence is not None:
            if evidence.observed_at > now:
                raise ValueError("Recorded confirmation is in the future")
            confirmation = read_execution_confirmation(
                session, command.effect, evidence.observed_at
            )
            if confirmation is None or confirmation.evidence_id != evidence.id:
                raise ValueError("Original confirmation does not verify")
            confirmation_status, verified_at = "VERIFIED_AT_CONFIRMATION", evidence.observed_at
        elif original.autonomy_level == "ASK_ONCE" and original.status in {
            "AUTHORIZED",
            "SUBMITTED",
            "UNKNOWN",
            "SUCCEEDED",
            "RECONCILED",
        }:
            confirmation_status = "MISSING"
        return empty.model_copy(
            update={
                "status": "RECORDED",
                "original_request": body,
                "original_action_request": action.request,
                "client_request_hash": configuration_hash(body.model_dump(mode="json")),
                "server_request_hash": action.request_hash,
                "epoch_state": state,
                "historical": state != "OPEN",
                "action": original,
                "confirmation": confirmation,
                "confirmation_status": confirmation_status,
                "confirmation_verified_at": verified_at,
            }
        )
    except (ValueError, TypeError, KeyError) as error:
        raise _fail("DYNAMIC_GOAL_ORIGINAL_BINDING_INVALID", str(error)) from error


def prepare_full_dynamic_goal_execution(
    engine: Engine, user_id: UUID, body: FullDynamicGoalPrepareRequest, now: datetime
) -> ActionResponse:
    """Use only the explicitly installed typed original pipeline extension."""
    if "_full_dynamic_goal_request" not in inspect.signature(prepare_action).parameters:
        raise _fail("DYNAMIC_GOAL_EXECUTION_NOT_IMPLEMENTED", "原执行管道尚未安装动态目标严格接缝")
    old = PrepareActionRequest(
        idempotency_key="full-dynamic-goal:" + body.idempotency_key,
        intent=GoalIntent(kind="allocate_goal", goal_id=body.goal_id),
    )
    installed = cast(Callable[..., ActionResponse], prepare_action)
    return installed(engine, user_id, old, now, _full_dynamic_goal_request=body)
