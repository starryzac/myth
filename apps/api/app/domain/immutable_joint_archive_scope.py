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

from app.domain.immutable_joint_validation_scope import (
    _historical_key,
    _historical_pure_proofs,
    _reuse_historical_proof,
)
from app.domain.policy_configuration import configuration_hash

V3 = "registered-joint-goal-execution-archive-v3"
V4 = "registered-joint-goal-execution-source-dag-v4"
MAX_PROOFS = 16
MAX_DECODED_ORIGINALS = 16
MAX_DECODED_TOTAL_BYTES = 64 * 1024 * 1024
MAX_CACHED_COMPRESSED_BYTES = 8 * 1024 * 1024
CompleteArguments = Mapping[str, Any] | Callable[[], Mapping[str, Any]]


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
    complete_wire: CompleteArguments,
    uncached_pure_verifier: Callable[[], None],
    *,
    fresh_original: Callable[[], dict[str, Any]] | None = None,
    verifier_implementation: Callable[..., Any] | None = None,
) -> None:
    """Positive pure trace proof only, keyed by every byte-relevant wire field."""
    def complete() -> dict[str, Any]:
        assert fresh_original is not None
        original = fresh_original()
        wire = complete_wire if isinstance(complete_wire, Mapping) else complete_wire()
        if (
            original["wire"]["algorithm_versions"].get("full_joint_goal_execution") != protocol
            or configuration_hash(original["wire"]) != configuration_hash(dict(wire))
        ):
            raise ValueError("Fresh complete native trace differs from passed wire")
        return original

    getter = complete if fresh_original is not None else None
    proof_key = _historical_key("wire", protocol, verifier_implementation, getter)

    def verify() -> None:
        _reuse_historical_proof(
            proof_key,
            lambda: _historical_key("wire", protocol, verifier_implementation, getter),
            uncached_pure_verifier,
        )

    current = _request.get()
    if (
        protocol not in {V3, V4}
        or current is None
        or not isinstance(complete_wire, Mapping) and protocol != V4
    ):
        verify()
        return
    def key() -> str:
        wire = complete_wire if isinstance(complete_wire, Mapping) else complete_wire()
        identity = configuration_hash({"kind": "frozen-trace", "wire": dict(wire)})
        if proof_key is not None:
            identity = configuration_hash(
                {"request_identity": identity, "native_proof_key": proof_key}
            )
        return identity

    identity = key()
    if (
        fresh_original is not None
        and _historical_pure_proofs.get() is not None
        and proof_key is None
    ):
        uncached_pure_verifier()
        return
    with current.lock:
        if identity in current.proved:
            return
    verify()
    if identity != key():
        raise ValueError("Pure verifier changed the complete original wire")
    with current.lock:
        if len(current.proved) < MAX_PROOFS:
            current.proved.add(identity)


def decode_complete_original(
    protocol: str,
    complete_arguments: CompleteArguments,
    decoded_bytes: int,
    uncached_complete_decoder: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Return an isolated original, never a cached caller-mutable dictionary."""
    current = _request.get()
    if (
        protocol not in {V3, V4}
        or current is None
        or not isinstance(complete_arguments, Mapping) and protocol != V4
    ):
        return uncached_complete_decoder()
    def key() -> str:
        arguments = (
            complete_arguments if isinstance(complete_arguments, Mapping) else complete_arguments()
        )
        return configuration_hash({"kind": "record-decode", "args": dict(arguments)})

    identity = key()
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
    if identity != key():
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
