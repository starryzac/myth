# FULL-204：有限全局自主动作集观察

实施状态：原策略生产者有限范围可运行，直接检查通过；真实 PG 未运行。原 FULL-204 **未关闭**。

## 可运行能力

新增 `full_action_set_boundary` 域、服务、路由。从服务器固定模拟用户、当前 OPEN 审计轮次、同一 RR / READ ONLY 快照读取完整原行，重新生成原 recurring payment、Goal allocation、asset purchase、逐持仓 redemption 的规范候选。不是把已保存 ActionPlan 列表称为全部机会。

有限范围名为 `POLICY_BACKED_SERVER_PRODUCERS_V1`。每个当前原策略产生一个既有确定性规划器的规范候选，原全部持仓分别登记。用户未明确提出的任意手动金额、目的账户与收款人意图不属于代理生产者，`arbitrary_manual_intents_covered=false`。原持仓明确 `policy_version_id=null` 时，仅从自主生产者集合排除；不宣称手动恢复已覆盖。

原库存含 users、accounts、policies、policy_versions、goals、asset_positions、两个实际 GLOBAL_CATALOG 表、原 action/bank/posting/receipt/resource reservation/evidence，以及 FullPolicy 三表；加载的 FullAssetExecution 和 Command 表也保留完整所有者原行。全部列、整数分、NULL、原 JSON、原哈希保留。每表上限 200、规范候选上限 16、持久观察完整输入 512 KiB；超限保持 UNKNOWN 或拒绝保存，不能截掉分母后声称完整。

每个有完整原件的候选重算原 `revalidate_execution`、原自主级别及 FULL 保护结果。比较类别、金额、现金与收入来源、目标归属、目的、产品/持仓、费损、期限可用性和权限级别。数值来源变化而同规范动作集不变是 `BoundaryObserved`；已完整验真的集合改变是 `BoundaryCrossed`。完整性不足时签名、事件种类和语义键均为 null。初次完整观察没有 before，保存 `BoundaryObserved`，不通知。

POST 只追加原 DecisionRun 与其原审计材料，不创建金融行动、确认、银行操作、投影或问询。原完整输入/结果保存于 `decision-trace-v1`；读取重算旧数学、核完整原来源副本，并逐层对照实际父观察原轨迹（上限 64）。同 owner/epoch/key 重放返回原观察；同键不同完整请求拒绝。无跨请求权限缓存。

## 文件与接线

- `apps/api/app/domain/full_action_set_boundary.py`：严格 DTO、原分母重建、候选原经济绑定和签名比较。
- `apps/api/app/services/full_action_set_boundary.py`：实际只读捕获、原数学复算、观察原键持久与原历史读取、GLOBAL 通知来源 helper。
- `apps/api/app/api/v1/full_action_set_boundary.py`：新路由；未擅自注册 Main 或改变共享依赖。
- `apps/api/app/tests/test_full_action_set_boundary.py`、`test_full_action_set_boundary_api.py`：明确 synthetic / doubles 直接风险。
- `apps/api/app/tests/test_full_action_set_boundary_integration.py`：根任务独占生成库 PG 候选；作者只 collection，未执行。

根任务后续注册：

| 路径 | 行为 | 依赖要求 |
| --- | --- | --- |
| GET `/api/v1/boundary/action-set/current` | 实际有限集合与完整性 | 原 fixed owner / clock；RR READ ONLY |
| POST `/api/v1/boundary/action-set/observe` | 元数据观察 | 严格 `expected_epoch_id`、可空 `previous_observation_run_id`、原 `idempotency_key`；没有金额/事实/时钟参数 |
| GET `/api/v1/boundary/action-set/observations/{run_id}` | 原历史观察 | 原 fixed owner；RR READ ONLY；不授当前权限 |

服务接口：`capture_current_action_set(session,user_id,now)` 返回私有完整 `ActionSetCapture`；`read_current_action_set` 返回紧凑 DTO；`observe_global_boundary(engine,user_id,body,now)` 保存原轨迹；`read_global_boundary_observation(session,user_id,run_id,now)` 验真原历史；`verify_frozen_action_set_trace(trace)` 是无 SQL 的冻结数学/来源绑定复核。

共享接缝：原 `services/decision_trace.py` 与 `domain/audit_chain.py` 的版本白名单须增加 **新的** `full-policy-action-set-boundary-v1`，并在新算法分支调用冻结复算 helper。旧算法/哈希路径不改。未接这些分支前，旧 reader 会正确判 `UNSUPPORTED_VERSION`；不能把未接入路径说成已实际验收。

通知接缝：`global_boundary_intervention_source(session,user_id,run_id,now)` 只接实际完整原父链；返回现 `full_intervention.Source`，payload 为未改写原 GlobalBoundaryObservation，`attention` 仅由完整 `BoundaryCrossed` 推导。根任务须新增真正 `GLOBAL_ACTION_SET_BOUNDARY` source kind/producer 分支，保留旧 QUESTION / SINGLE_ACTION_BOUNDARY 默认协议；不能重标签旧单动作事件。初次/UNKNOWN 观察拒绝作为 GLOBAL 通知来源，数值静默观察可读而不要求 attention。

## 实际检查与原失败

最终命令均由 `scripts/run_scoped_check.py --task W3` 记录实际源码/HEAD/日志：

