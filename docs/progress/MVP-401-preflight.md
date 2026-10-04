# MVP-401 资金边界首页：只读前置设计与验收映射

当前实施前置：304已按2026-10-04用户授权节奏定向关闭（17/92），本地提交后推进401。下文逐任务全量等待/全量check频率为历史设计，现按AGENTS1.4仅两个版本节点；本设计的金融、来源、只读和真实黄金链要求继续适用。ignored候选代码及旧v1真实30变体输出已有准备，尚未部署，不计401通过。

状态：DESIGN_ONLY / NOT_IMPLEMENTED / NOT_VERIFIED（2026-10-04）。本文件在 MVP-304 收尾等待期间只读编写。401 源码、合同、测试、资金动作和正式 seed 均未在此预审中修改或运行；401 实现必须等 304 完整验收并提交。本文不修改已冻结的 304 工作流挂钩。

## 计划与现状

依据仓库[初版计划](../../钱途有界_初版开发计划_Codex执行版.md)第 2.2 节三条黄金链路、第 10.1 节首页和 MVP-401–404：401 要交付余额分层、下一义务、自主资金、介入状态和解释入口，并让三条链路的状态变化在首页反映。核心视觉应解释资金边界，不能用收益曲线替代。完整版计划中的多用户、调度、完整资产类别和消息可靠性按后续任务推进。

当前 `apps/web/src/App.tsx` 只调用 `/api/v1/health`；四个 Vitest 用例仅验证模拟标记、健康失败与重试。`tests/e2e/health.spec.ts` 仅验证 Web→代理→真实 FastAPI health。Playwright 默认 project 为 `edge`、channel 为 `msedge`，但环境变量可以覆盖。已有工程连通 PASS **不证明任何资金卡片、黄金链路、解释或业务状态已通过**。

主要真实来源：

| 来源 | 当前事实与边界 |
|---|---|
| `api/v1/accounts.py:account_summary` | 账户、账单、现金本金和 UNKNOWN 本金的账面事实汇总；不是自主资金判断 |
| `services/boundary.py:load_boundary_context/compute_user_boundary` | 验证银行模拟证明、策略、目标归属和结清来源；确定性财务边界 |
| `domain/boundary.py:compute_boundary` | 今日至第 90 日，共 91 日、273 个事件阶段；保护分项来自首点 BEFORE_PAYMENT，safe_idle 来自全窗最小余量 |
| `services/autonomy.py:assess_action` | 具体动作的只读四级评估、确认要求/满足状态；evaluation_only，不等于执行回执 |
| `services/execution.py:get_action` / `action_contracts.py` | 原动作、effect/hash、bank_status、回执、decision_run_id；GET 不执行 |
| `services/decision_trace.py:get_action_trace/get_decision_trace` | 原 as_of 与当前 read_at 分开，原解释、当前引用和审计状态分开 |
| `services/goals.py:list_goals` | 目标投影；分层金额仍须通过目标归属证明核对 |
| `services/recovery.py:preview_recovery/get_recovery_run` | 条件恢复计划与 actual_boundary 分开；GET/preview 不结算 T1 |
| `services/execution_exposure.py`、`asset_exposure_import.py`、`recovery_sources.py` | 完整曝光、原申购来源、当前待执行/已实现状态；不能只凭 policy_version_id 猜测“系统配置” |

架构语义参照 [账户事实](../architecture/account-facts-api.md)、[边界 API](../architecture/boundary-api.md)、[边界来源](../architecture/boundary-service.md)、[执行 API](../architecture/execution-api.md)、[决策轨迹 API](../architecture/decision-trace-api.md)及 ADR 0005/0009/0011/0012。实施时以当次冻结源码与生成 OpenAPI 为准，历史文档中的 seed/表数/样本金额不直接用作新 UI 真值。

## 每张首页卡片的口径

