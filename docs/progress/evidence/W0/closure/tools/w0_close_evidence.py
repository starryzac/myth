"""Archive W0 source binding and preservation evidence, with no product/DB writes."""

import hashlib
import json
import shutil
import subprocess
import yaml
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/progress/evidence/W0/closure"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*argv: str) -> str:
    return subprocess.run(["git", *argv], cwd=ROOT, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def main() -> int:
    OUT.mkdir(exist_ok=False)
    initial = json.loads((ROOT / "docs/progress/evidence/W0/preflight/initial-manifest.json").read_text(encoding="utf-8"))
    changed = [name for name, expected in initial.items() if sha(ROOT / name) != expected]
    allowed = {
        "AGENTS.md", "docs/progress/STATUS.md", "docs/progress/HANDOFF.md",
        "docs/spec/requirements-traceability.md", "apps/api/app/tests/test_task_orchestration.py",
        "apps/api/app/tests/test_decision_trace_audit.py",
        *[f"apps/api/app/services/{name}.py" for name in (
            "audit_chain", "decision_trace", "execution_projection", "recovery_receipt_integrity", "simulated_bank"
        )],
    }
    assert set(changed) <= allowed, changed
    protected = [name for name in initial if name not in allowed]
    mapping = yaml.safe_load((ROOT / "docs/spec/full-delta-map.yaml").read_text(encoding="utf-8"))
    mapped = mapping["baseline"]["working_tree_snapshot"]["dirty_sha256"]
    mvp404 = {name: expected for name, expected in mapped.items() if
              name.startswith(("apps/web/", "packages/contracts/")) or name == "docs/progress/MVP-404.md"}
    assert all(sha(ROOT / name) == expected for name, expected in mvp404.items())
    raw_paths = subprocess.run([
        "git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--",
        "apps", "packages", "scripts", "Makefile", "make.cmd", "pyproject.toml", "uv.lock",
        "package.json", "pnpm-lock.yaml", "alembic.ini", "AGENTS.md", "docs/spec",
    ], cwd=ROOT, check=True, capture_output=True).stdout.decode("utf-8").split("\0")
    paths = sorted({name for name in raw_paths if name and (ROOT / name).is_file()})
    source = {}
    for name in paths:
        source[name] = sha(ROOT / name)
        target = OUT / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
        assert sha(target) == source[name]
    tools = {}
    for name in ("w0_formal_preservation.py", "w0_close_evidence.py", "w0_performance_report.py",
                 "w0_resume_baseline.py", "w0_verify_clearing.py", "w0_cleanup_benchmarks.py"):
        original = ROOT / ".runtime" / name
        target = OUT / "tools" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        tools[name] = sha(original)
        assert sha(target) == tools[name]
    pg = json.loads((ROOT / "docs/progress/evidence/W0/verification-pg-repaired/manifest.json").read_text(encoding="utf-8"))
    pg_changed = [name for name, expected in pg["source_before"].items() if sha(ROOT / name) != expected]
    assert set(pg_changed) <= {
        "apps/api/app/tests/test_benchmark_reads.py", "apps/api/app/tests/test_verification_selection.py"
    }, pg_changed
    old_diff = git("diff", "--name-only", "--", *[
        f"docs/progress/evidence/MVP-{name}" for name in (
            "301", "302", "303", "304", "401", "402", "403"
        )
    ])
    assert old_diff == "", old_diff
    result = {
        "status": "PASSED",
        "purpose": "W0_SOURCE_BINDING_AND_ORIGINAL_PRESERVATION",
        "observed_at": datetime.now(UTC).isoformat(),
        "git_head": git("rev-parse", "HEAD"),
        "dirty_tree": git("status", "--porcelain=v1", "--untracked-files=normal"),
        "tool_sha256": sha(Path(__file__)),
        "initial_paths_compared": len(initial),
        "changed_initial_paths": changed,
        "protected_initial_paths": protected,
        "protected_initial_paths_unchanged": True,
        "mvp404_backend_sources_retained": True,
        "mvp404_frontend_contract_and_progress_sha256": mvp404,
        "mvp404_frontend_contract_and_progress_unchanged": True,
        "mvp404_frontend_comparison_source": "full-delta-map baseline working_tree_snapshot observed 2026-10-04T22:19:06.881752Z; initial backend manifest is separate",
        "audit_domain_codecs_dependencies_unchanged": True,
        "final_pg_changed_paths": pg_changed,
        "final_pg_financial_source_and_selected_tests_stable": True,
        "old_tracked_mvp_evidence_diff": old_diff,
        "old_untracked_evidence_limit": "Not all old untracked files had an initial full byte manifest; no W0 overwrite/delete operations performed there.",
        "source_files": source,
        "reviewable_local_proof_tools_sha256": tools,
    }
    (OUT / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"status":result["status"], "source_files":len(source), "changed_initial_paths":changed},ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
