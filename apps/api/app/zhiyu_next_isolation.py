"""Exact ownership checks for the separate Zhiyu extension simulation environment."""

import os
import re
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import URL, Engine, make_url

from app.db.testing import require_test_database

VARIANT = "zhiyu-next"
ROUND_PATTERN = r"[0-9]{8}T[0-9]{6}Z-([0-9a-f]{32})"


def require_zhiyu_next_environment(environment: Mapping[str, str]) -> URL:
    """Reject inherited Demo credentials and partially declared extension environments."""
    expected = require_test_database(environment.get("ZHIYU_NEXT_DATABASE"))
    identity = expected.removeprefix("bf_test_")
    round_match = re.fullmatch(ROUND_PATTERN, environment.get("ZHIYU_NEXT_ROUND", ""))
    url = make_url(environment.get("DATABASE_URL", ""))
    if (
        environment.get("ZHIYU_NEXT_VARIANT") != VARIANT
        or environment.get("ZHIYU_NEXT_ENVIRONMENT_ID") != expected
        or environment.get("SIMULATION_MODE") != "true"
        or environment.get("ZHIYU_NEXT_WORKER_ENABLED", "false") not in {"false", "true"}
        or round_match is None
        or round_match.group(1) != identity
        or url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or url.port != 54329
        or url.database != expected
        or url.username != "zhiyu_next_" + identity
        or re.fullmatch(r"[0-9a-f]{48}", url.password or "") is None
        or url.query
    ):
        raise ValueError("Zhiyu extension requires its exact registered local simulation namespace")
    return url


def require_zhiyu_next_engine(engine: Engine, *, check_connection: bool = False) -> str:
    """Check declared credentials and optionally the effective PostgreSQL role and namespace."""
    declared = require_zhiyu_next_environment(os.environ)
    if engine.url != declared:
        raise ValueError("Actual engine differs from the declared Zhiyu extension connection")
    if check_connection:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            row = connection.execute(
                text(
                    "SELECT current_database(), current_user, rolsuper, rolcreatedb, "
                    "rolcreaterole, rolreplication, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
            if row[0] != declared.database or row[1] != declared.username or any(row[2:]):
                raise ValueError("PostgreSQL ownership or low privilege boundary does not match")
    return str(declared.database)