| 计划卡片 | 真实来源与建议显示 | 必须保留的边界 / 当前 API 缺口 |
|---|---|---|
| 当前总余额 | `AccountSummary.cash_balance_cents`，标题明确“当前账户现金”；非信用卡账户现金合计，含目标账户现金；显示逐账户或 oldest/latest observed_at | 信用额度、持仓本金、收益、未来工资都不计入这张现金卡。若需“总资产”另列账面现金/本金/UNKNOWN/负债，不伪装成现金或净可用额度 |
| 受保护资金 | `BoundaryResult.protected_cents_by_reason`：obligations、living、emergency、goal_cash、goal_minimum；总额由同一结果的分项求和 | 这是当前首点保护，不是各日分项最大值拼接。信用卡保护为真实未付总额，不用最低还款代替；证据不足时空 map 不显示“保护为零” |
| 目标归属资金 | 经 `load_boundary_context` 验证的 GoalOwnership：allocated = cash_owned + principal_owned；首页同时标目标现金和目标本金，未映射 GOAL 账户现金另列“待明确归属的受保护目标现金” | `GET /goals` 的 allocated_cents 不能单独升级为已验证来源。已有归属在策略暂停/撤销后仍受保护；goal_cash 已包含于受保护分项，不能再次从现金扣减；目标本金可能也属于已自主配置，不能跨卡重复累加 |
| 当前可自主资金 | `boundary.safe_idle_cents`，文案“91 日窗口内可持续占用的一般资金上限”，旁列 minimum_margin/deficit 与约束日期 | `financial_only=true`。READY/正额度都不代表已有权限、可给任一产品或 AUTO_EXECUTE。产品上限也不等于执行资格。未来工资/退款预测、收益不扩大它；null 显示“暂无法证明”，不是 0 |
| 已自主配置资金 | 从已验证完整曝光与原 AUTHORIZED_PURCHASE 来源筛选当前未结清本金，分清 general/goal；说明这是已配置本金 | 现 `/positions` 仅给持仓投影，没有可直接用的“自主配置合计”。不能把全部 HELD 当系统购买，不能仅凭 policy_version_id 判断；MANUAL/UNKNOWN 来源、UNKNOWN 状态、待申购/预留分开显示，不能与现款相加称可用 |
| 下一笔确定义务 | 同快照的已验证账单/已确认 recurring 版本/实际结清事实，输出最早未结清 occurrence 的 due_date、remaining_cents、身份/来源、是否逾期；并列同日多笔时保留数量或完整列项 | 当前 BoundaryResponse 只有 occurrence IDs 与分阶段 trace，没有完整 next_obligation DTO。前端不得解析 ID 自造金额/支付日，不按“流水相似”猜结清。过期但未结清义务仍须反映；未知金额不能显示“没有义务” |
| 是否需要用户介入 | 具体待处理动作的 `AutonomyDecision.level/reasons/confirmation_required/confirmation_satisfied`，叠加 action.status、bank_status、receipt、来源核验状态 | 当前缺 bounded 首页动作列表；不能从“最新 DecisionRun”或整个余额推断全局授权。原 ASK_ONCE 保留历史来源，已确认后用 confirmation_satisfied 表示，无需重复索取同一确认；NONE 仅表示没有已知待处理事项，不宣称所有未来动作获准 |
| 为什么可以自主使用 X | 当前边界的 as_of/input_digest/boundary_hash、保护分项、minimum_margin、约束/notes；具体动作另读 `/actions/{id}/decision` | 当前金融解释与历史动作解释必须有各自时点和身份。GET 边界不会保存 run；禁止为被动打开解释调用 `/decisions/assess`、prepare 或 execute。不能把某次旧 AUTO 解释用于当前额度 |

分层视觉分为“当前现金与当前保护”和“整个窗口的可持续上限”两层。`cash − 当前保护` 不一定等于 `safe_idle`：后续生效保护、付款/返本阶段都会改变窗口最小值。不能为了画满条形图强凑等式；保护超过当前现金时显示真实缺口，不能把负余量抹成安全。目标归属和持仓是归属/配置的交叉视角，用明确标签或包含关系展示，不能作为互斥分块重复相加。

