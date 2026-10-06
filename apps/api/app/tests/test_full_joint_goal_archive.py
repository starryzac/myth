"""Lossless transport risks only; synthetic inputs are not joint financial acceptance."""

import base64
import hashlib
import json
import math
from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from app.domain.full_joint_goal_archive import (
    CODEC,
    MAX_DECODED_BYTES,
    JointArchiveError,
    _gzip,
    decode_joint_archive,
    encode_joint_archive,
)


def fixture() -> dict[str, Any]:
    return {
        "original_plan_hash": "a" * 64,
        "source_hash": "b" * 64,
        "negative_cents": -10,
        "missing_cents": None,
        "count": 100,
        "sample_ratio": 0.125,
        "float_integral": 1.0,
        "zero_float": -0.0,
        "confirmed": False,
        "unicode": "原件\n😀/完整",
        "nested": [{"integer": 1, "boolean": True, "null": None}],
    }


def encoded_plaintext(raw: bytes) -> dict[str, Any]:
    """Construct deliberately bad wire input, never a successful financial fixture."""
    result = encode_joint_archive({})
    compressed = _gzip(raw)
    result.update(
        plaintext_sha256=hashlib.sha256(raw).hexdigest(),
        decoded_bytes=len(raw),
        compressed_bytes=len(compressed),
        payload_base64=base64.b64encode(compressed).decode("ascii"),
    )
    return result


def test_deterministic_complete_roundtrip_keeps_float_int_bool_and_original_hashes() -> None:
    original = fixture()
    before = deepcopy(original)
    first = encode_joint_archive(original)
    second = encode_joint_archive(dict(reversed(list(original.items()))))
    assert first == second and first["codec"] == CODEC
    decoded = decode_joint_archive(first)
    assert decoded == original == before and decoded is not original
    assert type(decoded["count"]) is int
    assert type(decoded["float_integral"]) is float
    assert type(decoded["confirmed"]) is bool
    assert math.copysign(1, decoded["zero_float"]) == -1
    assert decoded["original_plan_hash"] == "a" * 64
    assert first["plaintext_sha256"] not in {"a" * 64, "b" * 64}
    assert all(
        first[key] is False
        for key in (
            "bank_authority",
            "grants_authority",
            "financial_write",
            "financial_validation_performed",
        )
    )
    raw = json.dumps(
        original, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    assert first["decoded_bytes"] == len(raw)
    assert first["plaintext_sha256"] == hashlib.sha256(raw).hexdigest()
    header = base64.b64decode(first["payload_base64"])[:10]
    assert header == b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"


def test_complete_1098_points_over_old_ten_mib_are_not_deduplicated_or_truncated() -> None:
    points = [
        {
            "point": index,
            "phase": ("BEFORE_PAYMENT", "AFTER_PAYMENT", "AFTER_PRINCIPAL")[index % 3],
            "source_original": "explicit-synthetic-capacity-original:" + "a" * 10000,
            "source_hash": hashlib.sha256(str(index).encode()).hexdigest(),
            "minimum_cents": 20,
        }
        for index in range(1098)
    ]
    original = {"fixture_kind": "TOOL_ONLY_SYNTHETIC_CAPACITY", "all_1098_points": points}
    envelope = encode_joint_archive(original)
    assert 10 * 1024 * 1024 < envelope["decoded_bytes"] < MAX_DECODED_BYTES
    assert envelope["compressed_bytes"] < envelope["decoded_bytes"]
    actual = decode_joint_archive(envelope)
    assert actual == original and len(actual["all_1098_points"]) == 1098
    assert actual["all_1098_points"][-1]["point"] == 1097
    with pytest.raises(JointArchiveError, match="envelope size"):
        decode_joint_archive(envelope, max_decoded_bytes=10 * 1024 * 1024)


@pytest.mark.parametrize(
    "change",
    [
        "extra",
        "codec",
        "flag",
        "bool-size",
        "size",
        "over-64m",
        "hash",
        "base64",
        "whitespace",
        "padding",
    ],
)
def test_envelope_identity_noauthority_hash_and_encoding_cannot_be_replaced(change: str) -> None:
    wire = encode_joint_archive(fixture())
    if change == "extra":
        wire["success"] = True
    elif change == "codec":
        wire["codec"] = "unknown"
    elif change == "flag":
        wire["bank_authority"] = 0
    elif change == "bool-size":
        wire["decoded_bytes"] = True
    elif change == "size":
        wire["decoded_bytes"] += 1
    elif change == "over-64m":
        wire["decoded_bytes"] = MAX_DECODED_BYTES + 1
    elif change == "hash":
        wire["plaintext_sha256"] = "c" * 64
    elif change == "base64":
        wire["payload_base64"] = "$invalid$"
    elif change == "whitespace":
        wire["payload_base64"] += "\n"
    else:
        wire["payload_base64"] += "===="
    with pytest.raises(JointArchiveError):
        decode_joint_archive(wire)


@pytest.mark.parametrize("change", ["crc", "truncated", "tail", "member", "mtime"])
def test_single_complete_gzip_member_is_required(change: str) -> None:
    wire = encode_joint_archive(fixture())
    compressed = bytearray(base64.b64decode(wire["payload_base64"]))
    if change == "crc":
        compressed[-8] ^= 1
    elif change == "truncated":
        del compressed[-2:]
    elif change == "tail":
        compressed.extend(b"not-an-original")
    elif change == "member":
        compressed.extend(_gzip(b"{}"))
    else:
        compressed[4] = 1
    wire["compressed_bytes"] = len(compressed)
    wire["payload_base64"] = base64.b64encode(compressed).decode("ascii")
    with pytest.raises(JointArchiveError):
        decode_joint_archive(wire)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"z":1,"a":2}',
        b'{ "a":2}',
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":1e999}',
        b'{"amount_cents":true}',
        b'{"amount_cents":1.0}',
        b'{"a":9223372036854775808}',
        b'{"a":"\\ud800"}',
        b'{"a":"\\u0061"}',
        b'{"a":"unterminated}',
        b'{"a":[]]c}',
        b'{"a":[1,2}',
        b"[]",
    ],
)
def test_rehashed_plaintext_still_requires_strict_complete_canonical_json(raw: bytes) -> None:
    with pytest.raises(JointArchiveError):
        decode_joint_archive(encoded_plaintext(raw))


