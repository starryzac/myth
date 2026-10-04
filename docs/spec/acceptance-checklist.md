# 钱途有界全局验收清单

执行频率按 2026-10-04 用户指令调整：全量在“初版完成”和“完整版完成”两个验收节点执行，失败后修复并在该节点重跑。开发过程中采用受影响模块及直接相关测试；资金守恒、防重复扣款、UNKNOWN 恢复或迁移运行相应集成，前端运行类型检查和相关页面验证。以下质量门与证据要求继续适用，定向通过不冒充版本全量通过。

核对日期：2026-10-03。全部状态初始 `PENDING`。本文件列出任务标题之外也必须满足的要求，并记录原计划的歧义；不证明当前实现完成，不修改原计划范围。`M` 为仓库根目录《钱途有界_初版开发计划_Codex执行版.md》，`F` 为《钱途有界_完整开发计划_Codex执行版.md》；数字为正文行号。92 项任务见 [需求追踪表](requirements-traceability.md)。

## 执行与范围

| ID | 要求及来源 | 应取得证据 | 状态 |
|---|---|---|---|
| EXEC-01 | 初版完成后升级完整版；按最小未完成编号推进；不跨 Epic 并行改核心模型（M:32；F:11、1782–1783）。 | 顺序进度账与里程碑；核心改动依赖记录。 | PENDING |
| EXEC-02 | 每个 FULL Epic 独立分支 F0 foundation、F1 evidence-policy、F2 cashflow-boundary、F3 goal-optimizer、F4 asset-engine、F5 intervention-engine、F6 action-recovery、F7 web-experience、F8 experiments-security、F9 proposal-delivery（F:74–97）。 | 实际分支与阶段质量记录；如运行权限影响 Git，显式记录未验证。 | PENDING |
| EXEC-03 | 任务实现、测试、文档、迁移/示例、必要 ADR、OpenAPI/类型同步、make check、progress 文件（M:43–56；F:89–97）。 | 每任务进度文档含实际命令输出和限制；先失败测试再实现的记录。 | PENDING |
| SCOPE-01 | 模拟工行内部闭环，无真实银行/真实资金/真实个人数据；无跨行、生活服务、股票等本金波动资产自动交易（M:32–44、157–168；F:22–31）。 | 数据、接口、产品池、UI、材料人工审查。 | PENDING |
| SCOPE-02 | LLM 只候选编译/解释/周期发现，不掌握金额、权限、安全、状态、适配或执行；前端不计算金融结果（M:34–39；F:99–110）。 | 数据流和服务边界审查、恶意候选和前端输入测试。 | PENDING |
| SCOPE-03 | 禁止 LibreOffice；额度限制终止前更新交接文件（用户规则）。 | 交付工具链记录；必要时交接目标、改动、验证、阻塞、临时边界、下一精确命令。 | PENDING |

## 命令与质量门

| ID | 要求及来源 | 成功必须证明什么 | 状态 |
|---|---|---|---|
| CMD-M01 | M:58–74：bootstrap、dev、seed、lint、test、e2e、check、demo-reset、export-evidence。 | 每命令确实执行对应工作；不是打印成功或直接退出。任一失败不进入下一阶段。 | PENDING |
| CMD-M02 | 初版 check = lint + test + e2e；最后 bootstrap → seed → check → export-evidence（M:69、1111–1120）。 | 真实退出码/输出；所有效果数字可追溯 run_id。 | PENDING |
| CMD-F01 | 完整 check 必须调用 lint、typecheck、unit、property、integration、e2e、security-check、audit-verify、evidence-check（F:112–126）。 | 九个子命令分别有覆盖实际要求的结果，任一失败向上传递。 | PENDING |
| CMD-F02 | 完整最终 bootstrap、seed、check、security-check、audit-verify、export-evidence、build-proposal、demo-reset（F:1746–1757）。 | 干净环境可复现运行记录，构建材料、证据和演示初态核验。 | PENDING |
| G0 | 用户、银行动作、非目标、自主边界清楚，术语唯一（F:1641–1644）。 | 规格/术语/权限矩阵与一级需求追踪审查。 | PENDING |
| G1 | 现金流不变量、未来收入隔离、多目标/定存约束通过，至少一基线（F:1646–1651）。 | 性质测试、独立预期与至少一实际基线比较。 | PENDING |
| G2 | 事实至回执闭环，策略修改/恢复，三黄金链加两异常链（F:1653–1657）。 | 集成/E2E/真实 UI 轨迹与独立账本回执。 | PENDING |
| G3 | 冻结、基线、消融、失败样本齐全，数字可追溯，研究只报实际（F:1659–1663）。 | 证据包校验、实验原始结果、研究开展记录。 | PENDING |
| G4 | 企划、白皮书、答辩、录屏、代码一致，边界显著，无 unsupported 宣传（F:1665–1669）。 | 逐项人工审核记录，不能仅用程序关键词扫描替代。 | PENDING |

