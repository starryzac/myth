"""Current RR/RO joint batch previews and fixed-effect phase proof readers."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.full_joint_goal_execution import (
    ALGORITHM,
    MARKER,
    FullJointGoalExecutionInput,
    FullJointGoalPrepareRequest,
    FullJointGoalPreview,
    derive_joint_execution_plan,
)
from app.services.decision_recording import (
    CAPTURE_KEY,
    DecisionCapture,
    capture_evidence,
    start_capture,
)
from app.services.full_action_set_joint_producers import capture_current_joint_producers
from app.services.full_policy_lifecycle import read_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class FullJointGoalExecutionCapture:
    inputs: FullJointGoalExecutionInput
    preview: FullJointGoalPreview
    originals: DecisionCapture


def capture_full_joint_goal_execution(
    session: Session, user_id: UUID, body: FullJointGoalPrepareRequest, now: datetime
) -> FullJointGoalExecutionCapture:
    """Only the same current verified snapshot supplies money, sources and scope."""
    now = _now(now)
    body = FullJointGoalPrepareRequest.model_validate_json(body.model_dump_json())
    previous = session.info.get(CAPTURE_KEY)
    capture = start_capture(session)
    try:
        scope = read_full_policy(session, user_id, body.full_policy_id, now)
        if (scope.current_version.version_id, scope.epoch_id, scope.template_name) != (
            body.expected_full_policy_version_id,
            body.expected_epoch_id,
            "GoalAllocationPolicy",
        ):
            raise PolicyLifecycleError(
                "JOINT_CURRENT_SCOPE_CHANGED", "当前联合规划版本或轮次不同", 409
            )
        original = capture_current_joint_producers(session, user_id, now)
        capture.sources.update(original.originals.sources)
        capture.policies.update(original.originals.policies)
        scope_ids = {identity for identity in scope.current_version.evidence_ids}
        scope_ids.update(
            UUID(row["confirmation"]["confirmation_evidence_id"])
            for row in original.inputs.original_actual_input.base.original_inventory[
                "full_policy_versions"
            ]
            if row["policy_id"] == str(scope.policy_id)
        )
        capture_evidence(session, user_id, sorted(scope_ids, key=str))
        inputs = FullJointGoalExecutionInput(request=body, scope=scope, joint=original.inputs)
        preview = derive_joint_execution_plan(inputs)
        capture.inputs[MARKER] = inputs.model_dump(mode="json")
        capture.algorithms[MARKER] = ALGORITHM
    finally:
        if previous is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous
            if isinstance(previous, DecisionCapture):
                previous.sources.update(capture.sources)
                previous.policies.update(capture.policies)
    return FullJointGoalExecutionCapture(inputs, preview, capture)


def preview_full_joint_goal_execution(
    session: Session, user_id: UUID, body: FullJointGoalPrepareRequest, now: datetime
) -> FullJointGoalPreview:
    return capture_full_joint_goal_execution(session, user_id, body, now).preview
