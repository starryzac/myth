"""Finite same-package originals for reviewed-change v2, without authority."""

import copy
import json
import math
import re
from typing import Any, cast
from uuid import UUID

from app.domain.full_joint_goal_archive import MAX_DECODED_BYTES, MAX_DEPTH, MAX_NODES
from app.domain.full_joint_goal_planning import FullJointBinding
from app.domain.policy_configuration import configuration_hash

PROTOCOL = "reviewed-policy-shared-source-originals-v1"
MODE = "ALL_1098_HARD_POINTS_SHARE_VERIFIED_SOURCE_REFS"
POINTS = 1098
FIELDS = {
    "protocol",
    "source_basis_hash",
    "evidence",
    "joint_source_refs",
    "originals",
    "original_preview_hash",
    "original_preview_bytes",
}


class SharedReviewSourceError(ValueError):
    """The exact finite shared representation cannot be safely resolved."""


def require(condition: bool) -> None:
    if not condition:
        raise SharedReviewSourceError("COMPLETE_FINITE_REVIEW_SHARED_ORIGINALS_REQUIRED")


def encoded(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def metrics(value: Any) -> tuple[int, int, int]:
    """Exact compact bytes, value nodes and root-zero depth before expansion."""
    nodes = 0
    deepest = 0

    def visit(item: Any, depth: int) -> int:
        nonlocal nodes, deepest
        nodes += 1
        deepest = max(deepest, depth)
        require(nodes <= MAX_NODES and depth <= MAX_DEPTH)
        if item is None or type(item) in {str, bool, int, float}:
            if type(item) is float:
                require(math.isfinite(item))
            try:
                size = len(encoded(item))
            except (ValueError, UnicodeError) as cause:
                raise SharedReviewSourceError("FINITE_UTF8_REVIEW_JSON_REQUIRED") from cause
        elif type(item) is list:
            size = 2 + max(0, len(item) - 1) + sum(visit(child, depth + 1) for child in item)
        elif type(item) is dict:
            size = 2 + max(0, len(item) - 1)
            for key, child in item.items():
                require(type(key) is str)
                try:
                    size += len(encoded(key)) + 1 + visit(child, depth + 1)
                except UnicodeError as cause:
                    raise SharedReviewSourceError("FINITE_UTF8_REVIEW_JSON_REQUIRED") from cause
        else:
            raise SharedReviewSourceError("STRICT_JSON_REVIEW_VALUE_REQUIRED")
        require(size <= MAX_DECODED_BYTES)
        return size

    return visit(value, 0), nodes, deepest


def _uuid(value: Any) -> bool:
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _digest(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _table(basis: dict[str, Any], basis_hash: str) -> dict[str, dict[str, Any]]:
    metrics(basis)
    require(_digest(basis_hash) and configuration_hash(basis) == basis_hash)
    require(_uuid(basis.get("user_id")))
    rows = basis.get("tables", {}).get("evidence_items")
    require(type(rows) is list and len(rows) <= 10000)
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        require(type(row) is dict and _uuid(row.get("id")))
        require(row["id"] not in by_id and row.get("user_id") == basis["user_id"])
        by_id[row["id"]] = row
    require(basis.get("row_counts", {}).get("evidence_items") == len(rows))
    return by_id


def _joint(originals: dict[str, Any], user: str) -> tuple[dict[str, Any], list[Any], list[Any]]:
    joint = originals.get("joint_binding")
    require(type(joint) is dict and joint.get("status") == "VERIFIED")
    joint = cast(dict[str, Any], joint)
    require(joint.get("reasons") == [])
    for key in ("original_point_count", "full_point_count", "bound_point_count"):
        require(type(joint.get(key)) is int and joint[key] == POINTS)
    candidate = joint.get("candidate")
    require(type(candidate) is dict and candidate.get("user_id") == user)
    candidate = cast(dict[str, Any], candidate)
    points, refs = candidate.get("hard_protection_points"), joint.get("verified_source_refs")
    require(type(points) is list and len(points) == POINTS)
    require(type(refs) is list and 0 < len(refs) <= 1000)
    return joint, cast(list[Any], points), cast(list[Any], refs)


def _references(refs: list[Any], by_id: dict[str, dict[str, Any]], user: str) -> None:
    seen = set()
    for ref in refs:
        require(type(ref) is dict and set(ref) == {"user_id", "evidence_id", "content_hash"})
        key = ref.get("evidence_id")
        require(_uuid(key) and key not in seen and ref.get("user_id") == user)
        require(_digest(ref.get("content_hash")))
        require(key in by_id and by_id[key].get("content_hash") == ref["content_hash"])
        seen.add(key)


def pack_shared_originals(
    preview: dict[str, Any], basis: dict[str, Any], basis_hash: str
) -> dict[str, Any]:
    """Only remove exact repetitions at the two closed locations."""
    original_size, _, _ = metrics(preview)
    require(preview.get("template_name") == "GoalAllocationPolicy")
    require(preview.get("user_id") == basis.get("user_id"))
    by_id = _table(basis, basis_hash)
    originals = preview.get("source_originals")
    require(type(originals) is dict and originals.get("protocol") != PROTOCOL)
    originals = cast(dict[str, Any], originals)
    evidence = originals.get("evidence")
    require(type(evidence) is list and len(evidence) == len(by_id))
    evidence = cast(list[Any], evidence)
    row_ids = []
    for row in evidence:
        require(type(row) is dict and _uuid(row.get("id")))
        require(row["id"] in by_id and row == by_id[row["id"]])
        row_ids.append(row["id"])
    require(len(set(row_ids)) == len(by_id))
    _, points, refs = _joint(originals, basis["user_id"])
    _references(refs, by_id, basis["user_id"])
    require(all(type(point) is dict and point.get("source_refs") == refs for point in points))
    bare = copy.deepcopy(originals)
    del bare["evidence"]
    for point in bare["joint_binding"]["candidate"]["hard_protection_points"]:
        del point["source_refs"]
    capsule = {
        "protocol": PROTOCOL,
        "source_basis_hash": basis_hash,
        "evidence": {"row_ids": row_ids, "rows_hash": configuration_hash({"rows": evidence})},
        "joint_source_refs": {
            "mode": MODE,
            "point_count": POINTS,
            "source_refs_hash": configuration_hash({"source_refs": refs}),
        },
        "originals": bare,
        "original_preview_hash": configuration_hash(preview),
        "original_preview_bytes": original_size,
    }
    packed = {**preview, "source_originals": capsule}
    require(resolve_shared_preview(packed, basis, basis_hash) == preview)
    return capsule


def resolve_shared_preview(
    preview: dict[str, Any], basis: dict[str, Any], basis_hash: str
) -> dict[str, Any]:
    """Preflight bytes/nodes/depth before allocating the exact complete preview."""
    metrics(preview)
    require(preview.get("template_name") == "GoalAllocationPolicy")
    capsule = preview.get("source_originals")
    require(type(capsule) is dict and set(capsule) == FIELDS)
    capsule = cast(dict[str, Any], capsule)
    require(capsule["protocol"] == PROTOCOL and capsule["source_basis_hash"] == basis_hash)
    require(_digest(capsule["original_preview_hash"]))
    declared = capsule["original_preview_bytes"]
    require(type(declared) is int and 0 < declared <= MAX_DECODED_BYTES)
    by_id = _table(basis, basis_hash)
    require(preview.get("user_id") == basis["user_id"])
    evidence = capsule["evidence"]
    require(type(evidence) is dict and set(evidence) == {"row_ids", "rows_hash"})
    ids = evidence["row_ids"]
    require(type(ids) is list and len(ids) == len(by_id) and all(_uuid(key) for key in ids))
    require(len(set(ids)) == len(ids) and set(ids) == set(by_id))
    count = preview.get("source_counts", {}).get("evidence")
    require(type(count) is int and count == len(ids))
    rows = [by_id[key] for key in ids]  # bounded original rows, no repeated expansion
    require(
        _digest(evidence["rows_hash"])
        and evidence["rows_hash"] == configuration_hash({"rows": rows})
    )
    originals = capsule["originals"]
    require(type(originals) is dict and "evidence" not in originals)
    joint, points, refs = _joint(originals, basis["user_id"])
    require(all(type(point) is dict and point and "source_refs" not in point for point in points))
    _references(refs, by_id, basis["user_id"])
    shared = capsule["joint_source_refs"]
    require(type(shared) is dict and set(shared) == {"mode", "point_count", "source_refs_hash"})
    require(
        shared["mode"] == MODE
        and type(shared["point_count"]) is int
        and shared["point_count"] == POINTS
    )
    require(
        _digest(shared["source_refs_hash"])
        and shared["source_refs_hash"] == configuration_hash({"source_refs": refs})
    )
    bare = {**preview, "source_originals": originals}
    bare_bytes, bare_nodes, bare_depth = metrics(bare)
    row_bytes, row_nodes, row_depth = metrics(rows)
    ref_bytes, ref_nodes, ref_depth = metrics(refs)
    expected_bytes = bare_bytes + len(encoded("evidence")) + 2 + row_bytes
    expected_bytes += POINTS * (len(encoded("source_refs")) + 2 + ref_bytes)
    require(expected_bytes == declared and expected_bytes <= MAX_DECODED_BYTES)
    require(bare_nodes + row_nodes + POINTS * ref_nodes <= MAX_NODES)
    require(max(bare_depth, 2 + row_depth, 6 + ref_depth) <= MAX_DEPTH)
    resolved = copy.deepcopy(bare)
    resolved["source_originals"]["evidence"] = copy.deepcopy(rows)
    for point in resolved["source_originals"]["joint_binding"]["candidate"][
        "hard_protection_points"
    ]:
        point["source_refs"] = copy.deepcopy(refs)
    raw = encoded(resolved)
    require(
        len(raw) == declared and configuration_hash(resolved) == capsule["original_preview_hash"]
    )
    FullJointBinding.model_validate_json(encoded(resolved["source_originals"]["joint_binding"]))
    return resolved
