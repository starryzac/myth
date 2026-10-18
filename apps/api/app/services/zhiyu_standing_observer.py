"""Durably observe original events; this phase cannot accept or execute finance."""

from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.immutable_joint_archive_scope import immutable_joint_archive_scope
from app.domain.immutable_joint_validation_scope import immutable_joint_validation_scope
from app.domain.policy_configuration import configuration_hash
from app.domain.zhiyu_standing_journal import StandingRun, verify_transition
from app.services.demo_console import _epoch
from app.services.zhiyu_orchestration import _audit_healthy, serial_user, store
from app.services.zhiyu_standing_recording import recorded_native_replay_scope
from app.services.zhiyu_standing_sources import ObservedOriginal, read_original_sources
from app.services.zhiyu_standing_store import (
    MAX_RUN_RECORDS,
    PREFIX,
    _rows,
    original_control,
    original_grant,
    original_runs,
)
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def original_observation_payload(value: ObservedOriginal) -> dict[str, Any]:
    payload = value.model_dump(mode="json")
    if configuration_hash(value.original_source) != value.source.original_source_hash:
        raise ValueError("Complete original source hash differs")
    return {
        "protocol": "standing-observed-original-record-v1",
        "observation": payload,
        "observation_hash": configuration_hash(payload),
        "native_bank_authority": False,
    }


def observe_once(engine: Engine, user_id: UUID, grant_id: UUID, now: datetime) -> dict[str, Any]:
    """No native accept/execute callback exists in this observer."""
    require_zhiyu_next_engine(engine, check_connection=True)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Aware current server clock required")
    with serial_user(engine, user_id):
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                with (
                    Session(bind=connection) as read,
                    immutable_joint_validation_scope(),
                    immutable_joint_archive_scope(),
                    recorded_native_replay_scope(),
                ):
                    _audit_healthy(read, user_id)
                    grant = original_grant(read, user_id, grant_id)
                    control = original_control(read, user_id, grant_id)
                    _epoch(read, user_id, grant.scope.epoch_id)
                    current = original_runs(read, user_id)
                    if (
                        control.state != "ACTIVE"
                        or not grant.scope.issued_at <= now < grant.scope.expires_at
                    ):
                        return {
                            "status": "INACTIVE",
                            "state": control.state,
                            "new_events": [],
                            "native_bank_authority": False,
                        }
                    originals = read_original_sources(
                        read, grant.scope, str(engine.url.database), now
                    )
        previous = {row.source.event_key: row for row in current}
        new = [value for value in originals if value.source.event_key not in previous]
        if not new:
            return {"status": "IDLE", "new_events": [], "native_bank_authority": False}
        # Each newly observed event and its original source are atomically durable.
        # No preparation, reservation, consent, bank submission or budget acceptance.
        with Session(engine) as write, write.begin():
            _audit_healthy(write, user_id)
            actual_grant = original_grant(write, user_id, grant_id)
            actual_control = original_control(write, user_id, grant_id)
            if (
                actual_grant != grant
                or actual_control != control
                or actual_control.state != "ACTIVE"
            ):
                raise ValueError("Current original USER scope or control changed")
            _epoch(write, user_id, grant.scope.epoch_id)
            latest = {row.source.event_key: row for row in original_runs(write, user_id)}
            remaining = [value for value in new if value.source.event_key not in latest]
            if len(_rows(write, user_id, PREFIX + "run:")) + len(remaining) > MAX_RUN_RECORDS:
                raise ValueError("Complete journal record inventory exceeds current capacity")
            recorded = []
            for value in remaining:
                key = value.source.event_key
                if key in latest:
                    continue
                record = StandingRun(
                    source=value.source,
                    revision=1,
                    state="OBSERVED",
                    recorded_at=now,
                    grant_id=grant.grant_id,
                )
                verify_transition(None, record)
                store(
                    write,
                    user_id,
                    "EVENT",
                    PREFIX + "source:" + key,
                    original_observation_payload(value),
                    now,
                )
                store(
                    write,
                    user_id,
                    "EVENT",
                    PREFIX + "run:" + key + ":1",
                    record.model_dump(mode="json"),
                    now,
                )
                recorded.append(key)
        return {"status": "OBSERVED", "new_events": recorded, "native_bank_authority": False}
