# FULL-105：重复未来 Dated 修改预览的显式 history-v2 分支

## 可运行能力与状态

2026-10-06 新增独立域、服务和路由，用完整未来 Dated 原版本链核验解除旧金融预览对 `version_number != 1` 的拒绝。本包 **FUNCTIONAL_MODULE_FINAL / FULL-105 PENDING**；36 项直接/HTTP 夹具检查通过，真实隔离 PG 仅收集、尚未运行。旧 v1 输入、数学函数、事实摘要、确认记录和历史哈希未改；本包不修改 Main、依赖事务选择、OpenAPI、Web 宿主或正式模拟库。

原要求来源为 `docs/spec/requirements-traceability.md:59`（原完整版计划 1192–1194）：修改前预览自主资金、目标、持仓及未来动作影响；无副作用；实际确认后的差分与预览一致；UI/接口测试。该包只补完整已核验的 **再次修改未来 DatedExpense**，不以部分模板关闭原编号。

## 新增源与接口

- `apps/api/app/domain/full_policy_change_history.py`：严格 `FullPolicyHistoryImpactInput` 与 `FullPolicyHistoryFinancialImpact`，新协议 `full-policy-history-impact-input-v2`、`full-policy-financial-impact-history-v2`；所有旧输入作为完整 `original_input` 保留，不重编号当前版本。
- `apps/api/app/services/full_policy_change_history.py`：`preview_full_policy_history_financial_impact(session, user_id, policy_id, body, now)`，同一干净 RR/RO 事务捕获实际当前 FullPolicy、原金融年度结果、完整未来 Dated proof、现金、原归属、持仓、产品及活跃现金占用。原银行/收入/审计金融上下文由年度消费者加载一次；不先调用旧预览再重复捕获。只复用旧 `_goal_facts` 及无状态数学 helper，不缓存权限。
- `apps/api/app/api/v1/full_policy_change_history.py`：独立 `router`，`POST /api/v1/full-policies/{policy_id}/financial-change-preview-history`，operation ID `preview_full_policy_history_financial_change`。输入仍实际 `FullPreviewRequest {expected_version_id, configuration}`；不接受 user、epoch、clock、money、history proof、result、authority。extra query 拒绝。输出为 `FullPolicyHistoryChangeFinancialPreview`，显式 `full-policy-change-history-preview-v2`。
- 三个直接/路由/真实隔离候选测试文件：`test_full_policy_change_history.py`、`test_full_policy_change_history_route.py`、`test_full_policy_change_history_integration.py`。

**Root 注册接缝**：include 新 router，并在 `app/api/dependencies.py` 的 `policy_preview_read` 后缀识别中加入 `/financial-change-preview-history`，令实际 POST 使用 REPEATABLE READ + READ ONLY。域服务 `_read_snapshot` 不允许脏 Session、普通事务或假只读。本包没有放宽这个门，也不直接修改 shared。

## 核验与计算口径

1. 接受当前 Dated 版本数大于1，且完整 `FutureDatedHistoryProof` 与实际 owner、OPEN epoch、当前版本/hash、server as_of、时区精确绑定。复用原 `verify_future_dated_history` 重核全部原版本、命令、Evidence、完整数量、配置/请求/回执哈希和连续链，不能仅相信 VERIFIED 字符串。旧所有 Dated 窗口须严格在未来；当前窗口也须晚于本地今天。
2. 当前唯一原发生项（或确实位于365天之外而无发生项）须逐字段匹配当前原版本的窗口、max、ID、Evidence、日期、类型、来源和状态；内部算术一致的假曲线也不能替代已核验当前配置。
3. 原今天加365个未来日、每天 BEFORE_PAYMENT / AFTER_PAYMENT / AFTER_PRINCIPAL，共1098点必须完整；独立重建原 MVP 曲线加全部原 FULL 承诺和现金占用，要求与原 FULL 曲线逐点一致。现金/Goal/Position/Product 分母唯一且不省略，现金与原 MVP 起点相同。
4. 原每个来源账户检查重算当前现金、Goal归属、该账户占用、周期所需上界、余量及风险；原 minimum margin / safe idle / deficit / 产品全部条件容量重算后精确相同。所有原产品条款和本金返还阶段保持。
5. 只将选中 Dated 的未来承诺替换为未确认候选；其它原 MVP/FULL 保护、占用、本金可用时点、今天/逾期原发生项保持。候选按原 strict Full DSL 校验，未确认和不授权标签保持。返回完整候选曲线和真实有符号差量，负数不截成0；财务容量不是可执行金额。
6. 原 Goal 当前现金/本金和 Position 原记录/当前未结清本金保留，当前只读差量0；未来分配/处置仍 null + UNKNOWN。当前动作 ID 完整保留，预览不重算、失效或执行动作。

