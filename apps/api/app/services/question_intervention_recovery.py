"""Recover missed post-commit notifications from verified original Question heads."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.db.models import User
from app.domain.boundary_types import BoundaryModel
from app.domain.question_workflow import QuestionRevision
from app.services.audit_chain import audit_read_scope, current_audit_epoch
from app.services.autonomy_envelope import _snapshot
from app.services.full_intervention import _reader
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from app.services.question_intervention_producer import (
    ProducerStatus,
    QuestionProducerResult,
    produce_current_question_intervention,
)
from app.services.question_workflow import _all_records, _heads
from pydantic import Field
from sqlalchemy.engine import Engine

MAX_BATCH = 32


class QuestionRecoveryItem(BoundaryModel):
    session_id: UUID
    status: ProducerStatus
    error_code: str | None = None
    observation_attempted: bool = False
    idempotency_key: str | None = None
    request_hash: str | None = None
    message_id: UUID | None = None


class QuestionRecoveryReport(BoundaryModel):
    protocol: Literal["original-question-notification-recovery-v1"] = (
        "original-question-notification-recovery-v1"
    )
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    status: Literal["COMPLETE_SCAN", "MORE_PENDING", "SOURCE_UNVERIFIED"]
    complete_registered_inventory: bool
    pending_original_sessions: int | None
    items: list[QuestionRecoveryItem] = Field(default_factory=list)
    next_session_cursor: UUID | None = None
    error_code: str | None = None
    answers_question: Literal[False] = False
    delivered: Literal[False] = False
    acknowledged: Literal[False] = False
    authority_granted: Literal[False] = False
    execution_eligible: Literal[False] = False


def _pending_heads(engine: Engine, user: UUID, now: datetime) -> list[QuestionRevision]:
    # Discovery is one complete, clean RO/RR read. A SQL prefix or cached list
    # cannot establish absence; the original reader retains its capacity checks,
    # exact audit verification and complete immutable revision chains.
    with _reader(engine) as session, audit_read_scope(session):
        _snapshot(session)
        owner = session.get(User, user)
        epoch = current_audit_epoch(session, user)
        if owner is None or not owner.is_simulated or epoch is None or epoch.status != "OPEN":
            raise PolicyLifecycleError("QUESTION_RECOVERY_SOURCE_UNVERIFIED", "当前模拟周期未验真")
        heads = _heads(_all_records(session, user, now))
        if any(head.user_id != user or head.epoch_id != epoch.id for head in heads):
            raise PolicyLifecycleError("QUESTION_RECOVERY_SOURCE_UNVERIFIED", "原问题归属不一致")
        _snapshot(session)
        return [head for head in heads if head.state == "PENDING_ANSWER"]


def recover_current_question_notifications(
    engine: Engine,
    user: UUID,
    now: datetime,
    *,
    batch_size: int = 8,
    after_session_id: UUID | None = None,
) -> QuestionRecoveryReport:
    """One bounded server invocation; every item gets the original fresh producer.

    A later worker cycle may re-observe the same *notification* command. Its
    original revision-derived key and semantic ID are unchanged; the original
    producer independently rechecks current sources and the original write fence.
    No finance command, question answer, DELIVER or ACK is retried here.
    """
    now = _now(now)
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH:
        raise ValueError("Question recovery batch_size must be an integer in 1..32")
    if not isinstance(user, UUID) or (
        after_session_id is not None and not isinstance(after_session_id, UUID)
    ):
        raise ValueError("Question recovery identities must be server UUIDs")
    try:
        heads = _pending_heads(engine, user, now)
        if len({head.session_id for head in heads}) != len(heads):
            raise ValueError("Original pending Question heads are not unique")
        ordered = sorted(heads, key=lambda head: head.session_id)
    except Exception as error:
        # Driver text, source bodies and connection credentials never enter logs.
        return QuestionRecoveryReport(
            user_id=user,
            as_of=now,
            status="SOURCE_UNVERIFIED",
            complete_registered_inventory=False,
            pending_original_sessions=None,
            error_code=error.code
            if isinstance(error, PolicyLifecycleError)
            else type(error).__name__,
        )
    remaining = [
        head for head in ordered if after_session_id is None or head.session_id > after_session_id
    ]
    selected = remaining[:batch_size]
    items = []
    for head in selected:
        try:
            result = produce_current_question_intervention(engine, user, head.session_id, now)
            # Do not accept a result attributed to another owner/session or a
            # copied unsafe DTO. The producer retains original source validation.
            result = QuestionProducerResult.model_validate_json(result.model_dump_json())
            if result.user_id != user or result.session_id != head.session_id:
                raise ValueError("Question producer owner/session mismatch")
            items.append(
                QuestionRecoveryItem(
                    session_id=head.session_id,
                    status=result.status,
                    error_code=result.error_code,
                    observation_attempted=result.observation_attempted,
                    idempotency_key=result.request.idempotency_key if result.request else None,
                    request_hash=result.request_hash,
                    message_id=(
                        result.original_response.original_receipt.message_id
                        if result.original_response
                        else None
                    ),
                )
            )
        except Exception as error:
            items.append(
                QuestionRecoveryItem(
                    session_id=head.session_id,
                    status="SOURCE_UNVERIFIED",
                    error_code=(
                        error.code
                        if isinstance(error, PolicyLifecycleError)
                        else type(error).__name__
                    ),
                )
            )
    more = len(remaining) > len(selected)
    return QuestionRecoveryReport(
        user_id=user,
        as_of=now,
        status="MORE_PENDING" if more else "COMPLETE_SCAN",
        complete_registered_inventory=True,
        pending_original_sessions=len(ordered),
        items=items,
        next_session_cursor=selected[-1].session_id if more else None,
    )
