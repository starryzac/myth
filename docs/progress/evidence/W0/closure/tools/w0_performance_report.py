"""Render measured W0 evidence; unfinished rows stay unfinished."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT / "docs/progress/evidence/W0/performance-20261004T225248Z-a5290990"
SCENES = ("short-seeded", "mvp401-long-chain", "expanded-fixed-history")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cells(result, key):
    values = []
    for path in ("api-get", "repeatable-read-service"):
        reading = result["readings"][path]
        value = reading[key]
        values.append(f"{value:.6f}" if isinstance(value, float) else str(value))
    return " / ".join(values)


def calls(result, name):
    return " / ".join(str(result["readings"][path]["functions"]["python_call_counts"].get(name, 0))
                      for path in ("api-get", "repeatable-read-service"))


def main():
    manifest = load(RUN / "manifest.json")
    entries = []
    for scene in SCENES:
        for phase in ("baseline", "candidate"):
            directory = "baseline" if phase == "baseline" else manifest.get("latest_candidate", "candidate")
            metadata = next(item for item in manifest["scenarios"] if item["scenario"] == scene)
            path = Path(metadata["baseline"]) if phase == "baseline" and metadata.get("baseline") else RUN / directory / scene / "result.json"
            result = load(path)
            entries.append((scene, phase, path, result))
    passed = all(result and result["status"] == "PASSED" for _, _, _, result in entries)
    lines = [
        "# W0 三个固定场景性能实测", "",
        f"生成：{datetime.now(UTC).isoformat()}。状态：{'完整对照已取得' if passed else 'IN_PROGRESS，未取得完整对照'}。",
        "本报告只从本次原结果生成；每路径 n=1，所有比例均为单样本局部对照。没有 P95/P99、稳定加速倍数或延迟 SLA 结论。", "",
        f"[原运行清单]({RUN.name}/manifest.json)；[方法与限制复核](performance-method-review.md)。基线加载执行前冻结源码，候选复用同一场景数据库与时钟，不重新 seed。", "",
        "本清单显式复用原短/长 PASSED 结果和哈希，原扩大夹具失败清单保留；工具唯一文本差异为扩大夹具 counterparty 改用 seed 已开户 payroll/merchant，测量代码未变。" if manifest.get("measurement_routines_unchanged") else "", "",
        "## 正常读取和数据规模", "",
        "API 墙钟为真实 TestClient GET 至响应收到；服务墙钟含新 RR/READ ONLY Session、服务及产品 model_dump。均排除客户端 JSON 解码/证据哈希和诊断插桩。", "",
        "| 场景 | 版本/结果 | 23 表业务行数 | 审计原文 UTF-8 字节 | API / 服务正常秒 |", "| --- | --- | ---: | ---: | --- |",
    ]
    records = []
    for scene, phase, path, result in entries:
        if not result or result["status"] != "PASSED":
            lines.append(f"| {scene} | {phase} / {result['status'] if result else 'NOT_MEASURED'} | — | — | — |")
            continue
        lines.append(f"| {scene} | [{phase} / PASSED]({path.relative_to(RUN.parent).as_posix()}) | {result['data_before']['total_rows']} | {result['database_before']['stored_original_canonical_bytes']} | {cells(result, 'normal_median_wall_seconds')} |")
        records.append({"scene":scene, "phase":phase, "result_sha256":sha(path), "result":str(path.relative_to(ROOT)),
                        "source_fingerprint":result["source_before"]["source_fingerprint"], "data_sha256":result["data_before"]["sha256"]})
    lines += ["", "## 独立诊断请求的重复工作", "",
              "以下均按 API / 服务分列，不与正常秒数混合。SQL 包括事务设置和容量查询。返回行数累积重复查询及聚合结果，不能当唯一业务规模。模型次数仅 Python BaseModel 入口，C 层嵌套实例数未观测。canonical 核心次数不与其嵌套入口相加。", "",
              "| 场景/版本 | SQL 次数 | SQL 驱动区间合计秒 | 累计已知返回行 | canonical_bytes 次数 | 模型 Python 入口 | ledger_heads 次数 |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for scene, phase, _, result in entries:
        if not result or result["status"] != "PASSED":
            continue
        models = " / ".join(str(result["readings"][path]["functions"]["pydantic_python_entry_calls"])
                            for path in ("api-get", "repeatable-read-service"))
        lines.append(f"| {scene}/{phase} | {cells(result,'query_count')} | {cells(result,'sql_wall_seconds')} | {cells(result,'known_driver_rows')} | {calls(result,'app.domain.audit_chain.canonical_bytes')} | {models} | {calls(result,'app.services.simulated_bank.ledger_heads')} |")
    lines += ["", "## 独立 CPU 与内存 profile", "",
              "CPU 请求有 cProfile 开销，含数据库等待；内存请求有 tracemalloc 开销。Python 峰值不含原生或 DB 服务端分配。进程工作集为 worker 生命周期高水位，包含导入、夹具、快照与 profiles；基线构造夹具而候选不构造，因此不计算请求内原生内存优化比例。原 pstats、文本与 tracemalloc 文件保留在各结果目录。", "",
              "| 场景/版本 | CPU profile 墙钟秒 | 内存 profile 墙钟秒 | 请求 Python 分配峰值 bytes | 进程生命周期工作集峰值 bytes |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for scene, phase, _, result in entries:
        if not result or result["status"] != "PASSED":
            continue
        p = result["profiling"]
        m = result["process_memory_after_reads"]
        lines.append(f"| {scene}/{phase} | {p['cpu_service_wall_seconds']:.6f} | {p['memory_service_wall_seconds']:.6f} | {p['python_peak_allocated_bytes']} | {m.get('lifetime_peak_working_set_bytes','UNAVAILABLE')} |")
    lines += ["", "## 同输入结果与零写核对", ""]
    for scene in SCENES:
        metadata = next(item for item in manifest["scenarios"] if item["scenario"] == scene)
        before = load(Path(metadata["baseline"])) if metadata.get("baseline") else None
        after = load(RUN / manifest.get("latest_candidate", "candidate") / scene / "result.json")
        if not before or not after or before["status"] != "PASSED" or after["status"] != "PASSED":
            lines.append(f"- {scene}：完整对照尚未取得，不报告收益。")
            continue
        assert before["data_before"]["sha256"] == before["data_after"]["sha256"] == after["data_before"]["sha256"] == after["data_after"]["sha256"]
        assert before["audit_status"] == after["audit_status"] == "VALID"
        assert before["database_before"]["migration_heads_sha256"] == before["database_after"]["migration_heads_sha256"] == after["database_before"]["migration_heads_sha256"] == after["database_after"]["migration_heads_sha256"]
        ratios = []
        for path in ("api-get", "repeatable-read-service"):
            assert before["readings"][path]["response_sha256"] == after["readings"][path]["response_sha256"]
            b = before["readings"][path]["normal_median_wall_seconds"]
            c = after["readings"][path]["normal_median_wall_seconds"]
            ratios.append(f"{path} 耗时变化 {(c/b-1)*100:+.2f}%")
        lines.append(f"- {scene}：响应 hash 一致、审计 VALID、业务表与迁移元数据四份摘要一致；{'；'.join(ratios)}（n=1）。")
    lines += ["", "## 执行环境与未覆盖项", "",
              "实际平台 Windows 11/AMD64，Python 3.12.5，28 逻辑 CPU。WMI 返回 CPU 型号/物理核/内存字段均为空，详细硬件未取得。各结果保留自身 PostgreSQL 版本/参数、依赖锁、源与工具 hash。CPU 频率、亲和性和后台负载未控制；DB 缓存未知、未清空。", "",
              "扩大场景为长链加三轮固定 INCOME=100/CONSUMPTION=100 分，以及实际 prepare_action；外部现金净变化零，新增准备动作不确认/执行，状态及预留以真实 fixture-phases 和表快照为准。初始正式模拟库未用于构造性能场景。", "",
              "未覆盖外部网络/浏览器延迟、部署、多用户并发、DB 服务端 CPU/内存、请求原生内存、C 层全部嵌套构造或长期统计性能。性能结论不关闭 FULL 规模实验、真人研究或两次全量验收。"]
    (RUN.parent / "performance-results.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    (RUN.parent / "performance-report-binding.json").write_text(json.dumps({
        "status":"PASSED" if passed else "IN_PROGRESS", "generated_at":datetime.now(UTC).isoformat(),
        "tool_sha256":sha(Path(__file__)), "report_sha256":sha(RUN.parent / "performance-results.md"),
        "entries":records},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"complete":passed,"passed_measurements":len(records)},ensure_ascii=False))


if __name__ == "__main__":
    main()
