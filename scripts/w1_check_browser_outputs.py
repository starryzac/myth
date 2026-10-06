"""Bind one current-check browser and its alive audit to their original producer runs.

This adapter reads existing bytes only. The exporter independently recomputes financial,
reset and audit semantics; this module cannot turn prepared or old development runs into
acceptance. No database, browser, clock, permission or financial outcome is supplied here.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

GROUPS = (
    "six_business_e2e",
    "three_demo_rounds",
    "original_financial_chain",
    "audit_chain",
)
ORIGINAL_ORACLE = ".runtime/drive_mvp404_browser.py"
ORIGINAL_ORACLE_SHA256 = "5f45cf3879c8fc74354bc6129e67f72788aa9ed0d1d6c2dbaf9f62531727ee35"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            require(key not in value, "Duplicate original JSON key")
            value[key] = item
        return value

    def constant(value: str) -> Any:
        raise ValueError("Nonfinite original JSON: " + value)

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def capture(
    root: Path,
    context_path: Path,
    browser_path: Path,
    browser_scoped: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return original reference groups; only new, explicitly registered runs qualify."""
    root = root.resolve()

    def local(path: Path) -> Path:
        result = path.resolve()
        require(result.is_relative_to(root) and result.is_file(), "Missing/escaped original")
        require(
            not any(part.startswith(".env") for part in result.relative_to(root).parts),
            "Private environment is not a public original",
        )
        return result

    def relative(value: Any) -> Path:
        require(isinstance(value, str) and bool(value), "Original relative path required")
        path = Path(value.replace("\\", "/"))
        require(not path.is_absolute() and ".." not in path.parts, "Original path escaped")
        return local(root / path)

    context_path, browser_path = local(context_path), local(browser_path)
    context_raw = context_path.read_bytes()
    context = strict_json(context_raw)
    require(
        context.get("protocol") == "bounded-funds-final-context-v1"
        and set(GROUPS) <= set(context.get("deferred_groups", [])),
        "Explicit current-check context does not register all browser groups",
    )
    source = context["source"]
    source_hash = source["source_sha256"]
    files = source["files"]
    require(bool(files), "Empty current-check source")
    for name, expected in files.items():
        require(sha(relative(name).read_bytes()) == expected, "Current-check source drift")
    browser = strict_json(browser_path.read_bytes())
    browser_run = browser_path.parent.name
    binding = {
        "path": context_path.relative_to(root).as_posix(),
        "sha256": sha(context_raw),
        "owner_run_id": context["owner_run_id"],
        "source_sha256": source_hash,
    }
    require(
        browser.get("purpose") == "MVP_ACCEPTANCE"
        and browser.get("acceptance_context") == binding
        and browser.get("run_id") == browser_run
        and browser.get("status") == "PASSED"
        and browser.get("mode") == "all"
        and browser.get("commit") == source["git_head"]
        and browser.get("generated_database_absent") is True
        and browser.get("temporary_database_exited") is True
        and browser.get("cleanup_status") == "VERIFIED_ABSENT"
        and re.fullmatch(r"bf_test_[0-9a-f]{32}", str(browser.get("database"))) is not None,
        "Old/partial/failed/unowned browser originals cannot close current check",
    )
    require(
        browser.get("source_before") == browser.get("source_after")
        and bool(browser.get("source_before")),
        "Actual browser source changed",
    )
    for name, expected in browser["source_before"].items():
        normalized = name.replace("\\", "/")
        require(
            (normalized in files and files[normalized] == expected)
            or (
                normalized == ORIGINAL_ORACLE
                and expected == ORIGINAL_ORACLE_SHA256
                and sha(relative(normalized).read_bytes()) == expected
            ),
            "Browser source is outside current-check freeze",
        )
    artifacts: dict[str, dict[str, Any]] = {}
    runs: dict[str, dict[str, Any]] = {}

    def original(path: Path, run_id: str, role: str = "RESULT") -> str:
        path = local(path)
        name = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        identity = "browser_" + sha(name.encode())[:24]
        validator = (
            "STRICT_JSON_V1"
            if path.suffix == ".json"
            else "PNG_HEADER_V1"
            if path.suffix == ".png"
            else "NATIVE_PROOF_ONLY"
            if path.suffix in {".gz", ".zip", ".webm"}
            else "UTF8_TEXT_V1"
        )
        record = {
            "artifact_id": identity,
            "role": role,
            "path": name,
            "sha256": sha(raw),
            "size_bytes": len(raw),
            "run_id": run_id,
            "purpose": "MVP_ACCEPTANCE",
            "source_sha256": source_hash,
            "validator": validator,
        }
        require(
            identity not in artifacts or artifacts[identity] == record, "Original run collision"
        )
        artifacts[identity] = record
        runs[run_id] = {
            "purpose": "MVP_ACCEPTANCE",
            "source_context": "CURRENT_SOURCE",
            "source_sha256": source_hash,
        }
        return identity

    def indexed(manifest: dict[str, Any], parent: Path, run_id: str) -> None:
        index = manifest.get("artifact_hashes")
        require(isinstance(index, dict) and bool(index), "Empty original index")
        assert isinstance(index, dict)
        for name, expected in index.items():
            path = relative((parent / name.replace("\\", "/")).relative_to(root).as_posix())
            require(path.is_relative_to(parent), "Indexed original escaped producer directory")
            require(sha(path.read_bytes()) == expected, "Indexed original bytes differ")
            original(path, run_id)

    def scoped(value: dict[str, Any], required_command: str) -> dict[str, str]:
        paths = value["paths"]
        require(
            set(paths) == {"manifest", "source_before", "source_after", "log"}, "Scoped originals"
        )
        manifest = strict_json(local(paths["manifest"]).read_bytes())
        before = strict_json(local(paths["source_before"]).read_bytes())
        after = strict_json(local(paths["source_after"]).read_bytes())
        argv = manifest.get("command", [])
        actual_producer = any(
            isinstance(argument, str)
            and (
                argument.replace("\\", "/") == required_command
                or (
                    Path(argument).is_absolute()
                    and Path(argument).resolve() == (root / required_command).resolve()
                )
            )
            for argument in argv
        )
        require(
            manifest.get("run_id") == value["run_id"]
            and manifest.get("status") == "PASSED"
            and type(manifest.get("exit_code")) is int
            and manifest["exit_code"] == 0
            and manifest.get("all_source_stable") is True
            and manifest.get("git_head") == source["git_head"]
            and before == after
            and all(before.get(name) == expected for name, expected in files.items())
            and actual_producer
            and "--run" in manifest["command"]
            and manifest.get("log_sha256") == sha(local(paths["log"]).read_bytes()),
            "Actual scoped producer exit/argv/source/original differs",
        )
        return {
            name: original(
                local(path), value["run_id"], "COMMAND_LOG" if name == "log" else "COMMAND_MANIFEST"
            )
            for name, path in paths.items()
        }

    browser_refs = scoped(browser_scoped, "scripts/w1_browser_acceptance.py")
    indexed(browser, browser_path.parent, browser_run)
    browser_ref = original(browser_path, browser_run)
    oracle_ref = original(root / ".runtime/drive_mvp404_browser.py", browser_run, "SOURCE")
    reset_ref = original(root / "scripts/browser_checkpoint_oracles.py", browser_run, "SOURCE")
    result_ref = original(browser_path.parent / "playwright/results.json", browser_run)
    audit = browser.get("audit_observation", {})
    observer_path = relative(audit.get("path"))
    require(
        audit.get("status") == "PASSED"
        and type(audit.get("exit_code")) is int
        and audit["exit_code"] == 0
        and audit.get("sha256") == sha(observer_path.read_bytes()),
        "Alive audit failed or original changed",
    )
    observer = strict_json(observer_path.read_bytes())
    observer_run = observer_path.parent.name
    require(
        observer.get("purpose") == "MVP_ACCEPTANCE"
        and observer.get("acceptance_context") == binding
        and observer.get("run_id") == observer_run
        and observer.get("owner_run_id") == browser_run
        and observer.get("status") == "PASSED",
        "Observer is not the actual current-check child",
    )
    audit_scoped_path = relative(audit.get("scoped_manifest"))
    audit_scoped_manifest = strict_json(audit_scoped_path.read_bytes())
    audit_refs = scoped(
        {
            "run_id": audit_scoped_manifest["run_id"],
            "paths": {
                "manifest": audit_scoped_path,
                "source_before": audit_scoped_path.parent / "source.before.json",
                "source_after": audit_scoped_path.parent / "source.after.json",
                "log": audit_scoped_path.parent / "output.log",
            },
        },
        "scripts/w1_audit_observe.py",
    )
    indexed(observer, observer_path.parent, observer_run)
    observer_ref = original(observer_path, observer_run, "AUDIT")
    checkpoint_rows = browser.get("checkpoints", [])
    require(bool(checkpoint_rows), "Empty original financial checkpoints")
    previous = None
    money_pairs = []
    rounds = []
    round_labels = []
    round_scenarios = set()
    previous_label = None
    for row in checkpoint_rows:
        metadata = row["result"]["snapshot"]
        require(
            relative(metadata["path"]).is_relative_to(browser_path.parent), "Checkpoint ownership"
        )
        if row["mode"] == "MONEY":
            require(previous is not None, "Financial checkpoint has no original before")
            money_pairs.append({"before": previous, "after": metadata})
        if row["mode"] == "BEGIN" and re.fullmatch(r"round-[123]-reset-before", row["label"]):
            require(
                previous is not None and previous_label == "rent-real-version-change",
                "Complete round must retain the exact original final money checkpoint",
            )
            rounds.append(metadata)
            round_labels.append(row["label"])
            round_scenarios.add(row["scenario_id"])
        previous = metadata
        previous_label = row["label"]
    require(
        bool(money_pairs)
        and len(rounds) == 3
        and set(round_labels) == {f"round-{number}-reset-before" for number in (1, 2, 3)}
        and len(round_scenarios) == 1,
        "Missing/duplicated original financial pairs/three rounds in the same scenario",
    )
    common = {
        "scoped": browser_refs,
        "browser_manifest": browser_ref,
        "playwright_results": result_ref,
        "oracle_source": oracle_ref,
        "reset_adapter_source": reset_ref,
    }
    inputs = {
        "six_business_e2e": common,
        "three_demo_rounds": {**common, "round_snapshots": rounds},
        "original_financial_chain": {
            "scoped": browser_refs,
            "oracle_source": oracle_ref,
            "snapshot_pairs": money_pairs,
        },
        "audit_chain": {"scoped": audit_refs, "observer": observer_ref},
    }
    ids = sorted(artifacts)
    return {
        name: {
            "check": {
                "requirement_id": name,
                "validator": "MVP_NATIVE_V2",
                "inputs": inputs[name],
                "artifact_ids": ids,
            },
            "artifacts": list(artifacts.values()),
            "runs": runs,
        }
        for name in GROUPS
    }
