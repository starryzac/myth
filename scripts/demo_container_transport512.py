"""Explicit 512MiB stdin snapshot capacity revision; pure builder/decoder only."""

from __future__ import annotations

import ast
import base64
import gzip
import io
import json
import re
import runpy
from pathlib import Path
from typing import Any, cast

from scripts.demo_container_transport import (
    DecodedSnapshot,
    OwnedTransport,
    PreparedCommand,
    _argv,
    _nonfinite,
    _pairs,
    list_value,
    object_value,
    original_json,
    registered_business_tables,
    require,
    sha,
)

ROOT = Path(__file__).resolve().parents[1]
CAPACITY_REVISION = "W1_CONTAINER_SNAPSHOT_512MIB_STREAM_V1"
DECODED_LIMIT = 512 * 1024 * 1024
COMPRESSED_LIMIT = 512 * 1024 * 1024
FRAME_LIMIT = 720 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
FROZEN = {
    "scripts/demo_container_transport.py": (
        "7ba9223b5d50d022b794633748ba2bfc678353e9ac05645271df09929ea8dcd5"
    ),
    "scripts/demo_container_snapshot.py": (
        "aaa1dd1f63858f693da366a5b42e8f7036f010a19e229dfc014fe4e688ae18a0"
    ),
}


def frozen_dependencies() -> None:
    require(
        all(sha((ROOT / name).read_bytes()) == digest for name, digest in FROZEN.items()),
        "Frozen 64MiB originals changed",
    )


def snapshot_frame512(raw: bytes) -> dict[str, Any]:
    require(len(raw) <= FRAME_LIMIT, "Original snapshot framing exceeds explicit 720MiB budget")
    return object_value(
        json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    )


