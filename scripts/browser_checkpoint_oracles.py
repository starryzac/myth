"""Adapt JSON snapshot column types while retaining the frozen original reset oracle.

This is a pure evidence checker. It never connects to a database or runs a browser,
and changes only a newly loaded oracle's normalization function, not its source.
"""

from __future__ import annotations

import hashlib
import json
import runpy
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from app.domain.audit_chain import canonical_text
from app.domain.bank_posting_codec import bank_posting_data
from sqlalchemy import Table

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_ORACLE_PATH = ROOT / ".runtime/drive_mvp404_browser.py"
ORIGINAL_ORACLE_SHA256 = "5f45cf3879c8fc74354bc6129e67f72788aa9ed0d1d6c2dbaf9f62531727ee35"
Snapshot = dict[str, list[dict[str, Any]]]


def _mapped_python_type(column_type: Any) -> type[Any]:
    try:
        kind: type[Any] = column_type.python_type
    except NotImplementedError:
        kind = object
    # UTCDateTime's generic TypeDecorator reports object without raising. Inspect
    # its explicit implementation, never guess from a value's string contents.
    if kind is object:
        implementation = getattr(column_type, "impl", None)
        if implementation is not None:
            try:
                kind = implementation.python_type
            except NotImplementedError:
                pass
    return kind


def normalized_original(table: Table, row: Mapping[str, Any]) -> dict[str, Any]:
    """Restore only mapped temporal/UUID types; preserve cents and JSONB verbatim."""
    data = dict(row)
    for column in table.columns:
        value = data[column.name]
        if value is None:
            continue
        kind = _mapped_python_type(column.type)
        if kind is datetime:
            data[column.name] = (
                value if isinstance(value, datetime) else datetime.fromisoformat(value)
            )
        elif kind is date:
            data[column.name] = value if type(value) is date else date.fromisoformat(value)
        elif kind is UUID:
            data[column.name] = value if isinstance(value, UUID) else UUID(value)
        elif kind is int and type(value) is not int:
            raise ValueError(
                f"Mapped integer {table.name}.{column.name} must remain a strict integer"
            )
    original = cast(dict[str, Any], json.loads(canonical_text(data, raw=True)))
    return bank_posting_data(original) if table.name == "simulated_bank_postings" else original


def reset_oracle(before: Snapshot, after: Snapshot, baseline: Snapshot) -> dict[str, Any]:
    """Call every original reset invariant with only the column decoder repaired."""
    digest = hashlib.sha256(ORIGINAL_ORACLE_PATH.read_bytes()).hexdigest()
    if digest != ORIGINAL_ORACLE_SHA256:
        raise ValueError("Original reset oracle source changed; retain the frozen evidence checker")
    loaded = runpy.run_path(
        str(ORIGINAL_ORACLE_PATH), run_name="bounded_funds_reset_oracle_readonly"
    )
    original = cast(
        Callable[[Snapshot, Snapshot, Snapshot], dict[str, Any]], loaded["reset_oracle"]
    )
    # runpy returns a namespace copy; use the function's actual, isolated globals.
    original.__globals__["normalized_original"] = normalized_original
    return original(before, after, baseline)
