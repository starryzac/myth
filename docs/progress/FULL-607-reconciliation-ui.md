# FULL-607 对账中心只读前端

状态：生产前端已交付，44项直接合成 HTTP/组件风险测试、整体 Web 类型和相关 lint 通过；未运行 PostgreSQL、真实浏览器或全量验收，FULL-607 仍 PENDING。原来源 `docs/spec/requirements-traceability.md:95` 要求余额、目标归属、持仓、回执、预期实际与重复副作用对账、故意不一致人工处理和 UNKNOWN 收敛；本包不以只读展示代替完整要求。

## 可运行能力

无参数 `ReconciliationPage`，建议主协调器接 `#reconciliation`，导航“对账中心”。唯一请求是服务端固定 owner/clock 的 `GET /api/v1/reconciliation/current`，空 query，不接受金额、银行事实、回执、身份、时钟、权限或修复输入。没有 POST、自动重试、修账、新幂等键或金融动作；只读 GET 不需新增全局写入 store。

采用当前生成的 FullReconciliationReport 及其真实子 DTO，原生产三源为 `domain/full_reconciliation.py`、`services/full_reconciliation.py`、`api/v1/full_reconciliation.py`。页面分别展示：

- 现金账：非信用卡账户的应用原数、独立模拟银行原数、带符号整数差额（应用−银行）和原 head。
- 持仓账：原本金相同口径，不将未到账收益、市场估值或状态名称当现金。
- 目标归属账：原 allocated、账户/position_ids、当前产权证据 ID/hash/服务核验标记，以及独立的目标现金、目标本金；放置位置不代替产权。
- 八表 inventory：每表 captured_count / actual_count / complete 原分母，容量不足和未捕获原数均保留。完整捕获不等于全部金融效果已验证。
- 每条原行动：原状态、幂等键、原请求/effect hash、预期及核得实际金额/费用/损失、bank operation/status/request hash、posting/receipt/status 原引用与具体 issues。SETTLED 无应用回执仍是 BANK_SETTLED_APPLICATION_UNRESOLVED；原 UNKNOWN、实际已核银行两腿和应用未解决可以同时存在，不能据此假设现金没变或重扣。
- 完整 AuditVerification 的链/引用/checkpoint/分母/tail/诊断，不压成一个 boolean；全部问题与 MISSING/UNSUPPORTED/PENDING 的完整 uncovered 清单和服务器 limitations。

MATCHED 只表当前服务已核范围匹配，准备但尚无银行原件的行动不被改成已执行。SERVICE_RECEIPT_VERIFIED 是原生产回执核验标记，不是当前授权。simulation/read_only 恒 true；grants_authority/executes_funds/repairs_performed/receipt_is_current_authority/economic_verified 恒 false。人工处理只展示报告状态，尚未创建持久人工任务或修复命令。

Reader 使用 BigInt 核安全整数差额，要求 null/MISSING 保持未知、八表捕获分母与返回清单相符、原键/操作/回执/腿引用和声明的已核状态相容，uncovered 不删项；原坏请求 hash 仍可作为人工诊断原字符串保留。**未核的人工负例报告可含实际不守恒银行腿**，保原数/原 JSON 与 INTEGRITY 诊断，不吞掉失败原件，也不把其验真；只有 bank_ledger_verified=true 才核腿恒等式/原 head 绑定。没有客户端独立重算原审计、输入哈希或银行效果，HTTP fixture 不是实测金融证据。

银行腿按20条只读分页，完整响应数组、所有原分母和原 JSON 保留，所有原记录仍由 reader 验证。原引用索引只在本次 parse 内建立，不缓存跨请求权限。刷新失败不把上次报告当成当前成功，进行中明确说明仍为上次快照。

## 五个新源与实际检查

`apps/web/src/api/full-reconciliation.ts`、`pages/ReconciliationPage.tsx`、相应两个 `.test` 文件及 `tests/full-reconciliation-fixture.ts`。复用现卡片/金额/字段样式，未修改 App、共享 stores、后端、generated contracts 或旧页面。

均通过 `scripts/run_scoped_check.py --task W6` 捕获原命令、退出码、日志及 source.before/after。证据目录统一为 `docs/progress/evidence/W6/`：

| 原目录 | 实际结果 |
| --- | --- |
| `full-reconciliation-ui-direct-first-20261005T175055Z-f2018392` | **FAILED**，38 reader PASS + 4页面 PASS/1 FAIL，总42 PASS/1 FAIL；内外区域可访问名称重复导致唯一页面失败，未重标 |
| `full-reconciliation-ui-regions-page-repaired-20261005T175139Z-61635322` | 修区域名称后相关5页面 PASS，2.68秒，wrapper4.465034秒 |
| `full-reconciliation-ui-final-index-and-page-direct-20261005T175345Z-d0a79530` | 加本次索引/20腿完整分页后两模块 **44 PASS/0 FAIL**，3.29秒，wrapper5.118173秒 |
| `full-reconciliation-ui-final-index-web-types-20261005T175341Z-beded137` | 整体 Web tsc --noEmit exit0，wrapper11.308922秒 |
| `full-reconciliation-ui-final-index-three-lint-20261005T175341Z-e4674c03` | 最后变更的reader/page/page-test ESLint exit0 |
| `full-reconciliation-ui-five-local-lint-20261005T175048Z-fb847bb1` | 首五源 ESLint exit0；scope stable，global变化仅其他 agent 的后端 `test_full_reconciliation_service.py`；未变 reader-test/fixture 复用此原检查 |

最终测试/类型/三源 lint 的 all_source_stable/scoped_source_stable 均 true。首失败页面完整原 bytes/SHA 另存 `.runtime/FULL-607-ui-before-region-fix-20261005T1752Z/`；目录名是标签，实际创建时间以原运行记录为准。

实际命令为 `pnpm.cmd --filter @bounded-funds/web exec vitest run src/api/full-reconciliation.test.ts src/pages/ReconciliationPage.test.tsx`、修名后仅该页面 test、`pnpm.cmd --filter @bounded-funds/web typecheck`；lint 只显式当前变更源。没有执行 PG、真实 Edge、全量 test/e2e 或金融修复。

最终五源完整字节和当前合同源SHA归档：`.runtime/FULL-607-reconciliation-ui-final-20261005T175536Z-642f9c8c/manifest.json`，SHA256 `f15fee84ae177ef19162d29c28df3fb66ac82f42c1d30bf4d259956cd845ca3a`。reader SHA `4ba5c7ab4362d95a05a5e24dc65997643b78b55982ccd37d67a24c9ebd77c8b4`；page SHA `3bbcabf00d39108126492683097bdb74a98ee432e84aafe3d5c222b9239b327c`。

## 未覆盖与下一依赖

主协调器负责导航集成和后端实际 PG；本批没有声称其候选已经通过。真实浏览器/移动端/键盘体验、完整不同银行状态与未知结果最终收敛、重复副作用实际金融负例、全部九类 FULL 动作及硬重启仍需原节点验收。

没有修账/持久人工任务/恢复执行 endpoint；不能称人工处理已完成或自行把 UNKNOWN 收敛为失败/成功。旧恢复缺完整目标产权维度时仍 UNKNOWN；未有银行 anchor 的目标原数不补零。信用卡负债不在现金比较，持仓仅核本金，正式外部经济证明始终未提供。初版与完整版最终集中验收均未在本包执行，编号不关闭。

