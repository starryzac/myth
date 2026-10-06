# FULL-107 独立日历周期发现 v2

## 原要求与本包状态

原完整计划《钱途有界_完整开发计划_Codex执行版.md》392—408、1200—1202：银行历史中相同收款人或账单机构、交易类型稳定、连续周期、固定日期附近相似金额和方差阈值，仅询问是否建立策略，不直接建立未来义务。追踪表 FULL-107 仍 PENDING；旧 FULL-107、旧 v1 规则、原失败和所有正式历史不改。

本包新增 `domain/calendar_periodic_suggestions.py`、`services/calendar_periodic_suggestions.py`、`api/v1/calendar_periodic_suggestions.py`、`tests/test_calendar_periodic_suggestions.py`。Backend FINAL/HOLD；Main、RRRO、生成合同与宿主由 Root 集成。没有迁移、seed、reset、金融 POST、通知、自动授权或跨请求缓存。

## 可运行合同

GET `/api/v1/policy-suggestions/calendar-periodic`，operationId `suggest_calendar_periodic_policies`；router `app.api.v1.calendar_periodic_suggestions.router`。Root 应沿已有 `/policy-suggestions/` GET 只读 RRRO 依赖接入。服务额外核干净 session、真实 `SHOW transaction_isolation=repeatable read` / `transaction_read_only=on`，当前服务端用户唯一 OPEN epoch、epoch.opened_at 不晚于实际服务器时间。

参数仅发现选项：`lookback_days` 1—365，默认365；`minimum_cycles` 3—12，默认3；`maximum_day_spread` 0—2，默认2；`maximum_cv_bps` 0—1000，默认1000。未知 query 拒绝。客户端不能提交身份、epoch、时点、事实、金额、结果、回执或确认。

响应 `CalendarPeriodicSuggestions` 协议 `full-calendar-periodic-discovery-v2`，包括 server `user_id/epoch_id/as_of`、实际 parameters、history_start/end、原 HistoryProof、完整 patterns/samples/originals/supporting_sources、source_issues、排除普通消费数量、原 source_evidence_ids、绑定完整 epoch row_copy 及历史原件的 source_digest。`writes_performed/grants_authority/bank_authority/hard_protection_changed/future_obligation_created/audit_chain_verified` 均 false；最后一项明确本候选核验不能冒充完整审计验真。

每个真实 kind/account/payee 组完整比较三种日历假设：`MONTHLY_DATE`、`MONTH_END`、`WEEKLY`。一周期多次、缺周期、日期跨度、完整整数金额方差、不闭日/未来样本、重复原 fact、覆盖或来源缺失均阻止可确认配置；不修剪异常值、不把不同 payee/type/account 合并凑样本。每组每种假设都保留全部样本与具体不成立原因，不自动择一。均值、总体方差和 CV² 为精确分数，阈值使用整数交叉相乘，无浮点金融数值。

状态 `READY_DISCOVERY/INSUFFICIENT_HISTORY/UNSTABLE/UNKNOWN`。`candidate_support=AVAILABLE_FOR_USER_REVIEW` 才有原 FULL Schema 验证的 configuration/hash/template；不是确认或当前权限。`DSL_UNSUPPORTED` 和 `NOT_READY` 的三个候选字段都是 null。

真实月底仅全部样本 offset=0、spread=0 可映射 due_day=31：原 `domain/execution.py` 和 `domain/full_payment_permissions.py` 的 `min(due_day, monthrange(...)[1])` 已明确短月月底合同。周规律、非零月底偏移及包含月底抖动的规律没有当前可表达 DSL，仍只读建议，不能给可确认月策略。未成立规律 next_occurrence=null；成立规律的下次日期明确 `next_occurrence_is_hypothesis=true`，并非银行已开账单、义务或资金承诺。

FIXED_TRANSFER 只取原 CASH 借方 INTERNAL_TRANSFER；RENT 仍需原唯一、当前有效 USER_DECLARED 类别证明；信用卡账单原 account/bill/evidence/金额/账期/已知时间/来源使用均核。复用原历史覆盖完整重放，不用交易名称推断。原账单没有独立外部历史覆盖，`OBSERVED_BILLS_ONLY` 永远保留，不能将已见账单序列说成完整所有账单；交易为空也不能推出未来无义务。

## 已运行检查

- 首次直接 19 PASS / 12 FAIL / 14 setup ERROR：合成 AuditEpoch 夹具多传不存在 `updated_at`；原日志 `W5/calendar-periodic-v2-first-direct-20261006T054210Z-04ec2c20` 与源码 `first-runtime-red-20261006T054310Z` 保留。
- 首次 mypy 4 错：局部 date|null 类型、Literal 默认、测试 null 跨类型 identity；原日志 `calendar-periodic-v2-first-types-20261006T054211Z-5fb24880` 保留，无 suppression。
- 修复夹具/类型后 45 直接风险 PASS2.49s：`calendar-periodic-v2-fixture-repaired-direct-20261006T054335Z-59263b15`。旧断言、金额边界、未知来源、STRICT query/HTTP codec 全保留。
- 新三周期完整合成 BANK/覆盖证书原形状正例分别为短月月底、周、月底前一日：3 PASS2.03s，`calendar-periodic-v2-three-cycle-source-risk-20261006T054510Z-aec651d6`。这是新的模块 fixture，不是真实银行/真人/PG研究数据，未把合成证据当正式原件。
- 最终四源 strict mypy / Ruff PASS：`calendar-periodic-v2-three-cycle-final-types-20261006T054510Z-5c754f5d` / `calendar-periodic-v2-three-cycle-final-static-20261006T054510Z-64143be7`。来源与原失败归档在 `.runtime/FULL-107-calendar-v2/`。45 已过未变行为与新增3分开记，不冒称同批48金融验收。

## 未覆盖与下一依赖

真实 PG、浏览器、全量、真人周期研究均 NOT_RUN / NOT_STARTED。本模块无实际真实资金接口。周、月底前若干日、调休日、季度/年度、同机构多个独立账单序列、独立账单覆盖及真实来源的候选直接采纳尚未实现。v2 原观察只允许用户复核配置后去既有持久声明/原确认入口；不把发现当自动义务、收款人银行关系或执行权限。Root 安装实际 router/RRRO/生成 schema 后，新独立 Web reader/panel 继续；原 FULL-107 不关闭。
