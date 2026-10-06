"""Verify the public quality orchestration without launching services or test suites."""

import ast
import io
import json
import os
import runpy
import subprocess
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[4]
MYPY = ("uv", "run", "--frozen", "mypy")
LINT = [
    ("uv", "run", "--frozen", "ruff", "check", "apps/api", "scripts"),
    ("uv", "run", "--frozen", "ruff", "format", "--check", "apps/api", "scripts"),
    MYPY,
    ("pnpm", "--dir", "apps/web", "lint"),
]
TYPECHECK = [
    ("uv", "run", "--frozen", "python", "scripts/generate_openapi.py", "--check"),
    MYPY,
    ("pnpm", "--dir", "apps/web", "typecheck"),
]


@pytest.fixture
def task_globals(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    # The CLI configures its process environment/streams on import; isolate that setup.
    with monkeypatch.context() as context:
        context.chdir(ROOT)
        context.setattr(os, "environ", os.environ.copy())
        context.delenv("BOUNDEDFUNDS_FINAL_CONTEXT", raising=False)
        with (
            io.TextIOWrapper(io.BytesIO(), encoding="utf-8") as stdout,
            io.TextIOWrapper(io.BytesIO(), encoding="utf-8") as stderr,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            namespace = runpy.run_path(str(ROOT / "scripts/tasks.py"))

        # Capture the original requested child argv at the native-wrapper boundary.
        # These are orchestration fixtures, never real scoped output manifests.
        def scoped_record(label: str, *args: str) -> dict[str, Any]:
            namespace["main"].__globals__["run"](*args)
            return {"run_id": "TOOL_ONLY-" + label, "paths": {}}

        namespace["main"].__globals__["scoped_run"] = scoped_record
        yield namespace["main"].__globals__


@pytest.mark.parametrize("target,expected", [("lint", LINT), ("typecheck", TYPECHECK)])
def test_standalone_quality_targets_keep_all_checks(
    task_globals: dict[str, Any], target: str, expected: list[tuple[str, ...]]
) -> None:
    commands: list[tuple[str, ...]] = []

    def record(*args: str) -> None:
        commands.append(args)

    task_globals["run"] = record
    task_globals["main"](target)
    assert commands == expected


def test_check_deduplicates_mypy_and_preserves_each_distinct_gate(
    task_globals: dict[str, Any],
) -> None:
    commands: list[tuple[str, ...]] = []

    def record(*args: str) -> None:
        commands.append(args)

    task_globals["run"] = record
    task_globals["main"]("check")
    directory = task_globals["RUN_DIRECTORY"] / "mvp-501"
    coverage, observed = (
        str(directory / "coverage.json"),
        str(directory / "hypothesis-observed.json"),
    )
    assert commands == [
        *LINT,
        *[args for args in TYPECHECK if args != MYPY],
        ("docker", "compose", "up", "-d", "--wait", "db"),
        (
            "uv",
            "run",
            "--frozen",
            "python",
            "-m",
            "pytest",
            "apps/api/app/tests",
            "scripts/tests",
            "-p",
            "scripts.mvp_hypothesis_counter",
            "--cov",
            "--cov-config=docs/spec/mvp-coverage.ini",
            f"--cov-report=json:{coverage}",
            "--hypothesis-show-statistics",
            f"--mvp-coverage-json={coverage}",
            f"--mvp-hypothesis-output={observed}",
            "-p",
            "no:cacheprovider",
        ),
        (
            "uv",
            "run",
            "--frozen",
            "python",
            "scripts/verify_mvp_coverage.py",
            "--coverage",
            coverage,
            "--hypothesis-report",
            observed,
            "--output",
            str(directory / "coverage-property-gate.json"),
        ),
        ("pnpm", "--dir", "apps/web", "test"),
        ("pnpm", "--dir", "apps/web", "e2e", "health.spec.ts"),
        (
            "uv",
            "run",
            "--frozen",
            "python",
            "scripts/w1_browser_acceptance.py",
            "--run",
            "--mode",
            "all",
            "--api-port",
            "18047",
            "--web-port",
            "15179",
            "--output",
            str(ROOT / "output/playwright" / ("current-check-" + task_globals["RUN_ID"])),
        ),
    ]
    assert commands.count(MYPY) == 1


def test_fast_check_keeps_all_static_gates_without_launching_services(
    task_globals: dict[str, Any],
) -> None:
    commands: list[tuple[str, ...]] = []

    def record(*args: str) -> None:
        commands.append(args)

    task_globals["run"] = record
    task_globals["main"]("fast-check")
    assert commands == [*LINT, *[args for args in TYPECHECK if args != MYPY]]


def test_completed_fast_checks_are_not_reused_across_invocations(
    task_globals: dict[str, Any],
) -> None:
    commands: list[tuple[str, ...]] = []

    def record(*args: str) -> None:
        commands.append(args)

    task_globals["run"] = record
    # Each call must collect the static gates again; acceptance groups deliberately
    # cannot be registered twice under the same native check run identity.
    task_globals["quality_checks"]("lint", "typecheck")
    task_globals["quality_checks"]("lint", "typecheck")
    assert commands.count(MYPY) == 2
    assert len(commands) == 2 * len([*LINT, *[args for args in TYPECHECK if args != MYPY]])


def test_failed_fast_gate_stops_before_contract_tests_and_services(
    task_globals: dict[str, Any],
) -> None:
    commands: list[tuple[str, ...]] = []

    def reject_type_error(*args: str) -> None:
        commands.append(args)
        if args == MYPY:
            raise subprocess.CalledProcessError(1, args)

    task_globals["run"] = reject_type_error
    with pytest.raises(subprocess.CalledProcessError):
        task_globals["main"]("check")
    assert commands == LINT[:3]


def test_coverage_gate_failure_stops_before_frontend_and_business_browser(
    task_globals: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[str, ...]] = []
    measured_data_files: list[str | None] = []
    monkeypatch.setenv("COVERAGE_FILE", "previous-unrelated-coverage")

    def reject_low_coverage(*args: str) -> None:
        commands.append(args)
        if "pytest" in args:
            measured_data_files.append(os.environ.get("COVERAGE_FILE"))
        if "scripts/verify_mvp_coverage.py" in args:
            raise subprocess.CalledProcessError(1, args)

    task_globals["run"] = reject_low_coverage
    with pytest.raises(subprocess.CalledProcessError):
        task_globals["main"]("check")
    assert measured_data_files == [str(task_globals["RUN_DIRECTORY"] / "mvp-501/.coverage")]
    assert os.environ["COVERAGE_FILE"] == "previous-unrelated-coverage"
    assert commands[-1][4] == "scripts/verify_mvp_coverage.py"
    assert not any(
        args[:1] == ("pnpm",) and args[-1] in {"test", "health.spec.ts"} for args in commands
    )


def test_financial_property_selection_registers_all_21_given_nodes() -> None:
    observer = runpy.run_path(str(ROOT / "scripts/verify_mvp_coverage.py"))
    scope = json.loads((ROOT / "docs/spec/mvp-coverage-scope.json").read_text(encoding="utf-8"))
    expected = observer["property_nodes"](ROOT, scope)
    for node_id in expected:
        path, function = node_id.split("::")
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        node = next(
            item
            for item in tree.body
            if isinstance(item, ast.FunctionDef) and item.name == function
        )
        assert "pytest.mark.property" in [ast.unparse(item) for item in node.decorator_list], (
            node_id
        )
    assert len(expected) == 21
