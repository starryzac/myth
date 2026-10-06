"""W0 same-data PostgreSQL read benchmark; never reset the formal demo database.

Run ``--phase baseline --source-root <frozen apps/api>`` once, then run
``--phase candidate --manifest <printed manifest>`` against those same databases.
Both phases preserve raw failures. Cleanup is separate and accepts only the
exact generated disposable database names recorded in that manifest.
Latency, SQL/call counters, CPU profiling and Python allocation profiling are
separate passes. Instrumented numbers are diagnostics, never a product SLA.
"""

from __future__ import annotations

import argparse
import cProfile
import gzip
import hashlib
import io
import json
import os
import platform
import pstats
import re
import statistics
import subprocess
import sys
import threading
import time
import tracemalloc
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "W0_SAME_DATA_SIMULATED_POSTGRESQL_READ_BENCHMARK"
SCENARIOS = ("short-seeded", "mvp401-long-chain", "expanded-fixed-history")


def utc() -> str:
    return datetime.now(UTC).isoformat()


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value) + b"\n")


def require_database_name(name: str) -> str:
    if re.fullmatch(r"bf_test_[0-9a-f]{32}", name) is None:
        raise ValueError("Only an exact generated bf_test_<32 hex> database is permitted")
    return name


def require_output(path: Path) -> Path:
    resolved = path.resolve()
    allowed = (ROOT / "docs/progress/evidence/W0").resolve()
    if allowed not in resolved.parents:
        raise ValueError("Evidence output must be strictly beneath docs/progress/evidence/W0")
    return resolved


def git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def source_state(source_root: Path) -> dict[str, Any]:
    files = {}
    for path in sorted((source_root / "app").rglob("*.py")):
        files[path.relative_to(source_root).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    locks = {}
    for name in ("uv.lock", "pnpm-lock.yaml", "pyproject.toml", "package.json", "alembic.ini"):
        path = ROOT / name
        if path.is_file():
            locks[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "git_head": git("rev-parse", "HEAD"),
        "dirty_tree": git("status", "--porcelain=v1", "--untracked-files=normal"),
        "loaded_source_root": str(source_root),
        "source_sha256": files,
        "source_fingerprint": digest(files),
        "dependency_lock_sha256": locks,
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def hardware() -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "cpu_clock_control": "NOT_CONTROLLED",
        "process_affinity": "NOT_CONTROLLED",
        "server_cache": "UNKNOWN_NOT_FLUSHED",
        "background_load": "NOT_CONTROLLED",
    }
    if os.name == "nt":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "$p=Get-CimInstance Win32_Processor; $m=Get-CimInstance Win32_ComputerSystem; "
                "@{processor=$p.Name; cores=$p.NumberOfCores; "
                "logical=$p.NumberOfLogicalProcessors; memory_bytes=$m.TotalPhysicalMemory} "
                "| ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        info["windows_inventory_exit_code"] = result.returncode
        if result.returncode == 0:
            info["windows_inventory"] = json.loads(result.stdout)
    return info


def process_memory() -> dict[str, Any]:
    """Read this worker's OS high-water mark without running or profiling a request."""
    scope = (
        "Worker process lifetime peak; includes imports, fixture setup, snapshots, normal reads "
        "and profiling. Cannot isolate one request or compare request-local native peaks. "
        "Excludes the separate PostgreSQL server process."
    )
    if os.name != "nt":
        return {"status": "UNAVAILABLE", "scope": scope, "reason": "Windows API only"}
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    memory = ProcessMemoryCounters()
    memory.cb = ctypes.sizeof(memory)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(memory), memory.cb):
        return {"status": "UNAVAILABLE", "scope": scope, "win_error": ctypes.get_last_error()}
    return {
        "status": "MEASURED",
        "observed_at": utc(),
        "pid": os.getpid(),
        "scope": scope,
        "source": "GetProcessMemoryInfo(PROCESS_MEMORY_COUNTERS)",
        "working_set_bytes": int(memory.WorkingSetSize),
        "lifetime_peak_working_set_bytes": int(memory.PeakWorkingSetSize),
        "pagefile_usage_bytes": int(memory.PagefileUsage),
        "lifetime_peak_pagefile_usage_bytes": int(memory.PeakPagefileUsage),
    }


