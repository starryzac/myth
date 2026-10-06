"""Produce a W0 verification plan from changed files; never execute checks."""

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = ROOT / "docs/spec/verification-map.yaml"


def select_verification(files: list[str], configuration: dict[str, Any]) -> dict[str, Any]:
    changed = sorted({name.replace("\\", "/").removeprefix("./") for name in files})
    stages: dict[str, list[str]] = {stage: [] for stage in configuration["stage_order"]}
    matched: list[dict[str, Any]] = []
    unmatched: list[str] = []
    uncovered: list[str] = []
    checks: list[list[str]] = []
    sets = configuration["test_sets"]

    def add_rule(rule: dict[str, Any], names: list[str]) -> None:
        matched.append({"rule": rule["id"], "files": names, "reason": rule["reason"]})
        for stage in ("quick_pure", "integration_pg"):
            for group in rule.get(stage, []):
                stages[stage].extend(sets[group])
        stages["benchmark"].extend(rule.get("benchmark", []))
        uncovered.extend(rule.get("uncovered", []))
        checks.extend(rule.get("checks", []))

    covered: set[str] = set()
    for rule in configuration["rules"]:
        names = [
            name
            for name in changed
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in rule["patterns"])
        ]
        if names:
            add_rule(rule, names)
            covered.update(names)
    for name in changed:
        if name in covered:
            continue
        unmatched.append(name)
        financial = any(
            name.startswith(prefix) for prefix in configuration["unknown_financial_prefixes"]
        )
        key = "unknown_financial_policy" if financial else "unknown_other_policy"
        policy = configuration[key]
        fallback = {
            **policy,
            "id": key,
            "reason": "Unrecognized changed file requires explicit impact review.",
        }
        add_rule(fallback, [name])
    python_files = [name for name in changed if name.endswith(".py")]
    if python_files:
        checks.insert(0, ["python", "-m", "ruff", "format", "--check", *python_files])
        checks.insert(0, ["python", "-m", "ruff", "check", *python_files])
        api_sources = [name for name in python_files if name.startswith("apps/api/app/services/")]
        if api_sources:
            checks.append(["python", "-m", "mypy", *api_sources])
    stages["quick_static"] = [" ".join(command) for command in checks]
    for stage, nodes in stages.items():
        stages[stage] = list(dict.fromkeys(nodes))
    return {
        "status": "NEEDS_SCOPE_REVIEW" if unmatched else "SELECTED_NOT_RUN",
        "executed": False,
        "changed_files": changed,
        "stage_order": configuration["stage_order"],
        "stages": stages,
        "quick_static_argv": checks,
        "selections": matched,
        "unrecognized_files": unmatched,
        "uncovered": list(dict.fromkeys(uncovered)),
        "rules": configuration["execution_rules"],
        "full_suite_selected": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+")
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    configuration = yaml.safe_load(args.map.read_text(encoding="utf-8"))
    result = select_verification(args.files, configuration)
    encoded = json.dumps(result, ensure_ascii=True, indent=2) + "\n"
    if args.output is not None:
        # Exclusive creation preserves prior selection evidence and original failures.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    print(encoded, end="")
    return 2 if result["unrecognized_files"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
