"""Review/apply exact owned W0 benchmark DB cleanup without editing run manifests."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "apps/api"))
import benchmark_reads as benchmark
from app.db.settings import DatabaseSettings
from sqlalchemy.engine import make_url

OLD = ROOT / "docs/progress/evidence/W0/performance-20261004T225248Z-a5290990/manifest.json"
NEW = ROOT / "docs/progress/evidence/W0/performance-fixture-repair-20261004T232505Z-5daa2f9b/manifest.json"
OUT = ROOT / "docs/progress/evidence/W0/cleanup-final"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    mode = sys.argv[1]
    assert mode in {"review", "apply"}
    url = make_url(DatabaseSettings().database_url)
    assert (url.host, url.port, url.database) == ("127.0.0.1", 54329, "bounded_funds")
    if mode == "review":
        OUT.mkdir(exist_ok=False)
        old = json.loads(OLD.read_text(encoding="utf-8"))
        new = json.loads(NEW.read_text(encoding="utf-8"))
        assert old["purpose"] == new["purpose"] == benchmark.PURPOSE
        assert old["status"] == "BASELINE_FAILED"
        assert new["status"] == "SAME_DATA_COMPARISON_PASSED"
        assert new["prior_failed_manifest_sha256"] == sha(OLD)
        names = sorted({benchmark.require_database_name(item["database"]) for manifest in (old,new) for item in manifest["scenarios"]})
        assert len(names) == 4
        review = {"status":"EXACT_TARGETS_REVIEW_REQUIRED", "databases":names,
                  "manifests_sha256":{str(path.relative_to(ROOT)):sha(path) for path in (OLD,NEW)},
                  "formal_database_excluded":True, "tool_sha256":sha(Path(__file__))}
        benchmark.write_json(OUT/"reviewed-targets.json",review)
        print(json.dumps(review,ensure_ascii=False,indent=2))
        return
    review = json.loads((OUT/"reviewed-targets.json").read_text(encoding="utf-8"))
    assert review["tool_sha256"] == sha(Path(__file__))
    assert all(sha(ROOT/name)==expected for name,expected in review["manifests_sha256"].items())
    old = json.loads(OLD.read_text(encoding="utf-8"))
    new = json.loads(NEW.read_text(encoding="utf-8"))
    assert old["purpose"] == new["purpose"] == benchmark.PURPOSE
    assert old["status"] == "BASELINE_FAILED" and new["status"] == "SAME_DATA_COMPARISON_PASSED"
    derived = {benchmark.require_database_name(item["database"]) for manifest in (old,new) for item in manifest["scenarios"]}
    assert len(derived) == len(review["databases"]) == 4
    assert derived == set(review["databases"])
    assert json.loads((ROOT/"docs/progress/evidence/W0/formal-final/result.json").read_text(encoding="utf-8"))["status"] == "PASSED"
    result = {"status":"INCOMPLETE", "review_sha256":sha(OUT/"reviewed-targets.json"), "databases":[]}
    benchmark.write_json(OUT/"result.json",result)
    for name in review["databases"]:
        benchmark.cleanup_database(benchmark.require_database_name(name))
        result["databases"].append({"database":name,"drop_exit_status":"PASSED"})
        benchmark.write_json(OUT/"result.json",result)
    assert all(sha(ROOT/name)==expected for name,expected in review["manifests_sha256"].items())
    result["status"] = "PASSED"
    result["original_run_manifests_unchanged"] = True
    benchmark.write_json(OUT/"result.json",result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