## 数据、算法、权限与可靠性

| ID | 要求及来源 | 验收范围 | 状态 |
|---|---|---|---|
| CORE-01 | 金额整数分、带时区 UTC，指定技术栈/目录及 PostgreSQL 迁移（M:208–320、327）。 | 禁止二进制浮点金额、无时区时间；检查实库迁移。 | PENDING |
| CORE-02 | 核心表 users/accounts/transactions/credit_card_bills/asset_products/asset_positions/evidence_items/policies/policy_versions/goals/policy_proposals/decision_runs/decision_constraints/action_plans/action_receipts/audit_events（M:341–358）。 | 16 表、外键与金额/状态约束，升级回滚升级一致。 | PENDING |
| CORE-03 | 五证据等级；历史观察仅候选，声明未确认不授权，MODEL 只建议，冲突 CONFLICTED、缺证 UNKNOWN（M:329–337；F:288–296）。 | 各等级边界、证据冲突与缺失不可被模型补齐。 | PENDING |
| CORE-04 | 双时态字段 valid_from/to、observed_at、source_type/ref、content_hash、evidence_level、supersedes_id（F:270–286）。 | 迟到事实、覆盖、冲突、按当时已知信息回放。 | PENDING |
| CORE-05 | 策略版本不可变，确认、生失效、原因、证据、前 hash、影响；变更使未提交动作失效、在途复核、重算/持仓重查/恢复（F:298–318）。 | 修改/撤销竞争时序全覆盖；不能生成旧权限。 | PENDING |
| CORE-06 | 12 模板：RecurringObligation、LivingReserve、EmergencyBuffer、DatedExpense、LongTermGoal、PeriodicTransfer、AssetAuthorization、Recovery、GoalAllocation、CrossGoalReallocation、SeasonalReserve、Intervention（F:156–171）。 | 每类有效/无效实例与 Schema，禁止任意自然语言程序。 | PENDING |
| CORE-07 | 编译脱敏→实体时间→模板→严格 Schema→缺失冲突→摘要→用户修改确认；缺金额日期问，不能猜；来源片段、规则冲突确认（F:352–388）。 | 默认离线规则可运行，LLM 可选但须独立验证，绝不可直接 ACTIVE。 | PENDING |
| CORE-08 | 准备金取确认类别、排一次性、连续窗口分位数、确认缓冲；历史不足 INSUFFICIENT_HISTORY（M:459–478）。 | 公式数值、版本、历史区间、排除记录；同输入同输出。 | PENDING |
| CORE-09 | MVP 90 日、FULL 365 日；未来收入为零，资产赎回/到期前不可当现金；任何时点满足保护需求（M:480–504；F:432–479）。 | 用日级现金时序证明，不用余额总额代替；未来收入变形测试。 | PENDING |
| CORE-10 | 自主包络为 FinanciallySafe ∩ UserAuthorized ∩ LiquidityCompatible ∩ EvidenceSufficient ∩ SupportedAction（F:454–467）。 | 每集合缺一都拒绝自动；全部拒因可解释。 | PENDING |
| INV-01 | AUTO 后硬义务截止前现金覆盖（F:473）。 | 随机动作后各时间点验证，并与独立参考计算比较。 | PENDING |
| INV-02 | 未来收入变化不影响当前自主资金（F:474）。 | 未来收入扰动任意幅度/日期不改执行结果。 | PENDING |
| INV-03 | 已归属目标不被低优先级占用（F:475）。 | 多目标/资产调拨、恢复、回拨授权边界。 | PENDING |
| INV-04 | 失效策略版本不再产生权限（F:476）。 | 暂停/撤销/过期/修改/在途竞争。 | PENDING |
| INV-05 | 赎回可用前产品不计现金（F:477）。 | T1、锁定、到期和未知回执。 | PENDING |
| INV-06 | 有损提前支取不自动作为安全现金（F:478）。 | 未确认损失阻断；不虚构已恢复。 | PENDING |
| INV-07 | 重试不重复副作用（F:479）。 | 重复请求、并发、重启、回执丢失、UNKNOWN 对账。 | PENDING |
| CORE-11 | 多目标词典序：硬义务、应急生活、最低缺口、高优先缺口、延期、target、max、最少调动（F:502–515）。 | 小规模独立求解/枚举，不能把加权和误作词典序。 | PENDING |
| CORE-12 | 归属与资产分离；仅新增未归属资金自动分配；跨目标显式预授权且只特定紧急条件（F:517–524）。 | 账户/归属守恒、默认拒绝、策略失效边界。 | PENDING |
| CORE-13 | 最小冲突集；修复仅可调整策略，按改动数/偏离/优先级损失排序，确认成新版（F:526–535）。 | 不可满足证明及最小性，未冲突部分明确保持。 | PENDING |
| CORE-14 | 产品类 CASH/T0/T1/FD7/FD30/FD90/LOW_RISK_TERM；风险、本金波动、起投、锁定、赎回、到期、提前损失、模拟收益、自动权限、版本生效日（F:545–578）。 | 每类可行性正负例；高风险不进 AUTO。 | PENDING |
| CORE-15 | 安全/无新损失、用款日、恢复能力优先，其后净模拟收益、换手费用、并列流动性；MVP 最多单产品（M:524；F:580–599）。 | 锁定期限匹配和多资产容量/复杂度限制；到期最新策略重算。 | PENDING |
| CORE-16 | 不确定世界运行真实引擎，动作相同不问，minimax 一次一问，回答后重算且旧候选确认失效（F:603–664）。 | 比较类别/金额/归属/风险，不用置信度替代；无动作变化静默、介入去重。 | PENDING |
| CORE-17 | 状态 DRAFT→PLANNED→AUTHORIZED→SUBMITTED→SUCCEEDED/TERMINAL_FAILED/UNKNOWN→RECONCILED；root_id/attempt_id/幂等/版本；独立账本（F:670–683）。 | UNKNOWN 不按失败盲重试；状态转移与实际效果分离验证。 | PENDING |
| CORE-18 | 动作 9 类：TRANSFER_INTERNAL、PAY_RECURRING_OBLIGATION、TRANSFER_TO_CONFIRMED_PAYEE、ALLOCATE_TO_GOAL、PURCHASE_ASSET、REDEEM_ASSET、OPEN_FIXED_DEPOSIT、MATURE_FIXED_DEPOSIT、EARLY_WITHDRAW_FIXED_DEPOSIT（F:685–695）。 | 各类真实模拟账本效果、授权、回执及对账。 | PENDING |
| CORE-19 | 恢复自主现金→T0→T1→到期定存；有损提前支取转人工；来不及 LIQUIDITY_RISK（F:697–708）。 | 时间可用性、损失排序、部分恢复和无法恢复。 | PENDING |
| CORE-20 | 对账余额/归属/持仓/回执/预期实际/重复；Outbox/Inbox 重启不丢不重（F:710–719、1336–1342）。 | 故障点注入、持久恢复、差异人工处理，不能日志自证成功。 | PENDING |
| SEC-01 | USER/REVIEWER/SYSTEM/DEMO_ADMIN，动作绑定主体、授权、版本、幂等、范围；新外付意图须用户发起（F:175–186、955–970）。 | 身份/角色/范围正负例；审查员/演示管理员不能越权资金。 | PENDING |
| SEC-02 | 默认不发原始交易和标识给外部模型，最少字段；日志不含完整账号/手机号/身份证（F:947–953）。 | 脱敏单测、日志与导出检查、外部请求字段审查。 | PENDING |
| SEC-03 | STRIDE 至少含提示注入/越权字段/旧策略/审计篡改/重复动作/产品漂移/在途撤销/前端假解释/账本不一致（F:972–984）。 | 威胁→控制→测试/人工核验逐项追踪。 | PENDING |

