"""TOOL_ONLY binding fixtures: no real browser, audit, bank or financial success proof."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.w1_check_browser_outputs import GROUPS, capture, sha, strict_json


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ToolFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.browser_path = root / "output/playwright/TOOL_ONLY-browser/manifest.json"
        self.observer_path = root / "docs/progress/evidence/W1/TOOL_ONLY-observer/manifest.json"
        self.context_path = root / "docs/progress/evidence/W1/TOOL_ONLY-context.json"
        names = [
            "apps/api/app/tool_only.py",
            "scripts/w1_browser_acceptance.py",
            "scripts/w1_audit_observe.py",
            "scripts/browser_checkpoint_oracles.py",
            ".runtime/drive_mvp404_browser.py",
        ]
        self.files = {}
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# TOOL_ONLY source; not a runnable financial oracle\n")
            self.files[name] = sha(path.read_bytes())
        self.context = {
            "protocol": "bounded-funds-final-context-v1",
            "owner_run_id": "TOOL_ONLY-parent",
            "database": "bf_test_" + "a" * 32,
            "deferred_groups": list(GROUPS),
            "source": {"git_head": "a" * 40, "source_sha256": "b" * 64, "files": self.files},
        }
        dump(self.context_path, self.context)
        binding = {
            "path": self.context_path.relative_to(root).as_posix(),
            "sha256": sha(self.context_path.read_bytes()),
            "owner_run_id": "TOOL_ONLY-parent",
            "source_sha256": "b" * 64,
        }
        self.browser: dict[str, Any] = {
            "purpose": "MVP_ACCEPTANCE",  # TOOL_ONLY fixture, never a product evidence package.
            "acceptance_context": binding,
            "run_id": "TOOL_ONLY-browser",
            "status": "PASSED",
            "mode": "all",
            "commit": "a" * 40,
            "database": "bf_test_" + "c" * 32,
            "generated_database_absent": True,
            "temporary_database_exited": True,
            "cleanup_status": "VERIFIED_ABSENT",
            "source_before": self.files,
            "source_after": self.files,
            "artifact_hashes": {},
            "checkpoints": [],
        }
        baseline = self.checkpoint("baseline", "BEGIN")
        self.browser["baseline"] = baseline
        for number in (1, 2, 3):
            self.checkpoint("rent-real-version-change", "MONEY")
            self.checkpoint(f"round-{number}-reset-before", "BEGIN")
            self.checkpoint(f"round-{number}-reset-after", "RESET")
        result = self.browser_path.parent / "playwright/results.json"
        dump(result, {"classification": "TOOL_ONLY_NOT_ACTUAL_PLAYWRIGHT"})
        self.browser["artifact_hashes"]["playwright/results.json"] = sha(result.read_bytes())
        self.scoped = self.make_scoped(
            "TOOL_ONLY-browser-wrapper", "scripts/w1_browser_acceptance.py"
        )
        observer_scoped = self.make_scoped("TOOL_ONLY-audit-wrapper", "scripts/w1_audit_observe.py")
        self.observer = {
            "purpose": "MVP_ACCEPTANCE",
            "acceptance_context": binding,
            "run_id": "TOOL_ONLY-observer",
            "owner_run_id": "TOOL_ONLY-browser",
            "status": "PASSED",
            "artifact_hashes": {"TOOL_ONLY-original.txt": sha(b"TOOL_ONLY unverified audit\n")},
        }
        self.observer_path.parent.mkdir(parents=True, exist_ok=True)
        (self.observer_path.parent / "TOOL_ONLY-original.txt").write_bytes(
            b"TOOL_ONLY unverified audit\n"
        )
        self.browser["audit_observation"] = {
            "status": "PASSED",
            "exit_code": 0,
            "path": self.observer_path.relative_to(root).as_posix(),
            "scoped_manifest": observer_scoped["paths"]["manifest"].relative_to(root).as_posix(),
        }
        self.refresh()

    def checkpoint(self, label: str, mode: str) -> dict[str, Any]:
        number = len(self.browser["checkpoints"])
        path = self.browser_path.parent / "checkpoints" / f"{number:03d}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Intentionally not a financial snapshot: only byte registration is under test.
        path.write_bytes(f"TOOL_ONLY not gzip {number}".encode())
        metadata = {
            "path": path.relative_to(self.root).as_posix(),
            "sha256": sha(path.read_bytes()),
        }
        self.browser["artifact_hashes"][path.relative_to(self.browser_path.parent).as_posix()] = (
            sha(path.read_bytes())
        )
        self.browser["checkpoints"].append(
            {
                "label": label,
                "mode": mode,
                "scenario_id": "w1-TOOLONLYround",
                "result": {"snapshot": metadata},
            }
        )
        return metadata

    def make_scoped(self, run_id: str, command: str) -> dict[str, Any]:
        directory = self.root / "docs/progress/evidence/W1" / run_id
        paths = {
            "manifest": directory / "manifest.json",
            "source_before": directory / "source.before.json",
            "source_after": directory / "source.after.json",
            "log": directory / "output.log",
        }
        dump(paths["source_before"], self.files)
        dump(paths["source_after"], self.files)
        paths["log"].write_bytes(b"TOOL_ONLY transcript, no process executed\n")
        dump(
            paths["manifest"],
            {
                "run_id": run_id,
                "status": "PASSED",
                "exit_code": 0,
                "all_source_stable": True,
                "git_head": "a" * 40,
                "command": ["TOOL_ONLY-python", command, "--run"],
                "log_sha256": sha(paths["log"].read_bytes()),
            },
        )
        return {"run_id": run_id, "paths": paths}

    def refresh(self) -> None:
        dump(self.observer_path, self.observer)
        self.browser["audit_observation"]["sha256"] = sha(self.observer_path.read_bytes())
        dump(self.browser_path, self.browser)

    def capture(self) -> dict[str, dict[str, Any]]:
        return capture(self.root, self.context_path, self.browser_path, self.scoped)


def test_tool_binding_retains_nested_original_runs_and_all_byte_indices(tmp_path: Path) -> None:
    fixture = ToolFixture(tmp_path)
    result = fixture.capture()
    assert set(result) == set(GROUPS)
    original = result["audit_chain"]
    assert len(original["runs"]) == 4
    assert (
        original["check"]["inputs"]["scoped"]["manifest"]
        != result["six_business_e2e"]["check"]["inputs"]["scoped"]["manifest"]
    )
    rounds = result["three_demo_rounds"]["check"]["inputs"]["round_snapshots"]
    assert rounds == [
        row["result"]["snapshot"]
        for row in fixture.browser["checkpoints"]
        if row["label"].endswith("reset-before")
    ]
    assert any(row["validator"] == "NATIVE_PROOF_ONLY" for row in original["artifacts"])
    assert all(row["validator"] != "MVP_NATIVE_V2" for row in original["artifacts"])
    assert fixture.browser_path.read_bytes() == json.dumps(fixture.browser).encode()


@pytest.mark.parametrize(
    "mutation",
    [
        "development",
        "failed",
        "partial",
        "not_cleaned",
        "wrong_owner",
        "wrong_context",
        "foreign_database",
        "observer_development",
        "observer_owner",
        "observer_failed",
        "original_drift",
        "source_drift",
        "scoped_exit",
        "scoped_source",
        "scoped_argv",
        "missing_round",
        "wrong_last_money",
    ],
)
def test_tool_binding_cannot_relabel_or_complete_missing_originals(
    tmp_path: Path, mutation: str
) -> None:
    fixture = ToolFixture(tmp_path)
    if mutation == "development":
        fixture.browser["purpose"] = "DEVELOPMENT"
    elif mutation == "failed":
        fixture.browser["status"] = "FAILED"
    elif mutation == "partial":
        fixture.browser["status"] = "PARTIAL_SCOPE_PASSED"
    elif mutation == "not_cleaned":
        fixture.browser["generated_database_absent"] = False
    elif mutation == "wrong_owner":
        fixture.browser["run_id"] = "TOOL_ONLY-other"
    elif mutation == "wrong_context":
        fixture.browser["acceptance_context"] = {"sha256": "d" * 64}
    elif mutation == "foreign_database":
        fixture.browser["database"] = "bounded_funds"
    elif mutation == "observer_development":
        fixture.observer["purpose"] = "DEVELOPMENT"
    elif mutation == "observer_owner":
        fixture.observer["owner_run_id"] = "TOOL_ONLY-other"
    elif mutation == "observer_failed":
        fixture.observer["status"] = "FAILED"
    elif mutation == "original_drift":
        (fixture.browser_path.parent / "checkpoints/001.json.gz").write_bytes(b"changed")
    elif mutation == "source_drift":
        (tmp_path / "apps/api/app/tool_only.py").write_bytes(b"changed")
    elif mutation.startswith("scoped_"):
        path = fixture.scoped["paths"]["manifest"]
        value = strict_json(path.read_bytes())
        if mutation == "scoped_exit":
            value["exit_code"] = False
        elif mutation == "scoped_source":
            value["all_source_stable"] = False
        else:
            value["command"] = ["TOOL_ONLY-prepared"]
        dump(path, value)
    elif mutation == "missing_round":
        fixture.browser["checkpoints"].pop(2)
    else:
        fixture.browser["checkpoints"][1]["label"] = "unregistered-final-money"
    fixture.refresh()
    with pytest.raises(ValueError):
        fixture.capture()


def test_strict_duplicate_and_nonfinite_originals_refused() -> None:
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
        with pytest.raises(ValueError):
            strict_json(raw)


def test_original_metadata_copy_remains_independent(tmp_path: Path) -> None:
    fixture = ToolFixture(tmp_path)
    result = fixture.capture()
    saved = copy.deepcopy(result)
    fixture.browser["status"] = "FAILED"
    fixture.refresh()
    assert result == saved
    with pytest.raises(ValueError):
        fixture.capture()


def test_actual_absolute_observer_argument_preserves_its_registered_producer(
    tmp_path: Path,
) -> None:
    fixture = ToolFixture(tmp_path)
    path = tmp_path / fixture.browser["audit_observation"]["scoped_manifest"]
    value = strict_json(path.read_bytes())
    value["command"][1] = str(tmp_path / "scripts/w1_audit_observe.py")
    dump(path, value)
    fixture.capture()
    value["command"][1] = str(tmp_path / "unregistered/w1_audit_observe.py")
    dump(path, value)
    with pytest.raises(ValueError):
        fixture.capture()


@pytest.mark.parametrize("mutation", ["duplicate", "mixed_scenario"])
def test_three_original_rounds_cannot_be_repeated_or_mixed(tmp_path: Path, mutation: str) -> None:
    fixture = ToolFixture(tmp_path)
    row = fixture.browser["checkpoints"][5]
    assert row["label"] == "round-2-reset-before"
    if mutation == "duplicate":
        row["label"] = "round-1-reset-before"
    else:
        row["scenario_id"] = "w1-TOOLONLYother"
    fixture.refresh()
    with pytest.raises(ValueError):
        fixture.capture()
