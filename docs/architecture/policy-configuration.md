# MVP 策略配置与确认内容

日期：2026-10-04（Asia/Shanghai）。适用任务：MVP-103；供 MVP-104 / MVP-105 复用。

## 公共接口与边界

`app.domain.policy_configuration.validate_configuration(configuration)` 校验并返回新的 JSON 兼容字典，日期序列化为 `YYYY-MM-DD`、UUID 为标准小写连字符字符串，所有默认值明确写入结果。输入字典不被修改。缺字段、额外字段、错误类型或不满足约束会抛出 Pydantic `ValidationError`（属于 `ValueError`）或 `ValueError`。

`configuration_hash(configuration)` 仅对**传入文档**计算 SHA-256：UTF-8、`ensure_ascii=False`、对象键排序、分隔符 `(',', ':')`、`allow_nan=False`。它不补默认值、不验证业务模板；不完整候选也可以计算摘要。根必须为对象，各层只接受 JSON 类型，拒绝非字符串对象键、tuple、任意 Python 对象、NaN 与无穷值。列表顺序保留。确认链路必须先验证并向用户展示标准化结果，再对此结果计算摘要；原始候选摘要不代表已获授权。

本模块不读取时间、数据库、银行事实或模型输出，不确认策略、查询对象归属、授予金额权限或执行资金动作。`bill_balance` 仅定义引用形状，真实账单余额与账户归属需由服务验证。所有 UUID 引用的存在、对象类型和用户归属同样属于服务层。

## 所有模板共有的字段

| 字段 | 约束与默认值 |
| --- | --- |
| `type` | 必须为下列五种类型之一，返回时保留。 |
| `name` | 可选；默认 `null`，给出时去首尾空白后长度 1–120。 |
| `valid_from` / `valid_until` | 可选日历日期，默认均为 `null`；结束日不得早于开始日。同一天有效区间合法。 |

JSON 日期必须采用完整 `YYYY-MM-DD`，拒绝时间戳、日期时间和不存在的日期。Python 直接调用也允许 `date`，不接受 `datetime`。没有显式起点时，本模块保持 `null`；可信的确认时间与用户时区由生命周期服务解释，不把墙钟时间写入纯配置标准化结果。截止日的含当天语义、对应 UTC 边界及日历调度由服务解释；本模块不会猜测每月 31 日如何调度。

每层 Pydantic 对象均设置 `extra='forbid'` 和严格类型。所有金额是非负整数分，拒绝 bool、float 和数字字符串，最大为 PostgreSQL 有符号 BIGINT 上限 `9_223_372_036_854_775_807`。权限开关只接受 JSON 布尔值，不强转 `1` 或 `'true'`。

通用 `priority` 对象字段为 `importance`（整数 0–100，默认 50）、`minimum_cents`（默认 0）、`reducible`（默认 false）、`deferrable`（默认 false）。省略整个对象时明确补入这四项。

## 五种 MVP 模板

### recurring_obligation

- 必需：`payee_id`（非空、最长 160）、`due_day`（严格整数 1–31）、`amount_rule`。
- 默认：`prepare_days_before=0`（非负整数）、`auto_execute=false`、上述默认 `priority`。
- `amount_rule` 是以 `kind` 区分的严格联合：`exact` 必须有 `amount_cents`；`range` 必须有 `min_cents` 和 `max_cents` 且 min ≤ max；`bill_balance` 必须有 `account_id` UUID。
- 银行账单模板不含猜测金额。跨用户、非信用卡账户或不确定账单是否可确认由后续服务处理；配置验证通过不能代替银行事实。

### living_reserve

- 必需：`horizon_days` 为正整数；`method` 必须含 `name='rolling_window_quantile'`、正整数 `lookback_days`、有限数值 `quantile`（0 < q ≤ 1）、非空 `essential_categories` 数组（类别去首尾空白后长度 1–48）。
- 必须满足 `horizon_days ≤ lookback_days`；不接受布尔分位数或字符串分位数。
- 默认：`method.exclude_one_off=true`、`extra_buffer_cents=0`、`reconfirm_on_boundary_crossing=true`。
- 类别是否经用户确认、历史是否足够以及实际分位数估算属于 MVP-201，未在这里伪造结果。

