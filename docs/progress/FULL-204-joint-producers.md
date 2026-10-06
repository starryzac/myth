# FULL-204 联合目标当前候选生产者

2026-10-06，新协议 `joint-action-set-input-v1` / 算法 `full-policy-joint-action-producers-v1`。旧 actual-v2、v1、Full-v1、周期/Recovery/Release、原 Joint/FullJoint 求解器和 307 原算法、默认行为、输入与历史哈希不变。本包是实际服务器只读消费者，未创建新金融执行入口；不能单凭 family complete 关闭全局 FULL-204。

## 入口和组合合同

`app.services.full_action_set_joint_producers.capture_current_joint_producers(session,user_id,now,*,original_actual_capture=None)` 返回 `JointActionSetCapture(inputs,result,originals)`；`read_current_joint_producers` 只返回结果。只接受 clean Session、REPEATABLE READ、READ ONLY、实际 simulated User、当前 OPEN epoch 和服务器业务时钟。可复用参数仅供同一 RRRO invocation 的 Root 私有组合读取，owner/epoch/as_of 必须一致；不接受公共客户端金额、事实、授权、结果或 now，没有跨请求缓存。

同一 Session 内调用原 `joint_goal_planning(...,capture_inputs=...)`，保存完整 `MultiGoalAllocationInput`，再调用实际 `full_joint_goal_planning` 并要求原 Joint 响应相同。完整 Full 保护 typed 输入从原 verified financial context + FullAnnual 来源重新捕获，不把 DTO hash 当输入；原 365 点绑定和八层整数求解器重新运行，结果/来源/1098 点/binding 全等才继续。当前允许同请求重复读取同原服务，不改变权限或用效率代替验真。

Root v5 精确字段：`joint_family_complete`、`original_actual_input_hash`、`expected_candidate_keys`、`results[{candidate_key,goal_id,view,shadow_original_candidate_key}]`、`handled_unsupported_codes`、`reasons`、`expected_goal_ids`、`original_action_ids`、`unresolved_original_action_ids`。原 input hash 为 `configuration_hash(original_actual_input.model_dump(mode="json"))`，对应 v4 原 snapshot.input_hash；新 input/result 哈希另外保留。

Domain `derive_joint_producers(JointActionSetInput)`；Service `verify_joint_source_copies(trace,inputs,result)`；`verify_frozen_joint_producers(trace)` 只接受 `algorithm_versions.joint_action_producers` 的新算法、EVALUATION/无 action、`inputs.joint_action_set_input` 和精确 `outcome.joint_action_set_result`。验原件副本 identity/source/level/owner/content/captured hash/status/aware 时间，重算全部数学；不将旧算法 relabel。

## 完整分母与有限行为

- 全部实际 Goal（不按 ACTIVE/当前完成过滤）、对应现双 hash FullGoalModel 与原 MVP 版本、全部正 AVAILABLE income fragments 和 BANK 原来源；所有 Goal/模型/时钟/范围/当月已贡献/已归属值与当前原行绑定。缺当前模型或原权限 UNKNOWN，缺件不作为无动作。
- 原库完整表 count/行 SHA、同 owner/OPEN epoch、原 current financial basis、完整 catalogue/position/各类 reservation；现金、GOAL_CASH、POSITION、legacy reserved 均保留。ASSIGNED/SPENT 不成为 AVAILABLE，未来收入不增加当前资金池。
- 全部 ALLOCATE_GOAL 或 307 marker 原 Action，逐个 PREPARE trace/command/action/request/hash 核验；SUBMITTED/UNKNOWN/RESERVED 或不完整原 bank/receipt 保 unresolved，暂停/撤销不消失。历史 SETTLED 必须原 frozen projection/receipt/完整 posting/原 transaction/Evidence 全部通过。
- 原 Full 保护 typed sources/原当前版本/配置/confirmation/evidence/time/source scope、1098 点完整重算。原 input.horizon 保留实际捕获值（默认 90），`project_full_protection` 的原内部 365 曲线产生 1098 点；不悄悄改 input/hash 为 365。
- 容量 8 Goal / 16 MiB，新输入截断、重复、超容量、来源不全、solver UNKNOWN 均保留原 Goal 分母与 null 金额；任何成员未完成则整家族 UNKNOWN，没有可执行的部分成功标签。

正 allocation 只有与已有服务器 307 当前候选 **完整经济效果相同** 才 INCLUDED：ALLOCATE_GOAL、Goal、金额、目的账户、原 MVP version、逐 fragment/origin/source account/amount income uses 全相等；仍独立经过原 validation/权限和 Full 保护 veto，继承其真实签名而不生成新 grant。只有此时 `shadow_original_candidate_key="goal:<原UUID>"`。不是把 Joint 金额硬塞到单目标 planner。

原已证明 OPTIMAL 的零金额只给 `EXCLUDED` 并保留原 producer（无 shadow）；零不代表无来源或无未决动作。求解器解释性 reasons（如精确最优与 censored delay）按原完整重算集合保留，不用非空字符串判失败，也不用 `OPTIMAL` 字符串替代数学。

