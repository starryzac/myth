"""Export the real API schema; pnpm types generates the browser contract."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from app.main import app

root = Path(__file__).resolve().parents[1]
destination = root / "packages/contracts/openapi.json"
schema = json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
checking = "--check" in sys.argv
if checking and (not destination.exists() or destination.read_text(encoding="utf-8") != schema):
    raise SystemExit("OpenAPI drift: run make types")
pnpm = shutil.which("pnpm")
if not pnpm:
    raise SystemExit("pnpm is required")
# Keep the exact generated contract in the ignored run directory for diagnosis.
# Windows restricted tokens cannot access tempfile's owner-only mkdir(0700).
temporary = root / ".runtime" / "contracts" / uuid4().hex
temporary.mkdir(parents=True, exist_ok=False)
temporary_json = Path(temporary) / "openapi.json"
temporary_ts = Path(temporary) / "schema.d.ts"
temporary_json.write_text(schema, encoding="utf-8")
generated = subprocess.run(
    [pnpm, "exec", "openapi-typescript"],
    input=schema,
    text=True,
    encoding="utf-8",
    capture_output=True,
    check=True,
    cwd=root,
).stdout
temporary_ts.write_text(generated, encoding="utf-8")
types = destination.with_name("schema.d.ts")
if checking:
    if not types.exists() or types.read_text(encoding="utf-8") != generated:
        raise SystemExit("TypeScript contract drift: run make types")
    print("OpenAPI and TypeScript contracts match the current API.")
else:
    destination.write_text(schema, encoding="utf-8")
    types.write_text(generated, encoding="utf-8")
    print("Updated OpenAPI and TypeScript contracts.")
