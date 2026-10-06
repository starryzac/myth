# 联合目标不同金额执行 v2：底座和接线合同

2026-10-06，新算法 `registered-joint-goal-execution-v2`，新原请求键 `full_joint_goal_execution`。原 Joint/FullJoint 八层求解器、307、旧 Action/BankCommand/effect/hash、历史 ASSIGNED 和收入用途不变。专用流程 USER/ASK，原已确认每 Goal MVP 月度 min/target/max/窗口/期限仍是金融许可；Full GoalAllocationPolicy 是组合范围/单次总上限的规划确认，不成为新的银行权限。

## 当前只读能力

新 `app.domain.full_joint_goal_execution` 和 `app.services.full_joint_goal_execution` 真消费当前完整 GoalAllocationPolicy/原确认、全部 Goal 原模型/MVP 版本、同一 RRRO 原 Joint 输入及 Full 1098 点来源。新 HTTP 请求仅 `full_policy_id`、`expected_full_policy_version_id`、`expected_epoch_id`、`idempotency_key`。不接受 amount/facts/clock/permission/result。当前有限合同要求 policy.goal_ids 与全体已登记 Goal 精确相等、2—8 个；不删除未选 Goal 或未决动作来制造可行。

`max_single_allocation_cents` 明确为整次联合批次总额 cap。保留原 income_lots 分母、全部原硬保护；在各点 other_protection_floor 中仅增加 `max(0, remaining-cap)`，使新可分配金额不超过 cap。不转移现金、不借未来收入、不降低硬最低；原 solver 完整八层重算。每子 amount/逐 fragment/origin/source/amount 固定，逐子原 307 fixed-effect proof 和原权限/安全重验，不能调用 greedy dynamic builder 后换用不同来源。

周度合同：目前原 Goal/MVP 和 FullGoalModel 提供月度范围，当前 GoalAllocationPolicy 无 weekly 字段。此版不声称实现周度授权或多期全局调度；未知来源、未完整 Full 保护/reference/history 或旧未决责任保 UNKNOWN。

## Root 唯一 ORM/0015 接缝

三张表与资产组合表完全分离；Root 修改 shared Base/迁移，代理只消费精确新类。各表继承 OwnedMixin：`id UUID PK`、`user_id UUID owned FK`、`created_at UTCDateTime NOT NULL`。所有身份/原 JSON/hash UPDATE/DELETE 拒绝、非空历史不可 downgrade；不比较实际审计 appended wall clock 与模拟业务 created_at。

| 类 / 表 | 必要字段 | 约束 |
|---|---|---|
| FullJointGoalExecutionPlan / full_joint_goal_execution_plans | epoch_id UUID; full_policy_id UUID; full_policy_version_id UUID; idempotency_key varchar160; request JSONB; request_hash varchar64; plan JSONB; plan_hash varchar64; expires_at UTCDateTime | owned epoch FK; unique(id,user_id,epoch_id); unique(user_id,epoch_id,key); FullPolicy owner/epoch 与 FullVersion policy/owner FK（保留行）；request JSON object≤1MiB；plan JSON object≤10MiB；hash小写64；expires_at>created_at |
| FullJointGoalExecutionChild / full_joint_goal_execution_children | plan_id UUID; epoch_id UUID; child_number int; goal_id UUID; original_mvp_version_id UUID; action_plan_id UUID; bank_idempotency_key varchar160; command JSONB; command_hash varchar64 | (plan_id,user_id,epoch_id) parent FK；unique(plan,child_number), unique(action_plan_id), unique(user_id,bank_key)；1≤child_number≤8；command object≤1MiB/hash64；Goal/MVP/Action 保留身份不 FK 当前会被 reset 删除的行 |
| FullJointGoalExecutionConsent / full_joint_goal_execution_consents | plan_id UUID; epoch_id UUID; idempotency_key varchar160; request JSONB; request_hash varchar64; plan_hash varchar64; evidence_id UUID; evidence_hash varchar64; original_evidence JSONB | owned parent/epoch FK；unique(plan_id), unique(user,epoch,key)；Evidence 保留原 identity 无当前行 FK（reset 会删除当前 Evidence）；JSON objects≤1MiB；全部hash64/key1..160；保原实际 Evidence，不补造 actor |

