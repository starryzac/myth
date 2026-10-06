# FULL-204：实际 FULL 动态目标生产者差量

实施状态：独立 FULL 集合模块已实现；直接风险和接线检查见下表。真实 PG 与 GLOBAL 通知共享接线由根任务串行安排，未在本包运行。原 FULL-204 **未关闭**。

## 可运行范围

新 `full_action_set_boundary_full` 域、服务和独立 API 保留原 v1 六源及全部原输入。当前 finite scope 是 `POLICY_BACKED_FULL_SERVER_PRODUCERS_V1`，algorithm 是 `full-policy-action-set-boundary-full-v1`。

实际 `FULL_GOAL_MODEL_V1` 来源按 Goal 汇总完整原 Evidence 分母。当前生产者在同一实际 RR / READ ONLY Session 中调用 `read_full_goal_model` 与 `read_full_dynamic_goal_inputs`，重新读取当前原 MVP Goal 月度 `[min,target,max]`、完整原 FullModel/Evidence、银行已验证收入碎片、Goal 归属、当月贡献、全部他动作占用和完整 365 天保护来源。权限来自原 `_authority`，没有将 FullModel 规划确认变为银行许可。

冻结数学使用原 `derive_full_dynamic_goal_proof` / `build_full_dynamic_goal_effect`，原 `revalidate_execution(full_dynamic_goal_proof=proof)`，原自主级别与 FULL 保护否决。动态生产者替换唯一 `goal:{GoalId}`，不同时保留一个名义额度和一个动态额度。150000 超原 nominal 100000 的直接夹具只能依靠实际原已确认 max=150000 的 proof 通过；旧无 proof 路径仍 BLOCKED。HTTP 不接受这类私有 inputs。

原 raw inventory 的模型与收入原件全文/内容哈希、owner/epoch/clock、完整模型 IDs、原精确 PolicyConfirmation 的版本/owner/reviewedHash/accepted/时窗和证据分母均重核。当前完整收入 ledger 必须等于原基线收入全文；现金/Goal/持仓 RESERVED 原分母与 legacy income claim 精确相等；不排除任何 own claim。FULL 保护全 policy 分母与当前原版本/配置/原确认绑定，当前 proof 仍另保旧权限、原最低与期限独立否决。缺原件、UNKNOWN 或保留 FULL 家族未实现时，集合签名和事件为 null。

每表 200、规范候选 16、完整观察输入 512 KiB、父链 64 层上限保留。超限保持 UNKNOWN；保存观察不裁剪完整原输入。无跨请求权限缓存。当前 getter 不生成金融 Action、Evidence、confirmation、bank row、reservation 或 receipt；POST observe 仅追加原 DecisionRun / 审计元数据。

## 精确接线

三生产源（原 v1 不变）：

- `apps/api/app/domain/full_action_set_boundary_full.py`
- `apps/api/app/services/full_action_set_boundary_full.py`
- `apps/api/app/api/v1/full_action_set_boundary_full.py`

| 新路径 | 返回 / 行为 |
| --- | --- |
| GET `/api/v1/boundary/full-action-set/current` | `FullActionSetSnapshot`；根依赖设置 RR / READ ONLY |
| POST `/api/v1/boundary/full-action-set/observe` | `FullGlobalBoundaryObservation`；原 `GlobalBoundaryObserveRequest` 仅 epoch、previousRun、key |
| GET `/api/v1/boundary/full-action-set/observations/{run_id}` | 重核原完整数学、来源副本、真正实际父链；不是当前金融授权 |

私有接口：

- `capture_full_action_set(session,user_id,now,base:ActionSetCapture|None=None) -> FullActionSetCapture`。可选 base 只能是本请求同一个 RRRO Session 的实际 v1 捕获；服务不跨调用缓存。返回 `inputs: FullActionSetInput`、`snapshot`、`originals: ActionSetCapture`。
- `FullActionSetInput={base:原完整ActionSetInput,dynamic_goals:[candidate_key,model_evidence_ids,data:完整FullDynamicGoalInput|null,authority:原AuthorityAssessment|null,excluded_by_current_policy,missing_reasons]}`。原 base bytes 的语义/哈希保留，不把新的动态效果假装成旧 v1 数学。
- `derive_full_action_set(inputs)` 无 SQL 冻结重算；仅内存 evaluation shadow 复用原 v1 身份/源库存检查，保存的 base 不改。
- `read_current_full_action_set(session,user_id,now)` / `observe_full_global_boundary(engine,user_id,body,now)` / `read_full_global_boundary_observation(session,user_id,run_id,now)`。
- `verify_frozen_full_action_set_trace(trace:DecisionTrace)` 重算 `trace.inputs['full_action_set_input']`；原 outcome key `full_global_boundary_observation`。每个实际消费的 dynamic source 必须有原完整 trace source copy。previous snapshot 必须逐层等于真实父 trace，而非仅传一个候选 before 数字。
- `full_global_boundary_intervention_source(...) -> 原 full_intervention.Source`。只有实际完整非初次原观察才可作为 GLOBAL 来源；`attention` 仅来源于完整 BoundaryCrossed。不是旧 SINGLE_ACTION 来源的重标签。

