"""The migration preserves old original rows and refuses to destroy retained history."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.models import AuditEvent, User
from app.domain.audit_chain import parse_event
from app.domain.audit_chain_types import AuditIntent
from app.services.audit_chain import (
    append_audit_event,
    ensure_audit_epoch,
    list_audit_events,
    verify_audit_chain,
)
from app.services.demo_seed import DEMO_USER_ID, DEMO_USER_REF, SEED_AS_OF, seed_demo
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_migrations import migrated_database as migrated_database
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_legacy_rows_survive_upgrade_without_fabricated_chain(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, config = migrated_database
    command.downgrade(config, "0005_decision_trace")
    identity, event_id = DEMO_USER_ID, uuid4()
    clock = datetime(2026, 10, 4, tzinfo=UTC)
    with Session(engine) as session, session.begin():
        session.add(
            User(
                id=identity,
                external_ref=DEMO_USER_REF,
                display_name="小钱（合成演示用户）",
                timezone="Asia/Shanghai",
                created_at=SEED_AS_OF,
            )
        )
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO audit_events(id,user_id,sequence_number,event_type,
            aggregate_type,aggregate_id,correlation_id,idempotency_key,payload,event_hash,occurred_at)
            VALUES (:id,:user,1,'LEGACY_TEST','USER',:user,:user,'legacy-original',
                    CAST(:payload AS jsonb),:digest,:clock)"""),
            {
                "id": event_id,
                "user": identity,
                "digest": "a" * 64,
                "clock": clock,
                "payload": '{"legacy":true}',
            },
        )
        original = connection.execute(text("SELECT * FROM audit_events")).mappings().one()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        preserved = (
            connection.execute(text("SELECT * FROM audit_events WHERE id=:id"), {"id": event_id})
            .mappings()
            .one()
        )
        assert {key: preserved[key] for key in original} == dict(original)
    with Session(engine) as session:
        listed = list_audit_events(session, identity)
        assert len(listed.items) == 1
        assert listed.items[0].completeness == "LEGACY_UNAUDITED"
        assert verify_audit_chain(session, identity).status == "LEGACY_UNAUDITED"
    with Session(engine) as session, session.begin():
        epoch = ensure_audit_epoch(session, identity, clock)
        genesis = session.get(AuditEvent, epoch.genesis_event_id)
        assert genesis is not None and genesis.canonical_text is not None
        event = parse_event(genesis.canonical_text)
        intent = AuditIntent.model_validate(
            event.model_dump(mode="python", include=set(AuditIntent.model_fields))
        )
        assert append_audit_event(session, intent, clock, clock).id == event.id
    with Session(engine) as session:
        activated = verify_audit_chain(session, identity)
        assert activated.chain_status == "VALID"
        assert activated.status == activated.reference_status == "LEGACY_UNAUDITED"
    summary = seed_demo(engine, reset_key="legacy-reset")
    before_retry = database_snapshot(engine)
    assert seed_demo(engine, reset_key="legacy-reset") == summary
    assert database_snapshot(engine) == before_retry
    with Session(engine) as session:
        reset = verify_audit_chain(session, identity)
        assert reset.chain_status == "VALID"
        assert reset.status == reset.reference_status == "LEGACY_UNAUDITED"
        assert session.get(AuditEvent, event_id) is not None


def test_downgrade_refuses_retained_history_without_changing_database(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, config = migrated_database
    seed_demo(engine)
    before = database_snapshot(engine)
    with pytest.raises(ValueError, match="history cannot be discarded"):
        command.downgrade(config, "0005_decision_trace")
    assert database_snapshot(engine) == before
    with Session(engine) as session:
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
