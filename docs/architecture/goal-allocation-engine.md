# 单目标新增资金候选引擎

范围遵循 [ADR 0006](../adr/0006-goal-new-funds-allocation.md)。纯函数只规划一个明确目标，保留所有其他目标及资金约束；不写数据库、不读取系统时间、不访问网络，不分配执行权限。FULL 多目标联合优化和跨目标回拨不在本项范围。

## 公共接口

`app.domain.goal_allocation` 导出 `IncomeLot`、`LotAllocation`、`GoalAllocationResult`、`ALGORITHM_VERSION` 和：

```python
plan_goal_allocation(
    goal_id, policy_version_id, snapshot,
    active_policy_versions, positions, products, lots,
    *, source_issues=(),
) -> GoalAllocationResult
```

`snapshot` 是完整的 MVP-202 `BoundarySnapshot`，策略、持仓和产品也使用既有 DTO。必须包括目标归属和当前本地自然月累计贡献；当前月份由快照的 `as_of` 和 `timezone` 唯一决定。目标必须对应所指定的精确策略版本，该版本在当前时刻已经确认且有效，且本地日期不晚于目标 deadline。截止当天允许，次日起不新增贡献，返回 INACTIVE_POLICY / GOAL_DEADLINE_PASSED；不改写截止日或收回既有归属。持久状态 ACTIVE、版本是否当前以及来源真实性由服务验证；领域重新检查关联、时间窗、配置哈希和全部财务约束。

`IncomeLot` 包含原始交易 UUID、原始 CASH 账户 UUID、`amount_cents`、`available_cents`、`occurred_at`、`observed_at`、证据 UUID。方向、来源和经济角色只能为 CREDIT、BANK_CONFIRMED、INCOME。字面值类型不是证明：适配层须先核对可信来源声明、银行事实、全部实际消费、成功归属和在途预留，才能构造 DTO。领域不通过金额或交易类别自行推断来源尚未消费。

`source_issues` 复用 MVP-202 的 `SourceIssue`。缺完整来源声明须传入问题；完整可信的空清单或 `available_cents=0` 可以计算为精确零。来源经济角色由银行事实决定，消费分类的修改不能制造收入资格。

## 输入失败门

- 金额使用非负整数分，拒绝布尔、浮点及超出 bigint 的输入；原始入账金额必须大于零，可用余额不得超过原始金额。
- 所有时间必须带时区，观察时间不得早于发生时间。发生、观察都不得在快照未来；发生时间不得晚于对应来源账户余额的观察时间。
- 来源仅限原始 CASH 账户。每个原始交易 UUID 只能出现一次，包括可用余额为零和当前目标不合资格的来源。
- 按源账户核对全部 lot 的可用合计，不得超过该账户余额扣除已归属目标现金后的金额。其他账户的旧余额不能补足它。已有目标现金缺少账户映射时关闭精确规划。
- 每次调用最多 10000 个 lot、1000 个来源问题。完整财务上下文继续受 MVP-202 的容量及唯一性检查。每个输入模型都重新验证，`model_copy` 构造的非法字段也不能绕过严格类型。
- 适配层必须确认输入的来源、归属、本月贡献与采用余额属于一致的完整 epoch；领域 DTO 不复制整套来源证据账本。

重复、矛盾字段或容量超限抛出 `ValueError` / Pydantic `ValidationError`。缺事实和不确定来源返回状态及原因，不伪造精确额度。

## 候选金额

先独立调用 `compute_boundary` 重算基线。基线缺证或已有负余量时不搜索改善动作；这部分属于另行冻结的恢复语义。目标账户可以是本人 CASH 或 GOAL，缺目的账户不能形成候选。

每项本月剩余额度为 `max(0, 月额度 - 已贡献)`，再由目标总额剩余缺口封顶。合格来源的发生时间必须不早于 `max(当前版本 confirmed_at, valid_from)`。同日确认前收入、旧版本下较早收入不会隐式继承资格。未消费来源可以跨月保留，月额度重置不重建来源。

