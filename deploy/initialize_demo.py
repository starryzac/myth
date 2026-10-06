"""Initialize an explicitly owned local demo database once; retain existing histories."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.db.base import Base  # noqa: E402
from app.db.session import create_database_engine  # noqa: E402
from app.db.settings import DatabaseSettings  # noqa: E402
from app.db.testing import require_test_database  # noqa: E402
from app.services.audit_chain import current_audit_epoch, verify_audit_chain  # noqa: E402
from app.services.demo_seed import DEMO_USER_ID, seed_demo  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def require_owned(engine: Engine) -> str:
    url = engine.url
    if url.drivername != "postgresql+psycopg" or url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Demo initialization requires its actual local PostgreSQL namespace")
    if url.port != 54329:
        raise ValueError("Demo initialization requires the original local54329 boundary")
    return require_test_database(url.database or "")


def initialize(engine: Engine) -> dict[str, Any]:
    name = require_owned(engine)
    # Separate startup serialization: never hold the financial reset lock while
    # calling its original seed service through another connection.
    lock = int.from_bytes(
        hashlib.sha256(f"demo-init-v1:{name}".encode()).digest()[:8], "big", signed=True
    )
    with engine.connect() as coordinator:
        coordinator.execute(text("SELECT pg_advisory_lock(:lock)"), {"lock": lock})
        coordinator.commit()
        try:
            with Session(engine) as session, session.begin():
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                session.execute(text("SET TRANSACTION READ ONLY"))
                counts = {
                    table.name: session.scalar(select(func.count()).select_from(table))
                    for table in Base.metadata.sorted_tables
                }
                populated = any(count for count in counts.values())
                if populated:
                    from app.db.models import User

                    user = session.get(User, DEMO_USER_ID)
                    epoch = current_audit_epoch(session, DEMO_USER_ID)
                    if user is None or not user.is_simulated or epoch is None:
                        raise ValueError(
                            "Nonempty database lacks the original owned demo; do not seed/reset"
                        )
                    audit = verify_audit_chain(session, DEMO_USER_ID, epoch.id)
                    if audit.status != "VALID":
                        raise ValueError(
                            "Existing demo audit cannot be verified; retain original history"
                        )
                    return {
                        "status": "EXISTING_HISTORY_RETAINED",
                        "database": name,
                        "epoch_id": str(epoch.id),
                        "rows_by_table": counts,
                        "audit": audit.model_dump(mode="json"),
                        "seed_called": False,
                    }
            summary = seed_demo(engine)
            return {
                "status": "FRESH_DEMO_INITIALIZED",
                "database": name,
                "seed_called": True,
                "summary": summary.model_dump(mode="json"),
            }
        finally:
            coordinator.execute(text("SELECT pg_advisory_unlock(:lock)"), {"lock": lock})
            coordinator.commit()


def main() -> int:
    engine = create_database_engine(DatabaseSettings().database_url)
    try:
        require_owned(engine)  # Guard the actual DSN before migrations can write anything.
        from alembic import command
        from alembic.config import Config

        configuration = Config(str(ROOT / "alembic.ini"))
        configuration.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
        configuration.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(configuration, "head")
        print(json.dumps(initialize(engine), ensure_ascii=False, indent=2))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
