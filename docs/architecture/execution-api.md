# 模拟动作执行 API

MVP-301 实现说明；统一完整验收状态见 [进度](../progress/MVP-301.md)。服务器固定模拟用户、时钟和金额规划，全部数据及银行接口均为本地合成模拟。

| 请求 | 用途 |
|---|---|
| `POST /api/v1/actions/prepare` | 提交幂等键和意图，生成可复核的固定经济载荷；不移动或预留资金 |
| `GET /api/v1/actions/{id}` | 读取原动作、历史准备判断和已知银行状态；不触发结算 |
| `POST /api/v1/actions/{id}/confirm` | 明确接受原 `effect_hash`，仅适用于本次动作 |
| `POST /api/v1/actions/{id}/execute` | 请求体必须是 `{}`；重新验证当前事实后执行或查询原银行操作 |
| `GET /api/v1/actions/{id}/receipt` | 只读核验并返回原回执；尚未到账对账时返回 409 |

准备请求包含 `idempotency_key` 和 `intent`。意图由 `kind` 区分：

| kind | 输入 | 服务器绑定的执行内容 |
|---|---|---|
| `transfer_internal` | 本人源/目标账户、整数 `amount_cents` | 确切金额与收付账户；必须确认 |
| `pay_recurring` | `policy_id` 及 `period` 或 `bill_id` | 已知收款身份和本期确切未付额；区间义务须已知实际总额且明确确认 |
| `allocate_goal` | `goal_id` | 当前目标月储备、实际新增收入片段与原目标账户 |
| `purchase_asset` | `policy_id` | 当前服务端选定产品、金额、多源真实扣款、明确返本账户和退出计划 |
| `redeem_asset` | `position_id` | 原授权仓位、当前有效报价、本金、净到账、费用与损失 |

所有请求拒绝未知字段与客户端时钟、用户身份、派生金额或自动授权覆盖。确认体是 `{"effect_hash":"<64位原摘要>","accepted":true}`；不接受字符串或数字代替布尔确认。

返回 `prepared_validation` 是准备当时的判断。它不表示现在仍可执行；执行必须重新检查权限、来源、已占用资源和全部90日现金边界点。动作15分钟准备窗口到期后未受理动作不能继续提交；已受理银行事实按原操作继续对账。已成功动作的重复请求仅核验和返回原回执。

回执 `executed_cents` 表示执行本金/支付额，赎回净到账见固定载荷 `effect.net_cents`，与单列的 `fee_cents`、`loss_cents` 满足本金守恒。读取回执必须与独立银行操作、完整且唯一的分录引用、金额及时间一致；异常返回409而不重付或改写回执。

实际执行分成预留、独立银行结算、应用对账三个事务。`ACCEPTED` 和 `UNKNOWN` 保留占用及未完成状态。GET不会推进结算，POST执行重试沿原动作查询；不得换键清除未知结果。内部转账保持收入原始来源，只变位置；目标同账户储备只变归属，不伪造现金转账。

未提交的主动退出计划保存为 `UNSUBMITTED_PLAN`，不能作为银行已承诺的未来返本来放宽申购后现金边界。持续调度、四级完整矩阵、完整决策轨迹、审计链与业务界面仍由后续计划任务验收。

协议和证明分工见 [ADR0009](../adr/0009-atomic-simulated-action-execution.md)、[银行层](execution-bank.md)、[领域判断](execution-domain.md)、[收入账本](income-ledger.md)。
