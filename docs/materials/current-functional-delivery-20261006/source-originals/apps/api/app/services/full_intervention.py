"""Verified original questions and boundary observations; delivery grants no authority."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.full_models import InterventionInbox, InterventionOutbox
from app.db.models import DecisionRun, User
from app.domain.boundary_action_events import ALGORITHM_VERSION as BOUNDARY_ALGORITHM
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_intervention import (
    ALGORITHM,
    CONSUMER,
    PROTOCOL,
    AcknowledgmentRequest,
    DeliveryRequest,
    GlobalBoundaryObservationRequest,
    Hash,
    InterventionMessage,
    InterventionReceipt,
    ObserveRequest,
    QuestionObservationRequest,
    State,
    boundary_semantics,
    command_identity,
    effective_state,
    message_identity,
    question_semantics,
)
from app.domain.full_policy_configuration import InterventionPolicy
from app.domain.policy_configuration import configuration_hash
from app.domain.question_workflow import CommandKey, PendingPlanningQuestion, QuestionRevision
from app.services.audit_chain import current_audit_epoch
from app.services.autonomy_envelope import _snapshot
from app.services.boundary_action_events import BoundaryObservationResponse, _signature
from app.services.decision_trace import get_decision_trace, record_trace
from app.services.full_policy_lifecycle import read_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from app.services.question_workflow import QuestionWorkflowResponse, read_question_session
from pydantic import Field, StrictInt, TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

MAX_MESSAGES = 512
CommandKeyAdapter = TypeAdapter(CommandKey)


class InterventionInboxClaim(BoundaryModel):
    """Actual retained claim identity; it does not prove a delivery response or human view."""

    inbox_id: UUID
    user_id: UUID
    message_id: UUID
    epoch_id: UUID
    consumer_ref: Literal["intervention-center-v1"] = "intervention-center-v1"
    payload_hash: Hash
    state: Literal["RECEIVED", "ACKNOWLEDGED", "INVALIDATED"]
    created_at: datetime
    received_at: datetime
    actual_human_view_verified: Literal[False] = False


class CurrentQuestionObservation(BoundaryModel):
    """Original new observation, distinct from the immutable historical payload source."""

    observation_run_id: UUID
    observation_trace_hash: Hash
    original_receipt: InterventionReceipt
    session_id: UUID
    revision: Annotated[StrictInt, Field(ge=1, le=16)]
    source_run_id: UUID
    source_trace_hash: Hash
    semantic_key: Hash
    current_question: PendingPlanningQuestion
    authority_granted: Literal[False] = False
    execution_eligible: Literal[False] = False


class InterventionView(BoundaryModel):
    simulation: Literal[True] = True
    original_message: InterventionMessage
    payload_hash: Hash
    stored_state: State
    effective_state: State | Literal["UNKNOWN", "ARCHIVED"]
    source_status: Literal["CURRENT", "STALE", "UNKNOWN", "ARCHIVED"]
    available_at: datetime
    pending: bool
    previously_claimed: bool
    original_inbox_claim: InterventionInboxClaim | None
    current_question: PendingPlanningQuestion | None
    original_acknowledgment: InterventionReceipt | None
    current_question_observation: CurrentQuestionObservation | None = None
    current_source_binding: Literal[
        "ORIGINAL_MESSAGE", "CURRENT_OBSERVATION", "LEGACY_TERMINAL_SOURCE", "UNVERIFIED"
    ] = "UNVERIFIED"
    authority_granted: Literal[False] = False
    answers_question: Literal[False] = False
    execution_eligible: Literal[False] = False
    dedicated_audit_event: Literal[False] = False
    global_boundary_subscription: Literal["NOT_IMPLEMENTED", "EXPLICIT_OBSERVATION_ONLY"] = (
        "NOT_IMPLEMENTED"
    )


class InterventionList(BoundaryModel):
    simulation: Literal[True] = True
    items: list[InterventionView]
    actual_message_count: Annotated[StrictInt, Field(ge=0)]
    complete_inventory: Literal[True] = True
    presentation_truncated: bool
    authority_granted: Literal[False] = False


class InterventionCommandResponse(BoundaryModel):
    simulation: Literal[True] = True
    original_receipt: InterventionReceipt
    message: InterventionView
    replayed_original_receipt: bool
    authority_granted: Literal[False] = False
    execution_eligible: Literal[False] = False


class InterventionCommandLookup(BoundaryModel):
    simulation: Literal[True] = True
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    epoch_id: UUID
    idempotency_key: CommandKey
    original_receipt: InterventionReceipt | None
    message: InterventionView | None
    authority_granted: Literal[False] = False
    replacement_allowed: Literal[False] = False


class InterventionDeliveryResponse(BoundaryModel):
    simulation: Literal[True] = True
    message: InterventionView
    inbox_id: UUID
    original_received_at: datetime
    present_once: bool
    actual_human_view_verified: Literal[False] = False
    authority_granted: Literal[False] = False
    answers_question: Literal[False] = False


@dataclass(frozen=True)
class Source:
    trace: DecisionTrace
    semantic_key: str
    question: PendingPlanningQuestion | None
    session_id: UUID | None
    revision: int | None
    boundary: dict[str, Any] | None
    attention: bool


def _error(code: str, detail: str) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, detail, 409)


@contextmanager
def _reader(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        yield session


def _owner(session: Session, user: UUID) -> None:
    row = session.scalar(select(User).where(User.id == user))
    if row is None or not row.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)


def _epoch(session: Session, user: UUID, expected: UUID) -> None:
    epoch = current_audit_epoch(session, user)
    if epoch is None or epoch.status != "OPEN" or epoch.id != expected:
        raise _error("STALE_INTERVENTION_EPOCH", "通知原周期已变化")


def _trace(session: Session, user: UUID, identity: UUID, now: datetime) -> DecisionTrace:
    result = get_decision_trace(session, user, identity, now)
    if result.completeness != "COMPLETE" or result.audit_chain_status != "VALID":
        raise _error("INTERVENTION_SOURCE_INTEGRITY_ERROR", "通知来源完整轨迹与审计未验真")
    if result.trace is None:
        raise _error("INTERVENTION_SOURCE_INTEGRITY_ERROR", "通知来源没有原 typed 轨迹")
    return result.trace


def _boundary_source(session: Session, user: UUID, identity: UUID, now: datetime) -> Source:
    trace = _trace(session, user, identity, now)
    try:
        if trace.algorithm_versions.get("boundary_observation") != BOUNDARY_ALGORITHM:
            raise ValueError("Not an original single-action observation")
        value = BoundaryObservationResponse.model_validate_json(
            json.dumps(trace.outcome["boundary_observation"])
        )
        before, before_trace = _signature(session, user, value.before_run_id, now)
        after, after_trace = _signature(session, user, value.after_run_id, now)
        if (
            value.user_id != user
            or value.observation_run_id != identity
            or value.recorded_at != trace.as_of
            or value.before_trace_hash != before_trace.trace_hash
            or value.after_trace_hash != after_trace.trace_hash
            or value.before_action_signature != before
            or value.after_action_signature != after
            or value.kind != ("BoundaryObserved" if before == after else "BoundaryCrossed")
            or trace.inputs["original_request"]
            != {
                "before_run_id": str(value.before_run_id),
                "after_run_id": str(value.after_run_id),
                "expected_epoch_id": str(value.epoch_id),
            }
        ):
            raise ValueError("Original observation bindings differ")
    except (ValueError, TypeError, KeyError) as error:
        raise _error("INTERVENTION_SOURCE_INTEGRITY_ERROR", "原单动作事件不能完整重核") from error
    return Source(
        trace,
        boundary_semantics(before, after),
        None,
        None,
        None,
        value.model_dump(mode="json"),
        before != after,
    )


def _question_source(
    session: Session, user: UUID, body: QuestionObservationRequest, now: datetime
) -> Source:
    workflow = read_question_session(session, user, body.session_id, now)
    state = workflow.current_revision
    if (
        workflow.effective_state != "PENDING_ANSWER"
        or workflow.pending_question is None
        or state.epoch_id != body.expected_epoch_id
        or state.revision != body.expected_revision
        or state.run_id != body.expected_run_id
    ):
        raise _error("STALE_INTERVENTION_QUESTION", "需要恢复确切当前问题，旧问题不能继续投递")
    trace = _trace(session, user, state.run_id, now)
    if trace.outcome.get("question_revision") != state.model_dump(mode="json"):
        raise _error("INTERVENTION_SOURCE_INTEGRITY_ERROR", "原问题与其原轨迹不一致")
    return Source(
        trace,
        question_semantics(state),
        workflow.pending_question,
        state.session_id,
        state.revision,
        None,
        True,
    )


def _global_boundary_source(session: Session, user: UUID, identity: UUID, now: datetime) -> Source:
    # The producer imports Source. Import locally to retain its frozen module
    # without an import cycle, and never promote single-action observations.
    from app.services.full_action_set_boundary import global_boundary_intervention_source
    from app.services.full_action_set_boundary_full import full_global_boundary_intervention_source

    original = _trace(session, user, identity, now)
    version = original.algorithm_versions.get("global_action_set")
    if version == "full-policy-action-set-boundary-v1":
        return global_boundary_intervention_source(session, user, identity, now)
    if version == "full-policy-action-set-boundary-full-v1":
        return full_global_boundary_intervention_source(session, user, identity, now)
    raise _error("INTERVENTION_SOURCE_INTEGRITY_ERROR", "原全局观察算法未明确支持，不能降格")


def _global_source_status(
    session: Session, message: InterventionMessage, now: datetime
) -> Literal["CURRENT", "STALE", "UNKNOWN"]:
    from app.domain.full_action_set_boundary import ActionSetSnapshot
    from app.domain.full_action_set_boundary_full import FullActionSetSnapshot
    from app.services.full_action_set_boundary import read_current_action_set
    from app.services.full_action_set_boundary_full import read_current_full_action_set

    assert message.boundary_observation is not None
    original = message.boundary_observation["snapshot"]
    version = original["algorithm_version"]
    current: ActionSetSnapshot | FullActionSetSnapshot
    if version == "full-policy-action-set-boundary-v1":
        current = read_current_action_set(session, message.user_id, now)
    elif version == "full-policy-action-set-boundary-full-v1":
        current = read_current_full_action_set(session, message.user_id, now)
    else:
        return "UNKNOWN"
    if (
        not current.global_action_set_complete
        or current.status != "COMPLETE"
        or current.action_set_signature is None
        or (current.user_id, current.epoch_id) != (message.user_id, message.epoch_id)
        or current.algorithm_version != original["algorithm_version"]
        or current.scope != original["scope"]
    ):
        return "UNKNOWN"
    return (
        "CURRENT" if current.action_set_signature == original["action_set_signature"] else "STALE"
    )


def _policy(
    session: Session, user: UUID, identity: UUID | None, epoch: UUID, now: datetime
) -> dict[str, Any] | None:
    if identity is None:
        return None
    view = read_full_policy(session, user, identity, now)
    if (
        view.epoch_id != epoch
        or view.template_name != "InterventionPolicy"
        or not view.planning_confirmation_valid
        or view.reference_validation != "CURRENT"
        or view.effective_status not in {"ACTIVE", "CONFIRMED"}
    ):
        raise _error("INTERVENTION_POLICY_NOT_CURRENT", "介入设置必须来自当前已确认原版本")
    config = InterventionPolicy.model_validate_json(json.dumps(view.current_version.configuration))
    return {
        "policy_id": str(identity),
        "version_id": str(view.current_version.version_id),
        "content_hash": view.current_version.content_hash,
        "confirmed_at": view.current_version.confirmed_at.isoformat(),
        "configuration": config.model_dump(mode="json"),
        "bank_authority": False,
        # Exact repeated semantics are merged; new economic questions bypass any interval.
        "throttle_scope": "IDENTICAL_NO_NEW_ECONOMIC_CONSEQUENCE_ONLY",
    }


def _read_observe(
    session: Session, user: UUID, identity: UUID, now: datetime
) -> tuple[InterventionReceipt, InterventionMessage] | None:
    if (
        session.scalar(
            select(DecisionRun.id).where(DecisionRun.id == identity, DecisionRun.user_id == user)
        )
        is None
    ):
        return None
    trace = _trace(session, user, identity, now)
    try:
        if trace.algorithm_versions != {"trace": "decision-trace-v1", "intervention": ALGORITHM}:
            raise ValueError("Unsupported notification command algorithm")
        receipt = InterventionReceipt.model_validate_json(json.dumps(trace.outcome["receipt"]))
        message = InterventionMessage.model_validate_json(json.dumps(trace.outcome["message"]))
        if (
            receipt.kind != "OBSERVE"
            or receipt.user_id != user
            or receipt.original_command != trace.inputs["original_command"]
            or receipt.recorded_at != trace.as_of
            or receipt.message_id != message.message_id
            or receipt.payload_hash != configuration_hash(message.model_dump(mode="json"))
            or identity != command_identity(user, receipt.epoch_id, receipt.idempotency_key)
        ):
            raise ValueError("Original notification command receipt changed")
        if receipt.original_command["request"].get("kind") == "QUESTION":
            _question_observation_proof(session, trace, receipt, message, now)
        elif receipt.original_command["request"].get("kind") == "GLOBAL_ACTION_SET_BOUNDARY":
            request = GlobalBoundaryObservationRequest.model_validate_json(
                json.dumps(receipt.original_command["request"])
            )
            source = _global_boundary_source(session, user, request.observation_run_id, now)
            if (
                trace.parent_run_id != request.observation_run_id
                or trace.inputs["source_trace_hash"] != source.trace.trace_hash
                or request.reviewed_source_trace_hash != source.trace.trace_hash
                or message.source_kind != request.kind
                or source.semantic_key != message.semantic_key
                or source.attention != message.requires_user_attention
                or source.boundary is None
                or source.boundary["epoch_id"] != str(request.expected_epoch_id)
            ):
                raise ValueError("Global notification command lost its verified original source")
    except (KeyError, TypeError, ValueError) as error:
        raise _error("INTERVENTION_INTEGRITY_ERROR", "通知原命令或回执不一致") from error
    return receipt, message


def _question_observation_proof(
    session: Session,
    trace: DecisionTrace,
    receipt: InterventionReceipt,
    message: InterventionMessage,
    now: datetime,
) -> CurrentQuestionObservation:
    body = QuestionObservationRequest.model_validate_json(
        json.dumps(receipt.original_command["request"])
    )
    original = _trace(session, receipt.user_id, body.expected_run_id, now)
    state = QuestionRevision.model_validate_json(json.dumps(original.outcome["question_revision"]))
    if (
        message.source_kind != "QUESTION"
        or message.user_id != receipt.user_id
        or message.epoch_id != receipt.epoch_id
        or state.user_id != receipt.user_id
        or state.epoch_id != body.expected_epoch_id
        or state.session_id != body.session_id
        or state.revision != body.expected_revision
        or state.run_id != body.expected_run_id
        or state.as_of != original.as_of
        or state.pending_question is None
        or question_semantics(state) != message.semantic_key
        or original.algorithm_versions.get("question") != "full-one-question-v1"
        or original.trace_hash != body.reviewed_source_trace_hash
        or trace.parent_run_id != state.run_id
        or trace.inputs.get("source_trace_hash") != original.trace_hash
        or receipt.recorded_at < state.as_of
    ):
        raise ValueError("Observation does not prove the exact original question semantics")
    return CurrentQuestionObservation(
        observation_run_id=trace.run_id,
        observation_trace_hash=trace.trace_hash,
        original_receipt=receipt,
        session_id=state.session_id,
        revision=state.revision,
        source_run_id=state.run_id,
        source_trace_hash=original.trace_hash,
        semantic_key=message.semantic_key,
        current_question=state.pending_question,
    )


def _current_question_observation(
    session: Session,
    value: InterventionMessage,
    workflow: QuestionWorkflowResponse,
    now: datetime,
    questions: dict[UUID, QuestionWorkflowResponse] | None = None,
) -> CurrentQuestionObservation | None:
    # Semantic identity spans equivalent sessions. An immutable payload's session
    # cannot replace a new observation's exact current session.
    local = questions if questions is not None else {}
    local[workflow.current_revision.session_id] = workflow
    ids = list(
        session.scalars(
            select(DecisionRun.id)
            .where(
                DecisionRun.user_id == value.user_id,
                DecisionRun.input_snapshot["decision_trace"]["inputs"]["original_command"][
                    "kind"
                ].astext
                == "OBSERVE",
                DecisionRun.input_snapshot["decision_trace"]["inputs"]["original_command"][
                    "request"
                ]["kind"].astext
                == "QUESTION",
                DecisionRun.input_snapshot["decision_trace"]["outcome"]["receipt"][
                    "message_id"
                ].astext
                == str(value.message_id),
            )
            .order_by(DecisionRun.as_of, DecisionRun.id)
            .limit(MAX_MESSAGES + 1)
        )
    )
    if len(ids) > MAX_MESSAGES:
        raise _error("INTERVENTION_CAPACITY_EXCEEDED", "当前原观察超过512，不能截断为成功")
    proofs: list[CurrentQuestionObservation] = []
    for identity in ids:
        try:
            observed = _read_observe(session, value.user_id, identity, now)
            if observed is None:
                raise ValueError("Indexed observation is missing its original record")
            receipt, original_message = observed
            trace = _trace(session, value.user_id, identity, now)
            proof = _question_observation_proof(session, trace, receipt, original_message, now)
            if original_message != value:
                raise ValueError("Observation is not bound to this immutable semantic message")
            current = local.get(proof.session_id)
            if current is None:
                current = read_question_session(session, value.user_id, proof.session_id, now)
                local[proof.session_id] = current
            state = current.current_revision
            if (
                current.effective_state != "PENDING_ANSWER"
                or current.pending_question is None
                or state.user_id != value.user_id
                or state.epoch_id != value.epoch_id
                or proof.session_id != state.session_id
                or proof.revision != state.revision
                or proof.source_run_id != state.run_id
                or proof.current_question != current.pending_question
                or question_semantics(state) != value.semantic_key
            ):
                # A valid historical observation does not prove a later question.
                continue
        except (ValueError, KeyError, TypeError) as error:
            raise _error("INTERVENTION_INTEGRITY_ERROR", "新原观察与当前问题来源不一致") from error
        proofs.append(proof)
    return proofs[-1] if proofs else None


def _outbox(session: Session, user: UUID, identity: UUID) -> InterventionOutbox:
    row = session.scalar(
        select(InterventionOutbox).where(
            InterventionOutbox.id == identity, InterventionOutbox.user_id == user
        )
    )
    if row is None:
        raise PolicyLifecycleError("NOT_FOUND", "通知原件不存在", 404)
    return row


def _message(session: Session, row: InterventionOutbox, now: datetime) -> InterventionMessage:
    try:
        value = InterventionMessage.model_validate_json(json.dumps(row.payload))
        command = _read_observe(session, row.user_id, value.creation_command_run_id, now)
        if (
            command is None
            or command[1] != value
            or command[0].duplicate_semantics
            or configuration_hash(row.payload) != row.payload_hash
            or value.message_id != row.id
            or value.user_id != row.user_id
            or value.epoch_id != row.epoch_id
            or value.source_kind != row.source_kind
            or value.source_run_id != row.source_run_id
            or value.source_trace_hash != row.source_trace_hash
            or value.semantic_key != row.semantic_key
            or value.created_at != row.created_at
            or value.session_id != row.session_id
            or value.question_revision != row.question_revision
            or (value.question.question_id if value.question else None) != row.question_id
            or row.protocol_version != PROTOCOL
            or row.state
            not in {"PENDING", "ACKNOWLEDGED", "INVALIDATED", "RECORDED_ONLY", "DEFERRED"}
            or row.available_at < row.created_at
            or row.updated_at < row.created_at
        ):
            raise ValueError("Notification payload and original source metadata differ")
        original = _trace(session, row.user_id, value.source_run_id, now)
        if original.trace_hash != value.source_trace_hash:
            raise ValueError("Changed original source trace")
        if value.source_kind == "QUESTION":
            state = QuestionRevision.model_validate_json(
                json.dumps(original.outcome["question_revision"])
            )
            if (
                state.run_id != value.source_run_id
                or state.user_id != value.user_id
                or state.epoch_id != value.epoch_id
                or state.session_id != value.session_id
                or state.revision != value.question_revision
                or state.pending_question != value.question
                or question_semantics(state) != value.semantic_key
            ):
                raise ValueError("Payload question no longer matches its original worlds")
        else:
            source = (
                _global_boundary_source(session, row.user_id, value.source_run_id, now)
                if value.source_kind == "GLOBAL_ACTION_SET_BOUNDARY"
                else _boundary_source(session, row.user_id, value.source_run_id, now)
            )
            if (
                source.boundary != value.boundary_observation
                or source.semantic_key != value.semantic_key
                or source.attention != value.requires_user_attention
            ):
                raise ValueError("Payload boundary no longer matches its original economics")
    except (ValueError, KeyError, TypeError) as error:
        raise _error("INTERVENTION_INTEGRITY_ERROR", "通知原payload/hash/来源不一致") from error
    return value


def _inbox(session: Session, row: InterventionOutbox) -> InterventionInbox | None:
    inbox = session.scalar(
        select(InterventionInbox).where(
            InterventionInbox.outbox_id == row.id, InterventionInbox.user_id == row.user_id
        )
    )
    if inbox is not None and (
        inbox.id != uuid5(row.id, CONSUMER)
        or inbox.consumer_ref != CONSUMER
        or inbox.payload_hash != row.payload_hash
        or inbox.received_at < row.created_at
        or inbox.created_at > inbox.received_at
        or inbox.updated_at < inbox.received_at
        or inbox.state not in {"RECEIVED", "ACKNOWLEDGED", "INVALIDATED"}
    ):
        raise _error("INTERVENTION_INTEGRITY_ERROR", "原唯一收件回执不一致")
    return inbox


def _ack_receipt(
    inbox: InterventionInbox | None, row: InterventionOutbox
) -> InterventionReceipt | None:
    if inbox is None:
        if row.state == "ACKNOWLEDGED":
            raise _error("INTERVENTION_INTEGRITY_ERROR", "已收阅通知缺原收件回执")
        return None
    if inbox.state != "ACKNOWLEDGED":
        if (
            any(
                value is not None
                for value in (
                    inbox.acknowledged_at,
                    inbox.acknowledgment_key,
                    inbox.acknowledgment_request,
                    inbox.acknowledgment_request_hash,
                    inbox.original_receipt,
                )
            )
            or row.state == "ACKNOWLEDGED"
        ):
            raise _error("INTERVENTION_INTEGRITY_ERROR", "未收阅状态不能保留确认原件")
        return None
    try:
        receipt = InterventionReceipt.model_validate_json(json.dumps(inbox.original_receipt))
        if (
            receipt.kind != "ACKNOWLEDGE"
            or receipt.user_id != row.user_id
            or receipt.epoch_id != row.epoch_id
            or receipt.message_id != row.id
            or receipt.payload_hash != row.payload_hash
            or receipt.idempotency_key != inbox.acknowledgment_key
            or receipt.original_command != inbox.acknowledgment_request
            or receipt.request_hash != inbox.acknowledgment_request_hash
            or receipt.recorded_at != inbox.acknowledged_at
            or row.state != "ACKNOWLEDGED"
        ):
            raise ValueError("Acknowledgment lost its original exact command")
    except (ValueError, KeyError, TypeError) as error:
        raise _error("INTERVENTION_INTEGRITY_ERROR", "收阅原件与其hash/epoch不一致") from error
    return receipt


def _view(
    session: Session,
    row: InterventionOutbox,
    now: datetime,
    questions: dict[UUID, QuestionWorkflowResponse] | None = None,
) -> InterventionView:
    value = _message(session, row, now)
    inbox = _inbox(session, row)
    acknowledgment = _ack_receipt(inbox, row)
    if inbox is not None and inbox.received_at > now:
        raise _error("INTERVENTION_CLAIM_NOT_YET_OCCURRED", "原收件时钟晚于当前读取时点")
    source_status: Literal["CURRENT", "STALE", "UNKNOWN", "ARCHIVED"] = "CURRENT"
    current_question = None
    current_observation = None
    source_binding: Literal[
        "ORIGINAL_MESSAGE", "CURRENT_OBSERVATION", "LEGACY_TERMINAL_SOURCE", "UNVERIFIED"
    ] = "ORIGINAL_MESSAGE"
    epoch = current_audit_epoch(session, row.user_id)
    if epoch is None or epoch.id != row.epoch_id or epoch.status != "OPEN":
        source_status = "ARCHIVED"
        source_binding = "UNVERIFIED"
    elif value.source_kind == "GLOBAL_ACTION_SET_BOUNDARY":
        source_status = _global_source_status(session, value, now)
        if source_status != "CURRENT":
            source_binding = "UNVERIFIED"
    elif value.session_id is not None:
        # This dictionary lives only in this call's RO/RR transaction; it is never
        # stored on a Session, process, authorization context or across requests.
        local = questions if questions is not None else {}
        workflow = local.get(value.session_id)
        if workflow is None:
            workflow = read_question_session(session, row.user_id, value.session_id, now)
            local[value.session_id] = workflow
        current_question = workflow.pending_question
        if workflow.effective_state == "UNKNOWN":
            source_status = "UNKNOWN"
            source_binding = "UNVERIFIED"
        elif (
            workflow.effective_state != "PENDING_ANSWER"
            or workflow.current_revision.run_id != value.source_run_id
            or workflow.current_revision.revision != value.question_revision
            or workflow.pending_question != value.question
        ):
            source_status = "STALE"
            source_binding = "UNVERIFIED"
        if source_status != "CURRENT":
            current_observation = _current_question_observation(
                session, value, workflow, now, local
            )
            if current_observation is not None:
                source_status = "CURRENT"
                source_binding = "CURRENT_OBSERVATION"
                current_question = current_observation.current_question
        if row.state == "INVALIDATED":
            # Historical terminal metadata and consumer states are one-way under
            # 0012. A new observation never revives an old invalidated message.
            source_binding = "LEGACY_TERMINAL_SOURCE"
    state = effective_state(cast(State, row.state), source_status=source_status)
    return InterventionView(
        original_message=value,
        payload_hash=row.payload_hash,
        stored_state=cast(State, row.state),
        effective_state=state,
        source_status=source_status,
        available_at=row.available_at,
        pending=state == "PENDING" and value.requires_user_attention and now >= row.available_at,
        previously_claimed=inbox is not None,
        original_inbox_claim=InterventionInboxClaim(
            inbox_id=inbox.id,
            user_id=inbox.user_id,
            message_id=inbox.outbox_id,
            epoch_id=row.epoch_id,
            payload_hash=inbox.payload_hash,
            state=cast(Literal["RECEIVED", "ACKNOWLEDGED", "INVALIDATED"], inbox.state),
            created_at=inbox.created_at,
            received_at=inbox.received_at,
        )
        if inbox
        else None,
        current_question=current_question,
        original_acknowledgment=acknowledgment,
        current_question_observation=current_observation,
        current_source_binding=source_binding,
        global_boundary_subscription="EXPLICIT_OBSERVATION_ONLY"
        if value.source_kind == "GLOBAL_ACTION_SET_BOUNDARY"
        else "NOT_IMPLEMENTED",
    )


def read_intervention(
    session: Session, user: UUID, identity: UUID, now: datetime
) -> InterventionView:
    _snapshot(session)
    _owner(session, user)
    return _view(session, _outbox(session, user, identity), _now(now))


def list_interventions(
    session: Session, user: UUID, now: datetime, limit: int = 50
) -> InterventionList:
    _snapshot(session)
    _owner(session, user)
    if type(limit) is not int or not 1 <= limit <= MAX_MESSAGES:
        raise _error("INVALID_INTERVENTION_LIMIT", "展示数量必须在1..512内")
    rows = list(
        session.scalars(
            select(InterventionOutbox)
            .where(InterventionOutbox.user_id == user)
            .order_by(InterventionOutbox.created_at, InterventionOutbox.id)
            .limit(MAX_MESSAGES + 1)
        )
    )
    if len(rows) > MAX_MESSAGES:
        raise _error("INTERVENTION_CAPACITY_EXCEEDED", "完整通知原件超过512，不能截断成全量成功")
    questions: dict[UUID, QuestionWorkflowResponse] = {}
    views = [_view(session, row, _now(now), questions) for row in rows]
    return InterventionList(
        items=views[:limit],
        actual_message_count=len(views),
        presentation_truncated=len(views) > limit,
    )


def read_intervention_command(
    session: Session, user: UUID, epoch: UUID, key: str, now: datetime
) -> InterventionCommandLookup:
    _snapshot(session)
    _owner(session, user)
    CommandKeyAdapter.validate_python(key)
    now = _now(now)
    observation = _read_observe(session, user, command_identity(user, epoch, key), now)
    inbox = session.scalar(
        select(InterventionInbox).where(
            InterventionInbox.user_id == user, InterventionInbox.acknowledgment_key == key
        )
    )
    receipt = observation[0] if observation else None
    if inbox is not None:
        row = _outbox(session, user, inbox.outbox_id)
        if row.epoch_id == epoch:
            if receipt is not None:
                raise _error("INTERVENTION_INTEGRITY_ERROR", "同一原键出现两个通知命令")
            receipt = _ack_receipt(_inbox(session, row), row)
    return InterventionCommandLookup(
        status="RECORDED" if receipt else "NOT_FOUND_NOT_FINAL",
        epoch_id=epoch,
        idempotency_key=key,
        original_receipt=receipt,
        message=read_intervention(session, user, receipt.message_id, now) if receipt else None,
    )


def observe_intervention(
    engine: Engine, user: UUID, body: ObserveRequest, now: datetime
) -> InterventionCommandResponse:
    now = _now(now)
    original = {"kind": "OBSERVE", "user_id": str(user), "request": body.model_dump(mode="json")}
    digest = configuration_hash(original)
    identity = command_identity(user, body.expected_epoch_id, body.idempotency_key)
    with audit_command_guard(engine, user), Session(engine) as writer, writer.begin():
        _user(writer, user)
        _epoch(writer, user, body.expected_epoch_id)
        with _reader(engine) as reader:
            found = read_intervention_command(
                reader, user, body.expected_epoch_id, body.idempotency_key, now
            )
            if found.original_receipt is not None:
                if (
                    found.original_receipt.request_hash != digest
                    or found.original_receipt.kind != "OBSERVE"
                ):
                    raise _error("IDEMPOTENCY_CONFLICT", "通知原键不能换请求")
                assert found.message is not None
                return InterventionCommandResponse(
                    original_receipt=found.original_receipt,
                    message=found.message,
                    replayed_original_receipt=True,
                )
            if isinstance(body, QuestionObservationRequest):
                source = _question_source(reader, user, body, now)
            elif isinstance(body, GlobalBoundaryObservationRequest):
                source = _global_boundary_source(reader, user, body.observation_run_id, now)
            else:
                source = _boundary_source(reader, user, body.observation_run_id, now)
            if source.trace.trace_hash != body.reviewed_source_trace_hash:
                raise _error("INTERVENTION_SOURCE_HASH_MISMATCH", "通知必须复核确切原来源hash")
            if source.boundary and source.boundary["epoch_id"] != str(body.expected_epoch_id):
                raise _error("STALE_INTERVENTION_EPOCH", "原单动作事件不属于当前周期")
            policy = _policy(reader, user, body.intervention_policy_id, body.expected_epoch_id, now)
            semantic_id = message_identity(user, body.expected_epoch_id, source.semantic_key)
            replaced: list[UUID] = []
            if source.session_id is not None:
                previous_messages = list(
                    reader.scalars(
                        select(InterventionOutbox)
                        .where(
                            InterventionOutbox.user_id == user,
                            InterventionOutbox.epoch_id == body.expected_epoch_id,
                            InterventionOutbox.session_id == source.session_id,
                            InterventionOutbox.source_run_id != source.trace.run_id,
                            InterventionOutbox.id != semantic_id,
                            InterventionOutbox.state.in_(["PENDING", "DEFERRED"]),
                        )
                        .limit(MAX_MESSAGES + 1)
                    )
                )
                if len(previous_messages) > MAX_MESSAGES:
                    raise _error("INTERVENTION_CAPACITY_EXCEEDED", "替换原问题超过完整读取容量")
                for previous_message in previous_messages:
                    _message(reader, previous_message, now)
                    _ack_receipt(_inbox(reader, previous_message), previous_message)
                    replaced.append(previous_message.id)
            prior = reader.scalar(
                select(InterventionOutbox).where(
                    InterventionOutbox.id == semantic_id, InterventionOutbox.user_id == user
                )
            )
            message = (
                _message(reader, prior, now)
                if prior
                else InterventionMessage(
                    message_id=semantic_id,
                    user_id=user,
                    epoch_id=body.expected_epoch_id,
                    source_kind=body.kind,
                    source_run_id=source.trace.run_id,
                    source_trace_hash=source.trace.trace_hash,
                    semantic_key=source.semantic_key,
                    creation_command_run_id=identity,
                    session_id=source.session_id,
                    question_revision=source.revision,
                    question=source.question,
                    boundary_observation=source.boundary,
                    intervention_policy_binding=policy,
                    requires_user_attention=source.attention,
                    created_at=now,
                    global_action_set_complete=isinstance(body, GlobalBoundaryObservationRequest),
                )
            )
            duplicate = prior is not None
        for replaced_id in replaced:
            previous_row = _outbox(writer, user, replaced_id)
            previous_inbox = _inbox(writer, previous_row)
            previous_row.state = "INVALIDATED"
            previous_row.updated_at = now
            previous_row.invalidation_reason = "QUESTION_REVISION_REPLACED"
            if previous_inbox is not None and previous_inbox.state == "RECEIVED":
                previous_inbox.state = "INVALIDATED"
                previous_inbox.updated_at = now
        payload = message.model_dump(mode="json")
        payload_hash = configuration_hash(payload)
        if not duplicate:
            writer.add(
                InterventionOutbox(
                    id=message.message_id,
                    user_id=user,
                    created_at=now,
                    epoch_id=message.epoch_id,
                    protocol_version=PROTOCOL,
                    source_kind=message.source_kind,
                    source_run_id=message.source_run_id,
                    source_trace_hash=message.source_trace_hash,
                    semantic_key=message.semantic_key,
                    session_id=message.session_id,
                    question_id=message.question.question_id if message.question else None,
                    question_revision=message.question_revision,
                    payload=payload,
                    payload_hash=payload_hash,
                    state="PENDING" if message.requires_user_attention else "RECORDED_ONLY",
                    available_at=now,
                    updated_at=now,
                )
            )
        receipt = InterventionReceipt(
            kind="OBSERVE",
            user_id=user,
            epoch_id=body.expected_epoch_id,
            idempotency_key=body.idempotency_key,
            request_hash=digest,
            original_command=original,
            message_id=message.message_id,
            payload_hash=payload_hash,
            recorded_at=now,
            duplicate_semantics=duplicate,
        )
        trace = build_trace(
            run_id=identity,
            user_id=user,
            phase="EVALUATION",
            as_of=now,
            parent_run_id=source.trace.run_id,
            action_id=None,
            algorithm_versions={"trace": "decision-trace-v1", "intervention": ALGORITHM},
            inputs={"original_command": original, "source_trace_hash": source.trace.trace_hash},
            sources=[],
            policies=[],
            constraints=[],
            candidates=[],
            outcome={"receipt": receipt.model_dump(mode="json"), "message": payload},
        )
        record_trace(writer, trace)
    with _reader(engine) as reader:
        view = read_intervention(reader, user, message.message_id, now)
    return InterventionCommandResponse(
        original_receipt=receipt, message=view, replayed_original_receipt=False
    )


def _claim(
    session: Session, row: InterventionOutbox, now: datetime
) -> tuple[InterventionInbox, bool]:
    inbox = _inbox(session, row)
    if inbox is not None:
        _ack_receipt(inbox, row)
        return inbox, False
    inbox = InterventionInbox(
        id=uuid5(row.id, CONSUMER),
        user_id=row.user_id,
        created_at=now,
        outbox_id=row.id,
        consumer_ref=CONSUMER,
        payload_hash=row.payload_hash,
        state="RECEIVED",
        received_at=now,
        updated_at=now,
    )
    session.add(inbox)
    return inbox, True


def deliver_intervention(
    engine: Engine, user: UUID, identity: UUID, body: DeliveryRequest, now: datetime
) -> InterventionDeliveryResponse:
    now = _now(now)
    with audit_command_guard(engine, user), Session(engine) as writer, writer.begin():
        _user(writer, user)
        _epoch(writer, user, body.expected_epoch_id)
        row = _outbox(writer, user, identity)
        if row.epoch_id != body.expected_epoch_id or row.payload_hash != body.reviewed_payload_hash:
            raise _error("INTERVENTION_HASH_OR_EPOCH_MISMATCH", "收件必须绑定原通知hash及周期")
        with _reader(engine) as reader:
            view = read_intervention(reader, user, identity, now)
        if not view.pending:
            raise _error("INTERVENTION_NOT_CURRENT", "失效或已收阅通知不能再次弹出")
        inbox, first = _claim(writer, row, now)
        inbox_id, received_at = inbox.id, inbox.received_at
    with _reader(engine) as reader:
        view = read_intervention(reader, user, identity, now)
    return InterventionDeliveryResponse(
        message=view,
        inbox_id=inbox_id,
        original_received_at=received_at,
        present_once=first,
    )


def acknowledge_intervention(
    engine: Engine, user: UUID, identity: UUID, body: AcknowledgmentRequest, now: datetime
) -> InterventionCommandResponse:
    now = _now(now)
    original = {
        "kind": "ACKNOWLEDGE",
        "user_id": str(user),
        "message_id": str(identity),
        "request": body.model_dump(mode="json"),
    }
    digest = configuration_hash(original)
    with audit_command_guard(engine, user), Session(engine) as writer, writer.begin():
        _user(writer, user)
        _epoch(writer, user, body.expected_epoch_id)
        row = _outbox(writer, user, identity)
        if row.epoch_id != body.expected_epoch_id or row.payload_hash != body.reviewed_payload_hash:
            raise _error("INTERVENTION_HASH_OR_EPOCH_MISMATCH", "收阅必须绑定原通知hash及周期")
        with _reader(engine) as reader:
            existing = read_intervention_command(
                reader, user, body.expected_epoch_id, body.idempotency_key, now
            )
            if existing.original_receipt is not None:
                if (
                    existing.original_receipt.request_hash != digest
                    or existing.original_receipt.kind != "ACKNOWLEDGE"
                ):
                    raise _error("IDEMPOTENCY_CONFLICT", "收阅原键不能换请求或消息")
                assert existing.message is not None
                return InterventionCommandResponse(
                    original_receipt=existing.original_receipt,
                    message=existing.message,
                    replayed_original_receipt=True,
                )
            # ACK keys use a whole-user namespace, including retained old epochs.
            if (
                writer.scalar(
                    select(InterventionInbox.id).where(
                        InterventionInbox.user_id == user,
                        InterventionInbox.acknowledgment_key == body.idempotency_key,
                    )
                )
                is not None
            ):
                raise _error("IDEMPOTENCY_CONFLICT", "历史收阅原键不能用于另一个周期")
            view = read_intervention(reader, user, identity, now)
        if not view.pending:
            raise _error("INTERVENTION_NOT_CURRENT", "旧问题或已收阅原通知不能新收阅")
        inbox, _ = _claim(writer, row, now)
        if inbox.state != "RECEIVED":
            raise _error("INTERVENTION_NOT_CURRENT", "已失效收件不能复活")
        receipt = InterventionReceipt(
            kind="ACKNOWLEDGE",
            user_id=user,
            epoch_id=body.expected_epoch_id,
            idempotency_key=body.idempotency_key,
            request_hash=digest,
            original_command=original,
            message_id=identity,
            payload_hash=row.payload_hash,
            recorded_at=now,
            duplicate_semantics=False,
        )
        inbox.state = "ACKNOWLEDGED"
        inbox.updated_at = inbox.acknowledged_at = now
        inbox.acknowledgment_key = body.idempotency_key
        inbox.acknowledgment_request = original
        inbox.acknowledgment_request_hash = digest
        inbox.original_receipt = receipt.model_dump(mode="json")
        row.state = "ACKNOWLEDGED"
        row.updated_at = now
    with _reader(engine) as reader:
        view = read_intervention(reader, user, identity, now)
    return InterventionCommandResponse(
        original_receipt=receipt, message=view, replayed_original_receipt=False
    )
