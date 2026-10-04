"""Import the deterministic demo into the configured, already migrated database."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.db.models import AuditEpoch, AuditEvent, AuditSubjectSnapshot  # noqa: E402
from app.db.session import create_database_engine  # noqa: E402
from app.domain.demo_identity import DEMO_USER_ID  # noqa: E402
from app.services.audit_chain import get_audit_head  # noqa: E402
from app.services.demo_seed import seed_demo  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def main() -> None:
    engine = create_database_engine()
    try:
        summary = seed_demo(engine)
        with Session(engine) as session:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            head = get_audit_head(session, DEMO_USER_ID)
            audit = {
                "schema_version": "seed-audit-observation-v1",
                "simulation": True,
                "user_id": str(DEMO_USER_ID),
                "read_at": datetime.now(UTC).isoformat(),
                "isolation": session.scalar(text("SHOW transaction_isolation")),
                "read_only": session.scalar(text("SHOW transaction_read_only")) == "on",
                "head": None if head is None else head.model_dump(mode="json"),
                "retained_epoch_count": session.scalar(
                    select(func.count())
                    .select_from(AuditEpoch)
                    .where(AuditEpoch.user_id == DEMO_USER_ID)
                ),
                "retained_event_count": session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.user_id == DEMO_USER_ID)
                ),
                "retained_snapshot_count": session.scalar(
                    select(func.count())
                    .select_from(AuditSubjectSnapshot)
                    .where(AuditSubjectSnapshot.user_id == DEMO_USER_ID)
                ),
            }
        print(
            json.dumps(
                {**summary.model_dump(mode="json"), "audit_metadata": audit},
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
