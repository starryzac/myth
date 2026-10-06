"""Persist new v4 observations and verify their complete original ancestry.

Only decision/audit metadata is written. Notification production is still absent.
"""

import json
from datetime import datetime
from uuid import UUID, uuid5

from app.db.models import DecisionRun
from app.domain.decision_trace import build_trace, verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_action_set_boundary import GlobalBoundaryObserveRequest
from app.domain.full_action_set_boundary_actual import MAX_CAPTURE_BYTES
from app.domain.full_action_set_boundary_recovery_composed import (
    ALGORITHM,
    RecoveryComposedActionSetInput,
    RecoveryComposedActionSetSnapshot,
    RecoveryComposedGlobalObservation,
    compare_recovery_composed_action_sets,
    derive_recovery_composed_action_set,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch
from app.services.decision_trace import get_decision_trace, record_trace
from app.services.full_action_set_boundary import _verify_original_source_copies
from app.services.full_action_set_boundary_recovery_composed import (
    capture_recovery_composed_action_set,
)
from app.services.full_action_set_recovery_producers import verify_recovery_source_copies
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NAMESPACE = UUID("dfb9d38c-5698-532d-a90f-bcb2458d7d7f")


def _error(
    message: str, code: str = "RECOVERY_COMPOSED_ORIGINAL_UNVERIFIED"
) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def verify_frozen_recovery_composed_observation(
    trace: DecisionTrace,
) -> RecoveryComposedGlobalObservation:
    verify_trace(trace)
    if (
        trace.algorithm_versions.get("global_action_set") != ALGORITHM
        or trace.phase != "EVALUATION"
        or trace.action_id is not None
        or set(trace.inputs)
        != {"original_request", "recovery_composed_action_set_input", "previous_snapshot"}
    ):
        raise ValueError("Not the exact recovery composed observation algorithm")
    inputs = RecoveryComposedActionSetInput.model_validate_json(
        json.dumps(trace.inputs["recovery_composed_action_set_input"])
    )
    snapshot = derive_recovery_composed_action_set(inputs)
    original = inputs.composed.periodic.original_actual_input
    _verify_original_source_copies(trace, original.base, snapshot.global_action_set_complete)
    verify_recovery_source_copies(trace, inputs.recovery, snapshot.recovery_family)
    sources = {row.id: row for row in trace.sources}
    periodic = inputs.composed.periodic
    for periodic_producer in periodic.producers:
        for command in periodic_producer.commands:
            for source in command.trace.sources:
                if sources.get(source.id) != source:
                    raise ValueError("RECOVERY_COMPOSED_PERIODIC_ORIGINAL_SOURCE_COPY_DIFFERS")
        candidate = periodic_producer.candidate
        if candidate is not None and candidate.facts is not None:
            for identity in (
                candidate.facts.source_evidence_ids + candidate.facts.authority.evidence_ids
            ):
                row = sources.get(identity)
                if (
                    row is None
                    or row.user_id != snapshot.user_id
                    or row.content_integrity != "VERIFIED"
                    or row.captured_content_hash != configuration_hash(row.content)
                ):
                    raise ValueError("RECOVERY_COMPOSED_PERIODIC_CURRENT_SOURCE_COPY_MISSING")
    for dynamic_producer in original.dynamic_goals:
        if dynamic_producer.data is not None:
            for ref in dynamic_producer.data.source_refs:
                row = sources.get(ref.evidence_id)
                if (
                    row is None
                    or row.user_id != ref.user_id
                    or row.content_hash != ref.content_hash
                    or row.content_integrity != "VERIFIED"
                    or configuration_hash(row.content) != ref.content_hash
                ):
                    raise ValueError("RECOVERY_COMPOSED_DYNAMIC_ORIGINAL_SOURCE_COPY_MISSING")
    for asset_producer in original.assets:
        if asset_producer.actual_preview is not None:
            required = set(
                asset_producer.actual_preview.original_full_planning.source_evidence_ids
                + asset_producer.actual_preview.original_full_protection.source_evidence_ids
            )
            if asset_producer.authority is not None:
                required.update(asset_producer.authority.evidence_ids)
            for identity in required:
                row = sources.get(identity)
                if (
                    row is None
                    or row.user_id != snapshot.user_id
                    or row.content_integrity != "VERIFIED"
                    or row.captured_content_hash != configuration_hash(row.content)
                ):
                    raise ValueError("RECOVERY_COMPOSED_ASSET_ORIGINAL_SOURCE_COPY_MISSING")
    value = RecoveryComposedGlobalObservation.model_validate_json(
        json.dumps(trace.outcome["recovery_composed_global_observation"])
    )
    previous = (
        None
        if trace.inputs["previous_snapshot"] is None
        else RecoveryComposedActionSetSnapshot.model_validate_json(
            json.dumps(trace.inputs["previous_snapshot"])
        )
    )
    if previous is not None and previous.snapshot_hash != configuration_hash(
        previous.model_dump(mode="json", exclude={"snapshot_hash"})
    ):
        raise ValueError("RECOVERY_COMPOSED_PREVIOUS_SNAPSHOT_HASH_DIFFERS")
    kind, semantic = compare_recovery_composed_action_sets(previous, snapshot)
    comparable = previous is None or (
        previous.global_action_set_complete
        and (previous.user_id, previous.epoch_id, previous.scope, previous.algorithm_version)
        == (snapshot.user_id, snapshot.epoch_id, snapshot.scope, snapshot.algorithm_version)
        and previous.as_of <= snapshot.as_of
    )
    if (
        value.snapshot != snapshot
        or (previous is None) != (trace.parent_run_id is None)
        or value.kind != kind
        or value.semantic_key != semantic
        or value.requires_user_attention != (kind == "BoundaryCrossed")
        or (trace.user_id, trace.run_id, trace.as_of)
        != (value.user_id, value.observation_run_id, snapshot.as_of)
        or value.original_request.model_dump(mode="json") != trace.inputs["original_request"]
        or configuration_hash(value.original_request.model_dump(mode="json")) != value.request_hash
        or value.epoch_id != snapshot.epoch_id
        or value.original_request.expected_epoch_id != snapshot.epoch_id
        or value.idempotent_replay
        or trace.run_id
        != uuid5(
            NAMESPACE, f"{value.user_id}:{value.epoch_id}:{value.original_request.idempotency_key}"
        )
        or value.original_request.previous_observation_run_id != trace.parent_run_id
        or value.previous_observation_run_id != trace.parent_run_id
        or value.previous_snapshot_hash != (previous.snapshot_hash if previous else None)
        or value.previous_action_set_signature
        != (previous.action_set_signature if previous else None)
        or value.global_action_set_complete != (snapshot.global_action_set_complete and comparable)
        or trace.outcome
        != {
            "recovery_composed_global_observation": value.model_dump(mode="json"),
            "decision_status": "COMPUTED" if kind else "UNKNOWN",
        }
    ):
        raise ValueError("RECOVERY_COMPOSED_ORIGINAL_CALCULATION_OR_REQUEST_DIFFERS")
    return value


def read_recovery_composed_observation(
    session: Session, user_id: UUID, run_id: UUID, now: datetime
) -> RecoveryComposedGlobalObservation:
    original = get_decision_trace(session, user_id, run_id, now)
    if (
        original.trace is None
        or original.completeness != "COMPLETE"
        or original.audit_chain_status != "VALID"
    ):
        raise _error("原完整组合轨迹或审计未验真")
    try:
        value = verify_frozen_recovery_composed_observation(original.trace)
        child, child_trace, visited = value, original.trace, {run_id}
        while child.previous_observation_run_id is not None:
            identity = child.previous_observation_run_id
            if identity in visited or len(visited) >= 64:
                raise ValueError("RECOVERY_COMPOSED_ORIGINAL_ANCESTRY_CYCLIC_OR_OVER_CAPACITY")
            visited.add(identity)
            parent = get_decision_trace(session, user_id, identity, now)
            if (
                parent.trace is None
                or parent.completeness != "COMPLETE"
                or parent.audit_chain_status != "VALID"
            ):
                raise ValueError("RECOVERY_COMPOSED_ORIGINAL_PARENT_UNVERIFIED")
            before = verify_frozen_recovery_composed_observation(parent.trace)
            if child_trace.inputs["previous_snapshot"] != before.snapshot.model_dump(mode="json"):
                raise ValueError("RECOVERY_COMPOSED_CAPTURED_PARENT_DIFFERS_FROM_ORIGINAL")
            child, child_trace = before, parent.trace
        return value
    except (ValueError, TypeError, KeyError) as error:
        raise _error(str(error)) from error


def observe_recovery_composed_action_set(
    engine: Engine, user_id: UUID, body: GlobalBoundaryObserveRequest, now: datetime
) -> RecoveryComposedGlobalObservation:
    body = GlobalBoundaryObserveRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    identity = uuid5(NAMESPACE, f"{user_id}:{body.expected_epoch_id}:{body.idempotency_key}")
    with Session(engine) as session, session.begin():
        _user(session, user_id)
        epoch = current_audit_epoch(session, user_id)
        if epoch is None or epoch.status != "OPEN" or epoch.id != body.expected_epoch_id:
            raise _error("原组合观察轮次已变化", "RECOVERY_COMPOSED_EPOCH_CHANGED")
        if session.get(DecisionRun, identity) is not None:
            original = read_recovery_composed_observation(session, user_id, identity, now)
            if original.original_request != body:
                raise _error("原组合观察key不能改变请求", "IDEMPOTENCY_CONFLICT")
            return original.model_copy(update={"idempotent_replay": True})
        before = (
            read_recovery_composed_observation(
                session, user_id, body.previous_observation_run_id, now
            )
            if body.previous_observation_run_id
            else None
        )
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
            connection.begin(),
        ):
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as read:
                current = capture_recovery_composed_action_set(read, user_id, now)
        if current.result.epoch_id != epoch.id:
            raise _error("原独立组合快照轮次不同", "RECOVERY_COMPOSED_EPOCH_CHANGED")
        if len(current.inputs.model_dump_json().encode("utf8")) > MAX_CAPTURE_BYTES:
            raise _error(
                "原完整组合输入超限，不能删减分母", "RECOVERY_COMPOSED_CAPTURE_CAPACITY_EXCEEDED"
            )
        previous = before.snapshot if before else None
        kind, semantic = compare_recovery_composed_action_sets(previous, current.result)
        comparable = previous is None or (
            previous.global_action_set_complete
            and (previous.user_id, previous.epoch_id, previous.scope, previous.algorithm_version)
            == (
                current.result.user_id,
                current.result.epoch_id,
                current.result.scope,
                current.result.algorithm_version,
            )
            and previous.as_of <= current.result.as_of
        )
        value = RecoveryComposedGlobalObservation(
            user_id=user_id,
            epoch_id=epoch.id,
            observation_run_id=identity,
            previous_observation_run_id=body.previous_observation_run_id,
            original_request=body,
            request_hash=configuration_hash(body.model_dump(mode="json")),
            snapshot=current.result,
            kind=kind,
            semantic_key=semantic,
            requires_user_attention=kind == "BoundaryCrossed",
            previous_snapshot_hash=previous.snapshot_hash if previous else None,
            previous_action_set_signature=previous.action_set_signature if previous else None,
            global_action_set_complete=current.result.global_action_set_complete and comparable,
        )
        try:
            trace = build_trace(
                run_id=identity,
                user_id=user_id,
                phase="EVALUATION",
                as_of=now,
                parent_run_id=body.previous_observation_run_id,
                action_id=None,
                algorithm_versions={"global_action_set": ALGORITHM},
                inputs={
                    "original_request": body.model_dump(mode="json"),
                    "recovery_composed_action_set_input": current.inputs.model_dump(mode="json"),
                    "previous_snapshot": previous.model_dump(mode="json") if previous else None,
                },
                sources=list(current.originals.sources.values()),
                policies=list(current.originals.policies.values()),
                constraints=[],
                candidates=[],
                outcome={
                    "recovery_composed_global_observation": value.model_dump(mode="json"),
                    "decision_status": "COMPUTED" if kind else "UNKNOWN",
                },
            )
            verify_frozen_recovery_composed_observation(trace)
        except (ValueError, TypeError, KeyError) as error:
            raise _error(str(error)) from error
        record_trace(session, trace)
        return value
