"""Exactly three original-mode references; lossless transport is never authority."""

import copy
import hashlib
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from app.domain.full_joint_goal_archive import MAX_DECODED_BYTES, MAX_DEPTH, MAX_NODES
from app.domain.full_joint_goal_archive_protocol import _canonical
from app.domain.policy_configuration import configuration_hash

PROTOCOL = "standing-asset-original-fixed-reference-v1"
MARKER = "$standing_asset_original_v1"
MODES = ("PORTFOLIO", "FIXED_LADDER")
REF_FIELDS = {MARKER, "content_hash"}
BINDING_FIELDS = {"user_id", "epoch_id", "phase", "as_of", "family"}
FLAGS = {"bank_authority", "grants_authority", "financial_write", "financial_validation_performed"}
FIELDS = {
    "protocol",
    "shared_input",
    "binding",
    "expanded_input_hash",
    "expanded_bytes",
    "expanded_nodes",
    "expanded_depth",
    "reference_count",
    *FLAGS,
}
NATIVE_PROTOCOL = "zhiyu-standing-delegated-native-v2"
MODE_PROTOCOL = "zhiyu-full-asset-mode-question-v1"
RESIDUAL_PROTOCOL = "accepted-finite-asset-mode-residual-v1"


def exact_hash(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def original_binding(value: dict[str, Any]) -> dict[str, Any]:
    result = {key: value[key] for key in ("user_id", "epoch_id", "phase", "as_of")}
    result["family"] = value["native"]["kind"]
    if (
        value["protocol"] != NATIVE_PROTOCOL
        or result["phase"] not in {"PREPARE", "RESERVE", "BANK_ACCEPT"}
        or result["family"] not in {"ASSET_PORTFOLIO", "MATURITY_REINVESTMENT"}
        or any(
            type(result[key]) is not str or str(UUID(result[key])) != result[key]
            for key in ("user_id", "epoch_id")
        )
        or type(result["as_of"]) is not str
    ):
        raise ValueError("Exact native owner/epoch/family/phase binding required")
    clock = datetime.fromisoformat(result["as_of"].replace("Z", "+00:00"))
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Native current server clock must be aware")
    return result


def paths() -> tuple[tuple[str | int, ...], ...]:
    return (
        ("native", "residual_mode_evaluation", "accepted_evaluation"),
        *(
            ("native", "residual_mode_evaluation", "worlds", index, "input", "original_world")
            for index in range(2)
        ),
    )


def at(value: Any, path: tuple[str | int, ...]) -> Any:
    for key in path:
        value = value[key]
    return value


def put(value: dict[str, Any], path: tuple[str | int, ...], item: Any) -> None:
    at(value, path[:-1])[path[-1]] = item


def targets(value: dict[str, Any]) -> list[dict[str, Any]]:
    evaluation = value["native"]["current_mode_evaluation"]
    residual = value["native"]["residual_mode_evaluation"]
    if (
        type(evaluation) is not dict
        or evaluation.get("protocol") != MODE_PROTOCOL
        or type(evaluation.get("worlds")) is not list
        or len(evaluation["worlds"]) != 2
        or [world.get("mode") for world in evaluation["worlds"]] != list(MODES)
        or type(residual) is not dict
        or residual.get("protocol") != RESIDUAL_PROTOCOL
        or type(residual.get("worlds")) is not list
        or len(residual["worlds"]) != 2
    ):
        raise ValueError("Both complete original and residual worlds are required")
    return [evaluation, *evaluation["worlds"]]


def scan(value: Any, bound: dict[str, Any], references: bool) -> None:
    allowed = set(paths()) if references else set()
    found: set[tuple[str | int, ...]] = set()
    pending: list[tuple[Any, tuple[str | int, ...]]] = [(value, ())]
    nodes = 0
    while pending:
        child, path = pending.pop()
        nodes += 1
        # The unshared writer has exactly three bounded original copies.
        # The shared reader retains the unchanged generic one-million-node gate.
        if nodes > (MAX_NODES if references else 3 * MAX_NODES) or len(path) > MAX_DEPTH:
            raise ValueError("Fixed-reference original exceeds structural budget")
        if type(child) is dict:
            if MARKER in child:
                if path not in allowed or set(child) != REF_FIELDS or path in found:
                    raise ValueError("Foreign/ambiguous reference marker is forbidden")
                found.add(path)
                continue
            for key in ("user_id", "epoch_id", "expected_epoch_id"):
                expected = bound["user_id" if key == "user_id" else "epoch_id"]
                if key in child and child[key] is not None and child[key] != expected:
                    raise ValueError("Nested original owner or epoch differs")
            pending.extend((item, (*path, key)) for key, item in child.items())
        elif type(child) is list:
            pending.extend((item, (*path, index)) for index, item in enumerate(child))
    if found != allowed:
        raise ValueError("Exactly three fixed-path references are required")


def expansion_budget(shared: dict[str, Any]) -> tuple[int, int, int]:
    """Precompute all logical work from unchanged bounded shared JSON and 3 leaves."""
    shared_bytes, shared_nodes, shared_depth = _canonical(
        shared, MAX_DECODED_BYTES, MAX_NODES, MAX_DEPTH
    )
    original_targets = targets(shared)
    byte_count, node_count, depth = len(shared_bytes), shared_nodes, shared_depth
    for index, (path, original) in enumerate(zip(paths(), original_targets, strict=True)):
        reference = at(shared, path)
        expected = "EVALUATION" if index == 0 else "WORLD_" + MODES[index - 1]
        if (
            type(reference) is not dict
            or set(reference) != REF_FIELDS
            or reference[MARKER] != expected
            or reference["content_hash"] != configuration_hash(original)
        ):
            raise ValueError("Exact original world reference/hash differs")
        old, old_nodes, _ = _canonical(reference, MAX_DECODED_BYTES, MAX_NODES, MAX_DEPTH)
        replacement, replacement_nodes, replacement_depth = _canonical(
            original, MAX_DECODED_BYTES, MAX_NODES, MAX_DEPTH
        )
        byte_count += len(replacement) - len(old)
        node_count += replacement_nodes - old_nodes
        depth = max(depth, len(path) + replacement_depth)
    if (
        not 1 <= byte_count <= MAX_DECODED_BYTES
        or not 1 <= node_count <= 3 * MAX_NODES
        or depth > MAX_DEPTH
        or byte_count > 3 * len(shared_bytes)
        or node_count > 3 * shared_nodes
    ):
        raise ValueError("Exact fixed-reference expanded bytes/nodes/depth/amplification exceeded")
    return byte_count, node_count, depth


def pack_asset_original(value: dict[str, Any]) -> dict[str, Any]:
    """Three predetermined equal-value duplicates only; no opaque financial summaries."""
    try:
        bound = original_binding(value)
        originals = targets(value)
        scan(value, bound, False)
        shared = copy.deepcopy(value)
        for index, (path, original) in enumerate(zip(paths(), originals, strict=True)):
            if at(value, path) != original:
                raise ValueError("Residual cannot replace either original whole world")
            put(
                shared,
                path,
                {
                    MARKER: "EVALUATION" if index == 0 else "WORLD_" + MODES[index - 1],
                    "content_hash": configuration_hash(original),
                },
            )
        scan(shared, bound, True)
        predicted_bytes, predicted_nodes, predicted_depth = expansion_budget(shared)
        expanded, nodes, depth = _canonical(
            value, predicted_bytes, predicted_nodes, predicted_depth
        )
        if len(expanded) != predicted_bytes or nodes != predicted_nodes:
            raise ValueError("Exact logical work calculation differs")
        return {
            "protocol": PROTOCOL,
            "shared_input": shared,
            "binding": bound,
            "expanded_input_hash": hashlib.sha256(expanded).hexdigest(),
            "expanded_bytes": len(expanded),
            "expanded_nodes": nodes,
            "expanded_depth": depth,
            "reference_count": 3,
            **dict.fromkeys(FLAGS, False),
        }
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("Complete fixed-reference input shape required") from error


def unpack_asset_original(record: dict[str, Any], *, expected_expanded_hash: str) -> dict[str, Any]:
    try:
        if (
            type(record) is not dict
            or set(record) != FIELDS
            or record["protocol"] != PROTOCOL
            or any(record[name] is not False for name in FLAGS)
            or not exact_hash(expected_expanded_hash)
            or record["expanded_input_hash"] != expected_expanded_hash
            or type(record["binding"]) is not dict
            or set(record["binding"]) != BINDING_FIELDS
            or type(record["reference_count"]) is not int
            or record["reference_count"] != 3
            or any(
                type(record[name]) is not int
                for name in ("expanded_bytes", "expanded_nodes", "expanded_depth")
            )
        ):
            raise ValueError("Closed independently anchored fixed-reference record required")
        shared = record["shared_input"]
        bound = original_binding(shared)
        if bound != record["binding"]:
            raise ValueError("Original owner/epoch/phase/clock/family binding differs")
        scan(shared, bound, True)
        byte_count, node_count, depth_upper = expansion_budget(shared)
        if (
            record["expanded_bytes"] != byte_count
            or record["expanded_nodes"] != node_count
            or not 0 <= record["expanded_depth"] <= depth_upper
        ):
            raise ValueError("Fixed-reference logical work budget differs")
        # All budgets have been verified before allocating the three full copies.
        restored = copy.deepcopy(shared)
        for path, original in zip(paths(), targets(shared), strict=True):
            put(restored, path, copy.deepcopy(original))
        expanded, nodes, depth = _canonical(restored, byte_count, node_count, depth_upper)
        if (
            len(expanded) != byte_count
            or nodes != node_count
            or depth != record["expanded_depth"]
            or hashlib.sha256(expanded).hexdigest() != expected_expanded_hash
        ):
            raise ValueError("Expanded complete original hash or logical work differs")
        return cast(dict[str, Any], restored)
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("Complete closed fixed-reference record required") from error
