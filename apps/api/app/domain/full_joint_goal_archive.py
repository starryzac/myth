"""Lossless bounded JSON transport; no planning, permission or economic validation."""

import base64
import binascii
import hashlib
import json
import math
import struct
import zlib
from typing import Any, NoReturn, cast

PROTOCOL = "full-joint-goal-original-archive-v1"
CODEC = "canonical-json-gzip-mtime0-base64-v1"
MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_NODES = 1_000_000
MAX_DEPTH = 64
MAX_COMPRESSED_BYTES = MAX_DECODED_BYTES + 1024 * 1024
STREAM_BYTES = 1024 * 1024
_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
_FALSE_FLAGS = (
    "bank_authority",
    "grants_authority",
    "financial_write",
    "financial_validation_performed",
)
_FIELDS = {
    "protocol",
    "codec",
    "plaintext_sha256",
    "decoded_bytes",
    "compressed_bytes",
    "payload_base64",
    *_FALSE_FLAGS,
}


class JointArchiveError(ValueError):
    """An incomplete, noncanonical or out-of-budget original archive."""


def _limits(byte_limit: int, node_limit: int, depth_limit: int) -> None:
    for actual, maximum, minimum in (
        (byte_limit, MAX_DECODED_BYTES, 1),
        (node_limit, MAX_NODES, 1),
        (depth_limit, MAX_DEPTH, 0),
    ):
        if type(actual) is not int or not minimum <= actual <= maximum:
            raise JointArchiveError("Joint archive caller limit is invalid")


