# FULL-601：原金融状态机的持久投递与恢复接缝

实施日期：2026-10-05。排程依据为用户明确授权的[功能优先修订](../spec/execution-amendment-v2-functional-priority.md)。本包与 [FULL-602](FULL-602.md) 共用实现，新增本机模拟命令投递，复用实际原执行、查询、确认和回执核验服务。

功能状态：`IMPLEMENTED_PARTIAL`。原验收状态：`ACCEPTANCE_PENDING`。本批没有关闭原 FULL-601 编号，也没有将旧文件或纯测试当成完整版验收。

## 可运行能力

[command_delivery.py](../../apps/api/app/services/command_delivery.py) 将持久的原 ActionPlan 绑定到唯一 Outbox、固定消费者 Inbox 和逐次追加的 CommandDeliveryAttempt。root_id 为原 action_id，每次投递有新的 attempt UUID；原 operation_id、request_hash、effect_hash 和银行 idempotency_key 不变。消息仅包含身份与原哈希，不接收金额、经济后果、grant、confirmation 或成功结果。

接收、标记处理中、原金融三阶段和消息确认各自提交。消费者使用同 Engine 上单独连接持有的 session advisory try-lock，BUSY 返回不创建尝试、不执行资金。调用原 `execute_action(engine, user_id, action_id, now)` 时不持有跨 Session 的用户行锁。整个命令仍受原 `audit_command_guard` 的 reset 互斥保护。

| 实际原状态或银行事实 | 投递行为 |
| --- | --- |
| 无银行行、AUTO_EXECUTE、PLANNED/AUTHORIZED/SUBMITTED | 调用原执行服务，仍由原服务核源、当前权限、边界和经济后果。 |
| 无银行行、ASK_ONCE | 每次读取原 effect 精确绑定的当前确认；没有有效确认时 WAITING_CONFIRMATION。 |
| UNKNOWN 且无银行行，或银行 UNKNOWN | UNRESOLVED；不按失败盲目另发资金操作。 |
| 原银行 ACCEPTED/SETTLED，身份、业务键、载荷、哈希和银行幂等键均一致 | 恢复同一原 action/operation，调用原服务查询及投影，不创建替代 operation。 |
| 原 SUCCEEDED/RECONCILED | 通过原 `get_action` 重新核验银行腿及回执；满足同 action/operation 的 SETTLED 和 SUCCEEDED receipt 才可消息确认。 |
| 原 INVALIDATED/CANCELLED/FAILED 或银行 REJECTED | 消息 STOPPED，保存实际原状态；不凭 Inbox FAILED 宣称资金终败。 |
| 原周期 SEALED、当前周期不同或原 ActionPlan 已归档 | STOPPED/NO_CURRENT_ACTION；保留原消息与尝试，不在新周期重建资金行动。 |

Inbox 的 `SERVICE_RECEIPT_VERIFIED` 只表示原服务回执已经重新核验。Outbox 的 `DELIVERED` 表示消息处理状态。所有返回和尝试结果中的 `economic_verified` 均为 false，尚无独立金融效果证明。实际原错误继续抛出，消息确认记录失败不能替代原业务第一原因。崩溃留下的旧 RECEIVED/PROCESSING 尝试不回写为新尝试的成功。

## 时间合同修订与保留证据

首轮实际 PostgreSQL 10 节点全部失败，原因是新增 producer 错将 `AuditEpoch.created_at` 的真实审计 appended_at 写入时间与可信模拟业务 `now` 比较。原审计服务使用真实写时创建周期；模拟金融时间可以合法早于该写时。这是新增代码错误，原失败保持在 [首轮 manifest](evidence/W2/durable-delivery-crash-replay-confirmation-real-pg-20261005T125152Z-f462ed52/manifest.json)，实际 pytest 为 `10 failed / 69.52s`，wrapper 为 FAILED；金融相关 scope 稳定，外部两个 Web 测试文件变化使 global source 不稳定。

显式窄修 `DELIVERY_CLOCK_DOMAINS_V2` 删除这一处跨时钟比较。原 `current_audit_epoch` 仍按 user_id 和 OPEN 查询；原 epoch id、消息 epoch 绑定、ActionPlan.created_at≤业务 now、aware 业务时钟及原确认/策略/经济后果时间门保留。没有改审计时间、原历史哈希或正式模拟历史。新增三项纯回归覆盖晚审计写时、未来业务动作拒绝、缺少当前 OPEN 周期拒绝。本服务及路由内检索未发现第二处审计写时与业务时错误比较。

前源码、原哈希、直接测试、静态检查和 format 首次 RED 保存于 `.runtime/FULL-601-602-clock-boundary-fix-20261005T1254Z/`。修复后直接域/API 66 PASS/2.40s、严格 mypy 2 文件 PASS、Ruff/format PASS。实际 PostgreSQL 重跑由 root 单链安排，本文当前尚无重跑成功结论。既有 107 PASS/3.84s 与初次 6 文件静态检查保存在 `.runtime/FULL-601-602-durable-delivery/`，不覆盖为修复后实证。

## 具体未覆盖项

- 原完整状态词汇 `DRAFT→PLANNED→AUTHORIZED→SUBMITTED→SUCCEEDED/TERMINAL_FAILED/UNKNOWN→RECONCILED` 尚未统一落地。本包保留原金融 `FAILED` 等词汇，不迁移历史、不把投递状态充当完整金融状态。
- 本包没有新增原计划九类金融动作；未支持类别的实际执行仍按原服务拒绝。完整动作、产品、恢复优先级及全量对账属于各自 FULL 工作包。
- 新增投递路由已注册主应用，GET 使用 RR READ ONLY。真实 HTTP/浏览器整链、进程硬终止、全部非法金融转移和完整独立经济核验尚未由本包验收。
- 无后台默认自动服务、外部消息代理、真实资金接口。当前消费者由显式本机模拟 API 调用。

## 修后实际验证追加

原 10 个定向节点修后实际全部通过：`10 passed / 210.91s`，wrapper exit 0 / PASSED，实际命令及源码原件见 [修后 PG manifest](evidence/W2/durable-delivery-clock-repaired-real-pg-20261005T130035Z-660470f0/manifest.json)。运行 scope 源稳定，`scoped_source_stable=true`。同期 FullGoal 服务/API、联合规划 adapter 及生成合同变化，`all_source_stable=false`，不能宣称全仓冻结。首轮 FAILED 及首轮原件不改；此成功仅覆盖上列投递/恢复风险，非 FULL-601 原完整验收。

已解除本包源码 HOLD；没有随验证结果修改生产实现。后续与 FULL-603/604/605 汇合完成原状态机、九动作及对账验收，进程硬重启和独立经济证明仍待完成。