## API、事件与体验

初版路由前缀统一 `/api/v1`（M:570–614）。应逐条核对，不能用一个总览接口替代：

```text
POST /demo/reset
GET /accounts/summary
GET /transactions
POST /demo/events/{event_name}
POST /policies/discover
POST /policies/compile
GET /policy-proposals
POST /policy-proposals/{id}/confirm
GET /policies
GET /policies/{id}/versions
PATCH /policies/{id}
POST /policies/{id}/suspend
POST /policies/{id}/revoke
POST /goals
GET /goals
GET /products
GET /positions
POST /engine/recompute
GET /decisions/{id}
GET /decisions/{id}/explanation
POST /actions/{id}/execute
POST /actions/{id}/confirm
GET /actions/{id}/receipt
GET /audit/events
GET /audit/events/{id}
POST /audit/verify
GET /exports/evidence
```

完整扩展路由（F:1131–1146），HTTP 方法由实现规格明确定义并测试：

```text
/facts/snapshots
/evidence/conflicts
/policies/{id}/impact-preview
/goals/allocation/plan
/goals/conflicts
/interventions
/interventions/{id}/answer
/assets/eligibility
/assets/optimization/plan
/actions/{id}/reconcile
/recovery/plan
/simulation/run
/experiments/runs
/evidence-packages/{run_id}
```

