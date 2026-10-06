"""Validate W0 FULL mapping coverage and exact historical requirements without running services."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "docs/spec/full-delta-map.yaml"
# Exact primary ownership from section 5 of the user-adopted execution revision.
EXPECTED_PARTITION = {
    "W2": "001 002 003 101 102 103 104 105 601 602 603 605 607",
    "W3": "201 202 203 204 205 206 301 302 303 304 305 306 307",
    "W4": "401 402 403 404 405 406 604 606",
    "W5": "106 107 108 501 502 503 504 505 506 507",
    "W6": "701 702 703 704 705 706 707 708",
    "W7": "801 802 803 804 805 806 807 808",
    "W8": "901 902 903 904 905 906 907",
}


def source_tasks(text: str) -> dict[str, dict[str, Any]]:
    matches = list(re.finditer(r"^### (FULL-\d{3}) (.+)$", text, re.MULTILINE))
    tasks: dict[str, dict[str, Any]] = {}
    for match in matches:
        identifier = match.group(1)
        if identifier in tasks:
            raise ValueError(f"duplicate original task: {identifier}")
        following = text.find("\n### ", match.end())
        block = text[match.start() : following if following != -1 else len(text)]
        block = block.split("\n---")[0].split("\n## ")[0].rstrip()
        tasks[identifier] = {
            "title": match.group(2),
            "task_line": text[: match.start()].count("\n") + 1,
            "task_block": block,
        }
    return tasks


def tracker_rows(text: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.startswith("|") or "FULL-" not in line:
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 8 or not re.fullmatch(r"FULL-\d{3}", fields[2]):
            continue
        if fields[2] in rows:
            raise ValueError(f"duplicate tracker task: {fields[2]}")
        rows[fields[2]] = fields
    return rows


def validate(data: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    full = ROOT / data["authoritative_sources"]["full_plan"]
    original = source_tasks(full.read_text(encoding="utf-8"))
    tracker = tracker_rows(
        (ROOT / data["authoritative_sources"]["tracker"]).read_text(encoding="utf-8")
    )
    entries = data["requirements"]
    identifiers = [entry["id"] for entry in entries]
    if len(original) != 67 or len(entries) != 67:
        errors.append("original and mapped task counts must both equal 67")
    if len(set(identifiers)) != len(identifiers):
        errors.append("mapped FULL IDs are not unique")
    if set(identifiers) != set(original) or set(tracker) != set(original):
        errors.append("original, tracker and mapping FULL ID sets differ")

    partition = data["primary_closing_partition"]
    expected_counts = {"W2": 13, "W3": 13, "W4": 8, "W5": 10, "W6": 8, "W7": 8, "W8": 7}
    package_counts = {package: len(members) for package, members in partition.items()}
    if package_counts != expected_counts:
        errors.append("W2-W8 package counts differ from explicit revision")
    for package, suffixes in EXPECTED_PARTITION.items():
        expected_ids = {"FULL-" + suffix for suffix in suffixes.split()}
        if set(partition.get(package, [])) != expected_ids:
            errors.append(package + " primary membership differs from explicit revision")
    flattened = [identifier for members in partition.values() for identifier in members]
    if len(flattened) != 67 or len(set(flattened)) != 67 or set(flattened) != set(original):
        errors.append("W2-W8 is not a disjoint exhaustive original FULL partition")

    referenced_paths: set[str] = set()
    dirty_paths = set(data["baseline"]["working_tree_snapshot"]["dirty_paths"])
    for entry in entries:
        identifier = entry["id"]
        prefix = f"{identifier}: "
        source = original.get(identifier)
        row = tracker.get(identifier)
        if source is None or row is None:
            continue
        if entry["status"] != "PENDING" or entry["required_closing_evidence"]:
            errors.append(prefix + "W0 mapping cannot close a FULL task")
        if entry["title"] != source["title"]:
            errors.append(prefix + "original title differs")
        if entry["original_task_text"] != source["task_block"]:
            errors.append(prefix + "exact original task text differs")
        if entry["original_source"]["task_line"] != source["task_line"]:
            errors.append(prefix + "original task line differs")
        source_fields = {
            "original_requirement": row[4],
            "original_acceptance": row[5],
        }
        for field, expected in source_fields.items():
            if entry[field] != expected:
                errors.append(prefix + field + " differs from original tracker")
        if entry["original_source"]["traceability_source_lines"] != row[3]:
            errors.append(prefix + "original source references differ")
        package = entry["primary_closing_package"]
        if package not in partition or identifier not in partition[package]:
            errors.append(prefix + "primary closing package is inconsistent")
        if not entry["missing_increments"] or not entry["reuse_boundary"]:
            errors.append(prefix + "missing explicit delta or reuse limitation")
        for field in (
            "reusable_implementation_paths",
            "reusable_test_paths",
            "reusable_evidence_paths",
        ):
            paths = entry[field]
            if len(paths) != len(set(paths)):
                errors.append(prefix + field + " contains duplicate paths")
            for name in paths:
                referenced_paths.add(name)
                path = (ROOT / name).resolve()
                if not path.is_relative_to(ROOT) or not path.is_file():
                    errors.append(prefix + "missing or out-of-root reusable path: " + name)
        candidate = set(entry["dirty_candidate_paths"])
        actual_dirty = set(entry["reusable_implementation_paths"]) & dirty_paths
        if candidate != actual_dirty:
            errors.append(prefix + "dirty candidate flags disagree with recorded snapshot")

    for name, expected in data["baseline"]["source_plan_sha256"].items():
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if actual != expected:
            errors.append("historical source plan bytes changed: " + name)

    changed_since_snapshot: list[str] = []
    snapshot = data["baseline"]["working_tree_snapshot"]
    for name, expected in snapshot["dirty_sha256"].items():
        path = ROOT / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            changed_since_snapshot.append(name)
    return {
        "status": "PASS" if not errors else "FAIL",
        "mapping_only": True,
        "original_full_count": len(original),
        "mapped_full_count": len(entries),
        "unique_full_count": len(set(identifiers)),
        "status_counts": dict(Counter(entry["status"] for entry in entries)),
        "package_counts": package_counts,
        "reusable_file_count": len(referenced_paths),
        "source_plan_hashes_unchanged": not any("source plan" in error for error in errors),
        "dirty_paths_changed_since_mapping_snapshot": changed_since_snapshot,
        "errors": errors,
        "scope": (
            "ID/source-text/partition/path checks only; no implementation or test closure proof"
        ),
    }


def main() -> int:
    try:
        data = yaml.safe_load(MAP_PATH.read_text(encoding="utf-8"))
        report = validate(data)
    except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError) as error:
        report = {"status": "FAIL", "errors": [str(error)], "mapping_only": True}
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
