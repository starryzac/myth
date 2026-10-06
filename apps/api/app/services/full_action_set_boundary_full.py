"""Actual RR/RO FULL Goal producers and metadata-only original observations."""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from app.db.models import DecisionRun
from app.domain.decision_trace import build_trace, verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_action_set_boundary import GlobalBoundaryObserveRequest, known_inactive_keys
from app.domain.full_action_set_boundary_full import (
    ALGORITHM,
    MAX_CANDIDATES,
    NAMESPACE,
    DynamicGoalProducerInput,
    FullActionSetInput,
    FullActionSetSnapshot,
    FullGlobalBoundaryObservation,
    compare_full_action_sets,
    derive_full_action_set,
    dynamic_model_inventory,
)
from app.domain.full_dynamic_goal_execution import FullDynamicGoalPrepareRequest
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch
from app.services.autonomy import _authority
from app.services.autonomy_envelope import _snapshot
from app.services.decision_recording import CAPTURE_KEY, capture_evidence, capture_versions
from app.services.decision_trace import get_decision_trace, record_trace
from app.services.full_action_set_boundary import (
    MAX_CAPTURE_BYTES,
    ActionSetCapture,
    _error,
    _verify_original_source_copies,
    capture_current_action_set,
)
from app.services.full_dynamic_goal_execution import read_full_dynamic_goal_inputs
from app.services.full_goals import read_full_goal_model
from app.services.full_intervention import Source
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class FullActionSetCapture:
    inputs: FullActionSetInput
    snapshot: FullActionSetSnapshot
    originals: ActionSetCapture


def capture_full_action_set(
    session: Session, user_id: UUID, now: datetime, base: ActionSetCapture | None = None
) -> FullActionSetCapture:
    """Actual same-session input per call; no permission or cross-call cache."""
    _snapshot(session)
    now = _now(now)
    base = base or capture_current_action_set(session, user_id, now)
    if (base.inputs.user_id, base.inputs.as_of) != (user_id, now):
        raise _error("FULL_ACTION_SET_BASE_BINDING_DIFFERS", "原基线用户或时点不一致")
    previous_capture = session.info.get(CAPTURE_KEY)
    session.info[CAPTURE_KEY] = base.originals
    producers: list[DynamicGoalProducerInput] = []
    try:
        with session.no_autoflush:
            inactive = known_inactive_keys(base.inputs.original_inventory, now)
            models = dynamic_model_inventory(base.inputs)
            for key, originals in models.items():
                if len(models) > MAX_CANDIDATES:
                    producers.append(
                        DynamicGoalProducerInput(
                            candidate_key=key,
                            model_evidence_ids=originals,
                            missing_reasons=["DYNAMIC_PRODUCER_CAPACITY_EXCEEDED"],
                        )
                    )
                    continue
                if key in inactive:
                    producers.append(
                        DynamicGoalProducerInput(
                            candidate_key=key,
                            model_evidence_ids=originals,
                            excluded_by_current_policy=True,
                        )
                    )
                    continue
                try:
                    identity = UUID(key[5:])
                    model = read_full_goal_model(session, user_id, identity, now)
                    if (
                        model.status != "VERIFIED"
                        or model.evidence_id is None
                        or model.evidence_hash is None
                    ):
                        raise ValueError("CURRENT_FULL_GOAL_MODEL_NOT_VERIFIED")
                    request = FullDynamicGoalPrepareRequest(
                        goal_id=identity,
                        expected_policy_version_id=model.base_policy_version_id,
                        expected_model_evidence_id=model.evidence_id,
                        expected_model_evidence_hash=model.evidence_hash,
                        expected_epoch_id=base.inputs.epoch_id,
                        idempotency_key="readonly-global-dynamic:" + str(identity),
                    )
                    data = read_full_dynamic_goal_inputs(session, user_id, request, now)
                    version = next(
                        row
                        for row in data.context.versions
                        if row.version_id == model.base_policy_version_id
                    )
                    authority = _authority(
                        session, user_id, [version.policy_id], now, expected=[version.version_id]
                    )
                    capture_evidence(
                        session,
                        user_id,
                        sorted(
                            {row.evidence_id for row in data.source_refs}
                            | set(authority.evidence_ids)
                        ),
                    )
                    capture_versions(
                        session, user_id, [row.version_id for row in data.context.versions]
                    )
                    producers.append(
                        DynamicGoalProducerInput(
                            candidate_key=key,
                            model_evidence_ids=originals,
                            data=data,
                            authority=authority,
                        )
                    )
                except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
                    producers.append(
                        DynamicGoalProducerInput(
                            candidate_key=key,
                            model_evidence_ids=originals,
                            missing_reasons=[getattr(error, "code", str(error))],
                        )
                    )
            inputs = FullActionSetInput(base=base.inputs, dynamic_goals=producers)
            snapshot = derive_full_action_set(inputs)
            if len(inputs.model_dump_json().encode("utf-8")) > MAX_CAPTURE_BYTES:
                # An oversized current getter retains the complete denominator,
                # but observations refuse to persist rather than truncate it.
                inputs = inputs.model_copy(
                    update={
                        "base": base.inputs.model_copy(
                            update={
                                "inventory_reasons": [
                                    *base.inputs.inventory_reasons,
                                    "FULL_CAPTURE_CAPACITY_EXCEEDED",
                                ]
                            }
                        )
                    }
                )
                snapshot = derive_full_action_set(inputs)
            return FullActionSetCapture(inputs, snapshot, base)
    finally:
        if previous_capture is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous_capture


