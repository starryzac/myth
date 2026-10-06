"""Three committed phases for deterministic, simulated economic actions."""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    PolicyVersion,
    SimulatedBankPosting,
    User,
)
from app.domain.execution import ACTION_PLAN_TYPES, execution_effect_hash, revalidate_execution
from app.domain.execution_types import BankCommand, ExecutionEffect, ExecutionValidation
from app.domain.full_dynamic_goal_execution import (
    MARKER as DYNAMIC_GOAL_MARKER,
)
from app.domain.full_dynamic_goal_execution import (
    FullDynamicGoalPrepareRequest,
    FullDynamicGoalProof,
    dynamic_goal_bank_key,
)
from app.domain.full_recovery_execution import (
    MARKER as RECOVERY_MARKER,
)
from app.domain.full_recovery_execution import FullRecoveryPrepareRequest, recovery_bank_key
from app.domain.income_ledger import IncomeOperation
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import (
    ActionReceiptResponse,
    ActionResponse,
    ConfirmActionRequest,
    PrepareActionRequest,
)
from app.services.execution_bank import has_full_asset_batch_binding, process_operation
from app.services.execution_context import load_execution_context
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_projection import project_execution, verify_execution_receipt
from app.services.execution_reservations import ResourceClaim, reserve_resources, resolve_resources
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.recovery_projection import _epochs
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

FULL_ASSET_BATCH_GUARDS_VERSION = "full-asset-batch-guards-v1"
FULL_RECOVERY_GUARDS_VERSION = "full-recovery-execution-guards-v1"


