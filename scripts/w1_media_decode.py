"""Explicit read-only VP8 capture decoder candidate. Never closes product acceptance.

Default validates original bindings without starting a decoder subprocess.
CURRENT_SOURCE MVP_ACCEPTANCE also invokes the original read-only Git/source gate.
--run decodes every supported VP8 frame into a fresh DERIVED output directory.
The concatenated PNG stdout is never a screenshot/native-capture substitute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import shutil
import struct
import subprocess
import threading
import time
import zlib
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, cast
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "bounded-funds-supported-media-decode-v1"
REQUEST_PROTOCOL = "bounded-funds-media-decode-request-v1"
DECODER_RELATIVE = ".runtime/playwright-browsers/ffmpeg-1011/ffmpeg-win64.exe"
DECODER_SHA = "5b8f3f59ba61685828939ff3c833109748adbdea2fff4b4ae570c9fc0fc1ff4d"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_STDOUT_BYTES = 8 * 1024 * 1024 * 1024
MAX_STDERR_BYTES = 128 * 1024 * 1024
MAX_FRAME_PNG = 16 * 1024 * 1024
MAX_FRAME_SCANLINES = 64 * 1024 * 1024
MAX_FRAMES = 60000
TIMEOUT_SECONDS = 600
AGENT_BINDING_METHOD = "ACTUAL_NAVIGATOR_EQUALS_ALL_ORIGINAL_TRACE_HAR_USER_AGENTS"
METHOD_REVISION = "LIVE_MEDIA_IMPORT_PATH_AND_CURRENT_CONTEXT_FRAME_LOG_BUDGET_V3"


def need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def object_value(value: Any, label: str) -> dict[str, Any]:
    need(isinstance(value, dict), label + " must be an object")
    return value  # type: ignore[no-any-return]


def strict_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            need(key not in value, "Duplicate JSON key")
            value[key] = item
        return value

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: invalid_number())


def invalid_number() -> Any:
    raise ValueError("Nonfinite JSON number")


def checksum(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            size += len(block)
            sha.update(block)
    return {"path": str(path), "bytes": size, "sha256": sha.hexdigest()}


def relative(value: Any) -> str:
    need(isinstance(value, str) and bool(value) and "\x00" not in value, "Invalid original path")
    name = cast(str, value).replace("\\", "/")
    parts = PurePosixPath(name).parts
    need(
        not PurePosixPath(name).is_absolute()
        and all(part not in {".", ".."} and ":" not in part for part in parts)
        and "/".join(parts) == name,
        "Original path must be exact, relative and without aliases",
    )
    return name


def repository_file(name: Any, root: Path = ROOT) -> Path:
    rel = relative(name)
    candidate = root.joinpath(*rel.split("/"))
    resolved = candidate.resolve(strict=True)
    need(resolved.is_relative_to(root.resolve()), "Original path escapes repository")
    for parent in [candidate, *candidate.parents]:
        if parent == root.parent:
            break
        need(not parent.is_symlink(), "Symlink original unsupported")
    need(resolved.is_file(), "Original path is not a file")
    return resolved


def artifact_index(owner: dict[str, Any]) -> dict[str, str]:
    actual: dict[str, str] = {}
    for name, digest in object_value(
        owner.get("artifact_hashes"), "original artifact index"
    ).items():
        key = relative(name)
        need(key not in actual, "Duplicate path aliases in original artifact index")
        need(
            isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest) is not None,
            "Invalid original artifact SHA",
        )
        actual[key] = digest
    return actual


def tool_sources(root: Path = ROOT) -> dict[str, Any]:
    return {
        name: file_record(repository_file(name, root))
        for name in (
            "scripts/w1_media_decode.py",
            "scripts/tests/test_w1_media_decode.py",
            "scripts/w1_current_check_context.py",
            "scripts/w1_final_acceptance.py",
            "scripts/export_evidence.py",
        )
    }


def current_source_state(root: Path) -> dict[str, Any]:
    state = runpy.run_path(str(root / "scripts/w1_final_acceptance.py"))["source_state"](root)
    return object_value(state, "current final-check source state")


def validate_parent_context(
    owner: dict[str, Any], evidence_use: str, root: Path = ROOT
) -> dict[str, Any]:
    purpose = owner.get("purpose", "DEVELOPMENT")
    need(purpose in {"DEVELOPMENT", "MVP_ACCEPTANCE"}, "Unknown original capture purpose")
    acceptance = owner.get("acceptance_context")
    if evidence_use == "HISTORICAL_CONTEXT":
        return {
            "original_capture_purpose": purpose,
            "original_acceptance_context": acceptance,
            "current_parent_context_verified": False,
            "parent_context_status": "HISTORICAL_UNVERIFIED",
        }
    if purpose == "DEVELOPMENT":
        need(acceptance is None, "Development cannot inherit final-check context")
        return {
            "original_capture_purpose": purpose,
            "current_parent_context_verified": False,
            "parent_context_status": "DEVELOPMENT_NOT_ACCEPTANCE",
        }
    need(evidence_use == "CURRENT_SOURCE", "Unknown current acceptance evidence use")
    registered = object_value(acceptance, "original current-check context")
    context_path = repository_file(registered.get("path"), root)
    source = current_source_state(root)
    files = object_value(source.get("files"), "full registered current-check source")
    for name in ("scripts/w1_media_decode.py", "scripts/tests/test_w1_media_decode.py"):
        need(
            files.get(name) == file_record(repository_file(name, root))["sha256"],
            "Media producer/tests absent from original full current-check inventory",
        )
    binding = runpy.run_path(str(ROOT / "scripts/w1_current_check_context.py"))["bind_context"](
        root, str(context_path), source
    )
    need(binding == registered, "Original parent context bytes/source differs")
    context = object_value(strict_json(context_path.read_bytes()), "original parent context bytes")
    need(
        context.get("database") == owner.get("database"),
        "Parent/capture actual owned database differs",
    )
    return {
        "original_capture_purpose": purpose,
        "current_parent_context_verified": True,
        "parent_context_status": "ORIGINAL_CURRENT_CONTEXT_RECOMPUTED",
        "acceptance_context": binding,
    }


def validate_capture_source(
    owner: dict[str, Any], evidence_use: str, root: Path = ROOT
) -> dict[str, Any]:
    before = object_value(owner.get("source_before"), "original capture before sources")
    need(bool(before), "Original capture before source denominator missing")
    source: dict[str, str] = {}
    for key, digest in before.items():
        name = relative(key)
        need(name not in source, "Source path aliases differ")
        need(
            isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest) is not None,
            "Original source SHA invalid",
        )
        source[name] = digest
    if evidence_use == "CURRENT_SOURCE":
        need(owner.get("source_after") == before, "Original capture source incomplete/drifted")
        for name, digest in source.items():
            need(
                file_record(repository_file(name, root))["sha256"] == digest,
                "Capture source is not current",
            )
        return {
            "source_binding": "CURRENT_SOURCE_REHASHED",
            "capture_source_relation": "BEFORE_AFTER_EQUAL_CURRENT",
            "capture_producer_current_source_verified": True,
        }
    need(evidence_use == "HISTORICAL_CONTEXT", "Unknown capture source purpose")
    relation = (
        "CAPTURE_SOURCE_AFTER_MISSING"
        if not owner.get("source_after")
        else (
            "HISTORICAL_BEFORE_AFTER_EQUAL"
            if owner["source_after"] == before
            else "CAPTURE_SOURCE_DRIFTED"
        )
    )
    return {
        "source_binding": "HISTORICAL_DECLARED_SOURCE_ONLY_NOT_CURRENT_ACCEPTANCE",
        "capture_source_relation": relation,
        "capture_producer_current_source_verified": False,
    }


def trace_binding(path: Path, video: dict[str, Any], browser: dict[str, Any]) -> dict[str, Any]:
    need(path.stat().st_size <= 64 * 1024 * 1024, "Original trace exceeds budget")
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            need(
                0 < len(entries) <= 3000 and len(set(names)) == len(names),
                "Trace member count/alias",
            )
            need(
                sum(entry.file_size for entry in entries) <= 128 * 1024 * 1024,
                "Trace decoded budget",
            )
            for entry in entries:
                relative(entry.filename)
                need(not entry.is_dir() and not entry.flag_bits & 1, "Unsupported trace entry")
            need(
                "test.trace" in names and "0-trace.trace" in names, "Original trace events missing"
            )

            def events(name: str) -> list[dict[str, Any]]:
                raw = archive.read(name)
                need(len(raw) <= 32 * 1024 * 1024, "Trace event budget")
                return [
                    object_value(strict_json(line), "trace event")
                    for line in raw.splitlines()
                    if line.strip()
                ]

            runner, capture = events("test.trace"), events("0-trace.trace")
            contexts = [entry for entry in capture if entry.get("type") == "context-options"]
            need(len(contexts) == 1, "Original context count differs")
            context = contexts[0]
            options = object_value(context.get("options"), "native context options")
            actual_agent = browser.get("actual_user_agent")
            need(
                context.get("version") == 8
                and context.get("origin") == "library"
                and context.get("channel") == "msedge"
                and context.get("browserName") == "chromium"
                and bool(context.get("contextId"))
                and isinstance(options.get("recordVideo"), dict)
                and isinstance(actual_agent, str)
                and "Edg/" in actual_agent
                and (context.get("userAgent") is None or context["userAgent"] == actual_agent),
                "Original Edge capture context differs",
            )
            # Default browser UA is absent from context-options. Independently bind
            # actual navigator output to every original native HAR request header.
            need("0-trace.network" in names, "Original native HTTP user-agent proof missing")
            network = events("0-trace.network")
            need(bool(network), "Original native HTTP user-agent denominator empty")
            for event in network:
                snapshot = object_value(event.get("snapshot"), "native HAR snapshot")
                request = object_value(snapshot.get("request"), "native HAR request")
                headers = request.get("headers")
                if not isinstance(headers, list):
                    raise ValueError("Native request headers missing")
                agents = [
                    header.get("value")
                    for header in headers
                    if isinstance(header, dict)
                    and str(header.get("name", "")).lower() == "user-agent"
                ]
                need(agents == [actual_agent], "Original native HTTP/browser agent differs")
            videos = [
                attachment
                for event in runner
                for attachment in event.get("attachments", [])
                if attachment.get("name") == "video"
                and attachment.get("contentType") == "video/webm"
            ]
            need(len(videos) == 1, "Exactly one native complete-session attachment required")
            native_sha = videos[0].get("sha1")
            need(
                isinstance(native_sha, str)
                and re.fullmatch("[0-9a-f]{40}", native_sha) is not None,
                "Original Playwright SHA1 resource missing",
            )
            resource = "resources/" + native_sha
            need(resource in names, "Original video resource missing")
            sha256, sha1, count = hashlib.sha256(), hashlib.sha1(usedforsecurity=False), 0
            with archive.open(resource) as stream:
                while block := stream.read(1024 * 1024):
                    count += len(block)
                    need(count <= MAX_INPUT_BYTES, "Video trace resource budget")
                    sha256.update(block)
                    sha1.update(block)
            need(
                count == video["bytes"]
                and sha256.hexdigest() == video["sha256"]
                and sha1.hexdigest() == native_sha,
                "Native attachment/video bytes differ",
            )
            return {
                "context_id": context["contextId"],
                "channel": "msedge",
                "native_playwright_sha1": native_sha,
                "trace_version": 8,
                "agent_binding_method": AGENT_BINDING_METHOD,
                "original_user_agent_header_count": len(network),
                "resource_bytes": count,
                "complete_content_review": "NOT_RUN",
            }
    except BadZipFile as error:
        raise ValueError("Corrupt original native trace") from error


def validate_request(request: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    need(
        set(request) == {"protocol", "evidence_use", "owner_manifest", "video", "run_id"},
        "Exact request fields required",
    )
    need(request["protocol"] == REQUEST_PROTOCOL, "Unknown decoder request protocol")
    need(
        request["evidence_use"] in {"HISTORICAL_CONTEXT", "CURRENT_SOURCE"}, "Unknown evidence use"
    )
    need(isinstance(request["run_id"], str) and bool(request["run_id"]), "Original run ID missing")
    manifest_path = repository_file(request["owner_manifest"], root)
    owner = object_value(strict_json(manifest_path.read_bytes()), "native capture manifest")
    need(owner.get("run_id") == request["run_id"], "Original capture run differs")
    need(
        owner.get("classification") == "ACTUAL_UI_ACCEPTANCE_NOT_PRODUCT_SLA"
        or owner.get("protocol") == "bounded-funds-offline-browser-v1",
        "Unsupported capture producer",
    )
    source_binding = validate_capture_source(owner, request["evidence_use"], root)
    parent_context = validate_parent_context(owner, request["evidence_use"], root)
    index = artifact_index(owner)
    video_path = repository_file(request["video"], root)
    need(
        video_path.is_relative_to(manifest_path.parent) and video_path.name == "video.webm",
        "Video must be the original native-session output",
    )
    video_rel = video_path.relative_to(manifest_path.parent).as_posix()
    need(
        index.get(video_rel) == file_record(video_path)["sha256"],
        "Original video index binding differs",
    )
    video = file_record(video_path)
    need(1024 < video["bytes"] <= MAX_INPUT_BYTES, "Original video input budget/empty")
    browser_path, trace_path = (
        video_path.with_name("actual-browser.json"),
        video_path.with_name("trace.zip"),
    )
    original_records = {}
    for path in (video_path, browser_path, trace_path):
        safe = repository_file(path.relative_to(root).as_posix(), root)
        record = file_record(safe)
        need(
            index.get(path.relative_to(manifest_path.parent).as_posix()) == record["sha256"],
            "Original session artifact index differs",
        )
        original_records[str(path)] = record
    browser = object_value(strict_json(browser_path.read_bytes()), "native browser identity")
    need(
        browser.get("run_id") == request["run_id"]
        and browser.get("requested_channel") == "msedge"
        and bool(browser.get("actual_engine_version"))
        and bool(browser.get("scenario_id")),
        "Native browser session differs",
    )
    decoder = repository_file(DECODER_RELATIVE, root)
    decoder_record = file_record(decoder)
    need(decoder_record["sha256"] == DECODER_SHA, "Trusted actual decoder binary changed")
    bindings = {
        str(manifest_path): file_record(manifest_path),
        **original_records,
        str(decoder): decoder_record,
    }
    return {
        "owner": owner,
        "video_path": video_path,
        "decoder_path": decoder,
        "bindings": bindings,
        "capture_binding": trace_binding(trace_path, video, browser),
        **source_binding,
        **parent_context,
    }


def read_exact(stream: BinaryIO, count: int) -> bytes:
    need(0 <= count <= MAX_FRAME_PNG, "Decoder read budget")
    chunks, left = [], count
    while left:
        block = stream.read(left)
        need(bool(block), "Decoder stdout truncated before frame EOF")
        chunks.append(block)
        left -= len(block)
    return b"".join(chunks)


def frame_png(stream: BinaryIO, first: bytes) -> tuple[bytes, dict[str, Any]]:
    need(first == PNG_SIGNATURE, "Decoder stdout is not a complete PNG frame stream")
    raw, compressed = bytearray(first), bytearray()
    types: list[bytes] = []
    width = height = channels = 0
    while True:
        header = read_exact(stream, 8)
        count, kind = struct.unpack(">I4s", header)
        need(
            count <= MAX_FRAME_PNG and len(raw) + count + 12 <= MAX_FRAME_PNG,
            "Frame PNG byte budget",
        )
        body, crc_raw = read_exact(stream, count), read_exact(stream, 4)
        need(
            zlib.crc32(kind + body) & 0xFFFFFFFF == struct.unpack(">I", crc_raw)[0],
            "Frame PNG CRC differs",
        )
        raw.extend(header + body + crc_raw)
        if kind == b"IHDR":
            need(not types and count == 13, "Frame IHDR count/order")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            need(
                0 < width <= 20000
                and 0 < height <= 20000
                and depth == 8
                and color in {2, 6}
                and compression == filtering == interlace == 0,
                "Unsupported decoded PNG frame layout",
            )
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            need(
                bool(types)
                and b"IHDR" == types[0]
                and (b"IDAT" not in types or types[-1] == b"IDAT"),
                "Frame PNG IDAT order",
            )
            compressed.extend(body)
        elif kind == b"IEND":
            need(count == 0 and bool(compressed), "Frame PNG trailer/data")
            break
        else:
            need(
                bool(types) and kind[0] & 32 != 0 and kind not in {b"acTL", b"fcTL", b"fdAT"},
                "Unknown critical/animated PNG chunk",
            )
        types.append(kind)
    expected = height * (1 + width * channels)
    need(0 < expected <= MAX_FRAME_SCANLINES, "Frame scanline budget")
    decoder = zlib.decompressobj()
    scans = decoder.decompress(bytes(compressed), expected + 1)
    need(
        decoder.eof
        and not decoder.unused_data
        and not decoder.unconsumed_tail
        and len(scans) == expected,
        "Frame decoded scanline EOF/size differs",
    )
    stride = 1 + width * channels
    need(
        all(scans[offset] <= 4 for offset in range(0, expected, stride)),
        "Frame scanline filter invalid",
    )
    return bytes(raw), {
        "width": width,
        "height": height,
        "channels": channels,
        "png_bytes": len(raw),
        "png_sha256": checksum(bytes(raw)),
        "scanline_bytes": expected,
        "scanline_sha256": checksum(bytes(scans)),
    }


def decode_stream(stream: BinaryIO, original: BinaryIO, index: BinaryIO) -> dict[str, Any]:
    frames, count, digest, dimensions = 0, 0, hashlib.sha256(), None
    while True:
        first = stream.read(8)
        if not first:
            break
        need(len(first) == 8, "Decoder stdout trailing partial frame")
        raw, record = frame_png(stream, first)
        frames += 1
        count += len(raw)
        need(
            frames <= MAX_FRAMES and count <= MAX_STDOUT_BYTES,
            "Complete decode frame/stdout budget",
        )
        shape = record["width"], record["height"], record["channels"]
        need(dimensions is None or shape == dimensions, "Native recording changes frame geometry")
        dimensions = shape
        original.write(raw)
        digest.update(raw)
        index.write((json.dumps({"ordinal": frames, **record}, sort_keys=True) + "\n").encode())
    need(frames > 0, "No supported frame was actually decoded")
    return {
        "frame_count": frames,
        "stdout_bytes": count,
        "stdout_sha256": digest.hexdigest(),
        "frame_geometry": dimensions,
        "eof_observed": True,
    }


def native_timing(raw: bytes, frame_sizes: list[int], exit_code: int) -> dict[str, Any]:
    frame_count = len(frame_sizes)
    need(type(exit_code) is int and exit_code == 0, "Actual decoder exited unsuccessfully")
    need(
        0 < frame_count <= MAX_FRAMES
        and all(type(size) is int and 0 < size <= MAX_FRAME_PNG for size in frame_sizes),
        "Actual decoded frame sizes/count invalid",
    )
    need(len(raw) <= MAX_STDERR_BYTES, "Native decoder stderr budget")
    text = raw.decode("utf-8", errors="strict")
    need(
        not re.search(r"\[(?:warning|error|fatal|panic)\]", text, re.I),
        "Native decode warning/error",
    )
    try:
        input_header = text.split("[info] Input #0, matroska,webm,", 1)[1].split(
            "[info] Stream mapping:", 1
        )[0]
    except IndexError as error:
        raise ValueError("Unsupported actual decoder input protocol") from error
    tracks = re.findall(r"\[info\]\s+Stream #0:(\d+): ([^\r\n]+)", input_header)
    need(
        len(tracks) == 1
        and tracks[0][0] == "0"
        and tracks[0][1].startswith("Video: vp8 (libvpx),"),
        "Complete decoding supports exactly one original VP8 video track and no audio/data",
    )
    demux = re.findall(
        r"demuxer -> ist_index:0:0 type:video pkt_pts:(-?\d+) pkt_pts_time:[^ ]+ "
        r"pkt_dts:-?\d+ pkt_dts_time:[^ ]+ duration:(\d+) duration_time:[^\r\n]+",
        text,
    )
    decoder = re.findall(
        r"decoder -> pts:(-?\d+) pts_time:[^ ]+ pkt_dts:-?\d+ pkt_dts_time:[^ ]+ "
        r"duration:(\d+) duration_time:[^ ]+ keyframe:\d+ frame_type:\d+ "
        r"time_base:(\d+)/(\d+)",
        text,
    )
    encoded = re.findall(
        r"encoder -> type:video pkt_pts:(-?\d+) pkt_pts_time:([^ ]+) "
        r"pkt_dts:-?\d+ pkt_dts_time:[^ ]+ duration:\d+ duration_time:([^\r\n]+)",
        text,
    )
    muxed = re.findall(
        r"muxer <- pts:(-?\d+) pts_time:([^ ]+) dts:-?\d+ dts_time:[^ ]+ "
        r"duration:\d+ duration_time:([^ ]+) size:(\d+)",
        text,
    )
    need(
        len(demux) == len(decoder) == len(encoded) == len(muxed) == frame_count,
        "Original demux/decoder/PNG encoder/muxer/full-stdout counts differ",
    )
    ends: list[Fraction] = []
    times: list[Fraction] = []
    for ordinal, (packet, decoded, output, mux) in enumerate(
        zip(demux, decoder, encoded, muxed, strict=True)
    ):
        pts, duration, numerator, denominator = map(int, decoded)
        need(
            numerator > 0 and denominator > 0 and duration > 0,
            "Invalid actual frame time base/duration",
        )
        need(tuple(map(int, packet)) == (pts, duration), "Demux packet and decoded frame differ")
        timestamp, length = (
            Fraction(pts * numerator, denominator),
            Fraction(duration * numerator, denominator),
        )
        need(
            timestamp >= 0 and (not ends or timestamp == ends[-1]),
            "Unsupported frame time gap/overlap",
        )
        need(
            Fraction(output[1]) == timestamp
            and Fraction(output[2]) == length
            and Fraction(mux[1]) == timestamp
            and Fraction(mux[2]) == length,
            "Original timestamps changed during full decode",
        )
        need(
            int(output[0]) == int(mux[0]) == ordinal and int(mux[3]) == frame_sizes[ordinal],
            "Frame output sequence/native muxed PNG size differs",
        )
        times.append(timestamp)
        ends.append(timestamp + length)
    final = re.findall(r"\[info\] frame=\s*(\d+)[^\r\n]*Lsize=", text)
    need(len(final) == 1 and int(final[0]) == frame_count, "Native EOF completion counter differs")
    need("audio:0KiB subtitle:0KiB other streams:0KiB" in text, "Uncovered native output stream")
    clip_duration = ends[-1] - times[0]
    return {
        "decoded_frame_count": frame_count,
        "demux_packet_count": len(demux),
        "first_pts_seconds": str(times[0]),
        "last_frame_pts_seconds": str(times[-1]),
        "end_pts_seconds": str(ends[-1]),
        "actual_duration_rational_seconds": str(clip_duration),
        "actual_duration_seconds": float(clip_duration),
        "video_tracks": 1,
        "audio_tracks": 0,
        "duration_method": "ALL_ORIGINAL_DECODED_FRAME_PTS_PLUS_LAST_DURATION_NOT_HEADER",
        "complete_content_review": "NOT_RUN",
        "four_minute_content": "UNVERIFIED",
    }


def decode_argv(decoder: Path, video: Path) -> list[str]:
    return [
        str(decoder),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "level+info",
        "-debug_ts",
        "-xerror",
        "-err_detect",
        "explode",
        "-copyts",
        "-i",
        str(video),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-fps_mode",
        "passthrough",
        "-c:v",
        "png",
        "-f",
        "image2",
        "-update",
        "1",
        "pipe:1",
    ]


def write_new(path: Path, value: Any) -> None:
    raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)


def execute(request_path: Path, output: Path, root: Path = ROOT) -> dict[str, Any]:
    request_raw = request_path.read_bytes()
    request = object_value(strict_json(request_raw), "decoder request")
    binding = validate_request(request, root)
    need(
        output.resolve().is_relative_to((root / ".runtime").resolve()),
        "Derived output must remain in .runtime",
    )
    need(not output.exists(), "Output already exists; preserve originals and use a fresh path")
    output.mkdir(parents=True)
    with (output / "request.original.json").open("xb") as stream:
        stream.write(request_raw)
    source_path = Path(__file__)
    source_before = file_record(source_path)
    tools_before = tool_sources(root)
    shutil.copyfile(source_path, output / "producer.original.py")
    report: dict[str, Any] = {
        "protocol": PROTOCOL,
        "method_revision": METHOD_REVISION,
        "status": "INCOMPLETE",
        "run_id": request["run_id"],
        "evidence_use": request["evidence_use"],
        "original_owner_status": binding["owner"].get("status"),
        "capture_binding": binding["capture_binding"],
        "source_binding": binding["source_binding"],
        "capture_source_relation": binding["capture_source_relation"],
        "capture_producer_current_source_verified": binding[
            "capture_producer_current_source_verified"
        ],
        "originals_before": binding["bindings"],
        "producer_before": source_before,
        "tool_sources_before": tools_before,
        "parent_context_before": validate_parent_context(
            binding["owner"], request["evidence_use"], root
        ),
        "product_acceptance": "UNVERIFIED",
        "task_closed": False,
        "output_classification": "DERIVED_DECODER_STDOUT_NOT_NATIVE_SCREENSHOTS",
        "uncovered": [
            "complete four-minute narrated content",
            "backup full three-chain content",
            "current product acceptance",
            "manual consistency review",
            "all financial oracles",
        ],
    }
    argv = decode_argv(binding["decoder_path"], binding["video_path"])
    report["command"] = {"argv": argv, "started_at": datetime.now(UTC).isoformat()}
    process: subprocess.Popen[bytes] | None = None
    timed_out = threading.Event()
    clock = time.monotonic()
    try:
        with (
            (output / "native.stderr").open("xb") as stderr,
            (output / "derived-png.stdout").open("xb") as stdout,
            (output / "derived-frame-index.jsonl").open("xb") as index,
        ):
            process = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr
            )

            def timeout() -> None:
                timed_out.set()
                if process is not None and process.poll() is None:
                    process.kill()

            timer = threading.Timer(TIMEOUT_SECONDS, timeout)
            timer.start()
            try:
                assert process.stdout is not None, "Actual decoder stdout pipe missing"
                frames = decode_stream(cast(BinaryIO, process.stdout), stdout, index)
                code = process.wait(timeout=10)
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                if process.stdout is not None:
                    process.stdout.close()
        report["command"].update(
            {
                "exit_code": code,
                "ended_at": datetime.now(UTC).isoformat(),
                "elapsed_seconds": time.monotonic() - clock,
                "timed_out": timed_out.is_set(),
            }
        )
        need(not timed_out.is_set(), "Actual decoder deadline exceeded")
        report["frames"] = frames
        frame_sizes = [
            object_value(strict_json(line), "derived frame index")["png_bytes"]
            for line in (output / "derived-frame-index.jsonl").read_bytes().splitlines()
        ]
        need(
            (output / "native.stderr").stat().st_size <= MAX_STDERR_BYTES,
            "Native decoder stderr budget",
        )
        report["timing"] = native_timing((output / "native.stderr").read_bytes(), frame_sizes, code)
        report["originals_after"] = {name: file_record(Path(name)) for name in binding["bindings"]}
        report["producer_after"] = file_record(source_path)
        report["tool_sources_after"] = tool_sources(root)
        report["parent_context_after"] = validate_parent_context(
            binding["owner"], request["evidence_use"], root
        )
        need(
            report["originals_before"] == report["originals_after"]
            and source_before == report["producer_after"]
            and tools_before == report["tool_sources_after"]
            and report["parent_context_before"] == report["parent_context_after"],
            "Original capture/decoder/producer changed during actual decode",
        )
        report["status"] = "COMPLETE_SUPPORTED_VIDEO_DECODE_OBSERVED"
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        report["command"].update(
            {
                "exit_code": process.returncode if process else None,
                "ended_at": datetime.now(UTC).isoformat(),
                "elapsed_seconds": time.monotonic() - clock,
                "timed_out": timed_out.is_set(),
            }
        )
    report["artifacts"] = {
        path.name: file_record(path) for path in output.iterdir() if path.is_file()
    }
    write_new(output / "manifest.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    try:
        if args.run:
            need(args.output is not None, "Explicit --run requires a fresh --output")
            result = execute(args.request, args.output)
            print(
                json.dumps(
                    {
                        "status": result["status"],
                        "output": str(args.output),
                        "original_owner_status": result["original_owner_status"],
                        "timing": result.get("timing"),
                        "product_acceptance": "UNVERIFIED",
                    }
                )
            )
            return 0 if result["status"] == "COMPLETE_SUPPORTED_VIDEO_DECODE_OBSERVED" else 2
        need(args.output is None, "Default validation creates no output")
        request = object_value(strict_json(args.request.read_bytes()), "decoder request")
        result = validate_request(request)
        print(
            json.dumps(
                {
                    "status": "VALIDATED_NOT_DECODED",
                    "original_owner_status": result["owner"].get("status"),
                    "source_binding": result["source_binding"],
                    "capture_source_relation": result["capture_source_relation"],
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
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "product_acceptance": "UNVERIFIED",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
