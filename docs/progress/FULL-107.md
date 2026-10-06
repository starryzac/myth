# FULL-107 周期规律候选

## 实现和验收状态

2026-10-05：功能优先执行修订二下新增生产 domain/service/router。实现为有限、可运行的只读候选能力，原编号验收状态仍 PARTIAL；未因旧 MVP 房租/信用卡发现文件直接关闭。原完整计划 392—408 行要求固定日期附近相似金额、同收款人/账单机构、连续周期、金额方差和类型稳定，输出建议，不能自动建立未来义务。

GET `/api/v1/policy-suggestions/periodic`，operationId `suggest_periodic_policies`。路由 `app.api.v1.policy_suggestions.router` 由根任务注册；共享依赖应将 `/api/v1/policy-suggestions/` 纳入 `full_read`。服务复用 `evidence_graph.readonly`，实际检查干净 Session、REPEATABLE READ、READ ONLY；仅 GET 的 RR 设置不满足服务条件。

固定 DemoUser/服务器时钟，客户端只能收紧：`lookback_days` 1—56（默认56）、`minimum_cycles` 2—12（默认2）、`maximum_day_spread` 0—2（默认2）、`maximum_cv_bps` 0—1000（默认1000）。未知查询字段、用户身份、now、银行事实、coverage、金额结果、自动确认均拒绝。

## 可运行能力

每次调用从当前用户读取实际账户、完整交易、证据、账单；账户100、交易/账单100000、证据250000为明确容量门，超限拒绝，不截断成成功。复用原 living_reserve `_coverage` 全量重放原覆盖证书清单及银行事实，核完整账户范围、闭日、owner、事实/证据规范哈希、可知时间及经济角色。`history_proof.verified` 仅指该覆盖/原事实合同核对，不是完整审计链或独立经济效果证明。

支持原 BANK_CONFIRMED `INTERNAL_TRANSFER` 的现金账户借方；消费只有已具唯一、有效且匹配的 USER_DECLARED 房租类别才进入房租候选；其他普通消费不会成为义务。收款标识逐字来自银行事实，空值/非规范空白不猜、不静默裁剪。原信用卡账户账单额外核 bill/user/account/source、唯一证据使用、最低还款额、总额/已还款/到期/账期/状态；没有账单外部历史覆盖证明时只声称已观察的实际账单序列。

按 kind＋源账户＋真实收款标识分组，只支持每个连续自然月一次。不删除异常值、不拼不同收款人/类型、不用未闭今天或未来账单到期日凑样本。展示所有样本/周期、日期跨度、下中位数建议日期、金额 min/max/均值、总体方差和CV平方的精确分数。对 n 个金额，方差 `(n*sum(x²)-sum(x)²)/n²`；CV阈值以整数平方交叉相乘比较，无浮点金融计算。

满足有限规则才返回 `READY` 和已通过原 FULL Schema 的 `PeriodicTransferPolicy` 或 `RecurringObligationPolicy`；`auto_execute=false`、`advice_only=true`、`requires_confirmation=true`、`bank_authority=false`、`future_obligation_guaranteed=false`。候选 cap/range 仅是已观察金额建议，不是用户已授预算。缺历史、错误来源、日期/金额/周期不稳定分别保留 `INSUFFICIENT_HISTORY`/`UNKNOWN`/`UNSTABLE`；无合格字段不会假 READY。每次 fresh SELECT/源核；确认索引仅本请求内复用，没有跨请求授权缓存。

## 检查和原件

新七源：`domain/pattern_suggestions.py`、`services/policy_suggestions.py`、`api/v1/policy_suggestions.py`、四个 `test_pattern_suggestions/test_policy_suggestions_service/test_policy_suggestions_api/test_policy_suggestions_integration.py`。日志和最终原源码SHA档案位于 `.runtime/FULL-107-108-patterns`。首次77纯/API PASS2.65s；新增整年历史正负例后129相关纯PASS3.45s（含旧 living_reserve/history_coverage），均为工具无关的模块/API doubles，不能当实际银行效果。首次类型9错误、第二类型1个 Optional 窄化错误、格式前Ruff长行 RED 原日志全部保留。最终 checks/source manifest 交付时登记；没有跑全量。

实际 PG 候选仅 `test_policy_suggestions_integration.py::test_actual_patterns_public_calendar_and_source_refusal_never_write_or_grant`。设计在生成隔离 bf_test 的原 seed 上核3种真实周期/实际来源、重复读取、全表零写、节日缺历史 null；再只在夹具通过原ORM改覆盖 status，下一请求必须拒绝，原 content/hash 不改。根任务执行前状态 NOT_RUN，不能据 collection 写 PASS。

## 未覆盖

候选 UI/LLM 编译及用户实际采纳到原持久生命周期，由根任务后接；本包不创建 proposal/Evidence/Policy、义务、确认、动作、银行 operation 或准备金。更宽周期、周/月末/调休日规则、其他账单机构、外部收款新绑定、未来收入以及 FULL 模板执行均未覆盖。没有当前真实银行或真人研究，不开启真实资金接口。原 FULL-107 完整验收未关闭。

## 2026-10-05 UTC 真实必要节点追加（原批次失败保留）

最终135相关纯/API风险 PASS3.40s；七文件 strict mypy、Ruff、format --check PASS。实际 PG 仅collection：1 test collected1.74s，尚未运行。原首次类型9错、第二类型1错和格式前Ruff RED日志保持。运行命令、当前原源码bytes、精确SHA和日志归档见 `.runtime/FULL-107-108-patterns` 的 final-source manifest。原编号仍PARTIAL；这些检查不构成全量验收或金融实证。

## 真实首批失败与未确认房租边界

根串行 W5 首实际批次2FAIL/1PASS634.56s，原 manifest/FAILED 与 seven-relevant 原件保留 `.runtime/W5-public-three-first-pg-failed-20261005T1622Z`。本周期节点首行错误期望 seed 房租已确认，从 `INVALID_CATEGORY_CONFIRMATION` 拒绝，尚未进入 HTTP 段；不能说该节点 HTTP PASS。原 demo_seed 只确认 food/transport/daily_necessities，两条真实 rent.category_confirmed=false，未有 USER_DECLARED 分类证据。本服务拒绝正确，无来源兼容 bug，不修改 seed、bank/history hash或降低确认门。

实际测试原源另存 `.runtime/FULL-107-108-patterns/actual-unconfirmed-rent-first-source`。新初态断言核两条原 rent 未确认、UNKNOWN/null 候选、实际 HTTP 同结果/全表零写/假facts422，保留原coverage篡改负例。原三种 READY 正合同保存为 `assert_confirmed_patterns`，只能在根新生产类别确认入口通过明确隔离合成 actor 确认两条 rent 后调用；未调用或未实跑不得称该正例 PASS/真人研究。根负责真正类别确认服务与 typed audit，未直接补造来源或改 seed 行来凑成功。生产 domain/service/router 七源保持旧 bytes，本次仅必要测试差量；新实际运行仍待根闭合。
