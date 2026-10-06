"""Read-only container helper delivered as stdin; imports/calls no finance at module load."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CAPACITY_REVISION = "W1_CONTAINER_SNAPSHOT_512MIB_STREAM_V1"
DECODED_LIMIT = 512 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


def gzip_original512(data: bytes) -> bytes:
    """Bounded original complete bytes; stream gzip and verify CRC through EOF."""
    if len(data) > DECODED_LIMIT:
        raise ValueError("Original snapshot exceeds explicit 512MiB budget")
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as writer:
        for start in range(0, len(data), CHUNK_BYTES):
            writer.write(data[start : start + CHUNK_BYTES])
    compressed = output.getvalue()
    total = 0
    checksum = hashlib.sha256()
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as reader:
        while True:
            chunk = reader.read(CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > DECODED_LIMIT:
                raise ValueError("Decoded producer verification exceeds 512MiB budget")
            checksum.update(chunk)
    if total != len(data) or checksum.hexdigest() != hashlib.sha256(data).hexdigest():
        raise ValueError("Original gzip CRC/EOF/data verification differs")
    return compressed


def main(config: dict[str, Any]) -> int:
    """Root coordinator executes this only inside its freshly verified owned API ID."""
    root = Path("/workspace")
    sys.path.insert(0, str(root / "apps/api"))
    from app.db.base import Base
    from app.db.models import AuditEpoch, User
    from app.db.session import create_database_engine
    from app.db.settings import DatabaseSettings
    from app.db.testing import require_test_database
    from app.services.audit_chain import verify_audit_chain
    from sqlalchemy import select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    def sha(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    def encode(value: Any) -> bytes:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False
        ).encode("utf-8")

    def sources() -> dict[str, str]:
        result = {}
        for name, checksum in config["runtime_sources"].items():
            path = (root / name).resolve()
            require(
                path.is_relative_to(root) and path.is_file(),
                "Registered runtime source missing/escaped",
            )
            result[name] = sha(path.read_bytes())
            require(result[name] == checksum, "Runtime source differs from actual registered image")
        return result

    require(
        config.get("capacity_revision") == CAPACITY_REVISION
        and type(config.get("decoded_byte_budget")) is int
        and config["decoded_byte_budget"] == DECODED_LIMIT
        and type(config.get("stream_chunk_bytes")) is int
        and config["stream_chunk_bytes"] == CHUNK_BYTES,
        "Explicit 512MiB capacity revision configuration required",
    )
    database = config["database"]
    target = make_url(DatabaseSettings().database_url)
    # Guard before creating any engine or importing a writable runner.
    require(
        require_test_database(database) == database
        and database == "bf_test_" + config["owner_uuid"]
        and target.drivername == "postgresql+psycopg"
        and target.host == "127.0.0.1"
        and target.port == 54329
        and target.database == database
        and target.username == "bf_demo",
        "Actual namespace DSN escaped owned local test guard",
    )
    output: dict[str, Any] = {
        "protocol": "bounded-funds-container-snapshot512-v1",
        "run_id": config["run_id"],
        "owner_run_id": config["owner_run_id"],
        "owner_uuid": config["owner_uuid"],
        "database": database,
        "api_container_id": config["api_container_id"],
        "source_head": config["source_head"],
        "source_digest": config["source_digest"],
        "snapshot_helper_sha256": config["snapshot_helper_sha256"],
        "capacity_revision": CAPACITY_REVISION,
        "decoded_byte_budget": DECODED_LIMIT,
        "stream_chunk_bytes": CHUNK_BYTES,
        "started_at": datetime.now(UTC).isoformat(),
        "status": "INCOMPLETE",
        "observations": [],
    }
    engine = None
    try:
        output["runtime_source_before"] = sources()
        engine = create_database_engine(target.render_as_string(hide_password=False))
        with Session(engine) as session, session.begin():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            isolation: str = session.execute(text("SHOW transaction_isolation")).scalar_one()
            readonly: str = session.execute(text("SHOW transaction_read_only")).scalar_one()
            identity_query = (
                "SELECT current_database() AS database, "
                "inet_server_addr()::text AS server_address, "
                "inet_server_port() AS server_port, current_user AS database_user"
            )
            identity = dict(session.execute(text(identity_query)).mappings().one())
            require(
                isolation == "repeatable read" and readonly == "on",
                "Actual RR/read-only transaction missing",
            )
            require(
                identity["database"] == database
                and identity["server_address"] in {"127.0.0.1", "127.0.0.1/32"}
                and type(identity["server_port"]) is int
                and identity["server_port"] == 54329
                and identity["database_user"] == "bf_demo",
                "Actual SQL identity escaped db namespace",
            )
            output["actual_transaction"] = {
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
                "Physical24/model23 registry mismatch",
            )

            def rows() -> dict[str, list[dict[str, Any]]]:
                current = {
                    table.name: [
                        dict(row)
                        for row in session.execute(select(table).order_by(table.c.id)).mappings()
                    ]
                    for table in Base.metadata.sorted_tables
                }
                current["alembic_version"] = [
                    dict(row)
                    for row in session.execute(
                        text("SELECT version_num FROM alembic_version ORDER BY version_num")
                    ).mappings()
                ]
                return current

            data = rows()
            before = encode(data)
            require(len(before) <= DECODED_LIMIT, "Original snapshot exceeds bounded export budget")
            compressed = gzip_original512(before)
            output["snapshot"] = {
                "sha256": sha(compressed),
                "data_sha256": sha(before),
                "bytes": len(before),
                "compressed_bytes": len(compressed),
                "physical_query": physical_query,
                "physical_tables": physical,
                "row_counts": {name: len(items) for name, items in data.items()},
            }
            output["snapshot_gzip_base64"] = base64.b64encode(compressed).decode("ascii")
            users = list(session.scalars(select(User).order_by(User.id)))
            require(
                bool(users) and all(user.is_simulated for user in users),
                "Simulated user registry missing",
            )
            for user in users:
                epochs = list(
                    session.scalars(
                        select(AuditEpoch)
                        .where(AuditEpoch.user_id == user.id)
                        .order_by(AuditEpoch.epoch_number)
                    )
                )
                require(bool(epochs), "Legacy/no-epoch user cannot claim complete audit")
                for epoch in epochs:
                    original = verify_audit_chain(session, user.id, epoch.id)
                    output["observations"].append(
                        {
                            "user_id": str(user.id),
                            "epoch_id": str(epoch.id),
                            "epoch_number": epoch.epoch_number,
                            "epoch_status": epoch.status,
                            "verification": original.model_dump(mode="json"),
                        }
                    )
            after = encode(rows())
            output["snapshot_after_data_sha256"] = sha(after)
            require(before == after, "Original rows changed during actual read-only observer")
        output["runtime_source_after"] = sources()
        require(
            output["runtime_source_before"] == output["runtime_source_after"],
            "Runtime source drift",
        )
        output["status"] = (
            "PASSED"
            if all(row["verification"]["status"] == "VALID" for row in output["observations"])
            else "AUDIT_FAILED"
        )
    except Exception as error:
        output["status"] = "FAILED"
        message = str(error)
        if target.password:
            message = message.replace(target.password, "[REDACTED]")
        output["error"] = {"type": type(error).__name__, "message": message}
    finally:
        if engine is not None:
            engine.dispose()
        output["finished_at"] = datetime.now(UTC).isoformat()
    sys.stdout.buffer.write(encode(output) + b"\n")
    return 0 if output["status"] == "PASSED" else 1