## 最小一致读取接缝（待 401 冻结合同）

现 `api/dependencies.py:get_session` 给每次 GET 独立 REPEATABLE READ 事务。一个响应的多次 SELECT 一致；浏览器并行请求 account/boundary/goals/actions **不是同一个快照**。在银行独立提交与应用投影之间拼接多个响应，可能显示“新余额 + 旧归属 + 旧已配置”并误报安全。单靠相同 user_id 或近似 as_of 不能修复这个问题。

建议 401 增加一个最小只读聚合入口，例如 `GET /api/v1/dashboard`（这是提议，当前不存在），在一个 caller-owned RR、READ ONLY Session 和一个可信 now 下组装首页；复用原验证/纯域算法，不在浏览器或聚合服务镜像重新实现保护、周期结清或权限引擎。

最低返回应包括 simulation=true、user_id、可信 as_of、timezone、账户事实观察时间范围、现有 boundary 结果/source_issues/来源摘要、已核目标归属、已核配置/待对账本金、结构化 next_obligations、bounded 待处理动作及明确完整性标记。资金事实与金融判断各自有有效性，不用一个成功 HTTP 状态掩盖某张卡片无法证明。动作卡只读调用原实际评估/回执/轨迹；超过预算或列表不完整时不能显示“无需介入”。

下一义务的数据提取应与 compute_boundary 使用同一经过结清校验的 occurrence 生成逻辑；若需提取纯 helper 或加只读结果字段，作为 401 明确合同变更处理并复用同一逻辑，不能另写一份支付日/金额算法。自主配置合计同理复用完整曝光与原来源校验，不能在客户端临时推断。

GET 聚合/解释/轮询禁止刷新策略生命周期写入、生成决策、开 epoch、补 audit 事件、预留资源、结算 T1、释放 UNKNOWN 或执行恢复。自然时间推进可以改变只读有效状态；实际到账仍需原 POST/对账事务。同一事务看见独立银行 SETTLED 但应用尚未投影时，应显示待对账/来源不足，而不是补造回执。

## 页面状态、整数分与刷新

| 状态来源 | 首页行为 |
|---|---|
| boundary READY | 展示已证明金融上限及“仍须具体动作授权/可行性”；已有策略与无授权分开，不自动执行 |
| INSUFFICIENT_EVIDENCE | 自主上限、无法证明的保护数值用“待核验/—”；展示 source_issues 的用户可读原因和重试，不把未知变成 0，不隐藏仍可明确展示的账面事实 |
| LIQUIDITY_RISK | 展示真实 deficit、最早/最紧约束日期及原因；保持现金账面数，解释安全恢复需求；不评价用户消费，也不通过未来收入把风险改绿 |
| action UNKNOWN / bank UNKNOWN / 已 SETTLED 未投影 | “执行结果待对账”；保留原动作/银行身份与预留边界，不叫失败已退款，不生成新 operation；只读刷新不会恢复执行 |
| bank ACCEPTED / recovery PENDING_SETTLEMENT | 展示受理与预计可对账时点；T1 尚未到账。到期 GET 仍保持待结算，不能由浏览器时钟当成已到账 |
| ASK_ONCE 且确认未满足 | “需要确认具体后果”，显示服务端费用/损失/金额/到达约束和原因；首页仅给查看入口，不能把 READY 用作默认确认 |
| ASK_ONCE 已满足 / AUTO_EXECUTE | 分别说明“原确认已核验”“具体动作在策略内可执行”；成功到账仍须独立银行与实际回执，等级不是执行状态 |
| ADVISE_ONLY / BLOCKED / 无已确认策略 | 显示对应原因和建议/授权边界；无策略不等于所有余额都能自动安排；无待处理项不伪造一个 run |
| 初次加载 / 真空集合 / 读取错误 | 加载用 skeleton，不闪现金0；合法空目标/无待处理动作/已证明无窗内义务各有明确空态；来源不足不能当空态；错误显示脱敏消息与 request_id，保留“旧快照/读取失败”标记 |