| 范围 | 原结果 | 证据目录 |
| --- | --- | --- |
| 两新直接文件 + 原 boundary_action_events 域直接风险 | 44 PASS，36.14s | `docs/progress/evidence/W3/global-action-set-final-direct-20261005T232420Z-12809d8d` |
| 六 Python 文件 `mypy --strict` | PASS | `docs/progress/evidence/W3/global-action-set-final-types-20261005T232420Z-e8845ef5` |
| 六文件 Ruff | PASS | `docs/progress/evidence/W3/global-action-set-final-static-20261005T232420Z-9b2f2588` |
| 单 PG 候选 `--collect-only` | 1 collected；不是运行 | `docs/progress/evidence/W3/global-action-set-actual-candidate-collection-20261005T232420Z-c47e6807` |

首轮 6 FAIL / 28 PASS 与六类型错误保留在 `global-action-set-direct-first-20261005T231644Z-5687145b`、`global-action-set-types-first-20261005T231644Z-42e7c686`；主要实现错误是原版本 UUID 集合与原行字符串键比较，导致正确候选被判 UNKNOWN，相关来源负例未到完整分支。修为严格规范 UUID 字符串比对。首 Ruff import 排序失败原件在 `global-action-set-static-first-20261005T231645Z-2af834ff`。

第二轮 42 PASS / 1 FAIL 原件 `global-action-set-direct-repaired-20261005T232029Z-4ff96564` 保留：自引用轨迹已被原 strict trace constructor 拒绝，测试错误期待能先构造，再由新 reader 拒绝；现断言构造器原拒绝，没有删除负例。第二类型三错原件 `global-action-set-types-repaired-20261005T232029Z-3bf884f7` 保留。修前五原文件字节已存 `.runtime/FULL-204/before-second-repair-20261005T232221Z`。首轮 source manifest/hash/log 保留，但首轮修前没有另存全量源字节副本，不宣称有该副本。

所有 synthetic / doubles 检查只证明程序风险门，不是金融效果或正式 corpus 实证。本包没有 PG、浏览器、全量或正式历史写入。

## 真实候选和未覆盖项

唯一必要 PG 候选：

`apps/api/app/tests/test_full_action_set_boundary_integration.py::test_actual_finite_set_numeric_silence_crossing_replay_and_zero_financial_writes`

候选使用原 Goal 确认/创建、原 suspend 生命周期和实际 ExternalFact INCOME 入账；在其真实规范自主生产者范围核 current GET 零写、两次收入数值变化而规范 Goal 额度不变静默、原策略暂停产生集合变化、原键完整重放、全部非观察元数据物理表不变、原 EXACT 审计 VALID。其结果当前 **NOT_RUN**，没有填造通过值。根任务须先完成上述共享/路由接线再串行实际运行。

具体缺口：当前有效 Full 可执行模板（包括组合资产、专用 Goal release、周期 Full 支付、联合/跨目标调度等）以及有效 `FULL_GOAL_MODEL_V1` 动态生产者尚未适配；库存见到这些就保留全 family 分母和 `*_PRODUCER_ADAPTER_MISSING`，整体 UNKNOWN。未生成原经济 Effect 的泛化规划失败也保持 UNKNOWN，不把成功标签/空数组推成零集合。不存在未来收入扩大今天边界的路径。

当前实现不能关闭完整版全局所有生产者、真正 GLOBAL 通知、全部原 FULL-204 风险条件、前端或全量验收；下一依赖是根任务共享白名单/数学核验和通知接线、唯一真实 PG，以及各 FULL 服务规范候选适配。既有正式失败、资金/银行历史与旧单动作边界协议不改。


## 2026-10-06 08:26 Root 接缝与实际证据

2026-10-06 08:26 北京时间：604 前端41直接风险/API8源和Root真实当前策略宿主均已交付；原pending与PLANNED/UNKNOWN工作区跨页阻新写/演示reset/登出，列表读取失败仍有独立原GET。Root最初18相关PASS9.80s，纯账户摘要夹具types FAILED2原件留存；窄补实际DTO后受影响5PASS8.63s、整体Webtypes b9f79960和5文件lint7c79cf22 PASS。Root五源FINAL 4bb74a52，708集中样式和房租窄屏说明已FINAL55288bc4，实际CSS/Vite build通过；Root跳导航不改hash、路由改变focus当前内容三case内已核；真实手机/键盘/屏幕阅读器/对比度仍NOT_RUN。

0014实际migration首命名约定重复prefixERROR8.47s保留，两个drop仅op.f窄修后actual1PASS11.26s，FINAL683e4ef7；旧0012/0013/已有行/哈希不改。204v1 actual原节点FAILED13s，后诊断FAILED16.34/14.72s均留；根因实际无 simulated_bank_ledger_heads 表、证据200行截断漏refs、512KiB不足。v1/已冻结Full-v1继续UNKNOWN，不能造表别名或提高旧版本容量改历史。独立显式 actual-v2使用真实表/全counts/现原schema type进行修订，307+真实asset family/math接新版本，原FAIL不改。后Full-v1与HTTP自动通知节点仍NOT_RUN。

307 actual原preview409 DYNAMIC_GOAL_V2_LEDGER_REQUIRED，FAILED12.91s；实际诊断FAILED13.49s证明原nativeV2唯一差异as_of `+00:00`与typed输出`Z`，同一时点。Root仅改新动态消费者及新历史验证接缝为严格原生IncomeLedger解析后全值相等；原JSON、原source hash、所有整数/源身份/归属/预约/权限/clock门和旧nativeV1拒绝不改，不写规范化原件。新增7反例和原35direct/4strict正在跑；准备后的真实银行/原键恢复仍未得结果，105/604后两节点NOT_RUN。当前无Root金融RUNNING，唯一shared财务负责人仍Root；新v2/102/材料独立并行。正式关闭21/92/FULL原项PENDING、真人0/NOT_STARTED、新性能NOT_MEASURED，最后集中验收尚待。

