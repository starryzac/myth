# FULL-303/304 专用现金回拨执行协议

此包延续现金来源证明与只读最低修复数学。独立的新协议保留原 MVP ExecutionEffect、原 ASSIGNED 收入分母与全部旧历史哈希。实现状态与需求验收状态分开；尚未完成金融执行验收。

## 当前实现

- 新 `domain/full_goal_release_execution.py` 严格定义原授权/Full版本/Goal原版本/来源片段绑定、有限时窗和金额。金额为严格整数分；零费用、零损失、零本金和零收入再分类不能接受布尔或非零值。
- 新命令仅有源Goal现金账户减额、同用户普通现金保护账户增额、Goal现金归属减额三条原腿。经济维度两腿守恒；归属维度独立，不把三腿错误求和。原 LOT_AVAILABLE、ASSIGNED、SPENT 均不修改。
- 来源按原 allocation action × income fragment 扣除已验真实际 SETTLED 回拨；不推测 FIFO，不把 UNKNOWN V1 来源升级。原来源不全、本金变化、未结算源目标回拨、重复与过量释放均保留 UNKNOWN/null。
- 全 FullPolicy 所有版本的累计口径分别保留 settled、accepted_reserved、cap_occupied。ACCEPTED 且无经济腿占用限额，不能冒充已付款；UNKNOWN 保留未知用量；已 SETTLED 即使服务无回执也计入银行用量。完整真实零库存可以支持零，而缺库存不推测零。

局部三腿核验不是完整银行账本或当前权限证明。专用授权必须在生产读取中重新验证实际当前 owner/epoch/版本/证据，执行前必须再核真实缺口、全部保护点、当前最低保障与累计用量。

## 验证

仅直接纯合同风险：37 PASS，0.99 秒；两源 strict mypy、Ruff、format PASS。首轮类型检查的九个测试字典注解错误日志和原源码保留在 `.runtime/FULL-303-304/release-execution/first-type-red-source`；不修改首轮结果。此处全部夹具是工具/合同测试，不是金融效果实测。

实际 PostgreSQL、银行、投影、恢复测试本包尚未运行。后续库存与生产服务将由父任务串行安排真实隔离数据库风险节点。旧默认锁和跨Goal拒绝负例保持原样。

## 库存读取接口

新增 `services/full_goal_release_inventory.py` 的 `read_goal_release_inventory(session, user_id, goal_id, expected_epoch_id, expected_goal_policy_version_id, full_policy_id, now)` 必须运行于真实 RR READ ONLY 快照；不接受客户端金额、原来源证明或金融真假标签。返回 original_basis、逐来源 residual、release_uses_available、全策略各版本 policy_usage、完整原四表库存及逐原行摘要、银行真实已核/应用已投影分别状态与当前完整 source_binding_hash。

首次来源采用实际 V1 VERIFIED_CASH_ONLY 证明。已有新回拨只能取经原请求/行动/来源摘要核验的服务保留基础，重新检查其全部银行/行动/回执/Posting 原行以及完整收入来源、原 ASSIGNED 分母。新的非回拨银行活动、新收入分母或无法分割的资产活动不会通过旧基础猜分割。缺失/篡改/不完整及 UNKNOWN 均无可用来源清单；银行已结算无服务回执仍扣准确来源，当前应用投影真假另列，不能以服务失败恢复累计可用额度。

库存直接纯风险 13 PASS，1.66 秒；strict 两源 PASS。首轮 Ruff 单个未使用导入失败与原源码保留，窄删导入后 Ruff/format PASS。这里只验证原件合同与拒绝门，实际 SQL 库存、完整银行、原授权与真实执行尚待父任务实际 PostgreSQL 链验证，不能当成产品金融证据。

## 生产执行服务与明确逐行动确认

