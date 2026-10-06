"""Actual additive migration: original financial rows and stored audit hashes stay intact."""

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.db.base import Base
from app.db.catalog_models import ProductCatalogVersion
from app.db.full_models import (
    CommandDeliveryAttempt,
    CommandInbox,
    CommandOutbox,
    FullPolicy,
    FullPolicyCommand,
    FullPolicyVersion,
)
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.services.demo_seed import seed_demo
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[4]
DELIVERY_TABLES = {
    CommandOutbox.__tablename__,
    CommandInbox.__tablename__,
    CommandDeliveryAttempt.__tablename__,
}
LATER_FULL_POLICY_TABLES = {
    FullPolicy.__tablename__,
    FullPolicyVersion.__tablename__,
    FullPolicyCommand.__tablename__,
    ProductCatalogVersion.__tablename__,
}


def originals(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            return {
                table.name: [
                    dict(row)
                    for row in connection.execute(select(table).order_by(table.c.id)).mappings()
                ]
                for table in Base.metadata.sorted_tables
                if table.name not in DELIVERY_TABLES | LATER_FULL_POLICY_TABLES
            }


@pytest.mark.integration
def test_additive_delivery_migration_preserves_all23_original_tables_and_hashes() -> None:
    with temporary_database() as url:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "0007_external_bank_facts")
        engine = create_database_engine(url)
        try:
            seed_demo(engine)
            before = originals(engine)
            assert len(before) == 23
            assert set(inspect(engine).get_table_names()) == set(before) | {"alembic_version"}
            command.upgrade(config, "0008_command_delivery")
            assert originals(engine) == before
            assert set(inspect(engine).get_table_names()) == (
                set(before) | DELIVERY_TABLES | {"alembic_version"}
            )
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "0008_command_delivery"
                )
                for name in sorted(DELIVERY_TABLES):
                    assert connection.scalar(text('SELECT count(*) FROM "' + name + '"')) == 0
            for model in (CommandOutbox, CommandInbox, CommandDeliveryAttempt):
                assert {
                    column["name"] for column in inspect(engine).get_columns(model.__tablename__)
                } == set(model.__table__.columns.keys())
        finally:
            engine.dispose()
