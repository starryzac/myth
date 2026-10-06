# MVP 五臂实际服务接线协议骨架

状态：`NOT_IMPLEMENTED / PENDING_PRODUCTION_VERIFICATION`。本文件与 `scripts/mvp_arm_executor.py` 只定义可运行的候选选择、原件读取和接线函数。当前 root-owned `app.services.experiment_arms` 窄入口不存在，默认 prepare/execute 在调用任何 callback 前拒绝。纯测试为 `TOOL_TEST_ONLY`，没有 DB、HTTP、银行、P 财务判断器或金融动作；不能凭此关闭 MVP-501/503，也不能把 MODEL_ONLY 改标签充作五臂 SERVICE_INTEGRATION。

## 与原管道接线的位置

已只读审查 `action_contracts.py`、`execution_planning.py` 与 `execution.py`。原 prepare_action 以 user lock 和同一事务检查 idempotency，再生成新 action UUID、构造 effect、加载真实 source/execution context、revalidate、计算 BankCommand hash、保存 ActionPlan/DecisionRun/trace/epoch/exposure。原 `_build_effect` 仅四个参数，当前没有候选替换入口。购买/目标小 intent 只有 policy/goal ID，金额与产品、来源、income 归属由服务器生成。confirm_action 精确绑定原 effect hash，写真实 USER_ACTION_CONFIRMATION 并重验；execute_action 保留资源占用、银行独立 commit、业务 projection 独立 commit 及 UNKNOWN/reconcile。

root 后续仅在**新 effect 创建之前、原锁与 prepare 事务内**接显式 callable `candidate_selector`。已有 idempotency action 先返回原动作，不调用新候选、不改原经济效果或历史哈希。`candidate_selector=None` 表示保留原实际 P 规划；callable 返回 `None` 表示该基线这次没有候选，不能退回 P 金额。callable 的金额/来源仅是候选，不能携带 grant、permission、预造 effect 或绕过产品/权限/source/income/economic-effect 检查。原 autonomy、reserve、银行三阶段、失败与 UNKNOWN 管道都必须保留。

当前程序没有实现上述 root 窄入口，也没有实际 pipeline 验收。记录函数即便读到完整原件，其 `execution_mode` 仍为 null、`capability_status=PENDING_PRODUCTION_VERIFICATION`、`financial_effect_evidence=false`。raw status 只是被捕获的状态；独立指标工具仍须重算真正结算、账本、receipt/projection 与权限，不能用本工具的 SUCCEEDED 或 stage 名称认定 SAFE。只有后续冻结真实源码、真实管道与完整原件验证通过后，另行明确修订服务能力状态。

## 唯一可接线接口

```python
# app.services.experiment_arms 中的原模块级函数；不能是 mock/class method。
verify_simulation_context(context) -> OriginalCapture
prepare_arm_action(context, small_request, *, candidate_selector=None) -> OriginalCapture
confirm_arm_action(context, action_id, original_effect_hash, actor_registration_ref) -> OriginalCapture
execute_arm_action(context, action_id) -> OriginalCapture

# 纯工具外层；没有 hooks/actual source freeze 即拒绝。
prepare(context, original_rule, opportunity_id, small_request,
        *, hooks=None, frozen_source_ref=None, registration_source_ref=None)
execute(context, original_prepared_record, actor_registration_ref,
        *, hooks=None, frozen_source_ref=None)

# 只核原件/合同，结果不授予执行权限。
candidate_selector(context, original_rule, opportunity_id) -> callable | None
record_prepare(context, capture)
record_confirmation(context, capture, action_id, original_effect_hash)
record_execution(context, capture, action_id, original_effect_hash)
```

SimulationContext 带明确 `now` 原业务时刻，不用实际机器 wall clock替代模拟时轴。接口不接受 Engine/连接串或远端 URL；provider 必须在每次调用实际核 Engine URL/DB owner epoch/user row。函数源码需来自真实 `apps/api/app/services/experiment_arms.py`，frozen_source_ref 精确 `{status:FROZEN_SERVICE_HOOK,original_path,sha256,archived_path}`，原源码 bytes 与实际存档 bytes 同一 SHA，已加载四函数 code 必须等于该原文件编译的模块级 code，prepare 签名必须有 candidate_selector。此门只核来源可用性；它不等于管道或金融效果证明。