金额传输与比较均保持整数分，服务器继续严格整数金额合同。前端入口验证解析值 finite、integer、`Number.isSafeInteger`，拒绝 bool/string/非整数/超安全整数金额并显示契约错误，不能静默舍入；后端 int64 不能不加检查就靠 TS number 保证精确。该检查不冒充对原 JSON 数字词法的无损校验，不能用它代替服务器严格合同。元格式化用整数商/余数或 BigInt 表示层，禁止先用浮点计算金融额度；负 margin 明确保留符号，null 独立处理，0 是确证零。未来如采用十进制字符串 DTO，须显式冻结生成合同，不悄悄改变现接口金额协议。

首次读取、用户主动刷新、窗口重新获得焦点和有限间隔只读轮询可更新整份聚合快照；前台可采用约 15–30 秒间隔，页面隐藏/离线时暂停。新响应整批替换卡片，旧请求取消或用 generation 防止晚返回覆盖新快照。显示服务端 as_of、账户观察时间及刷新中/旧快照状态；不把本地 Date.now 当权限时钟。HTTP 错误、simulation=false、结构/金额失真时不继续宣称快照有效。保留上次完整数据时必须标旧数据，不能保留旧绿色“现在可用”结论。只读重试不能转成资金 POST。

## 解释入口与 402–404 分界

401 提供可实际使用的轻量解释面板：当前金额、五类保护、91 日约束/缺口、来源不足、financial_only、当前解释时点。已有动作可以显示历史 summary/reasons、原策略/证据标识、实际回执是否存在以及当前 audit_chain_status；审计 LEGACY/UNSUPPORTED/INCOMPLETE/INTEGRITY 不显示“已完整验证”。原 explanation.audit_chain 的生成时历史字段不能替代当前顶层状态。

402 才实现自然语言候选编辑、首次确认、策略修改/暂停/撤销/到期、目标详情与影响预览。403 才实现完整证据→策略→约束→候选→拒绝→等级→回执→审计链页面。404 才实现黄金链路业务按钮和一键 reset；控制台只能注入预定义真实模拟事件，不能直接指定最终卡片金额。401 不做收益比较推荐、用户消费评价、真实银行接入、调度执行或尚未验证的持仓自动接管。进入后续页面的占位项应标“待下一任务”，不能提供看似可用但跳转失效的解释按钮。

## 401 验收矩阵（以下全部待实现、待运行）

