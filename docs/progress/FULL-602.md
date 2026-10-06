# FULL-602：持久 Outbox / Inbox

实施日期：2026-10-05。依照[功能优先修订](../spec/execution-amendment-v2-functional-priority.md)执行，与 [FULL-601](FULL-601.md) 共享原金融服务投递接缝。功能状态：`IMPLEMENTED_ACCEPTANCE_PENDING`。原验收状态：`ACCEPTANCE_PENDING`；原 FULL 编号关闭状态未修改。

## 持久能力与接线

root 在 [full_models.py](../../apps/api/app/db/full_models.py) 和 [0008 migration](../../apps/api/alembic/versions/0008_command_delivery.py) 新增 CommandOutbox、CommandInbox、CommandDeliveryAttempt 三张元数据表。Outbox 对原 action 唯一；Inbox 对原消息和固定 consumer 唯一；逐次 attempt 编号连续且使用真实新 UUID，旧结果保留。数据库身份 UPDATE 保护、owner/FK、哈希、状态与时间约束由该迁移负责。

原 [prepare_action](../../apps/api/app/services/execution.py) 仅在原 `record_action_created` 后、同 Session 和原 prepare 事务提交前调用 `enqueue_action_in_transaction`。准备与 Outbox 任一失败一起回滚。相同原 action 已有消息时只核原绑定，不重写原载荷。旧动作可经显式 enqueue 路径加入消息；不能通过旧消息重新生成 action、effect、确认或授权。

Outbox 保留原 epoch FK；action_plan_id 为不可变原身份引用且不指向会在原模拟 reset 中归档删除的当前 ActionPlan。原 reset 因此保留投递历史。消费者仅在同 owner、同 OPEN 原 epoch 且原 action 实际存在时调用原金融服务，已归档消息 STOPPED/NO_CURRENT_ACTION。

[delivery.py](../../apps/api/app/api/v1/delivery.py) 已由 root 注册主应用：

- `GET /api/v1/delivery?limit=50`：当前用户最近消息，limit 1..100；实际读取每条原 action、银行状态和回执。
- `GET /api/v1/delivery/{outbox_id}`：消息与全部原尝试，核连续编号、inbox/outbox/attempt 身份；不从存储状态字符串推导金融成功。
- `POST /api/v1/delivery/actions/{action_id}/enqueue`：空 JSON 对象，绑定原当前 action。
- `POST /api/v1/delivery/{outbox_id}/deliver`：空 JSON 对象，显式调用固定本机消费者。

owner、业务时钟及 Engine 由原服务依赖提供，公共 body/query 不接受替代消费者、时钟、金融载荷或权限。GET 使用 REPEATABLE READ + READ ONLY。原银行键与原三阶段保持不变；重投仅恢复同一逻辑操作。消息失败、资金 UNKNOWN、原银行拒绝和原服务回执验证分别保留。

## 原件与定向验证

- 原 execution.py producer 修改前完整原件 SHA256：`df529a7144230d8ed71f76e7c28d0de998aed250be3ad55c6a70e55ec41bd385`；在 `.runtime/FULL-601-602-durable-delivery/execution.before-outbox-producer.py`。本包只新增 import 与 enqueue 调用三行，原执行签名、银行及投影代码未修改。
- 新迁移实际隔离 PG [manifest](evidence/W2/full-delivery-additive-migration-real-pg-20261005T123647Z-b4a4124c/manifest.json)：1 PASS/4.35s，原 23 张金融表全部原行及原哈希一致，三张新元数据表为空，实际 physical inventory 为 27 表；原件与 scoped/global 源稳定。
- 原直接新域/API加原执行域：107 PASS/3.84s；严格 mypy 6 文件、Ruff、format PASS。已有检查原件及 prior RED 均保存在 `.runtime/FULL-601-602-durable-delivery/`。
- 首轮 [10 节点实际 PG](evidence/W2/durable-delivery-crash-replay-confirmation-real-pg-20261005T125152Z-f462ed52/manifest.json) 为 FAILED/10 FAIL/69.52s，错误与窄修见 FULL-601 的 `DELIVERY_CLOCK_DOMAINS_V2`。不能将静态成功或 collection 当作这些实际风险已经通过。
- 修后直接 66 PASS/2.40s；mypy 2 文件、Ruff/format PASS。新精确源码 SHA 清单位于 `.runtime/FULL-601-602-clock-boundary-fix-20261005T1254Z/final-source-sha-after-format.json`。实际 PG 重跑待 root 结果。

修后生产 service SHA256 `5d535cb38204d6e03b1a3717c006f8a60d2107e044f8109d7a4d88de2a66ad93`；execution.py `f0f8dae39139c9ef14ffde6dceef139b0183ea657297aaa2d185830a14bbc03d`；10 节点 integration test `51b8ea1020a0ff4cf8a256b466e0e954bbfc6765716fd8f80eceabb3bc95f938`。后两项未受时钟修复改动。

## 10 个实际风险节点与未覆盖项

实际测试位于 [test_command_delivery_integration.py](../../apps/api/app/tests/test_command_delivery_integration.py)，仅 disposable `bf_test`，无正式历史库操作。覆盖准备提交前共同回滚、ASK 当前确认与重复投递、接收后/确认前崩溃的全新 Engine 恢复、银行已提交丢回执/投影失败的 UNKNOWN 恢复、UNKNOWN 无银行时不盲重发、advisory BUSY 与错 owner 零副作用、原 reset 保留旧消息且不可重建、AUTO 原策略与实际银行管道。崩溃用 BaseException 注入跨过正常错误记录，随后新 Engine 读取持久结果；未做 OS 进程 kill。

尚无后台 pending 扫描/自动调度、外部 broker、列表历史分页、操作系统硬重启或真实资金接口。本包不实现新金融动作、完整差异人工对账或独立经济 oracle。实际源/原动作校验与原服务回执核验均可运行，但 `economic_verified=false`，FULL-602 全部原验收及完整版全量节点仍待集中完成。

## 修后实际验证追加

原 10 个定向节点全部实际通过：`10 passed / 210.91s`；[修后 manifest](evidence/W2/durable-delivery-clock-repaired-real-pg-20261005T130035Z-660470f0/manifest.json) 为 PASSED、exit 0。原 prepare/Outbox 共同回滚、ASK 确认、两崩溃点新连接恢复、两 UNKNOWN 已提交银行恢复、UNKNOWN 无银行不重发、BUSY/错 owner 零副作用、旧周期消息保留与 AUTO 原银行管道均得到本次定向结果。

命令 scope 源稳定；同期 FullGoal 服务/API、联合规划及生成合同变化使 global source 不稳定。该结果是特定源 scope 下的真实集成结果，不是全仓冻结或全量验收。原 10 FAIL 及失败原件保留不变；BaseException 注入与新 Engine 恢复不扩大为 OS 进程硬重启证据。本包生产源码 HOLD 已解除，原编号仍保留待验收状态。