context.protocol=`mvp-arm-isolated-context-v1`；bindings 为原完整十三项：experiment_run_id/case_id/arm_id/execution_mode/input_sha256/oracle_sha256/design_sha256/rule_sha256/source_sha256/seed_version/isolated_db_epoch/purpose/user_id。seed 固定 mvp-301-v6；purpose 区分 DEVELOPMENT/MVP_FROZEN/FULL_FAMILY_FROZEN/TOOL_TEST_ONLY。MODEL_ONLY 不可重标。TOOL_TEST_ONLY 与用途一致且不得调用 provider。仅允许 host=127.0.0.1、整数 port=54329、`bf_test_[32hex]`；正式、shared/default DB 排除。实际 ARM_CONTEXT 原件须含实际端点/epoch/user/is_simulated=true 和可解析 original_user_ref，原 user.id/is_simulated 再核。context 及 rule 在当次 invocation 改动即拒绝，每次 execution 都重新 attest，禁止跨请求授权缓存。

原 small_request 只允许 idempotency_key（1–160 非空白）与 intent。支持原五类小 intent 的 exact 字段：transfer_internal 的 source/destination/amount、purchase_asset 的 policy_id、allocate_goal 的 goal_id、redeem_asset 的 position_id、pay_recurring 的 policy_id 与 optional period/bill_id。不能塞入 effect、confirmation/grant 或购买/目标金额。原 effect 全字段、UUID/owner、经济 hash、整数金额必须从实际服务响应获得，adapter 只重算比对哈希，不写回任何历史。

## 五臂候选与 actor 合同

| 臂 | 候选行为 | 逐动作确认 |
|---|---|---|
| B0 | 原注册 MANUAL amount/opportunity/intent；仅 REGISTERED_MANUAL trigger，禁止主动 Agent recovery | 每个拟执行动作使用真实 confirm_action；原脚本 actor 与 exact action/effect hash 一起记录 |
| B1 | 原注册 cash_account_ids 中实际 CASH 余额总额减注册 threshold_cents，负值取 0 | 遵守实际 common gateway/autonomy；ASK_ONCE 仍需原逐动作确认 |
| B2 | 同原 cash scope 余额减注册固定 exact recurring obligation 的静态当地日 horizon 额 | 同上；range/bill/缺原配置 unsupported，不能读 P boundary floor 填值 |
| B3 | selector=None，保留实际 P 候选 | 每个拟执行动作实际合成脚本 actor 确认，即使原 autonomy=AUTO_EXECUTE |
| P | selector=None，保留同一实际 P 候选 | 原 AUTO_EXECUTE 不加 actor；原 ASK_ONCE 仍走真实确认 |

ADVISE_ONLY/BLOCKED 先拒绝，不能用 actor 点击改成执行。B0/B3 不伪造 grant，不把 policy 的一次确认当作本动作确认。原 actor_kind 使用既有 `SYNTHETIC_SCRIPTED_ACTOR`，event_type=AFFIRMATIVE_CONFIRMATION、phase=RUNTIME_INTERVENTION；actor original 必须匹配原 action/effect hash/确认时刻。用户确认证据保留原 UUID5(action,confirmation:hash)、USER_ACTION_CONFIRMATION/USER_CONFIRMED_ACTION、accepted=true、simulation=true、内容 SHA 和效期；真实 provider 必须调用原 confirm_action 来生成它。

rule.protocol=`mvp-arm-rule-v1`，原 purpose/case/arm/seed 精确绑定。prepare 另需 `registration_source_ref={path:绝对原文件,sha256:原bytesSHA}`，必须等于 bindings.rule_sha256 与提供的原 rule 内容，不能只注册一个计算出的金额。B0/B1/B2 的原 cash_account_ids 要唯一、完整、有真实 owner/CNY/CASH 行；不借目标账户或 position 余额。B2 注册 fixed_policy_version_ids、timezone=Asia/Shanghai 或 UTC、horizon_end_at；从原 configuration/content_hash/exact amount/due_day 逐当地日月推算，最多 90 天、31日按实际月末截断。它刻意是简单 static baseline，不是完整保护 oracle；不读 P preview 的 safe/suggested amount。

