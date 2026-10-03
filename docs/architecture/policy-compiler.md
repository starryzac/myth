# MVP-105 离线自然语言编译器

`apps/api/app/domain/policy_compiler.py` 实现有限、可复现的中文语法，版本为 `offline-policy-rules-v1`。输入相同文本、参考日期和时区时，输出相同 JSON。编译器不读取当前时间、数据库或网络，不创建或确认策略，也不执行资金动作。

## 纯函数接口

```python
compile_policy(text: str, context: CompileContext) -> CompilationResult

CompileContext(reference_date: date, timezone: str)
CompilationIssue(code: str, field: str, message: str, source_fragment: str)
CompilationResult(
    compiler_version: str,
    reference_date: date,
    timezone: str,
    draft: dict[str, Any],
    configuration: dict[str, Any] | None,
    issues: list[CompilationIssue],
    assumptions: list[str],
)
```

这些类型是禁止额外字段的 Pydantic 模型；`model_dump(mode="json")` 可直接持久化，日期为 ISO 字符串。参考日期由调用方提供，时区必须是有效 IANA 名称。文本长度为 1–2000 个字符；空白文本或超限文本返回问题，非字符串参数抛出 `ValueError`。

`draft` 只保留文本中明确识别的字段。只有全部子句均完整理解、无问题且通过严格 DSL 校验时，`configuration` 才包含标准化配置与显式默认值；否则它为 `null`。问题中的 `source_fragment` 是真实原文片段，缺失字段或跨字段错误没有单一片段时为空。

## 当前语法与例句

句子按中英文逗号、句号、分号、顿号或换行分割，每个子句必须完整匹配。支持 NFKC 规范化和普通排版空白；数字之间的空白产生问题，不能把 `1 2万元` 合并为 `12万元`。同一字段的不同值、多个不同目标都需要修订。

| 意图 | 可解析例句 | 结果 |
| --- | --- | --- |
| 立即应急缓冲 | `保留3000元应急金`、`始终留出一万元应急金` | `emergency_buffer`，金额为当前需要保留的缓冲 |
| 买车目标 | `买车目标三万元，截止2027年10月1日，每月至少一千八百、建议两千、最多两千五百元` | `goal_saving`，三项月额分别保留 |
| 旅行目标 | `旅行目标1.2万元，截止2027-06-30，每月固定储备1000元` | `goal_saving`，固定月额展开为相同的 min/target/max |
| 未来应急储蓄 | `到2027年12月31日攒够两万元应急金，每月至少1000元，建议1500元，最多2000元` | `goal_saving`，不会误作立即需要保留两万元 |
| 有效期 | `保留3000元应急金，有效期从2026年11月1日至2027年12月31日` | 保留两个日历日期 |
| 单独有效期边界 | `保留3000元应急金，从2026-11-01起，有效至2027-12-31` | 与完整有效期窗口相同 |
| 月额范围未定建议值 | `旅行目标1万元，截止2027-06-30，每月存1000元到2000元` | 草稿包含 min/max，要求补充 target，不取中点 |

月额限定词中，`至少/最低/最少` 对应 `min_cents`，`尽量/建议/目标` 对应 `target_cents`，`最多/最高` 对应 `max_cents`。首次明确 `每月/每个月` 后，后续独立限定词子句沿用月度语境；每个值依然必须明确出现。仅 `每月存2000元` 不推断为固定金额。

编译器只生成买车、旅行（包含“旅游”别名）、未来应急储蓄目标，以及立即应急缓冲。其他既有 DSL 模板仍通过结构化路径配置，当前模块没有实现 FULL 版模板或任意口语理解。

## 金额、日期与默认值

所有金额使用十进制精确计算并转换为整数分，范围为 `0..9223372036854775807`，目标总金额必须大于零。支持阿拉伯数字、完整中文数词、`万/千/百` 和精确小数，例如 `1234.56元`、`三万零五百元`、`1.2万元`、`一点五万元`。不使用浮点舍入，不能精确到分的金额会产生问题。

中文数词限于万位组，省略或歧义写法如 `三万五`、`一百二`、`三五万` 不猜测。`1到2万元` 的范围两端单位不明确，要求分别写单位；`1000元到2000元` 可解析。省略“元/块”的金额槽位按人民币元解释，并在 `assumptions` 明示。外币、负数、指数格式、模糊数量、溢出金额均不生成可确认配置。

