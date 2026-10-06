"""Capture an actual scoped command without turning selected or changing source into PASS."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def sources() -> dict[str, str]:
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
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
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in sorted(set(listing.split("\0")) - {""})
        if (ROOT / name).is_file()
        and (
            name in configuration
            or name.startswith(("apps/", "scripts/", "deploy/", "packages/contracts/"))
        )
    }


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task", required=True, choices=["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8"]
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--source-prefix", action="append", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("A command and a simple evidence label are required")
    if any(prefix.startswith(("/", "\\")) or ".." in prefix for prefix in args.source_prefix):
        parser.error("Source prefixes must be repository relative")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = ROOT / "docs/progress/evidence" / args.task / f"{args.label}-{run_id}"
    directory.mkdir(parents=True, exist_ok=False)
    before = sources()
    dump(directory / "source.before.json", before)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    env = dict(os.environ, PYTHONPATH=str(ROOT / "apps/api"), PYTHONUTF8="1")
    env.setdefault("UV_CACHE_DIR", str(ROOT / ".uv-cache"))
    started = datetime.now(UTC)
    print(f"EVIDENCE_DIRECTORY={directory}", flush=True)
    with (directory / "output.log").open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            output.write(line)
            output.flush()
            if len(line) > 2048:
                print(
                    line[:2048] + " [console abbreviated; complete line in output.log]", flush=True
                )
            else:
                print(line, end="", flush=True)
        code = process.wait()
    finished = datetime.now(UTC)
    after = sources()
    dump(directory / "source.after.json", after)
    changed = [
        name for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)
    ]
    scoped_changed = [
        name for name in changed if any(name.startswith(p) for p in args.source_prefix)
    ]
    status = "SOURCE_CHANGED" if scoped_changed else ("PASSED" if code == 0 else "FAILED")
    dump(
        directory / "manifest.json",
        {
            "run_id": run_id,
            "task": args.task,
            "label": args.label,
            "status": status,
            "scope": args.source_prefix,
            "command": command,
            "exit_code": code,
            "git_head": head,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "wall_seconds": (finished - started).total_seconds(),
            "environment": {"python": sys.version, "platform": platform.platform()},
            "source_changes": changed,
            "scoped_source_changes": scoped_changed,
            "all_source_stable": not changed,
            "scoped_source_stable": not scoped_changed,
            "log_sha256": hashlib.sha256((directory / "output.log").read_bytes()).hexdigest(),
            "coverage_boundary": "Only this actual command; not a full-version acceptance.",
        },
    )
    print(f"SCOPED_RESULT={status}; exit_code={code}", flush=True)
    return 2 if scoped_changed else code


if __name__ == "__main__":
    raise SystemExit(main())