def prepare_action(
    engine: Engine,
    user_id: UUID,
    request: PrepareActionRequest,
    now: datetime,
    *,
    _experiment_candidate_selector: Callable[..., dict[str, Any] | None] | None = None,
    _experiment_context_guard: Callable[[Session, UUID], None] | None = None,
    _experiment_stage_capture: Callable[[str, UUID, dict[str, Any]], None] | None = None,
    _full_dynamic_goal_request: FullDynamicGoalPrepareRequest | None = None,
    _full_recovery_request: FullRecoveryPrepareRequest | None = None,
) -> ActionResponse:
    request = PrepareActionRequest.model_validate_json(request.model_dump_json())
    now = _clock(now)
    key = "action:" + configuration_hash({"key": request.idempotency_key})
    intent = request.intent.model_dump(mode="json")
    if _full_recovery_request is not None:
        if _full_dynamic_goal_request is not None:
            raise PolicyLifecycleError(
                "INVALID_FULL_RECOVERY_CONTEXT", "不能混合两个完整执行协议", 409
            )
        _full_recovery_request = FullRecoveryPrepareRequest.model_validate_json(
            _full_recovery_request.model_dump_json()
        )
        if intent != {
            "kind": "redeem_asset",
            "position_id": str(_full_recovery_request.position_id),
        }:
            raise PolicyLifecycleError(
                "FULL_RECOVERY_INTENT_MISMATCH", "原持仓意图必须精确一致", 409
            )
        key = recovery_bank_key(_full_recovery_request.idempotency_key)
    if _full_dynamic_goal_request is not None:
        _full_dynamic_goal_request = FullDynamicGoalPrepareRequest.model_validate_json(
            _full_dynamic_goal_request.model_dump_json()
        )
        if intent != {"kind": "allocate_goal", "goal_id": str(_full_dynamic_goal_request.goal_id)}:
            raise PolicyLifecycleError(
                "DYNAMIC_GOAL_INTENT_MISMATCH", "原目标意图必须精确一致", 409
            )
        key = dynamic_goal_bank_key(_full_dynamic_goal_request.idempotency_key)
    with Session(engine) as session, session.begin():
        _lock_user(session, user_id)
        experimental = any(
            value is not None
            for value in (
                _experiment_candidate_selector,
                _experiment_context_guard,
                _experiment_stage_capture,
            )
        )
        if experimental:
            if _full_dynamic_goal_request is not None or _full_recovery_request is not None:
                raise PolicyLifecycleError("INVALID_DYNAMIC_GOAL_CONTEXT", "不能混合实验接缝", 409)
            # Private Python simulation hook; never accepted by the HTTP request schema.
            if (
                engine.url.host != "127.0.0.1"
                or engine.url.port != 54329
                or re.fullmatch(r"bf_test_[0-9a-f]{32}", engine.url.database or "") is None
                or session.scalar(text("SELECT current_database()")) != engine.url.database
                or not callable(_experiment_context_guard)
            ):
                raise PolicyLifecycleError(
                    "EXPERIMENT_NOT_IMPLEMENTED", "需要原隔离模拟上下文", 409
                )
            _experiment_context_guard(session, user_id)
        existing = session.scalar(
            select(ActionPlan).where(
                ActionPlan.user_id == user_id, ActionPlan.idempotency_key == key
            )
        )
        if existing is not None:
            if _full_recovery_request is not None:
                from app.services.full_recovery_execution import verify_full_recovery_prepare_replay

                return verify_full_recovery_prepare_replay(
                    session, user_id, existing, _full_recovery_request, now
                )
            if _full_dynamic_goal_request is not None:
                from app.services.full_dynamic_goal_execution import (
                    verify_full_dynamic_goal_prepare_replay,
                )

                return verify_full_dynamic_goal_prepare_replay(
                    session, user_id, existing, _full_dynamic_goal_request, now
                )
            if existing.request.get("intent") != intent:
                raise PolicyLifecycleError(
                    "IDEMPOTENCY_CONFLICT", "同一幂等键不能改变原始意图", 409
                )
            if _experiment_stage_capture is not None:
                _experiment_stage_capture("user_lock", existing.id, {"idempotent_replay": True})
                _experiment_stage_capture(
                    "prepare_transaction",
                    existing.id,
                    {
                        "idempotent_replay": True,
                        "backend_pid": session.scalar(text("SELECT pg_backend_pid()")),
                        "transaction_id": session.scalar(text("SELECT txid_current()")),
                    },
                )
            # Do not invoke a candidate or rewrite a persisted economic effect on replay.
            return get_action(session, user_id, existing.id, now)
        action_id = uuid4()
        if _experiment_stage_capture is not None:
            _experiment_stage_capture("user_lock", action_id, {"actual_owner_lock": True})
            _experiment_stage_capture(
                "prepare_transaction",
                action_id,
                {
                    "backend_pid": session.scalar(text("SELECT pg_backend_pid()")),
                    "transaction_id": session.scalar(text("SELECT txid_current()")),
                },
            )
        from app.services.decision_recording import record_execution_trace, start_capture

        start_capture(session)
        dynamic_proof: FullDynamicGoalProof | None = None
        dynamic_marker: dict[str, Any] | None = None
        recovery_marker: dict[str, Any] | None = None
        candidate = None
        if _experiment_candidate_selector is not None:
            # None from a callable means no candidate, not a P-plan fallback.
            candidate = _experiment_candidate_selector(session, user_id, action_id, intent, now)
            if candidate is None:
                raise PolicyLifecycleError("EXPERIMENT_NO_CANDIDATE", "原候选为空，无资金动作", 409)
        elif _experiment_stage_capture is not None:
            _experiment_stage_capture(
                "candidate_before_new_effect", action_id, {"original_planner": True}
            )
        if _full_dynamic_goal_request is not None:
            from app.services.full_dynamic_goal_execution import produce_full_dynamic_goal_effect

            effect, dynamic_proof, dynamic_marker = produce_full_dynamic_goal_effect(
                engine, session, user_id, action_id, _full_dynamic_goal_request, now
            )
        elif _full_recovery_request is not None:
            from app.services.full_recovery_execution import produce_full_recovery_effect

            effect, _, recovery_marker = produce_full_recovery_effect(
                engine, session, user_id, action_id, _full_recovery_request, now
            )
        elif candidate is None:
            # Preserve the original five-position default and fault-injection signature.
            effect = _build_effect(session, user_id, action_id, request, now)
        else:
            effect = _build_effect(
                session, user_id, action_id, request, now, _experiment_candidate=candidate
            )
        context = load_execution_context(session, user_id, effect, now)
        if recovery_marker is not None:
            from app.services.execution_context import require_full_recovery_confirmation_context

            context = require_full_recovery_confirmation_context(session, context)
        if _experiment_stage_capture is not None:
            _experiment_stage_capture(
                "source_context", action_id, {"original_context": context.model_dump(mode="json")}
            )
        validation = (
            revalidate_execution(effect, context)
            if dynamic_proof is None
            else revalidate_execution(effect, context, full_dynamic_goal_proof=dynamic_proof)
        )
        from app.services.full_execution_protection import (
            enforce_full_execution_protection,
            has_full_protection_policies,
        )

        if has_full_protection_policies(session, user_id):
            enforce_full_execution_protection(engine, user_id, effect, context, validation, now)
        if _experiment_stage_capture is not None:
            _experiment_stage_capture(
                "execution_revalidation",
                action_id,
                {"original_validation": validation.model_dump(mode="json")},
            )
        if validation.status not in {"READY", "CONFIRMATION_REQUIRED"}:
            raise PolicyLifecycleError(
                "EXECUTION_NOT_READY", "动作不能安全准备：" + ",".join(validation.reasons), 409
            )
        digest = execution_effect_hash(effect)
        if _experiment_stage_capture is not None:
            _experiment_stage_capture(
                "economic_hash",
                action_id,
                {"original_effect": effect.model_dump(mode="json"), "original_effect_hash": digest},
            )
        command = BankCommand(effect=effect, effect_hash=digest)
        payload = {
            "intent": intent,
            "execution": command.model_dump(mode="json"),
            "confirmation_evidence_id": str(uuid5(action_id, "confirmation:" + digest)),
            "prepared_validation": validation.model_dump(mode="json"),
        }
        if dynamic_marker is not None:
            payload[DYNAMIC_GOAL_MARKER] = dynamic_marker
        if recovery_marker is not None:
            payload[RECOVERY_MARKER] = recovery_marker
        initial = {"intent": intent, "effect_hash": digest}
        run = DecisionRun(
            id=uuid5(action_id, "decision"),
            user_id=user_id,
            created_at=now,
            idempotency_key=key,
            trigger_type="ACTION_PREPARE",
            algorithm_version="economic-effect-revalidation-v1",
            as_of=now,
            input_snapshot=initial,
            snapshot_hash=configuration_hash(initial),
            policy_version_ids=[str(v) for v in effect.policy_version_ids],
            evidence_ids=[],
            result={"action_id": str(action_id)},
            status="PENDING",
            completed_at=None,
        )
        session.add(run)
        session.flush()
        source = effect.cash_uses[0].account_id if effect.cash_uses else effect.position_account_id
        if source is None:
            raise PolicyLifecycleError("INVALID_EXECUTION_SOURCE", "动作缺少原始账户", 409)
        action = ActionPlan(
            id=action_id,
            user_id=user_id,
            created_at=now,
            decision_run_id=run.id,
            policy_version_id=effect.policy_version_id,
            source_account_id=source,
            destination_account_id=effect.destination_account_id
            if effect.destination_account_id != source
            else None,
            goal_id=effect.goal_id,
            product_id=effect.product_id,
            position_id=effect.position_id if effect.action_type == "REDEEM_ASSET" else None,
            action_type=ACTION_PLAN_TYPES[effect.action_type],
            amount_cents=effect.amount_cents,
            autonomy_level="ASK_ONCE"
            if validation.status == "CONFIRMATION_REQUIRED"
            else "AUTO_EXECUTE",
            status="PLANNED",
            idempotency_key=key,
            request=payload,
            request_hash=configuration_hash(payload),
            authorized_at=None,
            expires_at=effect.expires_at,
        )
        session.add(action)
        session.flush()
        if dynamic_marker is None and recovery_marker is None:
            record_execution_trace(
                session,
                effect,
                validation,
                now,
                "PREPARE",
                parent_run_id=None,
                autonomy_level=action.autonomy_level,
                existing_run=run,
                intent=intent,
            )
        if _experiment_stage_capture is not None:
            from app.services.audit_chain import row_copy

            _experiment_stage_capture(
                "decision_trace",
                action_id,
                {"original_run": row_copy(run), "original_action": row_copy(action)},
            )
        _epochs(session, user_id, now, action.id)
        if effect.income_uses:
            from app.services.income_ledger import read_income_state

            income = read_income_state(session, user_id, now)
            payload = {
                **payload,
                "income_evidence": {"id": str(income.evidence_id), "hash": income.evidence_hash},
            }
            action.request, action.request_hash = payload, configuration_hash(payload)
        if dynamic_marker is not None or recovery_marker is not None:
            record_execution_trace(
                session,
                effect,
                validation,
                now,
                "PREPARE",
                parent_run_id=None,
                autonomy_level=action.autonomy_level,
                existing_run=run,
                intent=intent,
                action_request=payload,
            )
        refresh_execution_exposure(session, user_id, now, action.id)
        session.flush()
        from app.services.audit_recording import record_action_created

        record_action_created(session, action, now)
        from app.services.command_delivery import enqueue_action_in_transaction

        enqueue_action_in_transaction(session, action, now)
        return get_action(session, user_id, action.id, now)


