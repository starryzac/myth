# MVP-304 最小挂钩设计（只读预审）

状态：`PREFLIGHT_ONLY`，2026-10-04；303 完整 check/提交仍由 root 控制。本次只新增本文并给前一预审补交叉链接，未修改源码、迁移、合同或测试，未启动测试、服务或资金流程。事件协议是待 root 冻结的实施建议，不是已实现结果。

本文收敛 [事件与事务预审](MVP-304-event-map-preflight.md) 的备选事件名。路径统一相对仓库根目录；函数顺序按当前源码核对，行号可能随 303 最终提交变化。

## 1. 必要事件与不必另造的事件

初版 MVP-304/8.6 与完整计划 13.1 要求可验证的真实决策—授权—请求—执行—回执链。建议采用以下最小事件集合；只有真实首次事实或状态 transition 才追加，不以 HTTP 调用次数计数。

| 最小事件 | 需要它的原因与合并边界 |
| --- | --- |
| `DECISION_RECORDED` | 锚定每个实际冻结的 303 run/schema/input hash/trace hash，包括 EVALUATION 和全部真实 phase；不重复一份算法实现 |
| `ACTION_CREATED` | 锚定完整原 ActionPlan/request hash、类型、原自主等级和初始状态；301 准备的最终 request 在 PREPARE 后仍可能补 income evidence，不能过早散列 |
| `ACTION_STATE_CHANGED` | 统一记录 AUTHORIZED、SUBMITTED、UNKNOWN、INVALIDATED 的真实应用 transition；confirmation proof、claims 或 authority cause 随该 transition 绑定 |
| `BANK_ACCEPTED` | 原银行请求首次实际受理；BANK_ACCEPT 决策 trace 只是受理重验，不能替代实际 operation 创建 |
| `BANK_SETTLED` | 原 operation 真正落完整守恒 legs；支持 T1 后续结算，以及应用 UNKNOWN 时仍可确认银行已提交 |
| `ACTION_PROJECTED` | 一次记录完整 receipt、transactions/evidence、posting set、声明消费和 action→SUCCEEDED；不再另造 RECEIPT_CREATED、ACTION_SUCCEEDED、RECONCILED 三个同效果事件 |
| `RECOVERY_OBSERVED` | 205 的首次 ACCEPTED 等待投影、真实失败观察、恢复 run 的实际状态改变；不把 205 尚为 SUBMITTED 的 action 编造成 301 UNKNOWN transition |
| `POLICY_VERSION_CONFIRMED` | 合并首次策略确认与用户确认修改，记录 previous version、new version/config hash、原人工 evidence、授权范围与原因 |
| `POLICY_STATE_CHANGED` | 实际持久化 ACTIVE/CONFIRMED/EXPIRED/SUSPENDED/REVOKED transition；不是 GET 计算的 effective_status |
| `GOAL_INITIALIZED` | 用户显式创建零目标及其独立 opening、ownership/contribution proofs；不是资金转账 |

若采用封存换 epoch 的演示 reset，另有 `EPOCH_STARTED/DEMO_RESET` 与 old-epoch seal 等审计基础设施事实，须与受限 reset 机制一起冻结。

不单独追加 RECOVERY_PLANNED（已有 RECOVERY_PLAN trace）、CONTRACT_MATURED_DECISION（已有 CONTRACT_SETTLEMENT trace）、每一条 posting（同 settlement 的完整 set）、每个 proof supersede、候选/LLM 输出、只读 preview、访问日志或纯重试。不要把 `DecisionRun.status=SUCCEEDED` 当成资金成功；303 它可以只表示评估已经计算完成。

## 2. 统一写入行为

建议一个受限 `append_event(session, ...)` 接缝，接收**当前原业务 Session**，不自行 commit、rollback、开新 Session、刷新策略、调用银行或补决策。事件与其所属原业务事务共成败。helper 规范化/hash/tenant/link 校验与受保护 head 的设计见前一文档第 4–6 节。

append key 采用命名空间加短 digest，不超过现表 160 字符。它绑定 event type、epoch、同用户不可变 subject 和必要的真实 transition/cause。调用路径在业务幂等 early-return 前后要区分：已有原业务事实返回时不得“补当年的事件”；仅新提交事实的分支调用 append。append helper 再提供相同 key/相同原语义返回原事件、改变原语义冲突的第二层保护。

