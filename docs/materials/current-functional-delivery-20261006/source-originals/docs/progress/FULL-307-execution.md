# FULL-307 原已确认范围内的动态储备执行消费者

状态：新增域、实际来源服务、严格公开 API 与直接风险检查已实现；共享执行/银行/历史接缝由主协调器集成，实际金融 PG 候选未运行。本附件扩展原 FULL-307 只读节奏能力，不覆盖或改写原文档及失败证据，FULL-307 尚未关闭。

## 交付边界

只允许原明确确认的 GoalSaving 月度 `[min,target,max]` 中计算动态追加额。金额可以超过 nominal target，但不得超过当月 max 剩余额、目标剩余额、完整原保护余量或当前已核新收入。已确认 FULL 模型须与实际 Goal、原 MVP 版本和双 hash 确认完全对应；FULL 模型本身不提供银行许可。

同调用读取实际 OPEN epoch、当前 Goal/MVP/FullModel、实际银行投影、原生 v2 收入来源与完整 fragment、当月实际已贡献、Goal 归属、全部其他 claims、365 原保护及 1098 个 FULL 硬点。缺原件、缺完整分母、来源漂移为 UNKNOWN 或明确拒绝，不能猜零。旧收入、principal、ASSIGNED/其他动作预留、未来收入不变成新可分配来源。只可恢复本动作已验真 RESERVED 的原 uses；银行已提交 COMMITTED 只能协调原键，不能重新受理。

OVERDUE_READY、PARTIAL 小于原 minimum、原 deadline/有效期已过均不允许新执行。旧 ownership、资金源、权限、期限、最低额、确认和银行完整保护否决仍独立存在。新 proof 只替换原 nominal 单一上限的有限判断；它不替换原 permission 或 receipt 验真。固定原 effect.amount/uses 永不根据 fresh 建议扩大或改源。

## HTTP 接口

- `POST /api/v1/dynamic-goal-actions/preview`：严格六字段 `goal_id/expected_policy_version_id/expected_model_evidence_id/expected_model_evidence_hash/expected_epoch_id/idempotency_key`。UUID 使用原 UUIDReference，key 非空且不超过 120；extra/query override 全拒绝。预览原金额/范围/原因/hash，`bank_authority=false/grants_authority=false/preview_only=true`，内部全输入不对 HTTP 展开。
- `POST /api/v1/dynamic-goal-actions/prepare`：同六字段，金额、clock、角色、收入原件、permission 不能来自客户端。只调用原 `prepare_action` 的显式 typed 私有接缝；未安装时拒绝 `DYNAMIC_GOAL_EXECUTION_NOT_IMPLEMENTED`，没有 legacy 或实验候选 fallback。返回原 ActionResponse。
- `GET /api/v1/dynamic-goal-actions/by-key/{idempotency_key}`：固定 owner、RRRO、无 query。NOT_FOUND 为非终局，不允许据此清待定或换键。RECORDED 返回原六字段、其 `client_request_hash`、完整 `original_action_request`、独立 `server_request_hash`、原 ActionResponse、原 epoch 状态及真实原确认。两种 request hash 分母不同，不能冒客户端重算 server envelope。

原 ASK_ONCE 仍调用 `/api/v1/actions/{id}/confirm` 的 strict `accepted=true/effect_hash`；原 AUTO 仍依赖当前实际原 Goal 权限。执行/原键恢复仍使用 `/api/v1/actions/{id}/execute` 空 body，不新建银行键。原 GET action/receipt/decision 保持原合同；GET 或 AUTHORIZED 字符串不能自动证明用户同意。

Lookup 的 `confirmation: ConfirmationGrant|null` 通过旧 `read_execution_confirmation` 在实际原 Evidence.observed_at 核验。`confirmation_status=VERIFIED_AT_CONFIRMATION` 仅说明这次历史确认；已过期也不会变成当前权限，`current_authority/confirmation_is_current_authority=false` 始终保留。ASK 已 AUTHORIZED 但缺 Evidence 显示 MISSING。实际原件被篡改即拒绝；SEALED 历史只在当前仍可实核原 Action/PREPARE/audit/Evidence 时返回，不把缺当前原件包装成已核归档。

## 主协调器精确接缝

