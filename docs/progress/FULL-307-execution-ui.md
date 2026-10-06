# FULL-307 原范围动态目标执行消费者

功能状态：独立生产前端模块已实现，宿主接线由主协调器完成。原 FULL-307 关闭状态仍为 **PENDING**。本包不把 HTTP 合成夹具通过当作 PostgreSQL、银行、浏览器或全量验收。

## 可运行能力

- 手动读取实际账户用户、当前完整目标模型、MVP版本、周期与 Evidence/sourceHash；模型缺失显示 UNKNOWN，不自动创建模型或权限。各读请求分别采集快照，提交时服务器再次重核。
- 原 `/dynamic-goal-actions/preview` 只发送六个身份字段，服务器计算当前可执行范围。明确展示原剩余 min、动态上界、原剩余 max 与原名义 target 差额；null 保留未知。compact proof 的完整 SHA、owner/epoch/model/版本和原请求只用于响应内部一致性核对，前端不重造审计、收益、金额决策或授权引擎。
- 明确准备后只调用原 `/actions/{id}/confirm` 与 `/actions/{id}/execute`。用户复核固定 Action、金额、现金账户、收入原交易及 fragment、effectHash后分别确认/执行；没有自动 POST、自动重试、定时分配或新金额输入。
- 每个实际写请求在发送前保存完整 body、body_json、SHA、原六字段请求、稳定 key和固定Action。预览原请求亦在其只读 POST 前单独保存。HTTP成功、4xx、解析错误与连接丢失都不释放实际写入门。
- 只由独立原 `/dynamic-goal-actions/by-key/{key}` 恢复。核对原请求 client hash、全 Action.request server hash、compact原proof与固定经济后果。确认恢复必须原 ConfirmationGrant/evidence/effect/时间匹配；AUTHORIZED且无原确认仍 MISSING。执行恢复只在这个原Action的SETTLED银行状态和原服务回执齐备时释放。银行SETTLED但应用无回执仍未决，不能假设资金没有变化。
- 未找到并非终局，保留原键，用户先回读后才可明确重发同完整原请求。UNKNOWN不能换键或创建第二Action。已经准备的原工作区也单独持久保存；刷新只恢复元数据，必须手动新GET才显示可用确认/执行入口，未终局工作区禁止用另一键准备第二动作。
- 跨族 pending/busy/storage_error 与原 write-flight 阻新写；自己的原GET核对可越过本族门。SEALED、旧模型/版本或历史确认不提供当前金融权限。

## 新增源与宿主接口

仅新增以下7个 Web 源，未改 App、GoalsPage、旧动态规划面板、API金融源、公共HTTP或生成合同：

1. `apps/web/src/api/full-dynamic-goal-execution.ts`
2. `apps/web/src/features/full-dynamic-goal-operation.ts`
3. `apps/web/src/components/FullDynamicGoalExecutionPanel.tsx`
4. 对应上述 reader/store/panel 的三个 `.test.ts` / `.test.tsx`
5. `apps/web/src/tests/full-dynamic-goal-execution-fixture.ts`（明确 TOOL_ONLY）

面板 props：

```tsx
<FullDynamicGoalExecutionPanel
  goal={{ id, policy_id, policy_version_id, name }}
  userId={actualUserId}
  epochId={actualEpochId}
  mutationBlocked={otherFamiliesPendingOrBusyOrStorageError}
/>
```

`goal` 的 name 可省，其他身份必须取实际当前读源。Root将面板放在其它族金融fieldset之外；`mutationBlocked`仅他族门，不能包含本族pending而锁死自身恢复。

`recoverDynamicGoalOperation(): Promise<State>` 与 `useDynamicGoalOperation()` 暴露 `pending/busy/recovering/storage_error/workspace`。Root在顶层恢复并把该族前四状态纳入全局新写门；workspace是保存的原回读资料，不是授权。它没有自动请求、自动POST或授权缓存。