事件 payload 保存首次可信经济/观察时点与冻结内容，重试先读取已存在事件再核原稳定语义，不能把新的 now/proof ID 拼进去覆盖原 payload。ACTION_CREATED/settlement/receipt 不按当前时钟生键；真正新的重新重验 run 按 run ID 锚定。causation 只能指向本用户已有较早事件，无法可靠定位原因时用 nullable causation 和已校验的 cause references，不能引用未来将写入的事件。

## 3. 集中的决策挂钩

文件 `apps/api/app/services/decision_trace.py`，函数 `record_trace`：

1. 保留 `verify_trace`、关系/来源/约束校验及既有冻结 payload 的 early-return。
2. 新 trace 写入/合并 DecisionRun，更新冻结 indexes/snapshot hash，插全部 DecisionConstraints。
3. **在末次 `session.flush()` 后、最后 `return row` 前**追加 `DECISION_RECORDED`，key 为该 epoch/user/run ID。绑定原 phase/as_of/schema/input hash/trace hash、parent/action IDs、算法版本和完整来源/策略 hash references。
4. 既有 `decision_trace` 分支的 `return row` 不追加。无审计的旧 trace 不是正常 retry 时可冒充的新历史。

它自然覆盖 `decision_assessment.save_assessment`、`decision_recording.record_execution_trace`、`record_recovery_plan`、`record_recovery_bank_acceptance`。这些 wrapper 不再另记同一 decision。`record_execution_trace` 的非 PREPARE run ID 含 now/capture digest；银行尚无 operation 时重新实际重验可能产生另一个 RESERVE run。这是新的计算记录，按 run 去重，不能按 action+phase 一概丢掉。银行已存在时现流程跳过重验，也不得审计层主动再录 phase。

## 4. 301 执行动作的精确顺序

| 路径与函数 | 新事件的准确位置／顺序 | 幂等及事务边界 |
| --- | --- | --- |
| `apps/api/app/services/execution.py::prepare_action` | 原应用 `Session.begin`/用户锁内：创建 run/action→PREPARE（集中 decision hook）→`_epochs`→可能补 `income_evidence` 并更新 request hash→`refresh_execution_exposure`→flush 后 `ACTION_CREATED`→`get_action` | 原 key 同 intent 的 early-return 不追加；最终完整 request 才锚定。ActionCreated 的初态 PLANNED，无银行效果 |
| 同文件 `confirm_action` | 原应用事务：读旧 action/原 effect→新确认 Evidence/flush→新上下文重验 READY→CONFIRM decision→action AUTHORIZED→epochs/exposure→flush 后 `ACTION_STATE_CHANGED`，cause=原 confirmation ID/hash | 前 status 先保存；只有真实状态变化追加。原确认 early-return 无事件。任何重验/get_action/最终 commit 失败，人工 proof、decision、transition 全回滚。无需第二个 ACTION_CONFIRMED 事件 |
| 同文件 `execute_action`，第一事务 | 无原 bank operation 分支：RESERVE decision→`reserve_resources`→必要的 `reserve_income_for_action`→action SUBMITTED→epochs/exposure→flush 后真实 `ACTION_STATE_CHANGED` | 保存 before/after，payload 绑定此次 RESERVE run 与真实 claims。已 SUBMITTED 的再次重验只保留新的 decision，不重复 submission transition；UNKNOWN→SUBMITTED 则是同动作的真实恢复 transition，仍不是新资金操作 |
| `apps/api/app/services/execution_bank.py::process_operation` | 独立银行事务：原 action/request 校验→无 operation 时 `_validate_new`（内含 fresh BANK_ACCEPT decision）→business collision/时限校验→创建 ACCEPTED BankOperation/flush→`BANK_ACCEPTED`→到期才 `_settle_operation` | 受理 key=原 operation ID；已有原 operation 分支不追加受理。不能在 `_validate_new` 成功但 operation 尚未建成时记实际受理 |
| 同文件 `_settle_operation` | 完整 cash/principal/payee/fee/loss/goal/income/liability legs→经济守恒检查→status SETTLED/settled_at→末次 flush 后 `BANK_SETTLED` | key=原 operation ID；payload 完整实际非 OPENING posting set 与 ledger dimensions/hash，opening/head 另作原锚点 references，不当作本次结算腿。仅调用方 ACCEPTED→SETTLED 分支到这里；T0 顺序 decision→accepted→settled，T1 下次只新增 settled |
| `apps/api/app/services/execution_projection.py::_project_execution` | `project_execution` 的原 savepoint 内：原 identity/legs→无 receipt 分支所有 account/goal/position/income/transaction/evidence 投影→新 receipt→action SUCCEEDED→claim CONSUMED→exposure→末次 flush 后 `ACTION_PROJECTED` | key=原 receipt ID；有合法 receipt 的 early-return 与 ACCEPTED/None 分支不追加。receipt/差分事件随 savepoint 及外层第三事务回滚；银行独立事件仍在 |
| `execution.py::_mark_unknown` | 原独立应用事务：保留 success early-return；保存 before→真实 UNKNOWN→exposure/flush 后 `ACTION_STATE_CHANGED` | before 已 UNKNOWN 时不重复 transition；cause 描述 bank response/projection observation，不据此写银行 REJECTED 或释放 claims |
| `execution.py::_record_bank_refusal` | 原独立应用事务：锁用户→核原 operation/非 OPENING legs→保存 before→可靠 absence/rejection 才 INVALIDATED/释放声明/epochs，否则 UNKNOWN→exposure/flush→真实 transition 事件 | 同 key 重放不重复；payload 保存可靠 no-effect 分类与实际释放 claim IDs。若没有真实状态改变但确有尚未释放声明被首次释放，需一条明确的释放差分事件，不能漏变化或假写状态改变 |