1. `produce_full_dynamic_goal_effect(engine, locked_session, user_id, action_id, body, now) -> (ExecutionEffect, FullDynamicGoalProof, marker)`：原用户锁及原决策 capture 下调用；另一个实际 RRRO connection fresh 读源，返回原 ALLOCATE_GOAL effect，并将完整私有输入/原 proof/source refs 注册到本阶段 capture。marker 保 canonical 完整 typed request、request_hash、effect_hash 与 PREPARE 原 proof。
2. `dynamic_goal_bank_key(key)`：`action:full-dynamic-goal:` 加原 key 对象 SHA。原普通 action 键和 effect/hash 协议不变。
3. `verify_full_dynamic_goal_prepare_replay(session,user_id,action,body,now)`：核原完整 typed body、原 Action/PREPARE/source/hash，六字段任一同键变化拒绝。不能只比较原 GoalIntent。
4. `has_full_dynamic_goal_binding(session,action)`：marker OR 新 key OR 原 DecisionRun/PREPARE 中真实 algorithm/完整 request/planning 关联。删 marker 并改 key 仍会进入严格门；缺原目标主决策或改变其 user/action/hash 拒绝，不能降级为 legacy。
5. `read_original_dynamic_goal_request(session,user_id,action,command,now)`：实际原 Action/get_decision_trace COMPLETE + audit VALID + frozen 全来源 +原 BankCommand +完整 action_request +原 proof +所有 Action 字段一致后返回原六字段。
6. `recheck_full_dynamic_goal_proof(engine,locked_session,action,command,now,own_action_id=None)`：用户锁下 phase1/确认及独立银行当前来源验证 fresh RRRO 调用；只验证原固定金额/uses，并录制本阶段完整新输入/proof/source。`own_action_id` 只能为空或本 action。调用者应将完整原 action_request 放入新协议 trace；该函数不改原 PREPARE marker/proof。
7. `read_current_full_dynamic_goal_proof(session,user_id,action,command,now,own_action_id=None)`：同当前实际 RRRO Session 的只读自治/动作评估；不授予执行。缺源抛原明确拒绝，计算失败保留 BLOCKED/UNKNOWN。
8. `read_frozen_full_dynamic_goal_proof(trace)`：新算法历史纯验真分支；核完整 typed trace、实际 source 原件/hash/owner/validwindow、原 model/native v2 income、完整原 MVP PolicyVersion 分母、原 context/effect/action_request 后重算。它不构造当前 grant；调用者另外核实际 audit/银行/receipt。旧算法不得进入此新分支。
9. `validate_full_dynamic_goal_proof(effect,context,proof)`：纯消费者校验当前 context/effect hash，再从完整 typed actual inputs 重算，不接受一个 caller numeric cap。内部 inputs 被 compact proof/HTTP 序列化排除，但原 trace 必须完整保存。

原 PREPARE 必须在 `income_evidence` 加入完整 Action.request 后保存新 top-level action_request；只有新算法分支增加该字段，旧输入/hash 不改。共享消费分母包括 prepare、confirm、RESERVE/execute、`execution_sources.verify_execution_sources` 的当前独立 bank source 验证、bank 首次接受、自治两处与动作事件读接口。`execution_sources` 是当前 Bank 验证，不是历史 replay；不能只在其之后的 bank guard 提供 proof。历史 decision_trace/audit/signature 要使用 frozen 新分支。已 SETTLED 原键只验原经济身份/legs/receipt 并恢复投影，当前 revoke 不重新授权或重扣。

## 直接检查与原失败

最终新增域/API/历史/原键检查：35 PASS / 16.59 秒，`docs/progress/evidence/W4/dynamic-goal-original-key-five-set-direct-pure-20261005T224521Z-9f3f36ff/manifest.json`。这是 synthetic/服务 double 与真实 typed 验证函数的直接检查，绝非金融执行证据。覆盖超 nominal 但不超 max、同月重复/current cap 缩小、保留原 uses、精确 own reservation、其他 Goal/claims 不借、原版本/模型/hash/期限/min/source 缺口、数字 proof 篡改、marker+key 剥离、原键 owner、confirm 真实 Evidence 缺失/篡改、历史确认不变当前权限和 strict JSON/query。

