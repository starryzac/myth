"""TOOL_TEST_ONLY: synthetic validation fixtures, never financial-effect evidence."""

from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path
from typing import Any

import pytest
from coverage import Coverage

ROOT = Path(__file__).resolve().parents[4]
TOOL = runpy.run_path(str(ROOT / "scripts/verify_mvp_coverage.py"))


def synthetic_scope() -> dict[str, Any]:
    return {
        "core_groups": {"test_only": {"modules": ["core.py"]}},
        "core_minimum_percent": 95,
        "backend_minimum_percent": 85,
        "coverage": {"format": 3, "version": "7.16.2"},
        "hypothesis": {"version": "6.168.3", "minimum_generated_passing": 1000},
    }


def synthetic_coverage(core: int = 95, other: int = 75) -> dict[str, Any]:
    files = {}
    for name, hit in (("core.py", core), ("other.py", other)):
        files[name] = {
            "summary": {
                "covered_lines": hit,
                "num_statements": 100,
                "missing_lines": 100 - hit,
                "excluded_lines": 0,
            },
            "executed_lines": list(range(1, hit + 1)),
            "missing_lines": list(range(hit + 1, 101)),
            "excluded_lines": [],
        }
    return {
        "meta": {"format": 3, "version": "7.16.2"},
        "files": files,
        "totals": {
            "covered_lines": core + other,
            "num_statements": 200,
            "missing_lines": 200 - core - other,
            "excluded_lines": 0,
        },
    }


def synthetic_raw(node: str, generated: int, reused: int = 0) -> str:
    # This is parser input only; no actual @given financial function ran.
    return (
        f"{node}:\n\n  - during reuse phase (0.01 seconds):\n"
        f"    - {reused} passing, 0 failing, and 0 invalid test cases\n\n"
        "  - during generate phase (0.01 seconds):\n"
        f"    - {generated} passing, 0 failing, and 7 invalid test cases\n\n"
        "  - Stopped because settings.max_examples=5000\n"
    )


def synthetic_report(generated: int, reused: int = 0) -> tuple[dict[str, Any], list[str]]:
    node = "apps/api/app/tests/test_tool_fixture.py::test_synthetic"
    return {
        "evidence_kind": "TOOL_TEST_ONLY",
        "hypothesis_version": "6.168.3",
        "pytest_exitstatus": 0,
        "records": [
            {
                "nodeid": node,
                "call_outcome": "passed",
                "raw_statistics": synthetic_raw(node, generated, reused),
            }
        ],
    }, [node]


def test_exact_thresholds_and_independent_core_file_gate() -> None:
    coverage = synthetic_coverage()
    assert TOOL["coverage_gate"](coverage, synthetic_scope(), ["core.py", "other.py"])["passed"]
    weak_core = synthetic_coverage(core=94, other=100)
    result = TOOL["coverage_gate"](weak_core, synthetic_scope(), ["core.py", "other.py"])
    assert not result["passed"]  # Aggregate 97% cannot conceal core 94%.
    assert "core.py" in " ".join(result["errors"])
    below_backend = synthetic_coverage(core=95, other=74)
    assert not TOOL["coverage_gate"](below_backend, synthetic_scope(), ["core.py", "other.py"])[
        "passed"
    ]


def test_missing_production_file_and_excluded_line_cannot_shrink_denominator() -> None:
    result = TOOL["coverage_gate"](
        synthetic_coverage(), synthetic_scope(), ["core.py", "other.py", "forgotten.py"]
    )
    assert not result["passed"]
    assert "forgotten.py" in " ".join(result["errors"])
    excluded = synthetic_coverage()
    excluded["files"]["other.py"]["summary"]["excluded_lines"] = 1
    excluded["files"]["other.py"]["excluded_lines"] = [101]
    excluded["totals"]["excluded_lines"] = 1
    assert not TOOL["coverage_gate"](excluded, synthetic_scope(), ["core.py", "other.py"])["passed"]