def database_url(name: str) -> Any:
    from app.db.settings import DatabaseSettings
    from sqlalchemy.engine import make_url

    return make_url(DatabaseSettings().database_url).set(database=require_database_name(name))


def create_isolated_database(name: str) -> None:
    from app.db.session import create_database_engine

    url = database_url(name)
    administration = create_database_engine(url.set(database="postgres").render_as_string(False))
    try:
        with administration.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{require_database_name(name)}"')
    finally:
        administration.dispose()


def cleanup_database(name: str) -> None:
    from app.db.session import create_database_engine

    url = database_url(name)
    administration = create_database_engine(url.set(database="postgres").render_as_string(False))
    try:
        with administration.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{require_database_name(name)}"')
    finally:
        administration.dispose()


def snapshot(engine: Any, path: Path) -> dict[str, Any]:
    from app.db import models
    from app.db.base import Base
    from sqlalchemy import inspect, select

    tables: dict[str, Any] = {}
    summaries: dict[str, Any] = {}
    # A new read-only worker must explicitly register its ORM models before
    # hashing. Otherwise Base can be empty, creating a false zero-row proof.
    if models.User.__table__ not in Base.metadata.sorted_tables or len(Base.metadata.tables) != 23:
        raise AssertionError("Expected all 23 current simulation ORM tables")
    physical = set(inspect(engine).get_table_names(schema="public"))
    expected = set(Base.metadata.tables) | {"alembic_version"}
    if physical != expected:
        raise AssertionError(f"Uncovered physical tables: {sorted(physical ^ expected)}")
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        for table in Base.metadata.sorted_tables:
            rows = [
                dict(row)
                for row in connection.execute(select(table).order_by(table.c.id)).mappings()
            ]
            tables[table.name] = rows
            summaries[table.name] = {
                "rows": len(rows),
                "snapshot_json_bytes": len(encoded(rows)),
                "sha256": digest(rows),
            }
    payload = encoded(tables)
    with gzip.open(path, "wb") as output:
        output.write(payload)
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "snapshot_json_bytes": len(payload),
        "total_rows": sum(item["rows"] for item in summaries.values()),
        "tables": summaries,
        "physical_tables": sorted(physical),
        "non_model_metadata_tables": ["alembic_version"],
        "metadata_coverage": "Migration head separately hashed and compared in database_metadata",
        "raw_snapshot": path.name,
    }


def database_metadata(engine: Any) -> dict[str, Any]:
    from sqlalchemy import text

    with engine.connect() as connection:
        settings = (
            connection.execute(
                text(
                    "SELECT version() AS version, current_database() AS database, "
                    "current_setting('shared_buffers') AS shared_buffers, "
                    "current_setting('work_mem') AS work_mem, "
                    "current_setting('max_connections') AS max_connections"
                )
            )
            .mappings()
            .one()
        )
        original_bytes = connection.execute(
            text(
                "SELECT (SELECT coalesce(sum(octet_length(canonical_text)),0) FROM audit_events) "
                "+ (SELECT coalesce(sum(octet_length(canonical_text)),0) "
                "FROM audit_subject_snapshots) "
                "+ (SELECT coalesce(sum(octet_length(seal_canonical_text)),0) FROM audit_epochs)"
            )
        ).scalar_one()
        locks = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT locktype, mode, granted, count(*) AS count FROM pg_locks "
                    "WHERE database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                    "GROUP BY locktype, mode, granted ORDER BY locktype, mode, granted"
                )
            ).mappings()
        ]
        migrations = (
            connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num"))
            .scalars()
            .all()
        )
    return {
        "runtime": dict(settings),
        "migration_heads": migrations,
        "migration_heads_sha256": digest(migrations),
        "stored_original_canonical_bytes": original_bytes,
        "database_locks": locks,
    }


