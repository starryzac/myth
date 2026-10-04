# MVP-205 安全恢复服务与独立模拟银行

对应 [ADR 0008](../adr/0008-evidence-bound-safety-recovery.md)。本文件描述本次实现和定向验证；全项目验收以主进度、完整 check 和正式 seed 记录为准。

## 公开合同

`app.services.recovery` 提供三个入口：

- `preview_recovery(session, user_id, now) -> RecoveryPreviewResponse`：只读，包含 `simulation/user_id/as_of/plan/source_evidence_ids/input_digest/source_issues`。
- `run_recovery(engine, user_id, idempotency_key, now) -> RecoveryRunResponse`：专用 Engine 驱动事务，创建一次恢复记录；同键查询并推进原请求，不按新状态另造一轮计划。
- `get_recovery_run(session, user_id, run_id, now) -> RecoveryRunResponse`：只读查询，不受理、不结算、不补账。

执行结果含 `run_id/status/plan/actual_boundary/actions/notifications`。`plan` 是原记录的历史计划，`actual_boundary` 是本次可信时钟下的真实财务结果。`RECOVERED` 仅用于银行已结算、应用已收到回执且当前边界为 `READY` 的执行；`PENDING_SETTLEMENT`、`PARTIAL_RECOVERY`、`RECONCILIATION_REQUIRED` 不宣称已经恢复。原无动作计划后来出现风险时，顶层状态改为 `NO_SAFE_RECOVERY` 或 `INSUFFICIENT_EVIDENCE`，保留原计划而不重规划。

请求键原文保存在输入快照中，1–160 字符，不静默修剪。数据库 run key 使用原键的规范 JSON SHA-256；动作 key 使用稳定动作 UUID，因此 160 字符输入不会使数据库列溢出。跨用户查询统一 404。

## 三个真实提交边界

1. 应用事务锁定模拟用户，读取完整证据和最新策略，生成 `DecisionRun` 与 `ActionPlan`，保存原计划、内容摘要和确定的整仓请求，然后提交。
2. `simulated_bank.process_redemption` 使用独立 Session/事务，重新核验新请求的当前授权及整仓身份，在独立银行账本受理或结算后提交。它只引用已经提交的 Action FK。
3. 新应用事务查询银行请求与 posting，消费经济事实，更新余额、持仓和证据，产生 `PRINCIPAL_RETURN` 流水、`ActionReceipt` 和本地通知。

银行事务已提交后丢失响应，或者应用投影事务失败，不能撤销银行事实。原键重试查询原银行请求，只补缺少的投影。GET 不推进 T1；原键 POST 在可信服务器时间到达后才推进原请求。

多请求批次按独立现金账本的序号消费到账，不依赖随机 UUID 顺序。后续银行请求拒绝时，之前已发生的到账仍单独对账；无法确认的请求继续保留待核对状态。每个投影使用保存点，身份异常不会回滚同批其他已经核对的到账。持久 `DecisionRun.SUCCEEDED` 要求全部原动作都有结算请求和成功回执；通知只为实际成功投影的动作生成。

## 独立银行事实

迁移 `0003_simulated_bank` 增加两表，既有 0001/0002 未改：

| 表 | 事实与约束 |
| --- | --- |
| `simulated_bank_redemptions` | 用户、原动作、持仓、原产品、原归属、返还账户、整仓本金、不可变请求 JSON/hash、请求与可用时点；用户内 key、动作、持仓分别唯一；请求经济字段不可 UPDATE。 |
| `simulated_bank_postings` | `CASH:<account>` / `POSITION:<position>` 账本键，序号、前条引用、前余额、带符号变动、后余额、操作与实际发生时点；数据库检查守恒、非负余额和合法两腿；全部 UPDATE 被拒绝。 |

`open_simulated_bank(session, user_id, as_of, *, cash_balances, position_principals)` 仅供可信种子/测试开户锚点使用。固定 UUID 与固定时间使相同开户重放一致；不能用不同金额替换既有 OPENING。当前种子提供四个非信用卡现金账户、三笔本金，共七条锚点。正常恢复路径从不开新 OPENING，也不把应用现余额复制成银行真值。

整仓结算在同一银行事务写本金 `-p` 与现金 `+p` 两腿，每腿由请求 ID 派生唯一 ID。账本链独立复算余额；应用余额与本金投影必须与其一致。多个已提交但未投影的本批结算通过明确银行 posting 调整核对，而不是猜测在途金额。控制性 demo reset 可删除记录，数据库管理员删除并重写全部历史的攻击不属于这里的不可变声明。

## 新请求、在途与自然到期

新自主赎回由纯恢复规划器检查当前风险、每个对应检查点不变差、原购买授权、当前最新确认授权、准确 scope、整仓单次上限、原产品版本条款和无损恢复开关。`recovery_sources` 复用完整资产曝光验证原购买 Action/Receipt/银行流水链；已证实的人工仓无原授权为 `ADVISE_ONLY`。种子旧 v1 产品不会借用新目录 v2 的无损条款。

