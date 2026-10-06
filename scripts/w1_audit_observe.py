"""Observe every actual epoch in a currently owned browser database, strictly read-only."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--browser-manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.run:
        print("PREPARED_NOT_EXECUTED: no database connection")
        return 0
    owner_path = (ROOT / args.browser_manifest).resolve()
    destination = (ROOT / args.output).resolve()
    require(
        owner_path.is_relative_to((ROOT / "output/playwright").resolve())
        and owner_path.name == "manifest.json",
        "Only the owned browser manifest is admitted",
    )
    require(
        destination.is_relative_to((ROOT / "docs/progress/evidence/W1").resolve())
        and not destination.exists(),
        "Fresh W1 observation output required",
    )
    owner_raw = owner_path.read_bytes()
    owner = json.loads(owner_raw)
    require(
        owner["run_id"] == owner_path.parent.name
        and owner["status"] == "INCOMPLETE"
        and owner["temporary_database_exited"] is False,
        "Browser database must still be owned and running",
    )
    loader = runpy.run_path(str(ROOT / "scripts/w1_browser_acceptance.py"))
    source_state = loader["source_state"]
    require(source_state() == owner["source_before"], "Browser source drift")
    purpose = owner.get("purpose", "DEVELOPMENT")
    acceptance = owner.get("acceptance_context")
    require(purpose in {"DEVELOPMENT", "MVP_ACCEPTANCE"}, "Unknown original browser purpose")
    if purpose == "MVP_ACCEPTANCE":
        require(isinstance(acceptance, dict), "Original final-check context is missing")
        current = runpy.run_path(str(ROOT / "scripts/w1_final_acceptance.py"))["source_state"](ROOT)
        binding = runpy.run_path(str(ROOT / "scripts/w1_current_check_context.py"))["bind_context"](
            ROOT, str(ROOT / acceptance["path"]), current
        )
        require(binding == acceptance, "Original parent context bytes/source differ")
    else:
        require(acceptance is None, "Development cannot inherit an acceptance binding")
    sys.path.insert(0, str(ROOT / "apps/api"))
    from app.db import models as models
    from app.db.base import Base
    from app.db.models import AuditEpoch, User
    from app.db.session import create_database_engine
    from app.db.settings import DatabaseSettings
    from app.db.testing import require_test_database
    from app.services.audit_chain import verify_audit_chain
    from sqlalchemy import select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    base = make_url(DatabaseSettings().database_url)
    database = require_test_database(owner["database"])
    require(re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is not None, "Owned UUID database")
    advertised = make_url(owner["database_url_redacted"])
    require(
        base.drivername == advertised.drivername == "postgresql+psycopg"
        and base.host == advertised.host == "127.0.0.1"
        and base.port == advertised.port == 54329
        and advertised.database == database
        and advertised.username == base.username,
        "Observation target escaped the owner's exact local engine",
    )
    destination.mkdir(parents=True)
    owner_copy = destination / "owner-manifest-initial.json"
    owner_copy.write_bytes(owner_raw)
    manifest: dict[str, Any] = {
        "protocol": "bounded-funds-audit-observation-v1",
        "status": "INCOMPLETE",
        "run_id": destination.name,
        "owner_run_id": owner["run_id"],
        "purpose": purpose,
        "acceptance_context": acceptance,
        "owner_manifest_path": str(owner_path.relative_to(ROOT)),
        "owner_manifest_initial_sha256": digest(owner_raw),
        "owner_manifest_initial_copy": str(owner_copy.relative_to(ROOT)),
        "producer_path": "scripts/w1_audit_observe.py",
        "producer_sha256": digest(Path(__file__).read_bytes()),
        "source_before": source_state(),
        "started_at": datetime.now(UTC).isoformat(),
        "database": database,
        "client_target": {"host": base.host, "port": base.port, "database": database},
        "observations": [],
        "formal_database_touched": False,
    }
    engine = create_database_engine(
        base.set(database=database).render_as_string(hide_password=False)
    )
    try:
        with Session(engine) as session, session.begin():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            isolation: str = session.execute(text("SHOW transaction_isolation")).scalar_one()
            readonly: str = session.execute(text("SHOW transaction_read_only")).scalar_one()
            require(isolation == "repeatable read" and readonly == "on", "Actual read-only RR")
            identity_query = (
                "SELECT current_database() AS database, "
                "inet_server_addr()::text AS server_address, "
                "inet_server_port() AS server_port, current_user AS database_user"
            )
            identity = dict(session.execute(text(identity_query)).mappings().one())
            require(identity["database"] == database, "Actual SQL connected to another database")
            manifest["actual_transaction"] = {
                "isolation_query": "SHOW transaction_isolation",
                "isolation": isolation,
                "readonly_query": "SHOW transaction_read_only",
                "readonly": readonly,
                "identity_query": identity_query,
                "identity_rows": [identity],
            }
            physical_query = (
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
            )
            physical: list[str] = list(session.execute(text(physical_query)).scalars())
            require(
                set(physical) == set(Base.metadata.tables) | {"alembic_version"}
                and len(physical) == 24,
                "Actual physical24/model23 mismatch",
            )

            def rows() -> dict[str, list[dict[str, Any]]]:
                actual = {
                    table.name: [
                        dict(row)
                        for row in session.execute(select(table).order_by(table.c.id)).mappings()
                    ]
                    for table in Base.metadata.sorted_tables
                }
                actual["alembic_version"] = [
                    dict(row)
                    for row in session.execute(
                        text("SELECT version_num FROM alembic_version ORDER BY version_num")
                    ).mappings()
                ]
                return actual

            before = encode(rows())
            raw_snapshot = gzip.compress(before, mtime=0)
            (destination / "snapshot-before.json.gz").write_bytes(raw_snapshot)
            manifest["snapshot"] = {
                "path": str((destination / "snapshot-before.json.gz").relative_to(ROOT)),
                "sha256": digest(raw_snapshot),
                "data_sha256": digest(before),
                "physical_tables": physical,
                "physical_query": physical_query,
                "row_counts": {name: len(items) for name, items in json.loads(before).items()},
                "bytes": len(before),
            }
            users = list(session.scalars(select(User).order_by(User.id)))
            require(
                bool(users) and all(user.is_simulated for user in users), "Actual simulated users"
            )
            for user in users:
                epochs = list(
                    session.scalars(
                        select(AuditEpoch)
                        .where(AuditEpoch.user_id == user.id)
                        .order_by(AuditEpoch.epoch_number)
                    )
                )
                require(bool(epochs), "Legacy/no-epoch user cannot produce complete audit proof")
                for epoch in epochs:
                    result = verify_audit_chain(session, user.id, epoch.id)
                    manifest["observations"].append(
                        {
                            "user_id": str(user.id),
                            "epoch_id": str(epoch.id),
                            "epoch_number": epoch.epoch_number,
                            "epoch_status": epoch.status,
                            "verification": result.model_dump(mode="json"),
                        }
                    )
            after = encode(rows())
            require(before == after, "Read-only observation changed original tables")
            manifest["snapshot_after_data_sha256"] = digest(after)
        manifest["source_after"] = source_state()
        require(manifest["source_before"] == manifest["source_after"], "Source drift")
        manifest["producer_after_sha256"] = digest(Path(__file__).read_bytes())
        require(manifest["producer_sha256"] == manifest["producer_after_sha256"], "Producer drift")
        manifest["status"] = (
            "PASSED"
            if all(row["verification"]["status"] == "VALID" for row in manifest["observations"])
            else "AUDIT_FAILED"
        )
    except Exception as error:
        manifest["status"] = "FAILED"
        message = str(error)
        if base.password:
            message = message.replace(base.password, "[REDACTED]")
        manifest["error"] = {"type": type(error).__name__, "message": message}
    finally:
        engine.dispose()
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        manifest["artifact_hashes"] = {
            path.name: digest(path.read_bytes()) for path in destination.iterdir() if path.is_file()
        }
        (destination / "manifest.json").write_bytes(encode(manifest))
    print(f"AUDIT_OBSERVATION={manifest['status']}; output={destination.relative_to(ROOT)}")
    return 0 if manifest["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
