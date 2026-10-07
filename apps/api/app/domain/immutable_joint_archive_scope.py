"""Request-local pure wire proofs; current native checks never live here."""

import json
import zlib
from _thread import LockType
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, cast

from app.domain.policy_configuration import configuration_hash

V3 = "registered-joint-goal-execution-archive-v3"
V4 = "registered-joint-goal-execution-source-dag-v4"
MAX_PROOFS = 16
MAX_DECODED_ORIGINALS = 16
MAX_DECODED_TOTAL_BYTES = 64 * 1024 * 1024
MAX_CACHED_COMPRESSED_BYTES = 8 * 1024 * 1024


@dataclass
class _Request:
    proved: set[str] = field(default_factory=set)
    decoded: dict[str, tuple[bytes, int]] = field(default_factory=dict)
    total_bytes: int = 0
    lock: LockType = field(default_factory=Lock)


_request: ContextVar[_Request | None] = ContextVar("joint_immutable_wire_request", default=None)


@contextmanager
def immutable_joint_archive_scope() -> Iterator[None]:
    if _request.get() is not None:
        yield
        return
    token = _request.set(_Request())
    try:
        yield
    finally:
        _request.reset(token)


def verify_complete_wire(
    protocol: str,
    complete_wire: Mapping[str, Any],
    uncached_pure_verifier: Callable[[], None],
) -> None:
    """Positive pure trace proof only, keyed by every byte-relevant wire field."""
    current = _request.get()
    if protocol not in {V3, V4} or current is None:
        uncached_pure_verifier()
        return
    identity = configuration_hash({"kind": "frozen-trace", "wire": dict(complete_wire)})
    with current.lock:
        if identity in current.proved:
            return
    uncached_pure_verifier()
    if identity != configuration_hash({"kind": "frozen-trace", "wire": dict(complete_wire)}):
        raise ValueError("Pure verifier changed the complete original wire")
    with current.lock:
        if len(current.proved) < MAX_PROOFS:
            current.proved.add(identity)


def decode_complete_original(
    protocol: str,
    complete_arguments: Mapping[str, Any],
    decoded_bytes: int,
    uncached_complete_decoder: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Return an isolated original, never a cached caller-mutable dictionary."""
    current = _request.get()
    if protocol not in {V3, V4} or current is None:
        return uncached_complete_decoder()
    identity = configuration_hash({"kind": "record-decode", "args": dict(complete_arguments)})
    with current.lock:
        previous = current.decoded.get(identity)
    if previous is not None:
        # Only server-generated immutable bytes of a successfully decoded original.
        # The full caller wire/owner/reference/caps key was just rehashed above.
        if (
            type(previous[0]) is not bytes
            or type(previous[1]) is not int
            or not 0 < previous[1] <= MAX_DECODED_TOTAL_BYTES
            or len(previous[0]) > MAX_CACHED_COMPRESSED_BYTES
        ):
            raise ValueError("Cached original metadata exceeds private byte bounds")
        inflater = zlib.decompressobj()
        try:
            raw = inflater.decompress(previous[0], previous[1] + 1)
        except zlib.error as error:
            raise ValueError("Cached original compressed integrity differs") from error
        if (
            len(raw) != previous[1]
            or not inflater.eof
            or inflater.unused_data
            or inflater.unconsumed_tail
        ):
            raise ValueError("Cached original plaintext length or integrity differs")
        result = json.loads(raw)
        if type(result) is not dict:
            raise ValueError("Cached original must remain a complete JSON object")
        return cast(dict[str, Any], result)
    original = uncached_complete_decoder()
    if identity != configuration_hash({"kind": "record-decode", "args": dict(complete_arguments)}):
        raise ValueError("Pure decoder changed its complete original arguments")
    # Native decoding already enforced canonical JSON, complete binding and every
    # caller node/depth/byte cap. Store that exact complete result as private bytes,
    # rather than retaining repeated million-node Python object graphs.
    if type(decoded_bytes) is not int or not 0 < decoded_bytes <= MAX_DECODED_TOTAL_BYTES:
        return original
    raw = json.dumps(
        original, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(raw) > MAX_DECODED_TOTAL_BYTES:
        return original
    compressed = zlib.compress(raw, level=1)
    with current.lock:
        if (
            identity not in current.decoded
            and len(current.decoded) < MAX_DECODED_ORIGINALS
            and len(compressed) + current.total_bytes <= MAX_CACHED_COMPRESSED_BYTES
        ):
            current.decoded[identity] = (compressed, len(raw))
            current.total_bytes += len(compressed)
    return original
