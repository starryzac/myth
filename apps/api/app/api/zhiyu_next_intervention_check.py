"""Identity-only Next reminder check over the existing actual observation producer."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.api.global_boundary_postcommit import postcommit_global_observation
from app.db.models import DecisionRun
from app.domain.boundary_types import BoundaryModel
from app.domain.full_action_set_boundary import GlobalBoundaryObserveRequest
from app.domain.full_action_set_boundary_actual import (
    ALGORITHM,
    NAMESPACE,
    ActualGlobalBoundaryObservation,
)
from app.services.audit_chain import audit_read_scope
from app.services.demo_console import _epoch
from app.services.full_action_set_boundary_actual import (
    observe_actual_global_boundary,
    read_actual_global_boundary_observation,
)
from app.services.full_intervention import _policy, _reader
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_orchestration import (
    remember_operation,
    remember_rejection,
    replay_operation,
    serial_user,
)

PROTOCOL: Literal["zhiyu-next-intervention-check-v1"] = "zhiyu-next-intervention-check-v1"


class ReminderCheckResult(BoundaryModel):
    simulation: Literal[True] = True
    protocol: Literal["zhiyu-next-intervention-check-v1"] = PROTOCOL
    user_id: UUID
    epoch_id: UUID
    client_request_id: UUID
    observation_run_id: UUID
    kind: Literal["BoundaryCrossed", "BoundaryObserved"] | None
    source_complete: bool
    notification_status: Literal[
        "OBSERVED", "ORIGINAL_RECOVERED", "NOT_CROSSED", "SOURCE_UNVERIFIED", "NOT_VERIFIED"
    ]
    bank_authority: Literal[False] = False
    financial_write: Literal[False] = False
    authority_granted: Literal[False] = False
    delivered: Literal[False] = False
    acknowledged: Literal[False] = False


def _key(epoch: UUID, request_id: UUID) -> str:
    return f"zhiyu-next:{epoch}:intervention-check:{request_id}"


def _identity(user: UUID, epoch: UUID, request_id: UUID) -> UUID:
    return uuid5(NAMESPACE, f"{user}:{epoch}:{_key(epoch, request_id)}")


def _request(epoch: UUID) -> dict[str, Any]:
    return {"kind": "INTERVENTION_CHECK", "expected_epoch_id": str(epoch)}


def _summary(
    value: ActualGlobalBoundaryObservation, request_id: UUID, status: str
) -> dict[str, Any]:
    return ReminderCheckResult.model_validate(
        {
            "user_id": value.user_id,
            "epoch_id": value.epoch_id,
            "client_request_id": request_id,
            "observation_run_id": value.observation_run_id,
            "kind": value.kind,
            "source_complete": value.global_action_set_complete,
            "notification_status": status,
        }
    ).model_dump(mode="json")


def _original(
    session: Session, user: UUID, epoch: UUID, request_id: UUID, now: datetime
) -> ActualGlobalBoundaryObservation | None:
    identity = _identity(user, epoch, request_id)
    if session.get(DecisionRun, identity) is None:
        return None
    value = read_actual_global_boundary_observation(session, user, identity, now)
    if (
        value.user_id,
        value.epoch_id,
        value.original_request.expected_epoch_id,
        value.original_request.idempotency_key,
    ) != (user, epoch, epoch, _key(epoch, request_id)):
        raise PolicyLifecycleError(
            "INTERVENTION_CHECK_ORIGINAL_INVALID", "原提醒核对身份无法验真，请保留原定位", 409
        )
    return value


def _previous(session: Session, user: UUID, epoch: UUID, now: datetime) -> UUID | None:
    # An immutable server-selected chain, not a caller-controlled pointer or raw source.
    trace = DecisionRun.input_snapshot["decision_trace"]
    row = session.scalar(
        select(DecisionRun)
        .where(
            DecisionRun.user_id == user,
            trace["algorithm_versions"]["global_action_set"].astext == ALGORITHM,
            trace["inputs"]["original_request"]["expected_epoch_id"].astext == str(epoch),
            trace["inputs"]["original_request"]["idempotency_key"].astext.startswith(
                f"zhiyu-next:{epoch}:intervention-check:", autoescape=True
            ),
        )
        .order_by(DecisionRun.as_of.desc(), DecisionRun.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    prior = read_actual_global_boundary_observation(session, user, row.id, now)
    try:
        prior_request = UUID(
            prior.original_request.idempotency_key.removeprefix(
                f"zhiyu-next:{epoch}:intervention-check:"
            )
        )
    except ValueError as error:
        raise PolicyLifecycleError(
            "INTERVENTION_CHECK_ORIGINAL_INVALID", "上一原观察缺少原请求标识", 409
        ) from error
    if (
        (prior.user_id, prior.epoch_id) != (user, epoch)
        or prior.snapshot.as_of > now
        or prior.observation_run_id != _identity(user, epoch, prior_request)
    ):
        raise PolicyLifecycleError(
            "INTERVENTION_CHECK_ORIGINAL_INVALID", "上一原观察不属于当前本人周期或自然时钟", 409
        )
    return prior.observation_run_id


def read_reminder_check_operation(
    session: Session, user: UUID, epoch: UUID, request_id: UUID, now: datetime
) -> dict[str, Any] | None:
    """Read-only recovery even if the observation committed before operation metadata."""
    value = _original(session, user, epoch, request_id, now)
    if value is None:
        return None
    from app.zhiyu_next_isolation import require_zhiyu_next_engine

    connection = session.get_bind()
    environment = require_zhiyu_next_engine(connection.engine)
    return {
        "simulation": True,
        "client_request_id": str(request_id),
        "status": "COMPLETED",
        "environment_id": environment,
        "epoch_id": str(epoch),
        "result": _summary(value, request_id, "NOT_VERIFIED"),
    }


def check_current_reminders(
    engine: Engine, user: UUID, epoch: UUID, request_id: UUID, now: datetime, response: Response
) -> dict[str, Any]:
    original = _request(epoch)
    with serial_user(engine, user):
        # Original requests never reselect a parent, append a source or replay a producer.
        with _reader(engine) as read, audit_read_scope(read):
            _epoch(read, user, epoch)
            saved = replay_operation(read, user, request_id, original)
            if saved is not None:
                return saved
            retained = _original(read, user, epoch, request_id, now)
            if retained is not None:
                return _summary(retained, request_id, "NOT_VERIFIED")
        try:
            with _reader(engine) as read, audit_read_scope(read):
                if _policy(read, user, None, epoch, now) is None:
                    raise PolicyLifecycleError(
                        "INTERVENTION_POLICY_REQUIRED",
                        "请先确认一项当前有效的提醒规则，再核对当前提醒",
                        409,
                    )
                previous = _previous(read, user, epoch, now)
            value = observe_actual_global_boundary(
                engine,
                user,
                GlobalBoundaryObserveRequest(
                    expected_epoch_id=epoch,
                    previous_observation_run_id=previous,
                    idempotency_key=_key(epoch, request_id),
                ),
                now,
            )
        except PolicyLifecycleError as error:
            # Only a command with no committed original can be a durable rejection.
            with _reader(engine) as read, audit_read_scope(read):
                exists = _original(read, user, epoch, request_id, now)
            if exists is None:
                remember_rejection(engine, user, request_id, original, error, now)
            raise
        value = postcommit_global_observation(engine, user, value, now, response)
        result = _summary(
            value,
            request_id,
            response.headers.get("X-Global-Intervention-Status", "SOURCE_UNVERIFIED"),
        )
        # Notification failure is metadata; it cannot replace the committed observation.
        with Session(engine) as writer, writer.begin():
            saved = replay_operation(writer, user, request_id, original)
            if saved is not None:
                return saved
            remember_operation(writer, user, request_id, original, result, now)
        return result