def read_gzip512(raw: bytes) -> bytes:
    require(len(raw) <= COMPRESSED_LIMIT, "Original compressed bytes exceed explicit 512MiB budget")
    chunks = []
    total = 0
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        while True:
            chunk = stream.read(CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            require(total <= DECODED_LIMIT, "Original decoded bytes exceed explicit 512MiB budget")
            chunks.append(chunk)
    # All admitted members have now reached EOF with standard gzip CRC/ISIZE validation.
    return b"".join(chunks)


def build_snapshot_command512(transport: OwnedTransport, run_id: str) -> PreparedCommand:
    frozen_dependencies()
    require(
        re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id) is not None,
        "Explicit safe observation run required",
    )
    helper = ROOT / "scripts/demo_container_snapshot_512.py"
    raw = helper.read_bytes()
    config = {
        "protocol": "bounded-funds-demo-container-transport512-v1",
        "capacity_revision": CAPACITY_REVISION,
        "decoded_byte_budget": DECODED_LIMIT,
        "stream_chunk_bytes": CHUNK_BYTES,
        "run_id": run_id,
        "owner_uuid": transport.owner_uuid,
        "owner_run_id": transport.owner_run_id,
        "database": transport.database,
        "source_head": transport.source_head,
        "source_digest": transport.source_digest,
        "api_container_id": transport.api_container_id,
        "runtime_sources": dict(transport.runtime_sources),
        "snapshot_helper_sha256": sha(raw),
    }
    literal = repr(json.dumps(config, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    stdin = raw + ("\nraise SystemExit(main(json.loads(" + literal + ")))\n").encode()
    return PreparedCommand(
        _argv(transport) + ("-",),
        stdin,
        "READ_ONLY_NATIVE_SNAPSHOT_AUDIT",
        transport.api_container_id,
        transport.source_digest,
    )


def decode_snapshot_output512(
    transport: OwnedTransport,
    command: PreparedCommand,
    stdout: bytes,
) -> DecodedSnapshot:
    """Validate native framing and return original bytes; never rewrite/hash historical rows.

    Caller must retain stdout/stderr/exit code before calling this validator, including
    failed output. A validated frame does not prove Docker was executed or offline.
    """
    frozen_dependencies()
    require(
        command.kind == "READ_ONLY_NATIVE_SNAPSHOT_AUDIT"
        and command.container_id == transport.api_container_id
        and command.source_digest == transport.source_digest,
        "Snapshot command binding differs",
    )
    report = snapshot_frame512(stdout)
    require(
        report.get("capacity_revision") == CAPACITY_REVISION
        and type(report.get("decoded_byte_budget")) is int
        and report["decoded_byte_budget"] == DECODED_LIMIT
        and type(report.get("stream_chunk_bytes")) is int
        and report["stream_chunk_bytes"] == CHUNK_BYTES,
        "Actual producer capacity revision differs",
    )
    require(
        report.get("protocol") == "bounded-funds-container-snapshot512-v1"
        and report.get("status") == "PASSED"
        and report.get("owner_uuid") == transport.owner_uuid
        and report.get("owner_run_id") == transport.owner_run_id
        and report.get("database") == transport.database
        and report.get("api_container_id") == transport.api_container_id
        and report.get("source_head") == transport.source_head
        and report.get("source_digest") == transport.source_digest
        and report.get("runtime_source_before")
        == report.get("runtime_source_after")
        == dict(transport.runtime_sources),
        "Native snapshot source/ownership/status differs",
    )
    helper = (ROOT / "scripts/demo_container_snapshot_512.py").read_bytes()
    require(
        report.get("snapshot_helper_sha256") == sha(helper) and command.stdin.startswith(helper),
        "Executed snapshot helper differs",
    )
    literal = command.stdin[len(helper) :].decode()
    # Generated invocation is bounded and has no extra command/argument mutation.
    tree = ast.parse(literal)
    require(
        len(tree.body) == 1 and isinstance(tree.body[0], ast.Raise), "Snapshot invocation malformed"
    )
    constants = [item.value for item in ast.walk(tree) if isinstance(item, ast.Constant)]
    require(
        len(constants) == 1 and isinstance(constants[0], str), "Snapshot configuration malformed"
    )
    config = object_value(original_json(cast(str, constants[0]).encode()))
    expected = build_snapshot_command512(transport, config["run_id"])
    require(
        expected == command and report.get("run_id") == config["run_id"],
        "Snapshot run/command differs",
    )
    transaction = object_value(report.get("actual_transaction"))
    require(
        transaction.get("isolation_query") == "SHOW transaction_isolation"
        and transaction.get("isolation") == "repeatable read"
        and transaction.get("readonly_query") == "SHOW transaction_read_only"
        and transaction.get("readonly") == "on",
        "Actual native read-only RR proof missing",
    )
    require(
        transaction.get("identity_query")
        == (
            "SELECT current_database() AS database, inet_server_addr()::text AS server_address, "
            "inet_server_port() AS server_port, current_user AS database_user"
        ),
        "Actual native identity SQL differs",
    )
    identities = list_value(transaction.get("identity_rows"))
    require(isinstance(identities, list) and len(identities) == 1, "Actual native identity missing")
    identity = object_value(identities[0])
    require(
        identity.get("database") == transport.database
        and identity.get("server_address") in {"127.0.0.1", "127.0.0.1/32"}
        and type(identity.get("server_port")) is int
        and identity["server_port"] == 54329
        and identity.get("database_user") == "bf_demo",
        "Actual native SQL endpoint differs",
    )
    encoded = report.get("snapshot_gzip_base64")
    require(isinstance(encoded, str), "Original gzip framing missing")
    require(
        len(cast(str, encoded)) <= ((COMPRESSED_LIMIT + 2) // 3) * 4,
        "Original compressed framing exceeds explicit bound",
    )
    raw = base64.b64decode(cast(str, encoded), validate=True)
    require(len(raw) <= COMPRESSED_LIMIT, "Original compressed bytes exceed explicit bound")
    meta = object_value(report.get("snapshot"))
    require(
        meta.get("sha256") == sha(raw) and meta.get("compressed_bytes") == len(raw),
        "Original gzip bytes/hash differ",
    )
    data_raw = read_gzip512(raw)
    require(
        len(data_raw) <= DECODED_LIMIT
        and meta.get("bytes") == len(data_raw)
        and meta.get("data_sha256") == report.get("snapshot_after_data_sha256") == sha(data_raw),
        "Original rows/hash/read-only before-after proof differ",
    )
    # Parse actual rows after the decompression budget is enforced.
    data = object_value(
        json.loads(data_raw.decode(), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    )
    physical = meta.get("physical_tables")
    require(
        isinstance(physical, list)
        and len(physical) == len(set(physical)) == len(data) == 24
        and set(physical) == set(data)
        and set(data) == registered_business_tables(transport) | {"alembic_version"}
        and all(
            isinstance(rows, list) and all(type(row) is dict for row in rows)
            for rows in data.values()
        )
        and meta.get("row_counts") == {name: len(rows) for name, rows in data.items()},
        "Actual original physical24 row denominator differs",
    )
    require(
        meta.get("physical_query")
        == (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        ),
        "Actual original physical table SQL differs",
    )
    observations = list_value(report.get("observations"))
    require(
        isinstance(observations, list) and bool(observations), "Original audit observations missing"
    )
    users = list_value(data.get("users"))
    epochs = list_value(data.get("audit_epochs"))
    require(
        isinstance(users, list)
        and bool(users)
        and all(row.get("is_simulated") is True for row in users)
        and isinstance(epochs, list),
        "Original simulated users/epochs missing",
    )
    expected_pairs = {(row["user_id"], row["id"]) for row in epochs}
    require(
        {row["id"] for row in users} == {row["user_id"] for row in epochs},
        "User audit denominator incomplete",
    )
    type_source = "apps/api/app/domain/audit_chain_types.py"
    require(
        dict(transport.runtime_sources).get(type_source) == sha((ROOT / type_source).read_bytes()),
        "Original audit result contract source drift",
    )
    schema = runpy.run_path(str(ROOT / type_source))["AuditVerification"]
    actual_pairs = set()
    for observation in observations:
        row = object_value(observation)
        pair = row.get("user_id"), row.get("epoch_id")
        require(
            pair not in actual_pairs and pair in expected_pairs,
            "Original audit epoch duplicated/foreign",
        )
        epoch = next(item for item in epochs if (item["user_id"], item["id"]) == pair)
        verification = object_value(row.get("verification"))
        schema.model_validate_json(json.dumps(verification, allow_nan=False))
        require(
            row.get("epoch_number") == epoch.get("epoch_number")
            and row.get("epoch_status") == epoch.get("status")
            and verification.get("status") == "VALID"
            and verification.get("chain_status") == verification.get("reference_status") == "VALID"
            and verification.get("checkpoint_status") in {"VERIFIED", "NOT_REQUESTED"}
            and verification.get("errors") == []
            and verification.get("errors_truncated") is False
            and verification.get("epoch_id") == row.get("epoch_id")
            and verification.get("user_id") == row.get("user_id"),
            "Original audit result/epoch differs",
        )
        actual_pairs.add(pair)
    require(
        actual_pairs == expected_pairs, "Original all-user/all-epoch audit denominator incomplete"
    )
    return DecodedSnapshot(stdout, raw, data_raw, report)
