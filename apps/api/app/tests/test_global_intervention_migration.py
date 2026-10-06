"""Root-only actual migration candidate; schema fixture rows are not global observations."""

import json
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.full_models import InterventionOutbox
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_migrations import migrated_database as migrated_database
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_global_kind_upgrade_preserves_all_rows_and_downgrade_retention(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, config = migrated_database
    command.downgrade(config, "0013_full_asset_execution")
    seed_demo(engine)
    with Session(engine) as session, session.begin():
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        epoch_id = epoch.id
        for kind in ("QUESTION", "SINGLE_ACTION_BOUNDARY"):
            session.add(
                InterventionOutbox(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    epoch_id=epoch_id,
                    created_at=SEED_AS_OF,
                    source_kind=kind,
                    source_run_id=uuid4(),
                    source_trace_hash="a" * 64,
                    semantic_key=uuid4().hex * 2,
                    session_id=uuid4() if kind == "QUESTION" else None,
                    question_id=uuid4() if kind == "QUESTION" else None,
                    question_revision=1 if kind == "QUESTION" else None,
                    payload={"SCHEMA_ONLY": True, "global_action_set_complete": False},
                    payload_hash="c" * 64,
                    available_at=SEED_AS_OF,
                    updated_at=SEED_AS_OF,
                )
            )
    before = json.loads(physical_snapshot(engine))
    command.upgrade(config, "0014_global_notifications")
    after = json.loads(physical_snapshot(engine))
    assert set(after) == set(before)
    assert after["alembic_version"] != before["alembic_version"]
    for name, rows in before.items():
        if name != "alembic_version":
            assert after[name] == rows, name
    command.downgrade(config, "0013_full_asset_execution")
    assert json.loads(physical_snapshot(engine)) == before
    command.upgrade(config, "0014_global_notifications")
    identity = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            InterventionOutbox(
                id=identity,
                user_id=DEMO_USER_ID,
                epoch_id=epoch_id,
                created_at=SEED_AS_OF,
                source_kind="GLOBAL_ACTION_SET_BOUNDARY",
                source_run_id=uuid4(),
                source_trace_hash="d" * 64,
                semantic_key="e" * 64,
                session_id=None,
                question_id=None,
                question_revision=None,
                payload={"SCHEMA_ONLY": True, "bank_authority": False},
                payload_hash="f" * 64,
                available_at=SEED_AS_OF,
                updated_at=SEED_AS_OF,
            )
        )
    retained = physical_snapshot(engine)
    for sql in (
        "UPDATE intervention_outbox SET source_kind='SINGLE_ACTION_BOUNDARY' WHERE id=:id",
        "UPDATE intervention_outbox SET payload='{}'::jsonb WHERE id=:id",
        "DELETE FROM intervention_outbox WHERE id=:id",
        "TRUNCATE intervention_outbox,intervention_inbox",
        "INSERT INTO intervention_outbox (epoch_id,protocol_version,source_kind,"
        "source_run_id,source_trace_hash,semantic_key,session_id,question_id,"
        "question_revision,payload,payload_hash,state,available_at,updated_at,"
        "invalidation_reason,user_id,id,created_at) "
        "SELECT epoch_id,protocol_version,source_kind,source_run_id,source_trace_hash,"
        "repeat('0',64),gen_random_uuid(),question_id,question_revision,payload,"
        "payload_hash,state,available_at,updated_at,invalidation_reason,user_id,"
        "gen_random_uuid(),created_at "
        "FROM intervention_outbox WHERE id=:id",
    ):
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text(sql), {"id": identity})
        assert physical_snapshot(engine) == retained
    with pytest.raises(
        DBAPIError, match="Refusing to discard retained global notification history"
    ):
        command.downgrade(config, "0013_full_asset_execution")
    assert physical_snapshot(engine) == retained
