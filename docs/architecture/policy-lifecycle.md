# MVP-103 策略生命周期与版本

日期：2026-10-04（Asia/Shanghai）。实现范围：明确确认、版本替换、暂停、撤销、按时生效/到期、候选动作失效，以及版本数据库更新保护。这里没有执行资金动作、恢复资金或完成对账。

## 服务接口与事务

`app/services/policy_lifecycle.py` 导出：

```python
confirm_proposal(session, user_id, proposal_id, reviewed_hash, accepted, now)
change_policy(session, user_id, policy_id, expected_version_id, configuration,
              reviewed_hash, accepted, reason, idempotency_key, now)
suspend_policy(session, user_id, policy_id, expected_version_id, now)
revoke_policy(session, user_id, policy_id, expected_version_id, now)
refresh_time_states(session, user_id, now)
effective_status(policy, version, now)
is_version_authorized(session, user_id, version_id, now)
```

前四个命令返回 `LifecycleResult`；刷新返回 `TimeRefreshResult`。调用方提供可信、带时区的 `now`，API 不允许用户传入服务器时钟。`session` 必须处在调用方控制的事务中，服务不自行提交外层事务。每个写命令再使用 savepoint，保证调用方捕获异常后也不会留下半个策略修改。

锁顺序是用户行、策略/候选行、依赖动作行。当前按用户串行化生命周期写操作，以较简单的锁序获得明确一致性。相同预期版本的两次并发修改只有一次成功；另一条重新读到新版本后返回 409。未来执行器须在相容的事务/锁顺序中重验授权，不能把某次 `is_version_authorized()` 返回 true 当成可以脱离事务执行的令牌。

`PolicyLifecycleError` 提供 `code`、安全的 `message` 和 `status_code`。不存在或非本用户对象返回 404；陈旧版本、重复键不同内容、审阅摘要不符和不允许的状态变化返回 409；无明确确认、无效配置、证据、关联对象或日期返回 422。

## 确认及引用完整性

1. 锁定同用户候选，必须处于 PROPOSED，或是已有确认的合法幂等重放。
2. `accepted is True` 才能确认，不从自然语言、金额或候选可信度推导接受。
3. 用 `validate_configuration` 严格校验并补入显式缺省字段，再对规范化 JSON 求 `configuration_hash`。用户审阅摘要必须与这一规范化结构完全一致。配置模块本身不授予权限，详见 [策略配置](policy-configuration.md)。
4. 所有引用证据必须存在、属于同用户、状态为 VALID、已被观察、已进入有效区间且尚未失效；内容摘要必须等于实际内容的规范 JSON SHA-256。UNKNOWN、CONFLICTED、未来证据、失效或他用户证据均拒绝。
5. `goal_saving.asset_policy_id` 必须引用自己的资产授权策略；`asset_authorization(scope=goal).goal_id` 必须引用自己的已存在 Goal；账单金额规则必须引用自己的 CREDIT_CARD 账户。JSON 里的 UUID 不会因语法合法就绕过归属验证。
6. 在同一事务中创建 Policy、第一版 PolicyVersion 和 `USER_CONFIRMED_POLICY / POLICY_CONFIRMATION` 证据，更新候选的确认状态及策略引用。源证据为空允许用户从新的目标候选明确建立策略，但不会凭空生成银行事实。

确认内容绑定用户、策略、版本、审阅配置摘要、确认时间、命令键/摘要和解释后的有效区间。授权检查要求确认 Evidence 的整个内容等于不可变版本的 `confirmation`，且来源类型、级别、来源版本引用都正确。仅重新计算被篡改证据的内容哈希仍不足以冒充另一版确认。

## 状态与时间

| 情况 | 持久/有效状态与行为 |
|---|---|
| 候选首次确认，已到起点 | ACTIVE；确认时间之前绝不具备权限，即使配置起日更早。 |
| 候选首次确认，未来起点 | CONFIRMED；到起点后刷新为 ACTIVE。 |
| ACTIVE / CONFIRMED 修改 | 追加新版本，立即撤回旧版持续权限；新版未来生效则为 CONFIRMED，不沿用旧版填补空窗。 |
| SUSPENDED 修改 | 追加版本但仍为 SUSPENDED，不能借修改恢复权限。 |
| 暂停 | SUSPENDED，当前原型没有 resume 命令。 |
| 撤销 | REVOKED；重复撤销无额外资金副作用。 |
| 到达排他终点 | EXPIRED；即使定时刷新尚未运行，实时有效性检查也拒绝授权。 |
| EXPIRED / REVOKED 修改 | 拒绝；不能通过 PATCH 复活。 |

MODIFIED 不作为长期状态保存；一个成功修改事务直接得到新版本对应的稳定状态。

当前支持 Asia/Shanghai 与 UTC 用户日界。配置 `valid_from` 为日期时，从该本地日零点开始；省略时以明确确认时间开始。`valid_until` 为含当日的日历日期，落库转换为次日本地零点，作为排他终点。例如上海 `valid_until=2026-10-05` 对应 `2026-10-05T16:00:00Z` 到期。不能表示的日期极值、以及默认起点晚于结束时间，返回领域错误而非数据库异常。