根任务负责新增路由、GET RRRO 前缀、`decision_trace` / `audit_chain` 的新算法白名单及新算法冻结 helper delegate。通知 producer 必须按这个独立 DTO / algorithm / scope 显式分派；不把新 FullGlobalObservation 填进旧 v1 DTO。旧分支、旧金融哈希、正式历史和原失败全部保留。

## 检查及证据

所有 synthetic / doubles 仅程序风险证据；不是银行效果、正式 corpus 或真人确认实证。新增 tests 不运行 PG。

| 范围 | 实际结果 | 证据 |
| --- | --- | --- |
| 首个 dynamic 直接风险 | 23 PASS，36.20s | `W3/full-action-set-dynamic-first-direct-20261005T234705Z-bd89f511` |
| 首严格类型 | FAILED：局部 UUID/string 变量同名及 nullable content 类型，共 3 错 | `W3/full-action-set-dynamic-first-types-20261005T234705Z-286838e9` |
| 首 Ruff | FAILED：新测试 import 排序 | `W3/full-action-set-dynamic-first-static-20261005T234706Z-ec5cc503` |
| 修正后六源 strict mypy | PASS | `W3/full-action-set-dynamic-final-types-20261005T235137Z-2d007689` |
| 修正后六源 Ruff | PASS | `W3/full-action-set-dynamic-final-static-20261005T235137Z-36c02a88` |

首类型/排序修正前 domain/test 原字节已存 `.runtime/FULL-204-full/before-first-type-import-repair-20261005T2349Z`；三个原 manifest/log/hash 不改。新增 direct/API 总结果、format、PG collection 与 final 源 archive 在终态后追加，未完成命令不填通过。

唯一实际 PG 候选（当前 NOT_RUN）：

`apps/api/app/tests/test_full_action_set_boundary_full_integration.py::test_actual_full_dynamic_goal_replaces_nominal_observes_numeric_silence_and_policy_crossing`

候选使用现有真实 FullGoalModel 入口、原 suspend 与 ExternalFact INCOME；完整 current GET 为唯一 dynamic Goal 20000 而原 nominal 10000，两次真实收入源变化但同动作集保持 BoundaryObserved，原暂停导致 BoundaryCrossed，读原父链和原键重放，除观察元数据外全部实际物理原表不变，原 EXACT 审计 VALID。只有根任务实际运行后才能登记金融证据。

## 完整未覆盖项与下一依赖

本差量真正覆盖当前原 MVP 规范生产者加已确认当前 307 动态目标。现有原 v1 仍可单独使用；本差量不以现有 ActionPlan 集合代替所有机会。

组合资产 / FIXED_LADDER、专用回拨、FullRecovery、Full 周期付款、Full 联合目标调度等 family 尚未进入新的冻结完整输入消费者。当前有效原 FullPolicy 都保原 `FULL_PRODUCER_ADAPTER_MISSING:{template}:{policy}`，整体仍 UNKNOWN；实际规划的成功字符串不会消除分母。动态现版本来源缺失 / 不唯一、上下文异常、复杂 unknown claim、未证明 future account debit 等也保 UNKNOWN，不能据此发全集 BoundaryCrossed。

下一工作继续各 family **实际 read-only preview + 原完整 typed inputs 冻结重算**，优先实组合资产、专用回拨/恢复；需要新有限模板 permission/独立用户 actor 时不能伪装成 AUTO。任意未发起 manual transfer、用户可随意输入的金额与目的不属于 server autonomous producer（`arbitrary_manual_intents_covered=false`）。GLOBAL 通知真实链、全部原 FULL204 风险/前端/集中全量验收均仍未关闭。

## 最终直接检查终态

新域/service+API doubles 两直接文件实际 **27 PASS，51.10s**，原件 `W3/full-action-set-dynamic-final-direct-20261005T235137Z-86ae57c2`。六文件 strict mypy、Ruff、format-check PASS；单一真实 PG 候选实际 collection 1 node（3.79s），不是运行。五命令的六源 before/after/current byte SHA 全部相同；全仓/精确范围稳定性逐项见冻结 manifest，不能将 scope 替代全仓。所有原类型/排序 FAILED 保留。

最终冻结原源目录：`.runtime/FULL-204-full/final-source-20261005T235422Z`。包含源原字节、每份 SHA、HEAD、五个真实检查 manifest SHA 与未运行边界。源码 FINAL 可由根任务注册并只串行运行该真实 PG 候选；没有升级银行效果、GLOBAL 实际通知或 FULL204 关闭状态。