def build_fixture(engine: Any, scenario: str, rounds: int, folder: Path) -> dict[str, Any]:
    from alembic import command
    from alembic.config import Config
    from app.domain.demo_identity import DEMO_USER_ID
    from app.domain.external_bank_fact_types import ExternalFactRequest
    from app.services.action_contracts import PrepareActionRequest, PurchaseIntent
    from app.services.execution import prepare_action
    from app.services.external_bank_facts import ingest_external_fact
    from app.tests.dashboard_scenario import STAGES, DashboardScenario

    phases = []
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", engine.url.render_as_string(False).replace("%", "%%"))
    started = time.perf_counter()
    command.upgrade(config, "head")
    phases.append({"phase": "migration", "wall_seconds": time.perf_counter() - started})
    flow = DashboardScenario(engine)
    started = time.perf_counter()
    flow.initialize()
    phases.append({"phase": "seed", "wall_seconds": time.perf_counter() - started})
    if scenario != "short-seeded":
        for stage in STAGES[1:]:
            started = time.perf_counter()
            result = flow.advance(stage)
            phases.append(
                {
                    "phase": stage,
                    "wall_seconds": time.perf_counter() - started,
                    "result": result,
                }
            )
            write_json(folder / "fixture-phases.json", phases)
    if scenario == "expanded-fixed-history":
        for number in range(rounds):
            for kind in ("INCOME", "CONSUMPTION"):
                flow.now += timedelta(seconds=10)
                started = time.perf_counter()
                result = ingest_external_fact(
                    engine,
                    DEMO_USER_ID,
                    ExternalFactRequest(
                        user_id=DEMO_USER_ID,
                        idempotency_key=f"w0-expanded-{number}-{kind}",
                        external_ref=f"w0-expanded-{number}-{kind}",
                        kind=kind,
                        account_id=flow.identities["cash"],
                        amount_cents=100,
                        counterparty_ref="w0-fixed-simulated-fixture",
                        occurred_at=flow.now,
                    ),
                    flow.now,
                )
                if result.bank_status != "SETTLED" or result.projection_status != "PROJECTED":
                    raise AssertionError("Legitimate expanded bank fact failed to settle/project")
                phases.append(
                    {
                        "phase": f"expanded-{number}-{kind}",
                        "wall_seconds": time.perf_counter() - started,
                        "result": result.model_dump(mode="json"),
                    }
                )
            flow.now += timedelta(seconds=10)
            started = time.perf_counter()
            result = prepare_action(
                engine,
                DEMO_USER_ID,
                PrepareActionRequest(
                    idempotency_key=f"w0-expanded-decision-{number}",
                    intent=PurchaseIntent(
                        kind="purchase_asset", policy_id=flow.identities["asset_policy"]
                    ),
                ),
                flow.now,
            )
            phases.append(
                {
                    "phase": f"expanded-{number}-actual-decision",
                    "wall_seconds": time.perf_counter() - started,
                    "result": result.model_dump(mode="json"),
                }
            )
            write_json(folder / "fixture-phases.json", phases)
    write_json(folder / "fixture-phases.json", phases)
    return {
        "scenario": scenario,
        "as_of": flow.now.isoformat(),
        "expanded_rounds": rounds if scenario == "expanded-fixed-history" else 0,
        "identities": {name: str(value) for name, value in flow.identities.items()},
        "fixture_origin": "DashboardScenario/STAGES + actual ingest_external_fact/prepare_action",
        "handcrafted_audit_or_result_writes": False,
    }


def service_read(engine: Any, now: datetime) -> dict[str, Any]:
    from app.domain.demo_identity import DEMO_USER_ID
    from app.services.dashboard import get_dashboard
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(engine) as session, session.begin():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        return get_dashboard(session, DEMO_USER_ID, now).model_dump(mode="json")


@contextmanager
def sql_counts(engine: Any) -> Iterator[dict[str, Any]]:
    from sqlalchemy import event

    records: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {"queries": records}

    def before(
        _connection: Any, _cursor: Any, statement: str, _parameters: Any, context: Any, _many: bool
    ) -> None:
        context._w0_query_started = time.perf_counter()

    def after(
        _connection: Any, cursor: Any, statement: str, _parameters: Any, context: Any, _many: bool
    ) -> None:
        records.append(
            {
                "statement": statement,
                "wall_seconds": time.perf_counter() - context._w0_query_started,
                "driver_rowcount": cursor.rowcount,
            }
        )

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        yield metrics
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
        metrics.update(
            {
                "query_count": len(records),
                "sql_wall_seconds": sum(q["wall_seconds"] for q in records),
                "known_driver_rows": sum(max(0, q["driver_rowcount"]) for q in records),
                "unknown_driver_rowcount_queries": sum(q["driver_rowcount"] < 0 for q in records),
            }
        )


