"""Installation, build and owned simulation lifecycle commands."""

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*command: str) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        raise SystemExit(f"Required command is unavailable: {command[0]}")
    subprocess.run([executable, *command[1:]], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["bootstrap", "build", "build-all", "prepare", "dev", "status", "types"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if arguments.target == "bootstrap":
        run("uv", "sync", "--frozen", "--no-dev")
        run("pnpm", "install", "--frozen-lockfile")
    elif arguments.target in {"build", "build-all"}:
        run("pnpm", "build" if arguments.target == "build" else "build:all")
    elif arguments.target == "types":
        run("pnpm", "types")
    else:
        target = {"prepare": "prepare", "dev": "start", "status": "status"}[arguments.target]
        run("uv", "run", "--frozen", "--no-dev", "python", "scripts/zhiyu_next.py", target, *arguments.arguments)


if __name__ == "__main__":
    main()
