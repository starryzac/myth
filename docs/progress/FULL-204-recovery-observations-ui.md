# FULL-204：恢复组合 v4 的原观察与同键恢复界面

2026-10-06。本包是生产前端能力差量，原 FULL 编号整体仍待完整版验收。它消费实际 `RecoveryComposedGlobalObservation` Schema，不改变现有只读动作集合面板、金融执行、旧观察协议或历史哈希。

## 可运行能力

`RecoveryComposedObservationPanel` 先实际 GET 当前 v4 动作集合，从其原 `user_id/epoch_id` 建立请求；可明确选择是否与上一条已核对的观察比较。提交体严格只有 `expected_epoch_id/previous_observation_run_id/idempotency_key`，不存在金额、权限、时间或结果输入。POST 前持久保存完整 body、原 JSON、SHA256、服务器来源身份摘要及预计运行 UUID5。命名空间为 `dfb9d38c-5698-532d-a90f-bcb2458d7d7f`，名称为 `user:epoch:key`。

HTTP POST、存储对象、先前完整结论均不能清未决门。只有独立实际 GET 原预计运行，核对 exact body/hash/user/epoch/run 及完整观察之后才解除。响应丢失、503、父原件缺失、错误的请求绑定继续保留原身份。用户手动同键重试先 GET，只有该运行的真实 404 才允许同 body/key POST；父记录 404 转为 `PARENT_ORIGINAL_UNVERIFIED`，不能作为当前运行不存在。最多核对 64 条原 ancestry，与后端容量一致，超限/循环拒绝。

新 reader 复用原 v4 snapshot reader，保留其完整原数学与生产者分母校验；额外重算请求 hash、UUID5、前后 snapshot/signature、BoundaryObserved/BoundaryCrossed/null、完整性、需复核标记及 semantic key。完整原 GET 响应文本保存在 localStorage，解析器保留收到的原响应文本。存储原件仅供再 GET 定位和原文显示，不恢复成新鲜核验或权限。

新写门在异步只读等待之后、实际保存/POST 之前读取最新父 family 门。读取原 GET 可以越过其它写门；同键 POST 仍受其它 family 门及通用 write-flight 约束。存储失败保留原请求并阻挡新 POST，仅精确原件 GET 成功且完整持久化成功才清该存储错误。

## Root 挂载合同

- 默认导出 `RecoveryComposedObservationPanel({mutationBlocked?: boolean, showOriginalRecovery?: boolean=true})`。父页挂载传 `showOriginalRecovery=false`，避免重复恢复入口。
- 命名导出 `RecoveryComposedObservationOriginalRecoveryPanel({mutationBlocked?: boolean})` 应挂在任何列表/fieldset 外；不依赖当前列表、账户加载或身份登录。原 GET 独立可用。
- `recoverRecoveryComposedObservationOperation()` / `useRecoveryComposedObservationOperation()` / `getRecoveryComposedObservationOperation()` 提供 `pending/busy/recovering/storage_error`。Root 应把四者加入其它 family 的写门；本族 `mutationBlocked` 只包含其它 family，store 自己阻挡本族新键。
- `beginRecoveryComposedObservation` / `beginRecoveryObservationSameKeyRetry` 接 `boolean | (()=>boolean)`，组件用 latest getter 防止异步等待后的门状态陈旧。

## 检查与失败原件

最终纯前端风险 29/29 PASS（API19、store5、component5，Vitest 3.92 秒）：`docs/progress/evidence/W3/recovery-composed-observation-request-recovery-final-direct-20261006T041644Z-90c1dbe9/manifest.json`。ESLint7 PASS：`.../recovery-composed-observation-request-recovery-final-static-20261006T041640Z-4d6ef739/manifest.json`。实际全 Web `tsc --noEmit` PASS：`.../recovery-composed-observation-request-recovery-final-types-20261006T041640Z-c86247e7/manifest.json`。三次 `scoped_source_stable/all_source_stable` 均为 true；仅这些检查，非完整版验收。

第一轮 24 PASS/3 FAIL 保留于 `.../recovery-composed-observation-web-native-worker-direct-20261006T041037Z-f77aa6a1/manifest.json`：完整夹具 epoch=`0a2c`，UNKNOWN 原夹具 epoch=`0320`，测试错误跨绑原请求，生产严格门正确拒绝。修复前精确 7 源保存于 `.runtime/recovery-composed-observation-first-behavior-red-20261006T041343Z/manifest.json`；修夹具后的23项原结果及其后两处恢复差量均有独立记录，未覆盖原失败。

更早裸 pnpm 子进程不存在（WinError2）的两个准备目录只保存 `source.before.json/output.log`，没有 final manifest，不计测试运行。实际 Node/pnpm.mjs 的默认受限 esbuild EPERM 失败保留于 `.../recovery-composed-observation-web-native-direct-20261006T040952Z-da3f1565/manifest.json`；自动审批允许后只运行本地新增 Vitest 模块，未运行浏览器、DB 或金融。

## 明确未覆盖

29项夹具/模拟HTTP都是 TOOL_ONLY，不能当银行、真实PG、浏览器、通知或正式实验原件。本子任务未执行任何 PG/browser/金融。原界面只写观察元数据；通知支持明确 `NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4`，无赎回、到账、金融确认或银行权限。Root 父页实际挂载和业务真实链须记录独立结果；不能凭本文件或模块检查关闭 FULL-204/507。
