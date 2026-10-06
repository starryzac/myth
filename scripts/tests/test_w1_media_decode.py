"""TOOL_ONLY risk fixtures; no financial effect, capture or successful movie is invented."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import struct
import sys
import zlib
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest

spec = importlib.util.spec_from_file_location(
    "w1_media_decode", Path(__file__).resolve().parents[1] / "w1_media_decode.py"
)
assert spec and spec.loader
candidate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = candidate
spec.loader.exec_module(candidate)


@pytest.fixture
def workspace():
    # Retain bounded TOOL_ONLY fixtures; pytest's Windows mode0700 temp is inaccessible here.
    folder = (
        Path(__file__).resolve().parents[2]
        / ".runtime/W1-media-source-integration-20261005T0900Z"
        / ("tool-only-fixture-" + uuid4().hex)
    )
    folder.mkdir()
    return folder


def chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    )


def png() -> bytes:
    return (
        candidate.PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x11\x22\x33"))
        + chunk(b"IEND", b"")
    )


def native_log(sizes=(70, 70)) -> bytes:
    # TOOL_ONLY syntax fixture for the observed, pinned FFmpeg 7.0.1 log protocol.
    lines = [
        "[info] Input #0, matroska,webm, from 'TOOL_ONLY.webm':",
        "[info]   Stream #0:0: Video: vp8 (libvpx), yuv420p, 1x1, 25 fps, 1k tbn",
        "[info] Stream mapping:",
        "[info] Stream #0:0 -> #0:0 (vp8 (libvpx) -> png (native))",
    ]
    for ordinal, size in enumerate(sizes):
        pts, seconds = ordinal * 40, ordinal / 25
        lines.extend(
            [
                f"[info] demuxer -> ist_index:0:0 type:video pkt_pts:{pts} "
                f"pkt_pts_time:{seconds} pkt_dts:{pts} pkt_dts_time:{seconds} "
                "duration:40 duration_time:0.04",
                f"[info] decoder -> pts:{pts} pts_time:{seconds} pkt_dts:{pts} "
                f"pkt_dts_time:{seconds} duration:40 duration_time:0.04 "
                "keyframe:0 frame_type:0 time_base:1/1000",
                f"[info] encoder -> type:video pkt_pts:{ordinal} pkt_pts_time:{seconds} "
                f"pkt_dts:{ordinal} pkt_dts_time:{seconds} duration:1 duration_time:0.04",
                f"[info] muxer <- pts:{ordinal} pts_time:{seconds} dts:{ordinal} "
                f"dts_time:{seconds} duration:1 duration_time:0.04 size:{size} latency(total:1ms)",
            ]
        )
    lines.extend(
        [
            "[info] video:1KiB audio:0KiB subtitle:0KiB other streams:0KiB global headers:0KiB",
            f"[info] frame= {len(sizes)} fps=1 q=-0.0 Lsize=N/A time=00:00:00.00",
        ]
    )
    return "\n".join(lines).encode()


def test_two_complete_png_frames_decoded_crc_zlib_bytes_and_index_are_not_native_capture():
    stdout, index = io.BytesIO(), io.BytesIO()
    original = png() * 2
    report = candidate.decode_stream(io.BytesIO(original), stdout, index)
    assert stdout.getvalue() == original
    assert report["frame_count"] == 2 and report["stdout_sha256"] == candidate.checksum(original)
    records = [json.loads(line) for line in index.getvalue().splitlines()]
    assert [r["ordinal"] for r in records] == [1, 2]
    assert all(r["scanline_bytes"] == 4 and r["width"] == r["height"] == 1 for r in records)


@pytest.mark.parametrize(
    "value",
    [b"", png()[:-1], png() + b"x", b"metadata says success", png()[:40] + b"x" + png()[41:]],
)
def test_empty_truncated_extra_and_crc_corrupt_frame_streams_reject(value):
    with pytest.raises(ValueError):
        candidate.decode_stream(io.BytesIO(value), io.BytesIO(), io.BytesIO())


def test_actual_frame_count_budget_never_silently_truncates(monkeypatch):
    monkeypatch.setattr(candidate, "MAX_FRAMES", 1)
    with pytest.raises(ValueError, match="frame/stdout budget"):
        candidate.decode_stream(io.BytesIO(png() * 2), io.BytesIO(), io.BytesIO())


def test_explicit_frame_log_budget_v3_preserves_every_other_original_limit():
    # TOOL_ONLY constants, not a video decode or forty-minute media proof.
    assert candidate.MAX_FRAMES == 60000
    assert (
        candidate.METHOD_REVISION
        == "LIVE_MEDIA_IMPORT_PATH_AND_CURRENT_CONTEXT_FRAME_LOG_BUDGET_V3"
    )
    assert candidate.MAX_INPUT_BYTES == 512 * 1024 * 1024
    assert candidate.MAX_STDOUT_BYTES == 8 * 1024 * 1024 * 1024
    assert candidate.MAX_STDERR_BYTES == 128 * 1024 * 1024
    assert candidate.MAX_FRAME_PNG == 16 * 1024 * 1024
    assert candidate.MAX_FRAME_SCANLINES == 64 * 1024 * 1024
    assert candidate.TIMEOUT_SECONDS == 600


def test_native_stderr_budget_is_still_a_rejection_gate(monkeypatch):
    monkeypatch.setattr(candidate, "MAX_STDERR_BYTES", len(native_log()) - 1)
    with pytest.raises(ValueError, match="stderr budget"):
        candidate.native_timing(native_log(), [70, 70], 0)


def test_actual_stdout_budget_never_silently_truncates(monkeypatch):
    monkeypatch.setattr(candidate, "MAX_STDOUT_BYTES", len(png()) - 1)
    with pytest.raises(ValueError, match="frame/stdout budget"):
        candidate.decode_stream(io.BytesIO(png()), io.BytesIO(), io.BytesIO())


def test_scanline_decompression_budget_is_effective(monkeypatch):
    monkeypatch.setattr(candidate, "MAX_FRAME_SCANLINES", 3)
    with pytest.raises(ValueError, match="scanline budget"):
        candidate.decode_stream(io.BytesIO(png()), io.BytesIO(), io.BytesIO())


def test_native_duration_is_all_decoded_pts_not_header_duration():
    raw = native_log().replace(
        b"[info] Stream mapping:", b"[info] Duration: 01:00:00.00\n[info] Stream mapping:"
    )
    result = candidate.native_timing(raw, [70, 70], 0)
    assert result["actual_duration_rational_seconds"] == "2/25"
    assert result["actual_duration_seconds"] == 0.08
    assert (
        result["four_minute_content"] == "UNVERIFIED"
        and result["complete_content_review"] == "NOT_RUN"
    )


@pytest.mark.parametrize("exit_code", [1, -9, None, False])
def test_actual_exit_code_not_success_flag_is_required(exit_code):
    with pytest.raises(ValueError, match="exited unsuccessfully"):
        candidate.native_timing(native_log(), [70, 70], exit_code)


@pytest.mark.parametrize(
    "mutation",
    [
        "warning",
        "decoder_missing",
        "extra_frame",
        "timing_gap",
        "zero_duration",
        "timestamp_changed",
        "mux_bytes",
        "summary_count",
        "audio_track",
        "other_codec",
        "other_video",
        "no_input",
        "no_eof",
    ],
)
def test_actual_native_log_counts_timestamps_tracks_errors_and_complete_eof_required(mutation):
    raw = native_log()
    if mutation == "warning":
        raw += b"\n[warning] File ended prematurely"
    elif mutation == "decoder_missing":
        raw = raw.replace(b"decoder -> pts:40", b"unknown -> pts:40")
    elif mutation == "extra_frame":
        raw += native_log()
    elif mutation == "timing_gap":
        raw = raw.replace(b"pkt_pts:40", b"pkt_pts:80").replace(
            b"decoder -> pts:40", b"decoder -> pts:80"
        )
    elif mutation == "zero_duration":
        raw = raw.replace(b"duration:40", b"duration:0")
    elif mutation == "timestamp_changed":
        raw = raw.replace(b"pkt_pts_time:0.04", b"pkt_pts_time:0.08")
    elif mutation == "mux_bytes":
        raw = raw.replace(b"size:70", b"size:71")
    elif mutation == "summary_count":
        raw = raw.replace(b"frame= 2", b"frame= 3")
    elif mutation == "audio_track":
        raw = raw.replace(
            b"[info] Stream mapping:", b"[info] Stream #0:1: Audio: opus\n[info] Stream mapping:"
        )
    elif mutation == "other_codec":
        raw = raw.replace(b"Video: vp8 (libvpx)", b"Video: vp9")
    elif mutation == "other_video":
        raw = raw.replace(
            b"[info] Stream mapping:",
            b"[info] Stream #0:1: Video: vp8 (libvpx)\n[info] Stream mapping:",
        )
    elif mutation == "no_input":
        raw = raw.replace(b"Input #0, matroska,webm,", b"Input #0, arbitrary,")
    elif mutation == "no_eof":
        raw = raw.replace(b"Lsize=", b"not-final=")
    with pytest.raises(ValueError):
        candidate.native_timing(raw, [70, 70], 0)


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "C:/outside", "x/../y", "x//y", "x/./y", "x\x00y"]
)
def test_explicit_original_paths_refuse_escape_and_aliases(name):
    with pytest.raises(ValueError):
        candidate.relative(name)


def test_original_windows_manifest_separator_has_one_explicit_representation():
    assert candidate.relative("original\\video.webm") == "original/video.webm"
    with pytest.raises(ValueError, match="aliases"):
        candidate.artifact_index({"artifact_hashes": {"x/y": "a" * 64, "x\\y": "a" * 64}})


def test_duplicate_json_success_flags_cannot_overwrite_original_failure():
    with pytest.raises(ValueError, match="Duplicate JSON"):
        candidate.strict_json(b'{"status":"FAILED","status":"PASSED"}')


@pytest.mark.parametrize("after", [None, {}, {"original.py": "b" * 64}])
def test_historical_missing_drifted_source_is_explicit_diagnostic_current_must_refuse(after):
    owner = {"status": "FAILED", "source_before": {"original.py": "a" * 64}, "source_after": after}
    observed = candidate.validate_capture_source(owner, "HISTORICAL_CONTEXT")
    assert observed["capture_producer_current_source_verified"] is False
    assert observed["capture_source_relation"] in {
        "CAPTURE_SOURCE_AFTER_MISSING",
        "CAPTURE_SOURCE_DRIFTED",
    }
    assert owner["status"] == "FAILED"
    with pytest.raises(ValueError, match="incomplete/drifted"):
        candidate.validate_capture_source(owner, "CURRENT_SOURCE")


def test_current_source_is_rehashed_actual_bytes_not_only_equal_declared_maps(workspace):
    path = workspace / "original.py"
    path.write_bytes(b"TOOL_ONLY original module")
    source = {"original.py": candidate.file_record(path)["sha256"]}
    owner = {"source_before": source, "source_after": source}
    result = candidate.validate_capture_source(owner, "CURRENT_SOURCE", workspace)
    assert result["capture_producer_current_source_verified"] is True
    path.write_bytes(b"TOOL_ONLY current source changed")
    with pytest.raises(ValueError, match="not current"):
        candidate.validate_capture_source(owner, "CURRENT_SOURCE", workspace)


def test_nonfinite_json_rejected():
    with pytest.raises(ValueError, match="Nonfinite"):
        candidate.strict_json(b'{"duration":NaN}')


def test_no_arbitrary_decoder_or_arguments_can_enter_request():
    with pytest.raises(ValueError, match="Exact request fields"):
        candidate.validate_request({"ffmpeg": "other.exe", "argv": ["unsafe"]})


def test_fixed_native_command_cannot_substitute_copy_seek_limit_or_replay():
    argv = candidate.decode_argv(Path("pinned-ffmpeg.exe"), Path("original-video.webm"))
    assert argv.count("original-video.webm") == 1 and argv[-1] == "pipe:1"
    assert "-xerror" in argv and argv[argv.index("-err_detect") + 1] == "explode"
    assert (
        argv[argv.index("-c:v") + 1] == "png" and argv[argv.index("-fps_mode") + 1] == "passthrough"
    )
    assert all(value not in argv for value in ("copy", "-ss", "-t", "-frames:v", "-r"))


def test_repository_file_must_be_real_original_under_workspace(workspace):
    original = workspace / "original.json"
    original.write_bytes(b"TOOL_ONLY")
    assert candidate.repository_file("original.json", workspace) == original
    with pytest.raises(ValueError):
        candidate.repository_file("../original.json", workspace)


def test_fresh_output_guard_happens_without_any_decoder_run(workspace, monkeypatch):
    request = workspace / "request.json"
    request.write_bytes(b'{"run_id":"TOOL_ONLY"}')
    existing = workspace / ".runtime/existing"
    existing.mkdir(parents=True)
    monkeypatch.setattr(candidate, "validate_request", lambda request, root: {})

    def forbidden(*args, **kwargs):
        pytest.fail("Default/fresh output guard attempted subprocess")

    monkeypatch.setattr(candidate.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="already exists"):
        candidate.execute(request, existing, workspace)


def trace_fixture(workspace, mutation=None):
    # TOOL_ONLY marker bytes validate binding guards, never an actual decoded VP8/capture claim.
    video = b"TOOL_ONLY not a movie" * 100
    native_sha = hashlib.sha1(video, usedforsecurity=False).hexdigest()
    browser = {"actual_user_agent": "TOOL_ONLY Edg/141.0"}
    context = {
        "type": "context-options",
        "version": 8,
        "origin": "library",
        "channel": "msedge",
        "browserName": "chromium",
        "contextId": "TOOL_ONLY-context",
        "options": {"recordVideo": {"dir": "TOOL_ONLY"}},
    }
    attachment = {"name": "video", "contentType": "video/webm", "sha1": native_sha}
    network = [
        {
            "snapshot": {
                "request": {
                    "headers": [{"name": "User-Agent", "value": browser["actual_user_agent"]}]
                }
            }
        }
    ]
    if mutation == "ua_mismatch":
        network[0]["snapshot"]["request"]["headers"][0]["value"] = "different Edg/141.0"
    elif mutation == "ua_missing":
        network[0]["snapshot"]["request"]["headers"] = []
    elif mutation == "ua_duplicate":
        network[0]["snapshot"]["request"]["headers"] *= 2
    elif mutation == "context_ua_mismatch":
        context["userAgent"] = "different Edg/141.0"
    elif mutation == "network_empty":
        network = []
    elif mutation == "future_protocol":
        context["version"] = 9
    elif mutation == "other_channel":
        context["channel"] = "chrome"
    path = workspace / "trace.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("test.trace", json.dumps({"attachments": [attachment]}))
        archive.writestr("0-trace.trace", json.dumps(context))
        if mutation != "network_missing":
            archive.writestr("0-trace.network", "\n".join(json.dumps(event) for event in network))
        if mutation != "resource_missing":
            archive.writestr("resources/" + native_sha, video)
        if mutation == "unsafe_member":
            archive.writestr("../outside", b"TOOL_ONLY")
    record = {"bytes": len(video), "sha256": candidate.checksum(video)}
    if mutation == "video_changed":
        record["sha256"] = "a" * 64
    return path, record, browser


def test_actual_default_agent_needs_all_original_har_headers_and_remains_content_unreviewed(
    workspace,
):
    result = candidate.trace_binding(*trace_fixture(workspace))
    assert result["original_user_agent_header_count"] == 1
    assert (
        result["agent_binding_method"]
        == "ACTUAL_NAVIGATOR_EQUALS_ALL_ORIGINAL_TRACE_HAR_USER_AGENTS"
    )
    assert result["complete_content_review"] == "NOT_RUN"


@pytest.mark.parametrize(
    "mutation",
    [
        "ua_mismatch",
        "ua_missing",
        "ua_duplicate",
        "context_ua_mismatch",
        "network_empty",
        "network_missing",
        "future_protocol",
        "other_channel",
        "resource_missing",
        "unsafe_member",
        "video_changed",
    ],
)
def test_original_native_capture_agent_resource_protocol_and_safe_zip_denominator_required(
    workspace, mutation
):
    with pytest.raises(ValueError):
        candidate.trace_binding(*trace_fixture(workspace, mutation))


def test_historical_context_is_preserved_unverified_without_current_source_lookup(monkeypatch):
    def forbidden(root):
        pytest.fail("Historical context must never be upgraded through current source")

    monkeypatch.setattr(candidate, "current_source_state", forbidden)
    original = {
        "purpose": "MVP_ACCEPTANCE",
        "status": "FAILED",
        "acceptance_context": {"missing": "old-original"},
    }
    result = candidate.validate_parent_context(original, "HISTORICAL_CONTEXT")
    assert (
        result["current_parent_context_verified"] is False
        and result["original_acceptance_context"] == original["acceptance_context"]
    )
    assert original["status"] == "FAILED"


def test_development_cannot_inherit_final_context():
    with pytest.raises(ValueError, match="cannot inherit"):
        candidate.validate_parent_context(
            {"purpose": "DEVELOPMENT", "acceptance_context": {}}, "CURRENT_SOURCE"
        )


def synthetic_context(workspace, monkeypatch):
    # TOOL_ONLY original context binding bytes; contains no bank/actions/money or product proof.
    files = {}
    for name in ("scripts/w1_media_decode.py", "scripts/tests/test_w1_media_decode.py"):
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"TOOL_ONLY current source")
        files[name] = candidate.file_record(path)["sha256"]
    source = {
        "git_head": "TOOL_ONLY",
        "files": files,
        "source_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    path = workspace / "docs/progress/evidence/W1/tool-only/context.json"
    path.parent.mkdir(parents=True)
    context = {
        "protocol": "bounded-funds-final-context-v1",
        "owner_run_id": "TOOL_ONLY",
        "database": "bf_test_" + "a" * 32,
        "deferred_groups": [
            "six_business_e2e",
            "three_demo_rounds",
            "original_financial_chain",
            "audit_chain",
        ],
        "source": source,
    }
    path.write_text(json.dumps(context), encoding="utf-8")
    import runpy

    binder = runpy.run_path(str(candidate.ROOT / "scripts/w1_current_check_context.py"))[
        "bind_context"
    ]
    original = binder(workspace, str(path), source)
    monkeypatch.setattr(candidate, "current_source_state", lambda root: source)
    owner = {
        "purpose": "MVP_ACCEPTANCE",
        "database": context["database"],
        "acceptance_context": original,
    }
    return owner, source, path


def test_actual_original_context_parser_rechecks_owned_database_bytes_and_registered_tool_source(
    workspace, monkeypatch
):
    owner, _, _ = synthetic_context(workspace, monkeypatch)
    result = candidate.validate_parent_context(owner, "CURRENT_SOURCE", workspace)
    assert result["current_parent_context_verified"] is True
    assert result["acceptance_context"] == owner["acceptance_context"]


@pytest.mark.parametrize(
    "mutation", ["database", "producer_missing", "context_changed", "producer_changed"]
)
def test_current_context_native_identity_source_original_bytes_gates_cannot_be_skipped(
    workspace, monkeypatch, mutation
):
    owner, source, path = synthetic_context(workspace, monkeypatch)
    if mutation == "database":
        owner["database"] = "bf_test_" + "b" * 32
    elif mutation == "producer_missing":
        source["files"].pop("scripts/w1_media_decode.py")
    elif mutation == "context_changed":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        (workspace / "scripts/w1_media_decode.py").write_bytes(b"TOOL_ONLY source changed")
    with pytest.raises(ValueError):
        candidate.validate_parent_context(owner, "CURRENT_SOURCE", workspace)