不能仅借 DecisionRun JSON 代替三表：原 DecisionRun 是完整冻结数学/来源 proof，但原金融入口需要可靠索引化 child 关联、唯一父/子键及顺序、唯一 consent 约束。只扫描 JSON UUID 会让删 marker/key 降级与 reset 后关联不可证。父仍须保存完整原输入/结果及 DecisionTrace 来源，表不是授权缓存；每 invocation/phase/bank 再读当前原件。

## 持久 workflow

prepare 先在一个原 User 锁事务保存整个不可变父+全部 UUID5 固定 child 绑定及完整数学决策记录，提交后分别调用原 ALLOCATE_GOAL prepare 事务。不能持有 User 行锁再跨 Session prepare（会死锁）；此包没有修改原 prepare 为共享 Session。绑定先于可执行 action，防尚无 marker 的降级窗口。Root 原私有 typed hook只接服务器 proof/固定 effect，不使用 `_experiment_candidate` 或客户端 amount。父/child 保存原完整 request/hash、plan/hash、旧 effect/hash和原银行键。

原 prepare 后续失败时，已经提交的父/绑定/已准备子步保留为 PARTIALLY_PREPARED，同原准备键恢复缺失子步，不换金额/来源/原键。全部子步实际原件可验证前不得 whole confirm 或执行；缺 hook 时在任何父写入前拒绝。父资金未预留（WHOLE_UNRESERVED_CHILDREN_USE_ORIGINAL_CLAIMS），已提交子步按原金融管道维护各自真实 claim。不能声称跨这些事务全回滚。

prepare、confirm、execute-child 三个写入口均要求当前 LOCAL_SIGNED_SESSION 的真实 USER（完整 owner/session/window 校验），路由复用 RecoveryPrincipalDependency，服务再次 require_local_user。客户端不传 role/actor。只读 GET/preview 不授予权限。

confirm 仅 strict accepted=true + reviewed_plan_hash + expected_epoch_id + 原 key，原完整 Evidence/DecisionTrace 保留。原 content 用闭合 FullJointWholeConsentContent：原 body/hash/plan/epoch/time 加实际完整 LocalActorPrincipal、actor_session_id、actor_role=USER、authentication_source=LOCAL_SIGNED_SESSION、human_identity_verified=false。此 USER 是本地认证身份，不是已开展真人研究。确认父不是金融完成，每个 ASK child 仍须用户明确点击固定 execute-child，以原 exact effect 调原 confirm_action；不批量自动确认子步，不复用以前确认。当前 phase proof.context requires_confirmation=true，新 inputs 按该值重新计算并完整 capture，原权限/金额/源不变。

父确认追加 EVALUATION DecisionTrace：run=uuid5(parent,"whole-confirmation-proof")，parent_run=uuid5(parent,"planning-proof")；inputs=joint_execution_input/joint_confirmation_request，outcome=joint_execution_plan/joint_confirmation/decision_status；来源保原父完整 copies 加真实确认 Evidence。缺完整原 trace/audit/source 时不把只读 retained JSON 称作当前已验证权限。

execute 固定 `expected_child_number` + `expected_action_id` + strict accepted/reviewed hash/epoch。已完成原 child 重读不推进下一 child；前子 SUBMITTED/UNKNOWN/任何未证原 bank/receipt 阻下一 child。阶段1和 bank 新 accept 在原 User 锁中当前权限/来源/365安全重算，只核原 amount/uses；只能排除严格证明本动作 own claim，其他所有 claims 保留。已有原 SETTLED 同键只验经济事实并 projection/recover，不重新授权/扣款。多个已提交银行动作无法原子回滚，明确非跨银行批次原子。

识别门必须 marker OR new bank-key OR持久 child.action_plan_id：同时剥 marker/key仍进 dedicated guard；完整 Action.request_hash/PREPARE typedTrace/parent/原子 command 绑定任一缺件直接拒绝，不回落 legacy。默认旧动作/算法分支不变。新 trace 必须完整 `action_request` 和 `joint_goal_execution` 私有 proof 输入，可从已冻结父+当前源复算；任何 stage 字符串不成为经济证明。

Root 独立 exact 三类已写 full_joint_goal_execution_models.py，目前 MODEL_ONLY；尚未安装 0015 或公开注册本 API。Root typed hook/算法白名单/历史 pure delegate 尚待接线。实际执行/PG 未运行；只读 preview 与固定计划数学完成情况和金融效果验收分别登记，不以接口 DTO 宣称可银行执行。原 type/static/direct FAIL 和修复结果在 .runtime/FULL-joint-goal-execution 保留。