最后一行的声明差分可仍放统一 ACTION_STATE_CHANGED 协议中，以 `transition_kind=RESOURCE_RESOLUTION` 描述，明确 before/after action 相同；它不是纯 no-op。这一边界需要真实 claims diff 判定，不能仅看 action.status。

## 5. 205 恢复与原合同到期

| 路径与函数 | 新事件的准确位置／顺序 | 幂等及事务边界 |
| --- | --- | --- |
| `apps/api/app/services/recovery.py::run_recovery`，第一事务 | 首次 run 分支：原 preview/capture→创建 ASSET_REDEEM 与 ASSET_MATURITY actions/flush→`record_recovery_plan`（集中 RECOVERY_PLAN/CONTRACT_SETTLEMENT decision hooks）→declarations exposure 或零动作完成→各新 action 的 `ACTION_CREATED` | ActionCreated 初态就是 SUBMITTED，记录原完整 BankRequest/request hash 和 declaration；不再加伪造 PLANNED→SUBMITTED。key=各原 action ID。已有原 run 分支不新建/重录。零动作仅 decision，无假 action/receipt |
| `apps/api/app/services/simulated_bank.py::process_redemption` | 原独立银行事务：新请求分支真实授权/原合同检查→BANK_ACCEPT decision→创建 ACCEPTED SimulatedBankRedemption→建/核对应 `LEGACY_REDEMPTION` BankOperation/flush→**仅本次新 request** `BANK_ACCEPTED`→记录 settle 前原状态→`_settle`→同步 unified status/settled_at/flush→真实 ACCEPTED→SETTLED 才 `BANK_SETTLED` | 两个表是一项操作，统一以 request.id（亦 operation.id）为 subject/key。新受理事件安排在 wrapper 已建/校验之后、settle 之前。旧 request 但缺 legacy wrapper 时只建 wrapper，不伪造第二次受理；旧历史未审计明确报告 |
| 同文件 `_settle` | 不另挂 304 append；它只在 `process_redemption` 内完成两条守恒 legs | 304 settlement 挂在外层 unified 同步成功后。不能 `_settle` 与 wrapper 各记一次，不能复用 301 `_settle_operation` 再跑 legacy 结算 |
| `apps/api/app/services/recovery_projection.py::project_request`，SETTLED 新回执分支 | `run_recovery` 每 action 的原 `begin_nested` 内：核 identity/原 request/完整 legs→所有 account/position/goal/proof/PRINCIPAL_RETURN transaction/evidence→新 receipt→action SUCCEEDED→末次 flush 后 `ACTION_PROJECTED` | key=原 receipt ID；已有 receipt 分支不追加。完整经济时间来自原现金 posting，observed/reconciled 为当前可信 now。外层 finalize 失败会回滚投影与事件；银行不回滚 |
| 同文件 `project_request`，ACCEPTED 分支 | 真正首次将 position 置 REDEEMING/补受理 availability proof 后、该分支 return 前 `RECOVERY_OBSERVED(kind=WAITING_PROJECTION)` | key=原 request ID+WAITING_PROJECTION；保存等待/available_at，executed cash=0、receipt=null。晚时钟再次 supersede proofs 不能冒充新资金效果；已有此等待事件返回原语义，不用新 proof IDs 改原 payload |
| `recovery.py::run_recovery`，第三事务 | 单项 `PolicyLifecycleError` 失败 savepoint 退出后、外层仍活着：追加 `RECOVERY_OBSERVED(kind=PROJECTION_FAILED)`；现有 bank-error 新 notification 与 run 真实完成 transition 后分别记录观察差分 | 失败事件在失败 savepoint 外，否则随失败消失；不能在失败 savepoint 内读取/flush 半成品。稳定键包含原 run/action/request、kind、稳定 error code 与已核原 bank 状态（不用动态 message/now）；同一失败重试不重复，银行状态真实推进后可记录新的观察。完成事件只在 run 真正改变时追加 |
| 同文件未捕获的异常路径 | 沿用原异常传播；记录到哪个已提交事务就以哪个为准 | 现恢复路径只收集 `PolicyLifecycleError`，不能为写审计擅自新增宽泛 catch、造 application UNKNOWN 或改原动作状态。银行已提交事实仍可查，缺应用观察如实标明 |