def read_current_full_action_set(
    session: Session, user_id: UUID, now: datetime
) -> FullActionSetSnapshot:
    return capture_full_action_set(session, user_id, now).snapshot


def verify_frozen_full_action_set_trace(trace: DecisionTrace) -> FullGlobalBoundaryObservation:
    verify_trace(trace)
    if (
        trace.algorithm_versions.get("global_action_set") != ALGORITHM
        or trace.phase != "EVALUATION"
    ):
        raise ValueError("Not the original FULL action-set algorithm")
    inputs = FullActionSetInput.model_validate_json(
        json.dumps(trace.inputs["full_action_set_input"])
    )
    snapshot = derive_full_action_set(inputs)
    _verify_original_source_copies(trace, inputs.base, snapshot.global_action_set_complete)
    sources = {row.id: row for row in trace.sources}
    for producer in inputs.dynamic_goals:
        if producer.data is None:
            continue
        for ref in producer.data.source_refs:
            source = sources.get(ref.evidence_id)
            if (
                source is None
                or source.user_id != ref.user_id
                or source.content_hash != ref.content_hash
            ):
                raise ValueError("A consumed original dynamic source copy is missing or different")
            if (
                source.content_integrity != "VERIFIED"
                or configuration_hash(source.content) != ref.content_hash
            ):
                raise ValueError("An original dynamic source content is not verified")
    value = FullGlobalBoundaryObservation.model_validate_json(
        json.dumps(trace.outcome["full_global_boundary_observation"])
    )
    previous = (
        FullActionSetSnapshot.model_validate_json(json.dumps(trace.inputs["previous_snapshot"]))
        if trace.inputs["previous_snapshot"] is not None
        else None
    )
    kind, semantic = compare_full_action_sets(previous, snapshot)
    comparable = previous is None or (
        previous.global_action_set_complete
        and (previous.user_id, previous.epoch_id, previous.scope, previous.algorithm_version)
        == (snapshot.user_id, snapshot.epoch_id, snapshot.scope, snapshot.algorithm_version)
        and previous.as_of <= snapshot.as_of
    )
    if (
        value.snapshot != snapshot
        or value.kind != kind
        or value.semantic_key != semantic
        or value.requires_user_attention != (kind == "BoundaryCrossed")
        or (trace.user_id, trace.run_id, trace.as_of)
        != (value.user_id, value.observation_run_id, inputs.base.as_of)
        or value.original_request.model_dump(mode="json") != trace.inputs["original_request"]
        or configuration_hash(value.original_request.model_dump(mode="json")) != value.request_hash
        or value.epoch_id != inputs.base.epoch_id
        or value.original_request.expected_epoch_id != inputs.base.epoch_id
        or trace.action_id is not None
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
    ):
        raise ValueError("Original FULL producer calculation or observation binding differs")
    return value


def read_full_global_boundary_observation(
    session: Session, user_id: UUID, run_id: UUID, now: datetime
) -> FullGlobalBoundaryObservation:
    result = get_decision_trace(session, user_id, run_id, now)
    if (
        result.trace is None
        or result.completeness != "COMPLETE"
        or result.audit_chain_status != "VALID"
    ):
        raise _error("FULL_GLOBAL_BOUNDARY_ORIGINAL_UNVERIFIED", "原全局轨迹和审计未完整验真")
    try:
        value = verify_frozen_full_action_set_trace(result.trace)
        child, child_trace, visited = value, result.trace, {run_id}
        while child.previous_observation_run_id is not None:
            identity = child.previous_observation_run_id
            if identity in visited or len(visited) >= 64:
                raise ValueError("Original FULL observation ancestry is cyclic or exceeds capacity")
            visited.add(identity)
            parent = get_decision_trace(session, user_id, identity, now)
            if (
                parent.trace is None
                or parent.completeness != "COMPLETE"
                or parent.audit_chain_status != "VALID"
            ):
                raise ValueError("Actual original FULL parent is not verified")
            before = verify_frozen_full_action_set_trace(parent.trace)
            if child_trace.inputs["previous_snapshot"] != before.snapshot.model_dump(mode="json"):
                raise ValueError("Captured previous FULL snapshot differs from original parent")
            child, child_trace = before, parent.trace
        return value
    except (ValueError, TypeError, KeyError) as error:
        raise _error("FULL_GLOBAL_BOUNDARY_ORIGINAL_UNVERIFIED", str(error)) from error


