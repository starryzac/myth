# 四级自主性只读适配（MVP-302）

入口位于 `app/services/autonomy.py`：

- `assess_intent(session, user_id, intent, now)`：评估用户表达的最小意图，服务器生成确定性经济载荷。
- `assess_action(session, user_id, action_id, now)`：保留未提交动作的原载荷、原精确版本与确认记录，再作当前检查。
- `assess_transfer_preferences(session, user_id, source_account_id, destination_account_id, amount_options, now)`：内部有限金额偏好评估，没有 HTTP worlds 接口。

结果 `AutonomyResponse` 包含用户、服务器时间、`decision`、可空 `effect`、可空原 `action_id`、来源证据 ID 和 `input_digest`。分类器由 `app/domain/autonomy.py` 提供。结果的 `evaluation_only=true` 不创建执行权限；301 必须在真正执行前持用户锁重新校验。

## 来源、权限与财务分别验证

首先校验对象属于当前模拟用户，跨用户及不存在对象返回 404。共享 boundary、完整 asset exposure、独立银行余额/持仓投影和已存在的新资金账目必须一致；证据缺失或冲突不能变成“问用户选一个银行余额”。普通规划失败保留机器错误代码并阻断，不按中文错误文本猜测自主等级。

正式策略须有当前版本、规范化配置摘要、完整当前有效来源及绑定用户/策略/版本/配置摘要/确认时间的 `USER_CONFIRMED_POLICY`。完整已知暂停、撤销、未生效等状态的新意图只允许修改策略的建议：没有经济载荷，`financial_evaluation=NOT_EVALUATED`，执行资格为 false。已知财务缺口优先阻断此类未估值建议。旧动作版本或失效状态不能借此重新生成当前版本建议。

本人划转没有持续策略。其 authority 证据只证明显式请求所指两个账户属于用户且余额来源有效，引用验证过的 `SIMULATED_BANK_BALANCE`。这不是新造策略确认；纯域还要求 `USER_EXPLICIT`，并始终归入 ASK_ONCE。精确确认后可将当前执行资格变为 true，等级仍为 ASK_ONCE，不能计为无人介入自动执行。

周期付款先核验已有银行收款身份，再判断是否只能建议修改暂停策略。未知收款关系始终阻断，包括暂停策略中的未知收款人。本项不建立新的收款关系。

正常可构造载荷复用 301 的 `plan_execution_effect`、`load_execution_context` 和 `revalidate_execution`，不会替换正式版本、账户、本金、资金来源、收款人、报价或保护规则。Goal/资产双权限从原载荷的完整精确版本集合重新校验。

目标链另有 3 项实际服务集成覆盖：通过公开零目标创建、真实 301 目标储备后形成已归属现金，再明确绑定目标与资产策略；合法新申购及原准备动作都为 AUTO_EXECUTE。目标策略暂停或修订后，旧动作保持原版本并被阻断，不能借仍有效的资产权限执行。该组首次运行即通过，属于补足覆盖，没有声称复现或修复不存在的生产缺陷。

## 有损退出的财务建议边界

只有费用或损失大于零、唯一问题为机器类型 `EXECUTION_REDEMPTION_PERMISSION_DENIED`、其余银行/原购买/当前事实完整时，才允许专用只读财务评估。适配器独立重读精确银行报价，在局部 context 副本中移除这一权限否定，运行原财务后态校验；权限定性始终保持 `OUTSIDE_AUTHORITY`，最终最多 ADVISE_ONLY，执行资格始终 false。不会写回 context 或传给执行器。

原持仓与报价绑定、净本金/费用/损失、所有未来保护检查点仍是硬门。改善眼前负值但破坏未来已安全检查点的方案仍 BLOCKED。真实测试还调用 301 `prepare_action`，证明相同无权方案继续被拒绝，数据库不变。

零费用零损失的权限否定不走这一例外，因为原 301 自动资格还可能包含不可跳过的产品合同检查；只返回未估值的权限建议，已知基线风险则阻断。明确人工仓缺原自动权限同样不能被新策略收编。

## 有限偏好与数据库快照

内部接口仅支持一个 `amount_cents` 用户偏好，2 至 8 个唯一正整数分，不接受 bool、越界数值或银行事实变量。金额排序后构造每个真实 planner/context/财务结果，未选择候选不返回单个可执行载荷。安全性或后果分歧要求澄清，但不授权执行；任何候选的根本来源问题仍阻断。

所有候选在同一独立 PostgreSQL **REPEATABLE READ、READ ONLY** 事务内计算。新连接来自调用 Session 绑定的同一数据库，在首次读取前设置隔离及只读；不修改、提交或回滚调用方事务。仅读取已提交事实。`session.new/dirty/deleted` 非空时拒绝调用；这只是防误用门，不声称可识别调用方一切已经 flush 但未提交的状态——这些状态在新事务中不可见。

共同摘要覆盖实际 boundary snapshot、正式版本、持仓、完整产品目录、曝光、新资金来源账目和已验证来源摘要，并包含用户与服务器时间；不由候选 effect 或随机操作 ID 生成。每个候选使用同一快照。真实两连接测试在首个候选后提交新的余额/证明，断言本轮世界仍统一读取旧完整事实，下一轮看到变化并因独立银行矛盾阻断。测试读取 PostgreSQL `SHOW transaction_isolation` 与 `SHOW transaction_read_only` 验证真实隔离，而非只检查配置字符串。

## 历史动作与副作用

已经提交、UNKNOWN、银行存在操作、已成功或已对账动作返回 409 `NO_RECLASSIFICATION_AFTER_ACCEPTANCE`。用户继续通过 301 原动作/回执只读查询获取历史经济事实；本入口不因当前策略撤销而重新分级、重新付款或释放占用。未提交旧动作绑定原 request hash、原 effect hash 和原意图，过期或旧版本阻断。

评估不写 ActionPlan、资源预留、银行操作、分录、回执、Goal、余额或策略。真实 PostgreSQL 四级矩阵、精确确认、同输入复现、租户边界、有损退出和有限快照测试对照全部 20 张表快照。301 的执行与银行协议保持原有权限门。
