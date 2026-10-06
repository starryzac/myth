"""TOOL_ONLY synthetic media/protocol risks; never financial or native-capture proof."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import struct
import sys
import zlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "media_posthoc_risk_candidate", ROOT / "scripts/w1_media_parse_only.py"
)
assert SPEC and SPEC.loader
CANDIDATE: Any = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CANDIDATE
SPEC.loader.exec_module(CANDIDATE)


def chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    )


def png() -> bytes:
    return (
        CANDIDATE.MEDIA.PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x11\x22\x33"))
        + chunk(b"IEND", b"")
    )


def frame_originals() -> tuple[bytes, bytes, dict[str, Any]]:
    raw = png() * 2
    output, index = io.BytesIO(), io.BytesIO()
    report = CANDIDATE.MEDIA.decode_stream(io.BytesIO(raw), output, index)
    return raw, index.getvalue(), report


def test_tool_only_full_png_index_crc_scanlines_fields_sha_and_eof_recomputed_without_decoder(
    monkeypatch: Any,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("POSTHOC_PARSE_ONLY cannot spawn a decoder")

    monkeypatch.setattr(CANDIDATE.MEDIA.subprocess, "Popen", forbidden)
    raw, index, old = frame_originals()
    report, sizes = CANDIDATE.verify_png_index(io.BytesIO(raw), io.BytesIO(index))
    assert report["frame_count"] == 2 and sizes == [len(png()), len(png())]
    assert report["stdout_sha256"] == old["stdout_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["index_sha256"] == hashlib.sha256(index).hexdigest()
    assert report["index_bytes"] == len(index) and report["eof_observed"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("ordinal", 7),
        ("png_sha256", "0" * 64),
        ("scanline_sha256", "0" * 64),
        ("png_bytes", 999),
        ("width", 99),
        ("height", 99),
        ("channels", 4),
        ("scanline_bytes", 999),
    ],
)
def test_tool_only_each_original_frame_field_is_rechecked(field: str, value: Any) -> None:
    raw, index, _ = frame_originals()
    entries = index.splitlines()
    row = json.loads(entries[0])
    row[field] = value
    entries[0] = json.dumps(row, sort_keys=True).encode()
    with pytest.raises(ValueError, match="index/full frame"):
        CANDIDATE.verify_png_index(io.BytesIO(raw), io.BytesIO(b"\n".join(entries) + b"\n"))


@pytest.mark.parametrize(
    "mutation",
    [
        "truncated_png",
        "extra_png",
        "missing_index",
        "extra_index",
        "index_space",
        "index_line_cap",
        "crc_corrupt",
    ],
)
def test_tool_only_no_missing_extra_reencoded_or_truncated_originals(mutation: str) -> None:
    raw, index, _ = frame_originals()
    if mutation == "truncated_png":
        raw = raw[:-1]
    elif mutation == "extra_png":
        raw += b"x"
    elif mutation == "missing_index":
        index = index.splitlines(keepends=True)[0]
    elif mutation == "extra_index":
        index += index.splitlines(keepends=True)[0]
    elif mutation == "index_space":
        index = b" " + index
    elif mutation == "index_line_cap":
        index = b"x" * 5000 + b"\n" + index
    else:
        raw = raw[:40] + b"x" + raw[41:]
    with pytest.raises(ValueError):
        CANDIDATE.verify_png_index(io.BytesIO(raw), io.BytesIO(index))


@pytest.mark.parametrize("limit", ["MAX_FRAMES", "MAX_STDOUT_BYTES", "MAX_FRAME_SCANLINES"])
def test_tool_only_prior_full_frame_budgets_remain_rejection_gates(
    monkeypatch: Any, limit: str
) -> None:
    raw, index, _ = frame_originals()
    monkeypatch.setattr(CANDIDATE.MEDIA, limit, 1)
    with pytest.raises(ValueError):
        CANDIDATE.verify_png_index(io.BytesIO(raw), io.BytesIO(index))


def envelope() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    # TOOL_ONLY protocol syntax; no real runner/decoder/bank record is invented.
    wrapper = {
        "status": "FAILED_OR_INCOMPLETE",
        "all_bound_originals_unchanged": True,
        "originals_before": {"TOOL_ONLY": "sha-placeholder"},
        "originals_after": {"TOOL_ONLY": "sha-placeholder"},
        "commands": [{"exit_code": 0}, {"exit_code": 2}],
    }
    child = {
        "protocol": "bounded-funds-supported-media-decode-v1",
        "method_revision": CANDIDATE.ORIGINAL_V2_METHOD,
        "status": "INCOMPLETE",
        "error": {"type": "ValueError", "message": "Native decoder stderr budget"},
        "evidence_use": "HISTORICAL_CONTEXT",
        "run_id": "TOOL_ONLY",
        "timing": None,
        "command": {"exit_code": 0, "timed_out": False},
    }
    request = {"evidence_use": "HISTORICAL_CONTEXT", "run_id": "TOOL_ONLY"}
    return wrapper, child, request


def test_tool_only_original_failed_exits_and_status_are_preserved() -> None:
    values = envelope()
    before = copy.deepcopy(values)
    CANDIDATE.validate_envelope(*values)
    assert values == before


@pytest.mark.parametrize(
    "mutation",
    [
        "wrapper_pass",
        "child_pass",
        "other_method",
        "other_error",
        "different_run",
        "current_source",
        "old_timing_success",
        "native_nonzero",
        "native_bool",
        "timed_out",
        "after_changed",
        "empty_source",
        "validate_nonzero",
        "decode_zero",
        "extra_command",
    ],
)
def test_tool_only_flags_cannot_upgrade_failed_or_mismatched_origin(mutation: str) -> None:
    wrapper, child, request = envelope()
    if mutation == "wrapper_pass":
        wrapper["status"] = "PASSED"
    elif mutation == "child_pass":
        child["status"] = "PASSED"
    elif mutation == "other_method":
        child["method_revision"] = "unknown"
    elif mutation == "other_error":
        child["error"]["message"] = "CRC invalid"
    elif mutation == "different_run":
        request["run_id"] = "OTHER_TOOL_ONLY"
    elif mutation == "current_source":
        request["evidence_use"] = "CURRENT_SOURCE"
    elif mutation == "old_timing_success":
        child["timing"] = {"status": "PASSED"}
    elif mutation == "native_nonzero":
        child["command"]["exit_code"] = 1
    elif mutation == "native_bool":
        child["command"]["exit_code"] = True
    elif mutation == "timed_out":
        child["command"]["timed_out"] = True
    elif mutation == "after_changed":
        wrapper["originals_after"]["TOOL_ONLY"] = "different"
    elif mutation == "empty_source":
        wrapper["originals_before"] = wrapper["originals_after"] = {}
    elif mutation == "validate_nonzero":
        wrapper["commands"][0]["exit_code"] = 1
    elif mutation == "decode_zero":
        wrapper["commands"][1]["exit_code"] = 0
    else:
        wrapper["commands"].append({"exit_code": 0})
    with pytest.raises(ValueError):
        CANDIDATE.validate_envelope(wrapper, child, request)


def test_tool_only_exact_known_frozen_parser_revision_is_allowed() -> None:
    frozen = (
        ROOT / ".runtime/W1-media-posthoc-log-budget-v3-20261005T1011Z/before/w1_media_decode.py"
    ).read_bytes()
    current = (ROOT / "scripts/w1_media_decode.py").read_bytes()
    CANDIDATE.assert_media_revision(frozen, current)


@pytest.mark.parametrize(
    "mutation", ["unknown_old_source", "png_algorithm", "other_cap", "frame_cap", "log_fields"]
)
def test_tool_only_unknown_or_weakened_frozen_algorithm_refused(mutation: str) -> None:
    frozen = (
        ROOT / ".runtime/W1-media-posthoc-log-budget-v3-20261005T1011Z/before/w1_media_decode.py"
    ).read_bytes()
    current = (ROOT / "scripts/w1_media_decode.py").read_bytes()
    if mutation == "unknown_old_source":
        frozen += b"\n# unknown old source"
    elif mutation == "png_algorithm":
        current = current.replace(b"zlib.crc32(kind + body)", b"zlib.crc32(body)")
    elif mutation == "other_cap":
        current = current.replace(b"MAX_STDOUT_BYTES = 8", b"MAX_STDOUT_BYTES = 16")
    elif mutation == "frame_cap":
        current = current.replace(b"MAX_FRAMES = 60000", b"MAX_FRAMES = 90000")
    else:
        current = current.replace(b'"-debug_ts",', b'"-benchmark",')
    with pytest.raises(ValueError):
        CANDIDATE.assert_media_revision(frozen, current)


@pytest.fixture
def workspace() -> Path:
    result = (
        ROOT
        / ".runtime/W1-media-posthoc-log-budget-v3-20261005T1011Z"
        / ("tool-only-" + uuid4().hex)
    )
    result.mkdir()
    return result


def test_tool_only_byte_hash_and_exact_path_records_are_recomputed(workspace: Path) -> None:
    path = workspace / "TOOL_ONLY.txt"
    path.write_bytes(b"TOOL_ONLY bytes")
    value = CANDIDATE.record(path)
    assert CANDIDATE.equal_record(value, path) == value
    path.write_bytes(b"TOOL_ONLY changed")
    with pytest.raises(ValueError, match="path/bytes/SHA"):
        CANDIDATE.equal_record(value, path)


def test_tool_only_absolute_record_path_stays_canonical_within_repository(workspace: Path) -> None:
    path = workspace / "TOOL_ONLY.txt"
    path.write_bytes(b"TOOL_ONLY bytes")
    assert CANDIDATE.repository_file(str(path)) == path
    with pytest.raises(ValueError):
        CANDIDATE.repository_file(str(workspace / ".." / workspace.name / path.name))
    with pytest.raises(ValueError):
        CANDIDATE.repository_file(str(ROOT.parent / "AGENTS.md"))


def test_tool_only_duplicate_json_originals_refused(workspace: Path) -> None:
    path = workspace / "TOOL_ONLY.json"
    path.write_bytes(b'{"status":"INCOMPLETE","status":"PASSED"}')
    with pytest.raises(ValueError, match="Duplicate"):
        CANDIDATE.read_object(path)
