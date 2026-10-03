# MVP-202 独立性质测试账本

依据 [ADR 0005](../adr/0005-mvp-cash-boundary.md)。本文件记录 `test_boundary_properties.py` 的测试来源、覆盖范围及真实运行证据；未通过的项目不算已验证。

## 独立性与接口

测试只通过公开 `compute_boundary` 接口调用被测引擎，不导入其私有函数，也不读取被测 trace 来构造预期。测试账本由明确的整数现金、已保护金额和逐事件借贷分录组成；它不解析策略、不展开月份、不判断证据授权，不是第二套产品财务引擎。

候选购买先扣现金并立即检查。每日事件依次是保护生效、付款、本金到账，每次变动后都检查余额。没有事件的日期余额不变，因此事件前后检查覆盖这些日期。小额生成用例逐分穷举合法购买额，而不是复制引擎的限额公式。候选确定本金返还仅记入明确的返还日；T0/T1 标签不生成返还事件。

## 固定账本真值

1. 现金 1,000,000，保护 750,000，余量 250,000；付房租 200,000 时现金、未付保护同减，余量不变；目标本金 80,000 到账时现金、目标现金保护同增，余量不变；随后实际生活消费 70,000 而滚动底线不变，余量降为 180,000。
2. 现金 200,000，保护 170,000，购买 40,000 后立即缺口 10,000；随后付已保护账单 150,000，缺口不变；当天更晚本金 100,000 到账才使余量为 90,000，不能消除之前失败的检查点。
3. 使用小整数分穷举：现金 20，当前保护 5，第 20 日保护增加 10；持续占用上限 5，第 7 日确定返还产品上限 15，第 20 日或第 30 日返还上限均为 5。第 20 日先检查新增保护，再计当日本金。

## 公共性质及实际计数

2026-10-04 合并运行实际通过 **11 个测试函数**：4 个固定测试和7组生成测试。每组使用 `settings(max_examples=200, derandomize=True, database=None, deadline=None)`。没有 `assume`、过滤条件或依赖已有样例数据库；统计日志确认每组都是 200 passing、0 failing、0 invalid。因此是 **1,200 个财务生成样例 + 200 个 DTO 隔离生成样例**，总计 1,400 次生成测试用例执行，不把固定测试、函数内多次调用或重复复跑累加成额外样例。

| 生成测试 | 独立预期或变换 | 实测有效样例 |
|---|---|---:|
| `test_positive_financial_caps_and_one_cent_excess_against_exhaustive_ledger` | 小整数债务、底线、未来保护增量、已有本金及其到账日；逐分穷举持续占用及确定返还产品的上限。每例必须有正额度，额度内每个分录检查点均非负，加1分至少一个点为负 | 200 |
| `test_increasing_protection_never_increases_financial_caps` | 增加立即或未来生效的保护，持续占用和所有产品上限均不增加；负余量保留为缺口和 LIQUIDITY_RISK | 200 |
| `test_paying_a_protected_bill_reduces_cash_and_debt_together` | 已付金额同时从现金和未付债务扣除，独立自由金额保持不变；包含部分及全部付清 | 200 |
| `test_owned_goal_principal_becoming_cash_never_enlarges_general_idle` | 目标本金变为已归属现金，目标总归属不变，一般闲钱不增加；到账日覆盖 day 0–90 | 200 |
| `test_real_goal_contribution_moves_ownership_without_double_protection` | 真实当前月贡献将一般现金转为目标现金，并减少同月最低剩额；合并现金不变、一般自由金额不变 | 200 |
| `test_equivalent_input_permutations_preserve_complete_financial_result_and_hash` | 重排现金账户、账单、目标、贡献事实、政策、产品、持仓及证据引用，完整结果含轨迹和 boundary_hash 相等；每例要求正额度 | 200 |
| `test_financial_dto_rejects_future_source_income_and_uncredited_yield` | Snapshot 拒绝未来收入/退款/利息/展示收益字段；Position 拒绝未入账收益字段；Product 拒绝年收益展示字段 | 200 |

正常正额度生成测试的金额是小整数分，现金不超过120分，逐分穷举代价可控；产品返还日覆盖0–95日，已有本金到账日、付款日覆盖0–90日，未来保护生效覆盖1–90日。固定测试另用真实量级的整数分验证，不把小额覆盖声称为所有金额及数据质量的穷举证明。

月度贡献性质固定为2026年10月至12月三个有效自然月，目标1,000分高于生成的累计保护，因此该组只验证贡献守恒；不同截止日、总额封顶、来源缺失、授权和日历边界由域/服务专项测试负责。

## 来源隔离与权限边界

财务 DTO 以禁止额外字段的方式拒绝未来收入、收益。此处没有编写一个假的来源投影器再用它自证哈希不变。真实数据库来源证据在正确重算内容哈希后，仅改变预测收入、未入账收益及产品展示利率仍不影响财务结果的验证，属于服务集成测试的责任；本文件的200个 DTO 样例不能替代该证据。

公开配置规范化和内容哈希函数仅用于构造合法策略输入，不生成任何预期金额。Oracle不从引擎返回轨迹获取事件，不调用被测引擎寻找预期上限。配置展开、证据真实性、资产权限、执行幂等、恢复到账及四级自主判定不由这个简化账本实现。本次通过只证明 MVP-202 在所列样例和性质上的财务条件，不代表 MVP-301/302 的 AUTO_EXECUTE 已实现。

## 命令与证据

```powershell
.venv\Scripts\python.exe -m pytest apps/api/app/tests/test_boundary_properties.py -q -p no:cacheprovider --hypothesis-show-statistics
.venv\Scripts\python.exe -m ruff check apps/api/app/tests/test_boundary_properties.py
.venv\Scripts\python.exe -m mypy apps/api/app/tests/test_boundary_properties.py
```

- [初始公共接口红灯](../progress/evidence/MVP-202-properties-public-first.txt)：骨架返回 safe_idle=200000，而独立例应为50000。此时域实现明确尚在开发；该记录是接口红灯，不是已完成实现的缺陷结论。
- [初次转绿](../progress/evidence/MVP-202-properties-initial-green.txt)：固定真值、正额度200例、DTO隔离200例通过。
- [最终合并运行](../progress/evidence/MVP-202-properties-final-green.txt)：11 passed in 22.62s；7组各200个有效样例，全部无失败、无丢弃。
- [Ruff](../progress/evidence/MVP-202-properties-ruff.txt) 与 [mypy](../progress/evidence/MVP-202-properties-mypy.txt)：均通过。

最早的3个oracle自校验日志报告过 pytest 缓存目录写入权限警告，后续定向运行使用 `-p no:cacheprovider`，没有修改共享缓存权限；警告记录保留在原日志中。没有重置真实演示数据库，也未修改财务引擎、DTO、API或其他测试文件。