def _build_effect(
    session: Session,
    user_id: UUID,
    action_id: UUID,
    request: PrepareActionRequest,
    now: datetime,
    *,
    _experiment_candidate: dict[str, Any] | None = None,
) -> ExecutionEffect:
    from app.services.execution_planning import plan_execution_effect

    if _experiment_candidate is None:
        return plan_execution_effect(session, user_id, action_id, request.intent, now)
    return plan_execution_effect(
        session,
        user_id,
        action_id,
        request.intent,
        now,
        _experiment_candidate=_experiment_candidate,
    )


def get_action(session: Session, user_id: UUID, action_id: UUID, now: datetime) -> ActionResponse:
    action = session.get(ActionPlan, action_id)
    if action is None or action.user_id != user_id or "execution" not in action.request:
        raise PolicyLifecycleError("NOT_FOUND", "动作不存在", 404)
    if configuration_hash(action.request) != action.request_hash:
        raise PolicyLifecycleError("INVALID_EXECUTION_SOURCE", "原始动作载荷校验失败", 409)
    command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
    operation = session.scalar(
        select(BankOperation).where(
            BankOperation.user_id == user_id, BankOperation.action_plan_id == action.id
        )
    )
    rows = list(
        session.scalars(
            select(ActionReceipt).where(
                ActionReceipt.user_id == user_id, ActionReceipt.action_plan_id == action.id
            )
        )
    )
    if len(rows) > 1:
        raise PolicyLifecycleError("BANK_RECONCILIATION_REQUIRED", "动作回执存在冲突", 409)
    receipt = None
    if rows:
        row = rows[0]
        if operation is None:
            raise PolicyLifecycleError("BANK_RECONCILIATION_REQUIRED", "回执缺少独立银行操作", 409)
        verify_execution_receipt(session, operation, row, _clock(now))
        receipt = ActionReceiptResponse(
            receipt_id=row.id,
            action_id=action.id,
            bank_operation_id=UUID(row.response["bank_operation_id"]),
            status=row.status,
            executed_cents=row.executed_cents,
            fee_cents=row.fee_cents,
            loss_cents=row.loss_cents,
            posting_ids=[UUID(key) for key in row.response["posting_ids"]],
            occurred_at=row.occurred_at,
            reconciled_at=row.reconciled_at,
        )
    elif action.status in {"SUCCEEDED", "RECONCILED"}:
        raise PolicyLifecycleError("BANK_RECONCILIATION_REQUIRED", "已结算动作缺少原回执", 409)
    return ActionResponse(
        user_id=user_id,
        action_id=action.id,
        decision_run_id=action.decision_run_id,
        status=action.status,
        autonomy_level=action.autonomy_level,
        effect=command.effect,
        effect_hash=command.effect_hash,
        prepared_at=action.created_at,
        as_of=_clock(now),
        prepared_validation=ExecutionValidation.model_validate_json(
            json.dumps(action.request["prepared_validation"])
        ),
        bank_status=operation.status if operation is not None else None,
        receipt=receipt,
    )


