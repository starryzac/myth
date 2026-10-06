"""Verify the public quality orchestration without launching services or test suites."""

import io
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
        with (
            io.TextIOWrapper(io.BytesIO(), encoding="utf-8") as stdout,
            io.TextIOWrapper(io.BytesIO(), encoding="utf-8") as stderr,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            namespace = runpy.run_path(str(ROOT / "scripts/tasks.py"))
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
    assert commands == [
        *LINT,
        *[args for args in TYPECHECK if args != MYPY],
        ("docker", "compose", "up", "-d", "--wait", "db"),
        ("uv", "run", "--frozen", "pytest", "--cov"),
        ("pnpm", "--dir", "apps/web", "test"),
        ("pnpm", "--dir", "apps/web", "e2e"),
    ]
    assert commands.count(MYPY) == 1


def test_completed_fast_checks_are_not_reused_across_invocations(
    task_globals: dict[str, Any],
) -> None:
    commands: list[tuple[str, ...]] = []

    def record(*args: str) -> None:
        commands.append(args)

    task_globals["run"] = record
    task_globals["main"]("check")
    task_globals["main"]("check")
    assert commands.count(MYPY) == 2


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