最终七文件 strict mypy PASS：`dynamic-goal-original-key-seven-strict-types-20261005T224521Z-ff2e2aba`。上述两次实际 wrapper 均 PASSED/exit0，scoped source stable=true。types 的 all source stable=true；35 纯检查的 all source stable=false，唯一全局变化为同期 Root `test_full_dynamic_goal_pipeline_seams.py` 和独立前端 `full-dynamic-goal-execution.ts`，不得改称全仓冻结。只证明各自命令范围，不是完整验收。

最终 Ruff 七文件 PASS：`dynamic-goal-final-seven-static-20261005T224746Z-37d9a6b1`；format 七文件 PASS：`dynamic-goal-final-seven-format-20261005T224746Z-35cc9ac8`；唯一 PG 候选仅 collection 1、PASSED/exit0：`dynamic-goal-original-key-actual-candidate-collection-20261005T224747Z-4c92b2c2`。三 wrapper 的原 manifest 与日志均保留；只收集候选并未调用数据库或资金服务。

此前相关旧节奏/目标加新域/API 80 PASS / 13.44 秒（`dynamic-goal-related-pure-20261005T222618Z-789d9987`）属于此前明确源码版本，不能冒充最终新增历史/lookup 同源 80 验证。历史窄修 5 PASS / 4.20 秒为 `dynamic-goal-corrected-history-five-20261005T223603Z-1dc11ec6`。首次 enum 夹具 1 FAIL/23 PASS、首次 strict 13 错、一次 API import strict 错、历史+JSON 5 FAIL/25 PASS、三项 TracePolicy/AuditVerification 类型错全部原样保留；出处为 `.runtime/FULL-307/first-pure-failed-20261005T2214Z`、`.runtime/FULL-307/history-contract-first-failed` 及相应 evidence manifests，不重标成功。早期无 wrapper 的首纯结果只保存实际 tool-return 注记，未伪造 raw log。

## 唯一实际 PG 候选与未覆盖

Root 单链候选：`app/tests/test_full_dynamic_goal_execution_integration.py::test_actual_dynamic_above_nominal_original_income_and_response_loss_key_recovery`。它在新 generated test DB 真实确认原 Full/Goal 模型并原 external income 入账，预览 20000 > nominal 10000 且 <=原 max 20000，全部物理表只读，实际原 prepare/完整同键冲突/lookup/所需 ASK 确认后 DROP_BANK_RESPONSE；随后核原唯一 SETTLED 银行键与 no receipt、原策略撤销后同键真实恢复、真实 income ASSIGNED/AVAILABLE、EXACT audit VALID 及 replay 零写。当前仅 collection，实际 NOT_RUN；任何 assert 不计成功。

还未覆盖：Root 共享全部消费者安装后的实际金融链、真实原响应丢失和撤销协调、银行 fresh own reservation 与完整 FULL future-account 否决、完整多目标联合动态调度/多期执行、原生 v1 收入兼容、OVERDUE 或低于原 minimum 的执行、真实前端与最终初版/完整版验收。没有新增授权缓存、LLM 金融事实、补造 bank grant 或真实资金接口。

## 2026-10-06 06:52 Root 原执行接缝交付

新增私有 typed request 只用于新动态目标 API，真实 USER 锁内 produce 原固定 effect。旧五位置 `_build_effect` 和原实验默认不变；不混实验接缝。新 key 重放核完整六字段 body、原 PREPARE/Action/effect；同 key 改 model/hash/epoch 不复用旧结果。

准备/确认/RESERVE/独立银行首次受理均重新读取 actual RRRO 证明并保原 MVP 权限、收入/Goal/账户/预留和 Full 365 保护。银行 source verifier 返回本次局部证明，后续原 FullProtection 必須核完全相同 effect/context，跨请求不缓存。已有 SETTLED 同键恢复走原银行回执/投影路径，不追加新授权。

仅新 algorithm capture 保存完整本阶段 actual inputs/proof 和 complete action_request；新 PREPARE 在原 income_evidence 加入后录制。旧算法默认 input/canonical/hash 不加入字段。当前Autonomy同RRRO重读；历史/Boundary signature只从原新协议冻结数据复算，不授新权限；两个 supported 集合仅追加新算法名称。实际 Main 注册 preview/prepare/by-key，GET 和精确preview用原RRRO。

