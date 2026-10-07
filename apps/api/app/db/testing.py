"""Name guard used by the isolated simulated application and initializer."""

import re


def require_test_database(database: str | None) -> str:
    if database is None or re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is None:
        raise ValueError("Destructive operations require a generated bf_test_<32 hex> database")
    return database