`input_hash` 绑定新完整输入封套与全部 proof；候选曲线 hash 使用 `hypothetical-full-curve-history-v2`。`current_fact_digest` 使用 `full-change-preview-history-actual-facts-v2`，绑定 owner/epoch/server as_of、原 FullView、原年度 digest、候选引用、全部 Action/Position/Goal/Product/现金占用。它们不是旧 v1 摘要，也不能用于确认授权或声称跨时点稳定。

## 检查原件

所有夹具明确 synthetic 原件形状/手算，不冒充银行、PG、浏览器或 FULL 验收。没有运行 `make check`、金融执行或正式 reset。

| 实际命令 | 结果与原件 |
|---|---|
| 新域 + HTTP 路由两个模块 pytest | [36 PASS / 9.16s](evidence/W2/repeated-dated-history-final-direct-20261006T024426Z-9e6ebc75/manifest.json)；包装用时另见 manifest；实际 global/scoped stable |
| 新六文件 strict mypy | [PASS](evidence/W2/repeated-dated-history-final-types-20261006T024426Z-2186b92e/manifest.json) |
| 新六文件 Ruff | [PASS](evidence/W2/repeated-dated-history-final-static-20261006T024426Z-c427ca33/manifest.json) |
| 新六文件 format --check | [PASS](evidence/W2/repeated-dated-history-final-format-20261006T024448Z-92f095c6/manifest.json) |
| 新隔离 PG candidate --collect-only | [1 collected / 5.05s，NOT_RUN](evidence/W2/repeated-dated-history-pg-candidate-collection-20261006T024448Z-043742af/manifest.json) |

首轮21纯用例通过原件 `6404a733` 与首轮 mypy 一条测试字典 union 类型错误 `6de2cf58` 均保留；精确源码拷贝存于 `.runtime/FULL-105-history/source-before-narrow-fix-20261006T023926Z/`。后续33直接用例原件 `86c23b19` 也保留；加入当前发生项绑定前的源码在 `source-before-occurrence-binding-20261006T024337Z/`，没有覆盖旧成功或失败。

唯一真实候选入口为：

```powershell
.venv/Scripts/python.exe -m pytest apps/api/app/tests/test_full_policy_change_history_integration.py::test_actual_second_change_preview_rebuilds_originals_and_never_writes_financial_facts -q -p no:cacheprovider
```

由 Root 在唯一金融锁与最终相关源码冻结后运行；候选使用原 temporary_database / migration / seed / Full确认路径，真实 v1→v2 原版本后新预览→明确 v3 原确认，逐1098点核对，Hypothesis身份与正式版本身份仅作显式映射，所有金额/其它发生项保持；真实物理全表快照核零写，原现金篡改后要求 UNKNOWN/null。**收集不代表这些断言已执行。**

## 具体未覆盖与后续依赖

- 目前尚未由 Root 注册新路由/只读依赖并生成 Schema；Web 尚未消费新协议。Root 需保旧v1入口，按显式新协议接消费者，不能让旧 parser 把新 hash 当旧协议。
- 真 PG、真实 HTTP 全链、重复确认后一致性、浏览器/手机和最终全量均 **NOT_RUN**。
- 新分支仅支持完整 proof 的严格未来 Dated。当前今天/逾期、跨读旧时点、缺原件/旧版本/冲突/篡改、Periodic 的跨版本历史及其他10模板仍 UNKNOWN；不能借本分支填金融0。
- 原曲线若含本分支不能完整重建的季节或组合叠加保护，返回 `ORIGINAL_FULL_CURVE_NOT_RECONSTRUCTIBLE_FOR_THIS_HISTORY_BRANCH`，保原 before / after null / delta null，绝不删除 floor 后继续 PROJECTED。
- 原未来收入仍计入0；未实现未来账户逐笔借记、候选 Goal 分配/持仓处置、确认后所有动作完整重算、历史结清桥及真实银行接口。当前原件不写、银行不执行、候选不授 PolicyVersion 或当前资金权限。
