# ScenarioRunner 开发协议与证据边界

状态：W1 DEVELOPMENT 实现；已取得原传输/投影故障、legacy原回执只读与严格JSON直接PG证明，真实浏览器验收未完成。协议 `bounded-funds-scenario-v1`，结构定义以 `apps/api/app/services/scenario_types.py` 为准。本文不是冻结实验验收。

`ScenarioRunner.run(Scenario)` 接收初态、逐步带时区时钟、合成事实、故障和性质；只允许 `127.0.0.1:54329/bf_test_<32hex>`。`SEED_NEW` 只在没有 User 的新库调用原 seed，不重置已有历史。`EXISTING` 可绑定原 epoch；步骤不得回退时钟或重复 ID。运行前重新按 JSON 合同复制输入，所有金额、版本、权限、效果、回执都由原服务决定。当前遇到非预登记错误停止，返回原实际错误；预期拒绝后继续观测、结果引用和通用声明/变更/恢复仍是后续差量。

现有步骤：固定 `DEMO_EVENT`、候选模板准备/明确确认、可信 `EXTERNAL_FACT`、原 `PREPARE_ACTION` / `CONFIRM_ACTION` / `EXECUTE_ACTION`，以及观察新已确认周期义务的实际空支付历史。观察不写成功支付或用户权限；已有支付/累计观察冲突拒绝，不替换原件。

公开 Demo HTTP 仍仅接原固定 event/epoch DTO；服务器时钟生成一个 DEVELOPMENT 单步。不向浏览器开放时钟、金额、故障或期待余额字段。公开单步和私有多阶段工具使用原 shared audit command guard 保持 reset 排斥；授权与来源在各次原金融服务中重验，没有跨请求授权/账本缓存。

定存提前支取固定事件明确包含购买后普通消费前置：根据当次实际最低余量+30000分走原银行事实结算/投影形成缺口，才取得原合同报价并准备有损ASK_ONCE；已有实际负缺口无需额外消费。事实、两经济分录、未确认拒绝、精确effect确认、实际回执/审计/守恒和同原命令重放均有直接PG证明。不放宽原赎回改善实际缺口的安全门。

可信私有 CLI `scripts/scenario_runner_rpc.py` 从 stdin 读取一条 RPC JSON，目的固定 DEVELOPMENT，目标从显式隔离 DATABASE_URL 取得。其资源操作为：

- `snapshot`：REPEATABLE READ / READ ONLY 读取23业务表摘要；物理24表、原 rows、迁移、源码与环境由外层协调器另存。
- `verify_round`：实际账本、原审计、无自动损失和六原 Demo 命令完成性质。性质仅覆盖本次记录图，不代表未见案例或14实验指标。
- `verify_legacy_recovery` + 原 action_id：只读原 `SimulatedBankRedemption` 和原 `ActionReceipt`，调用完整 legacy 验证；返回原 DB receipt 字段，不合成现代 ActionResponse 或重新授权。
- `prepare_rent_old_action` + 原 policy_id：核原确认 rent 模板，按原用户时区观察覆盖窗口的实际空支付历史，再用原 PaymentIntent 准备动作。没有注入预期旧动作失效；实际变更后结果另核。
- `ingest_goal_income` + 原 goal_id：核已确认自有目标，仅使用固定合成 payroll 200000 分原银行事实；同 epoch/goal 固定原 key，重放使用原 occurred_at。该操作不生成目标分配、目标归属或权限。界面仍须用真实 GoalIntent 准备与执行，并核收入源分、目标归属及回执。

故障仅在隔离库局部调用生效：`DROP_BANK_RESPONSE` 先调用原银行并完成独立 commit，然后模拟传输 TimeoutError，原执行器保持 UNKNOWN；`FAIL_APPLICATION_PROJECTION` 在银行 commit 后阻止应用投影。ContextVar 区分并发调用，局部 RLock 串行化 fault patch，退出恢复原函数。不修改历史请求、哈希或成功状态；协调只重试原逻辑身份。真实 PG 用例须验证这些行为，单独纯上下文测试不足以证明银行已提交。

`NO_LOSS_AUTOMATIC` 对现代和 legacy 两种原协议分别核验，拒绝无协议/歧义载荷；现代费用/损失动作若已执行须有原经济效果对应单次确认，legacy 使用原完整银行/账本/回执校验且费损为零。未结算 legacy 行明确未验证执行回执，不能冒充已到账。空性质列表不会增加提交后的 epoch 事务。

冻结用途目前明确拒绝执行：字段含64字符 digest 不证明输入原件已冻结。正式 MVP_FROZEN 与 FULL_FAMILY_FROZEN 还须实现真实清单、完整输入/Oracle/机制/source绑定及开发用途隔离；这些完成前不通过字段名关闭 MVP-503/FULL。当前 ScenarioResult 也未提供完整独立原观察、actor日志及实验时延，外层工具准备不等于效果已测。

证据状态见 `docs/progress/W1.md`。本协议没有真实资金接口、真人研究结果、SLA、完整实验优势或全量验收声明。
