"""Real additive migration and immutable delivery originals in an isolated database."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db.base import Base
from app.db.full_models import InterventionInbox, InterventionOutbox
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_migrations import migrated_database as migrated_database
from sqlalchemy import MetaData, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_intervention_upgrade_preserves_every_row_and_retains_claims(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, config = migrated_database
    command.downgrade(config, "0011_transaction_category_audit")
    seed_demo(engine)
    before = json.loads(physical_snapshot(engine))
    command.upgrade(config, "0012_intervention_delivery")
    after = json.loads(physical_snapshot(engine))
    assert set(after) - set(before) == {"intervention_outbox", "intervention_inbox"}
    assert after["intervention_outbox"] == after["intervention_inbox"] == []
    for name, rows in before.items():
        if name != "alembic_version":
            assert after[name] == rows, name
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        # This node probes exactly 0012. The later additive 0013 metadata is
        # verified by its own actual migration node, without weakening any
        # original 0012 column/constraint or retention negative.
        additions = {
            "full_asset_execution_portfolios",
            "full_asset_execution_batches",
            "full_asset_execution_consents",
        }
        legacy_metadata = MetaData(naming_convention=Base.metadata.naming_convention)
        for name, table in Base.metadata.tables.items():
            if name not in additions:
                table.to_metadata(legacy_metadata)
        assert set(Base.metadata.tables) - set(legacy_metadata.tables) == additions
        assert compare_metadata(context, legacy_metadata) == []
    # An empty additive schema can be removed. All original rows remain exact.
    command.downgrade(config, "0011_transaction_category_audit")
    assert json.loads(physical_snapshot(engine)) == before
    command.upgrade(config, "0012_intervention_delivery")
    message_id, inbox_id = uuid4(), uuid4()
    source_id, session_id, question_id = uuid4(), uuid4(), uuid4()
    with Session(engine) as session, session.begin():
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        # Migration fixture, not a product-produced question or financial result.
        session.add(
            InterventionOutbox(
                id=message_id,
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                epoch_id=epoch.id,
                source_kind="QUESTION",
                source_run_id=source_id,
                source_trace_hash="a" * 64,
                semantic_key="b" * 64,
                session_id=session_id,
                question_id=question_id,
                question_revision=1,
                payload={"migration_fixture": True, "grants_authority": False},
                payload_hash="c" * 64,
                available_at=SEED_AS_OF,
                updated_at=SEED_AS_OF,
            )
        )
        session.flush()
        session.add(
            InterventionInbox(
                id=inbox_id,
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                outbox_id=message_id,
                payload_hash="c" * 64,
                received_at=SEED_AS_OF,
                updated_at=SEED_AS_OF,
            )
        )
    retained = physical_snapshot(engine)
    for sql in (
        "UPDATE intervention_outbox SET payload='{}'::jsonb WHERE id=:message",
        "UPDATE intervention_outbox SET semantic_key=repeat('d',64) WHERE id=:message",
        "UPDATE intervention_inbox SET payload_hash=repeat('d',64) WHERE id=:inbox",
        "UPDATE intervention_inbox SET consumer_ref='another-consumer' WHERE id=:inbox",
        "UPDATE intervention_inbox SET state='ACKNOWLEDGED' WHERE id=:inbox",
        "DELETE FROM intervention_outbox WHERE id=:message",
        "DELETE FROM intervention_inbox WHERE id=:inbox",
        "TRUNCATE intervention_outbox,intervention_inbox",
    ):
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text(sql), {"message": message_id, "inbox": inbox_id})
        assert physical_snapshot(engine) == retained
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE intervention_inbox SET state='ACKNOWLEDGED',updated_at=:now,"
                "acknowledged_at=:now,acknowledgment_key='migration-original-key',"
                "acknowledgment_request='{}'::jsonb,acknowledgment_request_hash=repeat('d',64),"
                "original_receipt='{}'::jsonb WHERE id=:inbox"
            ),
            {"now": SEED_AS_OF + timedelta(seconds=1), "inbox": inbox_id},
        )
        connection.execute(
            text(
                "UPDATE intervention_outbox SET state='ACKNOWLEDGED',updated_at=:now WHERE id=:id"
            ),
            {"now": SEED_AS_OF + timedelta(seconds=1), "id": message_id},
        )
    acknowledged = physical_snapshot(engine)
    for sql in (
        "UPDATE intervention_inbox SET original_receipt=jsonb_build_object('modified',true)",
        "UPDATE intervention_inbox SET state='RECEIVED',acknowledged_at=NULL,"
        "acknowledgment_key=NULL,acknowledgment_request=NULL,"
        "acknowledgment_request_hash=NULL,original_receipt=NULL",
        "UPDATE intervention_outbox SET state='PENDING'",
    ):
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text(sql))
        assert physical_snapshot(engine) == acknowledged
    with pytest.raises(DBAPIError, match="Refusing to discard retained intervention history"):
        command.downgrade(config, "0011_transaction_category_audit")
    assert physical_snapshot(engine) == acknowledged
