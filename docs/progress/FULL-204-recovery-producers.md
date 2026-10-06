# FULL-204 真实恢复候选家族

2026-10-06。新增独立算法 `full-policy-recovery-action-producers-v1`、输入 `recovery-action-set-input-v1`。原 actual-v2/v1/full/periodic、604 经济命令与银行历史保持原字节。此包仅计算 `recovery_family_complete`；`full_global_adapter_installed=false`，不称全局动作集或 FULL-204 已全部验收。

## 实际可调用范围

`app.services.full_action_set_recovery_producers.capture_current_recovery_producers(session,user_id,now,*,original_actual_capture=None)` 返回 `RecoveryActionSetCapture(inputs,result,originals)`。`read_current_recovery_producers` 返回结果。调用必须是 clean 单 RR REPEATABLE READ / READ ONLY、当前实际 simulated User 与唯一 OPEN epoch、服务器可信业务时钟。默认每次 fresh 构造原 actual-v2 全来源；可选 capture 只供同一请求复用，owner/epoch/as_of 精确相同，不接受客户端财务原件，不跨请求缓存权限。新模块不调用 prepare/confirm/execute、不写任何资金、Evidence、Action、DecisionRun 或 receipt。

逐个读取当前 epoch 所有 RecoveryPolicy 的真实 FullLifecycle 原确认/当前版本/配置与完整当前源。复用原 `read_full_recovery_planning`、同原实际金融 context、365/1098 边界、immutable catalogue、所有原持仓、原 quote/current source，以及真实 `read_full_recovery_execution_inputs` 的独立 preview 输入。新候选必须由原 typed planner 完整重算，不只复制其状态字符串。随后仍使用原 `_facts`、revalidation/自治和完整 Full protection/future-account veto；不得从 Full planning confirmation 得到银行 grant。

当前实际支持 **GENERAL、单个整仓、T0、零费零损、原购买版本可核** 的 604 USER/ASK 候选。新 Full scope 的 deadline/金额/来源/目的/原产品版本/条款/风险、原 effect 和当前授权完整绑定；必须新的 exact 用户确认，旧 confirmation 不继承。当前候选仅有 `ASK_ONCE` 或原财务/权限明确排除，不执行资金。旧 MVP redemption 只有整个经济 effect 除 operation_id 外逐字段相同且唯一时可 shadow；不同 deadline、范围或任何经济字段均保留原候选，不能偷去另一机会。

## 完整原分母

- actual-v2 原全表、真实 count、逐表 rows hash、owner、audit/financial basis/source digest 全保留；16 MiB、每原表4096行、64当前 Full producer 是显式容量，超限 UNKNOWN。
- 所有 owned AssetPosition ID，包括 redeemed、manual、Goal、未选持仓都保留。原 planner 的所有未赎回 holdings 必须与原表完整一致，逐项核 immutable 原产品与条款/当前 catalogue 全量分母、原购买账户/本金/时间/归属/版本/银行事实及 quote。
- 原 execution-purchase-v1 购买须完整 command、原 BankOperation、posting、receipt 与原交易/Evidence 投影相同。旧手工购买缺 return_account_id 时只沿原 purchase_transaction_id 的 BANK DEBIT 指针求真实 owned 账户，严格核 transaction identity、owner、时间、方向、金额与本金合计；不补造返本账户。
- 原 MANUAL 且 null purchase version，只在上述来源完整且原 `_redeem` 明确拒绝无 original version 时记录 `authority_excluded_position_ids`。缺 source/不可证明授权不等于已否决；仍 UNKNOWN。旧 ASSIGNED/income/Goal 本金不变。
- 所有 REDEEM 与 ASSET_MATURITY 原动作、历史/在途命令都有 ID 与原 PREPARE trace 分母。每 trace 校验原 hash、owner、action/DecisionRun/request/BankCommand/validation。SUBMITTED/UNKNOWN/RESERVED、任何未核原 bank/receipt/投影均保留 unresolved，即使当前 Full 已暂停或撤销；不能释放旧 claim 或换键。
- SETTLED 必须有原 terminal action、完整 exact legs/receipt/transaction/Evidence 验真才能从 unresolved 去掉，status 字符串不证明终局。

完整原证据证明 `NO_RECOVERY_NEEDED` / `NOT_TRIGGERED` 可以 EXCLUDED，金额 null；所有持仓与原金额仍留在输入。T1、到期、部分、有损、Goal、组合、Full-only asset execution、缺当前原银行授权或未知价格等保 UNKNOWN，不借旧只读计划宣称可执行。独立完整家族不能消除其他 FULL unsupported family。

## Root 新组合/历史接缝

纯 `derive_recovery_producers(RecoveryActionSetInput)` 与 `verify_frozen_recovery_producer_inputs(raw)` 返回真实 typed 重算结果。结果携带原 actual input hash、新 input/result hash、全 Full policy/position/action 分母、unresolved、authority exclusions、唯一 shadow 和原 actual reasons。`handled_unsupported_codes` 只在该家族确实完整时列其 RecoveryPolicy 原代码；完整全局集合仍由 Root 新版本组合重算。

