# W0 性能证据方法复核

复核日期：2026-10-05（Asia/Shanghai）。本记录只读取已有日志与 JSON；没有启动 PostgreSQL、运行验收、重做基准或修改冻结工具。复核时有效运行 `performance-20261004T225248Z-a5290990` 的短历史为 `PASSED`，MVP-401 长链为 `INCOMPLETE`，扩大历史和 candidate 对照尚未在本次复核中取得完整结果。本文件不是三场景完成证明。

## 来源与固定条件

- 运行清单：`performance-20261004T225248Z-a5290990/manifest.json`，`repeats=1`、`expanded_rounds=3`。
- 工具：`scripts/benchmark_reads.py`，SHA256 `a55aaae0963b878ba1413703873d033481f3f541a7f28bf4927347a7d466f539`。
- 短历史 baseline 加载源码指纹：`21ffc22ec7825946b59d489015d0f4a02f47e0e2d8d24045606f30dd97938452`。HEAD 为 `4ccf84e973978482a1098d18c69fbfc9f011fac6`；工作区可包含未提交改动，因此版本必须同时引用加载源码指纹、依赖锁指纹和工具指纹，不能只引用 HEAD。
- 短历史原始文件：`baseline/short-seeded/result.json`、`api-get-wall.json`、`api-get-instrumented.json`、`fixture.json`、`fixture-phases.json`、`snapshot-before.json.gz`、`snapshot-after.json.gz`、`service-cpu.pstats`、`service-cpu.txt`、`service-python-allocations.tracemalloc`。
- 长链来源：`baseline/mvp401-long-chain/fixture-phases.json`、`fixture.json`、`result.json` 和 `baseline/mvp401-long-chain.log`。尚未完成的文件只能支持阶段事实，不能支持该场景通过。
- 每个场景使用生成的 `bf_test_<32 hex>` 数据库。candidate 复用同一数据库与 `fixture.as_of`；不会重新 seed。场景之间的 ID 和规模各自独立，不能将不同场景的耗时组成加速比。

## 最终性能表的字段映射

| 建议列名 | 引用字段或文件 | 准确含义与边界 |
|---|---|---|
| 场景与状态 | `fixture.scenario`、`status` | 只有最终 `PASSED` 可以作为完成结果；`INCOMPLETE`、中断和失败均保留。 |
| 正常 API 请求墙钟（秒） | `readings.api-get.normal_wall_samples[*].wall_seconds`、`normal_median_wall_seconds` | TestClient GET 到响应收到；排除客户端 JSON 解码、证据哈希、CPU/内存/函数计数插桩。它调用真实模拟后端和 PostgreSQL，但没有外部网络、浏览器或生产部署负载。 |
| 正常服务读取墙钟（秒） | `readings.repeatable-read-service.normal_wall_samples[*].wall_seconds` | 新只读 REPEATABLE READ 会话、服务调用与产品响应 `model_dump` 序列化；排除证据哈希。与 API 有不同调用边界，应分列。 |
| 样本次数与缓存条件 | `normal_wall_samples`、`hardware.server_cache` | 当前 `n=1`；“median”仅等于该单样本，不能声称统计稳定性、P95/P99或 SLA。Python 进程/路径首次读取不等于数据库冷缓存；数据库缓存为 `UNKNOWN_NOT_FLUSHED`。 |
| SQL 语句次数 | `api-get-instrumented.json:sql.query_count` | 独立插桩请求的执行语句次数，包括 `SET TRANSACTION READ ONLY`、容量 count、查询等；不是只有业务 SELECT 的次数。 |
| SQL 驱动执行区间合计（秒） | `sql.sql_wall_seconds` | SQLAlchemy before/after cursor execute 区间，含驱动、网络、服务端执行及该插桩环境开销；并非 PostgreSQL 服务端独占 CPU 时间，不能与正常请求墙钟相减推导“纯 Python 时间”。 |
| 持久化业务行数 | `data_before.total_rows`、`data_before.tables.<table>.rows` | 23 张已注册 ORM 表的一次完整快照行数。物理表共 24 张，另 1 张是 Alembic；其头版本单独记录、哈希与前后比较。 |
| 累计驱动返回行数 | `sql.known_driver_rows`、`unknown_driver_rowcount_queries` | 每条语句的已知非负 cursor rowcount 相加；重复查询会重复计入，`COUNT(*)` 返回的一行也计入。不是去重实体数、数据库扫描行数或物理库规模。未知计数不得补为已测。 |
| 快照 JSON 总字节 | `data_before.snapshot_json_bytes` | 23 张业务表按稳定排序打包后的 UTF-8 JSON 字节数，包含序列化字段与结构开销；不是原文、数据库物理占用或网络传输字节。 |
| 存储审计 canonical 原文总字节 | `database_before.stored_original_canonical_bytes` | `octet_length` 合计审计事件 `canonical_text`、subject snapshot `canonical_text` 和 epoch `seal_canonical_text`；包括该隔离库全部历史，不能替代完整业务 JSON 体积。 |
| canonical 核心入口调用次数 | `functions.python_call_counts.app.domain.audit_chain.canonical_bytes` | 单独报告核心字节规范化函数次数。`canonical_text`、`event_canonical_text` 等嵌套调用另列，不能把它们累加当“唯一 canonical 对象数”。 |
| 模型 Python 构造/验证入口次数 | `functions.pydantic_python_entry_calls` 与细分 `pydantic.main.*` | 统计 Python `BaseModel.__init__`/`model_validate*` 入口调用；不是全部模型实例总数，C 层嵌套验证器内部构造未独立观测。必须保留 `model_count_boundary` 说明。 |
| 账本验证入口次数 | `app.services.simulated_bank.ledger_heads`、`validate_bank_projection`、`app.services.income_ledger.read_income_state` | 分列函数次数。它们存在嵌套关系；相加不能得到“独立完整账本验证请求数”。既有 AnyIO 工作线程由 Python 3.12 all-thread hook 捕获。 |
| CPU 原始 profile | `service-cpu.pstats`、`service-cpu.txt` | 独立服务请求的调用次数与 own/cumulative time；覆盖调用线程，含数据库等待。profile 墙钟带插桩开销，不能当正常 API 墙钟或纯 CPU 利用率。 |
| 请求内 Python 分配峰值 | `profiling.python_peak_allocated_bytes` | 独立 tracemalloc 服务请求的 Python 分配峰值；不含原生分配、PostgreSQL 或整个进程 RSS。 |
| 进程生命周期工作集峰值 | `process_memory_after_reads.lifetime_peak_working_set_bytes` | Windows `GetProcessMemoryInfo` 对本 worker PID 的实测高水位，包含导入、建库/夹具、快照、正常读取与 profiling。baseline 建夹具、candidate 不建夹具，因此不得据此计算请求内原生内存优化比。 |
| 同数据与只读证明 | `data_before.sha256 == data_after.sha256`、migration head hashes、响应 hashes | 业务表快照与迁移头分别前后相等；candidate 还须与 baseline 相等。API/服务响应 hashes 同时相等。该证明不代替并发/篡改负例。 |
| 同负载耗时比例 | 完整 candidate 的 `comparison.json` | 只在对应场景 baseline/candidate 都通过、数据/时钟相同后引用。n=1 应标记“单样本局部对照”，不能升级为总体吞吐收益。 |

