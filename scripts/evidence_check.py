"""Revalidate explicit original evidence through the existing semantic byte exporter."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def evidence_command(manifest: Path, output: Path) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts/export_evidence.py"),
        "--manifest",
        str(manifest),
        "--output",
        str(output),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    manifest = args.manifest or os.environ.get("BOUNDEDFUNDS_EVIDENCE_REQUEST")
    if not manifest:
        print(
            "REJECTED: explicit evidence request is required; no newest file selected",
            file=sys.stderr,
        )
        return 2
    output = args.output or ROOT / ".runtime/evidence-check" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    # The original exporter enforces root/path/hash/current-source/run/18-group semantics.
    # Its native stdout/status and exit are retained, not translated into another PASS flag.
    return subprocess.run(
        evidence_command(Path(manifest), output), cwd=ROOT, check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