## 直接检查和必要集成前置（本包冻结）

- 数学/身份 31 项首 scoped 结果：30 PASS / 1 FAIL，95.23s。失败为新篡改夹具先违反原 income_uses≤cash_use 恒等式，尚未到父冻结门；原失败和源已存。窄修夹具为合法的新金额/现金/来源三者同时 +1 并重算子/父 hash，原 whole solver 输出不改；该节点重新 PASS 13.91s，30 个旧通过节点不重复。此前父结果原 50000/100000 与 fixture 假设反向所致 RED 也保留。
- 签名 USER/严格真实 JSON API 新 28 项 PASS / 5.76s：三写入口无 cookie / AGENT / 过期 / 别 owner 拒绝，服务亦在任何 SQL/hook 前拒绝；合法 UUID JSON 严格原 body/principal 传递，伪金融/actor/query 与非 strict accepted 拒绝；闭合父 actor 原件不接受会话串换/裸 hash/真人声明。纯 synthetic 协议风险，不是产品金融或真人效果。
- 已安装 hook 缺失风险补充 PASS / 2.91s，显式去掉专用版本门，不依赖根后续是否已安装真实 hook。
- 新 8 源严格 mypy PASS（cf72d08f）、Ruff PASS（db3ce750）；实际候选单源 mypy PASS（e8ec7d42）、Ruff PASS（7d0a528f）。候选首次 `_fault` 参数顺序 type RED 与未用 import static RED 保留，修为原四位置 `_fault(kind,step_kind,engine,user_id)`。本批无全量、金融 PG、浏览器。
- `test_full_joint_goal_execution_integration.py::test_actual_joint_different_amount_signed_whole_consent_fixed_key_unknown_no_advance` 仅 collection：1 项 / 4.01s，实际 NOT_RUN。候选通过原结构化提案/明确配置确认创建两 Goal，再原双 hash FullModel、原 INCOME 银行事实、完整 GoalAllocationPolicy，原 preview 1098 零写；签名 USER 父确认；前子丢银行响应后 UNKNOWN 阻后子；同固定子原键恢复/重放不推进后子；后子真实收入 ledger successor、全用途/银行原键/两回执/审计 VALID。没有生成新的成功证据或冻结正式案例。

**收入接线必要差量：** 现旧 `reserve_income_for_action` 只接受 action.request['income_evidence'] 原账本或 time-only successor。首子 ALLOCATE_GOAL 真实 ASSIGNED 会改变经济 ledger，故后子旧 reference 不再是 time-only；不能修改原 Action.request/hash/用途来伪装新行动。Root 将用专用 `validate_current_joint_income_reservation(session,action,proof,now)` 当前完整 parent/固定子/前序已验银行回执/原 fragment identity + 当前实际 Income Evidence/hash + fresh typed proof 校验，仅允许新 Joint 绑定分支消费当前真 ledger，原 reserve 守恒与可用额度门照旧。此 helper 尚未交付，当前闭环未完成。

**Root 最小接线：** 新私有 prepare `_full_joint_goal_request`，固定 action_id/bankkey 与原 MARKER JSON；三个 `FULL_JOINT_GOAL_GUARDS_VERSION=registered-joint-goal-execution-v2` 真实源门；prepare/confirm/阶段1/当前 independent-bank source/首次 bank accept/Autonomy 完整 fresh fixed proof；新历史算法 delegate 保 default 原字节；原 settled 同键恢复不得重新授权。新 Root `domain/full_joint_goal_execution_trace.py` 由 Root 单独拥有。新纯 helper exports `verify_frozen_joint_execution_trace(trace) -> FullJointFrozenPlan | FullDynamicGoalProof` 和 `read_frozen_full_joint_goal_proof(trace) -> FullDynamicGoalProof`，均无 Session/SQL；父 EVALUATION 与 child trace 输入/来源完整重算，当前新 child 必须 requires_confirmation=True。此包不改共享收入/银行/审计/执行模型或旧 producer 源。

**仍未覆盖：** 实际迁移/HTTP注册/跨源 hook/收入专用门接入后的真实金融链、进程硬崩溃和部分 materialization 重启实测、周度/多期/目标子集规划、跨多个银行子操作原子回滚（不承诺）、失效新版本的 FULL605 Joint 批次动作联动、reset 后缺原当前 trace 的归档读取与验证。当前原历史缺件拒绝，不补造 archive proof。原要求关闭和最终全量保持未验。
