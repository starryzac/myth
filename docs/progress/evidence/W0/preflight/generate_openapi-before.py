"""Export the real API schema; pnpm types generates the browser contract."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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
with tempfile.TemporaryDirectory(prefix="bounded-contracts-") as temporary:
    temporary_json = Path(temporary) / "openapi.json"
    temporary_ts = Path(temporary) / "schema.d.ts"
    temporary_json.write_text(schema, encoding="utf-8")
    subprocess.run(
        [pnpm, "exec", "openapi-typescript", str(temporary_json), "-o", str(temporary_ts)],
        check=True,
        cwd=root,
    )
    generated = temporary_ts.read_text(encoding="utf-8")
    types = destination.with_name("schema.d.ts")
    if checking:
        if not types.exists() or types.read_text(encoding="utf-8") != generated:
            raise SystemExit("TypeScript contract drift: run make types")
        print("OpenAPI and TypeScript contracts match the current API.")
    else:
        destination.write_text(schema, encoding="utf-8")
        types.write_text(generated, encoding="utf-8")
        print("Updated OpenAPI and TypeScript contracts.")
