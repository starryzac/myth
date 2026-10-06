# FULL-204 专用现金回拨候选家族

2026-10-06。新显式协议 `release-action-set-input-v1` / 算法 `full-policy-release-action-producers-v1`。原 actual-v2、v1、Full-v1、周期、Recovery、303/304 回拨数学、专用授权和金融执行历史保持原字节。本包只计算 `release_family_complete`，`full_global_adapter_installed=false`；不能据此关闭全局 FULL-204 或认定资金已执行。

## 实际入口与同请求绑定

`app.services.full_action_set_release_producers.capture_current_release_producers(session,user_id,now,*,original_actual_capture=None)` 返回 `ReleaseActionSetCapture(inputs,result,originals)`；`read_current_release_producers` 只返回结果。调用必须是 clean、REPEATABLE READ、READ ONLY 的同一请求 Session、实际 simulated User、唯一当前 OPEN epoch 和服务器业务时钟。默认 fresh 捕获原 actual-v2；可选 capture 仅供 Root 同一 RRRO invocation 复用，owner/epoch/as_of 严格一致，不能跨请求缓存或由公共客户端提交。所有授权、原 preview、现金来源、生命周期与保护读取仍从当前 Session 重新验证。

原 preview 没有导出 303 的 typed 输入。本新模块在同一 RRRO 内用原 Goal、reserved rows、实际 ownership、FullGoalModel、Core/Full 当前点重建 `ReallocationDecisionInput`，原独立整数数学与 preview.decision 完全相等才继续；不修改旧服务或将 UNKNOWN 的原 planning 当银行授权。原 365 输入重新从原 verified financial context + FullAnnual 来源形成，并严格匹配 preview/protection 的原 input/hash。客户端不传金额、clock、授权、银行事实或结果。

不调用 prepare/confirm/execute，不创建 Action、DecisionRun、Evidence、reservation、posting 或 receipt。readonly `_effect` 仅把原服务已验证的正金额候选转为原独立 `GoalReleaseBankCommand` 值对象，不向金融引擎提交，也不改旧 ExecutionEffect。

## 有限完整分母与来源

- 当前 epoch 全部 CrossGoalReallocationPolicy × 原配置列出的所有 Goal × 全部 owned CASH 账户，容量 64、完整 typed capture 16 MiB；未选、暂停、撤销、缺失 Goal 和缺版本均不被过滤。超容量 UNKNOWN。
- 所有 `FULL_GOAL_RELEASE_AUTHORIZATION` 原 Evidence，包括陈旧/已归档/损坏原件；每项原 trace/hash/request/source/owner/时间均核验。CURRENT 还从实际 Full 当前版本、全部 Goal 当前 MVP 版本与原双 hash FullModel/原请求 hash 重算。UNKNOWN 不等于无 scope；只有完整原件证明没有当前专用许可才 EXCLUDED。
- 所有 RELEASE_GOAL action 或专用 marker 原动作，逐个原 PREPARE trace/请求/command 验真。任何 SUBMITTED/UNKNOWN/RESERVED、缺 trace、无对应原 bank/receipt 或非终态保 unresolved；即使 Full 已暂停仍保留原责任。SETTLED 不能单凭状态排除，需完整三腿、原内部交易/Evidence、原逐动作确认与 receipt 验真。
- 原全用户 ActionPlan/BankOperation/Receipt/Postings 真实 count、原行 SHA、完整 release 分母，包括跨 FullPolicy 全版本使用。原 release command/postings 副本要与 actual-v2 原行逐项相同，不凭 VERIFIED 字符串。
- 原 ALLOCATE_GOAL command/原银行/receipt/COMMITTED uses 与所有 ASSIGNED fragments 独立核对；源 allocation×fragment 精确残余额重放。现金-only 可用，购买/赎回没有原 source split 时 UNKNOWN，不猜 FIFO，不将 ASSIGNED 转 AVAILABLE，不将释放记为新收入。
- 所有原 current Full 保护来源与 365×3=1098 点，原现金曲线、Goal cash/minimum、来源/目的账户、现金守恒和 margin 逐点非恶化。完整原 MVP snapshot/versions/positions/current产品、Full 当前版本/确认/source binding 均保留；未来资金不帮助今天的修复。

当前 INCLUDED 仅是已存在专用现金回拨执行接口的 **正最低修复金额、现金来源可精确归属、完整原专用许可、ASK_ONCE** 只读候选；仍需要新的 exact effect 用户确认，旧 consent 不继承为新动作确认。签名包含 RELEASE_GOAL、金额、来源 Goal/账户、保护目的、最低保障、条件、原 release uses、零本金/其他Goal/new-income/assigned-income/费用/损失和即时到账范围。不同经济动作不 shadow 原 allocation/transfer；`shadow_original_candidate_key=null`。

## Root 组合与冻结轨迹接缝