generated alias使用当前 Main 真实生成的 `FullDynamicGoalPrepareRequest/FullDynamicGoalProof/FullDynamicGoalPreview/FullDynamicGoalLookup/ActionResponse`。lookup 默认null字段实际必须齐全，reader用Required alias并逐项拒绝缺省/未验证原件。

## 实际模块验证及保留失败

所有命令外层为 `uv --cache-dir .uv-cache run --offline python scripts/run_scoped_check.py --task W6`，scope为上述7源。下列均是原生 manifest/output.log，未改旧记录：

| 检查 | 实际结果 | 原件 |
|---|---|---|
| 初次Vitest启动 | FAILED，esbuild spawn EPERM，未执行用例 | `W6/dynamic-goal-ui-direct-20261005T225620Z-464e0541` |
| 允许原生子进程后初次三模块 | 40 PASS / 1 FAIL，共41；16个store风险与8个panel用例通过 | `W6/dynamic-goal-ui-direct-permitted-20261005T225656Z-bfe7d5b7` |
| reader最终 | 17 PASS；超精度负例修为到达实际reader金额拒绝门 | `W6/dynamic-goal-ui-reader-final-20261005T225801Z-7ac37657` |
| panel最终 | 10 PASS；新增原预览存储拒绝、版本变更隐藏旧预览 | `W6/dynamic-goal-ui-panel-final-20261005T225955Z-62d6ab6d` |
| 最终整体Web类型 | exit0 | `W6/dynamic-goal-ui-types-final-20261005T225951Z-27acc7e7` |
| 最终本包7源lint | exit0 | `W6/dynamic-goal-ui-lint-final-20261005T225952Z-190767f6` |

唯一用例分母为 reader17 + 未变store16 + panel10 = **43**。没有把重跑累加为额外用例，也没有声称存在本包一次43全绿批次。初次失败是测试夹具在构造超精度值的SHA时提前拒绝，保留日志与第一次7源原bytes；未减负例或金融断言。最初未归档到wrapper的整体tsc错误（schema尚无ConfirmationGrant与字段收窄）不当成正式检查原件；其工具调用结果保留，后续实际wrapper类型通过已记录。

最终必要命令：

```powershell
node apps/web/node_modules/vitest/vitest.mjs run --root apps/web src/api/full-dynamic-goal-execution.test.ts
node apps/web/node_modules/vitest/vitest.mjs run --root apps/web src/components/FullDynamicGoalExecutionPanel.test.tsx
node apps/web/node_modules/typescript/bin/tsc --noEmit --project apps/web/tsconfig.json
node apps/web/node_modules/eslint/bin/eslint.js --config apps/web/eslint.config.js <本包7源> --max-warnings 0
```

## 未覆盖及下一前置

- 本包 **无真实PG/Edge/E2E**；Root的后台实际金融链由Root唯一执行，本包不推断其终态。宿主跨族gate与真实点击路线仍须Root接线及后续集中浏览器验收。
- FULL-307原月周期、原贡献、重复事件、最低额/上下限、实际产权关联的最终真实集成与原版本验收未关闭；自动周期调度/完整多目标联合执行未由本面板新增。
- 原确认事实的 `confirmed_at` 与原Evidence `observed_at` 当前producer相同；若合法历史形态不同，严格reader保留原请求，不强行改时间或冒成功。原确认读证明永远不是当前银行授权。
- 当前lookup没有直接返回实际银行幂等键字段；UI只显示实际客户端原key，明确不能把它冒充银行key。现服务器协议的派生银行键由后台固定，前端不生成第二键。后续如需直观展示银行键，应由后端按当前原行新增只读字段再生成合同。
- 原目标模型的source/hash失效或当前版本变化均由server重新校验。旧回执/保存工作区、compact SHA与UI checkbox不能替代当前权限。
- 所有实际金融仅模拟，未开启真实资金接口，未修改正式历史或任何旧失败证据。

最终源码/证据复用边界与精确 SHA 见新 `.runtime/FULL-307-ui-final-*/manifest.json` 及同目录 `HANDOFF.md`。原后台合同freeze `.runtime/FULL-307/final-source-20261005T225006Z/manifest.json` 保留。
