# FULL-805 当前 GENERAL 机制选择合同

2026-10-06：PARTIAL_IMPLEMENTED，原编号 PENDING；银行消费者 NOT_CONNECTED，真实臂执行和指标 NOT_RUN/null。旧 MVP、原 P 规划、prepare/recheck/historical、旧失败及历史哈希均未修改。

## 实际可调用入口

```python
from app.services.full_experiment_asset_selection import read_current_general_purchase_selection

selection = read_current_general_purchase_selection(
    session, user_id, request, rule_original, now
)
```

`request` 是原严格 `FullAssetPrepareRequest`，仅策略/版本/epoch/原 key/模式等身份字段。`rule_original` 是 `RegisteredFullMechanismRule(original_path, sha256)`，只供可信服务器实验入口消费，不能加入公共金融请求。`now` 来自服务器。Session 必须 clean、REPEATABLE READ、READ ONLY；实际 PostgreSQL 只能是 `127.0.0.1:54329/bf_test_<32hex>`，真实模拟 User 与唯一 OPEN epoch 必须匹配。默认不存在任何授权缓存。

规则原件只允许仓库 `.runtime` 或 `docs/experiments` 下的实际文件，最多 1 MiB，校验完整原字节 SHA、闭合严格 JSON、唯一键、整数、owner 与完整 request/key/版本。协议 `full-general-purchase-rule-original-v1`，purpose 仅 DEVELOPMENT。必须原登记 `opportunity_id` 与 `Rule`（B0/B1/B2/B3/B4/B5/P；消融闭合枚举）；comparison_days 为 1..365。可选 `funds_use_date` 的类型是 aware **instant**，不是客户端财务时钟。B0 仅原作者手动产品 UUID/整数金额；B1 阈值、B2 固定预算/产品及静态生活储备同样来自冻结前作者原件。不能加入银行事实、模型输出、角色、成功标签。

例如服务器先登记以下原件，UUID 只能来自本次原 lookup，不可填假 UUID 后称实际：

```text
protocol=full-general-purchase-rule-original-v1
purpose=DEVELOPMENT
user_id=<actual simulated User>
original_request=<complete original FullAssetPrepareRequest>
opportunity_id=<registered original opportunity>
rule={arm_id:B1, ablation:NONE, threshold_cents:<authored fixed integer>}
comparison_days=90
```

## 真来源与机制差量

每次调用读取实际已确认当前 Full GENERAL AssetAuthorizationPolicy、当前已授权 MVP 版本、完整审计、独立银行投影、真实收入账本/占用、完整 immutable catalogue/current source 和 365×3 全保护曲线。缺失/漂移/未知不补金额。同用户原 SUBMITTED/UNKNOWN action 或 ACCEPTED/UNKNOWN bank operation 保留原件后 UNKNOWN，原身份需先恢复；未决捕获有 1001 行界限，命中时仍 UNKNOWN，不能叫完整实测。

`source_originals` 保留原规则文本、Full/MVP 版本、原金融 digest、严格 planning input、**未改的 original_p_response**、全 Full protection、完整 catalogue、曝光/审计/使用的证据及收入账本。完整 catalogue 中无效/旧版本保留；当前有效候选池中的排除产品也保留逐项原因。`source_counts` 显式记录相应分母。`current_source_hash` 绑定 owner/epoch/真实 as_of/全部原件/计数；as_of 或真实来源不同不能宽泛删时点后要求哈希相等。

本能力是有限、保守、单产品当前候选：按 365 天各保护分项的实际最大值求预算，真实 CASH 余额只扣一次全局已有 cash claims；不加入未来本金/工资。现金源逐账户扣已归属 Goal 与 claims，不卖已有持仓。起投额不向上偷偷补齐。产品真实版本/terms_digest/锁定/延迟/风险/已确认 scope 校验；原计划赎回不越使用时点或 Full 确认窗口，定存仍保真实到期，可供 B5 作不采用流动性过滤的提案。

