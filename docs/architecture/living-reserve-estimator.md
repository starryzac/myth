# MVP-201 可解释生活准备金估算

`app/domain/living_reserve.py` 提供纯函数 `estimate_living_reserve(configuration, inputs) -> LivingReserveEstimate`。算法版本为 `living-reserve-nearest-rank-v1`，不读取当前时间、数据库或网络，不修改输入，不保存策略，也不产生资金授权。HTTP/数据库适配层负责验证用户、来源证据及覆盖声明；域层对明确提供的事实做确定性计算。

## 时间与覆盖

配置复用 `living_reserve` DSL，默认配置由调用方给出：最近 56 个完整本地日、14 日窗口、0.8 分位。域层不暗中覆盖配置。

`reference_date` 是调用方根据用户 IANA 时区确定的当前本地日。历史区间为 `[reference_date - lookback_days, reference_date - 1]`，两端包含；当天和未来流水不入样。`occurred_on` 已由适配层从可信时间戳换算为本地日，域层不重新解释时区。

覆盖必须逐账户、逐日明确提供。对账户范围内每个账户，历史区间的每一天都必须在其 `HistoryCoverage.covered_dates` 中。不能用第一笔和最后一笔交易推断中间没有缺失，也不能把无交易当作已完整查询。

- 至少一个账户且所有历史日完整覆盖，无符合条件的消费：基础估计可以为 0。
- 覆盖缺失、缺一天、范围内缺账户或账户范围为空：状态为 `INSUFFICIENT_HISTORY`，基础与建议金额均为 `null`。
- 不足时返回逐账户缺失日期。已完整覆盖的日仍可展示日额，未覆盖的日为 `null`；不输出看似完整的窗口或分位排名。
- 历史范围内某笔流水的经济角色为 `UNKNOWN`，也返回 `INSUFFICIENT_HISTORY`。不能将它当作已明确排除的非消费后给出精确建议。这种情况下该日的 `covered` 仍表示原始流水覆盖是否完整，`amount_cents=null` 表示消费语义无法完整确定。

## DTO 与信任边界

输入是禁止额外字段、严格类型的 Pydantic 模型：

```python
ReserveEstimateInput(
    reference_date: date,
    timezone: str,
    account_ids: list[UUID],
    transactions: list[ReserveTransaction],
    coverage: list[HistoryCoverage],
)
HistoryCoverage(account_id: UUID, covered_dates: list[date])
ReserveTransaction(
    transaction_id: UUID,
    account_id: UUID,
    occurred_on: date,
    direction: Literal["CREDIT", "DEBIT"],
    amount_cents: int,
    category: str,
    category_confirmed: bool,
    is_one_off: bool,
    economic_role: Literal["CONSUMPTION", "NON_CONSUMPTION", "UNKNOWN"],
)
```

金额必须是大于零且不超过 `9223372036854775807` 的整数分，拒绝负数、零、布尔值和浮点数。日期、UUID 和布尔字段不做宽松字符串转换。交易 ID、账户 ID、每个覆盖对象的账户以及同账户覆盖日期都不得重复。公开函数重新验证输入快照，避免冻结模型中的可变列表在构造后绕过校验。

资源边界：`lookback_days <= 366`，账户与覆盖对象各最多 100 个，交易最多 100000 笔，每个账户最多 366 个覆盖日期。DSL 本身要求 `1 <= horizon_days <= lookback_days` 和有限 `0 < quantile <= 1`。不能构造完整历史区间的极早参考日期、计算结果超出整数分范围，都以 `ValueError`（含 Pydantic `ValidationError`）报告，不返回截断或溢出金额。容量超限是输入错误，不包装为历史不足。

适配层根据可信来源把原始经济角色转换为三类：`CONSUMPTION` 保留；`INCOME/OPENING/INTERNAL_TRANSFER/ASSET_PURCHASE/CREDIT_CARD_PAYMENT` 为 `NON_CONSUMPTION`；没有可信角色为 `UNKNOWN`。原始细分角色与证据保留在适配层输入摘要/源证据中。

