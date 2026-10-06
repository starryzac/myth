"""Read-only MVP-501 coverage and observed Hypothesis-statistics gate."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCOPE_PATH = ROOT / "docs/spec/mvp-coverage-scope.json"
REQUIRED_CORE = frozenset(
    f"apps/api/app/{name}.py"
    for name in (
        "domain/boundary",
        "domain/boundary_types",
        "domain/boundary_details_types",
        "services/boundary",
        "domain/living_reserve",
        "services/living_reserve",
        "domain/policy_configuration",
        "services/policy_lifecycle",
        "domain/recovery",
        "domain/recovery_types",
        "services/recovery",
        "services/recovery_sources",
        "services/recovery_projection",
        "services/recovery_receipt_integrity",
        "services/execution_sources",
        "services/simulated_redemption_quote",
        "domain/asset_exposure",
        "services/asset_exposure_import",
        "domain/income_ledger",
        "services/income_ledger",
        "services/financial_read",
        "services/historical_read",
        "domain/bank_posting_codec",
        "services/simulated_bank",
    )
)
REQUIRED_PROPERTIES = frozenset(
    f"apps/api/app/tests/test_{name}_properties.py"
    for name in ("boundary", "goal_allocation", "asset_allocation", "recovery")
)
NODE = re.compile(r"^(apps[\\/]api[\\/]app[\\/]tests[\\/].+::[^\s:]+):\s*$")
PHASE = re.compile(r"^  - during (reuse|generate|shrink|explain) phase \([^)]+\):$")
COUNTS = re.compile(r"^    - (\d+) passing, (\d+) failing, and (\d+) invalid test cases$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_scope(scope: dict[str, Any]) -> None:
    """A custom scope may tighten the registered gate, never weaken its floor."""
    if scope["backend_root"] != "apps/api/app" or set(scope["excluded_directory_names"]) != {
        "tests",
        "__pycache__",
    }:
        raise ValueError("The complete original backend denominator is required")
    for field, floor in (("backend_minimum_percent", 85), ("core_minimum_percent", 95)):
        value = scope[field]
        if type(value) is not int or not floor <= value <= 100:
            raise ValueError(f"{field} cannot weaken the original {floor}% minimum")
    modules = [path for group in scope["core_groups"].values() for path in group["modules"]]
    if len(modules) != len(set(modules)) or not REQUIRED_CORE.issubset(modules):
        raise ValueError("All 24 registered core modules are required without duplicates")
    properties = scope["hypothesis"]["property_files"]
    if len(properties) != len(set(properties)) or not REQUIRED_PROPERTIES.issubset(properties):
        raise ValueError("All four registered financial property files are required")
    for path in modules + properties:
        if not isinstance(path, str) or "\\" in path or ".." in Path(path).parts:
            raise ValueError("Registered paths must be canonical repository-relative source paths")
        if not path.startswith("apps/api/app/") or not path.endswith(".py"):
            raise ValueError("Registered paths must retain the original backend source root")
    minimum = scope["hypothesis"]["minimum_generated_passing"]
    if type(minimum) is not int or minimum < 1000:
        raise ValueError("At least 1000 actual generated passing evaluations are required")
    if scope["coverage"]["config_path"] != "docs/spec/mvp-coverage.ini":
        raise ValueError("The registered complete-denominator coverage configuration is required")


def production_files(root: Path, scope: dict[str, Any]) -> list[str]:
    base = root / scope["backend_root"]
    excluded = set(scope["excluded_directory_names"])
    return sorted(
        path.relative_to(root).as_posix()
        for path in base.rglob("*.py")
        if not excluded.intersection(path.relative_to(base).parts)
    )


def source_hashes(root: Path, scope: dict[str, Any]) -> dict[str, str]:
    paths = set(production_files(root, scope))
    paths.update(path.relative_to(root).as_posix() for path in (root / "scripts").rglob("*.py"))
    paths.update(path.relative_to(root).as_posix() for path in (root / "deploy").rglob("*.py"))
    paths.update(
        path.relative_to(root).as_posix() for path in (root / "apps/api/app/tests").rglob("*.py")
    )
    paths.update(scope["hypothesis"]["property_files"])
    paths.update(
        (
            "pyproject.toml",
            "uv.lock",
            "docs/spec/mvp-coverage-scope.json",
            "docs/spec/mvp-coverage.ini",
            "scripts/mvp_hypothesis_counter.py",
            "scripts/verify_mvp_coverage.py",
        )
    )
    return {path: sha256(root / path) for path in sorted(paths)}


def property_nodes(root: Path, scope: dict[str, Any]) -> list[str]:
    """Discover actual @given functions without importing financial code."""
    nodes: list[str] = []
    for path in scope["hypothesis"]["property_files"]:
        tree = ast.parse((root / path).read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                func = decorator.func if isinstance(decorator, ast.Call) else decorator
                if (isinstance(func, ast.Name) and func.id == "given") or (
                    isinstance(func, ast.Attribute) and func.attr == "given"
                ):
                    nodes.append(f"{path}::{node.name}")
                    break
    if not nodes:
        raise ValueError("No registered financial @given functions were found")
    return sorted(nodes)


def parse_statistics(raw: str) -> dict[str, Any]:
    """Parse one original Hypothesis 6.168.3 plugin statistics block."""
    result: dict[str, Any] = {"nodeid": None, "phases": {}}
    phase: str | None = None
    for line in raw.splitlines():
        if match := NODE.fullmatch(line):
            if result["nodeid"] is not None:
                raise ValueError("Duplicate statistics blocks cannot be summed")
            result["nodeid"] = match[1].replace("\\", "/")
        elif match := PHASE.fullmatch(line):
            phase = match[1]
            if phase in result["phases"]:
                raise ValueError("Duplicate phase statistics cannot be summed")
            result["phases"][phase] = None
        elif match := COUNTS.fullmatch(line):
            if phase is None or result["phases"][phase] is not None:
                raise ValueError("Statistics counts are outside a unique phase")
            result["phases"][phase] = dict(
                zip(("passing", "failing", "invalid"), map(int, match.groups()), strict=True)
            )
    if result["nodeid"] is None or not result["phases"]:
        raise ValueError("Missing actual plugin node/phase statistics")
    if any(value is None for value in result["phases"].values()):
        raise ValueError("Incomplete plugin phase statistics")
    return result


def integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def coverage_gate(
    coverage: dict[str, Any], scope: dict[str, Any], inventory: list[str]
) -> dict[str, Any]:
    errors: list[str] = []
    meta = coverage.get("meta", {})
    if meta.get("format") != scope["coverage"]["format"]:
        errors.append("Unsupported coverage JSON format")
    if meta.get("version") != scope["coverage"]["version"]:
        errors.append("Coverage version differs from registered local version")
    files: dict[str, Any] = {}
    for original, detail in coverage.get("files", {}).items():
        name = original.replace("\\", "/")
        if name.startswith("./"):
            name = name[2:]
        if name in files:
            raise ValueError(f"Duplicate normalized coverage file: {name}")
        files[name] = detail
    missing = sorted(set(inventory) - files.keys())
    extra = sorted(files.keys() - set(inventory))
    if missing:
        errors.append(f"Production files absent from coverage: {missing}")
    if extra:
        errors.append(f"Files outside registered backend inventory: {extra}")
    core = {path for group in scope["core_groups"].values() for path in group["modules"]}
    if len(core) != sum(len(group["modules"]) for group in scope["core_groups"].values()):
        raise ValueError("Core scope contains duplicate modules")
    if not core.issubset(inventory):
        errors.append("Registered core module is missing from current source inventory")
    covered = statements = excluded = 0
    per_core: dict[str, Any] = {}
    for path, detail in sorted(files.items()):
        summary = detail["summary"]
        hit = integer(summary["covered_lines"], f"{path}.covered_lines")
        total = integer(summary["num_statements"], f"{path}.num_statements")
        absent = integer(summary["missing_lines"], f"{path}.missing_lines")
        omitted = integer(summary["excluded_lines"], f"{path}.excluded_lines")
        if hit + absent != total:
            raise ValueError(f"Coverage line totals are inconsistent: {path}")
        for key, expected in (("executed_lines", hit), ("missing_lines", absent)):
            lines = detail[key]
            if (
                any(type(line) is not int or line < 1 for line in lines)
                or len(lines) != expected
                or len(set(lines)) != len(lines)
            ):
                raise ValueError(f"Coverage {key} list disagrees with summary: {path}")
        if set(detail["executed_lines"]).intersection(detail["missing_lines"]):
            raise ValueError(f"Covered and missing coverage lines overlap: {path}")
        excluded_lines = detail["excluded_lines"]
        if len(excluded_lines) != omitted:
            raise ValueError(f"Coverage exclusions disagree with summary: {path}")
        if omitted:
            errors.append(f"Unapproved executable line exclusions: {path}:{excluded_lines}")
        covered += hit
        statements += total
        excluded += omitted
        if path in core:
            okay = total > 0 and 100 * hit >= scope["core_minimum_percent"] * total
            per_core[path] = {
                "covered_lines": hit,
                "num_statements": total,
                "percent": 100 * hit / total if total else None,
                "passed": okay,
            }
            if not okay:
                errors.append(f"Core module below 95% (or empty): {path}: {hit}/{total}")
    totals = coverage.get("totals", {})
    for key, value in (
        ("covered_lines", covered),
        ("num_statements", statements),
        ("missing_lines", statements - covered),
        ("excluded_lines", excluded),
    ):
        if integer(totals.get(key), f"totals.{key}") != value:
            errors.append(f"Coverage aggregate differs from complete file sum: {key}")
    if not statements or covered * 100 < scope["backend_minimum_percent"] * statements:
        errors.append(f"Complete backend coverage below 85%: {covered}/{statements}")
    return {
        "passed": not errors,
        "errors": errors,
        "inventory_count": len(inventory),
        "covered_lines": covered,
        "num_statements": statements,
        "percent": covered * 100 / statements if statements else None,
        "core_modules": per_core,
    }


def hypothesis_gate(
    report: dict[str, Any], scope: dict[str, Any], expected_nodes: list[str]
) -> dict[str, Any]:
    errors: list[str] = []
    if report.get("coverage_append") is not False:
        errors.append("Coverage must be measured in this run without append")
    if report.get("evidence_kind") != "OBSERVED_PYTEST_HYPOTHESIS_RUNTIME":
        errors.append("Synthetic/tool input is not observed financial runtime evidence")
    if report.get("hypothesis_version") != scope["hypothesis"]["version"]:
        errors.append("Hypothesis version differs from registered statistics parser")
    if type(report.get("pytest_exitstatus")) is not int or report["pytest_exitstatus"] != 0:
        errors.append("The actual pytest run did not finish successfully")
    actual: dict[str, Any] = {}
    generated = failed = invalid = other_passing = 0
    for record in report.get("records", []):
        parsed = parse_statistics(record["raw_statistics"])
        node = parsed["nodeid"]
        if node in actual:
            raise ValueError(f"Repeated node statistics cannot inflate samples: {node}")
        if record["nodeid"] != node or node not in expected_nodes:
            errors.append(f"Unregistered or mismatched financial property node: {node}")
        if record.get("call_outcome") != "passed":
            errors.append(f"Financial property call was not passed: {node}")
        counts = parsed["phases"].get("generate", {"passing": 0, "failing": 0, "invalid": 0})
        if counts["passing"] == 0:
            errors.append(f"No successful generated examples observed: {node}")
        generated += counts["passing"]
        failed += sum(phase["failing"] for phase in parsed["phases"].values())
        invalid += sum(phase["invalid"] for phase in parsed["phases"].values())
        other_passing += sum(
            values["passing"] for phase, values in parsed["phases"].items() if phase != "generate"
        )
        actual[node] = parsed["phases"]
    missing = sorted(set(expected_nodes) - actual.keys())
    if missing:
        errors.append(f"Registered financial @given nodes missing statistics: {missing}")
    if generated < scope["hypothesis"]["minimum_generated_passing"]:
        errors.append(f"Actual generated passing samples below 1000: {generated}")
    if failed:
        errors.append(f"Hypothesis reported failing evaluations: {failed}")
    return {
        "passed": not errors,
        "errors": errors,
        "generated_passing": generated,
        "failing_all_phases": failed,
        "invalid_all_phases": invalid,
        "passing_other_phases_not_counted": other_passing,
        "expected_nodes": expected_nodes,
        "per_node_phases": actual,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--hypothesis-report", type=Path, required=True)
    parser.add_argument("--scope", type=Path, default=SCOPE_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    scope = json.loads(args.scope.read_text(encoding="utf-8-sig"))
    report = json.loads(args.hypothesis_report.read_text(encoding="utf-8-sig"))
    errors: list[str] = []
    try:
        validate_scope(scope)
        observed_hashes = source_hashes(ROOT, scope)
        if report.get("bindings_before") != report.get("bindings_after"):
            errors.append("Scope/configuration/counter/verifier changed during the measured run")
        expected_bindings = {
            "scope_sha256": sha256(args.scope),
            "counter_sha256": sha256(ROOT / "scripts/mvp_hypothesis_counter.py"),
            "verifier_sha256": sha256(Path(__file__)),
            "coverage_config_sha256": sha256(ROOT / scope["coverage"]["config_path"]),
        }
        if report.get("bindings_before") != expected_bindings:
            errors.append("Measured scope/configuration/tool originals differ from current source")
        for key, expected in (
            ("source_hashes_before", observed_hashes),
            ("source_hashes_after", observed_hashes),
            ("scope_sha256", sha256(args.scope)),
            ("counter_sha256", sha256(ROOT / "scripts/mvp_hypothesis_counter.py")),
            ("verifier_sha256", sha256(Path(__file__))),
            ("coverage_json_sha256", sha256(args.coverage)),
            ("coverage_config_path", scope["coverage"]["config_path"]),
            ("coverage_config_sha256", sha256(ROOT / scope["coverage"]["config_path"])),
        ):
            if report.get(key) != expected:
                errors.append(f"Runtime evidence is not bound to current input/source: {key}")
        coverage = coverage_gate(
            json.loads(args.coverage.read_text(encoding="utf-8-sig")),
            scope,
            production_files(ROOT, scope),
        )
        hypothesis = hypothesis_gate(report, scope, property_nodes(ROOT, scope))
    except (ValueError, KeyError, TypeError) as error:
        errors.append(f"Malformed/unrecognized evidence: {error}")
        coverage = {"passed": False}
        hypothesis = {"passed": False}
    result = {
        "task": "MVP-501",
        "status": "GATE_PASSED"
        if not errors and coverage["passed"] and hypothesis["passed"]
        else "GATE_FAILED",
        "coverage": coverage,
        "hypothesis": hypothesis,
        "binding_errors": errors,
        "inputs": {
            "coverage_sha256": sha256(args.coverage),
            "hypothesis_report_sha256": sha256(args.hypothesis_report),
            "scope_sha256": sha256(args.scope),
        },
        "boundary": scope["evidence_boundary"],
        "task_closed": False,
    }
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(output)
    print(output, end="")
    return 0 if result["status"] == "GATE_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