def _clock(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    return now.astimezone(UTC)


def confirm_action(
    engine: Engine, user_id: UUID, action_id: UUID, request: ConfirmActionRequest, now: datetime
) -> ActionResponse:
    from app.services.execution_sources import read_execution_confirmation

    request = ConfirmActionRequest.model_validate_json(request.model_dump_json())
    now = _clock(now)
    with Session(engine) as session, session.begin():
        _lock_user(session, user_id)
        previous = get_action(session, user_id, action_id, now)
        if request.effect_hash != previous.effect_hash:
            raise PolicyLifecycleError("CONFIRMATION_MISMATCH", "确认与原经济后果不一致", 409)
        action = session.get(ActionPlan, action_id)
        assert action is not None
        from app.services.audit_recording import audit_subject_data

        before_status = action.status
        before_data = audit_subject_data(action)
        existing = read_execution_confirmation(session, previous.effect, now)
        if existing is not None:
            return previous
        if action.status not in {"PLANNED", "AUTHORIZED"}:
            raise PolicyLifecycleError("INVALID_ACTION_STATE", "当前动作不可追加确认", 409)
        effect = previous.effect
        if not effect.valid_from <= now < effect.expires_at:
            raise PolicyLifecycleError("EXPIRED_EFFECT", "原经济后果已过期，需要重新准备", 409)
        identity = uuid5(action.id, "confirmation:" + previous.effect_hash)
        content = {
            "simulation": True,
            "user_id": str(user_id),
            "action_id": str(action.id),
            "effect_hash": previous.effect_hash,
            "accepted": True,
            "confirmed_at": now.isoformat(),
            "valid_until": effect.expires_at.isoformat(),
        }
        session.add(
            EvidenceItem(
                id=identity,
                user_id=user_id,
                created_at=now,
                evidence_level="USER_CONFIRMED_ACTION",
                source_type="USER_ACTION_CONFIRMATION",
                source_ref=str(action.id),
                content=content,
                content_hash=configuration_hash(content),
                status="VALID",
                observed_at=now,
                valid_from=now,
                valid_to=effect.expires_at,
            )
        )
        session.flush()
        from app.services.decision_recording import record_execution_trace, start_capture

        start_capture(session)
        context = load_execution_context(session, user_id, effect, now)
        confirmation = read_execution_confirmation(session, effect, now)
        from app.services.full_dynamic_goal_execution import (
            has_full_dynamic_goal_binding,
            recheck_full_dynamic_goal_proof,
        )

        dynamic_proof = None
        from app.services.full_recovery_execution import (
            has_full_recovery_binding,
            recheck_full_recovery_proof,
        )

        recovery_bound = effect.action_type == "REDEEM_ASSET" and has_full_recovery_binding(
            session, action
        )
        if recovery_bound:
            from app.services.execution_context import require_full_recovery_confirmation_context

            recheck_full_recovery_proof(
                engine,
                session,
                action,
                BankCommand(effect=effect, effect_hash=previous.effect_hash),
                now,
            )
            context = require_full_recovery_confirmation_context(session, context)
        if effect.action_type == "ALLOCATE_GOAL" and has_full_dynamic_goal_binding(session, action):
            dynamic_proof = recheck_full_dynamic_goal_proof(
                engine,
                session,
                action,
                BankCommand(effect=effect, effect_hash=previous.effect_hash),
                now,
            )
        result = (
            revalidate_execution(effect, context, confirmation=confirmation)
            if dynamic_proof is None
            else revalidate_execution(
                effect, context, confirmation=confirmation, full_dynamic_goal_proof=dynamic_proof
            )
        )
        if result.status != "READY":
            raise PolicyLifecycleError(
                "EXECUTION_NOT_READY", "确认时重验未通过：" + ",".join(result.reasons), 409
            )
        from app.services.full_execution_protection import (
            enforce_full_execution_protection,
            has_full_protection_policies,
        )

        if has_full_protection_policies(session, user_id):
            enforce_full_execution_protection(engine, user_id, effect, context, result, now)
        record_execution_trace(
            session,
            effect,
            result,
            now,
            "CONFIRM",
            parent_run_id=action.decision_run_id,
            autonomy_level=action.autonomy_level,
            confirmation=confirmation,
            action_request=action.request if dynamic_proof is not None or recovery_bound else None,
        )
        action.status, action.authorized_at = "AUTHORIZED", now
        _epochs(session, user_id, now, action.id)
        refresh_execution_exposure(session, user_id, now, action.id)
        session.flush()
        from app.services.audit_recording import record_action_transition

        record_action_transition(
            session,
            action,
            before_status,
            now,
            reason_code="USER_ACTION_CONFIRMED",
            cause_ref=str(identity),
            details={
                "confirmation_evidence_id": str(identity),
                "effect_hash": previous.effect_hash,
            },
            before_data=before_data,
        )
        return get_action(session, user_id, action.id, now)


def execute_action(engine: Engine, user_id: UUID, action_id: UUID, now: datetime) -> ActionResponse:
    from app.db.audit_guard import audit_command_guard

    with audit_command_guard(engine, user_id):
        return _execute_action(engine, user_id, action_id, now)


def _execute_action(
    engine: Engine, user_id: UUID, action_id: UUID, now: datetime
) -> ActionResponse:
    from app.services.execution_observations import observe_checkpoint, observed_begin
    from app.services.execution_sources import read_execution_confirmation
    from app.services.income_ledger import reserve_income_for_action

    now = _clock(now)
    with (
        Session(engine) as session,
        observed_begin(session, engine, user_id, action_id, now, "APPLICATION_RESERVATION"),
    ):
        _lock_user(session, user_id)
        observe_checkpoint(
            session, user_id, action_id, "APPLICATION_RESERVATION", "USER_LOCK_RETURNED", now
        )
        current = get_action(session, user_id, action_id, now)
        if current.status in {"SUCCEEDED", "RECONCILED"}:
            return current
        action = session.get(ActionPlan, action_id)
        assert action is not None
        from app.services.audit_recording import audit_subject_data

        before_status = action.status
        before_data = audit_subject_data(action)
        operation = session.scalar(
            select(BankOperation).where(BankOperation.action_plan_id == action.id)
        )
        if operation is None:
            if action.status not in {"PLANNED", "AUTHORIZED", "SUBMITTED", "UNKNOWN"}:
                raise PolicyLifecycleError("INVALID_ACTION_STATE", "当前动作不可执行", 409)
            has_claims = (
                session.scalar(
                    select(ActionResourceReservation.id).where(
                        ActionResourceReservation.action_plan_id == action.id,
                        ActionResourceReservation.status == "RESERVED",
                    )
                )
                is not None
            )
            from app.services.decision_recording import record_execution_trace, start_capture

            start_capture(session)
            context = load_execution_context(
                session,
                user_id,
                current.effect,
                now,
                own_action_id=action.id if has_claims else None,
            )
            confirmation = read_execution_confirmation(session, current.effect, now)
            from app.services.full_dynamic_goal_execution import (
                has_full_dynamic_goal_binding,
                recheck_full_dynamic_goal_proof,
            )

            dynamic_proof = None
            from app.services.full_recovery_execution import (
                has_full_recovery_binding,
                recheck_full_recovery_proof,
            )

            recovery_bound = (
                current.effect.action_type == "REDEEM_ASSET"
                and has_full_recovery_binding(session, action)
            )
            if recovery_bound:
                from app.services.execution_context import (
                    require_full_recovery_confirmation_context,
                )

                recheck_full_recovery_proof(
                    engine,
                    session,
                    action,
                    BankCommand(effect=current.effect, effect_hash=current.effect_hash),
                    now,
                    own_action_id=action.id if has_claims else None,
                    require_user_consent=True,
                )
                context = require_full_recovery_confirmation_context(session, context)
            if current.effect.action_type == "ALLOCATE_GOAL" and has_full_dynamic_goal_binding(
                session, action
            ):
                dynamic_proof = recheck_full_dynamic_goal_proof(
                    engine,
                    session,
                    action,
                    BankCommand(effect=current.effect, effect_hash=current.effect_hash),
                    now,
                    own_action_id=action.id if has_claims else None,
                )
            validation = (
                revalidate_execution(current.effect, context, confirmation=confirmation)
                if dynamic_proof is None
                else revalidate_execution(
                    current.effect,
                    context,
                    confirmation=confirmation,
                    full_dynamic_goal_proof=dynamic_proof,
                )
            )
            if validation.status != "READY":
                raise PolicyLifecycleError(
                    "EXECUTION_NOT_READY", "执行重验未通过：" + ",".join(validation.reasons), 409
                )
            from app.services.full_execution_protection import (
                enforce_full_execution_protection,
                has_full_protection_policies,
            )

            if has_full_protection_policies(session, user_id):
                enforce_full_execution_protection(
                    engine, user_id, current.effect, context, validation, now
                )
            if has_full_asset_batch_binding(session, action):
                from app.services.full_asset_execution_dispatch import (
                    enforce_full_asset_batch_phase1,
                )

                enforce_full_asset_batch_phase1(
                    engine,
                    session,
                    action,
                    BankCommand.model_validate_json(json.dumps(action.request["execution"])),
                    now,
                )
            reserve_run = record_execution_trace(
                session,
                current.effect,
                validation,
                now,
                "RESERVE",
                parent_run_id=action.decision_run_id,
                autonomy_level=action.autonomy_level,
                confirmation=confirmation,
                action_request=action.request
                if dynamic_proof is not None or recovery_bound
                else None,
            )
            reserve_resources(
                session, user_id, action.id, _claims(session, current.effect, context), now
            )
            if current.effect.income_uses:
                role: IncomeOperation = (
                    "TRANSFER_INTERNAL"
                    if current.effect.action_type == "TRANSFER_INTERNAL"
                    else "ALLOCATE_GOAL"
                    if current.effect.action_type == "ALLOCATE_GOAL"
                    else "SPEND"
                )
                reserve_income_for_action(
                    session,
                    user_id,
                    action.id,
                    current.effect.income_uses,
                    role,
                    now,
                    destination_account_id=current.effect.destination_account_id
                    if role == "TRANSFER_INTERNAL"
                    else None,
                )
            action.status, action.authorized_at = "SUBMITTED", action.authorized_at or now
            _epochs(session, user_id, now, action.id)
            refresh_execution_exposure(session, user_id, now, action.id)
            session.flush()
            from app.services.audit_recording import record_action_transition

            claim_ids = list(
                session.scalars(
                    select(ActionResourceReservation.id)
                    .where(
                        ActionResourceReservation.user_id == user_id,
                        ActionResourceReservation.action_plan_id == action.id,
                        ActionResourceReservation.status == "RESERVED",
                    )
                    .order_by(ActionResourceReservation.id)
                )
            )
            record_action_transition(
                session,
                action,
                before_status,
                now,
                reason_code="RESOURCES_RESERVED",
                cause_ref=str(reserve_run.id),
                details={
                    "reservation_run_id": str(reserve_run.id),
                    "resource_claim_ids": [str(identity) for identity in claim_ids],
                    "income_reserved": bool(current.effect.income_uses),
                },
                before_data=before_data,
            )
    # Phase 1 above is committed. Bank mutations commit independently.
    try:
        process_operation(engine, user_id, action_id, now)
    except PolicyLifecycleError:
        _record_bank_refusal(engine, user_id, action_id, now)
        raise
    except Exception:
        _mark_unknown(engine, user_id, action_id, now)
        raise
    try:
        with (
            Session(engine) as session,
            observed_begin(session, engine, user_id, action_id, now, "APPLICATION_PROJECTION"),
        ):
            _lock_user(session, user_id)
            observe_checkpoint(
                session, user_id, action_id, "APPLICATION_PROJECTION", "USER_LOCK_RETURNED", now
            )
            operation = session.scalar(
                select(BankOperation).where(
                    BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
                )
            )
            if operation is None:
                raise PolicyLifecycleError("BANK_RECONCILIATION_REQUIRED", "银行操作仍未知", 409)
            receipt = project_execution(session, operation, now)
            if receipt is None:
                refresh_execution_exposure(session, user_id, now, action_id)
            return get_action(session, user_id, action_id, now)
    except Exception:
        _mark_unknown(engine, user_id, action_id, now)
        raise


def _claims(session: Session, effect: ExecutionEffect, context: object) -> list[ResourceClaim]:
    from app.domain.execution_types import ExecutionContext

    assert isinstance(context, ExecutionContext)
    cash = {row.account_id: row.balance_cents for row in context.snapshot.cash_accounts}
    claims = [
        ResourceClaim(
            resource_kind="CASH",
            resource_key=str(use.account_id),
            amount_cents=use.amount_cents,
            capacity_cents=cash[use.account_id],
        )
        for use in effect.cash_uses
    ]
    claims.append(
        ResourceClaim(
            resource_kind="BUSINESS",
            resource_key=effect.business_key,
            amount_cents=1,
            capacity_cents=1,
        )
    )
    for use in effect.income_uses:
        lot = next((row for row in context.lots if row.fragment_id == use.fragment_id), None)
        if lot is None:
            raise PolicyLifecycleError("INVALID_EXECUTION_SOURCE", "缺少被绑定的收入片段", 409)
        claims.append(
            ResourceClaim(
                resource_kind="INCOME",
                resource_key=str(use.fragment_id),
                amount_cents=use.amount_cents,
                capacity_cents=lot.available_cents,
            )
        )
    if effect.action_type == "REDEEM_ASSET":
        claims.append(
            ResourceClaim(
                resource_kind="POSITION",
                resource_key=str(effect.position_id),
                amount_cents=effect.amount_cents,
                capacity_cents=effect.amount_cents,
            )
        )
    if effect.action_type == "PURCHASE_ASSET":
        version = session.get(PolicyVersion, effect.policy_version_id)
        assert version is not None and context.exposure is not None
        cap = max(
            0,
            version.configuration["max_auto_managed_cents"]
            - context.exposure.managed_principal_cents,
        )
        claims.append(
            ResourceClaim(
                resource_kind="MANAGED",
                resource_key="goal:" + str(effect.goal_id) if effect.goal_id else "general",
                amount_cents=effect.amount_cents,
                capacity_cents=cap,
            )
        )
        if effect.goal_id is not None:
            goal = next(row for row in context.snapshot.goals if row.goal_id == effect.goal_id)
            claims.append(
                ResourceClaim(
                    resource_kind="GOAL_CASH",
                    resource_key=str(effect.goal_id),
                    amount_cents=effect.amount_cents,
                    capacity_cents=goal.cash_owned_cents,
                )
            )
    return claims


def _mark_unknown(engine: Engine, user_id: UUID, action_id: UUID, now: datetime) -> None:
    """Retain the same action and all claims after a lost response or projection failure."""
    with Session(engine) as session, session.begin():
        _lock_user(session, user_id)
        action = session.get(ActionPlan, action_id)
        if (
            action is None
            or action.user_id != user_id
            or action.status in {"SUCCEEDED", "RECONCILED"}
        ):
            return
        from app.services.audit_recording import audit_subject_data

        before_status = action.status
        before_data = audit_subject_data(action)
        action.status = "UNKNOWN"
        refresh_execution_exposure(session, user_id, now, action_id)
        session.flush()
        from app.services.audit_recording import record_action_transition

        record_action_transition(
            session,
            action,
            before_status,
            now,
            reason_code="BANK_OR_PROJECTION_RESULT_UNKNOWN",
            cause_ref=str(action_id),
            before_data=before_data,
        )


def _record_bank_refusal(engine: Engine, user_id: UUID, action_id: UUID, now: datetime) -> None:
    """Only a deterministic local refusal plus locked independent absence can free claims."""
    from app.services.income_ledger import release_income_for_action

    with Session(engine) as session, session.begin():
        _lock_user(session, user_id)
        current = get_action(session, user_id, action_id, now)
        action = session.get(ActionPlan, action_id)
        assert action is not None
        if action.status in {"SUCCEEDED", "RECONCILED"}:
            return
        from app.services.audit_recording import audit_subject_data

        before_status = action.status
        before_data = audit_subject_data(action)
        reserved_ids = list(
            session.scalars(
                select(ActionResourceReservation.id).where(
                    ActionResourceReservation.user_id == user_id,
                    ActionResourceReservation.action_plan_id == action_id,
                    ActionResourceReservation.status == "RESERVED",
                )
            )
        )
        operation = session.scalar(
            select(BankOperation).where(
                BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
            )
        )
        legs = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.user_id == user_id,
                    SimulatedBankPosting.operation_id == action_id,
                    SimulatedBankPosting.entry_kind != "OPENING",
                )
            )
        )
        absent = operation is None and not legs
        rejected = (
            operation is not None
            and operation.status == "REJECTED"
            and not legs
            and operation.request == action.request["execution"]
            and operation.request_hash == configuration_hash(operation.request)
        )
        if not (absent or rejected):
            action.status = "UNKNOWN"
        else:
            action.status = "INVALIDATED"
            if current.effect.income_uses:
                release_income_for_action(
                    session, user_id, action_id, now, confirmed_no_effect=True
                )
            resolve_resources(session, user_id, action_id, "RELEASED", now)
            _epochs(session, user_id, now, action_id)
        refresh_execution_exposure(session, user_id, now, action_id)
        session.flush()
        from app.services.audit_recording import record_action_transition

        record_action_transition(
            session,
            action,
            before_status,
            now,
            reason_code="CONFIRMED_NO_EFFECT" if absent or rejected else "BANK_RESULT_UNKNOWN",
            cause_ref=str(action_id),
            details={
                "no_effect_status": "ABSENT" if absent else "REJECTED" if rejected else "UNKNOWN",
                "released_claim_ids": sorted(str(identity) for identity in reserved_ids)
                if absent or rejected
                else [],
            },
            before_data=before_data,
        )


def _lock_user(session: Session, user_id: UUID) -> User:
    from app.db.audit_guard import transaction_gate

    transaction_gate(session, user_id)
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
    return user
