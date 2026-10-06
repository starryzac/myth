"""TOOL_ONLY current-context admission; no database/browser/acceptance execution."""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.w1_check_browser_outputs import GROUPS
from scripts.w1_current_check_context import bind_context


def fixture(root: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    files = {"scripts/tool_only.py": "b" * 64}
    source = {
        "git_head": "a" * 40,
        "files": files,
        "source_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    context = {
        "protocol": "bounded-funds-final-context-v1",
        "owner_run_id": "TOOL_ONLY-parent",
        "database": "bf_test_" + "c" * 32,
        "deferred_groups": list(GROUPS),
        "source": source,
    }
    path = root / "docs/progress/evidence/W1/TOOL_ONLY-context.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(context), encoding="utf-8")
    return path, context, copy.deepcopy(source)


def test_original_context_bytes_full_source_and_owner_are_predeclared(tmp_path: Path) -> None:
    path, context, actual = fixture(tmp_path)
    binding = bind_context(tmp_path, str(path), actual)
    assert binding == {
        "path": path.relative_to(tmp_path).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "owner_run_id": context["owner_run_id"],
        "source_sha256": actual["source_sha256"],
    }


@pytest.mark.parametrize(
    "fault",
    [
        "formal_database",
        "null_owner",
        "bad_owner",
        "protocol",
        "missing_group",
        "stale_head",
        "stale_file",
        "bad_digest",
        "empty_source",
        "outside",
        "duplicate",
    ],
)
def test_old_partial_stale_or_unowned_context_cannot_promote_new_run(
    tmp_path: Path, fault: str
) -> None:
    path, context, actual = fixture(tmp_path)
    if fault == "formal_database":
        context["database"] = "bounded_funds"
    elif fault == "null_owner":
        context["owner_run_id"] = None
    elif fault == "bad_owner":
        context["owner_run_id"] = "../another-owner"
    elif fault == "protocol":
        context["protocol"] = "development"
    elif fault == "missing_group":
        context["deferred_groups"].pop()
    elif fault == "stale_head":
        actual["git_head"] = "d" * 40
    elif fault == "stale_file":
        actual["files"]["scripts/tool_only.py"] = "d" * 64
    elif fault == "bad_digest":
        context["source"]["source_sha256"] = actual["source_sha256"] = "d" * 64
    elif fault == "empty_source":
        context["source"]["files"] = actual["files"] = {}
    elif fault == "outside":
        path = tmp_path / "outside-context.json"
    raw = json.dumps(context)
    if fault == "duplicate":
        raw = raw[:-1] + ', "owner_run_id": "TOOL_ONLY-other"}'
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        bind_context(tmp_path, str(path), actual)
