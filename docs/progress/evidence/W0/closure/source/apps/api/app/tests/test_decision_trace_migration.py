"""Real PostgreSQL migration exercises optional, tenant-bound decision links."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.db.base import Base
from app.db.models import Account, ActionPlan, DecisionRun, User
from app.domain.policy_configuration import configuration_hash
from app.tests.test_migrations import migrated_database as migrated_database
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_trace_migration_schema_roundtrip_preserves_existing_runs(
    migrated_database: tuple[Engine, object],
) -> None:
    engine, config = migrated_database
    inspector = inspect(engine)
    columns = {item["name"]: item for item in inspector.get_columns("decision_runs")}
    assert columns["parent_run_id"]["nullable"]
    assert columns["subject_action_plan_id"]["nullable"]
    foreign = inspector.get_foreign_keys("decision_runs")
    assert any(item["constrained_columns"] == ["parent_run_id", "user_id"] for item in foreign)
    assert any(
        item["constrained_columns"] == ["subject_action_plan_id", "user_id"] for item in foreign
    )
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    command.downgrade(config, "0004_execution_bank")  # type: ignore[arg-type]
    assert "parent_run_id" not in {
        item["name"] for item in inspect(engine).get_columns("decision_runs")
    }
    command.upgrade(config, "head")  # type: ignore[arg-type]
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_trace_links_reject_cross_user_parent_and_action(
    migrated_database: tuple[Engine, object],
) -> None:
    engine, _ = migrated_database
    first, second, account_id, parent_id, action_id = [uuid4() for _ in range(5)]
    now = datetime(2026, 10, 4, tzinfo=UTC)
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                User(id=first, external_ref="first-trace", display_name="Synthetic"),
                User(id=second, external_ref="second-trace", display_name="Synthetic"),
            ]
        )
        session.flush()
        session.add(Account(id=account_id, user_id=first, external_ref="cash", name="Synthetic"))
        session.add(
            DecisionRun(
                id=parent_id,
                user_id=first,
                idempotency_key="parent",
                trigger_type="TEST",
                algorithm_version="fixture",
                as_of=now,
                input_snapshot={},
                snapshot_hash=configuration_hash({}),
            )
        )
        session.flush()
        session.add(
            ActionPlan(
                id=action_id,
                user_id=first,
                decision_run_id=parent_id,
                source_account_id=account_id,
                action_type="TRANSFER_INTERNAL",
                amount_cents=1,
                idempotency_key="action",
                request={},
                request_hash=configuration_hash({}),
            )
        )
    for field, identity in [("parent_run_id", parent_id), ("subject_action_plan_id", action_id)]:
        with Session(engine) as session:
            session.add(
                DecisionRun(
                    user_id=second,
                    idempotency_key=field,
                    trigger_type="TEST",
                    algorithm_version="fixture",
                    as_of=now,
                    input_snapshot={},
                    snapshot_hash=configuration_hash({}),
                    **{field: identity},
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
