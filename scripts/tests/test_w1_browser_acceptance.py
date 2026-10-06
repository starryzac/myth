"""Pure scope/report controls; TOOL_ONLY reports cannot become browser evidence."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.w1_browser_acceptance import (
    BUSINESS_TITLES,
    ROUND_TITLE,
    browser_command,
    validate_results,
)


def report(titles: list[str]) -> dict[str, Any]:
    return {
        "errors": [],
        "suites": [
            {
                "specs": [
                    {"title": title, "ok": True, "tests": [{"results": [{"status": "passed"}]}]}
                    for title in titles
                ]
            }
        ],
    }


def test_same_seven_named_single_attempts_required(tmp_path: Path) -> None:
    original = report([*BUSINESS_TITLES, ROUND_TITLE])
    path = tmp_path / "TOOL_ONLY.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    assert validate_results(path, "all")["count"] == 7
    partial = copy.deepcopy(original)
    partial["suites"][0]["specs"].pop()
    path.write_text(json.dumps(partial), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_results(path, "all")


@pytest.mark.parametrize(
    "failure", ["skipped", "failed", "retry", "duplicate_title", "global_error"]
)
def test_no_skip_retry_or_duplicate_case_denominator(tmp_path: Path, failure: str) -> None:
    value = report([*BUSINESS_TITLES, ROUND_TITLE])
    first = value["suites"][0]["specs"][0]
    if failure in {"skipped", "failed"}:
        first["tests"][0]["results"][0]["status"] = failure
    elif failure == "retry":
        first["tests"][0]["results"].append({"status": "passed"})
    elif failure == "duplicate_title":
        first["title"] = ROUND_TITLE
    else:
        value["errors"] = [{"message": "actual tool fixture error"}]
    path = tmp_path / "TOOL_ONLY.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_results(path, "all")


def test_rent_targeted_scope_never_closes_all(tmp_path: Path) -> None:
    path = tmp_path / "TOOL_ONLY.json"
    path.write_text(json.dumps(report([BUSINESS_TITLES[4]])), encoding="utf-8")
    assert validate_results(path, "rent")["mode"] == "rent"
    for mode in ("all", "cases", "rounds"):
        with pytest.raises(ValueError):
            validate_results(path, mode)
    arguments = browser_command("pnpm", "rent")
    assert arguments[-2:] == ["--grep", "修改房租后绑定旧版本"]


def test_goal_targeted_scope_keeps_original_title_and_never_closes_other_cases(
    tmp_path: Path,
) -> None:
    path = tmp_path / "TOOL_ONLY-goal.json"
    path.write_text(json.dumps(report([BUSINESS_TITLES[1]])), encoding="utf-8")
    result = validate_results(path, "goal")
    assert result == {"count": 1, "titles": [BUSINESS_TITLES[1]], "mode": "goal"}
    for mode in ("all", "cases", "rounds", "rent"):
        with pytest.raises(ValueError):
            validate_results(path, mode)
    assert browser_command("pnpm", "goal")[-2:] == ["--grep", BUSINESS_TITLES[1]]


@pytest.mark.parametrize("failure", ["skipped", "failed", "retry", "global_error"])
def test_goal_partial_negative_cannot_become_current_check(tmp_path: Path, failure: str) -> None:
    value = report([BUSINESS_TITLES[1]])
    first = value["suites"][0]["specs"][0]
    if failure in {"skipped", "failed"}:
        first["tests"][0]["results"][0]["status"] = failure
    elif failure == "retry":
        first["tests"][0]["results"].append({"status": "passed"})
    else:
        value["errors"] = [{"message": "TOOL_ONLY actual failure"}]
    path = tmp_path / "TOOL_ONLY-goal-negative.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_results(path, "goal")