@contextmanager
def call_counts() -> Iterator[dict[str, Any]]:
    counts: Counter[str] = Counter()
    lock = threading.Lock()
    metrics: dict[str, Any] = {}
    previous, previous_thread = sys.getprofile(), threading.getprofile()

    def profile(frame: Any, event: str, _arg: Any) -> None:
        if event != "call":
            return
        name = frame.f_code.co_name
        module = frame.f_globals.get("__name__", "")
        selected = (
            module.startswith("app.domain.audit_chain")
            and (
                name.startswith("canonical")
                or name.endswith("canonical_text")
                or name.startswith("parse_")
                or name.startswith("verify_frozen")
            )
            or module.startswith("app.services.")
            and name
            in {
                "verify_audit_chain",
                "validate_bank_projection",
                "ledger_heads",
                "read_income_state",
                "_verify",
                "_validated_subject_bytes",
            }
            or module == "pydantic.main"
            and name
            in {
                "__init__",
                "model_validate",
                "model_validate_json",
                "model_validate_strings",
            }
        )
        if selected:
            with lock:
                counts[f"{module}.{name}"] += 1

    # TestClient reuses AnyIO workers created during uninstrumented wall reads.
    # Python 3.12's all-thread hook measures those workers as well as new ones.
    threading.setprofile_all_threads(profile)
    try:
        yield metrics
    finally:
        threading.setprofile_all_threads(previous_thread)
        sys.setprofile(previous)
        metrics["python_call_counts"] = dict(sorted(counts.items()))
        metrics["pydantic_python_entry_calls"] = sum(
            value for name, value in counts.items() if name.startswith("pydantic.main.")
        )
        metrics["model_count_boundary"] = (
            "Python BaseModel __init__/model_validate* calls only; nested C validator "
            "construction is not independently counted"
        )


def checked_document(result: dict[str, Any], expected_hash: str | None = None) -> str:
    if result["audit"]["status"] != "VALID":
        raise AssertionError(f"Dashboard audit is {result['audit']['status']}")
    actual = digest(result)
    if expected_hash is not None and actual != expected_hash:
        raise AssertionError("Read response differs from baseline on identical data and clock")
    return actual


def checked_read(operation: Callable[[], dict[str, Any]], expected_hash: str | None = None) -> str:
    return checked_document(operation(), expected_hash)


def timed_read(
    operation: Callable[[], Any],
    normalize: Callable[[Any], dict[str, Any]],
    expected_hash: str | None = None,
) -> tuple[float, str]:
    started = time.perf_counter()
    result = operation()
    wall = time.perf_counter() - started
    # Decode, validate and hash only after the uninstrumented request returned.
    return wall, checked_document(normalize(result), expected_hash)