`finalize_projections` 只刷新 epochs/exposure，不重记 ACTION_PROJECTED/资金到账；批次 `RECOVERY_OBSERVED` 描述观察/完成，不第二次把本金算成银行结算。成熟动作保留 CONTRACT_SETTLEMENT 的 `new_authority=false`，并沿用原合同，没有“到期重新授权”事件。

## 6. 策略和目标：最少且能辨认实际差分

所有以下策略操作使用调用方 Session，内部 savepoint，最终由 `apps/api/app/db/session.py::database_session` 或现 CLI 的外层事务提交。不得独立提交审计，以免原业务随后回滚却留下成功授权。

| 路径与函数 | 精确位置／事件 | 幂等及排序细节 |
| --- | --- | --- |
| `apps/api/app/services/policy_lifecycle.py::_append_version` | 原 confirmation evidence→新 version/flush→`_project_goal` 成功后 `POLICY_VERSION_CONFIRMED`→return | 中央 hook 同时覆盖 `confirm_proposal` 与 `change_policy`；previous nullable 区分首次/修改。key=new version ID，绑定原 confirmation/hash/request_key/request_hash、配置/授权窗口与真实已有 goal 投影差分；不在 wrappers 重复记版本 |
| 同文件 `_refresh_one` | 当且仅当 status 真正变：保存 before→更新 policy.status/updated_at→`POLICY_STATE_CHANGED`→若 EXPIRED 再 `_invalidation`→flush | 原 version ID+before/after+可信生效 cause 去重；现调用来自 change/stop/显式 clock refresh，可全覆盖。effective_status/is_version_authorized 的只读函数不挂钩。EXPIRED action 事件可引用已有 state-change 事件 |
| 同文件 `_stop_policy` | 在 `_refresh_one` 后、版本/target 校验通过：若 target 真变，保存 before→target/updated_at→`POLICY_STATE_CHANGED`→`_invalidation`→flush | 时间刷新事件若真实发生先记，随后 target 真实变化再记，不能合成从旧状态直接跳过中间真实事实。repeat target 不记 policy transition；仍可能失效新计划 |
| 同文件 `_invalidation` | **仅在** `plan.status in {PLANNED,AUTHORIZED}` 且无 receipt 的赋值分支：保存 before→INVALIDATED→对应 `ACTION_STATE_CHANGED` | 返回 `invalidated` 列表也包含早已 INVALIDATED 的计划；不能拿结果 IDs 统一无条件追加。只给实际新失效赋值挂钩，保留返回合同。私有 cause context 可传 POLICY_REPLACED/STATE_CHANGED/EXPIRED 与原 version IDs/request hash；无前置事件时 causation 为 null，不引用尚未创建的新 version event |
| 同文件 `change_policy` | 已有 change-key 返回原 lifecycle 不挂；新分支的真实 `_invalidation` 事件先发生，新 version event 由 `_append_version` 后发生 | 此顺序是当前业务顺序。不得为凑审计顺序提前提交新 version 或改失效合同；version event 可绑定此次实际失效 references，但“inflight 保留列表”不是失效事件 |
| 同文件 `confirm_proposal` | 新分支由 `_append_version` 记录，后续 proposal CONFIRMED 与新版本同原 savepoint | 原 confirmed-proposal 的原版本验证/return 不追加。LLM/compiler proposal 不记为授权 |
| 同文件 `refresh_time_states` | 沿用 `_refresh_one` 集中 hook，wrapper 不额外生成时间刷新经济事件 | 无变化返回空更新，无事件；同版本 CONFIRMED→ACTIVE 与后来 EXPIRED 是不同真实 transitions，不能按 policy ID 只记一次 |
| `apps/api/app/services/goals.py::create_goal_projection` | 验原已确认版本/银行投影→新零 goal→ownership/contribution proofs/flush→仅新 goal 的 `open_execution_anchors`→exposure→flush 后 `GOAL_INITIALIZED` | key=原 goal ID；已有 goal/account 一致返回早于新初始化，不追加、不重开正额经济事实。目标创建与政策修改导致已有 goal 更新不同：后者并入 version-confirmed 原差分 |