新增 `services/full_goal_release_execution.py` 与 `api/v1/full_goal_release_execution.py`。POST `/api/v1/goal-cash-releases/preview` 和 `/prepare` 只接收原策略/源Goal/双方原版本/原epoch、专用授权的原epoch和原幂等键、同用户普通 CASH 目的账户和本行动原键，不接收金额、紧急结论、金融原件、clock 或 grant。服务从当前真实 303 数学、完整现金来源/全策略各版本用量、当前专用授权及 Full 1098 个保护点计算唯一最低修复。未知或未来账户扣款尚未证明时不能形成 READY；原只读 303 的 UNKNOWN/未授权说明保持原样，新执行能力另列。

原动作保持 ASK_ONCE。POST `/actions/{action_id}/execute` 必须为 `{accepted: true, reviewed_effect_hash: 原效果哈希, expected_epoch_id: 原epoch}`，严格拒绝 1、字符串、False、缺少确认、错效果、错epoch与额外金额。应用第一阶段在锁内重新读取实际当前数学和许可，创建独立 `USER_GOAL_RELEASE_ACTION_CONFIRMATION` 原 Evidence，记录引用该 Evidence 的实际状态审计，再提交 SUBMITTED。独立银行受理也核此原确认；不会用专用范围授权替代逐行动确认。响应列原确认 Evidence ID 和是否验真，不授予新权限。

采用明确 B 协议：prepare 为 `PLANNED_UNRESERVED`，第一阶段为 `RELEASE_PENDING_UNRESERVED`，不创建或修改 Income/ResourceReservation。原 Goal 默认锁继续保留；独立银行持当前 user 锁，fresh 读取完整当前来源/金额/最低保障/专用范围/累计限额，然后在该事务提交真实三腿。应用提交、银行结算、应用投影与原回执分别处理。投影有实际 savepoint；失败保留 UNKNOWN 与已提交银行原件。原收入 ledger 仅可刷新观察时间，原来源、ASSIGNED/SPENT/AVAILABLE、收入用途及其他目标/本金金额保持不变。

GET `/actions/{action_id}` 读取原完整命令/请求哈希、银行三腿、原回执、原逐行动确认；GET `/commands/{epoch_id}/by-key/{key}` 的未找到状态为 `NOT_FOUND_NOT_FINAL`，不能推断未提交或创建替换键。已 SETTLED 原银行键恢复仍要求显式接受同原效果/epoch，但只验历史原请求、原确认、三腿及投影，不要求已撤销的当前范围重新授权，不重扣。完成状态必须有真实银行和原回执；`receipt_is_current_authority` 与 `economic_experiment_verified` 保持 false。

## 生产包局部检查与尚未运行范围

服务/API 直接纯风险 36 PASS，2.61 秒；五源 strict mypy、Ruff 和 format PASS。真实隔离 PG 候选仅 collection：1 个节点，2.92 秒；没有运行数据库、银行或浏览器。纯服务双替身与 DTO 夹具不是金融效果证据。原空确认协议、首轮类型错误、Ruff 长函数名错误和一次 HTTP UUID 拒绝的全部日志/原源码保留在 `.runtime/FULL-303-304/release-service`，不重标成功。

唯一待父任务运行节点为 `test_full_goal_release_execution_integration.py::test_actual_goal_cash_release_consent_response_loss_rollback_and_original_key_recovery`。候选通过真实现有收入入口、Goal 分配、Full 模型/规划确认和专用许可入口构建功能风险前置；不是冻结实验输入，也不声称真人授权研究。它要求实际最低修复 10000、1098 点不恶化、明确逐行动确认、真实 bank COMMIT 后响应丢失、真实投影 rollback、撤销后按原键恢复、恰好三腿/一回执/两条 INTERNAL_TRANSFER 原交易、原收入分母不变、最终 EXACT audit 全 VALID，重复 execute/prepare/GET 与原回执读取后全部实际物理表零写。上述真实断言当前均为 NOT_RUN，验收状态仍未关闭。

明确未覆盖：进程硬终止和多进程并发实测、资产购买/赎回后无法精确分割的来源、跨新的收入/普通金融活动后旧基础不足的重新证明、Full 未来账户调整未证明范围、真实银行、真实用户研究与初版/完整版全量验收。缺口返回 UNKNOWN 或拒绝，不能用局部三腿、成功字符串或新服务可导入代替完成证据。