当前独立候选 arithmetic 只覆盖 purchase_asset/allocate_goal，以及 B0 精确显式金额内部转账；payment/redeem 候选改写、产品选择、完整 income funding、goal 所有权以及各臂完整自主动作调度尚未实现。原 P/B3 小 intent 协议可接全部五类，真实生产 provider 仍待实现。缺能力必须明确 NOT_IMPLEMENTED/MISSING；不能让 unsupported baseline 请求变成零风险成功，也不能用 P eval mock 充作 B0/B1/B2。

`unsafe_candidate_status`、gateway 原拒绝与 `actual_violation_status` 分别记录。候选工具没有独立安全 oracle，因此 unsafe=NOT_MEASURED；原 GATEWAY_REJECTED 只说明一次实际拒绝，不算执行违规；actual violation 必须之后用完整原账本/独立 oracle 推导，当前是 NOT_MEASURED。原 empty candidate、失败/未知与未实现机会均保留在冻结分母。

为了与现已冻结观察器/corpus 实际接线，正式 rule 原件可采用既有 `protocol=mvp-observation-registration-v1,kind=RULE` wrapper，把上述 `mvp-arm-rule-v1` algorithm 放在 `arm_algorithm`。wrapper 的 arm/case/purpose 精确匹配当次 context；whole wrapper 的原 bytes SHA 绑定 rule_sha256，不能改为算法子对象 SHA。原 mechanism_id/implementation_source_refs/execution_mode/registration_status/source等仍须由原观察器/corpus 校验，新增 executor 不代替这些冻结门。直接 algorithm 原件仅适合单独协议/TOOL_ONLY候选验证，不能让原观察器接受新标签代替原 RULE protocol。

## 实际原捕获协议

```text
OriginalCapture = {
  protocol: mvp-arm-original-capture-v1,
  artifact_ref: {artifact_sha256, json_pointer:"", value_sha256},
  original_paths: {原sha256: 绝对原文件路径}
}

原文件 = {
  protocol: mvp-raw-observation-v1,
  kind: ARM_CONTEXT | ARM_PREPARE | ARM_CONFIRMATION | ARM_EXECUTION | PIPELINE_STAGE | ...,
  bindings: 原十三项,
  payload: 原对象
}
```

每份文件重算实际 bytes SHA，拒绝 missing/漂移/重复 JSON key/NaN；每个 reference 为精确 artifact_sha256/json_pointer/value_sha256，可解析原数组/对象并重新核 canonical valueSHA。capture 原引用必须覆盖 full envelope，不能仅传一个非空 message/ref 或 VALID 字符串。真实 producer 应先保存原 stage/response/user/证据/receipt/posting 文件，再保存指向这些文件的 ARM capture；不可构造自哈希循环。文件新建，不覆盖原输入、历史失败或正式模拟原件。

ARM_PREPARE.payload：capture_origin=PRODUCTION_SERVICE_CALL，outcome=PREPARED/GATEWAY_REJECTED/NO_CANDIDATE。PREPARED 保留原完整 ActionResponse 的 simulation/user/action/decision/status/autonomy/full effect/effect_hash/prepared_at/as_of/prepared_validation。GATEWAY_REJECTED 保留实际 HTTP status/code/error.original_ref。原 pipeline 精确含 user_lock/prepare_transaction/candidate_before_new_effect/economic_hash/source_context/execution_revalidation/decision_trace；每项 original_ref 解析原 `{stage,user_id,action_id,occurred_at,capture_origin}`。未到达阶段也必须保存原未到达原因，不填成功。已有 idempotency replay 要明确原 replay 记录而不是声称本次新候选。

ARM_CONFIRMATION.payload：capture_origin、实际 response、confirmation_evidence_ref、actor_event_ref。程序核原 owner/action/effect hash、内容 SHA、确认/actor clock 与原 effect 有效窗口；actor 每次实际调用 confirm，并保留原 event，而不是添加字符串。