T0 到账前只有计划，结算后才增加真实现金。T1 受理仅把持仓投影为 `REDEEMING` 并记录银行承诺的未来本金时点，不加现金，也不释放本金管理占用。银行已有效受理之后，当前策略撤销或过期不抹去请求；到时照原请求结算和对账。旧本金可用事件被同持仓的新事件替换，结清后不再增加第二次现金。

原完整 `fixed-principal-return-v1` 合同已经自然到期时，服务创建 `ASSET_MATURITY` 原事件结算记录。它不是新的自主赎回授权：当前撤销不会阻止原合同本金到账。银行重新核对原版本有效窗、明确无损本金条款、锁期/延迟与实际到期日期。目录 `created_at` 表示被系统知悉/导入，需不晚于当前时钟；原版本有效窗仍必须覆盖购买。未知原条款、未到期或者其他来源冲突均不执行。若到期对账后仍有缺口，结果保持部分恢复，后续新恢复计划需要新请求键。

## 来源、归属与新收入隔离

`asset-exposure-v1` 保留原解释且不能包含恢复状态。发生恢复后使用 `asset-exposure-v2`，完整包含银行请求、posting、应用账户/持仓/动作/回执及相关银行证明的集合摘要。原购买链仍保留；`REDEEMED` 标签本身不能释放管理占用，必须有守恒银行两腿、到账流水和匹配回执。

恢复入口即使读取 v1，也核对独立 OPENING/投影的完整集合。已有银行请求或当前 v2 时，共享 `load_boundary_context` 校验 v2 的银行/应用全集与账本余额，所以 202、203、204 和恢复读取都能阻断应用余额篡改。该改动只在来源适配层，202 纯财务算法不变。v1 模拟进口仍保持原协议兼容。

对账校验当前持仓账户、产品、目标、原策略版本、本金及原动作与不可变银行载荷相符；不能把中断期间被改绑的仓位写入新归属证明。目标本金回原目标账户：现金归属增加、本金归属减少，`Goal.allocated_cents` 不变，月累计贡献不变，因此不能修补一般现金缺口。

有效现有目标归属、月贡献、非空新收入 lot 证明在对账后换 epoch；旧证明保留并标 `SUPERSEDED`。lot 的 `spent/assigned/reserved/available/prior_unspent/original` 全部保持原值，不造新的收入 lot。到账流水的银行角色是 `PRINCIPAL_RETURN`，生活费估算将其识别为非消费，203 仅接受 `INCOME`，所以返本不会成为新的目标储蓄资格。缺失的完整收入证明不会在恢复中凭空生成。

恢复不延长普通交易历史的完整日覆盖。跨日后，若其他策略依赖尚未补齐的历史，真实资金可以已到账，但当前边界仍可为证据不足。

## 有损提案与通知

可信显式报价使用 `BANK_CONFIRMED / SIMULATED_REDEMPTION_QUOTE`，content 为：

```json
{
  "simulation": true,
  "protocol": "recovery-quote-v1",
  "user_id": "UUID",
  "position_id": "UUID",
  "destination_account_id": "UUID",
  "goal_id": null,
  "quote": "RecoveryQuote JSON object"
}
```

报价绑定原产品 ID/version/terms digest、本金、费用、损失、净到账、请求与可用时点、有效期；其证据需同用户、已知、有效且 hash 相符。原计划中 `ASK_ONCE` 动作还绑定原/当前授权、scope、事实与边界摘要及请求 hash。该完整提案保存到原输入快照，读取检查快照摘要，不产生银行请求、动作成功回执或假确认入口。有损确认执行由 301 验收。

`notifications` 仅是本地可查询记录，不发送外部消息。`REDEMPTION_ACCEPTED` 明确未到账；`PRINCIPAL_RECEIVED` 只在 posting 已投影并产生实际到账回执后保存。

## 定向证据

- 本 owner 最终 `test_recovery_service.py` 13 项、`test_simulated_bank.py` 5 项、`test_migrations.py` 11 项：**29 passed / 56.34s**，见 [最终日志](../progress/evidence/MVP-205-service-final-green.txt)。
- 同账户两仓同刻 T0 按银行链顺序消费；T0+T1 首次只部分恢复，真实第二笔到账后恢复；同键完整快照不变。
- 原始红灯包含缺模型、未实现服务、真实成熟未结算、批次第二笔拒绝使首笔无法投影、批次未全部完成却持久标成功；原始日志保留。
- 独立审计覆盖银行提交丢响应、投影失败、确定性不同键争仓、改绑身份、银行批次部分拒绝、同时重算应用证据/hash仍被独立账本发现；独立目标和非空收入 lot 验收见各自证据文件。
- 14 个本 owner 与必要适配文件 Ruff、格式和 strict mypy 全绿，见 `MVP-205-service-{ruff,format,mypy}.txt`。

本项不实现一般支付/划转/申购 dispatcher、恢复有损执行、外部银行、通用 outbox、全部自主等级矩阵或全审计链；相关验收仍由后续任务承担。
