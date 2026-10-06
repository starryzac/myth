"""Exact local namespace guard for the dedicated Zhiyu simulation process."""

import os

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.testing import require_test_database


def require_zhiyu_engine(engine: Engine, *, check_connection: bool = False) -> str:
    expected = os.environ.get("ZHIYU_DEMO_DATABASE", "")
    require_test_database(expected)
    url = engine.url
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or url.port != 54329
        or url.database != expected
        or os.environ.get("SIMULATION_MODE", "true").lower() != "true"
    ):
        raise ValueError("Zhiyu requires the exact declared isolated local simulation database")
    if check_connection:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT current_database()")) != expected:
                raise ValueError("Actual PostgreSQL namespace does not match Zhiyu marker")
    return expected