28 领域事件（F:1083–1113）及全部通用字段均需实际产生或有可执行产生路径：

```text
AccountSnapshotRecorded TransactionPosted BillIssued
PolicyCandidateDiscovered PolicyProposalCompiled PolicyConfirmed
PolicyModified PolicySuspended PolicyExpired PolicyRevoked
GoalCreated GoalContributionPlanned BoundaryRecomputed BoundaryCrossed
InterventionRequested ActionPlanned ActionAuthorized ActionSubmitted
ActionSucceeded ActionFailed ActionUnknown ActionReconciled
AssetPurchased AssetRedeemed FixedDepositMatured
RecoveryStarted RecoveryCompleted AuditChainVerified
```

通用字段：`event_id, aggregate_id, correlation_id, causation_id, idempotency_key, occurred_at, observed_at, payload_version`（F:1118–1127）。

| ID | 体验验收 | 证据 | 状态 |
|---|---|---|---|
| UX-01 | 三黄金链：工资到账→保护/目标/资产自动安排；自然语言目标→结构化修改确认→持续运行；消费→安全恢复或损失确认（M:89–121）。 | 真实浏览器 E2E、状态和账本效果，完整解释。 | PENDING |
| UX-02 | 初版首页、策略、目标、轨迹、事件控制台字段完整；控制台仅注入事件（M:618–678）。 | UI/API/账本一致，连续演示三次无需改库。 | PENDING |
| UX-03 | 完整首次八步引导、八首页卡、策略依赖地图、目标中心、最小介入、审计搜索/版本过滤（F:1003–1075）。 | 每个页面可交互，不能静态卡片替代；移动/无障碍测试。 | PENDING |
| UX-04 | 离线三黄金链、可重置、备用完整录屏；截图与录屏同 run_id（M:833–836、1038–1043）。 | 真实浏览器录屏和截图来源、离线运行环境、run_id 关联。 | PENDING |

## 实验、证据与交付材料