| 编号 | 准备与真实触发 | 用户可见断言与来源证据 |
|---|---|---|
| F01 卡片真值与同快照 | 隔离随机 bf_test 库、真实迁移、受信模拟来源；同时读取聚合与服务端 oracle | 现金/保护/目标现金与本金/已配置/下一义务各自口径相符，不重复计本金或目标；每个金额来自同一 RR/now；读前后全库快照相等 |
| F02 并发与阶段 | 在真实银行独立提交后、应用投影前暂停；再解除并原动作对账 | 首页先展示待对账/证据不足，投影后刷新到真实回执状态；不能拼出安全假快照；GET 不补资金或 audit 事件 |
| A 工资到账自动安排 | 工资先未到账、后通过受信模拟银行导入成为 BANK_CONFIRMED；既有已确认义务/生活/应急/目标范围；按原服务 prepare/execute 完成目标划转及许可申购 | 未到账时不增上限；到确认到账/分配/配置各步真实刷新，目标归属与已配置分别变化；为何未询问来自具体 AUTO 评估/原策略，金额与真实回执核对 |
| B 未来目标 | 既有 compile→首次用户确认→create_goal API；后续真实新增资金按已确认范围分配 | 候选未确认不冒充 ACTIVE/不动目标现金；首次确认仅建立策略/零归属目标，不认领旧余额；后续实际分配才增归属。401 只验首页状态，402 再验编辑/确认 UI |
| C 大额消费安全恢复 | 受信银行消费事实改变现金及独立银行来源；原授权无损恢复；另有损定存走 ASK_ONCE | 消费后真实风险/缺口，T0 恢复到账后现金与已配置变化；T1 受理/等待/到期只读未到账分开；有损请求显示需确认并不自动支取；不评价消费 |
| F03 不足/错误/空态 | 缺/坏/冲突/过期来源、null额度、真正零、无策略/目标/义务、离线/500/非法响应/超 JS 安全整数 | 不把null或缺证据写0，不把空策略当授权，读取错误不保留绿色当前结论；恢复后用户刷新看到完整新快照 |
| F04 UNKNOWN/幂等 | 实际投影 INSERT 失败保留银行提交与 UNKNOWN，原键恢复 | 首页保留待对账/原身份；仅一个银行经济效果与回执；GET轮询零写；已确认ASK不重复要求同一确认 |
| F05 解释可追溯 | 从当前金额打开边界解释，从真实 action_id 打开原轨迹 | 当前/历史时点与金额不混用；来源、策略、原因、回执存在性可核对；旧/未知/坏审计不能声称VALID；打开解释全库零写 |
| F06 真实 Edge 业务证据 | Playwright channel实际为msedge，经Web代理→真实FastAPI→隔离PG；真实业务状态，禁止route.fulfill写死业务响应 | 逐链路保留真实页面截图、网络响应/请求ID、run/action/receipt关联、before/after状态及源码/合同摘要；确认browser/channel版本。health仅单独工程证据，不能计入黄金链业务通过 |

若现有受信服务/测试准备入口不能合法产生工资到账或消费事实，401 必须明确补足可复用的银行一致测试 fixture/事件适配接缝，或将对应业务 UI 验收标未完成；不能以手工改最终余额、改 opening 分录、手写自主额度或另一套页面假数据冒充链路。测试驱动状态不要求提前实现404控制台，但必须经过实际金融来源校验与执行路径，不能改未来任务范围来掩盖无法验证。

实施顺序：先冻结最小聚合/卡片口径与现有生成合同接缝，真实 API RED/只读/并发验证；再首页组件、金额和状态测试；最后真实 Edge 三链路及异常链路。HTTP 夹具单元测试可以验证错误/样式/格式，不能替代真实业务 e2e。401 最终需要完整 check、实际业务证据、源码路径集合/hash、任务进度文档与本地提交；本预审不填写任何“已通过”的未来矩阵数字。

## 2026-10-04 只读交叉审查后的实施候选

以下为 `DESIGN_ONLY / NOT_IMPLEMENTED / NOT_VERIFIED`。304第二轮完整check仍运行，未启动401实现；进入401时须据这些候选冻结具体DTO/SQL/服务合同，并完成真实升级测试。本节汇总前述设计间待闭合的分歧，不把静态审查计作运行通过。

