# FULL-604：原安全恢复动作前端消费

状态：UI_IMPLEMENTED / GENERATED_CONTRACT_CHECKED / SYNTHETIC_HTTP_41_PASSED / FINANCIAL_INTEGRATION_NOT_RUN；FULL-604 仍 PENDING。

原要求来源：`docs/spec/requirements-traceability.md:92`；完整计划 F:1344–1346、697–708。当前执行页面消费真实服务器规划、原固定 Action 与 USER 逐次同意，不能代替组合、部分、有损、到期结清及完整版验收。

## 可运行能力与来源

新增 `apps/web/src/api/full-recovery-execution.ts`、`features/full-recovery-execution-operation.ts`、`components/FullRecoveryExecutionPanel.tsx`，分别负责实际 generated DTO reader、原完整请求持久门和用户明确操作。对应三个直接测试模块及 `tests/full-recovery-execution-fixture.ts`、`tests/full-recovery-execution-base.json` 全部标注 TOOL_ONLY；合成银行状态/回执用于检查前端处理，不是 PostgreSQL、浏览器、真人 USER 或资金效果证明。

页面只读加载当前签名 local USER 与原 GET `/full-policies/{policy_id}/recovery-planning`；只显示实际候选、原本金/报价/净额/费用/损失、目录和期限来源，未知金额显示 UNKNOWN。原规划的 `execution_support=NOT_IMPLEMENTED` 保留；新执行适配的支持范围另行明确为授权整仓、零费用/损失、同 scope、T0/T1。所有 source issue 原 `code/source_ref/message` 逐字段显示。

用户手动 POST `/full-recovery-actions/preview` 只有 policy/version/epoch/position/key 五个身份字段；完整原 body/JSON/hash 在 POST 前保存。没有金额、时钟、报价、银行事实、角色或 receipt 输入。服务器 scope proof 只表示原执行范围一致，不授予权限。preview 的假设 operation identity 不作为最终 prepare 的 Action/effectHash，prepare 必须服务器重新核验。

prepare → 独立原键 GET → 明确 USER checkbox confirm → 独立原键 GET → 明确 checkbox execute → 独立原键 GET 原回执。每个金融 POST 前保存 exact intent/body/hash/key/action 并深冻结；响应成功、4xx、网络或解析错误均不释放 pending。原 GET 核完整请求、client/server 双 hash、冻结 scope proof、同 Action/effect、专用原 USER consent 和独立 Evidence 引用后才释放对应请求。AUTHORIZED 不替代 USER consent；银行 SETTLED 无应用回执仍保 UNKNOWN，现金变化不能由前端猜测。

NOT_FOUND_NOT_FINAL 不允许换键、换仓位或创建第二动作。刷新仅恢复原存储，不自动 GET/POST。用户先独立 GET，再明确手动恢复同一原 body/path/action；不自动 retry。坏存储或拒绝存储保留原 bytes，锁住新写。原未终局 workspace 继续阻另一键；SEALED/旧版本不会显示新的确认入口。历史原 consent/receipt 不表示当前银行授权，每次生产请求继续由服务器与独立银行验真。

## Root 宿主接线

默认 `FullRecoveryExecutionPanel` props：`{ policyId, userId, expectedVersionId, epochId, mutationBlocked? }`；四个身份必须来自当前实际服务器响应，不能用占位 UUID。`mutationBlocked` 只表示其它族写门，自己的独立原 GET 保持可用。Root 负责现有 Policy/Products/App 宿主与全局金融门，不属于本包源。

全局 `FullRecoveryOriginalRecoveryPanel` named export 无 props，只恢复原存储和用户手动原 GET，可放在其它族 fieldset 外。操作模块导出 `recoverFullRecoveryOperation`、`useFullRecoveryOperation`、`getFullRecoveryOperation`、`isFullRecoveryWorkspaceUnresolved`；state 为 `{pending, workspace, busy, recovering, storage_error}`。全局新资金操作/重置应同时阻 pending/busy/recovering/storage_error 和未终局 workspace。本族 begin 也核已有 demo/fullpolicy/fullgoal/onboarding/question/spending/intervention/专用授权/回拨/资产/固定付款/动态目标及 shared write-flight 门；不建立跨请求授权缓存。

## 实际最小检查与失败保留

最终三个模块 **41/41 PASS，Vitest 7.81s，wrapper 8.915414s**：

`docs/progress/evidence/W5/full604-final-original-consumer-41-direct-20261006T000937Z-3720034a/manifest.json`。

分母为 reader17、持久原请求恢复15、界面9。包含原身份/hash/金额/损失/授权级别/回执篡改拒绝、非终局和 USER consent 缺失、POST 不清门、银行 SETTLED 无应用回执、跨族阻写但自身 GET 可用、刷新不自动网络、版本变化、UNKNOWN、存储拒绝。命令：

```text
node apps/web/node_modules/vitest/vitest.mjs run --root apps/web --config vitest.config.ts src/api/full-recovery-execution.test.ts src/features/full-recovery-execution-operation.test.ts src/components/FullRecoveryExecutionPanel.test.tsx
```

运行使用已有 `scripts/run_scoped_check.py` 实际捕获 argv/退出码/日志/source before-after。三个模块 scoped_source_stable=true；all_source_stable=false，Root 并行变化仅 `App.tsx`、`FullPoliciesPanel.tsx` 和新 `FullRecoveryExecutionHost.tsx`。这不是当前完整宿主或全产品验收。

