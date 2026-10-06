"""Run the installed Ruff security rules on production API source, without importing it."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def security_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--select",
        "S",
        "--output-format",
        "json",
        "--extend-exclude",
        "apps/api/app/tests",
        "--force-exclude",
        "apps/api/app",
    ]


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    result = subprocess.run(security_command(), cwd=ROOT, check=False)
    print(flush=True)
    print(
        json.dumps(
            {
                "protocol": "bounded-funds-security-command-v1",
                "exit_code": result.returncode,
                "scope": "RUFF_S_STATIC_PRODUCTION_API_ONLY",
                "dependency_vulnerability_scan": "NOT_RUN",
                "penetration_test": "NOT_RUN",
                "financial_risk_integration": "NOT_RUN_BY_THIS_COMMAND",
                "task_closed": False,
            }
        ),
        flush=True,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
