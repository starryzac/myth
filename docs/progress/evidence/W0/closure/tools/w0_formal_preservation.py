"""W0 formal simulation read-only preservation proof; no seed/reset/write paths."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "apps/api"))

from app.db.session import create_database_engine
from app.db.settings import DatabaseSettings
from benchmark_reads import database_metadata, snapshot, source_state, write_json
from sqlalchemy.engine import make_url


def main() -> int:
    phase = sys.argv[1]
    if phase not in {"checkpoint", "final"}:
        raise ValueError("Only checkpoint/final read-only proof allowed")
    folder = ROOT / "docs/progress/evidence/W0" / f"formal-{phase}"
    folder.mkdir(exist_ok=False)
    settings = DatabaseSettings()
    url = make_url(settings.database_url)
    if url.database != "bounded_funds" or url.host != "127.0.0.1" or url.port != 54329:
        raise ValueError("Unexpected formal target; no operation performed")
    baseline_path = ROOT / "docs/progress/evidence/W0/preflight/formal-before-models-loaded.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline["total_rows"] != 408 or len(baseline["tables"]) != 23:
        raise ValueError("Initial model snapshot is incomplete")
    engine = create_database_engine(settings.database_url)
    result = {
        "status": "INCOMPLETE",
        "purpose": "W0_FORMAL_SIMULATION_READ_ONLY_PRESERVATION",
        "started_at": datetime.now(UTC).isoformat(),
        "database": url.database,
        "baseline_file_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source": source_state(ROOT / "apps/api"),
    }
    try:
        current = snapshot(engine, folder / "snapshot.json.gz")
        metadata = database_metadata(engine)
        result.update(snapshot=current, metadata=metadata)
        result["unchanged_business_rows"] = current["sha256"] == baseline["sha256"]
        result["unchanged_each_table"] = current["tables"] == baseline["tables"]
        result["audit_state"] = "LEGACY_UNAUDITED"
        result["audit_rows"] = {
            name: current["tables"][name]["rows"]
            for name in ("audit_epochs", "audit_events", "audit_subject_snapshots")
        }
        assert result["unchanged_business_rows"] and result["unchanged_each_table"]
        assert set(result["audit_rows"].values()) == {0}
        # The preflight records 0007; no W0 migration is authorized.
        assert metadata["migration_heads"] == ["0007_external_bank_facts"]
        checkpoint = ROOT / "docs/progress/evidence/W0/formal-checkpoint/result.json"
        if phase == "final" and checkpoint.is_file():
            first = json.loads(checkpoint.read_text(encoding="utf-8"))
            assert first["status"] == "PASSED"
            assert metadata["migration_heads_sha256"] == first["metadata"]["migration_heads_sha256"]
            result["unchanged_migration_metadata_since_checkpoint"] = True
        result["status"] = "PASSED"
        return 0
    except BaseException as error:
        result.update(status="FAILED", error_type=type(error).__name__, error=str(error))
        return 1
    finally:
        engine.dispose()
        result["finished_at"] = datetime.now(UTC).isoformat()
        write_json(folder / "result.json", result)
        print(json.dumps({key: result.get(key) for key in (
            "status", "database", "unchanged_business_rows", "unchanged_each_table", "audit_rows", "error_type", "error"
        )}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
