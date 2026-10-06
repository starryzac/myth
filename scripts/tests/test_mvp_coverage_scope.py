"""TOOL_TEST_ONLY: the configurable gate cannot weaken the original acceptance."""

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.mvp_hypothesis_counter import ObservedStatistics
from scripts.verify_mvp_coverage import REQUIRED_CORE, REQUIRED_PROPERTIES, validate_scope

ROOT = Path(__file__).resolve().parents[2]


def scope() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((ROOT / "docs/spec/mvp-coverage-scope.json").read_text(encoding="utf-8")),
    )


def test_registered_complete_scope_retains_original_minima() -> None:
    validate_scope(scope())
    assert len(REQUIRED_CORE) == 24 and len(REQUIRED_PROPERTIES) == 4
    tightened = scope()
    tightened.update(backend_minimum_percent=90, core_minimum_percent=98)
    tightened["hypothesis"]["minimum_generated_passing"] = 2000
    validate_scope(tightened)


@pytest.mark.parametrize(
    "field,value",
    [("backend_minimum_percent", 84), ("core_minimum_percent", 94), ("core_minimum_percent", True)],
)
def test_custom_scope_cannot_lower_original_coverage(field: str, value: object) -> None:
    weakened = scope()
    weakened[field] = value
    with pytest.raises(ValueError, match="original"):
        validate_scope(weakened)


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_core",
        "missing_core",
        "duplicate_core",
        "missing_properties",
        "excluded_service",
        "partial_backend",
        "different_config",
        "samples",
    ],
)
def test_custom_scope_cannot_shrink_denominator_or_observed_samples(mutation: str) -> None:
    weakened = copy.deepcopy(scope())
    if mutation == "empty_core":
        weakened["core_groups"] = {}
    elif mutation == "missing_core":
        weakened["core_groups"]["boundary"]["modules"].pop()
    elif mutation == "duplicate_core":
        weakened["core_groups"]["boundary"]["modules"].append(
            weakened["core_groups"]["boundary"]["modules"][0]
        )
    elif mutation == "missing_properties":
        weakened["hypothesis"]["property_files"].pop()
    elif mutation == "excluded_service":
        weakened["excluded_directory_names"].append("services")
    elif mutation == "partial_backend":
        weakened["backend_root"] = "apps/api/app/domain"
    elif mutation == "different_config":
        weakened["coverage"]["config_path"] = "pyproject.toml"
    else:
        weakened["hypothesis"]["minimum_generated_passing"] = 999
    with pytest.raises(ValueError):
        validate_scope(weakened)


@pytest.mark.parametrize("reused", ["effective_append", "prior_json"])
def test_observer_refuses_merged_coverage_and_existing_report_before_collection(
    reused: str,
) -> None:
    class ConfigStub:
        def getoption(self, key: str, default: object = None) -> object:
            options: dict[str, object] = {
                "--mvp-coverage-scope": ROOT / "docs/spec/mvp-coverage-scope.json",
                "--mvp-coverage-json": ROOT / "README.md" if reused == "prior_json" else None,
                "cov_config": "docs/spec/mvp-coverage.ini",
                "cov_append": reused == "effective_append",
            }
            return options.get(key, default)

    with pytest.raises(pytest.UsageError):
        ObservedStatistics(cast(pytest.Config, ConfigStub()), ROOT / ".runtime/unused-report.json")
