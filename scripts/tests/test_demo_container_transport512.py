"""TOOL_ONLY capacity/decoder fixtures; never actual container or financial success."""

from __future__ import annotations

import ast
import base64
import copy
import gzip
import io
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import demo_container_snapshot_512 as producer
from scripts import demo_container_transport512 as tool
from scripts.demo_container_transport import sha
from scripts.tests.test_demo_container_transport import TOOL, frame, raw, transport
from scripts.tests.test_demo_container_transport import native as native

ROOT = Path(__file__).resolve().parents[2]


def framed512(native: dict[str, Any]) -> tuple[Any, Any, dict[str, Any]]:
    owned = transport(native)
    old = TOOL.build_snapshot_command(owned, "TOOL_ONLY_snapshot")
    value = frame(native, old)
    value.update(
        protocol="bounded-funds-container-snapshot512-v1",
        capacity_revision=tool.CAPACITY_REVISION,
        decoded_byte_budget=tool.DECODED_LIMIT,
        stream_chunk_bytes=tool.CHUNK_BYTES,
        snapshot_helper_sha256=sha((ROOT / "scripts/demo_container_snapshot_512.py").read_bytes()),
    )
    return owned, tool.build_snapshot_command512(owned, "TOOL_ONLY_snapshot"), value


def replace_data(value: dict[str, Any], data: bytes) -> None:
    compressed = gzip.compress(data, mtime=0)
    value["snapshot_gzip_base64"] = base64.b64encode(compressed).decode()
    value["snapshot"].update(
        sha256=sha(compressed),
        data_sha256=sha(data),
        bytes=len(data),
        compressed_bytes=len(compressed),
    )
    value["snapshot_after_data_sha256"] = sha(data)


def test_tool_only_small_payload_equals_original_decoder_byte_for_byte(
    native: dict[str, Any],
) -> None:
    owned, new_command, new_frame = framed512(native)
    old_command = TOOL.build_snapshot_command(owned, "TOOL_ONLY_snapshot")
    old_frame = frame(native, old_command)
    old = TOOL.decode_snapshot_output(owned, old_command, raw(old_frame))
    new_stdout = raw(new_frame) + b"\n"
    new = tool.decode_snapshot_output512(owned, new_command, new_stdout)
    assert new.snapshot_gzip == old.snapshot_gzip and new.snapshot_data == old.snapshot_data
    assert new.original_stdout == new_stdout and len(json.loads(new.snapshot_data)) == 24
    for key in (
        "actual_transaction",
        "observations",
        "snapshot",
        "runtime_source_before",
        "runtime_source_after",
    ):
        assert old.report[key] == new.report[key]


