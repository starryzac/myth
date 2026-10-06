# FULL-307 原生收入与证据上下文接缝（2026-10-06）

## 完成内容

功能优先修订下补齐真实动态目标预览/准备/确认/银行链。原生V2收入原件与严格类型化收入按同一真实时点和值比较，保留原JSON与哈希。新动态目标证明的生产者执行上下文可比原消费者多核过若干资产暴露证据ID；专用接缝只并入生产者已引用、当前原件再验一致的ID，所有资金、账本、归属、产品、目标、策略、预约、时点、确认权限、原证明哈希仍精确匹配。

原FULL编号保持PENDING；这不是原初版或完整版全量验收。

## 新增或修改文件

- domain/full_dynamic_goal_execution.py：native_income_original_matches 与专用 bind_full_dynamic_goal_consumer_context。
- services/full_dynamic_goal_execution.py：原生V2原件比较，不更改历史原件。
- services/execution_context.py：require_full_dynamic_goal_proof_context，严格当前原件身份/内容哈希/有效期核对与原捕获。
- services/execution.py、execution_sources.py、execution_bank.py：仅新动态目标分支接缝，原准备/确认/提交/独立受理/保护验证。
- test_full_dynamic_goal_income_original_seam.py、test_full_dynamic_goal_context_evidence_seam.py：语义等价与篡改/未知原件/重复引用/金融和权限改变拒绝。

## 关键设计选择

不删除或弱化原完整context_hash校验；不重写历史哈希，不改原证明DTO/输入。新消费者上下文必须与生产者除exposure.evidence_ids外逐字段精确相同。消费者ID必须为生产者ID子集，生产者ID必须存在同属主原source_refs，引用不得重复。所有源的当前有效原件与源哈希须重新验真后才绑定；后续原确定性证明重算仍运行。

## 已运行命令与结果

- W4/dynamic-native-income-original-seam-direct-20261006T002459Z-647141ee：35 PASS（原28+新增7），10.83s；4源strict/静态通过，范围稳定，全仓库其他新源码变化。
- W4/actual-dynamic-native-income-seam-and-original-key-20261006T002609Z-54f82e70：真实隔离PG FAILED 1，21.69s；预览200、注入422与只读零写断言通过，准备409 CURRENT_DYNAMIC_GOAL_PROOF_INVALID，未执行银行资金动作。
- W4/actual-dynamic-original-context-diagnostic-20261006T002902Z-b072897b：真实PG FAILED 1，20.33s；完整JSON诊断仅exposure引用差（生产者24、原消费者19），金融和权限字段全部相同。
- W4/dynamic-original-context-evidence-seam-direct-20261006T003800Z-64c3915b：RUNNING；选入新风险及原执行上下文直接相关真实PG风险，结果待终端。
- W4/dynamic-original-context-evidence-seam-types-20261006T003810Z-6e389418：FAILED 1个新测试可空暴露类型分支；5生产源无类型错误，原失败保存，待直接命令终端后仅修夹具并重跑。

## 尚存限制

动态目标原准备/確認/独立银行受理/响应丢失原键恢复尚未获真实PASS。原用户四腿固定付款的真实2 PASS不替代这条链。FULL-105与604后续实际候选仍NOT_RUN；不对真人/新性能/原版本全量补造结果。所有失败日志及失败源码归档保留。

## 下一任务前置条件

先等当前直接风险终端、修可空测试类型、重跑受影响风险与strict；冻结源码后单独执行原307真实候选，发现失败只修直接接缝。该金融命令运行时共享Main/模型/迁移/原资金/审计保持冻结。随后按依赖接入完整证据图与全局动作集合新版本，再集中版本验收。

## 08:44 后续终态与冻结

上述直接命令已终态PASSED：64 PASS/82.12s（含原执行上下文10真实PG风险），全源及范围稳定均true。类型仅测试可空分支窄修，生产行为未改；8源strict PASS `dynamic-context-seam-and-frozen-new-routes-types-20261006T004006Z-f742ed98`，11个受影响证据接缝风险PASS/8.40s `dynamic-context-evidence-seam-corrected-fixture-risk-20261006T004215Z-1bc925e0`，13源Ruff PASS。旧typeFAILED原件及精确夹具在 `.runtime/root-full102-actual204-and-context-fixture-before-20261006T0040Z/`。

Root16源码FINAL `.runtime/root-dynamic-context-and-actual-v2-shared-final-20261006T0044Z/manifest.json` SHA `e0baf71ed33674ae0146b4c35ba132fb64fc94e6df38d1ef8c01d5d1954a6820`。新FULL102实际35表证据图和actual-v2动作边界路由已注册，只读GET各由原RRRO；exactactual-v2历史重算/typed通知联合/current与源分派/postcommit已接。真实OpenAPI生成child0/正常SOURCE_CHANGED `a75864ba`，独立--check `aee641ba` PASS，合同匹配当前API。

当前唯一Root金融真实候选 `W4/actual-dynamic-native-income-and-bound-context-original-key-20261006T004341Z-5985f7c9`（session52587）RUNNING；先等终端，不以中间准备或银行结果宣称全链通过。所有共享金融、Main/deps、原算法/模型/迁移/审计及已注册源HOLD。后续105/604与102/actual-v2实际节点仍NOT_RUN。

## Root 真实资金链终态（08:48）

`W4/actual-dynamic-native-income-and-bound-context-original-key-20261006T004341Z-5985f7c9` 终态PASSED/exit0：1真实PG PASS265.34s、wrapper268.714846s。相关scope稳定true，全仓稳定false仅独立新通知测试/前端模块变化。真实20,000分动态额度大于原名义10,000分且受原min/max、nativeincome/cashuses/当前保护/双原hash/权限/USER同意约束；原独立银行SETTLED、响应丢失UNKNOWN原key查回/恢复、撤销后原receipt回读、防重复execute/prepare、完整audit/源链/物理零写尾部通过。实际scope包括原financial_read.py正确拼写，无生产银行篡改或回放旧哈希。保留先前全部失败和精确源码。

本次仅原当前当月动态目标完整资金链；非多期/全联合/所有目标/真人研究/新性能/初版或完整版集中验收。正式FULL307 PENDING。