### goal_saving

- 必需：正数 `target_cents`、日历日期 `deadline`、`monthly_contribution={min_cents,target_cents,max_cents}`。
- 月度贡献满足 0 ≤ min ≤ target ≤ max；月度目标可以为 0，与已有数据库模型一致，长期目标总额必须大于 0。
- 若给出 `valid_from`，`deadline` 不得早于该日。策略可在目标截止日前到期，因此不强制 deadline ≤ valid_until。
- 默认：上述 `priority`、`cross_goal_reallocation_allowed=false`、`asset_policy_id=null`。
- `asset_policy_id` 给出时必须是 UUID。计划示例中的 `goal_asset_policy_car` 是说明用符号，接入前须由调用方解析到真实策略 UUID；这里拒绝把符号当可执行引用。

### asset_authorization

- 必需：`scope`、非空 `allowed_asset_classes`、`max_auto_managed_cents`、`single_action_cap_cents`、非负整数 `max_redemption_delay_days` 与 `max_lock_days`。
- `scope` 只接受 `general_idle_funds` 或 `goal`。`goal` 必须提供 `goal_id` UUID；`general_idle_funds` 不得携带非空 `goal_id`；默认 `goal_id=null`。
- 类别仅限实际 MVP 的 `CASH`、`CASH_MGMT_T0`、`CASH_MGMT_T1`、`FIXED_DEPOSIT`。不提前加入完整版期限类别。
- 单次上限不得超过总管理上限。风险等级是严格整数 0–5，默认 `max_principal_risk_level=0`。
- 默认 `allow_auto_recovery_without_penalty=false`、`allow_early_withdrawal_with_penalty=false`。模板中的授权上限并不代表当前安全金额；实际产品风险、流动性、硬约束与有损动作确认仍由后续确定性服务判断。

### emergency_buffer

```json
{
  "type": "emergency_buffer",
  "name": "应急缓冲",
  "valid_from": null,
  "valid_until": null,
  "amount_cents": 500000
}
```

这是计划已有应急缓冲要求的最小配置形状：一个非负金额及公共元数据。它不自动授权消费或投资，也不是新增完整版策略模板。

## 验证记录

先逐一新增未通过的公共接口测试，再实现对应模板：缺少模块导致首次收集失败；固定金额/账单引用、生活准备金、目标、资产授权、应急缓冲分别在未支持时失败后转绿。哈希的非 JSON 根、非字符串键和 tuple 测试初次出现 4 个失败，增加严格 JSON 检查后通过。未为了制造失败改坏已有实现。

已覆盖计划示例与默认值、严格金额和布尔、全部嵌套额外字段、日期窗、月度范围、类别/风险/锁期/赎回上限、UUID、作用范围、分位数、非 JSON/NaN/Infinity、确定性、幂等标准化以及输入不变性。

2026-10-04 最终定向检查结果：

```text
.venv/Scripts/python.exe -m pytest apps/api/app/tests/test_policy_configuration.py -q -p no:cacheprovider
153 passed in 0.64s

.venv/Scripts/python.exe -m ruff check apps/api/app/domain/policy_configuration.py apps/api/app/tests/test_policy_configuration.py
All checks passed!

.venv/Scripts/python.exe -m ruff format --check apps/api/app/domain/policy_configuration.py apps/api/app/tests/test_policy_configuration.py
2 files already formatted

.venv/Scripts/python.exe -m mypy apps/api/app/domain/policy_configuration.py apps/api/app/tests/test_policy_configuration.py
Success: no issues found in 2 source files
```

纯领域测试禁用 pytest 缓存，以避免当前沙盒对已有缓存目录的写入权限干扰；未跳过任何测试。本任务不代替仓库整体 `make check`。
