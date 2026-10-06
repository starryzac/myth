"""Bounded backward JSON pointers to actual earlier results in one invocation."""

import hashlib
import json
import re
from typing import Any

METHOD = "SCENARIO_ORIGINAL_RESULT_POINTER_V1"
MAX_DEPTH = 24
MAX_NODES = 10_000
MAX_BYTES = 1024 * 1024


def canonical(value: Any) -> bytes:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("Scenario input/reference value exceeds its original byte budget")
    return raw


def _reference(value: dict[str, Any], previous: set[str]) -> tuple[str, str]:
    ref = value.get("$ref")
    if set(value) != {"$ref"} or not isinstance(ref, dict) or set(ref) != {"step_id", "pointer"}:
        raise ValueError("An original result reference has exactly step_id and pointer")
    identity, pointer = ref["step_id"], ref["pointer"]
    if not (
        isinstance(identity, str)
        and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity)
        and identity in previous
        and isinstance(pointer, str)
        and pointer.startswith("/result/")
        and len(pointer) <= 512
        and re.search(r"~(?![01])", pointer) is None
    ):
        raise ValueError("Only a strict backward pointer to an actual original result is admitted")
    return identity, pointer


def resolve_inputs(
    inputs: dict[str, Any], previous: dict[str, dict[str, Any]], *, validate_only: bool = False
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """No DB/source/authorization is cached; each result is local to this scenario call."""
    canonical(inputs)
    count = 0
    expanded_bytes = 0
    observations: list[dict[str, str]] = []

    def visit(depth: int) -> None:
        nonlocal count
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            raise ValueError("Scenario result reference traversal budget exceeded")

    def copy_original(value: Any, depth: int) -> Any:
        # A literal '$ref' returned by a service is data, never another instruction.
        # Count the expanded subtree before it can bypass the input traversal cap.
        visit(depth)
        if isinstance(value, dict):
            return {key: copy_original(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [copy_original(item, depth + 1) for item in value]
        return value

    def walk(value: Any, depth: int) -> Any:
        nonlocal expanded_bytes
        visit(depth)
        if isinstance(value, dict) and "$ref" in value:
            identity, pointer = _reference(value, set(previous))
            if validate_only:
                return value
            original = previous[identity]
            current: Any = original
            for token in pointer[1:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                if isinstance(current, dict) and token in current:
                    current = current[token]
                elif (
                    isinstance(current, list)
                    and re.fullmatch(r"0|[1-9][0-9]*", token)
                    and int(token) < len(current)
                ):
                    current = current[int(token)]
                else:
                    raise ValueError("The actual prior original result has no such pointer")
            encoded = canonical(current)
            expanded_bytes += len(encoded)
            if expanded_bytes > MAX_BYTES:
                raise ValueError("Expanded original references exceed their cumulative byte budget")
            copied = copy_original(current, depth)
            observations.append(
                {
                    "method": METHOD,
                    "step_id": identity,
                    "pointer": pointer,
                    "original_row_sha256": hashlib.sha256(canonical(original)).hexdigest(),
                    "value_sha256": hashlib.sha256(encoded).hexdigest(),
                }
            )
            return copied  # Fresh copy; no alias into earlier immutable outputs.
        if isinstance(value, dict):
            return {key: walk(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item, depth + 1) for item in value]
        return value

    result = walk(inputs, 0)
    if not isinstance(result, dict):
        raise ValueError("Resolved scenario inputs must remain a JSON object")
    canonical(result)
    return result, observations
