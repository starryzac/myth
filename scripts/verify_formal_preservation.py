"""Read-only formal-history and archived W0 proof check with a fresh evidence directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.db import models  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import create_database_engine  # noqa: E402
from app.db.settings import DatabaseSettings  # noqa: E402
from benchmark_reads import database_metadata, snapshot  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task", required=True, choices=["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8"]
    )
    parser.add_argument("--phase", required=True, choices=["before", "checkpoint", "after"])
    args = parser.parse_args()
    assert models.User.__table__ in Base.metadata.sorted_tables
    assert len(Base.metadata.tables) == 23, "The W0 baseline has exactly 23 mapped business tables"
    settings = DatabaseSettings()
    url = make_url(settings.database_url)
    if (url.host, url.port, url.database) != ("127.0.0.1", 54329, "bounded_funds"):
        raise ValueError("Unexpected formal target; no operation performed")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = ROOT / "docs/progress/evidence" / args.task / f"preservation-{args.phase}-{run_id}"
    directory.mkdir(parents=True, exist_ok=False)
    initial = ROOT / "docs/progress/evidence/W0/preflight/formal-before-models-loaded.json"
    baseline = json.loads(initial.read_text(encoding="utf-8"))
    assert baseline["total_rows"] == 408 and len(baseline["tables"]) == 23
    index_path = ROOT / "docs/progress/evidence/W0/artifact-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    archived_changed = [
        name for name, digest in index["files"].items() if sha(index_path.parent / name) != digest
    ]
    engine = create_database_engine(settings.database_url)
    result: dict[str, object] = {
        "status": "INCOMPLETE",
        "purpose": "READ_ONLY_FORMAL_AND_W0_ARCHIVE_PRESERVATION",
        "run_id": run_id,
        "task": args.task,
        "phase": args.phase,
        "database": url.database,
        "started_at": datetime.now(UTC).isoformat(),
        "tool_sha256": sha(Path(__file__)),
        "baseline_sha256": sha(initial),
        "w0_index_sha256": sha(index_path),
        "w0_archived_files_checked": len(index["files"]),
        "w0_archived_files_changed": archived_changed,
        "archive_scope": "W0 evidence files; active STATUS/HANDOFF are outside this archive check.",
    }
    code = 1
    try:
        current = snapshot(engine, directory / "snapshot.json.gz")
        metadata = database_metadata(engine)
        result.update(snapshot=current, metadata=metadata)
        unchanged = (
            current["sha256"] == baseline["sha256"] and current["tables"] == baseline["tables"]
        )
        result["unchanged_business_rows"] = unchanged
        result["audit_state"] = "LEGACY_UNAUDITED"
        assert unchanged and not archived_changed
        assert metadata["migration_heads"] == ["0007_external_bank_facts"]
        assert all(
            current["tables"][name]["rows"] == 0
            for name in (
                "audit_epochs",
                "audit_events",
                "audit_subject_snapshots",
            )
        )
        result["status"] = "PASSED"
        code = 0
    except Exception as error:
        result.update(status="FAILED", error_type=type(error).__name__, error=str(error))
    finally:
        engine.dispose()
        result["finished_at"] = datetime.now(UTC).isoformat()
        (directory / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(
        json.dumps(
            {
                "status": result["status"],
                "directory": str(directory),
                "unchanged_business_rows": result.get("unchanged_business_rows"),
                "w0_archived_files_checked": result["w0_archived_files_checked"],
                "w0_archived_files_changed": archived_changed,
            },
            ensure_ascii=False,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
