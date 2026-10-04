# MVP-304 并发重置用例同步预算修复

状态：唯一原 node 的真实 PostgreSQL coverage 回归 GREEN，session 33323 已退出 0 并关闭；原完整验收 RED 仍完整保留，新的统一完整验收由 root 另行启动。

## 原始 RED 与诊断边界

- 完整验收 `20261004T084600Z-09f4a398` 的 pytest 结果是 `171 passed, 1 failed in 2447.88s`，唯一失败为原并发用例的 `bank_committed.wait(45)`。原始输出保留在 `MVP-304-check-red-09-uv.log`；完整清单为 `MVP-304-check-red-manifest.json`。
- 45 秒等待从提交整个 `execute_action` 开始，覆盖命令 guard、应用 RESERVE 事务和独立真实银行事务。它并非 reset 锁等待计时。失败时尚未提交 `reset_future`。
- 银行事件仅在原 `process_operation` 返回后设置。失败展示中的 Event 已 set，是测试 `finally` 释放投影并由 executor 等待后台动作后才展示 traceback 的结果；不能据此声明原运行成功完成应用投影或全部金融断言。
- 原日志中 PREPARE、confirm 的 HTTP 耗时分别约 20.718 秒、20.344 秒，说明 coverage 下完整路径存在可见代价；这不是银行事务的分段实测，也不能单凭原失败推断金融缺陷、reset 死锁或全部 suite 耗时归因。
- 原无 coverage 单用例曾 `1 passed in 99.56s`，是整体用例耗时，不能当作单独 reset 的 SLA。

## 授权的最小修复

仅改 `apps/api/app/tests/test_audit_workflow_integration.py` 中同一个既有用例。没有增加或删除用例，没有修改服务、migration、协议合同、根状态文件或金融语义。

- ready / resume / projection / archive 的防挂起同步预算分别为 240 / 240 / 240 / 600 秒。这些是测试同步预算，不是产品 SLA、性能验收线或底层数据库操作取消机制。
- ready 使用 Event 短轮询；若动作 future 提前完成，立即读取 `future.result()`，使真实异常直接传播；成功结束却未经过原银行接缝也直接失败。
- 保留原 `pg_locks` ExclusiveLock 未授予的 10 秒证明，原银行 SETTLED / receipt 尚不存在、最终 SUCCEEDED / receipt、旧 epoch SEALED、三类经济事件各唯一一次的断言。
- 两个薄计时包装仅原样调用真实 `execute_action` 与 `seed_demo`。银行暂停包装仍先调用原真实银行服务，提交后才暂停应用投影，没有复制金融实现。
- 用 `monotonic()` 保存四段实际时间：命令 guard+RESERVE 至进入银行、原银行事务、释放暂停至动作结束、reset 开始至结束。最后一段包含锁等待、归档和 seed，不冒称独立序列化耗时。异常路径也输出可得时长和 future 的 done/running/cancelled 状态。

## 源码原件与静态验证

原整文件保存为 `MVP-304-hooks-before-sync-repair.py.txt`，SHA256：

`77f89d67e7ed9169faefebe2a8040e18880f8491be69f95274098d24e85f2f3f`

它与原 RED manifest 中该文件的哈希完全一致。修复后冻结 SHA256：

`32f5b1e8110f6dffd88a4ea774c3fdd0ee5c4543b3c5458d0c38af6fefa45f78`

原件到修复的实际 diff 为 `MVP-304-hooks-reset-cov-repair.diff`。`MVP-304-hooks-reset-cov-repair-source-check.json` 对原完整清单 201 个文件逐项哈希核对，只有获授权测试文件不同，其余 200 个原件相等。

独立只读 AST 对照 `MVP-304-hooks-reset-cov-repair-assertions.txt` 退出 0：该函数外的整模块 AST 相等；除三处同步预算表达式外，原来的九个 assert 条件逐项相等，包括 10 秒锁等待证明及全部银行、回执、SEALED、事件唯一性断言。金额或状态断言没有放宽。

下列检查均真实退出 0：

```text
uv run --frozen ruff check apps/api/app/tests/test_audit_workflow_integration.py
All checks passed!
uv run --frozen ruff format --check apps/api/app/tests/test_audit_workflow_integration.py
1 file already formatted
uv run --frozen mypy --follow-imports=silent apps/api/app/tests/test_audit_workflow_integration.py
Success: no issues found in 1 source file
```

## 唯一目标 coverage 运行

```text
uv run --frozen pytest apps/api/app/tests/test_audit_workflow_integration.py::test_real_reset_waits_across_bank_commit_and_original_application_projection --cov -rP --durations=1
```

真实 PostgreSQL；沿用原 fixture 的随机 `bf_test_<32hex>` 数据库，迁移、seed、原银行事务和 reset 均真实执行，fixture 退出时清理此随机库。没有正式 demo 数据复位。

环境：`UV_CACHE_DIR=<repo>/.uv-cache`、`PYTHONUTF8=1`、`COVERAGE_FILE=<repo>/.runtime/MVP-304-reset-sync.coverage`；未覆盖根完整验收的原覆盖数据。原始 stdout/stderr 保存在 `MVP-304-hooks-reset-cov-repair.txt`；最终启动、结束、退出码及哈希元数据保存在 `MVP-304-hooks-reset-cov-repair-run.json`。

启动 UTC：`2026-10-04T09:42:21.3275143Z`；结束 UTC：`2026-10-04T09:46:18.0721623Z`；实际 exec session：`33323`；收集 1 项。最终结果：`1 passed, 1 warning in 233.33s`，test call 为 `214.40s`，外层 wall 为 `236.7372061s`。唯一 warning 是既有 Starlette/httpx 弃用提示。

| 实际计时段 | 秒 | 口径 |
| --- | ---: | --- |
| reserve_and_command_guard | 23.359 | 原动作工作线程开始，到进入原银行服务 |
| bank_transaction | 22.454 | 原 `process_operation` 调用开始到真实返回，不含测试暂停 |
| application_projection | 4.531 | 主线程释放暂停，到原 `execute_action` 工作线程结束 |
| reset_gate_archive_seed | 124.156 | 原 `seed_demo` 调用开始到返回，包含等锁、归档、seed |

四段是同次运行的原服务调用接缝测量，其中 reset 与应用投影并发重叠，不应简单相加当作总用例时长。前三段不包含 PREPARE / confirm，实际 HTTP 日志分别为 20.297 / 23.906 秒。

同次动作从开始到原银行返回至少 `23.359 + 22.454 = 45.813s`，明确超过旧 ready 的 45 秒预算。本次保留 10 秒真实 PostgreSQL advisory ExclusiveLock 等待证明，并完成了原银行 SETTLED/无提前 receipt、后续 SUCCEEDED/真实 receipt、旧 epoch SEALED、BANK_ACCEPTED/BANK_SETTLED/ACTION_PROJECTED 各一次的断言。它支持修复测试同步预算的结论；不构成某一服务性能 SLA 或全部完整验收的完成证明。

两个 future 的最终状态均为 `done=True, running=False, cancelled=False`。工具句柄随后真实返回 exit 0，无残留本次 Python 测试进程；没有重启、取消或用另一轮结果替代本次日志。coverage 所有权已交还 root；此文件和九个 hooks 服务继续冻结。

原始日志 SHA256：`faa50bb708c93ae7599ab52b848e7278da6a6df1f584f315fc72e01ebe28a085`。

独立覆盖数据 SHA256：`7a2cae9aebadfec495ebcd6c31629f7ace529e4dc3fc6aa9ae9191a8e987b8f1`。