## 本次可核对的短历史数值

以下仅为本运行的短历史 baseline，未形成优化收益：

- 状态 `PASSED`；正常 API 墙钟 `0.38520410005003214 s`；正常服务墙钟 `0.4027353998972103 s`，均 `n=1`。
- 23 张业务表 `422` 行；快照 JSON `407406` 字节；存储审计 canonical 原文 `2437` 字节。
- 独立 API 插桩请求：`81` 条 SQL；驱动执行区间合计 `0.21088520030025393 s`；累计已知驱动返回 `1226` 行；未知 rowcount `1` 条（本样本为 READ ONLY 设置语句）。
- `canonical_bytes=15`；`canonical_text=1`、`event_canonical_text=1`、`parse_event=1` 单列。
- 模型 Python 入口 `341` 次，即 `__init__=301`、`model_validate=38`、`model_validate_json=2`；C 层嵌套实例总数未测。
- `ledger_heads=4`、`validate_bank_projection=3`、`read_income_state=1`；`verify_audit_chain=1`。
- 请求内 Python 分配峰值 `2111478` 字节；worker 生命周期工作集峰值 `140394496` 字节。
- 业务数据 before/after SHA256 均为 `d0f0a3f6b05ad1ce937a29ddf57ddb71bb78cf49368b7ed86c69ac87368cfc53`；迁移头哈希均为 `ae87e48aea5900a1eab4593542cb8562801e5d79ab30016b2e06d049f642a1c2`；API/服务响应哈希一致，加载源码指纹前后一致。

## 硬件、执行环境与未覆盖项

平台可用字段为 Windows 11 `10.0.26200`、AMD64、`Intel64 Family 6 Model 183 Stepping 1, GenuineIntel`、逻辑 CPU 数 `28`、Python `3.12.5`。WMI 命令退出码虽为 `0`，但 `processor/cores/logical/memory_bytes` 全为 `null`，因此详细 CPU 商用型号、物理核数与物理内存大小为**未取得**；退出码不能当硬件信息成功。不得把 `28` 个逻辑 CPU 写成 `28` 个物理核。

该长链记录的数据库为 PostgreSQL `16.15`，`shared_buffers=128MB`、`work_mem=4MB`、`max_connections=100`。每个最终行应引用自身 `database_before.runtime` 与依赖锁哈希，不能仅沿用另一个场景的环境推断。

CPU 频率、进程亲和性、背景负载没有控制；服务器缓存未清空。未开展外部网络、生产部署、多用户并发、DB 服务端内存峰值、请求内原生分配峰值或全部 C 层嵌套模型构造计数。本基准不能单独关闭这些实验要求。

