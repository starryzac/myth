# MVP-202：只读现金边界来源适配

`compute_user_boundary(session, user_id, now) -> BoundaryResponse` 将已验证的模拟数据库事实转换为纯金融 DTO，再调用 `compute_boundary`。返回 `simulation=true`、`user_id`、UTC `as_of`、`boundary`、`source_evidence_ids`、独立的 `input_digest` 和 `source_issues[{code,source_ref,message}]`。`boundary.financial_only=true`，结果不创建自动执行资格。公式、91 个本地日及其他语义见 [ADR 0005](../adr/0005-mvp-cash-boundary.md)。

服务不 INSERT、UPDATE、DELETE、flush 或 commit，使用 `session.no_autoflush`。HTTP 的数据库依赖提供 REPEATABLE READ；其他调用方也必须提供一致的读取事务。服务器提供带时区的可信时间，支持 Asia/Shanghai 和 UTC。不存在的模拟用户报 404；非法时钟、容量越限或无法构造一致输入报明确 422。所有用户记录按同一个 user_id 读取，产品目录为共享版本化配置。

## 来源绑定

余额、账单、持仓和本文的累计进口协议均只接受 BANK_CONFIRMED / VALID 证据；USER_DECLARED、MODEL_INFERRED 不可替代银行事实。检查 evidence.user_id、payload.user_id、规范 JSON SHA256、valid_from、valid_to、observed_at；证据在 as_of 时必须已经可知。相同实体有多份未 SUPERSEDED 证明时视为冲突，不能任意挑选一条。

种子 v3 补全身份和时点绑定，但不改变经济金额，也不改变 v1 全局产品条款：

| source_type | 必须与当前事实相符的 payload 字段 |
|---|---|
| SIMULATED_BANK_BALANCE | simulation、user_id、account_id、account_type、currency、balance_cents、as_of（Account.observed_at） |
| SIMULATED_CREDIT_CARD_BILL | simulation、user_id、bill_id、account_id、source_ref、statement_date、due_date、total_cents、minimum_due_cents、paid_cents、status |
| SIMULATED_BANK_POSITION | simulation、user_id、position_id、account_id、product_id、goal_id、policy_version_id、principal_cents、purchased_at、maturity_at、available_at、status、as_of（该持仓证明观察时点） |

账单还核对 evidence_id/source_ref 关联、信用卡账户类型、已经出账，以及 paid/status 自洽。PAID 但仍欠款等矛盾转为来源不足，不跳过该债务后给出精确额度。信用卡账户额度不进入现金；其他账户余额表示未投入产品的现金余额，持仓本金不与它相加冒充现款。

本服务验证当前进口记录之间的一致性，不声称这些模拟证明具有银行数字签名，也不声称已实现真实银行全量采集或完整外部债务账本。

## 已确认策略与历史义务

使用当前版本的严格 DSL、配置摘要和明确确认绑定，复用生命周期服务的证据检查，校验时钟始终为当前 as_of。未来生效但已经确认的版本参加未来保护，不因此获得当前执行权限。living_reserve 调用 MVP-201；仅 READY 的推荐值作为滚动底线，覆盖不足时不能用零代替。

自然到期的单一 ordinary recurring 版本保留原生成窗，已生成历史周期必须有结清进口证明；paid=0 会继续保护欠款，到期后不新增周期。暂停、撤销或旧版本修改若可能影响已经生成的周期，而金额或实际停止历史不能唯一复原，则返回 `HISTORICAL_OBLIGATION_RECONCILIATION_REQUIRED`，整体为 INSUFFICIENT_EVIDENCE、额度 null。不会把最后一次 Policy.updated_at 冒充首次暂停时间。

适配层检查所有已确认 ordinary 历史版本在其生效、确认和下一版本替代时点之间是否可能经过到期日。确认以来尚无到期周期的修改仍可以给出新版本的精确未来边界。这里没有新增债务事件账本，也不把旧版本潜在欠款自动转为推测金额。真实账单独立于付款策略保留。

## 累计事实进口协议

以下协议复用 EvidenceItem；它们是受信任服务器导入的模拟事实，不是 HTTP 客户端可提交的 complete 标记。本任务只读取，不生成实际付款、贡献或对账事实。所有协议含 simulation=true、user_id，时间统一为带 UTC 偏移的 ISO 格式。

