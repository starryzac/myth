"""Two complete nonfinancial scope worlds; lossless storage grants no authority."""

import base64
import binascii
import hashlib
import json
from typing import Any
from uuid import UUID

from app.domain.audit_chain import MAX_SUBJECT_BYTES
from app.domain.full_joint_goal_archive import (
    MAX_COMPRESSED_BYTES,
    MAX_DECODED_BYTES,
    MAX_DEPTH,
    MAX_NODES,
    JointArchiveError,
    _constant,
    _gzip,
    _inflate,
    _json_budget,
    _pairs,
)
from app.domain.full_joint_goal_archive_protocol import _canonical
from app.domain.full_joint_scope_questions import ScopeQuestionRevision
from app.domain.policy_configuration import configuration_hash

PROTOCOL = "full-joint-scope-receipt-archive-v1"
CODEC = "canonical-json-gzip-mtime0-base64-v2"
KIND = "NONFINANCIAL_SCOPE_RECEIPT"
# The original EvidenceItem, request and audit-subject envelope also occupy space.
MAX_STORED_BYTES = MAX_SUBJECT_BYTES - 16 * 1024
_FLAGS = {
    "bank_authority": False,
    "grants_authority": False,
    "financial_write": False,
    "financial_validation_performed": False,
}
_BINDING = {"user_id", "epoch_id", "session_id", "command_id", "receipt_hash"}
_FIELDS = {
    "protocol",
    "codec",
    "record_kind",
    "binding",
    "metadata",
    "worlds",
    "decoded_bytes",
    "plaintext_sha256",
    "record_hash",
    *_FLAGS,
}
_PART_FIELDS = {
    "index",
    "decoded_bytes",
    "decoded_nodes",
    "decoded_depth",
    "compressed_bytes",
    "plaintext_sha256",
    "payload_base64",
}