`effective_status()` 只读计算，包括状态、确认记录绑定、配置摘要和有效区间；没有可验证确认的 ACTIVE 数据行也不会生效。`is_version_authorized()` 进一步要求该版本是该策略最新版本，并重新验证当前证据及完整确认内容。需要注意：这个函数是持续授权资格检查，不代替后续资金安全、金额、流动性、收款关系和执行幂等检查。

`refresh_time_states()` 实际保存未来生效和到期状态，并在到期时执行候选动作失效。相同时间重复刷新返回空的更新列表。写命令会先按需刷新目标策略；若之后的命令校验失败导致 savepoint 回滚，数据库状态可能仍待下一次刷新，但实时授权检查始终按时间拒绝过期版本。

## 版本与幂等回执

修改必须带 `expected_version_id`、新的完整配置、正确审阅摘要、明确接受、原因与幂等键。服务创建版本号加一的新行，旧行不改，`previous_hash` 指向上一版配置摘要，策略类型不能更换。

`PolicyVersion.content_hash` 是**规范化配置 JSON 的摘要**，不是整个版本全部元数据的密码学摘要。`previous_hash` 关联前一版配置摘要；不能把这两列宣传为完整版本链密码学防篡改证明。当前版本行的不可变性来自 0002 迁移的 PostgreSQL BEFORE UPDATE 触发器。

每个策略内的修改幂等键和请求摘要写入新版本 `confirmation`；请求摘要绑定用户、策略、预期版本、配置、原因与接受。相同键及相同请求返回原始结果；同键不同请求返回 409。候选重复确认返回原始第一版，不重复创建策略或确认凭证。

`LifecycleResult` 中的 `current_version_id`、`status`、`effective_status` 是**该命令首次成功提交时的历史回执**。幂等重放始终返回相同回执，不代表现在仍为 ACTIVE。版本 `impact_analysis.lifecycle_result` 保存该回执，响应字段描述也明确这一点。界面在 mutation 后应 GET 刷新当前状态，资金执行必须重新调用授权检查。测试证明撤销后重放旧确认/旧修改不会恢复权限或产生新版本。

## 旧动作失效和在途边界

修改、暂停、撤销和到期检查该策略的全部旧版本。受影响动作包含：

- 直接 `ActionPlan.policy_version_id` 引用；
- 所属 `DecisionRun.policy_version_ids` JSON 数组引用；
- 所属决策的 `DecisionConstraint.policy_version_id` 引用。

只有 PLANNED 或 AUTHORIZED，且没有任何执行回执的动作可以改为 INVALIDATED。已经 INVALIDATED 且无回执的依赖动作可以出现在重复命令的失效清单中，不重复执行资金副作用。

SUBMITTED、UNKNOWN 以及已有回执却处于未提交等异常状态的动作保持原状态；FAILED 但最新回执为 UNKNOWN 或已执行金额大于零的矛盾记录也进入 `inflight_action_ids`。该字段表示需要在途核验或对账，不表示服务已经解决不确定结果。请求内容、请求哈希、幂等键、策略版本引用均不改；不把它们改为 FAILED 后重付，不为成功/历史动作改绑新版本。SUCCEEDED、RECONCILED 等历史记录保持不变。

任何受影响在途动作都必须交给后续执行/对账/恢复模块，当前生命周期服务不发起赎回、冲正或转账。

## 目标投影及不可变迁移

已有 Goal 在策略修改后更新配置投影与准确版本引用，包括目标金额、期限、月度范围、优先级和许可字段。`allocated_cents`、账户余额、资产持仓本金和持仓原授权版本均不改。确认新 goal_saving 配置并不自行创建账户或划入资金；目标建立及分配由后续目标任务处理。

`0002_immutable_policy_versions` 创建拒绝所有 PolicyVersion UPDATE 的触发器。它没有禁止 DELETE，也不声称阻挡数据库管理员删除或禁用触发器；受控演示重置依然可以删除该演示用户记录。迁移降级仍仅允许随机 `bf_test_<32 hex>` 库，拒绝普通库与离线降级。没有修改初始 0001 迁移或 ORM 表结构。

## 真实验证

最初红灯保存在 `docs/progress/evidence/MVP-103-red.txt`。最终必要回归日志为 `MVP-103-service-regression.txt`，真实 PostgreSQL 16 共 49 项通过：29 个生命周期用例、11 个迁移/元数据用例、9 个演示种子用例。

关键覆盖包括明确接受及审阅匹配、版本追加和重放、暂停/撤销/未来生效/排他到期、所有权和坏证据拒绝、两种证据篡改、多旧版本直接/间接依赖、在途与异常回执保护、并发相同基线只有一成功、数据库更新触发器、版本插入故障后证据/策略/动作完整回滚、以及 Goal 更新不移动归属资金和持仓。0002 升降级后，16 表结构与 metadata 保持一致，受控演示重置仍通过。

Ruff 与 strict mypy 定向检查通过。项目完整 API、类型和质量门由主代理集成记录，本文件不声称资金执行或初版全部功能已经完成。