绝对日期接受 `YYYY-MM-DD` 或阿拉伯年份配中文月日，例如 `2027年十月一日`；相对年份接受“今年/明年”，必须使用传入的 `reference_date`，并将锚点、时区和解释年份写入 `assumptions`。非法日期、过去的目标截止日、已过期的有效期以及逆序窗口都会产生问题。未写具体日期的月份要求补充；只有明确“前/之前”才可把月份开始视为排他边界。

严格“前/之前”仅在编译时减去一天，得到包含式截止日。参考日期 `2026-10-04` 下，“明年十月前”解释为 `2027-09-30`；这与初版计划示例 JSON 中的 `2027-10-01` 不同，原因是这里显式采用排他词“前”的语义。下游应直接使用规范化截止日，不得再次减一天。闰年测试覆盖 `2028-03` 前为 `2028-02-29`。

未指定的 `valid_from/valid_until` 由 DSL 标准化为 `null`；纯函数不会用当前时间补值。生命周期服务负责首次确认时的生效规则。

目标配置沿用 DSL 的 `priority` 默认值：`importance=50`、`minimum_cents=0`、`reducible=false`、`deferrable=false`，并在 `assumptions` 完整展示供用户复核。这些默认值不代表理解了用户未说出的优先级或延期意图。明确的月度最低值仍保留在 `monthly_contribution.min_cents`；“尽量”只形成建议值，不能变成硬最低值。

`cross_goal_reallocation_allowed=false`、`asset_policy_id=null` 同样明示。编译器不生成资产授权或跨目标调拨许可。

## 黄金句与安全边界

参考日期 `2026-10-04`，时区 `Asia/Shanghai`：

> 明年十月前想攒三万买车，每个月尽量存两千，资金别锁太久。

对应草稿：

```json
{
  "type": "goal_saving",
  "name": "买车",
  "target_cents": 3000000,
  "deadline": "2027-09-30",
  "monthly_contribution": {"target_cents": 200000}
}
```

`configuration=null`，问题字段为 `monthly_contribution.min_cents`、`monthly_contribution.max_cents`、`max_lock_days`。未量化的“别锁太久”不能转换成任意锁期或资产授权；这里保留问题，等待用户明确修订。

未知、否定、条件和越界子句不能被静默丢弃。即使前半句已成功解析，下列后续文本也阻止整个结果变为可确认配置：`除非没有工资`、`否则取消`、`直接执行`、`无需确认`、`剩下全部买股票`、`允许跨目标挪用`、`最多锁定30天`、`允许有损提前支取`。当前语法不能表示这些声明，必须由用户改写或通过适用的结构化授权路径处理。文本里的 JSON 或“忽略之前所有规则”同样只是未理解子句。

编译成功仅表示有可审阅的候选结构。HTTP/服务层负责保存完整原文、编译结果与日期锚点的证据，验证修订与用户归属，形成 PROPOSED 候选并经既有 hash 确认流程生效。可选 LLM 适配器在服务边界实现；它不改变此纯规则编译器，也不允许跳过 DSL 校验和人工确认。

## 验证证据

测试直接调用公开纯函数，断言标准化金额、日期、草稿、问题、默认权限及确定性，不 mock 业务结果。已保存各阶段真实 red/green 输出：

- `docs/progress/evidence/MVP-105-compiler-red.txt` 与 `green-initial.txt`：最小应急缓冲调用链。
- `MVP-105-compiler-money-red/green.txt`：中文、阿拉伯及小数金额。
- `MVP-105-compiler-goal-red/green.txt`：目标、截止日、固定月额。
- `MVP-105-compiler-golden-red/green.txt`：黄金句草稿和未来应急储蓄。
- `MVP-105-compiler-window-red/green.txt`：有效期与保留范围缺项。
- `MVP-105-compiler-safety-red/green.txt`：过期日期及数字空白回归。

最终定向命令与结果记录在 `MVP-105-compiler-final.txt`；范围是本模块的纯函数测试、Ruff 与严格 mypy。API、数据库、人工修订和确认链路由集成任务分别验收。