def observe_full_global_boundary(
    engine: Engine, user_id: UUID, body: GlobalBoundaryObserveRequest, now: datetime
) -> FullGlobalBoundaryObservation:
    now = _now(now)
    identity = uuid5(NAMESPACE, f"{user_id}:{body.expected_epoch_id}:{body.idempotency_key}")
    with Session(engine) as session, session.begin():
        _user(session, user_id)
        epoch = current_audit_epoch(session, user_id)
        if epoch is None or epoch.status != "OPEN" or epoch.id != body.expected_epoch_id:
            raise _error("STALE_FULL_GLOBAL_BOUNDARY_EPOCH", "原观察轮次已变化")
        if session.get(DecisionRun, identity) is not None:
            original = read_full_global_boundary_observation(session, user_id, identity, now)
            if original.original_request != body:
                raise _error("IDEMPOTENCY_CONFLICT", "原观察键不能改变完整请求")
            return original.model_copy(update={"idempotent_replay": True})
        before = (
            read_full_global_boundary_observation(
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
                current = capture_full_action_set(read, user_id, now)
        if current.snapshot.epoch_id != epoch.id:
            raise _error("STALE_FULL_GLOBAL_BOUNDARY_EPOCH", "实际独立快照轮次不同")
        if len(current.inputs.model_dump_json().encode("utf-8")) > MAX_CAPTURE_BYTES:
            raise _error(
                "FULL_GLOBAL_BOUNDARY_CAPTURE_CAPACITY_EXCEEDED", "完整输入超限，不能删减分母"
            )
        previous = before.snapshot if before else None
        kind, semantic = compare_full_action_sets(previous, current.snapshot)
        comparable = previous is None or (
            previous.global_action_set_complete
            and previous.epoch_id == current.snapshot.epoch_id
            and previous.as_of <= current.snapshot.as_of
        )
        value = FullGlobalBoundaryObservation(
            user_id=user_id,
            epoch_id=epoch.id,
            observation_run_id=identity,
            previous_observation_run_id=body.previous_observation_run_id,
            original_request=body,
            request_hash=configuration_hash(body.model_dump(mode="json")),
            snapshot=current.snapshot,
            kind=kind,
            semantic_key=semantic,
            requires_user_attention=kind == "BoundaryCrossed",
            previous_snapshot_hash=previous.snapshot_hash if previous else None,
            previous_action_set_signature=previous.action_set_signature if previous else None,
            global_action_set_complete=current.snapshot.global_action_set_complete and comparable,
        )
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
                "full_action_set_input": current.inputs.model_dump(mode="json"),
                "previous_snapshot": previous.model_dump(mode="json") if previous else None,
            },
            sources=list(current.originals.originals.sources.values()),
            policies=list(current.originals.originals.policies.values()),
            constraints=[],
            candidates=[],
            outcome={
                "full_global_boundary_observation": value.model_dump(mode="json"),
                "decision_status": "COMPUTED" if kind else "UNKNOWN",
            },
        )
        verify_frozen_full_action_set_trace(trace)
        record_trace(session, trace)
        return value


def full_global_boundary_intervention_source(
    session: Session, user_id: UUID, run_id: UUID, now: datetime
) -> Source:
    result = get_decision_trace(session, user_id, run_id, now)
    if (
        result.trace is None
        or result.completeness != "COMPLETE"
        or result.audit_chain_status != "VALID"
    ):
        raise _error("FULL_GLOBAL_BOUNDARY_ORIGINAL_UNVERIFIED", "全局通知需完整原观察和有效审计")
    value = read_full_global_boundary_observation(session, user_id, run_id, now)
    if not value.global_action_set_complete or value.kind is None or value.semantic_key is None:
        raise _error("FULL_GLOBAL_BOUNDARY_SOURCE_UNKNOWN", "不完整或初始观察不能冒充全局通知")
    return Source(
        result.trace,
        value.semantic_key,
        None,
        None,
        None,
        value.model_dump(mode="json"),
        value.requires_user_attention,
    )
