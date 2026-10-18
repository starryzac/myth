"""Staged durable orchestration records over the existing audited evidence store."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from app.db.models import EvidenceItem
from app.domain.boundary_types import BoundaryModel
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash
from app.domain.standing_income_goal_scope import AnyGrant, parse_grant
from app.domain.zhiyu_standing_journal import StandingRun, grant_record_payload, verify_transition
from app.services.demo_console import _epoch
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_orchestration import _audit_healthy, _valid, serial_user, store
from pydantic import Field, StrictInt
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

MAX_RUN_RECORDS = 2048
PREFIX = "standing-v1:"


class StandingControl(BoundaryModel):
    protocol: Literal["zhiyu-next-standing-control-v1"] = "zhiyu-next-standing-control-v1"
    grant_id: UUID
    revision: Annotated[StrictInt, Field(ge=1, le=128)]
    expected_previous_revision: Annotated[StrictInt, Field(ge=0, le=127)]
    state: Literal["ACTIVE", "PAUSED", "REVOKED"]
    previous_record_hash: str | None = None
    client_request_id: UUID
    actor: LocalActorPrincipal
    recorded_at: datetime
    accepted: Literal[True] = True


def original_control(session: Session, user_id: UUID, grant_id: UUID) -> StandingControl:
    originals = []
    prefix = PREFIX + "control:" + str(grant_id) + ":"
    for row in _rows(session, user_id, prefix):
        current = StandingControl.model_validate_json(
            __import__("json").dumps(row.content["payload"])
        )
        require_local_user(current.actor, user_id, current.recorded_at)
        if (
            current.grant_id != grant_id
            or row.observed_at != current.recorded_at
            or row.content["key"] != prefix + str(current.revision)
            or current.expected_previous_revision != current.revision - 1
        ):
            raise ValueError("Control original identity differs")
        originals.append(current)
    previous = None
    for current in sorted(originals, key=lambda value: value.revision):
        if previous is None:
            if (
                current.revision != 1
                or current.state != "ACTIVE"
                or current.previous_record_hash is not None
            ):
                raise ValueError("Initial control must be the original USER grant")
        elif (
            current.revision != previous.revision + 1
            or current.previous_record_hash != configuration_hash(previous.model_dump(mode="json"))
            or current.recorded_at < previous.recorded_at
            or previous.state == "REVOKED"
        ):
            raise ValueError("Control chain differs or revoked authority was reused")
        previous = current
    if previous is None:
        raise PolicyLifecycleError("STANDING_CONTROL_MISSING", "持续安排控制原件缺失", 409)
    return previous


def lock_user_writer(session: Session, user_id: UUID) -> None:
    """Share the native bank's cross-process owner gate for control writers."""
    from app.services.execution import _lock_user

    _lock_user(session, user_id)


def persist_grant(
    engine: Engine,
    grant: AnyGrant,
    actor: LocalActorPrincipal,
    request_id: UUID,
    now: datetime,
) -> AnyGrant:
    """Persist a reviewed USER declaration. Native delegated authority stays false."""
    require_local_user(actor, grant.scope.user_id, now)
    if grant.actor_at_confirmation != actor or grant.confirmed_at != now:
        raise ValueError("Grant origin must be the currently authenticated server principal")
    user_id = grant.scope.user_id
    with serial_user(engine, user_id), Session(engine) as session, session.begin():
        lock_user_writer(session, user_id)
        _audit_healthy(session, user_id)
        if grant.scope.epoch_id != _epoch(session, user_id):
            raise ValueError("Grant epoch differs")
        store(
            session,
            user_id,
            "EVENT",
            PREFIX + "grant:" + str(grant.grant_id),
            grant_record_payload(grant),
            now,
        )
        control = StandingControl(
            grant_id=grant.grant_id,
            revision=1,
            expected_previous_revision=0,
            state="ACTIVE",
            client_request_id=request_id,
            actor=actor,
            recorded_at=now,
        )
        store(
            session,
            user_id,
            "EVENT",
            PREFIX + "control:" + str(grant.grant_id) + ":1",
            control.model_dump(mode="json"),
            now,
        )
    return grant


def control_grant(
    engine: Engine,
    user_id: UUID,
    grant_id: UUID,
    expected_revision: int,
    state: Literal["ACTIVE", "PAUSED", "REVOKED"],
    actor: LocalActorPrincipal,
    request_id: UUID,
    now: datetime,
) -> StandingControl:
    require_local_user(actor, user_id, now)
    with serial_user(engine, user_id), Session(engine) as session, session.begin():
        lock_user_writer(session, user_id)
        _audit_healthy(session, user_id)
        grant = original_grant(session, user_id, grant_id)
        commands = []
        # UUID identity is owned by this USER/epoch, not reusable on another grant.
        for row in _rows(session, user_id, PREFIX + "control:"):
            candidate = StandingControl.model_validate_json(
                __import__("json").dumps(row.content["payload"])
            )
            if candidate.client_request_id == request_id:
                commands.append(candidate)
        if commands:
            original_control(session, user_id, grant_id)
            if (
                len(commands) != 1
                or commands[0].grant_id != grant_id
                or commands[0].expected_previous_revision != expected_revision
                or commands[0].state != state
                or commands[0].actor.user_id != user_id
            ):
                raise PolicyLifecycleError(
                    "STANDING_CONTROL_REQUEST_CONFLICT", "原控制请求内容改变", 409
                )
            return commands[0]
        previous = original_control(session, user_id, grant_id)
        if previous.revision != expected_revision or previous.state == "REVOKED":
            raise PolicyLifecycleError(
                "STANDING_CONTROL_VERSION_CHANGED", "请重读当前持续安排状态", 409
            )
        if state == "ACTIVE" and not grant.scope.issued_at <= now < grant.scope.expires_at:
            raise PolicyLifecycleError("STANDING_SCOPE_EXPIRED", "原持续授权已过期", 409)
        current = StandingControl(
            grant_id=grant_id,
            revision=previous.revision + 1,
            expected_previous_revision=previous.revision,
            state=state,
            previous_record_hash=configuration_hash(previous.model_dump(mode="json")),
            client_request_id=request_id,
            actor=actor,
            recorded_at=now,
        )
        store(
            session,
            user_id,
            "EVENT",
            PREFIX + "control:" + str(grant_id) + ":" + str(current.revision),
            current.model_dump(mode="json"),
            now,
        )
    return current