def _canonical(value: dict[str, Any], byte_limit: int, node_limit: int, depth_limit: int) -> bytes:
    if type(value) is not dict:
        raise JointArchiveError("Joint archive original must be a JSON object")
    nodes = 0
    byte_count = 0

    def add_size(size: int) -> None:
        nonlocal byte_count
        byte_count += size
        if byte_count > byte_limit:
            raise JointArchiveError("Joint archive original exceeds decoded byte budget")

    def string_size(text: str) -> None:
        add_size(2)
        for start in range(0, len(text), 4096):
            try:
                segment = json.dumps(text[start : start + 4096], ensure_ascii=False).encode()
            except UnicodeError as error:
                raise JointArchiveError("Joint archive string is not complete UTF-8") from error
            add_size(len(segment) - 2)

    def visit(child: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > node_limit or depth > depth_limit:
            raise JointArchiveError("Joint archive original exceeds node/depth budget")
        if child is None:
            add_size(4)
            return
        if type(child) is str:
            string_size(child)
            return
        if type(child) is bool:
            add_size(4 if child else 5)
            return
        if type(child) is int:
            if not -(2**63) <= child <= 2**63 - 1:
                raise JointArchiveError("Joint archive integer is outside signed64")
            add_size(len(str(child)))
            return
        if type(child) is float:
            if not math.isfinite(child):
                raise JointArchiveError("Joint archive float must be finite")
            add_size(len(json.dumps(child, allow_nan=False)))
            return
        if type(child) is list:
            add_size(2 + max(0, len(child) - 1))
            for item in child:
                visit(item, depth + 1)
            return
        if type(child) is dict:
            add_size(2 + max(0, len(child) - 1))
            for key, item in child.items():
                if type(key) is not str:
                    raise JointArchiveError("Joint archive JSON keys must be strings")
                string_size(key)
                add_size(1)
                if key.endswith("_cents") and item is not None and type(item) is not int:
                    raise JointArchiveError("Joint archive money must be integer cents")
                visit(item, depth + 1)
            return
        raise JointArchiveError("Joint archive accepts JSON original values only")

    visit(value, 0)
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (UnicodeError, ValueError, RecursionError) as error:
        raise JointArchiveError("Joint archive JSON cannot be encoded canonically") from error
    if len(raw) > byte_limit:
        raise JointArchiveError("Joint archive original exceeds decoded byte budget")
    if len(raw) != byte_count:
        raise JointArchiveError("Joint archive canonical size calculation differs")
    return raw


def _gzip(raw: bytes) -> bytes:
    compressor = zlib.compressobj(level=9, wbits=-zlib.MAX_WBITS)
    body = compressor.compress(raw) + compressor.flush()
    return _HEADER + body + struct.pack("<II", zlib.crc32(raw), len(raw) % (2**32))


def encode_joint_archive(
    value: dict[str, Any],
    *,
    max_decoded_bytes: int = MAX_DECODED_BYTES,
    max_nodes: int = MAX_NODES,
    max_depth: int = MAX_DEPTH,
) -> dict[str, Any]:
    """Retain exact canonical JSON, including finite nonmoney float/int/bool distinctions."""
    _limits(max_decoded_bytes, max_nodes, max_depth)
    raw = _canonical(value, max_decoded_bytes, max_nodes, max_depth)
    compressed = _gzip(raw)
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise JointArchiveError("Joint archive compressed bytes exceed budget")
    return {
        "protocol": PROTOCOL,
        "codec": CODEC,
        "plaintext_sha256": hashlib.sha256(raw).hexdigest(),
        "decoded_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "payload_base64": base64.b64encode(compressed).decode("ascii"),
        **dict.fromkeys(_FALSE_FLAGS, False),
    }


def _inflate(compressed: bytes, budget: int) -> bytes:
    if compressed[:10] != _HEADER:
        raise JointArchiveError("Joint archive gzip header differs from the fixed codec")
    inflater = zlib.decompressobj(wbits=zlib.MAX_WBITS + 16)
    output = bytearray()
    try:
        for start in range(0, len(compressed), STREAM_BYTES):
            pending = compressed[start : start + STREAM_BYTES]
            while pending:
                block = inflater.decompress(pending, min(STREAM_BYTES, budget + 1 - len(output)))
                if len(output) + len(block) > budget:
                    raise JointArchiveError("Joint archive decompression exceeds byte budget")
                output.extend(block)
                if inflater.unused_data:
                    raise JointArchiveError("Joint archive has trailing or multiple gzip streams")
                remaining = inflater.unconsumed_tail
                if remaining == pending and not block:
                    raise JointArchiveError("Joint archive decompression made no progress")
                pending = remaining
            if inflater.eof and start + STREAM_BYTES < len(compressed):
                raise JointArchiveError("Joint archive has trailing compressed bytes")
    except zlib.error as error:
        raise JointArchiveError("Joint archive gzip integrity or checksum is invalid") from error
    if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise JointArchiveError("Joint archive gzip is truncated or incomplete")
    return bytes(output)


def _json_budget(raw: bytes, node_limit: int, depth_limit: int) -> None:
    """Bound valid JSON token count before json.loads can allocate all its containers."""
    index = nodes = 0
    stack: list[int] = []

    def value_node(depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > node_limit or depth > depth_limit:
            raise JointArchiveError("Joint archive decoded JSON exceeds node/depth budget")

    while index < len(raw):
        token = raw[index]
        if token in b" \t\r\n,:":
            index += 1
            continue
        if token in b"{[":
            value_node(len(stack))
            stack.append(token)
            index += 1
        elif token in b"}]":
            if not stack or (stack.pop(), token) not in {(123, 125), (91, 93)}:
                raise JointArchiveError("Joint archive JSON containers are malformed")
            index += 1
        elif token == 34:
            index += 1
            while index < len(raw) and raw[index] != 34:
                index += 2 if raw[index] == 92 else 1
            if index >= len(raw):
                raise JointArchiveError("Joint archive JSON string is truncated")
            index += 1
            lookahead = index
            while lookahead < len(raw) and raw[lookahead] in b" \t\r\n":
                lookahead += 1
            if lookahead == len(raw) or raw[lookahead] != 58:
                value_node(len(stack))
        else:
            value_node(len(stack))
            while index < len(raw) and raw[index] not in b" \t\r\n,:{}[]":
                index += 1
    if stack:
        raise JointArchiveError("Joint archive JSON containers are truncated")


def _pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in rows:
        if key in result:
            raise JointArchiveError("Joint archive JSON duplicate key is forbidden")
        result[key] = value
    return result


def _constant(_value: str) -> NoReturn:
    raise JointArchiveError("Joint archive JSON constant must be finite")


def decode_joint_archive(
    envelope: dict[str, Any],
    *,
    max_decoded_bytes: int = MAX_DECODED_BYTES,
    max_nodes: int = MAX_NODES,
    max_depth: int = MAX_DEPTH,
) -> dict[str, Any]:
    """Return full original JSON only; caller must separately revalidate economic semantics."""
    _limits(max_decoded_bytes, max_nodes, max_depth)
    if (
        type(envelope) is not dict
        or set(envelope) != _FIELDS
        or envelope["protocol"] != PROTOCOL
        or envelope["codec"] != CODEC
        or any(envelope[key] is not False for key in _FALSE_FLAGS)
    ):
        raise JointArchiveError("Joint archive envelope shape, codec or no-authority flags differ")
    size, compressed_size = envelope["decoded_bytes"], envelope["compressed_bytes"]
    digest, text = envelope["plaintext_sha256"], envelope["payload_base64"]
    if (
        type(size) is not int
        or not 1 <= size <= max_decoded_bytes
        or type(compressed_size) is not int
        or not 1 <= compressed_size <= MAX_COMPRESSED_BYTES
        or type(digest) is not str
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or type(text) is not str
        or len(text) > 4 * ((MAX_COMPRESSED_BYTES + 2) // 3)
    ):
        raise JointArchiveError("Joint archive envelope size, hash or encoded value is invalid")
    try:
        compressed = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error) as error:
        raise JointArchiveError("Joint archive base64 is invalid") from error
    if len(compressed) != compressed_size or base64.b64encode(compressed).decode("ascii") != text:
        raise JointArchiveError("Joint archive encoded length or canonical base64 differs")
    raw = _inflate(compressed, size)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise JointArchiveError("Joint archive plaintext length or hash differs")
    _json_budget(raw, max_nodes, max_depth)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise JointArchiveError("Joint archive plaintext is not strict JSON") from error
    if _canonical(value, max_decoded_bytes, max_nodes, max_depth) != raw:
        raise JointArchiveError("Joint archive plaintext is not canonical original JSON")
    return cast(dict[str, Any], value)
