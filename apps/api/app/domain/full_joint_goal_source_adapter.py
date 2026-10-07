"""Explicit native V4 originals and independent audited parent/trace bindings.

No database, current-policy reader, financial facts, permission or bank calls.
All native mathematical and current execution gates remain in their consumers.
"""

import copy
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid5

from app.domain.full_joint_goal_archive import (
    MAX_DECODED_BYTES,
    MAX_DEPTH,
    MAX_NODES,
    JointArchiveError,
)
from app.domain.full_joint_goal_archive_protocol import RecordKind, _canonical
from app.domain.full_joint_goal_source_archive import (
    PHASES,
    TRACE_PROTOCOL,
    SharedJointOriginal,
    binding,
    decode_shared_joint,
    encode_shared_joint,
    encode_shared_trace_parts,
    pool_sources,
    reference,
    trace_reference,
)
from app.domain.immutable_joint_archive_scope import decode_complete_original
from app.domain.policy_configuration import configuration_hash

if TYPE_CHECKING:
    from app.domain.decision_trace_types import DecisionTrace

ALGORITHM_V4 = "registered-joint-goal-execution-source-dag-v4"
MARKER = "full_joint_goal_execution"
PARENT_REFERENCE = "joint_plan_original_reference"
PAIR_REFERENCE = "original_pair_reference"
PART_FIELDS = {"protocol", "phase", "pair_hash", "record", PAIR_REFERENCE}