def _digest(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _binding(value: Any) -> dict[str, str]:
    if type(value) is not dict or set(value) != _BINDING:
        raise JointArchiveError("Scope receipt archive binding shape differs")
    for key in _BINDING - {"receipt_hash"}:
        if type(value[key]) is not str:
            raise JointArchiveError("Scope receipt archive identity must be a UUID")
        try:
            if str(UUID(value[key])) != value[key]:
                raise ValueError
        except ValueError as cause:
            raise JointArchiveError("Scope receipt archive identity must be canonical") from cause
    if not _digest(value["receipt_hash"]):
        raise JointArchiveError("Scope receipt archive original hash differs")
    return {key: value[key] for key in _BINDING}


def _full_original(
    metadata: dict[str, Any], worlds: list[dict[str, Any]], sizes: list[int]
) -> tuple[dict[str, Any], bytes]:
    if (
        type(metadata) is not dict
        or type(metadata.get("evaluation")) is not dict
        or "worlds" in metadata["evaluation"]
        or len(worlds) != 2
        or len(sizes) != 2
    ):
        raise JointArchiveError("Scope archive requires exact metadata and two complete worlds")
    # A fixed two-world receipt has a wire budget, not a new whole-receipt 1M-node
    # budget. Each world independently retains the existing generic codec limits.
    skeleton = {**metadata, "evaluation": {**metadata["evaluation"], "worlds": [{}, {}]}}
    skeleton_raw, _, _ = _canonical(skeleton, MAX_DECODED_BYTES, MAX_NODES, MAX_DEPTH)
    expected_size = len(skeleton_raw) + sum(sizes) - 4  # Replace the two exact '{}' values.
    if expected_size > MAX_DECODED_BYTES:
        raise JointArchiveError("Scope complete receipt exceeds unchanged decoded byte budget")
    value = {**metadata, "evaluation": {**metadata["evaluation"], "worlds": worlds}}
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf8")
    if len(raw) != expected_size:
        raise JointArchiveError("Scope receipt complete canonical size differs")
    return value, raw


def encode_scope_receipt(receipt: ScopeQuestionRevision) -> dict[str, Any]:
    value = receipt.model_dump(mode="json")
    worlds = value["evaluation"]["worlds"]
    if type(worlds) is not list or len(worlds) != 2:
        raise JointArchiveError("Scope archive requires exactly two complete native worlds")
    metadata = {
        **value,
        "evaluation": {k: v for k, v in value["evaluation"].items() if k != "worlds"},
    }
    parts = []
    sizes = []
    for index, world in enumerate(worlds):
        raw, nodes, depth = _canonical(world, MAX_DECODED_BYTES, MAX_NODES, MAX_DEPTH)
        compressed = _gzip(raw)
        if len(compressed) > MAX_COMPRESSED_BYTES:
            raise JointArchiveError("Scope archive compressed part exceeds codec budget")
        sizes.append(len(raw))
        parts.append(
            {
                "index": index,
                "decoded_bytes": len(raw),
                "decoded_nodes": nodes,
                "decoded_depth": depth,
                "compressed_bytes": len(compressed),
                "plaintext_sha256": hashlib.sha256(raw).hexdigest(),
                "payload_base64": base64.b64encode(compressed).decode("ascii"),
            }
        )
    _, original = _full_original(metadata, worlds, sizes)
    record = {
        "protocol": PROTOCOL,
        "codec": CODEC,
        "record_kind": KIND,
        "binding": _binding({key: value[key] for key in _BINDING}),
        "metadata": metadata,
        "worlds": parts,
        "decoded_bytes": len(original),
        "plaintext_sha256": hashlib.sha256(original).hexdigest(),
        **_FLAGS,
    }
    record["record_hash"] = configuration_hash(record)
    _canonical(record, MAX_STORED_BYTES, MAX_NODES, MAX_DEPTH)
    return record


def decode_scope_receipt(
    record: dict[str, Any], user: UUID, epoch: UUID, command: UUID
) -> ScopeQuestionRevision:
    if (
        type(record) is not dict
        or set(record) != _FIELDS
        or record["protocol"] != PROTOCOL
        or record["codec"] != CODEC
        or record["record_kind"] != KIND
        or any(record[key] is not False for key in _FLAGS)
    ):
        raise JointArchiveError("Scope receipt archive closed protocol or flags differ")
    _canonical(record, MAX_STORED_BYTES, MAX_NODES, MAX_DEPTH)
    bound = _binding(record["binding"])
    if (bound["user_id"], bound["epoch_id"], bound["command_id"]) != (
        str(user),
        str(epoch),
        str(command),
    ):
        raise JointArchiveError("Scope receipt archive owner, epoch or command differs")
    if (
        type(record["decoded_bytes"]) is not int
        or not 1 <= record["decoded_bytes"] <= MAX_DECODED_BYTES
        or not _digest(record["plaintext_sha256"])
        or not _digest(record["record_hash"])
        or configuration_hash({k: v for k, v in record.items() if k != "record_hash"})
        != record["record_hash"]
        or type(record["worlds"]) is not list
        or len(record["worlds"]) != 2
    ):
        raise JointArchiveError("Scope receipt archive counts or original hash differ")
    worlds: list[dict[str, Any]] = []
    sizes: list[int] = []
    for index, part in enumerate(record["worlds"]):
        if (
            type(part) is not dict
            or set(part) != _PART_FIELDS
            or type(part["index"]) is not int
            or part["index"] != index
            or not _digest(part["plaintext_sha256"])
        ):
            raise JointArchiveError("Scope archive fixed ordered world part differs")
        for key, low, high in (
            ("decoded_bytes", 1, MAX_DECODED_BYTES),
            ("decoded_nodes", 1, MAX_NODES),
            ("decoded_depth", 0, MAX_DEPTH),
            ("compressed_bytes", 1, MAX_COMPRESSED_BYTES),
        ):
            if type(part[key]) is not int or not low <= part[key] <= high:
                raise JointArchiveError("Scope archive declared part capacity differs")
        text = part["payload_base64"]
        if (
            type(text) is not str
            or not text.isascii()
            or len(text) != 4 * ((part["compressed_bytes"] + 2) // 3)
        ):
            raise JointArchiveError("Scope archive base64 size differs")
        if sum(sizes) + part["decoded_bytes"] > record["decoded_bytes"]:
            raise JointArchiveError("Scope archive parts exceed complete decoded byte budget")
        try:
            compressed = base64.b64decode(text, validate=True)
        except (ValueError, binascii.Error) as cause:
            raise JointArchiveError("Scope archive base64 differs") from cause
        if (
            len(compressed) != part["compressed_bytes"]
            or base64.b64encode(compressed).decode("ascii") != text
        ):
            raise JointArchiveError("Scope archive canonical base64 differs")
        raw = _inflate(compressed, part["decoded_bytes"])
        if (
            len(raw) != part["decoded_bytes"]
            or hashlib.sha256(raw).hexdigest() != part["plaintext_sha256"]
            or _gzip(raw) != compressed
        ):
            raise JointArchiveError("Scope archive canonical gzip or original bytes differ")
        _json_budget(raw, MAX_NODES, MAX_DEPTH)
        try:
            world = json.loads(
                raw.decode("utf8"), object_pairs_hook=_pairs, parse_constant=_constant
            )
        except (UnicodeError, ValueError, RecursionError) as cause:
            raise JointArchiveError("Scope archive world is not strict original JSON") from cause
        canonical, nodes, depth = _canonical(world, MAX_DECODED_BYTES, MAX_NODES, MAX_DEPTH)
        if canonical != raw or (nodes, depth) != (part["decoded_nodes"], part["decoded_depth"]):
            raise JointArchiveError("Scope archive world normalization or counts differ")
        worlds.append(world)
        sizes.append(len(raw))
    value, raw = _full_original(record["metadata"], worlds, sizes)
    if (
        len(raw) != record["decoded_bytes"]
        or hashlib.sha256(raw).hexdigest() != record["plaintext_sha256"]
        or {key: value.get(key) for key in _BINDING} != bound
    ):
        raise JointArchiveError("Scope archive full original or binding differs")
    # A decoded storage record is never trusted in place of the complete original
    # receipt, each native world/model, authenticated actor and original hashes.
    return ScopeQuestionRevision.model_validate_json(raw)