Domain `derive_release_producers(ReleaseActionSetInput)` 返回 `ReleaseActionSetResult`；Service `verify_release_source_copies(trace,inputs,result)` 核原完整 source 副本；`verify_frozen_release_producers(trace)` 只接受新 `algorithm_versions.release_action_producers`，EVALUATION、无 action、`inputs.release_action_set_input` 与精确 `outcome.release_action_set_result`，独立重算 typed 原件，不能旧白名单仅增加字符串。

仅 family 完整时返回 `handled_unsupported_codes = FULL_PRODUCER_ADAPTER_MISSING:CrossGoalReallocationPolicy:<原UUID>`。`original_actual_input_hash`、原 actual reasons/其他 unsupported 始终保留；其它 family、Root global compose/observe/notification 尚未接入本包，不由它自动宣称全局 COMPLETE。Root 后续新组合版本必须保原 v3/v4 input/hash 与新 family 原件，旧通知语义不变。

## 验证状态与边界

首 33 个 synthetic 直接风险 PASS 52.46s：`W3/release-producers-new-source-denominator-risks-20261006T033806Z-2b25239d`。随后 9 个 exact 授权来源差量 PASS 9.64s/33 deselected：`release-producers-new-exact-authorization-delta-20261006T034154Z-b700f91c`。这些是工具/模块合成风险，无真实 actor、资金效果或正式实验案例。

最终 49 个直接风险 PASS 66.24s：`release-producers-final-refusal-and-whole-original-risk-scope-20261006T035617Z-d593455f`。四个新源严格类型 PASS：`release-producers-final-four-types-and-static-20261006T035617Z-84f93e12`；该命令 scope 稳定，global 因其它 maturity/compose 测试开发变化为 false，不能称全仓冻结。Ruff 四源 PASS：`release-producers-final-four-ruff-20261006T035617Z-a83ef00b`；format 四源实际 check 通过。最终源码可运行，真实金融与全局组合仍待 Root 接入/实测。

最后一项风险明确区分：原 service `BLOCKED` 可以同时含缺银行原件，不能凭该标签将候选 EXCLUDED 或清除全集分母。本版这类原拒绝保留 `UNKNOWN / RELEASE_SERVICE_REFUSAL_NOT_INDEPENDENTLY_REPLAYED`，尚未把全部拒因独立冻结重算；只有当前完整来源证明无专用许可或明确 Full 禁用才有限 EXCLUDED。真实正 READY 必须完整源/原版本/原请求/原 caps/最低金额及 1098 点全部通过，任何未决旧责任仍阻止 family complete。

首次 strict 5 个局部注解/变量复用错误的原源已保存 `.runtime/FULL-204-release-producers/first-type-red-*`。后续原 strict 两夹具错误 FAILED 保留 `release-producers-four-strict-types-20261006T033825Z-c7184e7d`；不把中间 RED 改为成功。新增手算 helper 首类型错用了 GoalOwnership 不存在的 observed_at，原四源另存 `hand-protection-first-red-*`，原 FAILED `release-producers-final-four-strict-types-20261006T034459Z-28003a0f` 保留。

手算首 48 风险批为 42 PASS / 6 FAIL，`release-producers-final-exact-source-and-hand-protection-risks-20261006T034459Z-5625cc59`；其后两次六用例仍 FAIL，原件分别保留于 `hand-protection-coverage-red-20261006T035256Z`、`hand-protection-margin-red-20261006T035425Z` 及原 wrapper。真实保护门指出 GOAL 账户 500 分中，owned 300 之外的 unassigned 200 也必须完整登记并保留保护；原生产门、金额、种子、哈希不变。修正夹具后六用例 PASS 8.07s / 42 deselected：`release-producers-hand-margins-retain-unassigned-protection-20261006T035426Z-60f40d44`。手算 before/after 总现金均为 1000，保护 500→400、余量 500→600；未归属 200 分仍受保护，不当作新收入。

真实候选仅 collection：`test_full_action_set_release_producers_integration.py::test_actual_complete_release_family_original_scope_sources_365_and_zero_writes`。Root 专属 generated bf_test 中沿已有实际 Goal→income→ALLOCATE_GOAL→独立专用 scope 原入口建立前置，预期读取唯一 10000 ASK 候选、1098 原点、原银行/收入/全 physical 零写及 EXACT audit。**实际 PG 未运行**，不是成功证据。没有正式案例、真人、性能结论。

候选最终 collection 原命令：`release-producers-final-actual-root-candidate-collection-20261006T034931Z-6887c951`，一项收集 4.73s、wrapper PASSED（早于最后纯拒绝口径补强；实际测试文件未改，非金融成功）。Root 下一单链可执行该完整节点，不能以 collection 或 49 个合成风险代替它。

明确未覆盖：本金或 source split 未记录的 Goal 购买/赎回；缺当前原件/所有未决历史；需当前重建附加 periodic/seasonal/history runtime reference proof 的保护来源在未精确绑定前保持 UNKNOWN；其它 Joint/Recovery/资产未适配族；Root 新全局组合/通知接线；实际 PG/API/browser 及最终全量验收。不存在证据不补造金额或成功。
