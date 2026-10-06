"""Post-commit Question notification producer; never answers, claims or acknowledges."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import verify_trace
from app.domain.full_intervention import QuestionObservationRequest, question_semantics
from app.domain.policy_configuration import configuration_hash
from app.domain.question_workflow import PROTOCOL as QUESTION_PROTOCOL
from app.services.audit_chain import audit_read_scope
from app.services.autonomy_envelope import _snapshot
from app.services.decision_trace import DecisionTraceResponse, get_decision_trace
from app.services.full_intervention import (
    InterventionCommandResponse,
    _reader,
    observe_intervention,
    read_intervention_command,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from app.services.question_workflow import QuestionWorkflowResponse, read_question_session
from sqlalchemy.engine import Engine

PROTOCOL = "question-intervention-producer-v1"
ProducerStatus = Literal[
    "OBSERVED",
    "ORIGINAL_RECOVERED",
    "NOT_PENDING",
    "STALE_BEFORE_OBSERVE",
    "SOURCE_UNVERIFIED",
    "OBSERVATION_OUTCOME_UNKNOWN",
    "ORIGINAL_MESSAGE_NOT_CURRENT",
]


class QuestionProducerResult(BoundaryModel):
    protocol: Literal["question-intervention-producer-v1"] = "question-intervention-producer-v1"
    simulation: Literal[True] = True
    user_id: UUID
    session_id: UUID
    status: ProducerStatus
    workflow_state: str | None = None
    request: QuestionObservationRequest | None = None
    request_hash: str | None = None
    pending_question_hash: str | None = None
    source_fingerprint: str | None = None
    original_response: InterventionCommandResponse | None = None
    error_code: str | None = None
    observation_attempted: bool = False
    delivered: Literal[False] = False
    acknowledged: Literal[False] = False
    actual_human_view_verified: Literal[False] = False
    answers_question: Literal[False] = False
    authority_granted: Literal[False] = False
    execution_eligible: Literal[False] = False


@dataclass(frozen=True)
class PreparedQuestionObservation:
    body: QuestionObservationRequest
    request_hash: str
    question_hash: str
    source_fingerprint: str
    semantic_key: str


def prepare_question_observation(
    user: UUID,
    session_id: UUID,
    workflow: QuestionWorkflowResponse,
    original: DecisionTraceResponse,
    intervention_policy_id: UUID | None = None,
) -> PreparedQuestionObservation:
    """Require actual original bindings, not a supplied success flag or latest receipt."""
    state = workflow.current_revision
    trace = original.trace
    if (
        workflow.effective_state != "PENDING_ANSWER"
        or workflow.pending_question is None
        or state.pending_question != workflow.pending_question
        or state.user_id != user
        or state.session_id != session_id
        or workflow.current_source_fingerprint != state.source_fingerprint
        or workflow.fresh_evaluation_at is None
        or original.completeness != "COMPLETE"
        or original.audit_chain_status != "VALID"
        or original.user_id != user
        or original.run_id != state.run_id
        or trace is None
        or trace.user_id != user
        or trace.run_id != state.run_id
        or trace.as_of != state.as_of
        or original.as_of != state.as_of
        or trace.phase != "EVALUATION"
        or trace.algorithm_versions.get("question") != QUESTION_PROTOCOL
        or trace.outcome.get("question_revision") != state.model_dump(mode="json")
    ):
        raise PolicyLifecycleError(
            "QUESTION_PRODUCER_SOURCE_UNVERIFIED", "原当前问题及轨迹未完整验真", 409
        )
    verify_trace(trace)
    semantic = question_semantics(state)
    question_hash = configuration_hash(workflow.pending_question.model_dump(mode="json"))
    identity = configuration_hash(
        {
            "protocol": PROTOCOL,
            "user_id": str(user),
            "epoch_id": str(state.epoch_id),
            "session_id": str(session_id),
            "revision": state.revision,
            "run_id": str(state.run_id),
            "pending_question_hash": question_hash,
        }
    )
    body = QuestionObservationRequest(
        kind="QUESTION",
        session_id=session_id,
        expected_revision=state.revision,
        expected_run_id=state.run_id,
        reviewed_source_trace_hash=trace.trace_hash,
        expected_epoch_id=state.epoch_id,
        intervention_policy_id=intervention_policy_id,
        idempotency_key="question-producer:" + identity,
    )
    envelope = {"kind": "OBSERVE", "user_id": str(user), "request": body.model_dump(mode="json")}
    return PreparedQuestionObservation(
        body, configuration_hash(envelope), question_hash, state.source_fingerprint, semantic
    )


def _read_current(
    engine: Engine,
    user: UUID,
    session_id: UUID,
    now: datetime,
    intervention_policy_id: UUID | None,
) -> tuple[str, PreparedQuestionObservation | None]:
    # Every call opens a distinct transaction. Nothing is retained on the engine,
    # Session, process or authorization context across requests.
    with _reader(engine) as session, audit_read_scope(session):
        _snapshot(session)
        workflow = read_question_session(session, user, session_id, now)
        if workflow.effective_state != "PENDING_ANSWER":
            return workflow.effective_state, None
        original = get_decision_trace(session, user, workflow.current_revision.run_id, now)
        prepared = prepare_question_observation(
            user, session_id, workflow, original, intervention_policy_id
        )
        _snapshot(session)
        return workflow.effective_state, prepared


def _match_response(
    user: UUID,
    prepared: PreparedQuestionObservation,
    response: InterventionCommandResponse,
) -> None:
    receipt = response.original_receipt
    if (
        receipt.kind != "OBSERVE"
        or receipt.user_id != user
        or receipt.epoch_id != prepared.body.expected_epoch_id
        or receipt.idempotency_key != prepared.body.idempotency_key
        or receipt.request_hash != prepared.request_hash
        or receipt.original_command
        != {
            "kind": "OBSERVE",
            "user_id": str(user),
            "request": prepared.body.model_dump(mode="json"),
        }
        or receipt.message_id != response.message.original_message.message_id
        or receipt.payload_hash != response.message.payload_hash
        or response.message.original_message.semantic_key != prepared.semantic_key
    ):
        raise PolicyLifecycleError(
            "QUESTION_PRODUCER_RECEIPT_MISMATCH", "原观察回执与确切请求不同", 409
        )


def produce_current_question_intervention(
    engine: Engine,
    user: UUID,
    session_id: UUID,
    now: datetime,
    *,
    intervention_policy_id: UUID | None = None,
) -> QuestionProducerResult:
    """A separate post-commit side effect. Failure cannot undo the Question command.

    This server-side seam accepts identities and the trusted server clock only.
    There is no automatic POST retry, fallback key, consumer claim or ACK. A
    caller must return the already committed Question receipt unchanged, and
    separately retain this diagnostic result when notification work fails.
    """
    prepared: PreparedQuestionObservation | None = None
    state: str | None = None
    attempted = False
    error_code: str | None = None
    response: InterventionCommandResponse | None = None
    recovered = False
    try:
        now = _now(now)
        state, prepared = _read_current(engine, user, session_id, now, intervention_policy_id)
        if prepared is None:
            return QuestionProducerResult(
                user_id=user, session_id=session_id, status="NOT_PENDING", workflow_state=state
            )
        second_state, second = _read_current(engine, user, session_id, now, intervention_policy_id)
        if second != prepared:
            return QuestionProducerResult(
                user_id=user,
                session_id=session_id,
                status="STALE_BEFORE_OBSERVE",
                workflow_state=second_state,
                request=prepared.body,
                request_hash=prepared.request_hash,
                pending_question_hash=prepared.question_hash,
                source_fingerprint=prepared.source_fingerprint,
            )
        attempted = True
        # observe_intervention locks the actual user, then freshly revalidates the
        # exact workflow under its original command transaction and semantic ID.
        response = observe_intervention(engine, user, prepared.body, now)
        _match_response(user, prepared, response)
    except Exception as error:
        # Post-commit notification failures never replace the original Question
        # receipt. Do not emit exception text: driver messages can contain secrets.
        error_code = error.code if isinstance(error, PolicyLifecycleError) else type(error).__name__
        if prepared is not None and attempted:
            try:
                with _reader(engine) as session, audit_read_scope(session):
                    lookup = read_intervention_command(
                        session,
                        user,
                        prepared.body.expected_epoch_id,
                        prepared.body.idempotency_key,
                        now,
                    )
                if lookup.original_receipt is not None and lookup.message is not None:
                    response = InterventionCommandResponse(
                        original_receipt=lookup.original_receipt,
                        message=lookup.message,
                        replayed_original_receipt=True,
                    )
                    _match_response(user, prepared, response)
                    recovered = True
            except Exception:
                response = None
        if not recovered:
            return QuestionProducerResult(
                user_id=user,
                session_id=session_id,
                status="OBSERVATION_OUTCOME_UNKNOWN" if attempted else "SOURCE_UNVERIFIED",
                workflow_state=state,
                request=prepared.body if prepared else None,
                request_hash=prepared.request_hash if prepared else None,
                pending_question_hash=prepared.question_hash if prepared else None,
                source_fingerprint=prepared.source_fingerprint if prepared else None,
                error_code=error_code,
                observation_attempted=attempted,
            )
    assert prepared is not None and response is not None
    status: ProducerStatus = "ORIGINAL_RECOVERED" if recovered else "OBSERVED"
    if response.message.source_status != "CURRENT":
        status = "ORIGINAL_MESSAGE_NOT_CURRENT"
    return QuestionProducerResult(
        user_id=user,
        session_id=session_id,
        status=status,
        workflow_state=state,
        request=prepared.body,
        request_hash=prepared.request_hash,
        pending_question_hash=prepared.question_hash,
        source_fingerprint=prepared.source_fingerprint,
        original_response=response,
        error_code=error_code,
        observation_attempted=attempted,
    )
