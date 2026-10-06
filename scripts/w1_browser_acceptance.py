"""Guarded real Edge acceptance; explicit --run, one disposable database, UI financial writes.

Preparation/--help never connects to PostgreSQL. Six business cases use independent UI resets;
the additional three-round case retains one database throughout. No formal database reset,
financial output injection, mock HTTP, or historical evidence overwrite is provided.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import runpy
import shutil
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "bounded-funds-scenario-v1"
OUTPUT = ROOT / "output/playwright"
KINDS = {"BEGIN", "READ_ONLY", "MONEY", "RESET"}
BUSINESS_TITLES = (
    "工资到账至真实目标分配与自动申购",
    "自然语言新目标修订确认、零归属建立与后续新收入授权分配",
    "大额消费触发合法自动无损赎回",
    "定存损失单独询问并绑定原报价回执",
    "修改房租后绑定旧版本的原动作失效",
    "所选真实原动作审计链VALID且浏览零写",
)
ROUND_TITLE = "同一隔离库七按钮连续三轮及三次真实UI重置"


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def write_new(path: Path, value: Any) -> str:
    raw = encode(value)
    with path.open("xb") as handle:
        handle.write(raw)
    return digest(raw)


def reply_new(path: Path, value: Any) -> None:
    """Admit a complete broker response atomically; never expose partial JSON."""
    require(not path.exists(), "Original broker response already exists")
    pending = path.with_name(path.name + ".pending")
    write_new(pending, value)
    pending.rename(path)


def source_state() -> dict[str, str]:
    files = [
        path
        for folder in ("apps/api/app", "apps/api/alembic", "apps/web/src", "apps/web/tests")
        for path in (ROOT / folder).rglob("*")
        if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".css"}
    ]
    files.extend(
        ROOT / name
        for name in (
            "apps/web/playwright.config.ts",
            "apps/web/vite.config.ts",
            "scripts/w1_browser_acceptance.py",
            "scripts/scenario_runner_rpc.py",
            "scripts/browser_checkpoint_oracles.py",
            "scripts/w1_audit_observe.py",
            "scripts/w1_current_check_context.py",
            "scripts/run_scoped_check.py",
            "packages/contracts/openapi.json",
            "packages/contracts/schema.d.ts",
            "uv.lock",
            "pnpm-lock.yaml",
            "alembic.ini",
            "package.json",
            "apps/web/package.json",
            "pnpm-workspace.yaml",
            "pyproject.toml",
            ".runtime/drive_mvp404_browser.py",
        )
    )
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in sorted(files)}


def wait_health(url: str, processes: list[subprocess.Popen[bytes]]) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        require(all(process.poll() is None for process in processes), "Owned server exited early")
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.2)
    raise TimeoutError("Owned local server did not become ready")


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def browser_command(pnpm: str, mode: str) -> list[str]:
    arguments = [pnpm, "--dir", "apps/web", "e2e", "w1-demo.spec.ts", "--max-failures=1"]
    if mode == "cases":
        arguments += ["--grep-invert", "同一隔离库"]
    elif mode == "rounds":
        arguments += ["--grep", "同一隔离库"]
    elif mode == "rent":
        arguments += ["--grep", "修改房租后绑定旧版本"]
    elif mode == "goal":
        arguments += ["--grep", BUSINESS_TITLES[1]]
    if os.name == "nt" and Path(pnpm).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(arguments)]
    return arguments


def validate_results(path: Path, mode: str) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    specs: list[dict[str, Any]] = []

    def visit(suites: list[dict[str, Any]]) -> None:
        for suite in suites:
            specs.extend(suite.get("specs", []))
            visit(suite.get("suites", []))

    visit(report.get("suites", []))
    expected = {
        "all": {*BUSINESS_TITLES, ROUND_TITLE},
        "cases": set(BUSINESS_TITLES),
        "rounds": {ROUND_TITLE},
        "rent": {BUSINESS_TITLES[4]},
        "goal": {BUSINESS_TITLES[1]},
    }
    require(mode in expected, "Unregistered browser verification scope")
    wanted = len(expected[mode])
    require(len(specs) == wanted, "Missing independently named actual Playwright results")
    require(
        {spec.get("title") for spec in specs} == expected[mode], "Original named case denominator"
    )
    require(
        all(
            spec.get("ok") is True
            and len(spec.get("tests", [])) == 1
            and len(spec["tests"][0].get("results", [])) == 1
            and spec["tests"][0]["results"][0].get("status") == "passed"
            for spec in specs
        ),
        "Skipped/failed/retried results cannot close W1 browser acceptance",
    )
    require(not report.get("errors"), "Playwright reported global errors")
    return {"count": len(specs), "titles": [spec["title"] for spec in specs], "mode": mode}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--mode", choices=("all", "cases", "rounds", "rent", "goal"), default="all")
    parser.add_argument("--observe-audit", action="store_true")
    parser.add_argument("--acceptance-context")
    parser.add_argument("--api-port", type=int, default=18047)
    parser.add_argument("--web-port", type=int, default=15179)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not args.run:
        print("PREPARED_NOT_EXECUTED: explicit --run required; no database/browser started")
        return 0
    acceptance = None
    if args.acceptance_context:
        require(args.mode == "all", "Current-check acceptance requires all original cases/rounds")
        current = runpy.run_path(str(ROOT / "scripts/w1_final_acceptance.py"))["source_state"](ROOT)
        acceptance = runpy.run_path(str(ROOT / "scripts/w1_current_check_context.py"))[
            "bind_context"
        ](ROOT, args.acceptance_context, current)
    require(
        1024 <= args.api_port <= 65535
        and 1024 <= args.web_port <= 65535
        and args.api_port != args.web_port,
        "Invalid independent local ports",
    )
    destination = (
        (ROOT / args.output).resolve()
        if args.output
        else OUTPUT / f"w1-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    )
    require(
        destination.is_relative_to(OUTPUT.resolve()) and destination != OUTPUT.resolve(),
        "Output must remain under output/playwright",
    )
    require(not destination.exists(), "Fresh output required; original attempts are immutable")
    for port in (args.api_port, args.web_port):
        with socket.socket() as check:
            check.bind(("127.0.0.1", port))
    node, pnpm = shutil.which("node"), shutil.which("pnpm")
    require(node is not None and pnpm is not None, "Installed Node/pnpm runtime required")
    require(
        (ROOT / "scripts/scenario_runner_rpc.py").is_file(),
        "Scenario RPC source is not frozen/available",
    )
    sys.path.insert(0, str(ROOT / "apps/api"))
    from alembic import command
    from alembic.config import Config
    from app.db import models as models
    from app.db.base import Base
    from app.db.session import create_database_engine
    from app.db.settings import DatabaseSettings
    from app.db.testing import require_test_database, temporary_database
    from sqlalchemy import event, select, text
    from sqlalchemy.engine import make_url

    source_url = make_url(DatabaseSettings().database_url)
    require(
        source_url.drivername == "postgresql+psycopg"
        and source_url.host in {"127.0.0.1", "localhost", "::1"}
        and source_url.port == 54329,
        "Only guarded local54329 disposable database administration is supported",
    )
    destination.mkdir(parents=True)
    for name in ("broker", "checkpoints", "rpc", "playwright"):
        (destination / name).mkdir()
    frozen = source_state()
    oracle = runpy.run_path(
        str(ROOT / ".runtime/drive_mvp404_browser.py"), run_name="w1_read_only_oracles"
    )
    reset_adapter = runpy.run_path(
        str(ROOT / "scripts/browser_checkpoint_oracles.py"), run_name="w1_typed_reset_adapter"
    )
    manifest: dict[str, Any] = {
        "status": "INCOMPLETE",
        "started_at": now(),
        "run_id": destination.name,
        "purpose": "MVP_ACCEPTANCE" if acceptance is not None else "DEVELOPMENT",
        "acceptance_context": acceptance,
        "classification": "ACTUAL_UI_ACCEPTANCE_NOT_PRODUCT_SLA",
        "mode": args.mode,
        "source_before": frozen,
        "reset_oracle_method": {
            "revision": "W1_TYPED_COLUMN_NORMALIZATION_V1",
            "original_path": ".runtime/drive_mvp404_browser.py",
            "original_sha256": reset_adapter["ORIGINAL_ORACLE_SHA256"],
            "adapter_path": "scripts/browser_checkpoint_oracles.py",
            "adapter_sha256": frozen[str(Path("scripts/browser_checkpoint_oracles.py"))],
            "all_original_assertions_retained": True,
        },
        "phases": [],
        "checkpoints": [],
        "rpc": [],
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dirty_tree_before": subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ),
        "ports": {"api": args.api_port, "web": args.web_port},
        "trusted_clock": (
            "Native datetime.now(UTC); browser protocol forbids clock/result/amount inputs"
        ),
        "formal_database_touched": False,
        "temporary_database_exited": False,
    }
    processes: list[subprocess.Popen[bytes]] = []

    def save() -> None:
        (destination / "manifest.json").write_bytes(encode(manifest))

    def phase(name: str, action: Any) -> Any:
        started = time.perf_counter()
        row: dict[str, Any] = {"name": name, "started_at": now(), "status": "RUNNING"}
        manifest["phases"].append(row)
        save()
        try:
            result = action()
            row["status"] = "PASSED"
            return result
        except BaseException:
            row["status"] = "FAILED"
            raise
        finally:
            row["wall_seconds"] = time.perf_counter() - started
            row["finished_at"] = now()
            save()
            print(
                f"phase={name} status={row['status']} seconds={row['wall_seconds']:.3f}", flush=True
            )

    save()
    try:
        with ExitStack() as stack:
            created = time.perf_counter()
            url = stack.enter_context(temporary_database())
            generated_url = make_url(url)
            require(
                generated_url.host == source_url.host
                and generated_url.port == source_url.port
                and generated_url.drivername == source_url.drivername,
                "Generated database escaped the verified local source",
            )
            database = require_test_database(make_url(url).database)
            require(
                re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is not None,
                "Exact generated test database name required",
            )
            manifest["database"] = database
            manifest["database_url_redacted"] = make_url(url).render_as_string(hide_password=True)
            manifest["phases"].append(
                {
                    "name": "create_disposable_database",
                    "wall_seconds": time.perf_counter() - created,
                    "status": "PASSED",
                }
            )
            engine = create_database_engine(url)
            stack.callback(engine.dispose)
            snapshot_engine = create_database_engine(url).execution_options(
                isolation_level="REPEATABLE READ"
            )
            stack.callback(snapshot_engine.dispose)

            @event.listens_for(snapshot_engine, "begin")
            def readonly(connection: Any) -> None:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")

            snapshot_serial = 0

            def snapshot(label: str) -> tuple[dict[str, Any], dict[str, Any]]:
                nonlocal snapshot_serial
                snapshot_serial += 1
                with snapshot_engine.connect() as connection:
                    require(
                        connection.exec_driver_sql("SHOW transaction_isolation").scalar_one()
                        == "repeatable read",
                        "Snapshot isolation changed",
                    )
                    require(
                        connection.exec_driver_sql("SHOW transaction_read_only").scalar_one()
                        == "on",
                        "Snapshot is not read-only",
                    )
                    names: set[str] = set(
                        connection.execute(
                            text(
                                "SELECT table_name FROM information_schema.tables "
                                "WHERE table_schema='public' AND table_type='BASE TABLE'"
                            )
                        ).scalars()
                    )
                    require(
                        names == set(Base.metadata.tables) | {"alembic_version"}
                        and len(names) == 24,
                        "Physical24/model23 registry mismatch",
                    )
                    business = {
                        table.name: [
                            dict(row)
                            for row in connection.execute(
                                select(table).order_by(table.c.id)
                            ).mappings()
                        ]
                        for table in Base.metadata.sorted_tables
                    }
                    heads: list[str] = list(
                        connection.exec_driver_sql(
                            "SELECT version_num FROM alembic_version ORDER BY version_num"
                        ).scalars()
                    )
                raw = encode(business)
                path = destination / "checkpoints" / f"{snapshot_serial:04d}-{label}.json.gz"
                with path.open("xb") as handle:
                    handle.write(gzip.compress(raw, mtime=0))
                # Normalize to the same JSON primitives used by the immutable old oracle.
                data = json.loads(raw)
                record = {
                    "path": str(path.relative_to(ROOT)),
                    "sha256": digest(path.read_bytes()),
                    "data_sha256": digest(raw),
                    "metadata_heads": heads,
                    "physical_tables": sorted(names),
                    "row_count": sum(map(len, data.values())),
                    "bytes": len(raw),
                }
                return data, record

            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
            config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
            phase("migration", lambda: command.upgrade(config, "head"))
            from app.services.demo_seed import seed_demo

            summary = phase("native_seed_once", lambda: seed_demo(engine))
            write_new(destination / "native-seed-summary.json", summary.model_dump(mode="json"))
            baseline, baseline_meta = phase(
                "native_baseline_snapshot", lambda: snapshot("native-baseline")
            )
            manifest["baseline"] = baseline_meta
            manifest["baseline_oracle"] = oracle["money_oracle"](baseline, baseline)
            save()
            environment = dict(
                os.environ,
                PYTHONUTF8="1",
                PYTHONUNBUFFERED="1",
                PYTHONPATH=str(ROOT / "apps/api"),
                DATABASE_URL=url,
                API_PROXY_TARGET=f"http://127.0.0.1:{args.api_port}",
                VITE_API_BASE_URL="",
                BF_W1_BROWSER_RUN="1",
                BF_W1_SERVERS_EXTERNAL="1",
                BF_W1_API_PORT=str(args.api_port),
                BF_W1_WEB_PORT=str(args.web_port),
                BF_W1_RUN_DIRECTORY=str(destination),
            )

            def launch(arguments: list[str], label: str) -> subprocess.Popen[bytes]:
                handle = stack.enter_context((destination / f"{label}.log").open("xb"))
                process = subprocess.Popen(
                    arguments,
                    cwd=ROOT,
                    env=environment,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                processes.append(process)
                stack.callback(stop, process)
                manifest.setdefault("processes", []).append(
                    {"label": label, "pid": process.pid, "arguments": arguments}
                )
                save()
                return process

            launch(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "--no-access-log",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                ],
                "api",
            )
            launch(
                [
                    str(node),
                    str(ROOT / "apps/web/node_modules/vite/bin/vite.js"),
                    str(ROOT / "apps/web"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.web_port),
                ],
                "web",
            )
            phase(
                "api_ready",
                lambda: wait_health(f"http://127.0.0.1:{args.api_port}/api/v1/health", processes),
            )
            phase("web_ready", lambda: wait_health(f"http://127.0.0.1:{args.web_port}", processes))
            browser = launch(browser_command(str(pnpm), args.mode), "playwright")
            previous: dict[str, Any] | None = None
            previous_meta: dict[str, Any] | None = None
            handled: set[Path] = set()

            def handle_request(path: Path) -> None:
                nonlocal previous, previous_meta
                request = json.loads(path.read_text(encoding="utf-8"))
                identity = UUID(request["id"])
                require(path.name == f"{identity}.request.json", "Broker file identity mismatch")
                scenario = request["scenario_id"]
                require(
                    request["protocol"] == PROTOCOL
                    and isinstance(scenario, str)
                    and re.fullmatch(r"w1-[A-Za-z0-9]{1,64}", scenario) is not None,
                    "Invalid broker identity",
                )
                require(
                    set(request)
                    <= {
                        "protocol",
                        "id",
                        "scenario_id",
                        "operation",
                        "label",
                        "mode",
                        "expected_epoch_id",
                        "policy_id",
                        "action_id",
                        "goal_id",
                    },
                    "Browser cannot supply clock, financial outputs, or arbitrary scenario data",
                )
                require(source_state() == frozen, "Source changed during actual browser run")
                operation = request["operation"]
                expected = str(UUID(request["expected_epoch_id"]))
                result: dict[str, Any]
                if operation == "checkpoint":
                    label, mode = request["label"], request["mode"]
                    require(
                        isinstance(label, str)
                        and re.fullmatch(r"[a-z0-9-]{1,90}", label) is not None
                        and mode in KINDS,
                        "Invalid checkpoint",
                    )
                    data, metadata = phase("snapshot:" + label, lambda: snapshot(label))
                    require(
                        oracle["open_epoch"](data)["id"] == expected,
                        "Checkpoint epoch differs from actual UI",
                    )
                    result = {"snapshot": metadata}
                    if mode != "BEGIN":
                        if previous is None or previous_meta is None:
                            raise ValueError("Missing actual preceding snapshot")
                        require(
                            previous_meta["metadata_heads"] == metadata["metadata_heads"],
                            "Migration version changed during UI run",
                        )
                        if mode == "READ_ONLY":
                            require(
                                previous == data,
                                "Read/replay checkpoint wrote a physical business table",
                            )
                        elif mode == "RESET":
                            result["oracle"] = reset_adapter["reset_oracle"](
                                previous, data, baseline
                            )
                        else:
                            result["oracle"] = oracle["money_oracle"](previous, data)
                    previous, previous_meta = data, metadata
                    manifest["checkpoints"].append(
                        {"scenario_id": scenario, "label": label, "mode": mode, "result": result}
                    )
                else:
                    require(
                        operation
                        in {
                            "prepare_rent_old_action",
                            "verify_round",
                            "verify_legacy_recovery",
                            "ingest_goal_income",
                        },
                        "Unknown bounded scenario operation",
                    )
                    wire = {
                        "protocol": PROTOCOL,
                        "purpose": "DEVELOPMENT",
                        "scenario_id": scenario,
                        "operation": operation,
                        "expected_epoch_id": expected,
                    }
                    if operation == "prepare_rent_old_action":
                        wire["policy_id"] = str(UUID(request["policy_id"]))
                    elif operation == "verify_legacy_recovery":
                        wire["action_id"] = str(UUID(request["action_id"]))
                    elif operation == "ingest_goal_income":
                        wire["goal_id"] = str(UUID(request["goal_id"]))
                    stem = str(identity)
                    write_new(destination / "rpc" / f"{stem}.input.json", wire)
                    started = time.perf_counter()
                    with (destination / "rpc" / f"{stem}.stderr.txt").open("xb") as stderr:
                        completed = subprocess.run(
                            [sys.executable, str(ROOT / "scripts/scenario_runner_rpc.py")],
                            input=encode(wire) + b"\n",
                            stdout=subprocess.PIPE,
                            stderr=stderr,
                            env=environment,
                            cwd=ROOT,
                            timeout=1200,
                            check=False,
                        )
                    with (destination / "rpc" / f"{stem}.stdout.json").open("xb") as stdout:
                        stdout.write(completed.stdout)
                    require(
                        completed.returncode == 0, "Scenario RPC failed; retained raw stderr/stdout"
                    )
                    response = json.loads(completed.stdout)
                    require(
                        response["protocol"] == PROTOCOL
                        and response["scenario_id"] == scenario
                        and response["operation"] == operation
                        and response["status"] == "PASSED",
                        "Scenario RPC did not establish actual properties",
                    )
                    result = response["result"]
                    if operation == "verify_legacy_recovery":
                        require(
                            result.get("verified") is True
                            and result.get("action_id") == wire["action_id"],
                            "The original legacy receipt was not verified for this exact action",
                        )
                    elif operation == "ingest_goal_income":
                        require(
                            result.get("goal_id") == wire["goal_id"]
                            and result["request"]["kind"] == "INCOME"
                            and result["request"]["amount_cents"] == 200_000
                            and result["request"]["counterparty_ref"] == "payroll"
                            and result["result"]["simulation"] is True
                            and result["result"]["bank_status"] == "SETTLED"
                            and result["result"]["projection_status"] == "PROJECTED"
                            and len(result["result"]["economic_posting_ids"]) == 2
                            and result["result"]["transaction_id"] is not None,
                            "The fixed real simulated income was not settled and projected",
                        )
                    manifest["rpc"].append(
                        {
                            "scenario_id": scenario,
                            "operation": operation,
                            "input": str(
                                (destination / "rpc" / f"{stem}.input.json").relative_to(ROOT)
                            ),
                            "stdout_sha256": digest(completed.stdout),
                            "wall_seconds": time.perf_counter() - started,
                            "result": result,
                        }
                    )
                save()
                reply_new(
                    path.with_name(f"{identity}.response.json"),
                    {"id": str(identity), "status": "PASSED", "result": result},
                )

            while browser.poll() is None:
                for path in sorted((destination / "broker").glob("*.request.json")):
                    if path in handled:
                        continue
                    try:
                        handle_request(path)
                    except BaseException as error:
                        identity = path.name.removesuffix(".request.json")
                        reply = path.with_name(f"{identity}.response.json")
                        if not reply.exists():
                            reply_new(
                                reply,
                                {
                                    "id": identity,
                                    "status": "FAILED",
                                    "result": {},
                                    "error": str(error),
                                },
                            )
                        raise
                    handled.add(path)
                require(
                    all(process.poll() is None for process in processes[:2]),
                    "Owned API/Web exited during acceptance",
                )
                time.sleep(0.1)
            manifest["playwright_exit_code"] = browser.returncode
            require(browser.returncode == 0, "Actual Playwright failed; raw artifacts retained")
            manifest["actual_results"] = validate_results(
                destination / "playwright/results.json", args.mode
            )
            _, manifest["final_snapshot"] = phase("final_snapshot", lambda: snapshot("final"))
            if args.mode == "all" or args.observe_audit:
                # The parent owns this lifecycle: audit must finish before owned DB cleanup.
                # This replaces the external observer/drop race, without changing old evidence.
                audit_output = (
                    ROOT / "docs/progress/evidence/W1" / f"native-audit-{destination.name}"
                )
                audit_argv = [
                    sys.executable,
                    str(ROOT / "scripts/w1_audit_observe.py"),
                    "--run",
                    "--browser-manifest",
                    str((destination / "manifest.json").relative_to(ROOT)),
                    "--output",
                    str(audit_output.relative_to(ROOT)),
                ]
                manifest["audit_observation"] = {
                    "command": audit_argv,
                    "path": str((audit_output / "manifest.json").relative_to(ROOT)),
                    "status": "INCOMPLETE",
                }
                save()

                def observe_audit() -> None:
                    executed_audit = audit_argv
                    label = "final-alive-" + destination.name
                    if acceptance is not None:
                        executed_audit = [
                            sys.executable,
                            str(ROOT / "scripts/run_scoped_check.py"),
                            "--task",
                            "W1",
                            "--label",
                            label,
                            "--source-prefix",
                            "apps/",
                            "--source-prefix",
                            "scripts/",
                            "--",
                            *audit_argv,
                        ]
                    manifest["audit_observation"]["executed_command"] = executed_audit
                    with (destination / "audit-observation.log").open("xb") as audit_log:
                        completed = subprocess.run(
                            executed_audit,
                            cwd=ROOT,
                            env=environment,
                            stdout=audit_log,
                            stderr=subprocess.STDOUT,
                            timeout=7200,
                        )
                    manifest["audit_observation"]["exit_code"] = completed.returncode
                    require(completed.returncode == 0, "Native final alive audit failed")
                    if acceptance is not None:
                        candidates = re.findall(
                            r"^EVIDENCE_DIRECTORY=(.+)$",
                            (destination / "audit-observation.log").read_text(encoding="utf-8"),
                            re.M,
                        )
                        require(
                            len(candidates) == 1, "Actual audit wrapper must name its one output"
                        )
                        scoped_directory = Path(candidates[0].strip()).resolve()
                        require(
                            scoped_directory.is_relative_to(ROOT / "docs/progress/evidence/W1")
                            and scoped_directory.name.startswith(label + "-"),
                            "Actual audit scoped owner differs",
                        )
                        manifest["audit_observation"]["scoped_manifest"] = (
                            (scoped_directory / "manifest.json").relative_to(ROOT).as_posix()
                        )
                    actual_audit = json.loads((audit_output / "manifest.json").read_bytes())
                    require(
                        actual_audit["status"] == "PASSED"
                        and actual_audit["owner_run_id"] == destination.name
                        and actual_audit["source_before"] == actual_audit["source_after"] == frozen,
                        "Native audit ownership/source drift",
                    )
                    manifest["audit_observation"].update(
                        status="PASSED",
                        sha256=digest((audit_output / "manifest.json").read_bytes()),
                    )

                phase("final_alive_audit", observe_audit)
            manifest["source_after"] = source_state()
            require(manifest["source_after"] == frozen, "Source drift invalidates acceptance")
            if acceptance is not None:
                current = runpy.run_path(str(ROOT / "scripts/w1_final_acceptance.py"))[
                    "source_state"
                ](ROOT)
                require(
                    runpy.run_path(str(ROOT / "scripts/w1_current_check_context.py"))[
                        "bind_context"
                    ](ROOT, args.acceptance_context, current)
                    == acceptance,
                    "Final-check original context/full source changed",
                )
            manifest["status"] = "PASSED" if args.mode == "all" else "PARTIAL_SCOPE_PASSED"
        manifest["temporary_database_exited"] = True
        # Verify the generated database is absent using the same local admin source.
        admin = create_database_engine(
            source_url.set(database="postgres").render_as_string(hide_password=False)
        )
        try:
            with admin.connect() as connection:
                manifest["generated_database_absent"] = not connection.execute(
                    text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                    {"name": manifest["database"]},
                ).scalar_one()
            require(
                manifest["generated_database_absent"],
                "Generated database remains after guarded cleanup",
            )
        finally:
            admin.dispose()
    except BaseException as error:
        manifest["status"] = "FAILED"
        message = str(error)
        if source_url.password:
            message = message.replace(source_url.password, "[REDACTED]")
        manifest["error"] = {"type": type(error).__name__, "message": message}
    finally:
        for process in reversed(processes):
            stop(process)
        if "database" in manifest:
            # This also runs after failed browser/RPC attempts. Absence is observed, not inferred.
            cleanup_admin = create_database_engine(
                source_url.set(database="postgres").render_as_string(hide_password=False)
            )
            try:
                with cleanup_admin.connect() as connection:
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    absent = not connection.execute(
                        text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                        {"name": require_test_database(manifest["database"])},
                    ).scalar_one()
                manifest["generated_database_absent"] = absent
                manifest["cleanup_status"] = "VERIFIED_ABSENT" if absent else "STILL_PRESENT"
                manifest["temporary_database_exited"] = absent
                if not absent:
                    manifest["status"] = "FAILED"
            except BaseException as error:
                manifest["cleanup_status"] = "UNVERIFIED"
                manifest["cleanup_error_type"] = type(error).__name__
                manifest["status"] = "FAILED"
            finally:
                cleanup_admin.dispose()
        manifest["finished_at"] = now()
        manifest["artifact_hashes"] = {
            str(path.relative_to(destination)): digest(path.read_bytes())
            for path in destination.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        save()
    print(f"W1 {manifest['status']} evidence={destination.relative_to(ROOT)}", flush=True)
    return 0 if manifest["status"] in {"PASSED", "PARTIAL_SCOPE_PASSED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