def test_coverage_counts_are_not_rounded_percent_or_unchecked_summary() -> None:
    coverage = synthetic_coverage(core=94, other=100)
    coverage["files"]["core.py"]["summary"]["percent_covered"] = 100
    assert not TOOL["coverage_gate"](coverage, synthetic_scope(), ["core.py", "other.py"])["passed"]
    bad = synthetic_coverage()
    bad["files"]["core.py"]["summary"]["covered_lines"] = True
    with pytest.raises(ValueError, match="integer"):
        TOOL["coverage_gate"](bad, synthetic_scope(), ["core.py", "other.py"])
    bad = synthetic_coverage()
    bad["files"]["core.py"]["executed_lines"] = [1] * 95
    with pytest.raises(ValueError, match="disagrees"):
        TOOL["coverage_gate"](bad, synthetic_scope(), ["core.py", "other.py"])


def test_reuse_invalid_and_settings_limits_never_count_as_actual_generated_samples() -> None:
    report, expected = synthetic_report(20, reused=1000)
    result = TOOL["hypothesis_gate"](report, synthetic_scope(), expected)
    assert result["generated_passing"] == 20
    assert result["passing_other_phases_not_counted"] == 1000
    assert result["invalid_all_phases"] == 7
    assert not result["passed"]
    assert any("below 1000" in error for error in result["errors"])
    report, expected = synthetic_report(1000)
    result = TOOL["hypothesis_gate"](report, synthetic_scope(), expected)
    assert not result["passed"]  # A synthetic 1000 still is TOOL_TEST_ONLY.
    assert any("Synthetic/tool" in error for error in result["errors"])


def test_duplicate_runs_and_unknown_tool_properties_cannot_inflate_acceptance() -> None:
    report, expected = synthetic_report(1000)
    report["records"].append(copy.deepcopy(report["records"][0]))
    with pytest.raises(ValueError, match="Repeated node"):
        TOOL["hypothesis_gate"](report, synthetic_scope(), expected)
    report, expected = synthetic_report(1000)
    assert not TOOL["hypothesis_gate"](report, synthetic_scope(), expected + ["missing"])["passed"]
    assert not TOOL["hypothesis_gate"](report, synthetic_scope(), ["other"])["passed"]
    raw = report["records"][0]["raw_statistics"]
    with pytest.raises(ValueError, match="Duplicate"):
        TOOL["parse_statistics"](raw + raw)


def test_failed_calls_and_unsupported_local_statistics_version_are_rejected() -> None:
    report, expected = synthetic_report(1000)
    report["records"][0]["call_outcome"] = "failed"
    report["hypothesis_version"] = "future-version"
    report["pytest_exitstatus"] = 1
    result = TOOL["hypothesis_gate"](report, synthetic_scope(), expected)
    assert len(result["errors"]) >= 4
    assert not result["passed"]


def test_registered_scope_contains_original_core_and_actual_financial_properties() -> None:
    scope = json.loads((ROOT / "docs/spec/mvp-coverage-scope.json").read_text(encoding="utf-8"))
    inventory = TOOL["production_files"](ROOT, scope)
    modules = {path for group in scope["core_groups"].values() for path in group["modules"]}
    assert (
        {
            "apps/api/app/domain/boundary.py",
            "apps/api/app/services/boundary.py",
            "apps/api/app/domain/policy_configuration.py",
            "apps/api/app/services/policy_lifecycle.py",
            "apps/api/app/domain/recovery.py",
            "apps/api/app/services/recovery.py",
            "apps/api/app/services/recovery_receipt_integrity.py",
            "apps/api/app/services/simulated_bank.py",
        }
        <= modules
        <= set(inventory)
    )
    nodes = TOOL["property_nodes"](ROOT, scope)
    assert len(nodes) == 21
    assert all("test_mvp_coverage_tool" not in node for node in nodes)
    assert all("/tests/" not in path for path in inventory)


def test_independent_coverage_config_retains_namespace_and_all_non_test_lines() -> None:
    config = Coverage(config_file=str(ROOT / "docs/spec/mvp-coverage.ini"))
    assert config.get_option("run:source") == ["apps/api/app"]
    assert config.get_option("run:omit") == ["*/tests/*"]
    assert config.get_option("report:include_namespace_packages") is True
    assert config.get_option("report:exclude_lines") == []
