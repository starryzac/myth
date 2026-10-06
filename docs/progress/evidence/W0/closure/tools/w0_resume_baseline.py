"""Reuse immutable passed measurements, rerun failed fixture into a new manifest."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "apps/api"))
import benchmark_reads as benchmark


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    prior_path = ROOT / "docs/progress/evidence/W0/performance-20261004T225248Z-a5290990/manifest.json"
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    assert prior["purpose"] == benchmark.PURPOSE and prior["status"] == "BASELINE_FAILED"
    old_tool = prior_path.parent / "benchmark-tool-at-run.py"
    new_tool = ROOT / "scripts/benchmark_reads.py"
    # Exact fixture-only textual change. All read/measurement/instrumentation
    # routines remain byte-equivalent after newline normalization.
    assert new_tool.read_text(encoding="utf-8").replace(
        'counterparty_ref="payroll" if kind == "INCOME" else "merchant",',
        'counterparty_ref="w0-fixed-simulated-fixture",',
    ) == old_tool.read_text(encoding="utf-8")
    source = Path(prior["baseline_source_root"])
    state = benchmark.source_state(source)
    folder = benchmark.require_output(ROOT / "docs/progress/evidence/W0" / (
        "performance-fixture-repair-"+datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid4().hex[:8]))
    folder.mkdir(exist_ok=False)
    manifest = {
        "purpose":benchmark.PURPOSE, "started_at":benchmark.utc(), "status":"INCOMPLETE",
        "baseline_source_root":str(source), "repeats":prior["repeats"], "expanded_rounds":prior["expanded_rounds"],
        "formal_demo_database_touched":False, "scenarios":[],
        "prior_failed_manifest":str(prior_path), "prior_failed_manifest_sha256":sha(prior_path),
        "fixture_only_repair":"Use already-opened payroll/merchant clearing; no capital/bootstrap or production change",
        "old_tool_sha256":sha(old_tool), "new_tool_sha256":sha(new_tool),
        "measurement_routines_unchanged":True, "coordinator_proof_tool_sha256":sha(Path(__file__)),
    }
    for scene in prior["scenarios"][:2]:
        baseline = Path(scene["baseline"])
        old = json.loads(baseline.read_text(encoding="utf-8"))
        assert old["status"] == "PASSED" and scene["baseline_exit_code"] == 0
        assert old["source_before"]["tool_sha256"] == sha(old_tool)
        assert old["data_before"]["sha256"] == old["data_after"]["sha256"]
        for key in ("source_fingerprint", "dependency_lock_sha256", "git_head"):
            assert old["source_before"][key] == old["source_after"][key] == state[key]
        manifest["scenarios"].append({**scene, "reused_passed_measurement":True,
                                     "retained_result_sha256":sha(baseline), "retained_fixture_sha256":sha(baseline.parent/"fixture.json")})
    expanded = {"scenario":"expanded-fixed-history", "database":"bf_test_"+uuid4().hex,
                "reused_passed_measurement":False}
    manifest["scenarios"].append(expanded)
    output = folder / "baseline" / expanded["scenario"]
    path = folder / "manifest.json"
    benchmark.write_json(path,manifest)
    print("W0 repaired manifest: "+str(path),flush=True)
    code = benchmark.launch_worker([
        "--create", "--database",expanded["database"], "--scenario",expanded["scenario"],
        "--repeats",str(manifest["repeats"]), "--expanded-rounds",str(manifest["expanded_rounds"])
    ],source,output)
    expanded.update(baseline=str(output/"result.json"),baseline_exit_code=code)
    manifest["status"] = "BASELINE_FAILED" if code else "BASELINE_PASSED_CANDIDATE_PENDING"
    benchmark.write_json(path,manifest)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
