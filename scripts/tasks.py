"""Single cross-platform entry point for the documented make targets."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ.setdefault("UV_CACHE_DIR", str(ROOT / ".uv-cache"))
os.environ["PYTHONPATH"] = str(ROOT / "apps" / "api")
os.environ.setdefault("PYTHONUTF8", "1")
cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding="utf-8", errors="replace")
RUN_ID = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
RUN_DIRECTORY = ROOT / ".runtime" / "quality" / RUN_ID
COMMANDS: list[dict[str, object]] = []
W1_FINAL_OUTPUT_PROTOCOL = "bounded-funds-native-check-evidence-v1"
W1_FINAL_OUTPUT_GROUPS = (
    "backend_coverage_and_properties",
    "frontend_interactions",
    "six_business_e2e",
    "three_demo_rounds",
    "original_financial_chain",
    "audit_chain",
)
ACCEPTANCE_GROUPS: dict[str, dict[str, Any]] = {}


def source_state() -> dict[str, object]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    configuration = {
        "pyproject.toml",
        "uv.lock",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "package.json",
        "alembic.ini",
        "docker-compose.yml",
        "Makefile",
        "make.cmd",
        ".dockerignore",
        "docs/spec/mvp-coverage-scope.json",
        "docs/spec/mvp-coverage.ini",
    }
    hashes = {}
    for relative in sorted(set(tracked.split("\0")) - {""}):
        if relative in configuration or relative.startswith(
            ("apps/", "scripts/", "deploy/", "packages/contracts/", ".github/workflows/")
        ):
            path = ROOT / relative
            if path.is_file():
                hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"git_revision": revision, "source_sha256": hashes}


def command(*args: str) -> list[str]:
    binary = shutil.which(args[0])
    if not binary:
        raise SystemExit(f"Missing prerequisite: {args[0]}")
    return [binary, *args[1:]]


def run(*args: str) -> None:
    print("+ " + " ".join(args), flush=True)
    RUN_DIRECTORY.mkdir(parents=True, exist_ok=True)
    log_path = RUN_DIRECTORY / f"{len(COMMANDS) + 1:02d}-{args[0]}.log"
    started = datetime.now(UTC).isoformat()
    with log_path.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command(*args),
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            output.write(line)
            print(line, end="", flush=True)
        code = process.wait()
    COMMANDS.append(
        {"command": list(args), "started_at": started, "exit_code": code, "log": log_path.name}
    )
    if code:
        raise subprocess.CalledProcessError(code, args)


def uv(*args: str) -> None:
    run("uv", "run", "--frozen", *args)


def scoped_run(label: str, *args: str) -> dict[str, Any]:
    """Execute original argv through the actual native wrapper, never synthesize its records."""
    wrapper = [
        "uv",
        "run",
        "--frozen",
        "python",
        "scripts/run_scoped_check.py",
        "--task",
        "W1",
        "--label",
        RUN_ID + "-" + label,
    ]
    for prefix in (
        "apps/",
        "scripts/",
        "deploy/",
        "packages/",
        "docs/spec/",
        "pyproject.toml",
        "uv.lock",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "package.json",
        "alembic.ini",
        "docker-compose.yml",
        "Makefile",
        "make.cmd",
        ".dockerignore",
    ):
        wrapper.extend(("--source-prefix", prefix))
    executed = command(*args)
    run(*wrapper, "--", *executed)
    log = RUN_DIRECTORY / str(COMMANDS[-1]["log"])
    matches = re.findall(r"^EVIDENCE_DIRECTORY=(.+)$", log.read_text(encoding="utf-8"), re.M)
    matches = [
        value
        for value in matches
        if Path(value.strip()).name.startswith(RUN_ID + "-" + label + "-")
    ]
    if len(matches) != 1:
        raise RuntimeError("Native scoped output must identify exactly one original directory")
    directory = Path(matches[0].strip()).resolve()
    if not directory.is_relative_to((ROOT / "docs/progress/evidence/W1").resolve()):
        raise RuntimeError("Native scoped evidence escaped the W1 evidence directory")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    before = json.loads((directory / "source.before.json").read_text(encoding="utf-8"))
    after = json.loads((directory / "source.after.json").read_text(encoding="utf-8"))
    current = source_state()
    registered = cast(dict[str, str], current["source_sha256"])
    if not (
        manifest.get("command") == executed
        and manifest.get("label") == RUN_ID + "-" + label
        and manifest.get("status") == "PASSED"
        and type(manifest.get("exit_code")) is int
        and manifest["exit_code"] == 0
        and manifest.get("all_source_stable") is True
        and manifest.get("git_head") == current["git_revision"]
        and before == after
        and all(before.get(name) == digest for name, digest in registered.items())
        and manifest.get("log_sha256")
        == hashlib.sha256((directory / "output.log").read_bytes()).hexdigest()
    ):
        raise RuntimeError("Actual scoped argv, source, exit or original output differs")
    return {
        "run_id": manifest["run_id"],
        "requested_argv": list(args),
        "paths": {
            "manifest": directory / "manifest.json",
            "source_before": directory / "source.before.json",
            "source_after": directory / "source.after.json",
            "log": directory / "output.log",
        },
    }


def register_acceptance_group(
    requirement: str, scoped: dict[str, Any], extra: dict[str, Path] | None = None
) -> None:
    """Record paths from this task's executed child; semantic gates remain with the exporter."""
    if requirement in ACCEPTANCE_GROUPS:
        raise RuntimeError("Acceptance group already recorded for this native task")
    ACCEPTANCE_GROUPS[requirement] = {"scoped": scoped, "extra": extra or {}}