1. 外部事实采用一个 `external_bank_facts` 业务表及一个 `BANK_EXTERNAL_FACT` subject kind；银行原字段保持不可变，应用投影字段只允许明确迁移和一次结果填充。银行结算与应用投影仍是两个真实事务及事件。受信内部runner入口满足401链路准备，公开按钮留404。
2. 旧及后续command-origin posting保持明确18字段v1 codec；新增external-origin与清算posting使用v2。编码前核完整live行，旧origin必须NULL，新origin须真实同用户fact。旧原文/hash/锚不重算，既有不可变posting不另捕同ID的新NULL布局版本，避免SEALED重复身份。
3. 黄金fixture使用固定可信初始化包，在seed genesis前建立银行清算准备金及来源清单；清算opening明确v2，不能因origin=NULL选v1。已有epoch后首次建立则需要真实bootstrap事件，不借初始化入口消除LEGACY。准备金不是用户账户，不计现金卡或自主额度。
4. 当前黄金链的消费只涉及未预留、未归属的一般现金；追踪收入按稳定次序消费真正AVAILABLE，不动RESERVED/ASSIGNED位置，不将返本恢复为新工资资格。[消费投影审查](MVP-401-external-consumption-review.md)给出最小候选：核消费前完整原件与银行顺序，逐effect扣除income/claim/exposure重叠，以`U=B-G-ΣA-ΣI-R`证明未追踪一般现金；优先消费A，仅当余款可由U覆盖时整套投影。侵入预留/目标或原件映射不明则保存真实银行SETTLED＋projection UNKNOWN，原预留/目标/月贡献不改，GET不修复。原银行固定ECONOMIC两腿digest与后续收入归因memo digest分开，新memo不能修改已结算原件；现BankOperation完整腿合同保留。该候选未实现/未验证；不声称支持通用goal drawdown或pending-aware取消。
5. 新图归档明确覆盖20类业务实体；当前业务summary升级v3，金融seed版本仍v6。旧v2摘要仅保留为原时点证据，新列/新表后的完整当前摘要不冒称与旧v2逐字相同。seal counts继续表示实际保留snapshot数量，与live行数分列。
6. 审计外层audit-event-v1/canonical-v1保留；新外部事实payload明确版本2及对应registry/SQL分派。先升级兼容reader和数据库校验再启writer；旧PayloadV1默认字段/编码保留，新事实不制造Agent run/action/receipt。
7. exposure明确注册v3/v4；出现新外部事实、清算bootstrap或v2腿时要求完整v4来源与水位。旧v3 codec18字段保留且核新origin为NULL。新稳定ID的v4 Evidence替代旧来源，旧Evidence只合法SUPERSEDED，content/hash不改。
8. 首版保护分解、下一义务、目标归属共用完整边界证据门；账面账户与经过独立完整核验的配置可保留各自卡片状态。不能按私有issue名称猜局部资金安全；null、零和账面待对账状态各自表达。

303接缝的静态复核进一步确认：其inputs捕获Boundary/Execution DTO与Evidence，并未反射posting全行；受限JSON Evidence.content可承载明确v4协议。因此仅增加posting列/新银行原件不自动要求trace-v2。保持既有DTO、全部默认字段和历史run原文/hash；新事实只生成新run。现有回执校验仍需升级完整ledger和新origin关系，旧command腿必须明确没有外部origin。若最终实现新增顶层集合/phase或改变TraceEvidence/哈希合同，则另注册trace-v2，不能给旧v1 DTO随手加默认字段。

以上选择还需[审计升级金标准](MVP-401-audit-evolution-review.md)、[迁移审查](MVP-401-bank-fact-migration-review.md)及[三链手算oracle](MVP-401-golden-oracle-design.md)中的实际运行证据。普通消费越过归属/预留时的投影处理须在合同中明确，不因黄金数据避开该情形而宣称通用已支持。

## 首页金额显示的实际语言运行时反例

2026-10-04在当前Node v24.14.1实际执行了独立数值示例，原始输出见[evidence](evidence/MVP-401-money-display-counterexample.txt)。整数分 `9007199254740990` 确实通过 `Number.isSafeInteger`；但 `(cents/100).toFixed(2)` 输出 `90071992547409.91`，正确整数商/余数结果为 `90071992547409.90`，差1分。这是语言数值反例，不是已存在首页的缺陷或401组件测试通过，未改应用源码/合同/数据库。

401金额格式合同须在安全整数校验后用整数或BigInt拆分绝对值的元/分、补足两位并单独处理负号；分组格式化只作用于整数元，不再以浮点除100后舍入。覆盖null、真实0、负margin、普通金额、上界附近字面预期和非法/不安全值；不得通过字符串/布尔值的隐式转换得到金额。卡片总额和自主上限取同一服务端快照的已计算结果，不由浏览器加减卡片生成新金融结论。以上组件和API验收仍待401实际实现。