def source_binding_for_input(value: dict[str, Any], plan_hash: str = "0" * 64) -> dict[str, Any]:
    from app.domain.full_joint_goal_execution import NAMESPACE

    try:
        request = value["request"]
        base = value["joint"]["original_actual_input"]["base"]
        return binding(
            {
                "user_id": base["user_id"],
                "epoch_id": base["epoch_id"],
                "plan_id": str(
                    uuid5(
                        NAMESPACE, configuration_hash({"user": base["user_id"], "request": request})
                    )
                ),
                "request_hash": configuration_hash(request),
                "plan_hash": plan_hash,
                "plan_protocol": ALGORITHM_V4,
            }
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise JointArchiveError("V4 native input original binding missing") from error


def source_binding_for_plan(plan: dict[str, Any]) -> dict[str, Any]:
    try:
        if plan["protocol"] != ALGORITHM_V4:
            raise JointArchiveError("Native V4 cannot relabel another original plan")
        bound = source_binding_for_input(plan["inputs"], plan["plan_hash"])
        dag = pool_sources(plan, bound, "PLAN")
        _canonical(dag, MAX_DECODED_BYTES, MAX_NODES, MAX_DEPTH)
        view = SharedJointOriginal(dag, bound, "PLAN")
        view.check_original_binding()
        view.logical_metrics()
        return bound
    except (KeyError, TypeError, AttributeError) as error:
        raise JointArchiveError("V4 native complete plan binding missing") from error


def source_plan_reference(plan: dict[str, Any]) -> dict[str, Any]:
    """Deterministically bind original PLAN transport in independently audited trace."""
    return reference(encode_shared_joint(plan, source_binding_for_plan(plan), "PLAN"))


def encode_native_source_trace_parts(
    inputs: dict[str, Any],
    outcome: dict[str, Any],
    phase: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(phase) is not str or phase not in PHASES:
        raise JointArchiveError("V4 native trace phase differs")
    try:
        plan = (
            outcome["joint_execution_plan"]
            if phase == "EVALUATION"
            else inputs["planning"][MARKER]["plan"]
        )
        bound = source_binding_for_plan(plan)
        native_outcome = copy.deepcopy(outcome)
        if phase == "EVALUATION":
            expected_plan = source_plan_reference(plan)
            if (
                PARENT_REFERENCE in native_outcome
                and native_outcome[PARENT_REFERENCE] != expected_plan
            ):
                raise JointArchiveError("V4 parent original reference differs from whole plan")
            native_outcome[PARENT_REFERENCE] = expected_plan
        elif PARENT_REFERENCE in native_outcome:
            raise JointArchiveError("V4 child cannot substitute parent planning reference")
        left, right, expected_pair = encode_shared_trace_parts(inputs, native_outcome, phase, bound)
        return (
            {**left, PAIR_REFERENCE: copy.deepcopy(expected_pair)},
            {**right, PAIR_REFERENCE: copy.deepcopy(expected_pair)},
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise JointArchiveError("V4 native trace lacks complete original plan") from error


def decode_source_record(
    record: dict[str, Any],
    original_binding: dict[str, Any],
    kind: RecordKind,
    *,
    expected_reference: dict[str, Any],
) -> dict[str, Any]:
    """First full codec validation; request-local isolated copies only thereafter."""
    bound = binding(original_binding)
    if bound["plan_protocol"] != ALGORITHM_V4:
        raise JointArchiveError("Native V4 reader cannot downgrade/relabel original algorithm")
    return decode_complete_original(
        ALGORITHM_V4,
        {
            "record": record,
            "binding": bound,
            "kind": kind,
            "expected_reference": expected_reference,
            "shared_bytes_limit": MAX_DECODED_BYTES,
            "shared_node_limit": MAX_NODES,
            "depth_limit": MAX_DEPTH,
        },
        record.get("expanded_bytes", 0),
        lambda: cast(
            dict[str, Any],
            decode_shared_joint(record, bound, kind, expected_reference=expected_reference).read(),
        ),
    )


def decode_native_source_trace_parts(
    inputs: dict[str, Any],
    outcome: dict[str, Any],
    phase: str,
    user_id: UUID,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Caller verifies entire wire trace hash before taking its retained pair ref."""
    if type(phase) is not str or phase not in PHASES:
        raise JointArchiveError("V4 native trace phase differs")
    for part in (inputs, outcome):
        if (
            type(part) is not dict
            or set(part) != PART_FIELDS
            or part["protocol"] != TRACE_PROTOCOL
            or part["phase"] != phase
        ):
            raise JointArchiveError("V4 native trace closed part/phase differs")
    try:
        expected = inputs[PAIR_REFERENCE]
        actual = trace_reference(inputs["record"], outcome["record"], phase)
        if (
            expected != outcome[PAIR_REFERENCE]
            or expected != actual
            or inputs["pair_hash"] != actual["pair_hash"]
            or outcome["pair_hash"] != actual["pair_hash"]
        ):
            raise JointArchiveError("V4 independent original trace pair differs")
        bound = binding(inputs["record"]["binding"])
        if bound["user_id"] != str(user_id) or bound["plan_protocol"] != ALGORITHM_V4:
            raise JointArchiveError("V4 native trace user or exact algorithm differs")
        left = decode_source_record(
            inputs["record"], bound, "TRACE_INPUTS", expected_reference=expected["inputs"]
        )
        right = decode_source_record(
            outcome["record"], bound, "TRACE_OUTCOME", expected_reference=expected["outcome"]
        )
        if phase == "EVALUATION":
            if "joint_execution_input" not in left or "joint_execution_plan" not in right:
                raise JointArchiveError("V4 whole evaluation originals missing")
            if right.get(PARENT_REFERENCE) != source_plan_reference(right["joint_execution_plan"]):
                raise JointArchiveError("V4 independent original parent reference missing or dirty")
        elif "planning" not in left or "joint_execution_plan" in right or PARENT_REFERENCE in right:
            raise JointArchiveError("V4 complete child originals differ")
        return left, right
    except (KeyError, TypeError, AttributeError) as error:
        raise JointArchiveError("V4 native trace complete references malformed") from error


def expanded_source_joint_trace(trace: "DecisionTrace") -> "DecisionTrace":
    from app.domain.decision_trace import verify_trace

    if trace.algorithm_versions.get(MARKER) != ALGORITHM_V4:
        raise JointArchiveError("V4 trace cannot relabel another algorithm")
    verify_trace(trace)  # Wire hash before self-contained audited original refs.
    left, right = decode_native_source_trace_parts(
        trace.inputs, trace.outcome, trace.phase, trace.user_id
    )
    return trace.model_copy(update={"inputs": left, "outcome": right})


def native_semantic_trace(trace: "DecisionTrace") -> "DecisionTrace":
    """Remove only validated new transport receipt for unchanged native exact math."""
    if trace.algorithm_versions.get(MARKER) != ALGORITHM_V4 or trace.phase != "EVALUATION":
        return trace
    outcome = dict(trace.outcome)
    expected = source_plan_reference(outcome["joint_execution_plan"])
    if outcome.pop(PARENT_REFERENCE, None) != expected:
        raise JointArchiveError("V4 native semantic read requires exact original parent reference")
    return trace.model_copy(update={"outcome": outcome})


def verified_parent_plan_reference(
    trace: "DecisionTrace",
    original_binding: dict[str, Any],
) -> dict[str, Any]:
    """Store passes actual COMPLETE/audit VALID trace, never a claimed body label."""
    bound = binding(original_binding)
    if (
        trace.algorithm_versions.get(MARKER) != ALGORITHM_V4
        or trace.user_id != UUID(bound["user_id"])
        or trace.phase != "EVALUATION"
        or trace.run_id != uuid5(UUID(bound["plan_id"]), "planning-proof")
        or trace.action_id is not None
        or trace.parent_run_id is not None
    ):
        raise JointArchiveError("V4 parent original planning trace identity differs")
    view = expanded_source_joint_trace(trace)
    if source_binding_for_plan(view.outcome["joint_execution_plan"]) != bound:
        raise JointArchiveError("V4 parent columns differ from independently audited plan")
    expected = view.outcome[PARENT_REFERENCE]
    if (
        expected.get("record_kind") != "PLAN"
        or expected.get("binding") != bound
        or expected != source_plan_reference(view.outcome["joint_execution_plan"])
    ):
        raise JointArchiveError("V4 independent original parent reference differs")
    return cast(dict[str, Any], copy.deepcopy(expected))