| 当前机制 | 实际实现与界限 |
| --- | --- |
| B0 | 原手动产品与金额；不是已核真人操作/授权。 |
| B1 / B2 | 原现金阈值 / 固定预算与产品；可提出被共同银行守卫拒绝的金额，不能将提案当执行成功。 |
| B3 / P | 单一确定世界上的有限候选选择；保留真实原 P 组合结果，自己的结果不冒充原 P OPTIMAL；未覆盖不确定世界问询。 |
| B4 | 实际模型未调用、无 confidence 原件，MISSING。 |
| B5 | 同原候选、原到期金额/时间，移除有限候选流动性过滤；不改共同生产安全门。 |
| 四有限消融 | DYNAMIC_LIVING_RESERVE、MULTI_GOAL_CONSTRAINTS、LIQUIDITY_FILTER、AUDIT_CHAIN 改变实验提案/记录；不移除共同财务、权限或实际审计门。 |
| 其余四消融 | EVIDENCE_LEVEL、POLICY_VERSION、MINIMUM_QUESTION、SAFE_RECOVERY 在此当前确定性购买域没有实际对照，MISSING。 |

## Root 必须接的执行消费者

1. 以新显式实验协议和原 rule path/SHA/opportunity/arm 注册此 producer 的**当前源码**；不静默扩旧 MVP selector。公共 HTTP 不接受金额/模型结果/原件路径。
2. 消费者先按原 key 查历史；有原 Action/未知银行操作只能恢复原 identity，不能重选产品或换 key。新 prepare 在原 User 锁下 fresh 重新读取 source、重跑 producer，验证完整 request/rule/版本/epoch/实际产品/cash uses，保守保留原 Full+MVP 权限、全保护、完整收入来源/claims、terms/expiry、bank guard 与 ASK 明确确认。跨请求不复用授权状态。
3. 新 `full-mechanism-selected-portfolio-v1` 是独立提案：`batch`、产品 immutable binding、rule_raw_SHA、current_source_hash、mechanism_input_hash 及完整 portfolio_hash。不能塞进旧要求 P OPTIMAL 的 `build_frozen_portfolio` 或篡改原 P status。原 `FullAssetPrepareRequest` 不接受实验产品/金额，旧消费者未接本结果。
4. Root 需另设严格 private 选择接缝、原件 capture、全阶段 fresh/historical 核验与原执行 marker，才能将 selected product/batch 送入真实 prepare。本文没有声称接缝、银行交易或实际 Full 七臂已完成。

输出任何状态均 `bank_authority=false`、`funds_reserved=false`（portfolio）、`execution_status=NOT_RUN`、`runtime_consumer_status=NOT_CONNECTED`、`actual_metrics=null`、分母保留。GOAL、ladder、支付/恢复、完整组合、不确定候选世界、正式冻结规则/7臂/8消融/350样本/28指标仍未覆盖。

## 直接检查与数据库候选

26 个纯 synthetic 风险通过（3.01s），strict 4 文件、Ruff 4 文件通过；均 scoped_source_stable=true，global=false 因独立 recovery producer 源变化。日志：`docs/progress/evidence/W7/current-full-general-mechanism-original-exit-{direct,types,static}-20261006T03541*`。它们不是实际金融证据。

单一真实候选节点：`app/tests/test_full_experiment_asset_selection_integration.py::test_actual_complete_general_source_selects_distinct_rules_readonly_and_catalogue_drift_unknown`。仅 collected1，NOT_RUN。Root 后独占实际执行：真实 seed/银行收入/两种策略确认 → P/B1 原候选差异、B4 MISSING、完整物理表读取零写 → 原产品篡改 UNKNOWN/零写。该节点不调用银行消费者，不代表真实臂执行。

首轮 14PASS/1FAIL/2 temp setup ERROR、types1/static5 和第二次 Windows mode-0700 temp root拒绝、后续单一 import type RED均保留，原日志与修改前字节在 `.runtime/full-general-mechanism-*-red-*`。未删/降任何失败或原篡改负例。
