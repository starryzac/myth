# 单产品配置的只读来源适配

实现依据 ADR 0007。公开接口为 `preview_asset_allocation(session, user_id, policy_id, now)`，响应包含 simulation、user_id、policy_id、as_of、allocation、source_evidence_ids、input_digest 和 source_issues。调用者提供可信时钟；HTTP 使用同一 REPEATABLE READ 会话，服务使用 no_autoflush，不创建决策、动作、回执、持仓或预留。

## 授权与归属

查询指定用户的 asset_authorization 最新版本，经既有生命周期确认绑定、配置摘要和当前有效期验证。未来生效、暂停、撤销、过期均不产生新配置，完整财务 baseline 仍保留。

goal 范围还校验同用户 Goal、精确当前 goal_saving 版本及全部金额/期限/优先级投影；目标的 asset_policy_id 与当前配置必须共同指向选定授权，授权 goal_id 指回目标。两条链均须当前有效。候选仅改变所选目标现金与本金放置，累计归属、本月贡献及其他目标不变；服务不消费 MVP-203 的新增收入 lot。

目录从全部明确版本构造严格 AssetProductTerms；纯域选择已知有效的最高版本。terms_digest 为完整 maturity_rule JSON 的摘要，含 yield_rule；它不同于 MVP-202 排除展示收益的财务摘要。旧持仓继续保留原 product_id。

## 完整曝光进口协议

`app/domain/asset_exposure.py` 导出 `EXPOSURE_SOURCE=SIMULATED_ASSET_EXPOSURE`、`EXPOSURE_PROTOCOL=asset-exposure-v1`、严格 AssetExposure DTO 和 `asset_exposure_snapshot`。构造函数接受同用户完整的 accounts、positions、actions、receipts、evidence ORM 对象或字段映射，无 SQLAlchemy 依赖；settlements 默认为空。它仅构造进口声明，不验证银行真实性，不授予权限。

声明存入 EvidenceItem，等级 BANK_CONFIRMED。顶层包含 simulation=true、protocol、user_id、complete=true、UTC as_of，以及 accounts/positions/actions/receipts/evidence 规范排序的 ID 和字段摘要、settlements。完整内容摘要由 EvidenceItem.content_hash 绑定。曝光自身排除在 manifest 外，避免循环摘要。

- 账户摘要绑定身份、类型、余额和观察时间。
- 持仓摘要绑定账户、产品准确版本、目标、原授权版本、本金、购买/到期/可用时间和状态；不把累计展示收益作为本金。
- 动作摘要绑定身份、决策和授权、来源/目标账户、目标/产品/持仓、类型、金额、状态、幂等键、请求内容及摘要、创建/授权/失效时间。
- 全部 attempt 回执摘要绑定动作、attempt、引用、状态、执行额、费用/损失、响应和发生/结清/创建时间。
- 来源摘要绑定银行余额、持仓、交易和目标归属/贡献/本金可用证明的身份、hash、状态及时间窗。

`services/asset_exposure_import.py` 在同一会话重读完整集合、重算并比较整个声明。声明必须唯一且非 SUPERSEDED、当前 VALID、同用户、BANK_CONFIRMED、内容哈希正确。缺失或冲突不能等同于零占用。

epoch 不得晚于查询 now，声明 observed_at 不早于 epoch，epoch 覆盖余额及已知流水、动作和回执水位。查询时钟推进而没有新事实，不会单独让声明失效。goal 范围要求所选目标归属证明的 as_of 与 epoch 相同。账户各自最后观察时间可以不同。

全部未 SUPERSEDED 的银行持仓证明必须与当前持仓投影的 ID 集合及数量完全一致。删除投影但保留当前银行证明，即便重新生成 complete manifest 也不能隐藏该持仓；孤立、重复、错误用户或无效证明关闭精确额度。SUPERSEDED 历史证据可以保留。

## 取得方式与动作结清

