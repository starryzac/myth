"""Private isolated-harness stdin RPC. This does not add browser clock/amount/fault inputs."""

import json
import sys
from datetime import UTC, datetime
from io import TextIOWrapper
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.db.session import create_database_engine  # noqa: E402
from app.db.settings import DatabaseSettings  # noqa: E402
from app.services.demo_seed import DEMO_USER_ID  # noqa: E402
from app.services.scenario_runner import ScenarioRunner  # noqa: E402
from app.services.scenario_types import ScenarioRPC  # noqa: E402


def main() -> int:
    cast(TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    cast(TextIOWrapper, sys.stderr).reconfigure(encoding="utf-8", errors="replace")
    body = ScenarioRPC.model_validate_json(sys.stdin.readline())
    engine = create_database_engine(DatabaseSettings().database_url)
    try:
        result = ScenarioRunner(engine, DEMO_USER_ID).rpc(body, datetime.now(UTC))
        print(
            json.dumps(
                {
                    "protocol": body.protocol,
                    "scenario_id": body.scenario_id,
                    "status": "PASSED" if result.get("all_passed", True) else "PROPERTY_FAILED",
                    "operation": body.operation,
                    "result": result,
                },
                ensure_ascii=False,
            )
        )
        return 0 if result.get("all_passed", True) else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