策略 change 在 `_append_version` 前已经失效旧动作；建议私有 `_invalidation` cause 参数明确当次触发类型与稳定 request hash，事件直接绑定旧 version IDs。这样无需隐藏 Session.info 延迟队列，也无需修改公开 LifecycleResult。当前 `_append_version` 对 SUSPENDED 策略保留暂停状态，审计不得额外写“修改即 ACTIVE”或“恢复授权”的假 transition。

## 7. 205/301 同经济操作去重规则

1. BankOperation ID 是统一 operation identity；205 的 SimulatedBankRedemption.id 与对应 unified.id 相同。分别由自己的 bank owner 写事件，公共 append 只验证，不再次结算。
2. ACCEPTED/SETTLED key 分别由 type+原 operation ID 生成，不含模型表名、endpoint、retry now；同操作同经济阶段只有一条。若今后两个路径错误同处理一个 legacy 操作，应冲突/拒绝，不能得到两个“有效受理”。
3. receipt key 由原 receipt ID 生成；一次 ACTION_PROJECTED 已包含 action 成功/回执/对账，不再从外层 execute/run success 返回处写同类事件。
4. ACTION_CREATED（声明）、DECISION_RECORDED（计算）、BANK_SETTLED（经济事实）、ACTION_PROJECTED（应用对账）是不同事实，不把四条事件的 amount 相加计算流量。指标必须按 event type/operation role 明确取经济事实。
5. UNKNOWN/WAITING/失败观察没有 settled cash、没有伪造 posting/receipt；不生成新 effect/hash/idempotency key。合法原请求重试保留原动作、经济哈希和先前事件，最多新增真实结算/投影/重新重验记录。

## 8. 明确零挂钩的只读路径

`decision_trace.get_decision_trace/get_action_trace/list_decision_traces`、domain explanation、`recovery.preview_recovery/get_recovery_run`、autonomy/intent/transfer-preferences 的原只读 assessment、boundary/living-reserve/asset/goal/policy GET、`is_version_authorized/effective_status`、`verify_execution_receipt`、`verify_recovery_receipt`，以及新 audit list/detail/verify：没有 append、补录、重授权、自动对账、schema 修复或生命周期刷新。

只有显式 `decision_assessment.save_assessment` 的 POST 才保存评估及 decision 事件。仅 HTTP 方法是 POST 不能作为追加理由：只读 assess/preview/verify 仍零写。未知审计协议/旧未审计 run 明确返回未支持/未审计，而不是在读取时伪造完整链。

## 9. 交还与前置条件

本次仅通过定向 `Get-Content`/`rg` 阅读现有源码和事务，未执行新测试或真实资金流程。新增文件只有本文，另给前一事件预审补交叉链接。303 owner 源码、合同、测试、既有证据继续冻结。

root 完成 303 full check+提交后，先冻结本最小 event set、head/checkpoint 与 reset 的权限/epoch 方案，再进入 304 独立 RED/GREEN。本文把挂钩最小化，不免除真实 PG 对幂等/并发/事务回滚/UNKNOWN/T1/只读/删尾/reset 的必要验收。