每笔持仓需要独立银行持仓证明的 purchase_transaction_id，并验证原交易是已发生的 BANK_CONFIRMED / DEBIT / ASSET_PURCHASE，金额及购买时间一致。不能以可编辑交易 category 判定购买或人工来源。同一购买交易在本 v1 中不能物化为两个持仓。

人工持仓必须明确 `acquisition=synthetic_user_manual_purchase`，没有 policy_version_id，也没有动作关联。`policy_version_id=null` 本身不能证明人工取得。人工持仓继续参与金融边界，但不占自动管理额度。

自动持仓必须明确 `acquisition=synthetic_auto_purchase`，并绑定 purchase_action_id / purchase_receipt_id / purchase_transaction_id。原授权的配置及确认绑定仍需可核验；不要求旧授权现在仍 ACTIVE，改版、暂停或撤销不能消灭未结清占用。使用原授权中的稳定 scope 键 general 或 goal_id，而非所选最新版本 ID 分桶；同 scope 的其他策略也累计。

每个动作必须有且仅有一个 settlement 声明。v1 支持以下明确形式：

| state | 必要条件 | 当前占用 |
| --- | --- | --- |
| RESERVED_UNDEBITED | PLANNED/AUTHORIZED；无回执、无物化 position；完整进口明确未扣账 | 全额占来源现金及 scope 管理额度 |
| MATERIALIZED | SUCCEEDED/RECONCILED；唯一成功回执，全额且零费零损失；动作→回执→银行扣款→持仓精确关联 | 只计当前持仓一次 |
| NO_EFFECT | FAILED/CANCELLED/INVALIDATED；完整进口明确无效果，全部已有回执只能 FAILED 且执行/费用/损失为零 | 不占额度 |

MATERIALIZED 声明额外提供 position_id、receipt_id、transaction_id；其他两种只含 action_id、state。字段形状、请求 hash、来源账户、准确产品/授权/归属与金额都需吻合；不能按相同金额推测关联。

SUBMITTED/UNKNOWN、部分执行、多个成功 attempt、不明或有执行额的失败回执、缺失结清关联，以及本 v1 尚未支持的赎回结清，都返回 EXPOSURE_RECONCILIATION_REQUIRED。尤其不累加 attempts 的 executed_cents、不凭 FAILED/INVALIDATED 标签释放预留、不凭 REDEEMED 标签推断余额已完成对账。后续执行与对账接口需扩展明确协议才能接入这些结果，本项不假造执行账本。

## 纯域输入与失败语义

AssetExposure 输出 scope、goal_id、epoch、本 scope managed_principal_cents / pending_purchase_cents、所有 scope 的 reserved_cash_by_account / reserved_goal_cash_by_goal、计入及排除的 ID 列表和证据 ID。HELD/REDEEMING/MATURED 自动本金仍占额度；UNKNOWN 关闭精确建议。

一般预留与目标预留分开传递：目标现金本来已经受保护，不能作为一般预留再扣一次；同一目标待购则减少该目标本次可配置现金。申购预留不是已完成购买，也不会由预览落库。多个并发预览仍可能提出竞争候选，实际原子占用由 MVP-301 实现。

来源错误通过 source_issues 交给纯域返回不足，金额为 null；内部用于携带失败的零值 DTO 不是对外宣称精确零。只有完整有效证明才能得出零占用或保留现金。input_digest 记录来源变化，纯域 selection_hash 绑定规范财务、授权、产品及退出方案；模拟收益不进入现金边界。

## 验证记录

真实 PostgreSQL 使用随机 bf_test 数据库和正式迁移。原始日志保留模块缺失红灯、错误银行来源账户的真实 red→green、孤立有效持仓证明的真实 red→green。定向测试覆盖完整人工零占用、完整集合/摘要/时序错误、跨授权版本和同 scope 策略的 pending、物化本金单计、重复 attempt、未知回执及目标当前双向引用。来源36项与HTTP4项合并运行40 passed / 76.95s，见[定向记录](../progress/evidence/MVP-204-service-api-final.txt)；完整项目验收和最终源码摘要另见[MVP-204进度](../progress/MVP-204.md)。
