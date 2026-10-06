# FULL-204 真实物理来源动作集 v2 显式修订

本包为可运行域/只读服务/API及原观察轨迹消费者；**原 FULL-204 验收仍未关闭**。实现状态与原验收分开。尚未由根注册新路由/算法/通知分支，新增真实 PG 候选仅 collection，金融/浏览器/全量均 NOT_RUN。

## 修订原因与旧证据保留

根真实 `W3/actual-finite-global-complete-original-diagnostic-20261006T001652Z-03c0e126` 的原失败保留：旧 v1 要求真实 schema 不存在的 `simulated_bank_ledger_heads`；seed 超过 200 条 Evidence 时原完整来源被截断，真实原输入也超过 512 KiB。旧 v1 和 full-v1 因真实来源/容量门保持 UNKNOWN，这不是已完成全局功能。`actual-finite-full-and-http-global-producer-20261006T001147Z-d9b45ecc` 及更早迁移/实际批失败也未改写、删除或拆成新成功。

新协议不生成“账本头”别名，不改变旧数学、200 行/512 KiB 旧限额、算法、原 hash、模拟历史或旧 frozen source。旧 v1、full-v1、whole-asset-family 原字节原封保留。**ACTUAL_ACTION_SET_PHYSICAL_SOURCE_V2** 是显式新执行/来源合同，不把新效果交给旧 global verifier。

## 实际来源、容量和范围

新算法 `full-policy-action-set-boundary-actual-v2`；scope `POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_V2`。同一干净 RR / READ ONLY Session 验实际 simulated User、当前唯一 OPEN epoch、完整当前审计及原 financial context，逐次 SQL 重新读取，不跨请求缓存授权。

完整 required 24 表是 users/accounts/policies/policy_versions/goals/asset_positions/asset_products/action_plans/action_receipts/bank_operations/simulated_bank_postings/simulated_bank_redemptions/external_bank_facts/transactions/credit_card_bills/action_resource_reservations/evidence_items/full_policies/full_policy_versions/full_policy_commands/product_catalog_versions/full_asset_execution_portfolios/full_asset_execution_batches/full_asset_execution_consents。附捕获实际已注册三张 command metadata 表。唯一 GLOBAL 例外为 asset_products/product_catalog_versions；users 仅实际 owner ID，其他表全 user_id 过滤。

每表先核 `to_regclass`，同 RR 快照独立 SQL count，再按 ID 捕获全原列、NULL、整数、JSON及 canonical TEXT。行数上限 4096，超限保真实 count 和 4097 sentinel，`complete=false`，不把首 4096 当完整。missing physical table 的 actual_count=null，不是“真实空表”。每表 row count/captured count/full row SHA、原引用/owner、必需表和来源分母进入原 input hash；无 phantom 表。

完整输入的新容量为 16 MiB，候选上限 64；超限 UNKNOWN且 action_set_signature=null。捕获限额是新协议事实，不代表银行吞吐实测。原 DecisionTrace 每 JSON 10 MiB、250000 nodes/depth32 与完整 trace 10 MiB，以及审计原限额依然生效；observe 不能截断原件绕开这些原持久合同。超过旧原 trace 上限时原事务拒绝/回滚元数据，不能声称完整观察已保存。

原 PolicyVersion.configuration 的 canonical `recurring_obligation` / `asset_authorization` 生成 payment/purchase（它们与原 Policy.policy_type 的 recurring_expense/asset_allocation 不同）。全部真实 Goal/Position 都保分母；只有实际撤销/暂停/失效/未授权手工持仓可证明排除。未支持的 producer、未知效果或未证明 Full future account scope 保 UNKNOWN。

实际 FULL307 调原 `read_full_dynamic_goal_inputs`、当前 FullGoalModel/base MVP 确认/收入/归属/月贡献/claims/365保护；冻结 typed inputs 后调原 `dynamic_candidate` 单候选数学重算，替换同 Goal nominal，保完整原 base bytes，不叠两个虚假机会。WholeAsset 每当前同 epoch FullAssetAuthorization × 匹配真实 MVP scope × PORTFOLIO/FIXED_LADDER 调原 preview、原完整 planning inputs/不可变目录/whole basis；逐候选用 `asset_producer_view` 重算，完整目录和所有 claims/income/source 仍绑定。Whole ASK 不伪装 AUTO，原单产品生产者仍单独分母。GENERAL FIXED_LADDER 无真实 Goal 的原规则排除有限且可解释。

## 正式调用接缝（根注册）

- 域 DTO: `ActualActionSetInput`, `ActualTableCoverage`, `ActualActionSetSnapshot`, `ActualGlobalBoundaryObservation`。
- `capture_actual_action_set(session,user_id,now)->ActualActionSetCapture(inputs,snapshot,originals:DecisionCapture)`，干净 RRRO，全调用 fresh。
- `read_current_actual_action_set(session,user_id,now)->ActualActionSetSnapshot`。
- `observe_actual_global_boundary(engine,user_id,GlobalBoundaryObserveRequest,now)->ActualGlobalBoundaryObservation`：原用户锁内登记；独立 RRRO 完整 current capture；仅 append 原 DecisionRun/审计元数据，零金融写。same key 完整 body 相同才重放，不借新 planner 改旧 outcome。
- `verify_frozen_actual_action_set_trace(trace)`：只接受精确新算法；原 input key **actual_action_set_input**，outcome key **actual_global_boundary_observation**；新域重新计算所有完整分母/每候选/原 signature。全 source copy/逐原 source hash/owner 和 policy config、previous original snapshot 严格核，不接受旧 v1 helper的成功。
- `read_actual_global_boundary_observation(session,user_id,run_id,now)`：原 get_decision_trace COMPLETE+实际审计 VALID；逐原父轨迹核冻结 previous snapshot；循环/64代超限拒。
- `actual_global_boundary_intervention_source(...) -> 原 FullIntervention.Source`：仅同新算法、实际完整、非 initial、有原 semantic_key 的观察；`attention` 仅完整 BoundaryCrossed。unknown/初次不生产通知。