实际直接检查 100 PASS/10.35s：5新consumer/history风险及95原Execution/BoundaryEvents/DecisionTrace相关用例，有重叠不与旧批相加。新consumer允许150000原确认max而原路径仍BLOCKED，重算/clock/完整effect/cap tamper拒绝，实际 `_stored_trace` 拒绝重哈希结果篡改（Session替身，非PG）。原首两夹具RED保留：第一次错改不存在context字段；第二次金额单改正确触发原cash schema；仅修真实snapshot时钟和完整cash/income金额，不改生产门。原件 .runtime/root-dynamic-consumer-clock-fixture-first-red-20261005T2247Z 与 effect-fixture-second-red-20261005T2248Z。

原命令 W3/dynamic-goal-original-consumer-history-valid-effect-20261005T224742Z-37b03ba5；12源strict final-20261005T225009Z-59816dd0、format final-20261005T225010Z-c553501c，原11源static-20261005T224557Z-5e934303及新test static-20261005T225058Z-5992e446 PASS。精确12源 FINAL .runtime/root-dynamic-execution-shared-final-20261005T2252Z/manifest.json。OpenAPI生成实际child exit0、wrapper SOURCE_CHANGED（输出预期变动）；独立 --check待终态核对。真实PG/浏览器尚未运行，FULL307仍PENDING，未以pure或旧read-only通过冒充完整执行验收。

实际schema独立核对终态 W6/actual-registered-dynamic-goal-execution-openapi-check-20261005T225102Z-2da0010e PASSED/exit0，当前实际API与合同一致，生成时SOURCE_CHANGED记录保留。307真实金融节点尚NOT_RUN；此前混合批在606首节点停止，不继承其后node成功。


## 2026-10-06 08:26 Root 接缝与实际证据

2026-10-06 08:26 北京时间：604 前端41直接风险/API8源和Root真实当前策略宿主均已交付；原pending与PLANNED/UNKNOWN工作区跨页阻新写/演示reset/登出，列表读取失败仍有独立原GET。Root最初18相关PASS9.80s，纯账户摘要夹具types FAILED2原件留存；窄补实际DTO后受影响5PASS8.63s、整体Webtypes b9f79960和5文件lint7c79cf22 PASS。Root五源FINAL 4bb74a52，708集中样式和房租窄屏说明已FINAL55288bc4，实际CSS/Vite build通过；Root跳导航不改hash、路由改变focus当前内容三case内已核；真实手机/键盘/屏幕阅读器/对比度仍NOT_RUN。

0014实际migration首命名约定重复prefixERROR8.47s保留，两个drop仅op.f窄修后actual1PASS11.26s，FINAL683e4ef7；旧0012/0013/已有行/哈希不改。204v1 actual原节点FAILED13s，后诊断FAILED16.34/14.72s均留；根因实际无 simulated_bank_ledger_heads 表、证据200行截断漏refs、512KiB不足。v1/已冻结Full-v1继续UNKNOWN，不能造表别名或提高旧版本容量改历史。独立显式 actual-v2使用真实表/全counts/现原schema type进行修订，307+真实asset family/math接新版本，原FAIL不改。后Full-v1与HTTP自动通知节点仍NOT_RUN。

307 actual原preview409 DYNAMIC_GOAL_V2_LEDGER_REQUIRED，FAILED12.91s；实际诊断FAILED13.49s证明原nativeV2唯一差异as_of `+00:00`与typed输出`Z`，同一时点。Root仅改新动态消费者及新历史验证接缝为严格原生IncomeLedger解析后全值相等；原JSON、原source hash、所有整数/源身份/归属/预约/权限/clock门和旧nativeV1拒绝不改，不写规范化原件。新增7反例和原35direct/4strict正在跑；准备后的真实银行/原键恢复仍未得结果，105/604后两节点NOT_RUN。当前无Root金融RUNNING，唯一shared财务负责人仍Root；新v2/102/材料独立并行。正式关闭21/92/FULL原项PENDING、真人0/NOT_STARTED、新性能NOT_MEASURED，最后集中验收尚待。