def _rows(session: Session, user_id: UUID, prefix: str) -> list[EvidenceItem]:
    epoch_id = _epoch(session, user_id)
    result = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.user_id == user_id,
                EvidenceItem.source_type == "ZHIYU_NEXT_EVENT",
                EvidenceItem.content["key"].as_string().startswith(prefix, autoescape=True),
            )
            .order_by(EvidenceItem.created_at, EvidenceItem.id)
            .limit(MAX_RUN_RECORDS + 1)
        )
    )
    if len(result) > MAX_RUN_RECORDS:
        raise PolicyLifecycleError("STANDING_JOURNAL_CAPACITY", "持续安排记录超过完整读取容量", 409)
    for row in result:
        _valid(session, row, user_id, epoch_id)
    return result


def original_grant(session: Session, user_id: UUID, grant_id: UUID) -> AnyGrant:
    key = PREFIX + "grant:" + str(grant_id)
    rows = _rows(session, user_id, key)
    if len(rows) != 1 or rows[0].content["key"] != key:
        raise PolicyLifecycleError("STANDING_ORIGINAL_GRANT_MISSING", "请读取原持续授权", 409)
    payload = rows[0].content["payload"]
    if (
        payload.get("protocol") != "zhiyu-next-standing-grant-record-v1"
        or payload.get("native_bank_authority") is not False
        or set(payload) != {"protocol", "grant", "grant_hash", "native_bank_authority"}
        or payload["grant_hash"] != configuration_hash(payload["grant"])
    ):
        raise PolicyLifecycleError("STANDING_ORIGINAL_GRANT_INVALID", "原持续授权内容无法核验", 409)
    grant = parse_grant(__import__("json").dumps(payload["grant"]))
    if (
        grant.grant_id != grant_id
        or grant.scope.user_id != user_id
        or grant.scope.epoch_id != _epoch(session, user_id)
    ):
        raise PolicyLifecycleError(
            "STANDING_ORIGINAL_GRANT_INVALID", "持续授权用户或环境不匹配", 409
        )
    return grant


def original_runs(session: Session, user_id: UUID) -> list[StandingRun]:
    rows = _rows(session, user_id, PREFIX + "run:")
    histories: dict[str, list[StandingRun]] = {}
    for row in rows:
        run = StandingRun.model_validate_json(__import__("json").dumps(row.content["payload"]))
        expected_key = PREFIX + "run:" + run.source.event_key + ":" + str(run.revision)
        if (
            row.content["key"] != expected_key
            or run.source.user_id != user_id
            or run.source.epoch_id != _epoch(session, user_id)
            or run.source.environment_id != session.get_bind().engine.url.database
            or run.recorded_at != row.observed_at
        ):
            raise PolicyLifecycleError(
                "STANDING_RUN_IDENTITY_DIFFERS", "持续安排原事件身份不匹配", 409
            )
        histories.setdefault(run.source.event_key, []).append(run)
    latest = []
    for history in histories.values():
        previous = None
        for run in sorted(history, key=lambda value: value.revision):
            verify_transition(previous, run)
            previous = run
        assert previous is not None
        latest.append(previous)
    return sorted(latest, key=lambda value: value.source.event_key)


def append_run(engine: Engine, user_id: UUID, run: StandingRun, now: datetime) -> StandingRun:
    """Commit original state before returning; no execution or consent is emitted."""
    if run.recorded_at != now or run.source.user_id != user_id:
        raise ValueError("Run must use the server actor and clock")
    key = PREFIX + "run:" + run.source.event_key + ":" + str(run.revision)
    with serial_user(engine, user_id), Session(engine) as session, session.begin():
        lock_user_writer(session, user_id)
        _audit_healthy(session, user_id)
        rows = _rows(session, user_id, key)
        exact = [row for row in rows if row.content["key"] == key]
        if exact:
            if len(exact) != 1 or exact[0].content["payload"] != run.model_dump(mode="json"):
                raise PolicyLifecycleError(
                    "STANDING_RUN_REQUEST_CONFLICT", "原事件内容发生变化", 409
                )
            return run
        originals = original_runs(session, user_id)
        previous = next(
            (row for row in originals if row.source.event_key == run.source.event_key), None
        )
        verify_transition(previous, run)
        if run.grant_id is not None and run.grant_hash is not None:
            grant = original_grant(session, user_id, run.grant_id)
            if run.grant_hash != configuration_hash(grant.model_dump(mode="json")):
                raise PolicyLifecycleError(
                    "STANDING_GRANT_HASH_DIFFERS", "原持续授权hash不匹配", 409
                )
            if run.state == "ACCEPTED":
                control = original_control(session, user_id, grant.grant_id)
                if (
                    control.state != "ACTIVE"
                    or control.revision != run.control_revision_at_acceptance
                    or not grant.scope.issued_at <= now < grant.scope.expires_at
                ):
                    raise PolicyLifecycleError(
                        "STANDING_ACCEPTANCE_INACTIVE", "持续授权已暂停或改变", 409
                    )
        store(session, user_id, "EVENT", key, run.model_dump(mode="json"), now)
    return run
