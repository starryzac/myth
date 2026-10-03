# 自主资金边界查询

`GET /api/v1/boundary` 读取服务端模拟用户、可信时钟和一个 REPEATABLE READ 数据库快照。初版固定今日至第 90 日；接口不接受客户端账户余额、时间、目标归属或权限，也不允许通过查询参数缩短预测窗。

响应包括 simulation、user_id、as_of、boundary、source_evidence_ids、input_digest 和 source_issues。boundary 含：

- status：READY、LIQUIDITY_RISK 或 INSUFFICIENT_EVIDENCE。
- financial_only=true：只是财务必要条件，未授予执行权限。
- safe_idle_cents：整个窗口持续占用的一般资金上限。
- minimum_margin_cents 和 deficit_cents：保留有符号余量与真实缺口。
- protected_cents_by_reason：保护义务、生活、应急、目标现金及目标最低的分项。
- max_allocatable_by_product：按产品已证明返还条款得到的金融上限，后续还要满足资产授权和可行性限制。
- blocking_constraints、calculation_trace 和 boundary_hash：拒绝原因、各日期和事件阶段的计算、确定性财务摘要。
- calculation_notes：未出账账单等模型边界与需要重算的条件。

source_issues 是证据校验问题；证据不足时 safe_idle_cents 等无法证明的精确数值为 null。input_digest 留存来源快照，和排除未来收入/展示收益的 boundary_hash 分开。不得把 null 当成零，也不得把 READY 或正金融上限直接转为 AUTO_EXECUTE。

估算不写策略、目标、决策、动作、回执或资金事实。新账单、新消费、来源更正和政策修改都须重新计算；已有目标归属不会因停止策略自动释放。91 日投影只基于当前已知承诺及规则，未出账的未知新债务不被虚构为已知金额。

HTTP 验收测试使用真实临时 PostgreSQL，覆盖默认事实、候选/确认差异、账单去重、目标账户篡改和全表只读快照。开发阶段已记录实现前 404，三组 HTTP 与完整检查现均通过，结果见 [MVP-202 进度](../progress/MVP-202.md) 和实际日志。