def register_acceptance_originals(
    requirement: str,
    check: dict[str, Any],
    artifacts: list[dict[str, Any]],
    runs: dict[str, dict[str, Any]],
) -> None:
    """Allow a native child adapter to retain nested inputs and each original producer's run."""
    if requirement in ACCEPTANCE_GROUPS or check.get("requirement_id") != requirement:
        raise RuntimeError("Duplicate or mismatched native acceptance original group")
    ACCEPTANCE_GROUPS[requirement] = {
        "external": {"check": check, "artifacts": artifacts, "runs": runs}
    }


def final_acceptance_outputs() -> dict[str, str] | None:
    """Bind current-check originals to its explicit parent; missing adapters cannot close check."""
    context_name = os.environ.get("BOUNDEDFUNDS_FINAL_CONTEXT")
    if context_name is None:
        return None
    context_path = Path(context_name).resolve()
    if not context_path.is_relative_to((ROOT / "docs/progress/evidence/W1").resolve()):
        raise RuntimeError("Final context must be an explicit owned W1 original")
    context = json.loads(context_path.read_text(encoding="utf-8"))
    database = context.get("database")
    endpoint = urlsplit(os.environ.get("DATABASE_URL", ""))
    if not (
        context.get("protocol") == "bounded-funds-final-context-v1"
        and re.fullmatch(r"bf_test_[0-9a-f]{32}", str(database))
        and endpoint.hostname == "127.0.0.1"
        and endpoint.port == 54329
        and endpoint.username == "bounded"
        and endpoint.path == "/" + database
    ):
        raise RuntimeError("Final check context is not its isolated owned database")
    source = context["source"]
    current = source_state()
    registered = cast(dict[str, str], current["source_sha256"])
    if current["git_revision"] != source["git_head"] or any(
        registered.get(name) != digest for name, digest in source["files"].items()
    ):
        raise RuntimeError("Final check source differs from the frozen parent")
    expected = set(context["deferred_groups"]) - {"formal_history_preservation"}
    missing = expected - ACCEPTANCE_GROUPS.keys()
    if missing:
        raise RuntimeError("MISSING current-check adapters: " + ", ".join(sorted(missing)))
    artifacts: dict[str, dict[str, Any]] = {}
    runs: dict[str, dict[str, Any]] = {}

    def original(path: Path, run_id: str, role: str = "RESULT") -> str:
        path = path.resolve()
        relative = path.relative_to(ROOT.resolve()).as_posix()
        if not (
            relative.startswith(("docs/", ".runtime/", "output/playwright/"))
            and path.is_file()
            and not any(part.startswith(".env") for part in path.parts)
        ):
            raise RuntimeError("Native output is not an existing evidence original")
        identity = "native_" + hashlib.sha256(relative.encode()).hexdigest()[:24]
        raw = path.read_bytes()
        record = {
            "artifact_id": identity,
            "role": role,
            "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
            "run_id": run_id,
            "purpose": "MVP_ACCEPTANCE",
            "source_sha256": source["source_sha256"],
            "validator": "STRICT_JSON_V1" if path.suffix == ".json" else "UTF8_TEXT_V1",
        }
        if identity in artifacts and artifacts[identity] != record:
            raise RuntimeError("Original was ambiguously attributed to different native runs")
        artifacts[identity] = record
        runs[run_id] = {
            "purpose": "MVP_ACCEPTANCE",
            "source_context": "CURRENT_SOURCE",
            "source_sha256": source["source_sha256"],
        }
        return identity

    checks = []
    for requirement in sorted(expected):
        recorded = ACCEPTANCE_GROUPS[requirement]
        if "external" in recorded:
            external = recorded["external"]
            check = external["check"]
            if check.get("validator") != "MVP_NATIVE_V2":
                raise RuntimeError("Native child adapter lacks semantic inputs")
            for run_id, run in external["runs"].items():
                if run != {
                    "purpose": "MVP_ACCEPTANCE",
                    "source_context": "CURRENT_SOURCE",
                    "source_sha256": source["source_sha256"],
                }:
                    raise RuntimeError("Child original has stale or nonacceptance run binding")
                if run_id in runs and runs[run_id] != run:
                    raise RuntimeError("Child native run identity collides")
                runs[run_id] = run
            original_ids = set()
            for row in external["artifacts"]:
                path = (ROOT / row["path"]).resolve()
                if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
                    raise RuntimeError("Child original escaped or missing")
                if any(part.startswith(".env") for part in path.parts):
                    raise RuntimeError("Private child original refused")
                raw = path.read_bytes()
                if not (
                    row.get("purpose") == "MVP_ACCEPTANCE"
                    and row.get("source_sha256") == source["source_sha256"]
                    and row.get("run_id") in external["runs"]
                    and row.get("sha256") == hashlib.sha256(raw).hexdigest()
                    and type(row.get("size_bytes")) is int
                    and row["size_bytes"] == len(raw)
                ):
                    raise RuntimeError("Child original bytes/run/source differs")
                identity = row["artifact_id"]
                if identity in artifacts and artifacts[identity] != row:
                    raise RuntimeError("Child original artifact identity collides")
                artifacts[identity] = row
                original_ids.add(identity)
            if not set(check["artifact_ids"]) <= original_ids:
                raise RuntimeError("Child semantic group references absent originals")
            checks.append(check)
            continue
        scoped = recorded["scoped"]
        inputs: dict[str, Any] = {
            "scoped": {
                name: original(path, scoped["run_id"]) for name, path in scoped["paths"].items()
            }
        }
        inputs.update(
            {
                name: original(path, scoped["run_id"], "RULE" if name == "scope" else "RESULT")
                for name, path in recorded["extra"].items()
            }
        )
        checks.append(
            {
                "requirement_id": requirement,
                "validator": "MVP_NATIVE_V2",
                "inputs": inputs,
                "artifact_ids": sorted(
                    {
                        *inputs["scoped"].values(),
                        *(value for name, value in inputs.items() if name != "scoped"),
                    }
                ),
            }
        )
    bundle = {
        "protocol": W1_FINAL_OUTPUT_PROTOCOL,
        "producer_path": "scripts/tasks.py",
        "producer_sha256": hashlib.sha256((ROOT / "scripts/tasks.py").read_bytes()).hexdigest(),
        "owner_run_id": context["owner_run_id"],
        "task_run_id": RUN_ID,
        "source": source,
        "checks": checks,
        "artifacts": list(artifacts.values()),
        "runs": runs,
    }
    path = RUN_DIRECTORY / "acceptance-outputs.json"
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def quality_checks(*targets: str) -> None:
    """Run each identical fast command once within this orchestration only."""
    checks = {
        "lint": (
            ("uv", "run", "--frozen", "ruff", "check", "apps/api", "scripts"),
            ("uv", "run", "--frozen", "ruff", "format", "--check", "apps/api", "scripts"),
            ("uv", "run", "--frozen", "mypy"),
            ("pnpm", "--dir", "apps/web", "lint"),
        ),
        "typecheck": (
            ("uv", "run", "--frozen", "python", "scripts/generate_openapi.py", "--check"),
            ("uv", "run", "--frozen", "mypy"),
            ("pnpm", "--dir", "apps/web", "typecheck"),
        ),
    }
    completed: set[tuple[str, ...]] = set()
    for target in targets:
        for args in checks[target]:
            if args not in completed:
                run(*args)
                completed.add(args)