def test_tool_only_builder_uses_actual_new_stdin_source_and_no_runtime_execution(
    native: dict[str, Any],
) -> None:
    owned, command, _ = framed512(native)
    helper = (ROOT / "scripts/demo_container_snapshot_512.py").read_bytes()
    assert command.stdin.startswith(helper) and command.argv[5] == owned.api_container_id
    compile(command.stdin, "TOOL_ONLY_snapshot512_stdin", "exec")
    tree = ast.parse(command.stdin[len(helper) :])
    config = json.loads(
        next(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
    )
    assert (
        config["snapshot_helper_sha256"] == sha(helper)
        and config["decoded_byte_budget"] == 536870912
    )
    assert (
        config["stream_chunk_bytes"] == 1048576
        and config["capacity_revision"] == tool.CAPACITY_REVISION
    )
    assert producer.DECODED_LIMIT == tool.DECODED_LIMIT and producer.CHUNK_BYTES == tool.CHUNK_BYTES
    source = helper.decode()
    assert source.index("require_test_database(database)") < source.index(
        "engine = create_database_engine("
    )
    for snippet in (
        "SET TRANSACTION READ ONLY",
        "REPEATABLE READ",
        "Base.metadata.sorted_tables",
        "SELECT version_num FROM alembic_version",
        "verify_audit_chain(session, user.id, epoch.id)",
        "before == after",
    ):
        assert snippet in source
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(ast.parse(helper))
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert not calls & {"reset", "seed", "rpc", "add", "delete", "flush", "create_all", "drop_all"}


@pytest.mark.parametrize(
    "case",
    [
        "status",
        "run",
        "source",
        "helper",
        "old_helper",
        "budget",
        "float_budget",
        "chunk",
        "revision",
        "writable",
        "wrong_isolation",
        "database",
        "port",
        "user",
        "hash",
        "after_hash",
        "physical",
        "row_counts",
        "missing_epoch",
        "duplicate_epoch",
        "foreign_epoch",
        "partial_verification",
        "tampered_reference",
        "missing_user",
        "invalid_simulation",
        "nonfinite",
        "duplicate_key",
    ],
)
def test_tool_only_512_preserves_all_original_risk_and_denominator_gates(
    native: dict[str, Any], case: str
) -> None:
    owned, command, value = framed512(native)
    if case == "status":
        value["status"] = "FAILED"
    elif case == "run":
        value["run_id"] = "other"
    elif case == "source":
        value["source_digest"] = "f" * 64
    elif case == "helper":
        value["snapshot_helper_sha256"] = "f" * 64
    elif case == "old_helper":
        value["snapshot_helper_sha256"] = sha(
            (ROOT / "scripts/demo_container_snapshot.py").read_bytes()
        )
    elif case == "budget":
        value["decoded_byte_budget"] = 64 * 1024 * 1024
    elif case == "float_budget":
        value["decoded_byte_budget"] = float(tool.DECODED_LIMIT)
    elif case == "chunk":
        value["stream_chunk_bytes"] = 1
    elif case == "revision":
        value["capacity_revision"] = "OLD64"
    elif case == "writable":
        value["actual_transaction"]["readonly"] = "off"
    elif case == "wrong_isolation":
        value["actual_transaction"]["isolation"] = "read committed"
    elif case in {"database", "port", "user"}:
        field, bad = {
            "database": ("database", "bounded_funds"),
            "port": ("server_port", 5432),
            "user": ("database_user", "postgres"),
        }[case]
        value["actual_transaction"]["identity_rows"][0][field] = bad
    elif case == "hash":
        value["snapshot"]["sha256"] = "f" * 64
    elif case == "after_hash":
        value["snapshot_after_data_sha256"] = "f" * 64
    elif case == "physical":
        value["snapshot"]["physical_tables"].pop()
    elif case == "row_counts":
        value["snapshot"]["row_counts"]["users"] = 2
    elif case == "missing_epoch":
        value["observations"] = []
    elif case == "duplicate_epoch":
        value["observations"].append(copy.deepcopy(value["observations"][0]))
    elif case == "foreign_epoch":
        value["observations"][0]["epoch_id"] = "f" * 32
    elif case == "partial_verification":
        value["observations"][0]["verification"] = {"status": "VALID"}
    elif case == "tampered_reference":
        value["observations"][0]["verification"]["reference_status"] = "TAMPERED"
    elif case in {"missing_user", "invalid_simulation"}:
        data = json.loads(gzip.decompress(base64.b64decode(value["snapshot_gzip_base64"])))
        if case == "missing_user":
            data["users"] = []
        else:
            data["users"][0]["is_simulated"] = False
        replace_data(value, raw(data))
        value["snapshot"]["row_counts"] = {name: len(rows) for name, rows in data.items()}
    if case == "nonfinite":
        output = b'{"amount":NaN}'
    elif case == "duplicate_key":
        output = b'{"status":"PASSED","status":"PASSED"}'
    else:
        output = raw(value)
    with pytest.raises(ValueError):
        tool.decode_snapshot_output512(owned, command, output)


def test_tool_only_actual_65mib_json_gzip_crosses_old_budget_without_truncation(
    native: dict[str, Any],
) -> None:
    owned, command, value = framed512(native)
    data = json.loads(gzip.decompress(base64.b64decode(value["snapshot_gzip_base64"])))
    data["evidence_items"] = [
        {"tool_input_context": "TOOL_ONLY", "payload": "a" * (65 * 1024 * 1024)}
    ]
    value["snapshot"]["row_counts"] = {name: len(rows) for name, rows in data.items()}
    original = raw(data)
    replace_data(value, original)
    decoded = tool.decode_snapshot_output512(owned, command, raw(value))
    assert decoded.snapshot_data == original and len(decoded.snapshot_data) > 64 * 1024 * 1024
    assert sha(decoded.snapshot_data) == value["snapshot"]["data_sha256"]
    # Rebuild only the explicitly different protocol/helper capacity framing for
    # the untouched original decoder. Every row/gzip/audit result stays identical.
    old_command = TOOL.build_snapshot_command(owned, "TOOL_ONLY_snapshot")
    old_value = copy.deepcopy(value)
    old_value.update(
        protocol="bounded-funds-container-snapshot-v1",
        snapshot_helper_sha256=sha((ROOT / "scripts/demo_container_snapshot.py").read_bytes()),
    )
    with pytest.raises(ValueError, match="Original rows/hash/read-only"):
        TOOL.decode_snapshot_output(owned, old_command, raw(old_value))
    assert gzip.decompress(producer.gzip_original512(original)) == original


def test_tool_only_actual_over_512mib_gzip_is_rejected_by_real_stream_byte_count() -> None:
    output = io.BytesIO()
    block = b"a" * tool.CHUNK_BYTES
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as writer:
        writer.write(b'{"tool_input_context":"TOOL_ONLY","payload":"')
        for _ in range(512):
            writer.write(block)
        writer.write(b'"}')
    # Actual gzip contains a complete synthetic JSON >512MiB. No fake len, cap
    # replacement or financial-function mock is used to reach the real rejection.
    with pytest.raises(ValueError, match="decoded bytes exceed explicit 512MiB"):
        tool.read_gzip512(output.getvalue())


@pytest.mark.parametrize("case", ["crc", "isize", "truncated", "trailing_non_gzip"])
def test_tool_only_crc_truncation_or_trailing_bytes_never_admit_partial(case: str) -> None:
    compressed = bytearray(gzip.compress(b'{"tool_input_context":"TOOL_ONLY"}', mtime=0))
    if case == "crc":
        compressed[-8] ^= 1
    elif case == "isize":
        compressed[-4] ^= 1
    elif case == "truncated":
        compressed = compressed[:-4]
    else:
        compressed.extend(b"NOT_ANOTHER_GZIP_MEMBER")
    with pytest.raises((gzip.BadGzipFile, EOFError)):
        tool.read_gzip512(bytes(compressed))


def test_tool_only_concatenated_members_are_full_original_bytes() -> None:
    first, second = b'{"tool_input_context":"TOOL_ONLY",', b'"payload":"whole"}'
    assert (
        tool.read_gzip512(gzip.compress(first, mtime=0) + gzip.compress(second, mtime=0))
        == first + second
    )