ARM_EXECUTION.payload：capture_origin/action_id/effect_hash/action_status、bank_operation_ref，pipeline 精确 user_lock/reservation_transaction/bank_request_commit/business_projection_commit。原 bank ID/user/action/full immutable BankCommand/request_hash 全部核对；SUCCEEDED 另需实际 receipt_ref、posting_inventory_ref，原 receipt owner/action/bank/整数币分。UNKNOWN/SUBMITTED 保留 censored=true，不写成零、settled 或安全成功。协议解析尚未独立验证全部经济腿/receipt投影，economic_execution_verified=false；原观察指标工具负责实核真正发生的变化。尚无原银行动作（例如在 bank 前拒绝）的执行捕获形式仍未支持，明确缺原件，不补造 bank row。

## 当前运行与未覆盖

可运行 `python -m scripts.mvp_arm_executor --describe`，输出真实 NOT_IMPLEMENTED。纯 tests 仅验证上述拒绝门、候选 arithmetic、原 bytes/refs/hash、真实语义确认字段、状态保留与 pending 声明。没有调用 provider/金融服务/DB/浏览器，所有 data 为明确合成 TOOL_TEST_ONLY；合成 stage 的 capture_origin 标签不把它们升级为产品证据。

未覆盖：生产窄 hook；实际 Engine/SQL epoch 与共享 reset exclusion 集成；全五臂动作/调度与 recovery；完整 source manifest freeze/runtime三阶段捕获；真实服务回执与 ledger projection；24 个真实新流程输入/corpus冻结/24×5运行；14指标正式实验全覆盖与金融效果。须后续逐项真实实现、冻结原源码并补原件，不能直接把本协议或测试 PASS 标为完成。

## 协议骨架冻结证据（2026-10-05）

HEAD=`4ccf84e973978482a1098d18c69fbfc9f011fac6`。实际最后纯 pytest 2026-10-05 05:52:44.046201—05:52:45.828307 UTC，61 PASS；Ruff/mypy 两文件均 exit 0，三个命令的 all_source_stable/scoped_source_stable 均 true。CLI 实际输出 NOT_IMPLEMENTED/null/no financial evidence；provider 路径实际仍不存在（05:53 UTC只读核验）。没有金融/PG/浏览器/全量执行。

- pytest：`docs/progress/evidence/W1/arm-executor-v1-final-tests-20261005T055243Z-a8c15fb2/manifest.json`。
- Ruff：`docs/progress/evidence/W1/arm-executor-v1-final-ruff-20261005T055244Z-65144d0d/manifest.json`。
- mypy：`docs/progress/evidence/W1/arm-executor-v1-final-mypy-20261005T055244Z-c126a7f7/manifest.json`。
- 实际 CLI：`docs/progress/evidence/W1/arm-executor-v1-cli-describe-20261005T055248Z-f98acd60/manifest.json`。

最终 script SHA256=`a38e1aaea3f5578d291add9a87e5030c70748f35272f6fcb9145e9a543cee062`；test=`b226fe90ca081b009301146022eccd9e372acce19e3ef4a103bd3663988eb676`。实际源码原件存档 `.runtime/W1-arm-executor-source-originals-20261005T135317-7cde6d8b/manifest.json`，同时保存两个真实只读 dependencies 原 bytes：observations=`f88262565d9115f46e0d125a2feecd87f315235d8f4a1294c4caa1319c71861c`；trace=`511c2f011c962f35b5bedc042b07aaf0d13f0b22bef5d40bc21d51bdec07caef`。此前原 corpus、A1—A4、观察器与金融 helper 未修改。早期 Ruff 行长度失败 `arm-executor-final-ruff-20261005T054757Z-b072810a` 永久保留；45/56阶段通过记录也不覆盖为61结果。

测试覆盖五个原规则 wrapper、B0注册人工选择/禁止主动 recovery、独立阈值/固定月历计算、B3/P 不替换真实 P 入口、上下文与现金范围/币种/类型严格绑定、源码 mock gate、missing/漂移/错误 pointer/valueSHA/重复 JSON/nonfinite、原 effect 改动/字段缺少、真实语义逐动作确认与原 hash/时效、缺真实 receipt、UNKNOWN/SUBMITTED 保留、small intent 不注入金额/grant、CLI 明确 pending。它们全部是合成 TOOL_TEST_ONLY 协议证据，不是五臂真实金融集成证据。

