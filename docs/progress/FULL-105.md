# FULL-105：修改前的真实来源只读预览

## 实现状态与原验收状态

2026-10-05 已实现 `POST /api/v1/full-policies/{policy_id}/change-preview`。它严格校验当前 FULL 版本与候选 Schema，在 RR REPEATABLE READ + READ ONLY 事务中读实际金融事实、原引用版本及当前边界，返回配置差异与明确未实现的候选金融影响。功能可调用；原 FULL-105 **PARTIAL，未关闭**。候选资金/目标/持仓/未来动作的完整计算器未接，预览与实际确认后的金融差分一致性尚无实测。

## 文件与设计

本包代码、API与风险测试与 [FULL-104](FULL-104.md) 共用；不修改旧 MVP 预览、旧配置哈希、银行状态机或审计历史。主应用及只读依赖由 root 注册。

输入仅 `expected_version_id` 与 `configuration`，拒绝 owner、业务 clock、金额效果、permission、grant、成功结果等未知字段。候选按已有 FULL_V1 域模型校验，引用按实际 owner/ID/版本/原证据读取，不能将格式正确的 UUID 当存在或已授权。旧周期只读历史不接受当前修改预览；过期/撤销与并发版本不符拒绝。候选验证成功不赋 ACTIVE 或确认权限。

`FullChangePreview` 包含：

- `before_configuration`、规范化 `after_configuration`、其 `configuration_hash` 和真实 `changed_fields`；缺字段与 null 的差别保留。
- 实际 `current_fact_digest`、`current_financial_boundary`，由原验证资金上下文及确定性边界计算器产生。
- 实际 `reference_snapshots`、`relevant_goal_ids`、`relevant_position_ids`、`relevant_current_action_ids`，用于解释影响涉及哪些当前来源；这些不是已计算完的未来动作失效清单。
- `candidate_financial_status=NOT_IMPLEMENTED`，`delta_safe_idle_cents`、`delta_goal_allocation_cents`、`delta_position_principal_cents` 均为 **null**；`future_action_impact=NOT_IMPLEMENTED_NO_FULL_EXECUTION_ADAPTER`。不存在计算器时不填0或成功。
- `preview_only=true`、`bank_authority=false`、`dedicated_audit_event=false`，不写候选版本、确认、Evidence、ActionPlan、银行操作或收据。

只读入口要求干净 Session、实际 RR 与 transaction_read_only=on。持久确认使用独立写入入口和用户明确审核 hash；预览本身不能被当确认 receipt 或当前银行 authority。

## 已运行检查

本包直接检查和原日志在 [FULL-104](FULL-104.md) 及 `.runtime/FULL-104-105-pure-contract/`：相关纯/API及原 Schema兼容 **442 PASS / 4.37s**，严格 mypy5/Ruff/format PASS。五个本包文件源码冻结原件 `source-freeze-20261005T135412Z/manifest.json` 保留，旧 Ruff RED 未覆盖。纯/API test doubles 不表示 PostgreSQL 或金融实测。

实际隔离 PG 候选 `test_readonly_preview_uses_actual_sources_and_leaves_all_rows_unchanged` 核当前真实 factdigest/boundary、name 差异、候选影响 null 与全表前后完全一致；已 collection，**NOT_RUN**。其余七个生命周期/原键/历史风险节点也仅收集，由 root 串行安排。本包未运行数据库、浏览器或全量。

## 具体未覆盖与下一前置

尚需确定性 FULL 联合决策、目标分配与资产能力适配，才能计算候选确认前后的可用自主资金、目标金额、持仓本金及未来动作影响。必须从相同可信事实截面独立计算前后，不能因已复用原当前边界或文件而关闭 FULL-105。随后需要实际确认后的差分与预览一致、无写只读、错误来源/版本拒绝及前端真实操作证据。当前 null 保留上述缺口，不以已有预览接口成功代表完成原金融影响要求。

## 2026-10-05 13:55 UTC 实际验证追加

root 已运行本包实际隔离 PG 八节点，**8 PASS / 27.34s**，原包装 **29.367692s / exit0**，全源与scoped源码均稳定。原 [manifest](evidence/W2/full-policy-lifecycle-preview-history-original-key-real-pg-20261005T135521Z-7b52013c/manifest.json) 保留实际命令与SHA。预览节点真实检查当前原factdigest/boundary、候选name差异、影响null及全部表前后相同；生命周期和原键历史风险同批通过。它没有运行未实现的 FULL 候选金融计算器，不能证明预览与实际金融差分一致；原 FULL-105 仍未关闭。此前 NOT_RUN/collection 原记录保留。

## 2026-10-05 23:03 UTC：未来义务金融预览增量，尚待接线及真实验证

新增独立 `domain/full_policy_change_impact.py`、`services/full_policy_change_impact.py` 和两个直接测试文件。不修改上文原生命周期、原预览、共享银行/审计/保护算法或已有失败证据。此增量是生产规划能力，原 FULL-105 仍 **PARTIAL / PENDING**。

`preview_full_policy_financial_impact(session, user_id, policy_id, FullPreviewRequest, now)` 要求干净的真实 RR/RO Session。它重验当前用户、周期、完整策略历史/确认、当前版本、StrictConfig、候选引用及有效窗口；只调用一次真实 `compute_full_annual_protection`，由原服务校验银行、收入、占用、当前审计链及全部现有保护。随后在同一事务内窄读现金、产品、目标、持仓与全部当前动作原件；没有跨请求授权缓存，不持久化假候选版本、证据或结算。