`current_joint_execution=EXACT_EXISTING_EFFECT_ONLY`；不同联合金额或碎片为 `NOT_IMPLEMENTED_FOR_DIFFERENT_ALLOCATION`，UNKNOWN、不 shadow。当前原 planner 没有消费 Full GoalAllocationPolicy 配置；其当前 active 实例明确 `JOINT_FULL_ALLOCATION_POLICY_NOT_CONSUMED_BY_ORIGINAL_PLANNER`，`handled_unsupported_codes=[]`，不清除其 gap。需专门 history/seasonal/periodic reference wrapper 的当前保护来源未由本 adapter 完整重放时为 UNKNOWN；不能凭 reference-current/effective-status 标签降低保护。

## 后续真实 Joint batch 最小接缝

原 `MultiGoalAllocationResult.goals[].amount_cents` 与 `income_uses` 才是联合金额和来源。当前旧 prepare allocate_goal 选 nominal、307 effect builder 选动态 cap；两者不一定等于联合结果，不能只放宽旧 nominal 校验。新专用服务器 proof 必须绑定完整原 Joint 输入/Full 输入/重算结果/全部子 Goal 原版本/完整 fixed uses 与父 batch 哈希、固定每子 action/key。

原 claims 语义：prepared 尚无经济效果；phase1 RESERVED 与来源 reservation 必须按实际原 action/command/UUID 验真，本动作 own claim 可以由原门排除，其他所有 claim/未决责任仍保留。任何前子 UNKNOWN 不推进后子；前子 SETTLED 的原 ASSIGNED/income uses 不变，不借新建议换子金额或来源。每银行新 accept 要锁内 fresh 复算当前权限、保护和原 fixed amount/uses；已 SETTLED 同原键只验原经济事实并投影/协调，不重新扣款。新的多目标 USER ASK batch、persisted parent/child binding、exact consent、累计 capacity 与默认门集成由 Root 后续实现，本包不宣称已有。

## 验证

四新 Python 源 strict mypy PASS：`W3/joint-producers-final-types-20261006T041859Z-601f3539`；Ruff PASS：`joint-producers-final-static-20261006T041900Z-c9a76491`。两份命令 all/scoped source stable=true，exit0，仅该模块范围，不是全量验收。

实际候选 `test_full_action_set_joint_producers_integration.py::test_actual_complete_joint_original_goal_income_1098_and_exact_effect_zero_writes` 只 collection：`joint-producers-final-actual-collection-20261006T041900Z-a6602f8f`，1 test collected /4.65s，wrapper PASSED；**实际 PG NOT_RUN**。候选通过实际 FullGoal 双 hash 确认等额 target/max=20000 + BANK 原新收入前置，检查 1098 点/来源/原效果映射/all physical 零写/EXACT audit；此候选并非正式实验 case 或真人研究。

最终 37 项直接风险 PASS 197.03s：`joint-producers-final-owner-original-metadata-and-whole-math-20261006T042250Z-82810966`，wrapper exit0 / scoped_source_stable=true；global 因独立实验资产源变更为 false，不称全仓冻结。四源最终 strict PASS：`joint-producers-final-exact-metadata-types-20261006T042318Z-1eac398c`（scope=true/global=false，仅其它实验测试变化）；Ruff `...static-20261006T042318Z-21980473` 和 format `...format-20261006T042319Z-e0db19a4` PASS、all/scoped=true。

此前 29 FAIL/1 PASS（27.31s）是 synthetic fixture 误用 strict Python UUID/日期解析，原 `joint-producers-first-direct-source-and-effects-20261006T041110Z-e95b1755` 留存；随后 frozen fixture assignment、结果 hash 未先 JSON 化、OPTIMAL reason 误拒的各 FAILED 原 wrapper/source 全保留于 `.runtime/FULL-204-joint-producers/*-red-*`。只改本新源/夹具，没有放宽旧金融门或更改旧失败证据。

完整 35 项首批为 34 PASS /1 FAIL /192.21s，`joint-producers-whole-goal-source-trace-and-effect-risk-scope-20261006T041839Z-91ed8513`：唯一失败为 owner 异主数据已被原严格 schema 正确拒绝，测试错误期待 UNKNOWN；只将该断言改为真实 ValidationError，不改权限/owner 门。另新增金融底座 evidence metadata 原 id/hash 与 actual Evidence/source refs 一致门，新增两风险；修正合成原件旧 metadata hash，不将重算底座 hash 当原来源证明。原四源另存 `typed-owner-rejection-and-metadata-red-20261006T042247Z`，原 FAILED 保留。

已通过两个有限差量（16.11s/29 deselected）：`joint-producers-exact-original-max-and-nominal-difference-20261006T041523Z-346e7f6a`。全合成原件明确 target=max100000 时两个原效果相同可 INCLUDED；保留 max150000/target100000 时 joint=100000、dynamic=150000 为 UNKNOWN/no shadow。不能将合成差量当真实金融成功。

未覆盖：新 Joint batch 真实执行/全局 v5 注册与通知、当前 active GoalAllocationPolicy 配置消费、需专门 runtime reference/history 证明的额外 Full 保护、原 unverified/旧缺件历史、>8 Goal/超容量、多期全局最优、全部实际 PG/API/browser/性能/真人及最终验收。没有证据不补造成功。
