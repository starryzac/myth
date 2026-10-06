"""Explicit notification recovery process; no automatic delivery or finance."""

import argparse
import json
from datetime import UTC, datetime
from threading import Event
from uuid import UUID

from sqlalchemy import create_engine

from app.db.settings import DatabaseSettings
from app.services.question_intervention_recovery import recover_current_question_notifications


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--batch-size", type=int, choices=range(1, 33), default=8)
    parser.add_argument("--interval-seconds", type=int, choices=range(5, 301), default=30)
    args = parser.parse_args()
    settings = DatabaseSettings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    cursor = None
    stopped = Event()
    try:
        while not stopped.is_set():
            report = recover_current_question_notifications(
                engine,
                args.user_id,
                datetime.now(UTC),
                batch_size=args.batch_size,
                after_session_id=cursor,
            )
            # Only compact identities/statuses are exposed. Never print raw
            # Question source text, original connection URL or driver messages.
            print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False), flush=True)
            cursor = report.next_session_cursor
            if args.once:
                if report.status == "SOURCE_UNVERIFIED" or any(
                    item.status not in {"OBSERVED", "ORIGINAL_RECOVERED", "NOT_PENDING"}
                    for item in report.items
                ):
                    return 1
                return 2 if report.status == "MORE_PENDING" else 0
            stopped.wait(args.interval_seconds)
    except KeyboardInterrupt:
        stopped.set()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as error:
        print(json.dumps({"status": "WORKER_UNAVAILABLE", "error_type": type(error).__name__}))
        exit_code = 1
    raise SystemExit(exit_code)