def measure(
    engine: Any,
    fixture: dict[str, Any],
    folder: Path,
    repeats: int,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    from app.api.dependencies import get_engine, get_now
    from app.main import create_app
    from fastapi.testclient import TestClient

    now = datetime.fromisoformat(fixture["as_of"])
    api = create_app()
    api.dependency_overrides[get_engine] = lambda: engine
    api.dependency_overrides[get_now] = lambda: now
    readings: dict[str, Any] = {}
    with TestClient(api) as client:

        def api_read() -> Any:
            return client.get("/api/v1/dashboard")

        def api_document(response: Any) -> dict[str, Any]:
            if response.status_code != 200:
                raise AssertionError(
                    f"Dashboard API status {response.status_code}: {response.text}"
                )
            return response.json()

        operations = {
            "api-get": (api_read, api_document),
            "repeatable-read-service": (lambda: service_read(engine, now), lambda result: result),
        }
        for name, (operation, normalize) in operations.items():
            expected = baseline["readings"][name]["response_sha256"] if baseline else None
            rows = []
            for number in range(repeats):
                started_at = utc()
                wall, actual = timed_read(operation, normalize, expected)
                rows.append(
                    {
                        "repeat": number + 1,
                        "started_at": started_at,
                        "wall_seconds": wall,
                        "cache_state": "FIRST_PATH_READ_DB_CACHE_UNKNOWN"
                        if number == 0
                        else "REPEATED_PROCESS_READ_DB_CACHE_UNKNOWN",
                    }
                )
                expected = actual
                write_json(folder / f"{name}-wall.json", rows)
            with sql_counts(engine) as queries, call_counts() as functions:
                started = time.perf_counter()
                result = operation()
                instrumented_wall = time.perf_counter() - started
            checked_document(normalize(result), expected)
            write_json(
                folder / f"{name}-instrumented.json",
                {
                    "purpose": "SEPARATE_INSTRUMENTED_DIAGNOSTIC_PASS_NOT_LATENCY_SLA",
                    "wall_seconds": instrumented_wall,
                    "sql": queries,
                    "functions": functions,
                },
            )
            readings[name] = {
                "normal_wall_samples": rows,
                "normal_median_wall_seconds": statistics.median(
                    row["wall_seconds"] for row in rows
                ),
                "response_sha256": expected,
                "query_count": queries["query_count"],
                "sql_wall_seconds": queries["sql_wall_seconds"],
                "functions": functions,
                "known_driver_rows": queries["known_driver_rows"],
                "normal_wall_scope": (
                    "TestClient GET through response received; excludes client JSON decode/hash"
                    if name == "api-get"
                    else "Read-only RR session/service plus product model_dump "
                    "response serialization; "
                    "excludes evidence hashing"
                ),
            }
    if (
        readings["api-get"]["response_sha256"]
        != readings["repeatable-read-service"]["response_sha256"]
    ):
        raise AssertionError("Service and API responses differ on the same data, source and clock")

    # CPU and allocation profiles use separate requests with fresh read-only sessions.
    def operation() -> dict[str, Any]:
        return service_read(engine, now)

    expected = readings["repeatable-read-service"]["response_sha256"]
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    try:
        result = operation()
    finally:
        profiler.disable()
    cpu_wall = time.perf_counter() - started
    checked_document(result, expected)
    profiler.dump_stats(str(folder / "service-cpu.pstats"))
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.sort_stats(pstats.SortKey.CUMULATIVE).print_stats(100)
    stats.sort_stats(pstats.SortKey.TIME).print_stats(100)
    (folder / "service-cpu.txt").write_text(stream.getvalue(), encoding="utf-8")
    tracemalloc.start()
    started = time.perf_counter()
    try:
        result = operation()
        memory_wall = time.perf_counter() - started
        current, peak = tracemalloc.get_traced_memory()
        memory_snapshot = tracemalloc.take_snapshot()
        memory_snapshot.dump(str(folder / "service-python-allocations.tracemalloc"))
    finally:
        tracemalloc.stop()
    checked_document(result, expected)
    return {
        "readings": readings,
        "audit_status": "VALID",
        "profiling": {
            "cpu_service_wall_seconds": cpu_wall,
            "cpu_scope": "caller-thread whole service request",
            "memory_service_wall_seconds": memory_wall,
            "python_current_allocated_bytes": current,
            "python_peak_allocated_bytes": peak,
            "memory_scope": "tracemalloc Python allocations; excludes native/DB-server allocations",
            "latency_sla": "NOT_CLAIMED; profiles and counters are independent diagnostic passes",
        },
    }


def worker(args: argparse.Namespace) -> int:
    from app.db.session import create_database_engine

    folder = require_output(Path(args.folder))
    folder.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {"purpose": PURPOSE, "started_at": utc(), "status": "INCOMPLETE"}
    result["source_before"] = source_state(Path(args.source_root).resolve())
    result["hardware"] = hardware()
    engine = None
    try:
        if args.create:
            create_isolated_database(args.database)
        engine = create_database_engine(database_url(args.database).render_as_string(False))
        fixture = (
            build_fixture(engine, args.scenario, args.expanded_rounds, folder)
            if args.create
            else json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        )
        write_json(folder / "fixture.json", fixture)
        result["fixture"] = fixture
        result["data_before"] = snapshot(engine, folder / "snapshot-before.json.gz")
        result["database_before"] = database_metadata(engine)
        result["process_memory_before_reads"] = process_memory()
        write_json(folder / "result.json", result)
        baseline = (
            json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
        )
        if baseline and result["data_before"]["sha256"] != baseline["data_before"]["sha256"]:
            raise AssertionError("Scenario database differs from retained baseline data")
        if baseline and (
            result["database_before"]["migration_heads_sha256"]
            != baseline["database_before"]["migration_heads_sha256"]
        ):
            raise AssertionError("Scenario migration metadata differs from retained baseline")
        result.update(measure(engine, fixture, folder, args.repeats, baseline))
        result["data_after"] = snapshot(engine, folder / "snapshot-after.json.gz")
        result["database_after"] = database_metadata(engine)
        result["process_memory_after_reads"] = process_memory()
        result["source_after"] = source_state(Path(args.source_root).resolve())
        if result["data_before"]["sha256"] != result["data_after"]["sha256"]:
            raise AssertionError("Benchmark reads changed persisted simulation/audit history")
        if (
            result["database_before"]["migration_heads_sha256"]
            != result["database_after"]["migration_heads_sha256"]
        ):
            raise AssertionError("Benchmark reads changed migration metadata")
        result["dirty_tree_changed_during_measurement"] = (
            result["source_before"]["dirty_tree"] != result["source_after"]["dirty_tree"]
        )
        for key in ("source_fingerprint", "dependency_lock_sha256", "git_head", "tool_sha256"):
            if result["source_before"][key] != result["source_after"][key]:
                raise AssertionError(f"Loaded source/configuration drift during measurement: {key}")
        result["status"] = "PASSED"
        return 0
    except BaseException as error:
        result["status"] = "FAILED"
        result["error_type"] = type(error).__name__
        result["error"] = str(error)
        if engine is not None:
            try:
                result["data_after_failure"] = snapshot(
                    engine, folder / "snapshot-after-failure.json.gz"
                )
            except BaseException as snapshot_error:
                result["snapshot_after_failure_error_type"] = type(snapshot_error).__name__
        return 1
    finally:
        if engine is not None:
            engine.dispose()
        result["finished_at"] = utc()
        write_json(folder / "result.json", result)


def launch_worker(arguments: list[str], source_root: Path, folder: Path) -> int:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root)
    environment["PYTHONUTF8"] = "1"
    folder.parent.mkdir(parents=True, exist_ok=True)
    with (folder.parent / f"{folder.name}.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                "--source-root",
                str(source_root),
                "--folder",
                str(folder),
                *arguments,
            ],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        print(f"W0 worker {folder.name}; PID {process.pid}; raw log {log.name}", flush=True)
        return process.wait()