| source_type / protocol | payload |
|---|---|
| SIMULATED_RECURRING_SETTLEMENT / recurring-settlement-v1 | policy_id、period=YYYY-MM、paid_cents、payee_id、complete=true、as_of |
| SIMULATED_GOAL_OWNERSHIP / goal-ownership-v1 | goal_id、policy_id、account_id（可 null）、allocated_cents、cash_owned_cents、principal_owned_cents、排序后的 position_ids、as_of |
| SIMULATED_GOAL_MONTH_CONTRIBUTION / goal-month-contribution-v1 | goal_id、period=YYYY-MM、contributed_cents、complete=true、as_of |

周期结清按 policy_id + period 唯一；paid 不超过该已确认周期上限，不能用于 bill_balance 的真实账单。缺旧周期证明不假设 paid=0。未来未预付周期按全部确认金额保护；不会按历史同收款人或同金额猜测已付。

累计进口的 as_of 必须不早于本次采用余额快照的最新 observed_at，并且不晚于其证据观察时间和服务器 now。目标的本月贡献证明与归属证明还必须使用同一个 as_of，不能让较新的贡献从最低额中抵扣，却仍保护较旧的目标现金。完整证明可以明确给出贡献 0；缺证明不等于 0。未来才开始、当前月份没有承诺的目标无需提供当前月贡献证明。

归属金额必须等于目标现金加当前未结清持仓本金；持仓身份和 goal_id 已由银行来源绑定。目标现金合计不能超过其账户余额。即使目标策略暂停、撤销或到期，已有归属仍然保护。GOAL 账户中未映射 Goal 的余额单独列为受保护现金，不默认为一般闲钱。

## 本金可用与产品条款

T0/T1 标签、maturity_at 或可赎回资格本身不生成到账。本金未来事件还需要唯一 `BANK_CONFIRMED / SIMULATED_PRINCIPAL_AVAILABILITY`：

```json
{
  "simulation": true,
  "user_id": "<UUID>",
  "protocol": "principal-availability-v1",
  "position_id": "<UUID>",
  "account_id": "<UUID>",
  "goal_id": null,
  "principal_cents": 100000,
  "available_at": "2026-10-10T16:00:00+00:00",
  "principal_return_bps": 10000,
  "rollover": false
}
```

上述内容必须绑定当前持仓及其 available_at，证据必须现在已经可知。仍未 REDEEMED 的持仓若报告 available_at 已到或早于 now，返回 `UNRECONCILED_POSITION_AVAILABILITY`；本任务不假装已完成余额和归属对账。未来目标本金到账时现金与该目标现金同增，不能扩大通用闲钱。

产品目录只有明确 `maturity_rule.protocol=fixed-principal-return-v1`、day_basis=CALENDAR、guaranteed=true、rollover=false 的合同才形成候选本金返还。需要 term_days、settlement_delay_days、principal_return_bps=10000；term_days 不短于 lock_days，settlement_delay_days 不短于目录 redemption_delay_days，本金波动、非零风险或 auto_rollover=true 均与保证回款冲突。冲突返回 INVALID_PRODUCT_TERMS。现有 v1 合成产品未提供完整此协议，按全窗占用计算金融上限；不为演示擅自升级条款。

## 哈希隔离与验证记录

纯金融 DTO 不包含未来工资、预测退款、accrued_yield_cents 或 annual_yield_bps。boundary_hash 由域中的规范金融输入计算，忽略来源审计摘要；input_digest 独立记录使用的证据 ID 与内容摘要。修改原始证明的展示收益并正确重算证明哈希，可以改变审计摘要，但不改变财务结果及 boundary_hash。

真实 PostgreSQL 测试覆盖正常种子 3462400−160000−145000=3157400 分、未来确认策略、来源身份/状态/哈希/可知时间、同实体冲突、累计进口时点和唯一性、目标现金与本金、旧欠款、生活方法、本金到账、目录条款和收益/未来收入隔离。各只读场景比较完整 16 表快照，未保存 DecisionRun、Policy 或动作。

原始红绿记录位于 `docs/progress/evidence/MVP-202-service-*.txt`：初始缺模块、未来策略遗漏、旧累计进口、账单/归属矛盾、历史欠款及期限矛盾、贡献与归属时点混用、自动续作与未对账本金均保留实际失败输出。最终服务测试与静态结果单独记录；完整项目验收由主任务统一执行。

冻结源码后的服务完整组为 44 项通过，81.32 秒（`MVP-202-service-final-green.txt`）；Ruff 与严格 mypy 两文件通过，见 `MVP-202-service-ruff.txt` / `MVP-202-service-mypy.txt`。这些是本适配层的模拟数据验证，不代表真实银行连通或 AUTO_EXECUTE 引擎已经实现。
