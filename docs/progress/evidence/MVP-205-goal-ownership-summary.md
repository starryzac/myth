# MVP-205 目标本金到期返还独立验收

新增文件：`apps/api/app/tests/test_recovery_goal_ownership.py`。仅测试与本组证据有改动，没有修改服务、领域、数据库结构或种子。

实际隔离 PostgreSQL 场景复用 `mature_recovery_fixture`，再通过真实策略确认和修改命令构造 goal scope 原购买授权及双向引用。购买 Action、DecisionRun、原银行购买流水、原持仓、银行证明和 MATERIALIZED 曝光声明均绑定同一目标及其账户；不是只修改 goal_id。当前资产授权已真实撤销，既有合同到期仍须结算。

独立字面预期：原目标现金 160000 分、已归属本金 250000 分、allocated=410000 分；本月已贡献40000分。返本后目标现金410000分、本金0分、allocated和本月贡献不变。种子一般缺口30000分，加本月剩余最低60000分、11月和12月最低各100000分，完整窗口最低余量仍为 -290000 分。返还本金只增加同目标现金及相同金额的目标保护，不能抵销一般缺口。

还验证了独立银行本金减少/目标现金增加两腿各250000分、成功回执及本地通知、其他账户余额不变、当前归属/贡献/曝光来源完整；相同幂等键重放后整库快照完全不变。

## 实测与状态断言对齐

- `MVP-205-goal-ownership-first.txt`：首轮1 failed，唯一失败为测试预设顶层 `LIQUIDITY_RISK`，实际服务合同为 `PARTIAL_RECOVERY`。此时完整 fixture 已通过源校验并完成到期请求。
- 保留服务合同，测试断言对齐为顶层 `PARTIAL_RECOVERY` 与 `actual_boundary.status=LIQUIDITY_RISK` 两层表达。这是验收断言对齐，**不是资金算法修复**，没有改服务。
- `MVP-205-goal-ownership-second.txt`：1 passed，5.28s，全部金额、归属、回执和重放断言通过。
- `MVP-205-goal-ownership-ruff.txt`、`-format.txt`、`-mypy.txt`：Ruff、格式及严格 mypy 单文件均通过。格式化后的逻辑不变。

命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --frozen pytest apps/api/app/tests/test_recovery_goal_ownership.py -q -p no:cacheprovider --tb=short
uv run --frozen ruff check apps/api/app/tests/test_recovery_goal_ownership.py
uv run --frozen ruff format --check apps/api/app/tests/test_recovery_goal_ownership.py
uv run --frozen mypy apps/api/app/tests/test_recovery_goal_ownership.py
```

本验收证明目标既有本金自然到期的应用与独立模拟银行闭环，不代表真实银行接入，不替代主代理的全量验收。