def backend_acceptance() -> None:
    """One actual backend run supplies both coverage and observed property gates."""
    directory = RUN_DIRECTORY / "mvp-501"
    directory.mkdir(parents=True, exist_ok=True)
    coverage = str(directory / "coverage.json")
    observed = str(directory / "hypothesis-observed.json")
    previous = os.environ.get("COVERAGE_FILE")
    os.environ["COVERAGE_FILE"] = str(directory / ".coverage")
    try:
        scoped = scoped_run(
            "backend",
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
        )
    finally:
        if previous is None:
            os.environ.pop("COVERAGE_FILE", None)
        else:
            os.environ["COVERAGE_FILE"] = previous
    uv(
        "python",
        "scripts/verify_mvp_coverage.py",
        "--coverage",
        coverage,
        "--hypothesis-report",
        observed,
        "--output",
        str(directory / "coverage-property-gate.json"),
    )
    register_acceptance_group(
        "backend_coverage_and_properties",
        scoped,
        {
            "scope": ROOT / "docs/spec/mvp-coverage-scope.json",
            "coverage": Path(coverage),
            "hypothesis": Path(observed),
        },
    )


def wait_http(url: str, process: subprocess.Popen[bytes]) -> None:
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError(f"Service exited before ready: {url}")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Service failed readiness check: {url}")


