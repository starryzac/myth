"""Verify every retained epoch of a synthetic user in one read-only snapshot."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.db.models import AuditEpoch, AuditEvent, User  # noqa: E402
from app.db.session import create_database_engine  # noqa: E402
from app.domain.audit_chain import (  # noqa: E402
    build_checkpoint,
    checkpoint_canonical_text,
    parse_checkpoint,
)
from app.domain.demo_identity import DEMO_USER_ID  # noqa: E402
from app.services.audit_chain import get_audit_head, verify_audit_chain  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

CHECKPOINT_BYTE_LIMIT = 65536
EPOCH_LIMIT = 1000


def read_checkpoint(path: Path) -> str:
    with path.open("rb") as source:
        original = source.read(CHECKPOINT_BYTE_LIMIT + 1)
    if len(original) > CHECKPOINT_BYTE_LIMIT:
        raise ValueError("Checkpoint exceeds the byte limit")
    return original.decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=UUID, default=DEMO_USER_ID)
    parser.add_argument("--epoch-id", type=UUID)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--mode", choices=["PREFIX", "EXACT"], default="PREFIX")
    parser.add_argument("--checkpoint-output", type=Path)
    args = parser.parse_args(argv)
    try:
        checkpoint = (
            None if args.checkpoint is None else parse_checkpoint(read_checkpoint(args.checkpoint))
        )
        if checkpoint is not None and checkpoint.user_id != args.user_id:
            raise ValueError("Checkpoint belongs to another user")
        if args.checkpoint_output is not None and args.checkpoint_output.exists():
            raise ValueError("Checkpoint output already exists; preserve the trusted original")
    except (OSError, ValueError) as error:
        print(json.dumps({"simulation": True, "status": "INCOMPLETE", "error": str(error)}))
        return 2
    engine = None
    try:
        engine = create_database_engine()
        with Session(engine) as session:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            user = session.get(User, args.user_id)
            if user is None or not user.is_simulated:
                print(
                    json.dumps(
                        {"simulation": True, "status": "INCOMPLETE", "error": "USER_NOT_FOUND"}
                    )
                )
                return 2
            epoch_count = (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEpoch)
                    .where(AuditEpoch.user_id == args.user_id)
                )
                or 0
            )
            if epoch_count > EPOCH_LIMIT:
                print(
                    json.dumps(
                        {
                            "simulation": True,
                            "status": "INCOMPLETE",
                            "error": "EPOCH_LIMIT_EXCEEDED",
                            "registered_epoch_count": epoch_count,
                            "read_only": True,
                        }
                    )
                )
                return 2
            epochs = list(
                session.scalars(
                    select(AuditEpoch)
                    .where(
                        AuditEpoch.user_id == args.user_id,
                    )
                    .order_by(AuditEpoch.epoch_number)
                )
            )
            selected = [
                epoch for epoch in epochs if args.epoch_id is None or epoch.id == args.epoch_id
            ]
            issues = []
            previous = None
            for number, epoch in enumerate(epochs, 1):
                if epoch.epoch_number != number:
                    issues.append("EPOCH_SEQUENCE_GAP")
                if previous is None:
                    if epoch.previous_epoch_id is not None or epoch.previous_seal_hash is not None:
                        issues.append("GENESIS_EPOCH_LINK_INVALID")
                elif (
                    previous.status != "SEALED"
                    or epoch.previous_epoch_id != previous.id
                    or epoch.previous_seal_hash != previous.seal_hash
                ):
                    issues.append("PREVIOUS_EPOCH_SEAL_MISMATCH")
                previous = epoch
            results = [
                verify_audit_chain(
                    session,
                    args.user_id,
                    epoch.id,
                    checkpoint
                    if checkpoint is not None and checkpoint.epoch_id == epoch.id
                    else None,
                    args.mode,
                )
                for epoch in selected
            ]
            if not selected:
                results = [verify_audit_chain(session, args.user_id, args.epoch_id)]
            if checkpoint is not None and not any(
                epoch.id == checkpoint.epoch_id for epoch in selected
            ):
                issues.append("CHECKPOINT_EPOCH_NOT_SELECTED")
            legacy = (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.user_id == args.user_id,
                        AuditEvent.epoch_id.is_(None),
                    )
                )
                or 0
            )
            if legacy:
                issues.append("LEGACY_UNAUDITED_EVENTS_PRESENT")
            valid = (
                not issues
                and bool(selected)
                and all(result.status == "VALID" for result in results)
            )
            report = {
                "schema_version": "audit-cli-verification-v1",
                "simulation": True,
                "user_id": str(args.user_id),
                "status": "VALID" if valid else "NOT_VERIFIED",
                "verification_scope": (
                    "ALL_RETAINED_EPOCHS" if args.epoch_id is None else "SINGLE_EPOCH"
                ),
                "isolation": session.scalar(text("SHOW transaction_isolation")),
                "read_only": session.scalar(text("SHOW transaction_read_only")) == "on",
                "selected_epoch_count": len(selected),
                "registered_epoch_count": len(epochs),
                "legacy_event_count": legacy,
                "issues": issues,
                "checkpoint_origin": "CALLER_SUPPLIED" if checkpoint is not None else None,
                "epochs": [result.model_dump(mode="json") for result in results],
            }
            if valid and args.checkpoint_output is not None:
                head = get_audit_head(session, args.user_id, selected[-1].id)
                if head is None:
                    raise ValueError("Verified head disappeared inside one snapshot")
                saved = build_checkpoint(head, captured_at=datetime.now(UTC))
                args.checkpoint_output.parent.mkdir(parents=True, exist_ok=True)
                with args.checkpoint_output.open("x", encoding="utf-8") as output:
                    output.write(checkpoint_canonical_text(saved))
                report["checkpoint_output"] = str(args.checkpoint_output.resolve())
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if valid else 1
    except (SQLAlchemyError, OSError, ValueError) as error:
        print(
            json.dumps(
                {"simulation": True, "status": "INCOMPLETE", "error_type": type(error).__name__}
            )
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
