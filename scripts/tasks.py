"""Single cross-platform entry point for the documented make targets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ.setdefault("UV_CACHE_DIR", str(ROOT / ".uv-cache"))
os.environ["PYTHONPATH"] = str(ROOT / "apps" / "api")
os.environ.setdefault("PYTHONUTF8", "1")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
RUN_ID = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
RUN_DIRECTORY = ROOT / ".runtime" / "quality" / RUN_ID
COMMANDS: list[dict[str, object]] = []


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
    }
    hashes = {}
    for relative in sorted(set(tracked.split("\0")) - {""}):
        if relative in configuration or relative.startswith(
            ("apps/", "scripts/", "packages/contracts/")
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
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


def main(target: str) -> None:
    if target == "bootstrap":
        if not (ROOT / ".env").exists():
            shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        run("uv", "sync", "--frozen")
        run("pnpm", "install", "--frozen-lockfile")
    elif target == "dev":
        start_services()
    elif target == "migrate":
        run("docker", "compose", "up", "-d", "--wait", "db")
        uv("alembic", "upgrade", "head")
    elif target == "lint":
        uv("ruff", "check", "apps/api", "scripts")
        uv("ruff", "format", "--check", "apps/api", "scripts")
        uv("mypy")
        run("pnpm", "--dir", "apps/web", "lint")
    elif target == "typecheck":
        uv("python", "scripts/generate_openapi.py", "--check")
        uv("mypy")
        run("pnpm", "--dir", "apps/web", "typecheck")
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
        uv("pytest", "--cov")
        run("pnpm", "--dir", "apps/web", "test")
    elif target == "e2e":
        run("pnpm", "--dir", "apps/web", "e2e")
    elif target == "check":
        for child in ("lint", "typecheck", "test", "e2e"):
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
    source = source_state()
    try:
        main(sys.argv[1])
        successful = True
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
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
