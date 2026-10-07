"""Generate the API schema and browser types from the installed application."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps/api"))
from app.main import app  # noqa: E402

schema = json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
node = shutil.which("node")
cli = root / "node_modules/openapi-typescript/bin/cli.js"
if not node or not cli.is_file():
    raise SystemExit("Install Node.js and workspace dependencies before generating types")
result = subprocess.run(
    [node, str(cli)], input=schema, text=True,
    encoding="utf-8", capture_output=True, cwd=root,
)
if result.returncode:
    raise SystemExit(result.stderr or result.stdout or "API type generation failed")
generated = result.stdout
directory = root / "packages/contracts"
directory.mkdir(parents=True, exist_ok=True)
(directory / "openapi.json").write_text(schema, encoding="utf-8")
(directory / "schema.d.ts").write_text(generated, encoding="utf-8")
print("Updated API schema and browser types.")
