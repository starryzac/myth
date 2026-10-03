"""Single cross-platform entry point for the documented make targets."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ.setdefault("UV_CACHE_DIR", str(ROOT / ".uv-cache"))
os.environ["PYTHONPATH"] = str(ROOT / "apps" / "api")


def command(*args: str) -> list[str]:
    binary = shutil.which(args[0])
    if not binary:
        raise SystemExit(f"Missing prerequisite: {args[0]}")
    return [binary, *args[1:]]


def run(*args: str) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(command(*args), check=True, cwd=ROOT)


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
        uv("pytest", "-m", target)
    elif target == "test":
        uv("pytest", "--cov")
        run("pnpm", "--dir", "apps/web", "test")
    elif target == "e2e":
        run("pnpm", "--dir", "apps/web", "e2e")
    elif target == "check":
        for child in ("lint", "typecheck", "test", "e2e"):
            main(child)
    elif target in {"seed", "demo-reset"}:
        uv("python", "scripts/seed_demo.py")
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
    main(sys.argv[1])