`verify_recovery_source_copies(trace,inputs,result)` 核原 actual-v2 来源/政策副本与所有新 Full planning、execution、权限、Full protection/evidence 引用。独立 `verify_frozen_recovery_producers(trace)` 只接受 `algorithm_versions.recovery_action_producers=full-policy-recovery-action-producers-v1`、EVALUATION、无 action_id，`inputs.recovery_action_set_input` 与 `outcome.recovery_action_set_result` 逐字段重算相同。旧 global verifier 不能因白名单字符串消费新输入。外层持久 parent/epoch/audit 仍须 Root 真实观察协议核验。

本包没有新 Main/shared 路由/通知/算法白名单写入。Root 可以在新 composer 的同一个 RRRO 请求传原 actual capture，并调用纯 derive/source-copy helper；必须严格核同一原 actual 输入与唯一 shadow，重新算全局分母、signature、UNKNOWN。新恢复工具 helper 本身不等于通知或采纳。

## 必要检查、失败与实际边界

原 strict10 RED、首 fixture24 FAIL（错用 AuthorityAssessment `VALID`）、后22PASS/2FAIL（fixture 缺 authority/source evidence、过期 quote 违反 strict 窗口）均保存原 source 与 FAILED manifest。修的是新 fixture/局部类型，没有改原权限、quote、seed、银行或负例。

`W3/recovery-producers-complete-funding-direct-20261006T030206Z-808635ef`：24 synthetic direct PASS/54.11s，exit0、scope stable=true；global=false，仅独立 Root native source 开发。不将 synthetic 夹具当 actual资金/真人/冻结实验成果。后续新增来源分母/旧成熟动作/银行 DEBIT/精确 new trace 风险，最终结果在收口附录追加。

`W3/recovery-producers-final-static-20261006T030859Z-d8b63dd5` Ruff4 PASS；`recovery-producers-final-types-20261006T030858Z-9cb9d58b` strict4 PASS。03:08 final direct 与实际 collection 在导入阶段失败：Root 当时新 FullProjection→current-seasonal→ended→FullProjection 循环；`667fb2ba` 与 `0abd0dd0` 原 FAILED 保留。没有金融/PG执行；待 Root shared 修复后直接风险与 collection 需重新完成。

唯一 Root-only actual 候选：`test_full_action_set_recovery_producers_integration.py::test_actual_complete_t0_recovery_producer_ask_original_365_sources_and_zero_writes`。生成 bf_test、原实际 LIQUID purchase 与真实 FullRecovery 确认、原实际缺口→完整当前 getter→原365/1098/金额/来源/ASK/no-authority→EXACT audit VALID→全 physical 零写/同请求旧输入字节不变与独立请求 fresh 重建。**实际 PG NOT_RUN**，collection 不产生数据库、seed 或资金效果。

未覆盖：Root 新 global composer/实际 observe/通知接线与实际链、T1/成熟/部分/损失/Goal/多仓执行机会、已不能完整核验的旧 legacy trace、真实性能/正式 corpus/最终67全量验收。已有实际604银行能力的证据不能代替此新独立完整分母 producer 的 PG 验证。

## 最终检查附录

- Root 仅新投影模块把 current/ended 导入移到 DTO 定义完成后的函数内，旧 ended-v1 四源无变化。随后 `W3/recovery-producers-new-frozen-risks-import-repaired-20261006T031049Z-9d96ff9a`：37 direct PASS/78.96s，exit0、scope stable=true、global=false（独立前端开发），没有 PG。
- 最后增加真实孤立 BankOperation/SimulatedBankRedemption 不可从空 action 分母漏掉的拒绝门、任何现代命令关联 legacy redemption 行保持 UNKNOWN。`W3/recovery-producers-final-orphan-originals-delta-20261006T031334Z-ad21c8cf`：两个新负例与 T0 正例3 PASS/9.74s、36 deselected，exit0、scope稳定。没有把39个用例声称为新整批全过，也没有重复未变风险。
- `W3/recovery-producers-final-denominator-types-20261006T031335Z-62b73306`：最终4源 strict mypy PASS；`recovery-producers-final-denominator-static-20261006T031335Z-cfe8e4f2`：最终4源 Ruff PASS；均 all/scoped source stable=true。owned4 format完成。
- `W3/recovery-producers-actual-collection-import-repaired-20261006T031049Z-a1e7ad9b`：唯一真实候选1 collected/4.74s，all/scoped stable=true；**actual NOT_RUN**。其后仅 domain 历史孤立分母与纯测试增量，没有改变 actual candidate body、原银行/执行/API 或金融默认。

当前四个新源及本文可冻结为 `FUNCTIONAL_RECOVERY_ADAPTER_IMPLEMENTED_ACTUAL_AND_GLOBAL_COMPOSE_PENDING`。完整 Recovery 输入/结果、新 frozen helper 与 Root 新 v4 composer 接缝均可调用；没有写注册源、原运行器、共享财务或正式库。所有旧失败保持 FAILED，编号最终关闭仍待真实范围证据。
