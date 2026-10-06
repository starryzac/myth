from typing import Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from app.db import (  # noqa: F401 -- register all metadata
    catalog_models,
    full_joint_goal_execution_models,
    full_models,
    models,
)
from app.db.base import Base, MoneyCents, UTCDateTime
from app.db.session import create_database_engine
from app.db.settings import DatabaseSettings

config = context.config
database_url = config.get_main_option("sqlalchemy.url") or DatabaseSettings().database_url


def render_type(kind: str, item: object, autogen_context: AutogenContext) -> str | Literal[False]:
    if kind == "type" and isinstance(item, MoneyCents):
        return "sa.BigInteger()"
    if kind == "type" and isinstance(item, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


if context.is_offline_mode():
    context.configure(
        url=database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                compare_type=True,
                compare_server_default=True,
                render_item=render_type,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