域函数 `project_full_policy_change` 只支持 `DatedExpensePolicy` 和 `PeriodicTransferPolicy` 的假设性未来承诺替换：

- 基础为原完整年度保护曲线；原90天结果、365天原曲线及哈希均保持。366个日界面（今天+365个未来日）各3个阶段，共1098点；原顺序、日期、现金、保护、原本金可用时点、全部产品分母与占用不缩减。
- 今天及逾期的原同策略义务冻结，其他MVP/FULL义务、生活/应急/目标保护和占用冻结。周期当前已到期的整月冻结，不因把到期日移后再生成当月新付款。候选只从本地明天或更晚的候选有效日进入；月末按真实月份夹日，终止日沿原服务的排他时间边界解释。
- 支出取登记max、周期转账取exact或range max；这些是保守条件待支付量，并非实际账单、已付0或银行到账事实。未来收入计入0。所有候选明确 `UNCONFIRMED_CANDIDATE`、`hypothetical=true`、`grants_authority=false`，不能被解释成已确认的原策略版本。
- 返回原/候选全曲线、最低余量、安全闲置和逐原产品条件容量差量。产品容量保留原本金返还日三阶段先后；这只是财务条件容量，不等于可自动购买金额或新授权。周期来源账户不足标记流动性风险；该检查不声称已完成未来所有账户流出定位。
- 当前Goal现金/本金归属以及持仓原本金记录、当前未结清本金分别列原件，当前只读变化为0；未来目标分配和持仓处置保持UNKNOWN。当前动作完整列原ID，预览不修改或失效动作，确认后仍须真实重新验源、重查依赖并计算。
- 银行/收入/审计/引用/完整分母不明、当前策略旧版本欠付不明、候选今天或历史义务不明均返回UNKNOWN，曲线及金融差量为null；已实际占用CASH大于原年度占用floor时同样UNKNOWN，不扩大候选容量。原策略已结算及跨版本历史没有完整Full结清桥，不能通过候选假设解除此门。

返回 `FullPolicyChangeFinancialPreview` 显式保留 `expected_version_id`、`current_configuration_hash`、候选 `configuration_hash`、配置/字段差异、当前原件摘要和候选引用。新事实摘要协议为 `full-change-preview-actual-facts-v1`，绑定user/epoch/server as_of、原FullView、原年度输入digest、候选refs、所有原Action/Position/Goal/Product以及活跃CASH占用。它不是旧 `full-policy-preview-facts-v1`，不可把两者当同一摘要；也不稳定跨读取时点。确认仍只走原完整配置审核hash与当前版本检查，不能拿曲线hash授权。

### 本增量的实际检查

最终20个手算/直接风险用例 **PASS / 5.80s**，包装7秒左右；四文件strict mypy、Ruff及format均PASS，以下终态原件均scoped/global source stable=true。不是数据库、银行执行、浏览器或正式全量证据。

- [20个直接风险终态](evidence/W2/full-policy-financial-change-final-capability-direct-20261005T230219Z-cb638270/manifest.json)
- [四文件严格类型](evidence/W2/full-policy-financial-change-final-capability-types-20261005T230219Z-383d0137/manifest.json)
- [四文件静态检查](evidence/W2/full-policy-financial-change-final-capability-static-20261005T230220Z-1380583e/manifest.json)
- [四文件格式检查](evidence/W2/full-policy-financial-change-final-capability-format-20261005T230220Z-3d06784f/manifest.json)

首轮mypy4错误、Ruff2错误及后续纯格式RED均保留原日志/源码；修复是本增量局部差量，没有覆盖旧失败。唯一PG候选 `test_full_policy_change_impact_integration.py::test_actual_confirmed_full_future_change_preview_retains_all_originals_and_unknown_on_tamper` 已collection（不运行fixture），实际执行 **NOT_RUN**。它计划验证真实Full确认、1098点差量、原年度相同、重复读取、所有物理表零写、旧版本拒绝及银行余额篡改UNKNOWN；不提前写成功。

### 仍未覆盖及下一接缝

原公共预览与前端没有被本包改动，root需在共享源解除hold后接路由/响应及页面消费，并保留旧事实摘要协议。新独立服务不调用旧preview，不重复原金融上下文加载；接入旧preview时不能简单先跑旧preview再额外重跑两次完整上下文。可以采用独立只读financial preview响应，或由root提供同请求明确来源的上下文接缝。

其余10模板的完整候选金融反事实未实现：RecurringObligation/LivingReserve/EmergencyBuffer仍沿MVP入口；LongTermGoal沿双摘要Goal入口；AssetAuthorization/Recovery/GoalAllocation/CrossGoalReallocation的候选授权、处置与分配需要其真实规划/确认消费者；Seasonal没有actual adopted额，Intervention没有资金公式，均不因Schema存在填金融0。新增protected reference若不在当前完整已验真保护引用中亦UNKNOWN。实际确认后的同截面差分一致、完整历史结清、未来动作重算及全模板UI/接口实证仍是原FULL-105关闭前置，不能以本两模板部分能力关闭编号。