@pytest.mark.parametrize(
    "value",
    [
        {"amount_cents": True},
        {"amount_cents": 1.5},
        {"a": float("inf")},
        {"a": 2**63},
        {"a": UUID(int=1)},
        {1: "wrong key"},
        {"a": (1, 2)},
    ],
)
def test_encode_never_normalizes_typed_values_money_or_invalid_numbers(value: Any) -> None:
    with pytest.raises(JointArchiveError):
        encode_joint_archive(value)


def test_claimed_small_size_stops_a_compression_bomb_before_accepting_more_plaintext() -> None:
    original = {"synthetic_bomb": "x" * 3_000_000}
    wire = encode_joint_archive(original)
    wire["decoded_bytes"] = 32
    with pytest.raises(JointArchiveError, match="decompression exceeds byte budget"):
        decode_joint_archive(wire, max_decoded_bytes=100)


def test_decoded_budget_node_depth_and_caller_limits_fail_closed() -> None:
    original = {"array": [False, None, 1, 2.0, {"x": '\\quoted"'}]}
    wire = encode_joint_archive(original)
    assert decode_joint_archive(wire, max_nodes=8, max_depth=3) == original
    for limits in ({"max_nodes": 7}, {"max_depth": 2}, {"max_decoded_bytes": 3}):
        with pytest.raises(JointArchiveError):
            decode_joint_archive(wire, **limits)
        with pytest.raises(JointArchiveError):
            encode_joint_archive(original, **limits)
    for invalid_limits in (
        {"max_nodes": True},
        {"max_depth": 65},
        {"max_decoded_bytes": MAX_DECODED_BYTES + 1},
        {"max_decoded_bytes": 0},
    ):
        with pytest.raises(JointArchiveError, match="caller limit"):
            decode_joint_archive(wire, **invalid_limits)


def test_node_budget_is_checked_before_full_json_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = encode_joint_archive({"values": list(range(100))})
    monkeypatch.setattr(
        "app.domain.full_joint_goal_archive.json.loads",
        lambda *_args, **_kwargs: pytest.fail(
            "JSON allocation reached after token budget exceeded"
        ),
    )
    with pytest.raises(JointArchiveError, match="decoded JSON exceeds node"):
        decode_joint_archive(wire, max_nodes=10)


def test_encode_rejects_escaped_string_size_before_full_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_dumps = json.dumps
    calls: list[type[Any]] = []

    def dumps(value: Any, **kwargs: Any) -> str:
        calls.append(type(value))
        assert type(value) is not dict, (
            "whole object serialization must not precede budget rejection"
        )
        return original_dumps(value, **kwargs)

    monkeypatch.setattr("app.domain.full_joint_goal_archive.json.dumps", dumps)
    with pytest.raises(JointArchiveError, match="decoded byte budget"):
        encode_joint_archive({"escaped": "\n" * 10000}, max_decoded_bytes=100)
    assert calls