真实当前 generated Schema 整体 Web 类型检查 child0：`full604-actual-contract-consumer-whole-web-types-repaired-20261006T000900Z-0a97534b`，16.375861s；命令 `node apps/web/node_modules/typescript/bin/tsc --noEmit --project apps/web/tsconfig.json`。七 TS 源局部 ESLint child0：`full604-final-actual-source-issue-consumer-lint-20261006T000900Z-0ffc66ab`，3.063945s。两份 manifest 的 scoped/all source stable 均 true。之后 Root 宿主发生变化，其最终整体类型须由 Root 接线后检查，不能借前一结果称新宿主已验。

首次真实 direct 原件 `full604-original-consumer-direct-risk-20261006T000241Z-f610eb9f` 是 **40 PASS/1 FAIL**，不改标。失败为 execute 丢响应 UI 尚在 GET 异步处理中时同步查恢复按钮；仅改 test `findByRole` 等待，不改生产/fixture/断言。原八源保存 `.runtime/FULL-604-ui-first-direct-before-20261006T000324Z/`；修复目标单节点 `full604-original-consumer-unknown-ui-repaired-20261006T000354Z-a4ff0f82` 是1 PASS/8 targeted skipped，不冒充原41批通过。

首实际 generated 类型 `full604-actual-contract-consumer-whole-web-types-20261006T000656Z-3c377926` child2，唯一错误为误用 issue.entity_type。原八源保存 `.runtime/FULL-604-ui-first-types-before-20261006T000827Z/`；窄改为真实 source_ref/message，并在原 UNKNOWN 用例增加实际字段显示断言，分母保持41。早期 pnpm 子进程找不到命令、路径重复及 managed esbuild EPERM 原件均保留：`...direct-20261006T000123Z-f9205590`、`...lint-20261006T000123Z-e67124a8` 和 `...direct-native-node-20261006T000208Z-14164e5a`。前两个无 child 完成 manifest，不能写成测试 PASS；后续采用实际 node 路径并使用批准的测试进程权限，未安装依赖或弱化检查。

## 具体未覆盖与下一依赖

本包没有运行金融 PostgreSQL、浏览器、Docker、全量或正式 seed/reset，不配置或读取真实凭证。后端九合成 route 检查另见 `FULL-604-execution-api.md`，不与前端41项相加声称金融证明。

Root 当前真实 USER → 原资产回执/当前 FULL 声明 → ASK → 银行 commit 丢响应 → UNKNOWN 原键 → 原 USER consent/回执恢复链仍须实际运行。当前短缺 deadline 可等于 as_of，现实墙钟下一时刻可能 LIQUIDITY_RISK；页面如实显示，不延长原界限。真实 T1/GOAL 回款、到期原 reconciliation、有损专项、组合、分笔/部分和全部原验收尚未证明。原编号、失败、报价、授权版本、hash 和正式历史保持原状态。


## 2026-10-06 08:26 Root 接缝与实际证据

2026-10-06 08:26 北京时间：604 前端41直接风险/API8源和Root真实当前策略宿主均已交付；原pending与PLANNED/UNKNOWN工作区跨页阻新写/演示reset/登出，列表读取失败仍有独立原GET。Root最初18相关PASS9.80s，纯账户摘要夹具types FAILED2原件留存；窄补实际DTO后受影响5PASS8.63s、整体Webtypes b9f79960和5文件lint7c79cf22 PASS。Root五源FINAL 4bb74a52，708集中样式和房租窄屏说明已FINAL55288bc4，实际CSS/Vite build通过；Root跳导航不改hash、路由改变focus当前内容三case内已核；真实手机/键盘/屏幕阅读器/对比度仍NOT_RUN。

0014实际migration首命名约定重复prefixERROR8.47s保留，两个drop仅op.f窄修后actual1PASS11.26s，FINAL683e4ef7；旧0012/0013/已有行/哈希不改。204v1 actual原节点FAILED13s，后诊断FAILED16.34/14.72s均留；根因实际无 simulated_bank_ledger_heads 表、证据200行截断漏refs、512KiB不足。v1/已冻结Full-v1继续UNKNOWN，不能造表别名或提高旧版本容量改历史。独立显式 actual-v2使用真实表/全counts/现原schema type进行修订，307+真实asset family/math接新版本，原FAIL不改。后Full-v1与HTTP自动通知节点仍NOT_RUN。

307 actual原preview409 DYNAMIC_GOAL_V2_LEDGER_REQUIRED，FAILED12.91s；实际诊断FAILED13.49s证明原nativeV2唯一差异as_of `+00:00`与typed输出`Z`，同一时点。Root仅改新动态消费者及新历史验证接缝为严格原生IncomeLedger解析后全值相等；原JSON、原source hash、所有整数/源身份/归属/预约/权限/clock门和旧nativeV1拒绝不改，不写规范化原件。新增7反例和原35direct/4strict正在跑；准备后的真实银行/原键恢复仍未得结果，105/604后两节点NOT_RUN。当前无Root金融RUNNING，唯一shared财务负责人仍Root；新v2/102/材料独立并行。正式关闭21/92/FULL原项PENDING、真人0/NOT_STARTED、新性能NOT_MEASURED，最后集中验收尚待。

