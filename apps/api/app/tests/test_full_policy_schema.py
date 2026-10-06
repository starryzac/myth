"""Additive FULL planning migration proof on an owned disposable PostgreSQL database."""

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.db.base import Base
from app.db.catalog_models import ProductCatalogVersion
from app.db.full_models import FullPolicy, FullPolicyCommand, FullPolicyVersion
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.services.demo_seed import seed_demo
from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[4]
FULL_TABLES = {
    FullPolicy.__tablename__,
    FullPolicyVersion.__tablename__,
    FullPolicyCommand.__tablename__,
}


def physical_originals(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        metadata = MetaData()
        metadata.reflect(connection)
        return {
            name: [
                dict(row)
                for row in connection.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for name, table in sorted(metadata.tables.items())
            if name != "alembic_version"
        }


@pytest.mark.integration
def test_full_policy_additive_migration_preserves_original26_tables_and_canonical_hashes() -> None:
    with temporary_database() as url:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "0008_command_delivery")
        engine = create_database_engine(url)
        try:
            seed_demo(engine)
            before = physical_originals(engine)
            assert len(before) == 26 and set(before).isdisjoint(FULL_TABLES)
            command.upgrade(config, "0009_full_policy_lifecycle")
            after = physical_originals(engine)
            assert {
                name: value for name, value in after.items() if name not in FULL_TABLES
            } == before
            assert len(after) == 29 and all(after[name] == [] for name in FULL_TABLES)
            assert set(inspect(engine).get_table_names()) == (
                set(Base.metadata.tables) - {ProductCatalogVersion.__tablename__}
            ) | {"alembic_version"}
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "0009_full_policy_lifecycle"
                )
                triggers = set(
                    connection.scalars(
                        text(
                            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                            "AND tgrelid IN ('full_policies'::regclass, "
                            "'full_policy_versions'::regclass, 'full_policy_commands'::regclass)"
                        )
                    )
                )
                assert triggers == {
                    "full_policy_identity",
                    "full_policy_retained",
                    "full_policy_versions_immutable",
                    "full_policy_commands_immutable",
                }
            for model in (FullPolicy, FullPolicyVersion, FullPolicyCommand):
                assert {
                    column["name"] for column in inspect(engine).get_columns(model.__tablename__)
                } == set(model.__table__.columns.keys())
        finally:
            engine.dispose()