| ID | 要求及来源 | 验收证据 | 状态 |
|---|---|---|---|
| TEST-01 | 后端覆盖 ≥85%，边界/策略版本/恢复 ≥95%，至少 1000 组 Hypothesis，前端交互（M:801–808）。 | 真实分支/语句覆盖口径、模块清单、运行配置及原始报告。 | PENDING |
| TEST-02 | 六 E2E：工资、目标、消费赎回、定存询问、改房租旧动作、审计（M:810–819）。 | Playwright 实跑及断言核心账本/权限后果，非仅页面可见。 | PENDING |
| EXP-M01 | 24 冻结案例：6 正常、6 目标/义务冲突、4 消费收缩、4 定存/流动性、2 到期/换租、2 即时转账歧义（M:875–886）。 | 独立场景和真值、冻结校验和、开发隔离；规则升级后全跑。 | PENDING |
| EXP-M02 | B0 手工、B1 余额阈值、B2 静态规则、B3 全量确认、P；安全/效率/解释全部指标（M:840–873）。 | 同输入实际运行；违反、越权、流动性、有损误自动、旧版、覆盖、介入、闲置、时延、追溯/证据/链等完整结果。 | PENDING |
| EXP-F01 | 至少 50 族冻结案例，每族多个变体；整族分开发/验证/冻结；真值不是 LLM（F:802–838、1412–1414、1713）。 | 族差异依据、划分清单与无泄漏审查、真值含义务/授权/范围/产品/动作/等级/冲突/恢复。 | PENDING |
| EXP-F02 | B0 手工、B1 阈值、B2 固定预算产品、B3 全量确认、B4 模型置信度、B5 不考虑流动性收益优先、P（F:844–852）。 | 各机制真正实现，独立实际输出，不能伪造基线失败以抬高 P。 | PENDING |
| EXP-F03 | 八消融：证据等级、策略版本、动态生活准备金、多目标、流动性、最小问题、安全恢复、审计（F:898–911）。 | 每次只移除指定机制、实际结果、安全/自主/交互变化与失败。 | PENDING |
| EXP-F04 | 安全 9 类、自主效率 8 类、资金效率 5 类、审计解释 6 类指标（F:854–896）。 | 分子分母/时间口径、真值、CSV/JSON；收益只能同安全和授权约束比较。 | PENDING |
| EXP-F05 | 性能/并发/重启/重复/UNKNOWN/大规模故障（F:1424–1426）。 | 环境、负载、故障点、原始样本、时延和账本一致性；失败样本保留。 | PENDING |
| STUDY-01 | 18–24 成年青年拟招募，合成账户；候选确认、建目标、解释配置、收缩、审计、改撤策略；比全量确认/静态/P（F:913–930）。 | 任务脚本、同意文本、问卷、匿名工具、实际招募和开展状态；不得杜撰参与者或代填问卷。 | PENDING |
| STUDY-02 | 记录完成时间/介入/理解/控制权/信任校准/误解；仅探索性不外推（F:932–941；1663；1729）。 | 真实匿名记录和分析；未开展时明确“未开展”及人工外部验证项，不能标研究完成。 | PENDING |
| EVID-01 | 每决策 run_id、事实、等级、版本、约束图、算法、不确定候选、边界、候选/过滤、等级/问题、计划/回执、差分、hash（F:725–744）。 | 从动作到事实完整追溯；每字段真实可复现。 | PENDING |
| EVID-02 | 解释可动/不可动、不问/必问、选 T1/非定存、赎回/禁提前支取、边界变少、使用版本；结构化生成（F:746–760）。 | 解释与真实约束逐项一致，不依赖 LLM 临场编造。 | PENDING |
| EVID-03 | 包含 manifest.json、inputs/、policies/、decision_traces/、action_receipts/、metrics.csv、failures.jsonl、screenshots/、audit_chain.jsonl、report_fragment.md（F:762–777）。 | 文件实际内容与 manifest 校验；空目录/占位文件不代表对应证据存在。 | PENDING |
| EVID-04 | 材料每个效果数字到 run_id 或调查记录，CI 拒绝无源数字（F:779、1584）。 | registry 与正文校验负例；不得用元数据“页数/日期”等误作实验结论或漏检效果主张。 | PENDING |
| MAT-M01 | 初版 README、架构、DSL、API、威胁边界、实验与原始结果、企划、技术附录、四分钟稿、截图录屏清单（M:1098–1109）。 | 内容完整且与代码一致；未测结果留空或待实验。 | PENDING |
| MAT-M02 | 八图：余额分层、架构、生命周期、边界时间轴、资产过滤、恢复、证据链、实验对照（M:995–1004）。 | 可读实际图件、图注册和数据来源；截图不能用渲染图替代。 | PENDING |
| MAT-F01 | 10–12 页企划；25–35 页白皮书（22 建议章节）；8–12 页答辩（F:1436–1454、1468–1558）。 | 实际分页成品及源，真实页数检查、视觉审查；纯 Markdown 章节数不证明页数。 | PENDING |
| MAT-F02 | 四分钟现场版/备用录屏、问答、证据索引、复现说明（F:1448–1454、1735–1744）。 | 240 秒内容与录屏时长、同 run 截图、材料演示代码一致。 | PENDING |
| MAT-F03 | claims_registry.yaml、figures_registry.yaml、metrics_registry.yaml、企划书_正文.md、技术白皮书.md、答辩问答.md、evidence_links（F:1560–1584）。 | 主张文本/类型/来源或 run_id/允许入正文/最近核验/责任人齐全。 | PENDING |
| FINAL-01 | 最终人工确认无跨行生活漂移、未来收入透支、高风险自动、LLM 替规则、不可追溯动作、未支持结论（F:1759–1766）。 | 明确人工审查记录，未完成必须保留，不用 green checks 代替。 | PENDING |

