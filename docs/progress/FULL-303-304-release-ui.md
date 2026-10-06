# FULL-303/304 目标现金回拨前端

实现状态：新独立组件、严格实际 DTO reader、原请求持久恢复与直接模块测试已实现。原需求关闭状态：未关闭；本包不包含真实浏览器、金融效果实验或完整版验收。仅使用生成的实际 `GoalRelease*` Schema，不修改后端、旧效果哈希、Root GoalsPage/App 或共享写门代码。

## 可运行能力

- `GoalCashReleaseExecutionPanel` 接受 `goal: {id,name,policy_id,policy_version_id}`、`userId`、`epochId: string|null`、`mutationBlocked?: boolean` 和可选原 `authorization: ReleaseResponse|null`。
- 输入原专用授权 epoch/key 后仅 GET 原授权，或显示父组件提供的原授权；账户只从当前同用户 `/accounts/summary` 的真实 CASH 列表选择。原范围快照只是原请求依据，每次服务 prepare 仍 fresh 验权，浏览器没有当前权限缓存。
- 手动 preview 后只在实际 READY、原 Goal/version/epoch、当前来源 inventory、选定原 allocation×fragment×hash、累计/单笔 cap、1098 点保护比较均匹配时开放 prepare。UNKNOWN 金额保持 null；不接受金额、银行事实、权限、clock、grant 或成功结果输入。
- 原 prepare 九字段 body、JSON、key 和独立 `client_intent_hash` 在 POST 前完整保存。`original_request_hash` 是服务端 Action 完整封套摘要，独立保留，绝不把客户端意图摘要当服务端摘要。
- 显示原金额、最低保障、源 Goal/账户、保护 CASH、原片段与全部 hash、明确有限日期及紧急范围；本金/其他 Goal/新收入/原 ASSIGNED/fee/loss 全部零变化。准备与 SUBMITTED 未声称资金已预留。
- 明确复核 checkbox 后才执行严格三字段 `{accepted:true,reviewed_effect_hash,expected_epoch_id}`；执行 body/JSON 和原 action/effect/epoch 一并保存。所有 POST 响应继续保留待核对门。加载页面、reload、网络错误和 NOT_FOUND 不自动 POST、换键或清记录。
- 用户主动 GET 原 action 或原 epoch/key 才可清门；要求绑定原 body/server hash/command/decision、真实确认 identity、SETTLED、原完整 receipt、三 posting IDs 与两 cash transaction IDs、零 fee/loss，且 SUCCEEDED/RECONCILED 和非 unresolved。POST 或本地对象不能充当独立 GET。
- PLANNED/SUBMITTED/UNKNOWN 保留原行动，禁止新 prepare。UNKNOWN 银行已 SETTLED 且原腿核验、无 receipt 时，可在独立原 GET 后明确按同 action/body/effect/epoch 恢复投影；其他族门仍阻新银行执行。reload 的旧 SETTLED 标签不能替代本次 GET。服务端仍验原命令和真实三腿，不重新授予权限。

## 精确接线

新文件：

1. `apps/web/src/api/goal-cash-releases.ts`
2. `apps/web/src/api/goal-cash-releases.test.ts`
3. `apps/web/src/features/goal-cash-release-operation.ts`
4. `apps/web/src/features/goal-cash-release-operation.test.ts`
5. `apps/web/src/components/GoalCashReleaseExecutionPanel.tsx`
6. `apps/web/src/components/GoalCashReleaseExecutionPanel.test.tsx`
7. `apps/web/src/tests/goal-cash-release-fixture.ts`

实际服务路径：POST `/api/v1/goal-cash-releases/preview`、POST `/prepare`、GET `/actions/{actionId}`、POST `/actions/{actionId}/execute`、GET `/commands/{epochId}/by-key/{key}`。后三路径都以 `/api/v1/goal-cash-releases` 为前缀。by-key NOT_FOUND_NOT_FINAL 明确 `not_found_is_final=false`、`replacement_allowed=false`。

