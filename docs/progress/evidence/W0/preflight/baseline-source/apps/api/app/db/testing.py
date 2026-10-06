"""Disposable database isolation. Destructive commands never accept demo database names."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy.engine import make_url

from app.db.session import create_database_engine
from app.db.settings import DatabaseSettings


def require_test_database(database: str | None) -> str:
    if database is None or re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is None:
        raise ValueError("Destructive operations require a generated bf_test_<32 hex> database")
    return database


@contextmanager
def temporary_database() -> Iterator[str]:
    source = make_url(DatabaseSettings().database_url)
    name = require_test_database(f"bf_test_{uuid4().hex}")
    administration = create_database_engine(source.set(database="postgres").render_as_string(False))
    created = False
    try:
        with administration.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = True
        yield source.set(database=name).render_as_string(hide_password=False)
    finally:
        try:
            if created:
                require_test_database(name)
                with administration.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as connection:
                    connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        finally:
            administration.dispose()