## 原计划歧义及处理边界

下列是审查发现，尚不是已经实现的解决方案。需要实现决策时写 ADR；不能借此缩小原范围。

| 编号 | 原文问题 | 执行时应保持的要求 | 状态 |
|---|---|---|---|
| SPEC-01 | M:174 标题“三档”，M:176–182 列 AUTO_EXECUTE、ASK_ONCE、ADVISE_ONLY、BLOCKED 四种。 | 以四个枚举和具体语义为准；不删除 BLOCKED。 | PENDING |
| SPEC-02 | M:710 写“三个资产产品”，M:747 和 8.3 要求 CASH/T0/T1/定存四种行为。 | 产品实例数与类别区分，确保四类行为全部覆盖；不能为凑三个省略 T1 或定存。 | PENDING |
| SPEC-03 | M:1029“四分钟”六段 30+55+55+65+45+30 = 280 秒（M:1031–1036），F:1450/1740 要四分钟。 | 保留六段和黄金链/异常/证据内容，演示稿压至 240 秒并实测；不可把 280 秒声称四分钟。 | PENDING |
| SPEC-04 | F:804“至少 50 场景族”与 F:1412/1713“50 族冻结”并存；F:823 要整族划分。 | 冻结集至少 50 族，开发/验证另设整族；禁止把 50 总族拆分后声称 50 冻结族。 | PENDING |
| SPEC-05 | F:654–664 列边界缩小/赎回等介入触发，但 M:119/F:182 又允许预授权无损自动恢复。 | 先重算动作及授权；已授权无损恢复可自动并通知，真正新事实/新后果/未授权才询问；用 ADR 明确“事件”和“必须确认”区别。 | PENDING |
| SPEC-06 | F:915 用户研究是拟招募；FULL-806 要工具；最终 F:1729 列用户研究记录，G3 仅报告实际。 | 工具与真实研究分别记状态；未开展可交付工具和诚实记录，不能虚构样本，不能据此声称用户验证完成。 | PENDING |
| SPEC-07 | F:1657 要两个异常链但未在该处点名。 | 在规格明确选取且真实验证，例如有损定存确认、策略变更导致在途复核/UNKNOWN 对账；不能仅列名称而无链路。 | PENDING |
| SPEC-08 | “无显著损失”和“有损提前支取必须确认”并存（M:547–548；F:599）。 | 默认任意正提前支取损失需 ASK_ONCE；若定义显著阈值须有明确用户授权、ADR 与测试，不得自行放宽。 | PENDING |

## 停止结论或降级条件

计划 F:1693–1700 要求：静态规则同安全同介入时降低智能主张；minimax 不优于全量确认时去掉过度研究包装；动态授权难理解时减少模板；多资产不能稳定解释时退回单产品；定存恢复错误时初版只展示到期管理；任何冻结案例违反硬约束停止效果结论。执行这些条件需要实际证据、明确范围变化记录和保留原目标的未完成状态，不能靠自行降低验收标准获得“完成”。