## 过滤与计算

一笔流水只有同时满足账户在范围内、日期在历史区间内、方向为 `DEBIT`、经济角色为 `CONSUMPTION`、类别已由用户确认且属于配置所选类别，才参与消费统计。`exclude_one_off=true` 时进一步排除明确 `is_one_off=true` 的流水；配置为 `false` 时保留它。类别没有写死为默认三类，用户明确确认并选择的 `rent` 等类别可以参与；账户转账、申购和还款等非消费不会因为误用生活类别名称而参与。

每笔被排除的流水输出 ID、账户、日期、金额与全部适用理由：

| 理由 | 解释 |
| --- | --- |
| `ACCOUNT_OUT_OF_SCOPE` | 不属于当前账户范围 |
| `OUTSIDE_HISTORY` | 早于历史区间、当天或未来 |
| `NOT_DEBIT` | 不是支出方向 |
| `NON_CONSUMPTION` | 明确为非消费资金流 |
| `UNKNOWN_ECONOMIC_ROLE` | 经济角色不能确定，范围内会阻止精确估算 |
| `CATEGORY_UNCONFIRMED` | 类别没有用户确认 |
| `CATEGORY_NOT_SELECTED` | 不在当前所选基本生活类别中 |
| `ONE_OFF` | 按配置排除已明确标注的一次性消费 |

通过筛选的整数分按日相加；仅覆盖明确的空日补 0。枚举全部连续、重叠的 `horizon_days` 窗口，数量为 `lookback_days - horizon_days + 1`。对排序后的窗口金额使用 nearest-rank：

```text
q = Fraction(str(configuration.method.quantile))
n = 窗口数
rank = ceil(n * q) = (n * q.numerator + q.denominator - 1) // q.denominator
base_reserve_cents = 排序后的窗口金额[rank - 1]
recommended_reserve_cents = base_reserve_cents + extra_buffer_cents
```

不使用浮点乘法求排名，不对金额插值，不四舍五入。例如 `n=100, q=0.07` 精确得到第 7 项；不能因二进制浮点误差误取第 8 项。56/14 的默认配置产生 43 个窗口，0.8 分位取第 35 项。

## 可复算结果

`LivingReserveEstimate` 包含状态、算法版本、参考日期/时区、实际历史起止、配置摘要、账户/类别范围、逐日金额、按时间排列的窗口及金额、精确分数字符串、窗口数、1 起始排名、基础/缓冲/建议金额、参与交易 ID、逐项排除和覆盖缺口。列表按日期或 ID 稳定排序；交换交易、账户或覆盖输入顺序不会改变输出 JSON。

`READY` 只表示本次估算输入充分。推荐金额仍是审阅候选，不能自动成为硬约束；策略确认与生命周期由既有服务处理。该模块没有实现 MVP-202 的自主资金边界或未来预算续期。

## 测试与证据

测试仅调用公开 DTO/估算函数，使用手算字面真值，无数据库或业务结果 mock。覆盖以下行为：

- 三日金额 100/200/300、两日窗口 300/500、q=0.5 得到基础 300，缓冲 50 后为 350。
- 默认完整 56 日零消费的 43 窗与第 35 项；55 日、缺账户、空范围、仅首尾交易均不伪造精确值。
- 已选择 `food` 且类别已确认的 450000 分一次性消费被剔除；关闭排除后实际提高基础值；切换所选类别会改变结果。
- 非消费、未确认分类、当天/未来、区间外、未知角色、输入排列、n=100/q=0.07、非法类型/重复 ID/容量/溢出边界。

红绿证据在 `docs/progress/evidence/MVP-201-domain-*`：初始模块缺失、覆盖缺失、消费过滤、不可信输入、公开边界各保留真实失败与通过输出。最终定向测试、Ruff 和 mypy 记录在 `MVP-201-domain-final.txt`。合成种子 77900 分基础＋50000 分缓冲的实际 PostgreSQL 验证由适配服务测试另行记录；该数值不是金融效果实验。
