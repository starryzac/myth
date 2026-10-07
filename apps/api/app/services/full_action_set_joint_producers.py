"""Actual single RR/RO capture for finite joint-to-existing-effect mappings."""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.models import User
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_action_set_joint_producers import (
    ALGORITHM,
    JointActionSetInput,
    JointActionSetResult,
    JointOriginalCommand,
    derive_joint_producers,
    joint_action_ids,
    joint_goal_ids,
)
from app.domain.full_joint_goal_planning import captured_references
from app.domain.multi_goal_allocation import (
    MultiGoalAllocationInput,
    OptimizerVersion,
    SourceReference,
)
from app.services.audit_chain import current_audit_epoch
from app.services.autonomy_envelope import _snapshot
from app.services.decision_recording import (
    CAPTURE_KEY,
    DecisionCapture,
    capture_evidence,
    start_capture,
)
from app.services.decision_trace import get_decision_trace
from app.services.full_action_set_boundary import _verify_original_source_copies
from app.services.full_action_set_boundary_actual import (
    ActualActionSetCapture,
    capture_actual_action_set,
)
from app.services.full_action_set_release_producers import _protection_inputs
from app.services.full_joint_goal_planning import (
    FullJointPlanningResponse,
    full_joint_goal_planning,
)
from app.services.multi_goal_planning import joint_goal_planning
from app.services.policy_lifecycle import PolicyLifecycleError, _evidence, _now
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class JointActionSetCapture:
    inputs: JointActionSetInput
    result: JointActionSetResult
    originals: DecisionCapture


def capture_current_joint_producers(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    original_actual_capture: ActualActionSetCapture | None = None,
    optimizer_version: OptimizerVersion | None = None,
) -> JointActionSetCapture:
    _snapshot(session)
    now = _now(now)
    user = session.get(User, user_id)
    epoch = current_audit_epoch(session, user_id)
    if user is None or not user.is_simulated or epoch is None or epoch.status != "OPEN":
        raise PolicyLifecycleError(
            "CURRENT_JOINT_OWNER_EPOCH_MISSING", "需要当前模拟用户开放轮次", 409
        )
    actual = original_actual_capture or capture_actual_action_set(session, user_id, now)
    if (actual.inputs.base.user_id, actual.inputs.base.epoch_id, actual.inputs.base.as_of) != (
        user_id,
        epoch.id,
        now,
    ):
        raise PolicyLifecycleError(
            "JOINT_SAME_INVOCATION_OWNER_EPOCH_CLOCK_DIFFERS", "原捕获身份或时点不同", 409
        )
    previous = session.info.get(CAPTURE_KEY)
    capture = start_capture(session)
    capture.sources.update(actual.originals.sources)
    capture.policies.update(actual.originals.policies)
    planning: FullJointPlanningResponse | None = None
    original: MultiGoalAllocationInput | None = None
    protection = None
    refs: list[SourceReference] = []
    reasons: list[str] = []
    commands: list[JointOriginalCommand] = []
    ids = joint_action_ids(actual.inputs)
    try:
        with session.no_autoflush:
            for identity in ids:
                saved = JointOriginalCommand(action_id=identity)
                try:
                    row = next(
                        item
                        for item in actual.inputs.base.original_inventory["action_plans"]
                        if item["id"] == str(identity)
                    )
                    recorded = get_decision_trace(
                        session, user_id, UUID(row["decision_run_id"]), now
                    )
                    if (
                        recorded.trace is None
                        or recorded.completeness != "COMPLETE"
                        or recorded.audit_chain_status != "VALID"
                    ):
                        raise ValueError("JOINT_ORIGINAL_PREPARE_TRACE_OR_AUDIT_MISSING")
                    saved = saved.model_copy(update={"prepare_trace": recorded.trace})
                except (
                    PolicyLifecycleError,
                    ValueError,
                    TypeError,
                    KeyError,
                    StopIteration,
                ) as error:
                    saved = saved.model_copy(update={"missing_reasons": [str(error)]})
                commands.append(saved)
            if joint_goal_ids(actual.inputs):
                try:
                    values: list[MultiGoalAllocationInput] = []
                    original_response = joint_goal_planning(
                        session, user_id, now, capture_inputs=values.append,
                        optimizer_version=optimizer_version,
                    )
                    planning = full_joint_goal_planning(
                        session, user_id, now, optimizer_version=optimizer_version
                    )
                    if len(values) != 1 or planning.original_joint != original_response:
                        raise ValueError("JOINT_SAME_RRRO_ORIGINAL_CAPTURE_OR_RESPONSE_DIFFERS")
                    original = values[0]
                    protection, full = _protection_inputs(session, user_id, now)
                    if full != planning.full_protection:
                        raise ValueError("JOINT_SAME_INVOCATION_FULL_PROTECTION_DIFFERS")
                    source_ids = set(original_response.source_evidence_ids) | set(
                        full.source_evidence_ids
                    )
                    source_ids.update(item.evidence_id for item in captured_references(original))
                    rows = _evidence(
                        session,
                        user_id,
                        [str(identity) for identity in sorted(source_ids)],
                        now,
                        lock=False,
                    )
                    refs = [
                        SourceReference(
                            user_id=row.user_id, evidence_id=row.id, content_hash=row.content_hash
                        )
                        for row in sorted(rows, key=lambda row: row.id)
                    ]
                    capture_evidence(session, user_id, sorted(source_ids))
                except (
                    PolicyLifecycleError,
                    ValueError,
                    TypeError,
                    KeyError,
                    StopIteration,
                    OverflowError,
                ) as error:
                    reasons.append("JOINT_ACTUAL_CAPTURE_NOT_PROVEN:" + str(error))
            inputs = JointActionSetInput(
                original_actual_input=actual.inputs,
                expected_goal_ids=joint_goal_ids(actual.inputs),
                original_action_ids=ids,
                original_commands=commands,
                original_joint_input=original,
                actual_planning=planning,
                protection_inputs=protection,
                verified_source_refs=refs,
                source_reasons=reasons,
            )
            result = derive_joint_producers(inputs)
    finally:
        if previous is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous
            if isinstance(previous, DecisionCapture):
                previous.sources.update(capture.sources)
                previous.policies.update(capture.policies)
    return JointActionSetCapture(inputs, result, capture)


