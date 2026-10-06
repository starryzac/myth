"""Observe original Hypothesis pytest statistics; never modify financial calls."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Generator
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from scripts.verify_mvp_coverage import (
    ROOT,
    SCOPE_PATH,
    property_nodes,
    sha256,
    source_hashes,
    validate_scope,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("mvp-501-observed-evidence")
    group.addoption("--mvp-hypothesis-output", type=Path, default=None)
    group.addoption("--mvp-coverage-json", type=Path, default=None)
    group.addoption("--mvp-coverage-scope", type=Path, default=SCOPE_PATH)


def pytest_configure(config: pytest.Config) -> None:
    output: Path | None = config.getoption("--mvp-hypothesis-output")
    if output is None:
        return
    if output.exists():
        raise pytest.UsageError(f"Evidence output already exists; preserve original: {output}")
    if not config.getoption("hypothesis_show_statistics", default=False):
        raise pytest.UsageError("Observed evidence requires --hypothesis-show-statistics")
    config.pluginmanager.register(ObservedStatistics(config, output), "mvp-observed-statistics")


class ObservedStatistics:
    def __init__(self, config: pytest.Config, output: Path) -> None:
        self.output = output
        self.scope_path: Path = config.getoption("--mvp-coverage-scope")
        self.coverage_path: Path | None = config.getoption("--mvp-coverage-json")
        self.coverage_config: str = config.getoption("cov_config", default="")
        self.coverage_append = config.getoption("cov_append", default=False)
        if self.coverage_append:
            raise pytest.UsageError("Observed same-run coverage rejects effective --cov-append")
        if self.coverage_path is not None and self.coverage_path.exists():
            raise pytest.UsageError("Preserve prior coverage JSON; use a fresh evidence path")
        self.scope = json.loads(self.scope_path.read_text(encoding="utf-8-sig"))
        validate_scope(self.scope)
        self.bindings_before = self.bindings()
        self.expected = set(property_nodes(ROOT, self.scope))
        self.records: list[dict[str, Any]] = []
        self.outcomes: dict[str, str] = {}
        self.started_ns = time.time_ns()
        self.args = list(config.invocation_params.args)
        self.before = source_hashes(ROOT, self.scope)

    def bindings(self) -> dict[str, str]:
        return {
            "scope_sha256": sha256(self.scope_path),
            "counter_sha256": sha256(Path(__file__)),
            "verifier_sha256": sha256(ROOT / "scripts/verify_mvp_coverage.py"),
            "coverage_config_sha256": sha256(ROOT / self.coverage_config)
            if self.coverage_config and (ROOT / self.coverage_config).is_file()
            else "",
        }

    @pytest.hookimpl(hookwrapper=True, trylast=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[Any]
    ) -> Generator[None, Any, None]:
        outcome = yield
        report: pytest.TestReport = outcome.get_result()
        node = item.nodeid.replace("\\", "/")
        if node not in self.expected:
            return
        if call.when == "call":
            self.outcomes[node] = report.outcome
        if call.when == "teardown":
            raw = getattr(item, "hypothesis_statistics", None)
            if isinstance(raw, str):
                self.records.append(
                    {
                        "nodeid": node,
                        "call_outcome": self.outcomes.get(node),
                        "raw_statistics": raw,
                    }
                )

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        coverage_hash: str | None = None
        if self.coverage_path is not None:
            report_paths = [
                Path(arg.split("json:", 1)[1]).resolve()
                for arg in self.args
                if arg.startswith("--cov-report=json:")
            ]
            if (
                self.coverage_path.resolve() in report_paths
                and any(arg == "--cov" or arg.startswith("--cov=") for arg in self.args)
                and self.coverage_path.is_file()
                and self.coverage_path.stat().st_mtime_ns >= self.started_ns
            ):
                coverage_hash = sha256(self.coverage_path)
        report = {
            "schema_version": 1,
            "evidence_kind": "OBSERVED_PYTEST_HYPOTHESIS_RUNTIME",
            "hypothesis_version": version("hypothesis"),
            "pytest_version": version("pytest"),
            "pytest_exitstatus": int(exitstatus),
            "pytest_argv": self.args,
            "coverage_append": self.coverage_append,
            "coverage_data_file": os.environ.get("COVERAGE_FILE", ".coverage"),
            "bindings_before": self.bindings_before,
            "bindings_after": self.bindings(),
            "started_time_ns": self.started_ns,
            "finished_time_ns": time.time_ns(),
            "scope_sha256": sha256(self.scope_path),
            "counter_sha256": sha256(Path(__file__)),
            "verifier_sha256": sha256(ROOT / "scripts/verify_mvp_coverage.py"),
            "source_hashes_before": self.before,
            "source_hashes_after": source_hashes(ROOT, self.scope),
            "coverage_json_sha256": coverage_hash,
            "coverage_config_path": self.coverage_config.replace("\\", "/"),
            "coverage_config_sha256": sha256(ROOT / self.coverage_config)
            if self.coverage_config and (ROOT / self.coverage_config).is_file()
            else None,
            "expected_financial_nodes": sorted(self.expected),
            "records": self.records,
            "semantics": self.scope["hypothesis"]["semantics"],
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
