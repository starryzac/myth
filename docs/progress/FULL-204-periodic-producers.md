# FULL-204 周期付款当前生产者的明确差量

2026-10-06。`FUNCTIONAL_ADAPTER_IMPLEMENTED / ROOT_COMPOSE_AND_ACTUAL_PG_PENDING`。新算法 `full-policy-periodic-action-producers-v1`，输入 `periodic-action-set-input-v1`；保留旧 v1/full-v1/actual-v2 输入、数学、hash、失败及银行历史。此包仅声明 `periodic_family_complete`，`full_global_adapter_installed=false`，不将有限家族完成说成全部自主动作集已完整。

## 可调用功能

新独立 `app.api.v1.full_action_set_payment_producers.router`：GET `/api/v1/boundary/periodic-action-producers/current`，固定当前模拟 owner/server business clock。客户端无财务 body、金额、授权、period、银行事实或 now；任何 query 拒绝，POST 不存在。Root 注册并将 GET 前缀放入 RR READ ONLY。

`capture_current_periodic_payment_producers(session,user_id,now,*,original_actual_capture=None)` 返回 `PeriodicActionSetCapture(inputs,result,originals)`；`read_current_periodic_payment_producers` 返回结果。默认 fresh 调原 `capture_actual_action_set`，同一 clean RRRO Session 核当前 User 与唯一 OPEN epoch，逐调用 SQL；可选原 capture 只供同一次已验证调用复用，不能从公开请求接受或跨请求保存。完整原实际 input 原封嵌入，所有原来源/audit/count/owner/clock/source-denominator 问题保留。新的关系读取与候选仍 fresh，不缓存权限。

全 user 原 `FULL_PAYMENT_RELATION` SOURCE 原行（含历史/不活跃行）都保留 count/ID/hash 分母并严格解析；不是仅 SQL 过滤当前 CONFIRM 后断言没有关系。每当前 OPEN epoch 的 `PeriodicTransferPolicy` 逐项读当前原 Full version/config/confirmation/window/evidence/hash。当前版本全部原 CONFIRM 及每个 START 祖先都调用真实606 `_original` 与 `get_decision_trace`，须 COMPLETE/audit VALID。保存完整 Receipt、TypedTrace 和 SOURCE 副本；新 helper 逐项重新验原 scope、body/hash、Actor USER、evidence UUID、原 policy/config、祖先与源副本，不凭 CURRENT/VALID 字符串成功。

原 actual-v2 的 generic `PaymentIntent` 没有 natural-month period；原付款规划器要求精确当月和真实本期已付观察。因此新 adapter 按真实已确认606 scope.timezone 的当前本地自然月构建原 PaymentIntent，调用原 `_facts`、财务 revalidation/自治与完整 FULL protection；传原365/1098保护、future account typed bounds、全部原income/claim/source。不改旧 v2 生产者。缺本期已付证明、范围最终总额、效果、上下文或保护仍 UNKNOWN，不能猜零付款/余额。

完整专用当前 scope 必须与实际 Full/MVP config（payee/amount rule/due day/auto/source account/单笔 cap）及有效交集精确一致；原银行 payee、账户引用和当前 source hashes 必须可验。金额由旧原引擎计算，不自选新金额。整体财务/授权否决仍独立，已完整但效果超 dedicated scope 明确 EXCLUDED。ASK 表示需要该新动作确切 USER 确认，不能继承旧 action confirmation。原候选上下文中有确认即 UNKNOWN。

确实完整且无当前 dedicated relation，返回 `EXCLUDED / NO_CURRENT_DEDICATED_RELATION`；不能由过滤、缺源、缺 START 或伪 STALE 得出空集。原关联 SUBMITTED/UNKNOWN、任何 RESERVED claim、未终局 BankOperation、SETTLED 缺原 receipt 都保留原 action IDs 和 UNKNOWN，即使 Full 关系已暂停；不释放 claim、不撤销 bank、不写修复。来源关系通过原明确 Action FK/Full payment binding 关联，不搜索任意UUID文字。

每个当前 Full producer 都保独立分母。同MVP候选只在完整且唯一映射时返回 `shadow_original_candidate_key`；多个Full同原候选或同Full多MVP映射 UNKNOWN。候选签名沿原 `candidate_view` 完整 action signature；父组合不能再把 nominal payment 与新 Full scope 重复计成两个机会。

## 冻结轨迹与 Root 新组合接缝

纯 `derive_periodic_payment_producers(input)` 返回 `PeriodicActionSetResult`。结果包含全 Full policy 与 relation SOURCE 分母、每个原命令/当前确认、原 unresolved IDs、shadow、原 actual input hash、新 input/result hash。`original_actual_reasons` 原封保留；`remaining_unsupported_producers` 仅在家族完整且唯一 scope 已实算时列真正未处理家族，不是全局 COMPLETE。604 Recovery、专用 GoalRelease、联合 Goal 等仍需独立当前完整 producer adapter。