def read_current_joint_producers(
    session: Session, user_id: UUID, now: datetime
) -> JointActionSetResult:
    return capture_current_joint_producers(session, user_id, now).result


def verify_joint_source_copies(
    trace: DecisionTrace, inputs: JointActionSetInput, result: JointActionSetResult
) -> None:
    _verify_original_source_copies(
        trace, inputs.original_actual_input.base, result.joint_family_complete
    )
    copied = {row.id: row for row in trace.sources}
    raw = {
        UUID(row["id"]): row
        for row in inputs.original_actual_input.base.original_inventory["evidence_items"]
    }
    if len(copied) != len(trace.sources):
        raise ValueError("JOINT_DUPLICATE_TRACE_SOURCE_COPY")
    for ref in inputs.verified_source_refs:
        source, original = copied.get(ref.evidence_id), raw.get(ref.evidence_id)
        if (
            source is None
            or original is None
            or source.user_id != result.user_id
            or source.content_integrity != "VERIFIED"
            or source.content_hash != ref.content_hash
            or source.content != original["content"]
            or source.content_hash != original["content_hash"]
            or source.captured_content_hash != source.content_hash
            or source.evidence_level != original["evidence_level"]
            or source.source_type != original["source_type"]
            or source.source_ref != original["source_ref"]
            or source.status_at_decision != original["status"]
            or source.observed_at != datetime.fromisoformat(original["observed_at"])
            or source.valid_from != datetime.fromisoformat(original["valid_from"])
            or source.valid_to
            != (
                None
                if original.get("valid_to") is None
                else datetime.fromisoformat(original["valid_to"])
            )
        ):
            raise ValueError("JOINT_COMPLETE_ORIGINAL_TRACE_SOURCE_COPY_MISSING")


def verify_frozen_joint_producers(trace: DecisionTrace) -> JointActionSetResult:
    verify_trace(trace)
    if (
        trace.algorithm_versions.get("joint_action_producers") != ALGORITHM
        or trace.phase != "EVALUATION"
        or trace.action_id is not None
    ):
        raise ValueError("Not the exact joint producer algorithm")
    inputs = JointActionSetInput.model_validate_json(
        json.dumps(trace.inputs["joint_action_set_input"])
    )
    result = derive_joint_producers(inputs)
    verify_joint_source_copies(trace, inputs, result)
    if (trace.user_id, trace.as_of) != (result.user_id, result.as_of) or trace.outcome != {
        "joint_action_set_result": result.model_dump(mode="json")
    }:
        raise ValueError("Original joint result or owner/clock differs")
    return result