长链在本次只读复核时的行数 `10575`、审计原文字节 `13305017` 来自 `data_before/database_before`；其日志已出现一次正常 GET `101625 ms` 与一次插桩 GET `222937 ms`，但场景仍 `INCOMPLETE`。最终报告应等最终 `result.json` 的完整状态与正常墙钟文件，不能把插桩 GET 当局部优化结果，也不能把已有阶段日志改写成整个场景通过。

扩大历史固定三轮各新增真实模拟 `INCOME=100`、`CONSUMPTION=100` 事实及实际 `prepare_action(PurchaseIntent)` 决策。净现金变化为零，动作保留 `PLANNED_UNRESERVED`，未提交/未执行，不消耗新的银行资源；不写入手工审计或手工成功回执。所有轮次仍需实际成功生成并完成同数据读取，才能宣称扩大场景已测得。

## 实际夹具结果更正（2026-10-05 07:29 +08:00）

上段的 `PLANNED_UNRESERVED` 与“不消耗新的银行资源”在本报告原复核时未取得运行依据，不应当成实测。新清单 `performance-fixture-repair-20261004T232505Z-5daa2f9b` 的前两轮实际 `prepare_action` 结果为 `PLANNED`。新增动作没有经过确认/执行；预留是否存在及其状态以实际表快照为准，不假称为零。原扩大夹具因未开户清算对手方被真实服务拒绝，原失败清单保留；仅 counterparty 改为 seed 已开户 payroll/merchant 后在新隔离库重跑。所有测量程序不变，旧短/长通过结果显式复用。此补充不提前判定扩大性能或任何候选通过。

## 已取得的候选局部证据（2026-10-05 08:37 +08:00）

短与长候选已 PASS；扩大候选尚待后置守恒与 profiles。新增夹具最终原件确认六个 100 分事实均 SETTLED/PROJECTED，三个新增准备动作均 PLANNED，无新增回执/银行执行；八条历史预留为 CONSUMED，不能把 PLANNED_UNRESERVED 写成真实 ActionPlan.status。

长链正常 API 为 101.635072→19.960296s，服务为 83.554818→49.880999s；请求 Python 峰值为 249640231→280724062 bytes，增加必须保留。完整 [基线 pstats](performance-20261004T225248Z-a5290990/baseline/mvp401-long-chain/service-cpu.pstats) 与 [候选 pstats](performance-fixture-repair-20261004T232505Z-5daa2f9b/candidate-20261005T000955Z-8a890efa/mvp401-long-chain/service-cpu.pstats) 确认 `_constraint_copy` 19656→9828、`ledger_heads` 13→5。候选 `_posting_digest` 168 次，每次调用一次 canonical_bytes，恰对应 15397→15565 的增量。其累计剖析耗时 0.0270422s；这些嵌套插桩时间不能相加归因正常 API 的下降。ledger/digest 未进入截取文本榜单，必须引用完整 pstats。原件 SHA 分别 `914e40b0e4f387d4fa7d8da0d0f5c5fd20082fa8b9a87ed979168ac362538f5b` 与 `424615d1894f85a0237098bff6fba70974507470fc83b98f62bf73a92bcdd06f`。

扩大 normal API 已落盘 112.6293824→138.8106744s（+23.25%），service 为 116.1598792→81.1632647s（−30.13%）；两个路径都是 n=1，结果混合，不能概称稳定提速或全面退化。API 计数 5037→4613 SQL、74003→60645 模型 Python 入口、34→26 ledger_heads、102404→102773 canonical_bytes。`transactions` 查询 4062→4051，候选占全部 SQL 87.82%；`evidence_items` 363→64、`simulated_bank_postings` 210→166。分布只证明重复查询仍在，不能证明 API 变慢的原因；SQL 插桩区间不能从正常 wall 相减。

同请求作用域集中于 OPEN 审计 trace 读取；三个待准备动作的 `_action`/assess_action/事实与收入状态读取仍有重复核验。此项列为具体未覆盖工作。cache、背景负载、CPU 时钟未控制；内存峰值增加的因果来源未作独立实验，bulk DTO/originals 强引用只是待验证解释。本节保持当时状态，最终完整对照以 [实测表](performance-results.md) 与最终 manifest 为准。

## 完整对照收尾（2026-10-05 09:01 +08:00）

三个基线与三个候选最终全部PASS，新清单SAME_DATA_COMPARISON_PASSED。扩大候选CPU独立profile墙钟245.596218s，tracemalloc墙钟1054.863828s，Python请求峰值392620660 bytes（基线353670321，+11.01%）；正常API138.810674s/服务81.163265s单列。六份结果、原件、后置表/迁移/源码/锁零变化检查与全部计数均已保存。每场景四份API/服务、基线/候选响应SHA相等，审计VALID；正式库收尾与自建库清理另有独立PASS原件。[报告绑定](performance-report-binding.json)逐项绑定结果与当前/原失败manifest，不改原失败状态。本报告此前INCOMPLETE是当时状态，保留原文和更正记录；最终数字与限制以实测表为准。
