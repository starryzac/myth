# 当前完整策略依赖读取

本包对应原完整开发计划第 298–318 行的不可变版本与依赖生命周期、FULL-104（第 1188–1190 行）。原生命周期确认、历史回执和已实现的动作重查继续使用原接口；本包补充用户可读取的当前声明依赖及引用变化。FULL-104 尚未全项关闭。

## 接口

`GET /api/v1/full-policy-dependencies/{policy_id}` 无 query、无 POST。服务入口为 `read_full_policy_dependencies(session, user_id, policy_id, now)`；调用必须已处于 clean `REPEATABLE READ / READ ONLY` Session。用户及 aware 时钟由服务端依赖提供。

读取当前模拟用户唯一 OPEN 周期内的全部 FullPolicy 根节点，验证完整原审计链和各节点原 FullPolicyVersion/Command/确认链，再按实际配置解析当前引用。当前周期策略数量、完整 IDs、读取节点数与历史周期策略数量分别返回。外部用户根返回 404，历史根返回 `ARCHIVED_DEPENDENCY_SOURCE`；历史原件仍由原历史接口读取。

响应 `FullPolicyDependencyReview`，协议 `full-policy-dependency-review-v1`。`COMPLETE_CURRENT_DECLARATION_GRAPH` 仅表示本次当前 FULL 声明根及引用分母可读取，并非资金安全、完整冲突求解或授权证明。缺原件、审计失败、数量不等或超容量返回 UNKNOWN；不得截断后返回 COMPLETE。

## 原件与变化

每个节点保存当前版本、配置及配置摘要，确认时原引用 `recorded_references`、本次原引用 `current_references` 原样保留。边按 role/kind/目标 UUID 配对，分别给出 UNCHANGED、CHANGED、ADDED、UNAVAILABLE 和两份 binding_hash。不重写原引用的 snapshot 或摘要。

FULL 引用有效状态来自实际当前 FullPolicy 版本生命周期；MVP 引用另外通过当前原 Policy/PolicyVersion 及原 `effective_status` 计算 `reference_effective_statuses`。例如未做状态刷新、原持久 ACTIVE 的已到期 MVP 引用，其原 snapshot 仍是 ACTIVE，单独 `current_target_status` 为 EXPIRED。状态展示不代表银行授权。

完整根的 `must_not_reduce_policy_ids` / `asset_policy_id` 形成有向声明图。强连通分量和一条实际闭环路径确定性返回；自环保留。环仅表示需要用户复核，不构造财务不可行、最小不可满足集或自动修复结论。Goal、Account、Evidence、MVP 策略是实际引用目标，不冒充额外 FULL 根。

图边必须来自本次真实 `FULL_POLICY` 引用 kind；跨表即使 UUID 相同，`MVP_POLICY` 引用也不连到同 UUID 的 FULL 根。当前引用缺失时旧确认引用只作为历史对照，不用于制造当前闭环。

## 限制

根容量 64，确认时与当前引用合计容量 512。超容量保留真实数量并返回 UNKNOWN，不生成截断后成功图。读取时请求内复用完整审计验证；没有跨请求授权缓存。所有响应的银行权限、资金写入、全模板动作重查、持仓/边界恢复验真、财务冲突求解标记固定 false。

本包不执行暂停、修改、解除硬约束、重新授权、动作失效、在途恢复或现金重算。用户审阅后仍须进入原当前版本预览和明确确认流程。未接全模板动作依赖、所有资产再检查及安全恢复的原 FULL-104 缺口保留。

## 检查边界

直接纯域/源接缝/HTTP 检查使用明确合成原件或服务源替身，不能证明实际银行或 PostgreSQL。唯一实际 PG 候选 `test_actual_current_dependencies_drift_complete_denominator_and_zero_writes` 已登记：真实确认两个未来 DatedExpense、完整当前图读取、目标真实新版本导致引用变化、外参 422/非本用户 404、全物理表零写。候选只 collection；实际执行由主代理串行排程。
