"""Independent POSTHOC_PARSE_ONLY; never spawns a decoder or upgrades a prior exit/status."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "bounded-funds-media-posthoc-parse-v1"
REQUEST_PROTOCOL = "bounded-funds-media-posthoc-parse-request-v1"
FROZEN_V2_SOURCE_SHA = "159474c57653a046cff65b07a0dbe990df56ed5773ecd8d3d80e7d3d60fb57e6"
FROZEN_V2_WRAPPER_SHA = "4064bc89067e0a212ac56beb483f31a61d974b7bb80af958976fb6e32d144fdc"
ORIGINAL_V2_METHOD = "LIVE_MEDIA_IMPORT_PATH_AND_CURRENT_CONTEXT_FRAME_BUDGET_V2"
CURRENT_V3_METHOD = "LIVE_MEDIA_IMPORT_PATH_AND_CURRENT_CONTEXT_FRAME_LOG_BUDGET_V3"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_INDEX_LINE = 4096
SPEC = importlib.util.spec_from_file_location(
    "bounded_funds_posthoc_media_library", Path(__file__).with_name("w1_media_decode.py")
)
assert SPEC and SPEC.loader
MEDIA: Any = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MEDIA
SPEC.loader.exec_module(MEDIA)


def need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def object_value(value: Any, label: str) -> dict[str, Any]:
    need(isinstance(value, dict), label + " must be an object")
    return value  # type: ignore[no-any-return]


def record(path: Path) -> dict[str, Any]:
    return object_value(MEDIA.file_record(path), "actual original record")


def repository_file(name: Any, root: Path = ROOT) -> Path:
    if isinstance(name, str) and Path(name).is_absolute():
        absolute = Path(name)
        need(
            absolute.is_relative_to(root)
            and absolute == absolute.resolve(strict=True)
            and ".." not in absolute.parts,
            "Original absolute path is not canonical within repository",
        )
        name = absolute.relative_to(root).as_posix()
    return Path(MEDIA.repository_file(name, root))


def read_object(path: Path) -> dict[str, Any]:
    need(path.stat().st_size <= MAX_JSON_BYTES, "JSON original budget")
    return object_value(MEDIA.strict_json(path.read_bytes()), "original JSON")


def equal_record(expected: Any, path: Path) -> dict[str, Any]:
    value = object_value(expected, "recorded original")
    need(set(value) == {"path", "bytes", "sha256"}, "Exact original record fields required")
    actual = record(path)
    need(value == actual, "Original path/bytes/SHA differs")
    return actual


def assert_media_revision(frozen: bytes, current: bytes) -> None:
    need(hashlib.sha256(frozen).hexdigest() == FROZEN_V2_SOURCE_SHA, "Unknown frozen V2 source")

    def normalize(raw: bytes) -> str:
        tree = ast.parse(raw)
        tree.body = [
            item
            for item in tree.body
            if not (
                isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
                and item.targets[0].id in {"MAX_STDERR_BYTES", "METHOD_REVISION"}
            )
        ]
        return ast.dump(tree, include_attributes=False)

    need(normalize(frozen) == normalize(current), "V3 changed an original parser/other budget")
    need(
        MEDIA.METHOD_REVISION == CURRENT_V3_METHOD and MEDIA.MAX_STDERR_BYTES == 128 * 1024 * 1024,
        "Explicit V3 log budget required",
    )


def validate_envelope(
    wrapper: dict[str, Any], child: dict[str, Any], request: dict[str, Any]
) -> None:
    need(wrapper.get("status") == "FAILED_OR_INCOMPLETE", "Prior wrapper status must remain failed")
    need(child.get("status") == "INCOMPLETE", "Prior child status must remain incomplete")
    need(
        child.get("protocol") == "bounded-funds-supported-media-decode-v1"
        and child.get("method_revision") == ORIGINAL_V2_METHOD,
        "Unsupported original decoder method",
    )
    need(
        child.get("error") == {"type": "ValueError", "message": "Native decoder stderr budget"},
        "Only observed V2 stderr-cap failure supported",
    )
    need(
        child.get("evidence_use") == request.get("evidence_use") == "HISTORICAL_CONTEXT"
        and child.get("run_id") == request.get("run_id"),
        "Historical original request/child run differs",
    )
    need(child.get("timing") is None, "Prior incomplete timing must remain original")
    original_command = object_value(child.get("command"), "original decoder command")
    need(
        type(original_command.get("exit_code")) is int
        and original_command["exit_code"] == 0
        and original_command.get("timed_out") is False,
        "Original native decoder did not exit zero within deadline",
    )
    need(
        wrapper.get("all_bound_originals_unchanged") is True
        and wrapper.get("originals_before") == wrapper.get("originals_after")
        and bool(wrapper.get("originals_before")),
        "Original before/after denominator differs",
    )
    commands = wrapper.get("commands")
    if not isinstance(commands, list):
        raise ValueError("Exact two original child commands required")
    need(len(commands) == 2, "Exact two original child commands required")
    need(
        type(commands[0].get("exit_code")) is int
        and commands[0]["exit_code"] == 0
        and type(commands[1].get("exit_code")) is int
        and commands[1]["exit_code"] == 2,
        "Original validate0/decode2 exits must remain original",
    )


def validate_prior(request: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    need(
        set(request)
        == {
            "protocol",
            "evidence_use",
            "run_id",
            "prior_wrapper_manifest",
            "prior_wrapper_sha256",
            "prior_decoder_manifest",
            "prior_decoder_sha256",
        },
        "Exact parse-only request fields required",
    )
    need(request["protocol"] == REQUEST_PROTOCOL, "Unknown parse-only protocol")
    need(request["evidence_use"] == "HISTORICAL_CONTEXT", "Parse-only is historical only")
    wrapper_path = repository_file(request["prior_wrapper_manifest"], root)
    child_path = repository_file(request["prior_decoder_manifest"], root)
    need(wrapper_path.name == child_path.name == "manifest.json", "Original manifest name differs")
    need(
        child_path.parent == wrapper_path.parent / "derived-decode",
        "Original decoder/wrapper paths differ",
    )
    wrapper_record, child_record = record(wrapper_path), record(child_path)
    need(
        wrapper_record["sha256"] == request["prior_wrapper_sha256"]
        and child_record["sha256"] == request["prior_decoder_sha256"],
        "Pinned original wrapper/child SHA differs",
    )
    wrapper, child = read_object(wrapper_path), read_object(child_path)
    original_request_path = repository_file(str(wrapper_path.parent / "request.json"), root)
    original_request = read_object(original_request_path)
    equal_record(wrapper.get("request"), original_request_path)
    need(
        child.get("run_id") == request["run_id"] == original_request.get("run_id"),
        "Original run ID differs",
    )
    validate_envelope(wrapper, child, original_request)
    equal_record(wrapper.get("derived_manifest"), child_path)
    need(
        (child_path.parent / "request.original.json").read_bytes()
        == original_request_path.read_bytes(),
        "Exact original request bytes differ",
    )
    artifacts = object_value(child.get("artifacts"), "original derived artifacts")
    need(
        set(artifacts)
        == {
            "request.original.json",
            "producer.original.py",
            "native.stderr",
            "derived-png.stdout",
            "derived-frame-index.jsonl",
        },
        "Original complete derived artifact denominator differs",
    )
    originals: dict[str, Any] = {
        str(wrapper_path): wrapper_record,
        str(child_path): child_record,
        str(original_request_path): record(original_request_path),
    }
    for name, expected in artifacts.items():
        path = repository_file(str(child_path.parent / name), root)
        originals[str(path)] = equal_record(expected, path)
    frozen_source = child_path.parent / "producer.original.py"
    need(
        record(frozen_source)["sha256"] == FROZEN_V2_SOURCE_SHA,
        "Frozen V2 producer identity differs",
    )
    current_source = repository_file("scripts/w1_media_decode.py", root)
    assert_media_revision(frozen_source.read_bytes(), current_source.read_bytes())
    wrapper_source = repository_file(str(wrapper_path.parent / "wrapper.original.py"), root)
    need(
        record(wrapper_source)["sha256"] == FROZEN_V2_WRAPPER_SHA,
        "Unknown original command wrapper",
    )
    originals[str(wrapper_source)] = record(wrapper_source)
    archived = object_value(wrapper.get("archived_originals"), "archived original sources")
    old_bindings = object_value(wrapper.get("originals_before"), "original before records")
    expected_tools = {
        "scripts/w1_media_decode.py",
        "scripts/tests/test_w1_media_decode.py",
        "scripts/w1_current_check_context.py",
        "scripts/w1_final_acceptance.py",
        "scripts/export_evidence.py",
    }
    capture = object_value(
        MEDIA.validate_request(original_request, root), "actual historical capture binding"
    )
    need(
        capture["capture_source_relation"] == "HISTORICAL_BEFORE_AFTER_EQUAL"
        and capture["capture_producer_current_source_verified"] is False,
        "Original historical capture source denominator incomplete/drifted",
    )
    need(
        capture["capture_binding"] == child.get("capture_binding")
        and capture["source_binding"] == child.get("source_binding")
        and capture["capture_source_relation"] == child.get("capture_source_relation")
        and MEDIA.validate_parent_context(capture["owner"], "HISTORICAL_CONTEXT", root)
        == child.get("parent_context_before"),
        "Original capture/UA/trace/source/context bindings differ",
    )
    owner_path = repository_file(original_request["owner_manifest"], root)
    browser_path = Path(capture["video_path"]).with_name("actual-browser.json")
    expected_archive_paths = {str(root / name) for name in expected_tools} | {
        str(owner_path),
        str(browser_path),
    }
    need(set(archived) == expected_archive_paths, "Archived seven-source denominator differs")
    for original_name, copy_value in archived.items():
        original_value = object_value(old_bindings.get(original_name), "old source record")
        copy_record = object_value(copy_value, "actual archived copy")
        copy_path = repository_file(copy_record.get("path"), root)
        need(
            copy_path.is_relative_to(wrapper_path.parent / "originals"),
            "Archive copy leaves original wrapper",
        )
        equal_record(copy_record, copy_path)
        need(
            copy_record["sha256"] == original_value.get("sha256")
            and copy_record["bytes"] == original_value.get("bytes"),
            "Original archived source bytes differ",
        )
        originals[str(copy_path)] = copy_record
    tools_before = object_value(
        child.get("tool_sources_before"), "frozen original five-tool records"
    )
    need(set(tools_before) == expected_tools, "Original tool-source denominator differs")
    for name, value in tools_before.items():
        need(
            value == old_bindings.get(str(root / name)),
            "Original decoder/wrapper tool version differs",
        )
    need(
        child.get("producer_before") == old_bindings.get(str(current_source)),
        "Original producer version differs",
    )
    need(
        child["producer_before"]["sha256"] == FROZEN_V2_SOURCE_SHA,
        "Unsupported actual producer source",
    )
    capture_bindings = object_value(capture.get("bindings"), "actual originals")
    for name, value in capture_bindings.items():
        need(
            value == old_bindings.get(name) == child.get("originals_before", {}).get(name),
            "Original capture/video/trace/decoder path/SHA differs",
        )
        originals[name] = equal_record(value, repository_file(name, root))
    need(
        set(old_bindings) == {str(root / name) for name in expected_tools} | set(capture_bindings),
        "Full original before denominator differs",
    )
    need(
        child["command"]["argv"]
        == MEDIA.decode_argv(capture["decoder_path"], capture["video_path"]),
        "Original FFmpeg argv differs from original unchanged fixed transport",
    )
    expected_prefix = [
        "uv",
        "--cache-dir",
        ".uv-cache",
        "run",
        "--offline",
        "python",
        "scripts/w1_media_decode.py",
        "--request",
        str(original_request_path),
    ]
    need(
        wrapper["commands"][0]["argv"] == expected_prefix
        and wrapper["commands"][1]["argv"]
        == [*expected_prefix, "--run", "--output", str(child_path.parent)],
        "Original child actual argv differs",
    )
    for name in ("validate", "decode"):
        item = wrapper["commands"][0 if name == "validate" else 1]
        for channel in ("stdout", "stderr"):
            path = repository_file(str(wrapper_path.parent / (name + "." + channel)), root)
            originals[str(path)] = equal_record(item[channel], path)
    native_terminal = read_object(wrapper_path.parent / "decode.stdout")
    need(
        native_terminal.get("status") == "INCOMPLETE"
        and native_terminal.get("timing") is None
        and native_terminal.get("original_owner_status") == child.get("original_owner_status")
        and native_terminal.get("output") == str(child_path.parent),
        "Original child terminal does not bind retained incomplete output",
    )
    need(
        child.get("original_owner_status")
        == wrapper.get("original_owner_status")
        == capture["owner"]["status"]
        and wrapper.get("original_owner_purpose") == capture["owner"]["purpose"],
        "Original owner status/purpose changed",
    )
    need(
        64 * 1024 * 1024 < artifacts["native.stderr"]["bytes"] <= MEDIA.MAX_STDERR_BYTES,
        "Observed log not between explicit original and revised cap",
    )
    return {
        "wrapper": wrapper,
        "child": child,
        "capture": capture,
        "originals": originals,
        "directory": child_path.parent,
    }


def verify_png_index(stdout: BinaryIO, index: BinaryIO) -> tuple[dict[str, Any], list[int]]:
    count, size, digest, dimensions = 0, 0, hashlib.sha256(), None
    index_digest, index_bytes, frame_sizes = hashlib.sha256(), 0, []
    while first := stdout.read(8):
        need(len(first) == 8, "Retained stdout trailing partial frame")
        raw, actual = MEDIA.frame_png(stdout, first)
        count += 1
        size += len(raw)
        need(
            count <= MEDIA.MAX_FRAMES and size <= MEDIA.MAX_STDOUT_BYTES,
            "Retained frame/stdout budget",
        )
        shape = actual["width"], actual["height"], actual["channels"]
        need(dimensions is None or dimensions == shape, "Retained frame geometry changed")
        dimensions = shape
        expected = (json.dumps({"ordinal": count, **actual}, sort_keys=True) + "\n").encode()
        original_line = index.readline(MAX_INDEX_LINE + 1)
        need(
            len(original_line) <= MAX_INDEX_LINE and original_line == expected,
            "Retained index/full frame fields or canonical bytes differ",
        )
        index_digest.update(original_line)
        index_bytes += len(original_line)
        digest.update(raw)
        frame_sizes.append(len(raw))
    need(count > 0 and index.read(1) == b"", "Retained stdout/index complete EOF/count differs")
    return {
        "frame_count": count,
        "stdout_bytes": size,
        "stdout_sha256": digest.hexdigest(),
        "frame_geometry": list(dimensions) if dimensions else None,
        "eof_observed": True,
        "index_bytes": index_bytes,
        "index_sha256": index_digest.hexdigest(),
    }, frame_sizes


def tool_sources(root: Path) -> dict[str, Any]:
    names = (
        "scripts/w1_media_parse_only.py",
        "scripts/tests/test_w1_media_parse_only.py",
        "scripts/w1_media_decode.py",
        "scripts/tests/test_w1_media_decode.py",
    )
    return {name: record(repository_file(name, root)) for name in names}


def execute(request_path: Path, output: Path, root: Path = ROOT) -> dict[str, Any]:
    request_raw = request_path.read_bytes()
    request = read_object(request_path)
    need(
        output.resolve().is_relative_to((root / ".runtime").resolve()),
        "Output must be fresh within .runtime",
    )
    need(not output.exists(), "Overwrite refused")
    output.mkdir(parents=True)
    shutil.copyfile(request_path, output / "request.original.json")
    sources = tool_sources(root)
    for name in sources:
        shutil.copyfile(repository_file(name, root), output / Path(name).name)
    report: dict[str, Any] = {
        "protocol": PROTOCOL,
        "classification": "POSTHOC_PARSE_ONLY",
        "actual_decoder_this_invocation": False,
        "status": "INCOMPLETE",
        "started_at": datetime.now(UTC).isoformat(),
        "tool_sources_before": sources,
        "product_acceptance": "UNVERIFIED",
        "task_closed": False,
        "uncovered": [
            "current MVP source acceptance",
            "four-minute edited content",
            "three golden chains content review",
            "human audiovisual review",
            "all financial oracles",
        ],
    }
    clock = time.monotonic()
    prior: dict[str, Any] | None = None
    try:
        prior = validate_prior(request, root)
        report.update(
            {
                "originals_before": prior["originals"],
                "original_wrapper_status_retained": prior["wrapper"]["status"],
                "original_child_status_retained": prior["child"]["status"],
                "original_child_exit_code_retained": prior["wrapper"]["commands"][1]["exit_code"],
                "original_native_decoder_exit_code": prior["child"]["command"]["exit_code"],
                "original_owner_status": prior["capture"]["owner"]["status"],
                "original_owner_purpose": prior["capture"]["owner"]["purpose"],
                "source_binding": prior["capture"]["source_binding"],
                "capture_binding": prior["capture"]["capture_binding"],
                "run_id": request["run_id"],
            }
        )
        directory = Path(prior["directory"])
        with (
            (directory / "derived-png.stdout").open("rb") as stream,
            (directory / "derived-frame-index.jsonl").open("rb") as index,
        ):
            frames, sizes = verify_png_index(stream, index)
        original_frames = {
            key: value
            for key, value in frames.items()
            if key not in {"index_bytes", "index_sha256"}
        }
        need(
            original_frames == prior["child"]["frames"],
            "Re-read whole PNG report differs from original",
        )
        original_index = prior["child"]["artifacts"]["derived-frame-index.jsonl"]
        need(
            frames["index_bytes"] == original_index["bytes"]
            and frames["index_sha256"] == original_index["sha256"],
            "Re-read complete index bytes/SHA differs",
        )
        log = directory / "native.stderr"
        timing = MEDIA.native_timing(
            log.read_bytes(), sizes, prior["child"]["command"]["exit_code"]
        )
        after = {name: record(repository_file(name, root)) for name in prior["originals"]}
        source_after = tool_sources(root)
        need(
            after == prior["originals"]
            and source_after == sources
            and request_raw == request_path.read_bytes(),
            "Original bytes/source drifted during posthoc read",
        )
        report.update(
            {
                "frames": frames,
                "timing": timing,
                "originals_after": after,
                "tool_sources_after": source_after,
                "status": "POSTHOC_PARSE_ONLY_COMPLETE_SUPPORTED_STREAM_VERIFIED",
            }
        )
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    report.update(
        {"ended_at": datetime.now(UTC).isoformat(), "elapsed_seconds": time.monotonic() - clock}
    )
    MEDIA.write_new(output / "manifest.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.run:
            need(args.output is not None, "--run requires a fresh output")
            value = execute(args.request, args.output)
            print(
                json.dumps(
                    {
                        "classification": "POSTHOC_PARSE_ONLY",
                        "status": value["status"],
                        "actual_decoder_this_invocation": False,
                        "output": str(args.output),
                        "timing": value.get("timing"),
                        "product_acceptance": "UNVERIFIED",
                    }
                )
            )
            return (
                0
                if value["status"] == "POSTHOC_PARSE_ONLY_COMPLETE_SUPPORTED_STREAM_VERIFIED"
                else 2
            )
        need(args.output is None, "Default validation creates no output")
        validate_prior(read_object(args.request))
        print(
            json.dumps(
                {
                    "status": "VALIDATED_NOT_PARSED",
                    "classification": "POSTHOC_PARSE_ONLY",
                    "actual_decoder_this_invocation": False,
                    "product_acceptance": "UNVERIFIED",
                }
            )
        )
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {
                    "status": "INCOMPLETE",
                    "actual_decoder_this_invocation": False,
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "product_acceptance": "UNVERIFIED",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
