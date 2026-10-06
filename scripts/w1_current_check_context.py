"""Predeclare new native browser/audit work against one actual final-check context."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def bind_context(root: Path, context_name: str, actual_source: dict[str, Any]) -> dict[str, str]:
    root = root.resolve()
    path = Path(context_name).resolve()
    if not (
        path.is_relative_to(root / "docs/progress/evidence/W1")
        and path.is_file()
        and not any(part.startswith(".env") for part in path.parts)
    ):
        raise ValueError("An explicit owned W1 final-check context original is required")
    raw = path.read_bytes()

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate final-check context key")
            value[key] = item
        return value

    def constant(value: str) -> Any:
        raise ValueError("Nonfinite final-check context: " + value)

    context = json.loads(raw, object_pairs_hook=unique, parse_constant=constant)
    source = context.get("source", {})
    if not (
        context.get("protocol") == "bounded-funds-final-context-v1"
        and isinstance(context.get("owner_run_id"), str)
        and re.fullmatch(r"[a-zA-Z0-9_-]+", context["owner_run_id"])
        and re.fullmatch(r"bf_test_[0-9a-f]{32}", str(context.get("database")))
        and isinstance(context.get("deferred_groups"), list)
        and {"six_business_e2e", "three_demo_rounds", "original_financial_chain", "audit_chain"}
        <= set(context["deferred_groups"])
        and source == {key: actual_source[key] for key in ("git_head", "files", "source_sha256")}
        and bool(source["files"])
        and source["source_sha256"]
        == hashlib.sha256(
            json.dumps(source["files"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    ):
        raise ValueError("Current-check owner/database/groups/full current source differs")
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "owner_run_id": context["owner_run_id"],
        "source_sha256": source["source_sha256"],
    }
