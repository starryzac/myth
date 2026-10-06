"""Durable observations use original decision traces and their existing audit protocol."""

import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid5

from app.db.models import DecisionRun
from app.domain.boundary_action_events import ALGORITHM_VERSION, action_signature, event_kind
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import ConfirmationGrant, ExecutionContext, ExecutionEffect
from app.services.audit_chain import current_audit_epoch
from app.services.boundary_difference import _verified_input
from app.services.decision_trace import get_decision_trace, record_trace
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from pydantic import Field
from sqlalchemy.orm import Session

NAMESPACE = UUID("d8ec95a7-7940-43b5-b6b3-273b2c804b6a")


class BoundaryObservationRequest(BoundaryModel):
    before_run_id: UUID
    after_run_id: UUID
    expected_epoch_id: UUID


class BoundaryObservationResponse(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    action_scope: Literal["ORIGINAL_SINGLE_ACTION_COMPARISON"] = "ORIGINAL_SINGLE_ACTION_COMPARISON"
    global_action_set_complete: Literal[False] = False
    question_delivery: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    user_id: UUID
    epoch_id: UUID
    observation_run_id: UUID
    kind: Literal["BoundaryCrossed", "BoundaryObserved"]
    before_run_id: UUID
    after_run_id: UUID
    before_trace_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    after_trace_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    before_action_signature: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    after_action_signature: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    recorded_at: datetime
    idempotent_replay: bool


def _signature(
    session: Session, user_id: UUID, run_id: UUID, now: datetime
) -> tuple[str, DecisionTrace]:
    original = get_decision_trace(session, user_id, run_id, now)
    trace = original.trace
    if trace is None or trace.phase not in {"PREPARE", "CONFIRM", "RESERVE", "BANK_ACCEPT"}:
        raise PolicyLifecycleError("UNSUPPORTED_ACTION_OBSERVATION", "缺少完整原动作执行轨迹", 409)
    try:
        _verified_input(original, "execution_boundary")
        effect = ExecutionEffect.model_validate_json(json.dumps(trace.inputs["effect"]))
        context = ExecutionContext.model_validate_json(
            json.dumps(trace.inputs["execution_context"])
        )
        raw_confirmation = trace.inputs.get("confirmation")
        if "full_recovery_execution" in trace.algorithm_versions:
            from app.domain.full_recovery_execution_trace import verify_frozen_full_recovery_trace

            verify_frozen_full_recovery_trace(trace)
        confirmation = (
            ConfirmationGrant.model_validate_json(json.dumps(raw_confirmation))
            if raw_confirmation is not None
            else None
        )
        if "full_dynamic_goal_execution" in trace.algorithm_versions:
            from app.domain.full_dynamic_goal_execution import read_frozen_full_dynamic_goal_proof

            validation = revalidate_execution(
                effect,
                context,
                confirmation=confirmation,
                full_dynamic_goal_proof=read_frozen_full_dynamic_goal_proof(trace),
            )
        else:
            validation = revalidate_execution(effect, context, confirmation=confirmation)
        if (
            effect.user_id != user_id
            or context.snapshot.as_of != trace.as_of
            or validation.model_dump(mode="json") != trace.outcome["validation"]
            or validation.effect_hash != execution_effect_hash(effect)
        ):
            raise ValueError("The original economic validation cannot be reproduced")
        signature = action_signature(effect, validation, str(trace.outcome["autonomy_level"]))
    except (KeyError, TypeError, ValueError) as error:
        raise PolicyLifecycleError(
            "UNKNOWN_ACTION_OBSERVATION", "原经济效果、权限级别或风险无法重算", 409
        ) from error
    return signature, trace


def observe_boundary_actions(
    session: Session, user_id: UUID, body: BoundaryObservationRequest, now: datetime
) -> BoundaryObservationResponse:
    if session.new or session.dirty or session.deleted:
        raise PolicyLifecycleError("INVALID_WRITE_CONTEXT", "事件记录不能混入其他待写入变更", 409)
    now = _now(now)
    _user(session, user_id)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN" or epoch.id != body.expected_epoch_id:
        raise PolicyLifecycleError("STALE_DEMO_EPOCH", "边界事件所在轮次已变化", 409)
    # Request identifiers are the only client input; amounts/results/clock/authority
    # are never accepted. The stored trace supplies the immutable original receipt.
    identity = uuid5(NAMESPACE, f"{user_id}:{epoch.id}:{body.before_run_id}:{body.after_run_id}")
    prior = session.get(DecisionRun, identity)
    if prior is not None:
        original = get_decision_trace(session, user_id, identity, now)
        if original.completeness != "COMPLETE" or original.audit_chain_status != "VALID":
            raise PolicyLifecycleError("INVALID_EVENT_RECEIPT", "原事件记录无法验证", 409)
        if original.trace is None or original.trace.inputs.get(
            "original_request"
        ) != body.model_dump(mode="json"):
            raise PolicyLifecycleError("INVALID_EVENT_RECEIPT", "原事件请求已变化", 409)
        response = BoundaryObservationResponse.model_validate_json(
            json.dumps(original.trace.outcome["boundary_observation"])
        )
        return response.model_copy(update={"idempotent_replay": True})
    before_signature, before = _signature(session, user_id, body.before_run_id, now)
    after_signature, after = _signature(session, user_id, body.after_run_id, now)
    if before.as_of > after.as_of:
        raise PolicyLifecycleError("INVALID_EVENT_ORDER", "原后决策不能早于原前决策", 409)
    result = BoundaryObservationResponse(
        user_id=user_id,
        epoch_id=epoch.id,
        observation_run_id=identity,
        kind=event_kind(before_signature, after_signature),
        before_run_id=before.run_id,
        after_run_id=after.run_id,
        before_trace_hash=before.trace_hash,
        after_trace_hash=after.trace_hash,
        before_action_signature=before_signature,
        after_action_signature=after_signature,
        recorded_at=now,
        idempotent_replay=False,
    )
    trace = build_trace(
        run_id=identity,
        user_id=user_id,
        phase="EVALUATION",
        as_of=now,
        parent_run_id=after.run_id,
        action_id=None,
        algorithm_versions={"boundary_observation": ALGORITHM_VERSION},
        inputs={
            "original_request": body.model_dump(mode="json"),
            "before_original": before.model_dump(mode="json"),
            "after_original": after.model_dump(mode="json"),
        },
        sources=list({item.id: item for item in [*before.sources, *after.sources]}.values()),
        policies=list({item.id: item for item in [*before.policies, *after.policies]}.values()),
        constraints=[],
        candidates=[],
        outcome={
            "boundary_observation": result.model_dump(mode="json"),
            "decision_status": "COMPUTED",
        },
    )
    record_trace(session, trace)
    return result
