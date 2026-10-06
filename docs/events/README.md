# 事件接口索引

状态：INDEX_ONLY / FULL-002 PENDING。这里登记已有生产接口来源，不定义新的银行事件，不声称完整事件字典或验收完成。原银行状态、回执、审计链和正式历史保持不变。

| 实际已有合同 | 来源 |
|---|---|
| 原收入 origin / occurrence / observed 身份、内部划转不产生新收入 | [收入与资金位置合同](../architecture/income-ledger.md)；`app.domain.income_ledger` / `app.services.income_ledger` |
| 原经济动作/effect/hash、独立银行结清与应用回执的边界 | [执行域合同](../architecture/execution-domain.md)、[银行协议](../architecture/execution-bank.md)、[执行 API](../architecture/execution-api.md) |
| 不可变决定与原审计事件验证 | [决定记录 API](../architecture/decision-trace-api.md)、[审计链 API](../architecture/audit-chain-api.md) |

事件全集、producer/consumer 逐项状态迁移及各新 FULL 模块的真实集成证据仍须随其原任务交付，不能仅因本索引存在关闭任务。
