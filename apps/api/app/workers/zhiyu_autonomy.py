"""Explicitly enabled, source-verified worker for one owned extension round."""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.session import create_database_engine
from app.domain.demo_identity import DEMO_USER_ID
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_orchestration import drain_once
from app.zhiyu_next_isolation import require_zhiyu_next_engine

ROOT = Path(__file__).resolve().parents[4]
PERMIT = ROOT / ".runtime" / "zhiyu-next-validation" / "single-goal-acceptance.json"
REQUIRED_SOURCE = {
    "apps/api/app/services/zhiyu_orchestration.py",
    "apps/api/app/services/zhiyu_autonomy_validity.py",
    "apps/api/app/api/v1/zhiyu_next.py",
    "apps/api/app/workers/zhiyu_autonomy.py",
    "apps/api/app/zhiyu_next_isolation.py",
    "scripts/zhiyu_next.py",
    "uv.lock",
    "pyproject.toml",
}


def consumer_source_paths() -> set[str]:
    """Include the financial dependencies, migrations and newly added API modules."""
    return REQUIRED_SOURCE | {
        path.relative_to(ROOT).as_posix()
        for directory in (ROOT / "apps/api/app", ROOT / "apps/api/alembic")
        for path in directory.rglob("*.py")
        if "tests" not in path.relative_to(directory).parts
    }


def require_verified_source() -> None:
    """No daemon may accept money before its exact consumer source has passed."""
    if not PERMIT.is_file() or PERMIT.is_symlink():
        raise ValueError("Single-goal actual validation permit is missing")
    data = json.loads(PERMIT.read_text(encoding="utf-8"))
    if data.get("status") != "PASSED" or data.get("scope") != "single-goal-autonomy-v1":
        raise ValueError("Single-goal actual validation permit is not accepted")
    paths = data.get("source_hashes", {})
    if not consumer_source_paths().issubset(paths):
        raise ValueError(
            "Single-goal permit does not include its execution and environment contract"
        )
    for relative, digest in paths.items():
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError("Single-goal permit escaped this checkout")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Single-goal consumer source changed after actual validation")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", required=True)
    parser.add_argument("--enable-single-goal", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    arguments = parser.parse_args()
    if not arguments.enable_single_goal or not 5 <= arguments.interval_seconds <= 300:
        raise ValueError("Explicit validated single-goal enablement and bounded interval required")
    require_verified_source()
    sys.path.insert(0, str(ROOT))
    from scripts.zhiyu_next import current_round, read_round

    directory = current_round(arguments.round)
    metadata, environment = read_round(directory)
    if Path(sys.prefix).resolve() != ROOT / ".venv":
        raise ValueError("Worker requires this extension's independent virtual environment")
    os.environ.update(environment)
    os.environ["ZHIYU_NEXT_WORKER_ENABLED"] = "true"
    os.environ.pop("ZHIYU_NEXT_ADMIN_DATABASE_URL", None)
    engine = create_database_engine(environment["DATABASE_URL"])
    require_zhiyu_next_engine(engine, check_connection=True)
    registration: dict[str, Any] = {
        "protocol": "zhiyu-next-single-goal-worker-v1",
        "pid": os.getpid(),
        "round": directory.name,
        "environment_id": metadata["environment_id"],
        "epoch_id": metadata["epoch_id"],
        "cwd": str(ROOT),
        "started_at": datetime.now(UTC).isoformat(),
        "interval_seconds": arguments.interval_seconds,
        "status": "RUNNING",
        "scope": "ALLOCATE_GOAL_ZERO_COST_ONLY",
    }
    registration_path = directory / f"worker-{os.getpid()}.json"
    registration_path.write_text(
        json.dumps(registration, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        while True:
            require_verified_source()
            require_zhiyu_next_engine(engine)
            try:
                result = drain_once(engine, DEMO_USER_ID, datetime.now(UTC))
                print(
                    json.dumps({"at": datetime.now(UTC).isoformat(), **result}, ensure_ascii=False),
                    flush=True,
                )
            except PolicyLifecycleError as error:
                # Stop new acceptance and retain all original actions. Never spin
                # through an authorization/integrity failure or invent new keys.
                registration.update(status="STOPPED_FOR_REVIEW", error_code=error.code)
                print(json.dumps({"status": "STOPPED_FOR_REVIEW", "code": error.code}), flush=True)
                return 1
            if arguments.once:
                return 0
            time.sleep(arguments.interval_seconds)
    finally:
        if registration["status"] == "RUNNING":
            registration["status"] = "STOPPED"
        registration["stopped_at"] = datetime.now(UTC).isoformat()
        registration_path.write_text(
            json.dumps(registration, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
