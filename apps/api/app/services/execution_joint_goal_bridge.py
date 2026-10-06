"""Private bridge from a fixed joint child to the original financial pipeline."""

from datetime import datetime
from uuid import UUID

from app.db.models import ActionPlan
from app.domain.execution_types import ExecutionContext, ExecutionEffect
from app.domain.full_dynamic_goal_execution import FullDynamicGoalProof
from app.domain.full_joint_goal_execution import FullJointChildPrepareRequest, child_bank_key
from app.services.action_contracts import ActionResponse, PrepareActionRequest
from app.services.full_joint_goal_execution_store import (
    child_binding,
    error,
    read_joint_plan_original,
    verify_original_joint_child,
)
from sqlalchemy.orm import Session


def validate_joint_child_prepare(
    request: PrepareActionRequest, body: FullJointChildPrepareRequest
) -> FullJointChildPrepareRequest:
    body = FullJointChildPrepareRequest.model_validate_json(body.model_dump_json())
    if request.intent.kind != "allocate_goal" or request.idempotency_key != child_bank_key(
        body.plan_id, body.child_number
    ):
        raise error("JOINT_PRIVATE_ORIGINAL_INTENT_AND_KEY_REQUIRED")
    return body


def replay_joint_child_prepare(
    session: Session,
    user_id: UUID,
    action: ActionPlan,
    body: FullJointChildPrepareRequest,
    now: datetime,
) -> ActionResponse:
    _, plan, _, _ = read_joint_plan_original(session, user_id, body.plan_id, now)
    if body != child_binding(plan, body.child_number):
        raise error("JOINT_ORIGINAL_PRIVATE_CHILD_REPLAY_DIFFERS")
    verify_original_joint_child(session, plan, body.child_number, action, now)
    from app.services.execution import get_action

    return get_action(session, user_id, action.id, now)


def require_joint_goal_context(
    session: Session,
    effect: ExecutionEffect,
    context: ExecutionContext,
    proof: FullDynamicGoalProof,
) -> ExecutionContext:
    """Add mandatory ASK before the existing exact current-proof binding."""
    from app.services.execution_context import require_full_dynamic_goal_proof_context

    required = context.model_copy(update={"requires_confirmation": True})
    return require_full_dynamic_goal_proof_context(session, effect, required, proof)