Root 集成使用 `useGoalCashReleaseOperation()` 或 `getGoalCashReleaseOperation()`，初始化调用异步 `recoverGoalCashReleaseOperation()`。任何写族应把 `pending || busy || recovering || storage_error` 纳入全局新写门。自身 GET 不占写门；自己的组件在原银行 SETTLED 后仅允许明确旧行动投影恢复。父级不要因历史 receipt 给新权限。新组件已读取旧 demo/full policy/full goal/onboarding/question/spending/intervention/authorization 族门；反向纳入新族由 Root 负责，当前七源不修改其它族实现。

preview/lookup 的实际 DTO 没有 `simulation` 字段，使用本文件的有界原 JSON reader；Action/Auth/Accounts 继续原标准 HTTP helper 的 simulation 门。没有补造 DTO 字段。接口读取保留收到的 raw JSON 可通过 `cashOriginalJson()` 获取；sessionStorage 保留完整解析后的原请求、Action 和确认 body，不声称 sessionStorage 是服务端原 artifact 字节档案。

## 已运行检查与原失败

最小三个模块直接测试命令（cwd `apps/web`）：

```text
node node_modules/vitest/vitest.mjs run src/api/goal-cash-releases.test.ts src/features/goal-cash-release-operation.test.ts src/components/GoalCashReleaseExecutionPanel.test.tsx
```

最终 30 PASS / 3 files / 5.09 s，exit 0；合成 HTTP 夹具只验证 reader、界面和浏览器状态恢复，不是真实确认人研究、银行实测或金融成功证据。日志 `.runtime/FULL-303-304/release-ui/pure-final.log`。

七源严格类型：继承实际 Web tsconfig，include 仅本七源及其真实 import 依赖的 `.runtime/FULL-303-304/release-ui/tsconfig.json`，`node apps/web/node_modules/typescript/bin/tsc --noEmit --project .runtime/FULL-303-304/release-ui/tsconfig.json` exit 0 / 2.74 s。ESLint 对七源 `--max-warnings 0` exit 0 / 3.40 s。没有把这两个定向检查称为全 Web 验收；Root 在集成后统一 wholeWeb types。

原失败未修改：

- 最初 pnpm exec 的 vitest/eslint 缺 Windows bin wrapper，未运行测试；直接 node 首次启动 esbuild 被 sandbox `spawn EPERM`，未运行测试。随后授权的本地编译子进程运行 27 PASS / 4.97 s，未安装依赖。
- 首次 wholeWeb types 仅 Root-owned `GoalsPage.test.tsx` 未使用 `method` 导致 FAIL，原日志保持；Root 已单独归档并修参数，当前本包不重复全 Web 检查。
- 新三负例真实 RED：未知 effect 自洽 hash、选定来源与 inventory 不一致、POST/reload 的旧 SETTLED 绕他族门；3 FAIL / 26 PASS。原源和日志在 `pre-final-risk-20261006T0409Z`。
- 修复后一次仍 1 FAIL / 29 PASS，原因是 synthetic fixture 的 available/selected 共用对象，负例改一处也改另一处。该原日志名称为 `green.log`，实际状态仍 FAILED，未覆盖；夹具改为独立 clone 后最终 30 PASS。

## 未覆盖与下一前置

- Root 尚需把新 panel 挂到 GoalsPage，并把新 pending/recovering 写门反向接到全部写族、reset 门和刷新集合。七新源交付时没有冒称完成这些接线。
- 本包没有运行真实浏览器/Edge、真实 PG、银行经济实验、真人确认研究、完整 25/67 验收；后端实际金融 batch 由 Root 单一执行链登记，未取得结果时不得声称银行成功。
- 浏览器只核服务响应及原身份/有限经济形状与预期 IDs；不独立读取银行账本，不能把 server `service_receipt_verified` 提升为独立金融实验成功。
- 不支持改金额、任意 source/use、本金释放、重新分类收入、跨 Goal 新分配、同旧键新效果或自动重试。原拒绝/UNKNOWN 不清 pending；若后台最终无可执行终态，需原只读核对和后续明确人工流程，不能自行把失败当未提交。



## 2026-10-06 04:30 Root增量

六实际PG批终态PASS/1600.41s，wrapper1611.67274s；见 `evidence/W3/actual-release-sources-consent-repair-and-current-maturity-20261005T195334Z-810817b1`，范围来源稳定/全源独立变化。仅相应节点实证，非完整版本验收。具体旧缺口与原失败保留。
