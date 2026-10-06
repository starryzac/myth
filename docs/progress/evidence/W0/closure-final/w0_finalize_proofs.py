"""Bind measured W0 closure without editing source, old failures, or databases."""

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
W0 = ROOT / "docs/progress/evidence/W0"
RUN = W0 / "performance-fixture-repair-20261004T232505Z-5daa2f9b"
OUT = W0 / "closure-final"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def proof():
    assert not OUT.exists()
    full_map = yaml.safe_load((ROOT / "docs/spec/full-delta-map.yaml").read_text(encoding="utf-8"))
    assert len(full_map["requirements"]) == 67
    assert len({item["id"] for item in full_map["requirements"]}) == 67
    assert all(item["status"] == "PENDING" for item in full_map["requirements"])
    manifest_path = RUN / "manifest.json"
    manifest = load(manifest_path)
    assert manifest["status"] == "SAME_DATA_COMPARISON_PASSED"
    old_path = Path(manifest["prior_failed_manifest"])
    assert sha(old_path) == manifest["prior_failed_manifest_sha256"]
    assert load(old_path)["status"] == "BASELINE_FAILED"
    assert sha(ROOT / "scripts/benchmark_reads.py") == manifest["new_tool_sha256"]
    assert sha(old_path.parent / "benchmark-tool-at-run.py") == manifest["old_tool_sha256"]
    result_paths = []
    pairs = []
    expected_entries = {}
    for item in manifest["scenarios"]:
        base_path = Path(item["baseline"])
        candidate_path = RUN / manifest["latest_candidate"] / item["scenario"] / "result.json"
        base, candidate = load(base_path), load(candidate_path)
        assert base["status"] == candidate["status"] == "PASSED"
        assert base["audit_status"] == candidate["audit_status"] == "VALID"
        assert base["data_before"]["sha256"] == base["data_after"]["sha256"] == candidate["data_before"]["sha256"] == candidate["data_after"]["sha256"]
        assert base["database_before"]["migration_heads_sha256"] == base["database_after"]["migration_heads_sha256"] == candidate["database_before"]["migration_heads_sha256"] == candidate["database_after"]["migration_heads_sha256"]
        response_hashes = {result["readings"][path]["response_sha256"] for result in (base, candidate) for path in ("api-get", "repeatable-read-service")}
        assert len(response_hashes) == 1
        result_paths.extend((base_path, candidate_path))
        pairs.append((item["scenario"], base, candidate))
        for phase, path, result in (("baseline", base_path, base), ("candidate", candidate_path, candidate)):
            expected_entries[(item["scenario"], phase)] = (path, result)
    assert len(expected_entries) == 6
    formal_path = W0 / "formal-final/result.json"
    cleanup_path = W0 / "cleanup-final/result.json"
    assert load(formal_path)["status"] == "PASSED"
    assert load(cleanup_path)["status"] == "PASSED"
    assert load(cleanup_path)["original_run_manifests_unchanged"] is True

    closure_path = W0 / "closure/manifest.json"
    closure = load(closure_path)
    assert closure["status"] == "PASSED"
    assert len(closure["source_files"]) == 293
    assert len(closure["reviewable_local_proof_tools_sha256"]) == 6
    for name, expected in closure["source_files"].items():
        assert sha(ROOT / name) == sha(W0 / "closure/source" / name) == expected, name
    for name, expected in closure["reviewable_local_proof_tools_sha256"].items():
        assert sha(ROOT / ".runtime" / name) == sha(W0 / "closure/tools" / name) == expected, name
    for name, expected in closure["mvp404_frontend_contract_and_progress_sha256"].items():
        assert sha(ROOT / name) == expected, name
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == closure["git_head"]

    report_path = W0 / "performance-results.md"
    binding_path = W0 / "performance-report-binding.json"
    binding = load(binding_path)
    assert binding["status"] == "PASSED" and len(binding["entries"]) == 6
    keys = [(item["scene"], item["phase"]) for item in binding["entries"]]
    assert len(set(keys)) == 6 and set(keys) == set(expected_entries)
    for entry in binding["entries"]:
        path, result = expected_entries[(entry["scene"], entry["phase"])]
        assert (ROOT / entry["result"]).resolve() == path.resolve()
        assert entry["result_sha256"] == sha(path)
        assert entry["source_fingerprint"] == result["source_before"]["source_fingerprint"]
        assert entry["data_sha256"] == result["data_before"]["sha256"]
    assert sha(report_path) == binding["report_sha256"]
    report = report_path.read_text(encoding="utf-8")
    assert report.count("审计原文 UTF-8 字节") == 1
    report = report.replace("审计原文 UTF-8 字节", "存储审计 canonical 文本字节")
    report += "\n## 收尾绑定补充\n\n"
    report += "上述字节指标来自数据库 octet_length；未单独记录 server_encoding，因此不进一步宣称其字符编码。每场景 API 与服务、基线与候选的四份响应 SHA 均相同；固定 as_of 见各 fixture.json。\n\n"
    report += f"原工具 SHA `{manifest['old_tool_sha256']}`；最终工具 SHA `{manifest['new_tool_sha256']}`。当前及原失败 manifest 的路径和 SHA 另绑定于 [报告绑定](performance-report-binding.json)，原失败状态不更改。\n"
    report += "\n## 本次单样本中增加的成本\n\n"
    for scene, base, candidate in pairs:
        increases = []
        for path in ("api-get", "repeatable-read-service"):
            b = base["readings"][path]["normal_median_wall_seconds"]
            c = candidate["readings"][path]["normal_median_wall_seconds"]
            if c > b:
                increases.append(f"{path} 正常耗时 {(c / b - 1) * 100:+.2f}%")
        for label, getter in (
            ("API SQL 次数", lambda result: result["readings"]["api-get"]["query_count"]),
            ("API canonical_bytes 次数", lambda result: result["readings"]["api-get"]["functions"]["python_call_counts"].get("app.domain.audit_chain.canonical_bytes", 0)),
            ("请求 Python 分配峰值", lambda result: result["profiling"]["python_peak_allocated_bytes"]),
        ):
            b, c = getter(base), getter(candidate)
            if c > b:
                increases.append(f"{label} {b} → {c}（{(c / b - 1) * 100:+.2f}%）")
        report += f"- {scene}：{'；'.join(increases) if increases else '上述指标未观测到增加'}。\n"
    report += "\n这些结果含局部下降与增加，尚未测得稳定延迟、因果归属或总体算力节省。\n"
    report_path.write_text(report, encoding="utf-8")
    binding.update({
        "report_sha256": sha(report_path),
        "reporting_amendment_tool_sha256": sha(Path(__file__)),
        "manifest": {"path": str(manifest_path.relative_to(ROOT)), "sha256": sha(manifest_path)},
        "prior_failed_manifest": {"path": str(old_path.relative_to(ROOT)), "sha256": sha(old_path)},
        "api_service_and_cross_version_response_hashes_equal": True,
    })
    write(binding_path, binding)
    OUT.mkdir(exist_ok=False)
    shutil.copy2(Path(__file__), OUT / Path(__file__).name)
    assert sha(OUT / Path(__file__).name) == sha(Path(__file__))
    paths = [manifest_path, old_path, formal_path, cleanup_path, closure_path, report_path, binding_path, *result_paths]
    result = {
        "status": "PASSED", "purpose": "W0_FINAL_MEASURED_CLOSURE_BINDING",
        "observed_at": datetime.now(UTC).isoformat(), "git_head": head,
        "archived_source_files_revalidated": 293, "archived_helpers_revalidated": 6,
        "mvp404_files_revalidated": len(closure["mvp404_frontend_contract_and_progress_sha256"]),
        "all_six_measurements_passed": True, "same_data_and_migration_hashes": True,
        "all_four_response_hashes_per_scenario_equal": True,
        "formal_preservation_passed": True, "owned_benchmark_cleanup_passed": True,
        "old_failed_manifest_status_preserved": "BASELINE_FAILED",
        "full_requirement_statuses_unchanged": "67_PENDING",
        "old_untracked_evidence_limit": closure["old_untracked_evidence_limit"],
        "source_closure_sha256": sha(closure_path), "tool_sha256": sha(Path(__file__)),
        "bound_artifacts_sha256": {str(path.relative_to(ROOT)): sha(path) for path in paths},
    }
    write(OUT / "manifest.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def index():
    result = load(OUT / "manifest.json")
    assert result["status"] == "PASSED"
    for name, expected in result["bound_artifacts_sha256"].items():
        assert sha(ROOT / name) == expected, name
    index_path = W0 / "artifact-index.json"
    assert not index_path.exists()
    files = {str(path.relative_to(W0)).replace("\\", "/"): sha(path) for path in sorted(W0.rglob("*")) if path.is_file() and path != index_path}
    documents = [ROOT / "docs/progress" / name for name in ("W0.md", "STATUS.md", "HANDOFF.md")]
    write(index_path, {
        "status": "PASSED", "purpose": "W0_EVIDENCE_FILE_SHA256_INDEX",
        "observed_at": datetime.now(UTC).isoformat(), "file_count": len(files),
        "scope": "All current W0 evidence files excluding this self-referential index; source closure and original failure limitations are preserved",
        "files": files,
        "final_documents_sha256": {str(path.relative_to(ROOT)): sha(path) for path in documents},
    })
    print(json.dumps({"status": "PASSED", "file_count": len(files), "index_sha256": sha(index_path)}))


if __name__ == "__main__":
    mode = sys.argv[1]
    assert mode in {"proof", "index"}
    proof() if mode == "proof" else index()
