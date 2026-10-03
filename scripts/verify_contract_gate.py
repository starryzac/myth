"""Negative tests of the public typecheck target; restore generated files."""

import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
records = []
for name, message in [
    ("openapi.json", "OpenAPI drift"),
    ("schema.d.ts", "TypeScript contract drift"),
]:
    artifact = root / "packages/contracts" / name
    original = artifact.read_bytes()
    try:
        artifact.write_text("{}\n" if name.endswith("json") else "// stale\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "scripts/tasks.py", "typecheck"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = result.stdout + result.stderr
        passed = result.returncode != 0 and message in output
        records.append(
            {"artifact": name, "exit_code": result.returncode, "detected": passed, "output": output}
        )
        if not passed:
            raise RuntimeError(f"Undetected contract drift: {name}\n{output}")
    finally:
        artifact.write_bytes(original)
        (root / "docs/progress/evidence/MVP-003-contract-gates.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
print("Both stale contracts were rejected by typecheck.")