def coordinator(args: argparse.Namespace) -> int:
    if args.phase == "baseline":
        source_root = Path(args.source_root).resolve()
        if source_root == (ROOT / "apps/api").resolve():
            raise ValueError(
                "Baseline must load an immutable source snapshot, not the changing checkout"
            )
        folder = require_output(
            ROOT
            / "docs/progress/evidence/W0"
            / (
                "performance-"
                + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                + "-"
                + uuid4().hex[:8]
            )
        )
        folder.mkdir(parents=True, exist_ok=False)
        manifest_path = folder / "manifest.json"
        manifest: dict[str, Any] = {
            "purpose": PURPOSE,
            "started_at": utc(),
            "status": "INCOMPLETE",
            "baseline_source_root": str(source_root),
            "repeats": args.repeats,
            "expanded_rounds": args.expanded_rounds,
            "scenarios": [],
            "formal_demo_database_touched": False,
        }
        for name in SCENARIOS:
            manifest["scenarios"].append({"scenario": name, "database": f"bf_test_{uuid4().hex}"})
        write_json(manifest_path, manifest)
        print(f"W0 manifest: {manifest_path}", flush=True)
        for scenario in manifest["scenarios"]:
            out = folder / "baseline" / scenario["scenario"]
            code = launch_worker(
                [
                    "--create",
                    "--database",
                    scenario["database"],
                    "--scenario",
                    scenario["scenario"],
                    "--repeats",
                    str(args.repeats),
                    "--expanded-rounds",
                    str(args.expanded_rounds),
                ],
                source_root,
                out,
            )
            scenario["baseline"] = str(out / "result.json")
            scenario["baseline_exit_code"] = code
            write_json(manifest_path, manifest)
            if code:
                manifest["status"] = "BASELINE_FAILED"
                write_json(manifest_path, manifest)
                return code
        manifest["status"] = "BASELINE_PASSED_CANDIDATE_PENDING"
        write_json(manifest_path, manifest)
        return 0
    if not args.manifest:
        raise ValueError("candidate/cleanup requires the exact saved --manifest path")
    manifest_path = require_output(Path(args.manifest))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["purpose"] != PURPOSE:
        raise ValueError("Unrecognized benchmark manifest")
    if args.phase == "cleanup":
        for scenario in manifest["scenarios"]:
            cleanup_database(require_database_name(scenario["database"]))
        manifest["disposable_database_cleanup_at"] = utc()
        write_json(manifest_path, manifest)
        return 0
    if manifest["status"] not in {"BASELINE_PASSED_CANDIDATE_PENDING", "CANDIDATE_FAILED"}:
        raise ValueError(
            "Candidate requires a complete passed baseline and a fresh comparison directory"
        )
    source_root = (ROOT / "apps/api").resolve()
    candidate_id = (
        "candidate-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    comparisons = []
    for scenario in manifest["scenarios"]:
        baseline = Path(scenario["baseline"])
        out = manifest_path.parent / candidate_id / scenario["scenario"]
        code = launch_worker(
            [
                "--database",
                scenario["database"],
                "--scenario",
                scenario["scenario"],
                "--fixture",
                str(baseline.parent / "fixture.json"),
                "--baseline",
                str(baseline),
                "--repeats",
                str(manifest["repeats"]),
            ],
            source_root,
            out,
        )
        comparison: dict[str, Any] = {
            "scenario": scenario["scenario"],
            "candidate": str(out / "result.json"),
            "exit_code": code,
        }
        if code == 0:
            old = json.loads(baseline.read_text(encoding="utf-8"))
            new = json.loads((out / "result.json").read_text(encoding="utf-8"))
            comparison["same_data_sha256"] = old["data_before"]["sha256"]
            comparison["normal_median_wall_ratio_baseline_over_candidate"] = {
                name: old["readings"][name]["normal_median_wall_seconds"]
                / new["readings"][name]["normal_median_wall_seconds"]
                for name in old["readings"]
            }
        comparisons.append(comparison)
        write_json(manifest_path.parent / candidate_id / "comparison.json", comparisons)
        if code:
            manifest["status"] = "CANDIDATE_FAILED"
            manifest["latest_candidate"] = candidate_id
            write_json(manifest_path, manifest)
            return code
    manifest["status"] = "SAME_DATA_COMPARISON_PASSED"
    manifest["latest_candidate"] = candidate_id
    manifest["finished_at"] = utc()
    write_json(manifest_path, manifest)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("baseline", "candidate", "cleanup"), default="baseline")
    parser.add_argument("--source-root", default=str(ROOT / "apps/api"))
    parser.add_argument("--manifest")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--expanded-rounds", type=int, default=3)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--create", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--database", help=argparse.SUPPRESS)
    parser.add_argument("--scenario", choices=SCENARIOS, help=argparse.SUPPRESS)
    parser.add_argument("--folder", help=argparse.SUPPRESS)
    parser.add_argument("--fixture", help=argparse.SUPPRESS)
    parser.add_argument("--baseline", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (
        args.repeats < 1
        or args.repeats > 20
        or args.expanded_rounds < 1
        or args.expanded_rounds > 20
    ):
        parser.error("repeats and expanded-rounds must each be between 1 and 20")
    sys.path.insert(0, str(Path(args.source_root).resolve()))
    os.chdir(ROOT)
    return worker(args) if args.worker else coordinator(args)


if __name__ == "__main__":
    raise SystemExit(main())