def start_services() -> None:
    run("docker", "compose", "up", "-d", "--wait", "db")
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for args, url in [
            (
                (
                    "uv",
                    "run",
                    "--frozen",
                    "uvicorn",
                    "--no-access-log",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ),
                "http://127.0.0.1:8000/api/v1/health",
            ),
            (("pnpm", "--dir", "apps/web", "dev", "--host", "127.0.0.1"), "http://127.0.0.1:5173"),
        ]:
            process = subprocess.Popen(command(*args), cwd=ROOT, start_new_session=os.name != "nt")
            processes.append(process)
            wait_http(url, process)
        print("Ready: http://127.0.0.1:5173 — Ctrl+C stops API/Web; db persists.", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(1)
        raise RuntimeError("A development service stopped unexpectedly")
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if sys.platform != "win32":
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


def main(target: str) -> None:
    if target == "bootstrap":
        if not (ROOT / ".env").exists():
            shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        run("uv", "sync", "--frozen")
        run("pnpm", "install", "--frozen-lockfile")
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".runtime/playwright-browsers")
        run("node", "apps/web/node_modules/@playwright/test/cli.js", "install", "ffmpeg")
    elif target == "dev":
        start_services()
    elif target == "migrate":
        run("docker", "compose", "up", "-d", "--wait", "db")
        uv("alembic", "upgrade", "head")
    elif target in {"lint", "typecheck"}:
        quality_checks(target)
    elif target == "fast-check":
        quality_checks("lint", "typecheck")
    elif target == "types":
        uv("python", "scripts/generate_openapi.py")
    elif target == "unit":
        uv("pytest", "-m", "not integration and not property", "--cov", "--cov-report=term-missing")
        run("pnpm", "--dir", "apps/web", "test")
    elif target in {"property", "integration"}:
        if target == "integration":
            run("docker", "compose", "up", "-d", "--wait", "db")
        uv("pytest", "-m", target)
    elif target == "test":
        run("docker", "compose", "up", "-d", "--wait", "db")
        backend_acceptance()
        scoped = scoped_run("frontend", "pnpm", "--dir", "apps/web", "test")
        register_acceptance_group("frontend_interactions", scoped)
    elif target == "e2e":
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".runtime/playwright-browsers")
        run("pnpm", "--dir", "apps/web", "e2e", "health.spec.ts")
        browser_output = ROOT / "output/playwright" / ("current-check-" + RUN_ID)
        context_name = os.environ.get("BOUNDEDFUNDS_FINAL_CONTEXT")
        arguments = [
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
            str(browser_output),
        ]
        if context_name is not None:
            arguments.extend(("--acceptance-context", context_name))
        scoped = scoped_run("browser", *arguments)
        if context_name is not None:
            sys.path.insert(0, str(ROOT))
            from scripts.w1_check_browser_outputs import capture

            groups = capture(ROOT, Path(context_name), browser_output / "manifest.json", scoped)
            for name, value in groups.items():
                register_acceptance_originals(
                    name, value["check"], value["artifacts"], value["runs"]
                )
    elif target == "check":
        quality_checks("lint", "typecheck")
        for child in ("test", "e2e"):
            main(child)
    elif target == "full-check":
        # Keep MVP coverage/property/native-context production intact. Its one full backend
        # invocation covers unit/property/integration; never repeat that suite three more times.
        for child in ("security-check", "check", "audit-verify", "evidence-check"):
            main(child)
    elif target in {"seed", "demo-reset"}:
        run("docker", "compose", "up", "-d", "--wait", "db")
        uv("alembic", "upgrade", "head")
        uv("python", "scripts/seed_demo.py")
    elif target == "policy-refresh":
        run("docker", "compose", "up", "-d", "--wait", "db")
        uv("alembic", "upgrade", "head")
        uv("python", "scripts/refresh_policy_states.py")
    elif target == "export-evidence":
        uv("python", "scripts/export_evidence.py")
    elif target == "audit-verify":
        uv("python", "scripts/verify_audit_chain.py")
    elif target in {"security-check", "evidence-check", "build-proposal"}:
        uv("python", f"scripts/{target.replace('-', '_')}.py")
    else:
        raise SystemExit(f"Unknown target: {target}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/tasks.py TARGET")
    print(f"run_id={RUN_ID}", flush=True)
    successful = False
    acceptance_outputs = None
    source = source_state()
    try:
        main(sys.argv[1])
        if sys.argv[1] == "check":
            acceptance_outputs = final_acceptance_outputs()
        if source_state() != source:
            raise RuntimeError(
                "Source changed during this command; original result is not acceptance"
            )
        successful = True
    except subprocess.CalledProcessError as error:
        print(f"QUALITY_CHILD_EXIT={error.returncode}", file=sys.stderr, flush=True)
        raise SystemExit(error.returncode) from error
    finally:
        RUN_DIRECTORY.mkdir(parents=True, exist_ok=True)
        (RUN_DIRECTORY / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": RUN_ID,
                    "target": sys.argv[1],
                    "successful": successful,
                    "finished_at": datetime.now(UTC).isoformat(),
                    "commands": COMMANDS,
                    "source": source,
                    "source_after": source_state(),
                    "source_stable": source_state() == source,
                    "acceptance_outputs": acceptance_outputs,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