新增 router `app.api.v1.full_action_set_boundary_actual.router`，路径 `GET /api/v1/boundary/actual-action-set/current`、`POST .../observe`、`GET .../observations/{run_id}`；GET prefix 须由根放入 RR READ ONLY 列表。POST 只 expected_epoch_id / previous_observation_run_id / key，严格 JSON UUIDReference、未知字段和任何 query 拒绝；客户端不能提交 amount/facts/authority/clock/result。

根须在 decision_trace/audit_chain 两算法白名单与 frozen delegate 添加 **exact ALGORITHM/version** 分支，不能把新 DTO cast旧 global DTO。再在 GLOBAL source/current/通知 producer显式添加新 DTO 和对应 read/source helper。observe API 当前无 Root postcommit 接线，notification_support 明示 ROOT_ACTUAL_V2_SOURCE_BRANCH_REQUIRED；根接线前通知仍未完成。

## 直接检查与原 RED

本包全部 synthetic 仅功能风险，不是正式冻结24、五臂金融实验、真人确认或独立经济效果证明。

| 命令范围 | 实际结果 | 原件 |
| --- | --- | --- |
| 首 direct | 19 PASS / 5 FAIL，90.67s；新篡改夹具在业务 verifier 前先被原通用 hash 拒绝 | W3/actual-physical-action-set-v2-first-direct-20261006T003131Z-ed24203f |
| 首 strict | FAILED 19：新局部变量复用及 fixture常量import | W3/actual-physical-action-set-v2-first-types-20261006T003132Z-05b69fec |
| 首 Ruff | FAILED 10：导入/unused/长行 | W3/actual-physical-action-set-v2-first-static-20261006T003132Z-e7eec9d3 |
| 最终 direct | **24 PASS，73.95s** | W3/actual-physical-v2-final-direct-20261006T003456Z-a7a3f8d0 |
| 最终 strict 五源 | PASS | W3/actual-physical-v2-final-types-20261006T003456Z-573e174b |
| 最终 Ruff 五源 | PASS | W3/actual-physical-v2-final-static-20261006T003456Z-65e8887d |
| 最终 format 五源 | PASS | W3/actual-physical-v2-final-format-20261006T003648Z-f36e7b36 |
| 原实际 PG 候选 collection | 1 node / 4.34s；**NOT_RUN** | W3/actual-physical-v2-pg-collection-only-20261006T003457Z-7c37d4e1 |

首五源原字节保存在 `.runtime/FULL-204-actual-v2/first-check-source-before-correction-20261006T003359Z`。最终篡改负例重算 generic trace hash 后依旧被 v2 语义重算拒绝；没有删负例、把旧 FAIL 改 PASS 或以已哈希代替完整来源。

直接测试包含真实物理清单、超过旧 200行/512KiB后同一307经济签名、16MiB容量保原原件且UNKNOWN、缺表/虚构表/完整count/duplicate/owner/audit/source/clock/动态分母否决、真SUSPENDED数学变化BoundaryCrossed、非经济numeric变化BoundaryObserved、整组资产ASK与nominal缺口保留、新算法拒旧轨迹、重新genericHash的伪outcome/count/source/parent/owner拒绝、公开JSON及query门、actual SQL count/sentinel/完整原列/NULL/TEXT/零写 doubles。

唯一根实际 PG 候选：`apps/api/app/tests/test_full_action_set_boundary_actual_integration.py::test_actual_physical_v2_dynamic_goal_silence_crossing_and_zero_financial_writes`。实际入口生成 FullGoalModel 与原收入/暂停，期望当前实际 Evidence>200而全分母完整、dynamic20000；两次真实不同收入同动作集仅Observed、真实暂停Crossed；原键/父链/Source、原EXACT审计、除观察元数据外全部 physical rows不变。这里只 collect，不预报通过、不虚造性能。

## 未覆盖

专用现金回拨、FullRecovery、Full周期付款、Full联合全局目标等尚需独立 actual complete typed-preview inputs 和原权限/审计 proof adapter；有效且未适配时明确 unsupported/UNKNOWN，不借任何 READY 字符串消除全集分母。非发起 arbitrary manual transfer 不属于自主生产者。任意容量超限、无法解释source、非经济证据外客户端探索世界都不能形成自主全集许可。

Root 新 API/白名单/delegate/真正 GLOBAL postcommit source与507 producer、实际 PG/new family合并、真实容量/时延测量、前端、原 FULL204 all acceptance 都未完成。本包不产生 action/Evidence/confirmation/bank/receipt，不扩大原权限，不跨请求缓存；原金融Pipeline/失败/正式历史均不变。