## COMMON_GATEWAY_REJECTION_V2 显式输出口径修订

root 在真实源码审查后明确：原 P plan 若被正常安全门拒绝，必须记录真正 COMMON_GATEWAY_REJECTED；不能为了让 baseline 显示已付款而预造完整 effect、改历史 hash 或重标 P 的成功。本修订允许原实际拒绝的 error.code/http_status/source ref，并允许原同事务效果前已有 attempt_action_id（尚未形成持久 ActionPlan/银行动作）；原 pipeline 引用仍绑定该同一 attempt，未到达阶段保留原未到达原因。拒绝 capture 的 response 必须为 null，任何塞入预造 PREPARED response/effect 都拒绝。

provider 可以在原同锁中先读取原现金/义务、调用 B0—B2 selector、保存 raw candidate，再调用原服务生成真正 policy/product/source/evidence 完整 effect。仅 candidate amount/cash source 可送入新 effect 前参数，原 funding_income/revalidate/autonomy/三阶段仍执行。raw unsafe candidate、真实 common gateway 拒绝、实际服务结算三类不能混用。原正常 preview 已阻断时，只捕拒绝，不把 unsupported baseline 或 unsafe amount变为真实成功；不支持的意图明确 NOT_IMPLEMENTED。此前61 PASS与SHA原件保留，新增窄输出合同检查另存，不覆盖旧结果。

新增 63 TOOL_TEST_ONLY PASS：`arm-common-rejection-v2-final-tests-20261005T055844Z-fd371da2/manifest.json`；Ruff=`arm-common-rejection-v2-final-ruff-20261005T055844Z-de3d2698`；mypy两文件=`arm-common-rejection-v2-final-mypy-20261005T055845Z-ac8e73f2`。当前 script SHA=`cbb8e361349bd968cec5106c5651d1487818c9c39936c0f36e400e2933a02e21`；test=`0aae7ac1a80d8247680674dacc346b26cee523daa3496222d93e89c82ceef6d8`。新两项测试保留原有 attempt/null 失败身份，并拒绝把 fabricated PREPARED response塞入真实拒绝捕获；仍不执行任何金融/DB/PG/浏览器。

## HISTORICAL_RESPONSE_CLOCK_V3 显式原件时钟修订

provider 对接发现：T1 准备的原 ActionResponse 若在 T2 确认/执行重读，不应要求旧 as_of 等于 T2 的 ctx.now。修订只核 `原 prepared_at <= 原 as_of <= 当前业务时钟`，保留 T1 原件 bytes/as_of/prepared_at/hash，拒绝未来或逆序原时钟。实际新候选 planning view 仍精确绑定当次 ctx.now，当前效期由原确认/执行服务重新核；允许解析旧响应不授予权限或延长效果有效期。另四项纯时间差量测试记录随后补充，不覆盖原61/63证据。

V3 最终工具验证（2026-10-05T06:24:17Z）：`arm-clock-v3-final-tests-20261005T062415Z-729906bb` 为 67 TOOL_TEST_ONLY PASS（0.97s），对应 Ruff `arm-clock-v3-final-ruff-20261005T062420Z-0ea8fa93`、mypy `arm-clock-v3-final-mypy-20261005T062423Z-37ad1bd4` 均通过，三个 manifest 的 all/scoped source 均稳定。仅历史 ActionResponse 时钟验证修订与四个纯测试：旧响应 `prepared_at <= as_of <= 当前业务时钟`，保留旧原件与经济 hash；未来/反序时刻仍拒绝。V1 61、V2 63 的证据、原件和 SHA 保留。当前脚本 SHA256 `5b5bcdd02dad6275d56d7c40b0a7efbc36dbf044f225fd212ef7caf7738b68f1`；测试 `1383b366241c4404d69812db5db12b6f8aebaf45d7bf100b7fd697c32a25c07b`。没有生产接线、PG、银行、浏览器或产品金融效果证据。