`verify_frozen_periodic_payment_producers(trace)` 只接受 `algorithm_versions.periodic_action_producers=full-policy-periodic-action-producers-v1`、EVALUATION、无 action_id；`inputs.periodic_action_set_input` 和 `outcome.periodic_action_set_result` 必须逐字段重算相等。复用原 `_verify_original_source_copies` 核完整来源/政策副本，额外核每个 relation 原 Trace 与财务/授权来源。旧 global v2 算法不能消费此输入。此 helper 不持久任何轨迹。

Root 新 `full_action_set_boundary_composed` 应调用本 capture/纯 derive，逐字段验证同一 `original_actual_input`，在完整且 unique shadow 时替换原相应付款候选，再重新计算全分母/UNKNOWN/unsupported/signature。Root 注册新整体算法、trace及父入口；本包没有修改 Main/deps/旧 global observer/通知或任何 shared finance。只 READ 模块不 append Action/receipt/confirmation/DecisionRun/Evidence/审计。

## 必要检查与原失败

实际结果将在源冻结后追加。当前已取得 corrected 首批18直接风险 PASS/38.28s；这是 synthetic risk，不是金融/真人/正式实验。首18FAIL来自新 fixture 错把 user_id 传入原 FullPolicyView（该既有DTO没有此字段），首8typeRED含同一误假设与局部收窄。原源与正式输出保留 `.runtime/FULL-204-periodic-producers/first-failed-source-20261006T0212Z/` 及 W3 `periodic-action-producers-first-*`，没有改原DTO/负例。

后续加固 CURRENT 原状态实核、scope窗口、Full完整原字段、relation source map、inflight暂停责任和 frozen source copies；`source-denominator-types-7dd2f77b` 原2RED为局部 `starts` 集合/时窗变量重用，保存后只重命名，不改数学。相关 final 结果以末尾实测附录为准。

## 实际候选及未覆盖

新唯一候选 `test_full_action_set_payment_producers_integration.py::test_actual_current_periodic_producer_confirmation_denominator_and_zero_writes`：生成 bf_test，已有真实 MVP/Full 周期确认与原 simulator paid-history 观察、真实本地签名 USER START/CONFIRM→原全 SOURCE 分母→服务器100分付款 AUTO候选→全 physical 零写/原 audit VALID。尚不 prepare 或付款，不称其为606经济重验。Root 接新只读路由后统一单链执行；本代理仅 collection，不 PG/browser/Docker/全量。

未覆盖：完整全局 observe/通知/Root compose 实际链、604/Release/Joint producer 家族、已有旧协议所有 nominal recurring producer 的 period 修复、已全部付款/尚未来到due但原 effect 无法完整生成的独立确定性零机会解释（当前UNKNOWN）、超64生产者/4096表行/16MiB输入/旧Trace容量、真实新性能与最终67项验收。旧资金路径/原hash/任何失败证据没有回退或重写。

## 最终实际检查附录

- `W3/periodic-action-producers-source-denominator-direct-20261006T021857Z-e12cd56a`：21 synthetic 风险 PASS/36.88s，exit0、scoped stable=true、all_source_stable=false（独立 Root 源并行变动，不称全仓冻结）。随后仅局部时窗变量重命名和 malformed 原 scope 的明确 ValueError/UNKNOWN 处理；其它20个已过风险无行为变化，不重复整套。
- `W3/periodic-action-producers-final-malformed-source-risk-20261006T022208Z-d0f79c2c`：直接受影响的 service/source 原件风险 1 PASS/6.15s，包含完整原 SOURCE、scope保留、缺 original trace 和真正 malformed scope 不得 false-empty/崩溃；scoped stable=true、global=false。
- `W3/periodic-action-producers-final-types-20261006T022207Z-47f2bc96`：当前5源 strict mypy PASS；scoped stable=true、global=false。
- `W3/periodic-action-producers-final-static-20261006T022207Z-37b50b8f`：当前5源 Ruff PASS，all/scoped stable=true；owned5 format已经完成。
- `W3/periodic-action-producers-actual-collection-only-20261006T022208Z-dc1b7d9e`：唯一实际候选1 collected/5.08s，exit0、scoped stable=true、global=false；**actual PG NOT_RUN**，collection不执行生成库/seed/金融。

首18FAIL/8typeRED及后2typeRED均保存源和原 FAILED manifest；最终 PASS 不改旧状态。本包没有查询环境秘密、连接 PG/正式库、运行浏览器或改变银行、旧global、已确认策略和历史哈希。原编号最终关闭、global-v3注册/实际合并与通知仍由 Root 后续证据决定。
