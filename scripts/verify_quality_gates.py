"""Exercise the real lint target with temporary faulty source, then restore it."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
probe = ROOT / "apps/api/app/_quality_gate_probe.py"
if probe.exists():
    raise SystemExit("Probe already exists; refusing to overwrite")
records = []
try:
    for name, source, expected in [
        ("unused_import", "import os\n", "F401"),
        ("wrong_type", 'amount: int = "bad"\n', "[assignment]"),
    ]:
        probe.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "scripts/tasks.py", "lint"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = result.stdout + result.stderr
        passed = result.returncode != 0 and expected in output
        records.append(
            {"probe": name, "exit_code": result.returncode, "detected": passed, "output": output}
        )
        if not passed:
            raise RuntimeError(f"Quality gate did not detect {name}: {output}")
finally:
    probe.unlink(missing_ok=True)
    destination = ROOT / "docs/progress/evidence/MVP-002-negative-gates.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "run_id": str(uuid4()),
                "timestamp": datetime.now(UTC).isoformat(),
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
print("Both injected faults were rejected by the real lint target.")