上界为剩余 target、剩余 max、总目标缺口和合格来源可用合计的最小值。默认接近 target；不会因为还有钱就自动填满 max。

对候选 x，按发生时间、原始交易 UUID 稳定 FIFO 分解来源用量，然后构造假设后态：

1. 来源账户减去相应用量，目的账户加 x；同账户的增减抵消，合并现金不变。
2. 仅当前目标的现金归属、累计归属、本月累计贡献各加 x。
3. 其他目标、持仓本金、未映射 GOAL 余额和全部义务保持原值。
4. 使用所有策略、持仓及产品重新计算 MVP-202 的 91 个日期、273 个检查点。

不直接从受保护金额中减 x。当前最低可能已被保护，贡献后由同一边界引擎重新计算未完成最低，避免重复扣除；未来月份和其他目标的最低不能被释放。即使策略未来到期，新归属仍然保护。

固定输入下，现金归属增加 x，未完成最低保护的下降不快于 x，因此财务余量不随 x 增加。按整数分二分求上界内最大安全金额，最多需要 bigint 位数范围内的搜索轮次。没有浮点搜索和小数分舍入。

## 返回语义

所有结果都固定 `financial_only=true`、`preview_only=true`。

| 状态 | suggested / max_safe | 候选及来源用量 |
|---|---|---|
| READY | 精确金额，可以为零 | `candidate_boundary` 为建议金额的假设后态；`lot_allocations` 只列实际使用的来源 |
| MINIMUM_SHORTFALL | suggested=0；max_safe 保留已算出的安全上限 | 不静默下调最低；列出短缺，候选边界为零贡献基线，用量为空 |
| INSUFFICIENT_EVIDENCE | 两者为 null | 候选边界为 null，用量为空，原因明确缺失的事实 |
| LIQUIDITY_RISK | 两者为 null | 保留基线缺口，不以普通目标贡献绕过恢复动作规则 |
| INACTIVE_POLICY | 两者为 null | 精确版本不匹配、已失效或尚未生效，不能规划新贡献 |

`remaining_min_cents`、`remaining_target_cents`、`remaining_max_cents`、`eligible_new_funds_cents` 和 `minimum_shortfall_cents` 用于解释计算；失败门前无法确定的值保留 null。`max_safe_cents` 是本次 target/来源上界内的最大安全值，不是无来源约束的通用资金能力。

`baseline_boundary.boundary_hash` 绑定完整财务输入。`allocation_hash` 进一步绑定算法版本、目标及精确策略版本、完整原始 lot 字段与证据引用、来源问题，以及已计算的建议额、安全上限、最低短缺、后态边界哈希、逐来源用量。数组排序不会改变结果。来源证据改变可以改变候选摘要；服务的完整审计 `input_digest` 另行保留。

后态边界只是反事实计算，不是新的银行余额或已完成贡献证明。所有原始输入保持不变；同一快照重复预览得到同一候选。多个目标独立预览不能相加视作已预留预算。

## 已验证例子与限制

例 D 使用整数分：现金 1100000，既有归属 200000，其他保护 300000，本月已贡献 100000，范围 [150000,200000,250000]，新工资 500000。建议 100000；假设后现金仍 1100000、目标现金归属 300000，来源剩余 400000，通用余量由 550000 变为 500000。

自身定向测试另覆盖：通用闲钱零但相应最低已保护；未来保护峰值；策略到期后归属保持；最低不足；目标近完成；同账户归属；GOAL 未分配余额保持；FIFO 与排列不变；重复、非现金、越界或未来来源；失效版本；真实消费后的部分 lot；累计 target 达成后重算为零。

红绿证据保存于 `docs/progress/evidence/MVP-203-domain-*.txt`。最终测试与静态检查记录以 `MVP-203-domain-final.txt` 为准。本项不消费来源、不产生 ActionPlan、回执或银行交易，不证明并发执行的 exactly-once；这些由 MVP-301 的原子账本与执行前重验承接。
