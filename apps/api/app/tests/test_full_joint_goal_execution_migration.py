"""Actual additive schema/retention proof; SCHEMA_ONLY rows grant no authority."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db.base import Base
from app.db.full_joint_goal_execution_models import (
    FullJointGoalExecutionChild,
    FullJointGoalExecutionConsent,
    FullJointGoalExecutionPlan,
)
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.full_policy_lifecycle import FullCreateRequest, confirm_full_policy
from app.tests.test_full_policy_lifecycle import confirm_body
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_migrations import migrated_database as migrated_database
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
TABLES = (
    "full_joint_goal_execution_plans",
    "full_joint_goal_execution_children",
    "full_joint_goal_execution_consents",
)


def test_actual_joint_upgrade_keeps_all_old_rows_and_originals_are_immutable(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, config = migrated_database
    command.downgrade(config, "0014_global_notifications")
    seed_demo(engine)
    with Session(engine) as session, session.begin():
        # This is an actual stored FULL reference for FK testing. The fixture
        # below is explicitly not a Joint producer plan or financial consent.
        full = confirm_full_policy(
            session, DEMO_USER_ID, FullCreateRequest.model_validate(confirm_body()), SEED_AS_OF
        )
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        epoch_id = epoch.id
        clock = epoch.opened_at + timedelta(seconds=1)
    before = json.loads(physical_snapshot(engine))
    command.upgrade(config, "0015_joint_goal_execution")
    after = json.loads(physical_snapshot(engine))
    assert set(after) - set(before) == set(TABLES)
    assert all(after[name] == [] for name in TABLES)
    for name, rows in before.items():
        if name != "alembic_version":
            assert after[name] == rows, name
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
    command.downgrade(config, "0014_global_notifications")
    assert json.loads(physical_snapshot(engine)) == before
    command.upgrade(config, "0015_joint_goal_execution")

    parent_id, child_id, consent_id, action_id = (uuid4() for _ in range(4))
    with Session(engine) as session, session.begin():
        session.add(
            FullJointGoalExecutionPlan(
                id=parent_id,
                user_id=DEMO_USER_ID,
                epoch_id=epoch_id,
                full_policy_id=full.policy_id,
                full_policy_version_id=full.version_id,
                created_at=clock,
                expires_at=clock + timedelta(minutes=5),
                idempotency_key="schema-only-joint-parent",
                request={"schema_only": True},
                request_hash="a" * 64,
                plan={"schema_only": True, "bank_authority": False},
                plan_hash="b" * 64,
            )
        )
        session.flush()
        session.add(
            FullJointGoalExecutionChild(
                id=child_id,
                user_id=DEMO_USER_ID,
                epoch_id=epoch_id,
                plan_id=parent_id,
                child_number=1,
                goal_id=uuid4(),
                original_mvp_version_id=uuid4(),
                action_plan_id=action_id,
                bank_idempotency_key="schema-only-joint-child-key",
                command={"schema_only": True, "bank_authority": False},
                command_hash="c" * 64,
                created_at=clock,
            )
        )
        session.add(
            FullJointGoalExecutionConsent(
                id=consent_id,
                user_id=DEMO_USER_ID,
                epoch_id=epoch_id,
                plan_id=parent_id,
                idempotency_key="schema-only-joint-consent",
                request={"schema_only": True},
                request_hash="d" * 64,
                plan_hash="b" * 64,
                evidence_id=uuid4(),
                evidence_hash="e" * 64,
                original_evidence={"schema_only": True, "grants_authority": False},
                created_at=clock,
            )
        )
    retained = physical_snapshot(engine)
    invalid = [f"UPDATE {name} SET created_at=created_at+interval '1 second'" for name in TABLES]
    invalid += [f"DELETE FROM {name}" for name in TABLES]
    invalid += ["TRUNCATE " + ",".join(TABLES)]
    clone = (
        "INSERT INTO full_joint_goal_execution_children "
        "(plan_id,epoch_id,child_number,goal_id,original_mvp_version_id,action_plan_id,"
        "bank_idempotency_key,command,command_hash,user_id,id,created_at) "
        "SELECT {parent},epoch_id,{number},goal_id,original_mvp_version_id,gen_random_uuid(),"
        "{key},command,command_hash,user_id,gen_random_uuid(),{clock} "
        "FROM full_joint_goal_execution_children"
    )
    for parent, number, key, created in (
        ("plan_id", "9", "'schema-invalid-number'", "created_at"),
        ("plan_id", "1", "'schema-duplicate-order'", "created_at"),
        ("gen_random_uuid()", "2", "'schema-missing-parent'", "created_at"),
        ("plan_id", "2", "'schema-expired-parent'", "created_at+interval '10 minutes'"),
        ("plan_id", "2", "'schema-child-before-parent'", "created_at-interval '1 second'"),
        ("plan_id", "2", "bank_idempotency_key", "created_at"),
    ):
        invalid.append(clone.format(parent=parent, number=number, key=key, clock=created))
    for sql in invalid:
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text(sql))
        assert physical_snapshot(engine) == retained
    with pytest.raises(DBAPIError, match="Refusing to discard retained joint execution originals"):
        command.downgrade(config, "0014_global_notifications")
    assert physical_snapshot(engine) == retained
