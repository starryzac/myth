"""Concrete fresh native RESERVE and first BANK gates for delegated SYSTEM v2."""

from datetime import datetime
from uuid import UUID

from app.db.models import ActionPlan, DecisionRun
from app.domain.execution_types import ExecutionContext, ExecutionEffect
from app.domain.full_dynamic_goal_execution import (
    FullDynamicGoalProof,
    derive_full_dynamic_goal_proof,
)
from app.domain.standing_income_goal_slice import SingleGoalIncomeSliceOriginal
from app.domain.zhiyu_standing_native import (
    MARKER,
    AssetOriginal,
    DelegatedNativeInput,
    DelegatedNativeProof,
    JointGoalOriginal,
    PaymentOriginal,
    SingleGoalOriginal,
    derive_delegated_native_proof,
    original_effects,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_standing_dispatcher import current_read, original_run
from app.services.zhiyu_standing_recording import (
    binding,
    read_native_proof,
    record_standing_phase_trace,
    verify_action_binding,
)
from app.services.zhiyu_standing_snapshot import Phase, capture_native_snapshot
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, object_session


def has_standing_binding(action: ActionPlan) -> bool:
    if MARKER not in action.request:
        session = object_session(action)
        run = session.get(DecisionRun, action.decision_run_id) if session is not None else None
        if run is not None and (
            run.algorithm_version
            in {"zhiyu-standing-delegated-native-v2", "zhiyu-standing-income-goal-native-v1"}
            or MARKER in run.input_snapshot.get("decision_trace", {}).get("algorithm_versions", {})
        ):
            raise ValueError("Original delegated action marker was stripped")
        return False
    binding(action)  # A malformed claimed marker is never a legacy fallback.
    return True


def revalidate_standing_phase(
    engine: Engine, action: ActionPlan, now: datetime, phase: Phase
) -> tuple[
    ExecutionContext, FullDynamicGoalProof | None, DelegatedNativeInput, DelegatedNativeProof
]:
    marker = binding(action)
    with current_read(engine) as read:
        frozen, _ = read_native_proof(
            read,
            action.user_id,
            UUID(marker["accepted_proof_evidence_id"]),
            marker["accepted_proof_evidence_hash"],
        )
        number = verify_action_binding(action, frozen, marker)
        run = original_run(read, action.user_id, frozen.grant.grant_id, marker["event_key"])
        if (
            run.state not in {"ACCEPTED", "UNKNOWN"}
            or run.whole_plan_hash != marker["whole_plan_hash"]
            or run.original_action_ids
            != [row.operation_id for row in original_effects(frozen.native)]
            or run.original_bank_keys[number - 1] != action.idempotency_key
        ):
            raise ValueError("Exact accepted complete journal inventory differs")
        value = capture_native_snapshot(
            read, action.user_id, frozen.grant.grant_id, marker["event_key"], now, phase, frozen
        )
        proof = derive_delegated_native_proof(value)
        if proof.status != "READY" or value.settled_prefix != number - 1:
            raise PolicyLifecycleError(
                "STANDING_CURRENT_NATIVE_GATE_BLOCKED",
                "新受理必须当前完整SYSTEM委托原生门通过",
                409,
            )
    native = value.native
    dynamic = None
    if isinstance(native, SingleGoalIncomeSliceOriginal):
        context = native.current.context
    elif isinstance(native, SingleGoalOriginal):
        context = native.current.context
        dynamic = derive_full_dynamic_goal_proof(native.current, native.effect)
    elif isinstance(native, JointGoalOriginal):
        context = native.current_inputs[0].context
        dynamic = derive_full_dynamic_goal_proof(
            native.current_inputs[0], original_effects(native)[number - 1]
        )
    elif isinstance(native, AssetOriginal):
        context = native.context
    elif isinstance(native, PaymentOriginal):
        context = native.context
    else:
        raise ValueError("Explicit delegated native family is required")
    return context, dynamic, value, proof


def enforce_standing_first_bank(
    session: Session, user_id: UUID, effect: ExecutionEffect, now: datetime
) -> FullDynamicGoalProof | None:
    action = session.get(ActionPlan, effect.operation_id)
    if action is None or action.user_id != user_id or not has_standing_binding(action):
        raise ValueError("First bank gate requires the actual original delegated action")
    bind = session.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    _, dynamic, value, proof = revalidate_standing_phase(engine, action, now, "BANK_ACCEPT")
    record_standing_phase_trace(session, action, value, proof)
    return dynamic
