"""Benchmark safety gates and honest evidence handling without touching PostgreSQL."""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "w0_read_benchmark", ROOT / "scripts/benchmark_reads.py"
)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


@pytest.mark.parametrize(
    "name",
    [
        "bounded_funds",
        "postgres",
        "bf_test_",
        "bf_test_" + "a" * 31,
        "bf_test_" + "a" * 32 + '"; DROP DATABASE bounded_funds; --',
    ],
)
def test_cleanup_database_identifier_rejects_formal_and_injected_names(name: str) -> None:
    with pytest.raises(ValueError, match="generated"):
        benchmark.require_database_name(name)


def test_evidence_paths_cannot_escape_w0_or_overwrite_older_evidence() -> None:
    allowed = ROOT / "docs/progress/evidence/W0/new-run/result.json"
    assert benchmark.require_output(allowed) == allowed.resolve()
    for path in (
        ROOT / "docs/progress/evidence/MVP-401/result.json",
        ROOT / "docs/progress/evidence/W0/../MVP-401/result.json",
        ROOT / "docs/progress/evidence/W0",
    ):
        with pytest.raises(ValueError, match="strictly beneath"):
            benchmark.require_output(path)


def test_success_requires_valid_audit_and_identical_baseline_response() -> None:
    result: dict[str, Any] = {"audit": {"status": "VALID"}, "simulated_cents": 123}
    expected = benchmark.digest(result)
    assert benchmark.checked_read(lambda: result, expected) == expected
    changed = {**result, "simulated_cents": 124}
    with pytest.raises(AssertionError, match="differs from baseline"):
        benchmark.checked_read(lambda: changed, expected)
    for status in ("LEGACY_UNAUDITED", "INTEGRITY_ERROR", "UNKNOWN"):
        with pytest.raises(AssertionError, match="Dashboard audit"):
            benchmark.checked_read(lambda current=status: {"audit": {"status": current}})


def test_instrumentation_restores_existing_profile_hooks_after_failure() -> None:
    import sys
    import threading

    before = sys.getprofile(), threading.getprofile()
    with pytest.raises(RuntimeError, match="diagnostic failed"):
        with benchmark.call_counts():
            raise RuntimeError("diagnostic failed")
    assert (sys.getprofile(), threading.getprofile()) == before


def test_call_counts_include_a_worker_that_existed_before_the_diagnostic_pass() -> None:
    import threading

    from pydantic import BaseModel

    class Example(BaseModel):
        cents: int

    ready, release, finished = threading.Event(), threading.Event(), threading.Event()

    def existing_worker() -> None:
        ready.set()
        release.wait(2)
        Example(cents=123)
        finished.set()

    thread = threading.Thread(target=existing_worker)
    thread.start()
    assert ready.wait(2)
    try:
        with benchmark.call_counts() as measured:
            release.set()
            assert finished.wait(2)
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert measured["python_call_counts"]["pydantic.main.__init__"] >= 1


def test_normal_wall_stops_before_response_decode_validation_and_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []
    clocks = iter((10.0, 12.0))
    result = {"audit": {"status": "VALID"}, "simulated_cents": 123}

    def clock() -> float:
        events.append("clock")
        return next(clocks)

    def operation() -> str:
        events.append("operation")
        return "encoded HTTP response"

    def decode(_response: Any) -> dict[str, Any]:
        events.append("decode")
        return result

    monkeypatch.setattr(benchmark.time, "perf_counter", clock)
    wall, response_hash = benchmark.timed_read(operation, decode)
    assert wall == 2.0
    assert response_hash == benchmark.digest(result)
    assert events == ["clock", "operation", "clock", "decode"]


def test_own_process_memory_is_measured_with_an_explicit_lifetime_scope() -> None:
    import os

    result = benchmark.process_memory()
    assert "lifetime peak" in result["scope"]
    assert "PostgreSQL" in result["scope"]
    if os.name == "nt":
        assert result["status"] == "MEASURED", result
        assert result["lifetime_peak_working_set_bytes"] >= result["working_set_bytes"] > 0
        assert result["pid"] == os.getpid()
    else:
        assert result["status"] == "UNAVAILABLE"


def test_expansion_requests_use_existing_seed_clearing(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from alembic import command
    from app.services import execution, external_bank_facts
    from app.tests import dashboard_scenario

    facts: list[Any] = []
    decisions: list[Any] = []
    writes: list[Any] = []

    class Flow:
        def __init__(self, _engine: Any) -> None:
            self.now = datetime(2026, 10, 4, 1, tzinfo=UTC)
            self.identities = {"cash": uuid4(), "asset_policy": uuid4()}

        def initialize(self) -> None:
            pass

    def fact(_engine: Any, _user: Any, request: Any, now: Any) -> Any:
        # seed_demo opens precisely payroll and merchant. An arbitrary new
        # counterparty has no opening and must be rejected by the real bank.
        assert request.counterparty_ref in {"payroll", "merchant"}
        assert request.occurred_at == now and request.amount_cents == 100
        facts.append(request)
        return SimpleNamespace(
            bank_status="SETTLED", projection_status="PROJECTED", model_dump=lambda **_: {}
        )

    def decision(_engine: Any, _user: Any, request: Any, _now: Any) -> Any:
        decisions.append(request)
        return SimpleNamespace(model_dump=lambda **_: {"status": "PLANNED"})

    monkeypatch.setattr(command, "upgrade", lambda *_: None)
    monkeypatch.setattr(dashboard_scenario, "DashboardScenario", Flow)
    monkeypatch.setattr(dashboard_scenario, "STAGES", ("initial",))
    monkeypatch.setattr(external_bank_facts, "ingest_external_fact", fact)
    monkeypatch.setattr(execution, "prepare_action", decision)
    monkeypatch.setattr(benchmark, "write_json", lambda path, value: writes.append((path, value)))
    engine = SimpleNamespace(url=SimpleNamespace(render_as_string=lambda *_: "postgresql://unused"))
    result = benchmark.build_fixture(
        engine, "expanded-fixed-history", 3, ROOT / "docs/progress/evidence/W0/not-written-test"
    )
    assert len(facts) == 6 and len(decisions) == 3
    assert (
        sum(request.amount_cents * (1 if request.kind == "INCOME" else -1) for request in facts)
        == 0
    )
    assert all(request.intent.kind == "purchase_asset" for request in decisions)
    assert len({request.idempotency_key for request in facts + decisions}) == 9
    assert result["handcrafted_audit_or_result_writes"] is False
    assert writes
