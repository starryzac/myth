"""Actual additive schema and retention checks; fixture rows grant no financial authority."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db.base import Base
from app.db.catalog_models import ProductCatalogVersion
from app.db.full_models import (
    FullAssetExecutionBatch,
    FullAssetExecutionConsent,
    FullAssetExecutionPortfolio,
)
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.product_catalog import register_current_catalog
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_migrations import migrated_database as migrated_database
from sqlalchemy import MetaData, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_asset_execution_upgrade_preserves_old_rows_and_retains_originals(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, config = migrated_database
    command.downgrade(config, "0012_intervention_delivery")
    seed_demo(engine)
    before = json.loads(physical_snapshot(engine))
    command.upgrade(config, "0013_full_asset_execution")
    after = json.loads(physical_snapshot(engine))
    additions = {
        "full_asset_execution_portfolios",
        "full_asset_execution_batches",
        "full_asset_execution_consents",
    }
    assert set(after) - set(before) == additions
    assert all(after[name] == [] for name in additions)
    for name, rows in before.items():
        if name != "alembic_version":
            assert after[name] == rows, name
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        # This test deliberately stops at 0013. The three immutable joint
        # tables belong to 0015 and have their own head/retention test. Compare
        # every original table against its actual model without upgrading this
        # old migration's history or silently ignoring other schema changes.
        deferred_joint_tables = {
            "full_joint_goal_execution_plans",
            "full_joint_goal_execution_children",
            "full_joint_goal_execution_consents",
        }
        assert deferred_joint_tables <= set(Base.metadata.tables)
        asset_revision_metadata = MetaData(naming_convention=Base.metadata.naming_convention)
        for table in Base.metadata.sorted_tables:
            if table.name not in deferred_joint_tables:
                table.to_metadata(asset_revision_metadata)
        assert set(asset_revision_metadata.tables) == set(Base.metadata.tables) - (
            deferred_joint_tables
        )
        assert compare_metadata(context, asset_revision_metadata) == []
    command.downgrade(config, "0012_intervention_delivery")
    assert json.loads(physical_snapshot(engine)) == before
    command.upgrade(config, "0013_full_asset_execution")

    with Session(engine) as session, session.begin():
        register_current_catalog(session, DEMO_USER_ID, SEED_AS_OF)
    portfolio_id, batch_id, consent_id, future_action_id = (uuid4() for _ in range(4))
    with Session(engine) as session, session.begin():
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        product = session.scalar(select(ProductCatalogVersion).order_by(ProductCatalogVersion.id))
        assert epoch is not None and product is not None
        clock = epoch.opened_at + timedelta(seconds=1)
        # SCHEMA_ONLY: the real catalog identity is retained, but these explicit
        # migration fixture documents are not a product-produced plan or consent.
        session.add(
            FullAssetExecutionPortfolio(
                id=portfolio_id,
                user_id=DEMO_USER_ID,
                epoch_id=epoch.id,
                created_at=clock,
                expires_at=clock + timedelta(minutes=5),
                idempotency_key="schema-only-whole-original",
                request={"schema_only": True},
                request_hash="a" * 64,
                portfolio={"schema_only": True, "bank_authority": False},
                portfolio_hash="b" * 64,
            )
        )
        session.flush()
        session.add(
            FullAssetExecutionBatch(
                id=batch_id,
                user_id=DEMO_USER_ID,
                epoch_id=epoch.id,
                portfolio_id=portfolio_id,
                created_at=clock,
                batch_number=1,
                action_plan_id=future_action_id,
                bank_idempotency_key="schema-only-child-bank-original",
                command={"schema_only": True, "bank_authority": False},
                command_hash="c" * 64,
                catalogue_version_id=product.id,
                product_record_hash=product.product_hash,
            )
        )
        session.add(
            FullAssetExecutionConsent(
                id=consent_id,
                user_id=DEMO_USER_ID,
                epoch_id=epoch.id,
                portfolio_id=portfolio_id,
                created_at=clock,
                idempotency_key="schema-only-consent-original",
                request={"schema_only": True},
                request_hash="d" * 64,
                portfolio_hash="b" * 64,
                evidence_id=uuid4(),
                evidence_hash="e" * 64,
                original_evidence={"schema_only": True, "grants_authority": False},
            )
        )
    retained = physical_snapshot(engine)
    mutations = (
        [
            f"UPDATE {name} SET created_at=created_at+interval '1 second'"
            for name in sorted(additions)
        ]
        + [f"DELETE FROM {name}" for name in sorted(additions)]
        + [
            "TRUNCATE full_asset_execution_consents,full_asset_execution_batches,"
            "full_asset_execution_portfolios",
            "UPDATE full_asset_execution_portfolios SET portfolio='{}'::jsonb,"
            "portfolio_hash=repeat('f',64)",
            "UPDATE full_asset_execution_batches SET action_plan_id=gen_random_uuid()",
            "UPDATE full_asset_execution_consents SET original_evidence='{}'::jsonb,"
            "evidence_hash=repeat('f',64)",
        ]
    )
    # Explicit column lists avoid relying on ORM/physical column ordering.
    clone_batch = (
        "INSERT INTO full_asset_execution_batches "
        "(portfolio_id,epoch_id,batch_number,action_plan_id,bank_idempotency_key,command,"
        "command_hash,catalogue_version_id,product_record_hash,user_id,id,created_at) "
        "SELECT {parent},epoch_id,{order},gen_random_uuid(),{key},command,"
        "command_hash,catalogue_version_id,product_record_hash,user_id,"
        "gen_random_uuid(),{clock} FROM full_asset_execution_batches"
    )
    for parent, order, key, created in (
        ("portfolio_id", "5", "'schema-bad-order'", "created_at"),
        ("portfolio_id", "1", "'schema-duplicate-order'", "created_at"),
        ("gen_random_uuid()", "2", "'schema-missing-parent'", "created_at"),
        ("portfolio_id", "2", "'schema-expired-parent'", "created_at+interval '10 minutes'"),
        ("portfolio_id", "2", "'schema-child-before-parent'", "created_at-interval '1 second'"),
        ("portfolio_id", "2", "bank_idempotency_key", "created_at"),
    ):
        mutations.append(clone_batch.format(parent=parent, order=order, key=key, clock=created))
    for sql in mutations:
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text(sql))
        assert physical_snapshot(engine) == retained
    with pytest.raises(DBAPIError, match="Refusing to discard retained asset execution originals"):
        command.downgrade(config, "0012_intervention_delivery")
    assert physical_snapshot(engine) == retained
