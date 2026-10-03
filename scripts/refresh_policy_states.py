"""Persist time-driven activation/expiry for the reserved simulated user."""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.db.session import create_database_engine, database_session  # noqa: E402
from app.domain.demo_identity import DEMO_USER_ID  # noqa: E402
from app.services.policy_lifecycle import refresh_time_states  # noqa: E402


def main() -> None:
    engine = create_database_engine()
    try:
        with database_session(engine) as session:
            result = refresh_time_states(session, DEMO_USER_ID, datetime.now(UTC))
        print(result.model_dump_json(indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
