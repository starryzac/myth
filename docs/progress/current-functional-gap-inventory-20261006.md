# 当前功能缺口清单：FULL 67 项（2026-10-06）

本次是**只读功能审查**，不是验收或关闭记录。范围为原追踪表 FULL-001—FULL-907 的全部 67 项；原编号与 PENDING 状态不改，正式关闭仍以原追踪表为准。依据原《钱途有界_完整开发计划_Codex执行版.md》及其逐项行号，读取当前单项进度的后续增量、实际生产实现/路由与原结果文件；未将旧 `functional-backlog.md` 或单项文档早期的 NOT_RUN 当作当前结论。

观察 HEAD：`4ccf84e973978482a1098d18c69fbfc9f011fac6`；工作区包含其后的未提交成果，HEAD 不能代替源码字节版本。读取窗口为 2026-10-06 00:44—01:03 UTC 附近，最终源摘要见末尾。未运行测试、PG、浏览器、Docker、seed/reset、金融操作或安全扫描；只新增本文件。Root 独占实际金融链和共享源码，HOLD 由 Root 管理，本清单不解除锁。

下表“已有”指存在具体可调用实现，并说明有限范围；**不表示全部原要求完成**。“功能差量”与“验收缺证”分列。规划接口的 `execution_support=NOT_IMPLEMENTED` 本身不等于缺规划能力；已有独立消费者时不能据旧规划 DTO 宣称无执行。UNKNOWN/null、容量边界与合法拒绝也不是缺功能，但原要求范围尚未被任何消费者支持时仍是功能差量。工具、合成夹具、材料、真人和资金原件严格分层。

## 建议先分工的五个产品差量

| 优先 | 真实断点与影响编号 | 当前源码证据与最小交付 | 建议边界 / 前置 |
|---|---|---|---|
| 1 | **全局集合未消费已实现的 FULL 家族**（204/507/705） | actual-v2 已解决真实表/证据截断/旧容量问题，已消费真实动态 Goal 与 whole-asset；`domain/full_action_set_boundary_actual.py::derive_actual_action_set` 仅从 unsupported 清单移除这两类。当前活跃 Recovery、Periodic、CrossGoal、GoalAllocation、Dated 等仍令全局 UNKNOWN。需要逐家族实际完整分母、当前权限/风险与原输入冻结重算；Dated 保护规则不应被错误当成必须执行的 producer。不是再提高旧 v1 cap。 | 独立服务家族适配器可分工；Root 单点接新精确版本、历史 verifier/通知。依赖现实际 Recovery/Payment/Release/Joint 原读者，保同一 RRRO、UNKNOWN 与全部 claims，不新增资金行动。 |
| 2 | **修改预览没有完整的金融消费者**（105/104/206/703/704） | `domain/full_policy_change_impact.py::project_full_policy_change` 只支持 Dated/Periodic；其他模板 `TEMPLATE_NOT_SUPPORTED_FOR_FINANCIAL_CHANGE`。目标未来分配、持仓未来处置和动作生成仍 UNKNOWN。需复用实际 Joint/Asset/Recovery/动态规划，返回前后原输入与真实差量，保持假设不授权。 | 可按模板拆独立纯差分/来源桥；Root 接生命周期/API/确认后的重算。现 Dated/Periodic 能力保留，不能用当前零写事实差分 0 冒未来影响。Root 已安排未来 Dated 必要实链；它不等于其他模板已覆盖。 |
| 3 | **节日建议没有显式采纳到保护金额的来源桥**（108/201/701） | `policy_suggestions.py` 有官方有限日历、完整历史、整数分建议；Full 生命周期可以持久声明/确认 Seasonal 配置。但 `full_protection_projection.py` 仍只有 `ADVICE_ONLY_NO_ADOPTED_AMOUNT`，采纳额 null；`SpendingEvidencePage` 只读展示，无建议原件→明确采纳→重新验真保护路径。需要绑定窗口、原来源/算法/hash、用户选定金额与当前版本，首次确认后才计保护。 | 独立采纳合同/服务/UI可并行，持久模型/迁移/保护消费者 Root 集成。历史不足继续 UNKNOWN；不能把建议 cap 或空历史当已采纳金额，也不能修改 seed 分类/原银行事实。 |
| 4 | **FULL 恢复计划的全部动作尚不能被实际消费**（604/405/603/607） | 新 FullRecovery 执行已接原 prepare/confirm/reserve/首次银行/历史，当前仅**整仓、零费损、T0/T1、USER ASK**。`full_recovery_execution.py` 明确排除 MATURE/GOAL/部分/多步/有损；仅新增 FULL 保护导致旧 MVP 无负点时原安全改善门仍拒绝。原 MVP 早退 ASK 已存在，不能因此声称 Full 组合也支持。 | 先为真实 Full 缺口消费完整保护与计划，再按原 effect/原键串行固定步骤；部分/有损/Goal 需明确新协议或原条款合法 adapter，Root 单点集成 bank/projection/audit。不许靠放宽旧 validator 或承诺跨银行回滚补齐。Root 正排实际新链；成功仅改变该范围证据。 |
| 5 | **审计浏览器仍无原需求的动作搜索/版本筛选**（706/102） | `DecisionTracePage::DecisionList` 与 `api/v1/decisions.py` 只有 cursor/limit 分页；Full 原件图提供 kind/UUID 导航，不是按动作/策略版本查全原记录。需 GET 服务端 exact action/key 与 typed version refs 的受限检索，保实际 owner/epoch/知悉时间、总分母/容量及无法验真原件。 | 新只读服务/API/reader可分工，旧 trace/list 合同保留，Root 注册/RRRO/schema。不猜任意 JSON UUID 引用，不把未找到当无动作。Root 已分派此功能给本审查者，当前尚未实施。 |

原 FULL-203 的规划收入登记也确实缺实现，Root 已分派独立新模块；不是本清单中的“只待测”。FULL-804/805/808 的原始实验机制/证据准入能力另有实质缺口，见逐项表，不能最后仅补跑现有五臂工具就满足 FULL。

## 工程、事实与策略（11 项）

路径中的服务名均指 `apps/api/app/services/`；域名指 `apps/api/app/domain/`。公开 API 均以当前 `main.py` 实际注册为准，不从文件名推断接线。

| 编号 / 原计划 | 当前实际入口与支持范围 | 真实功能差量及前置 | 验收缺证（不混作功能缺失） |
|---|---|---|---|
| FULL-001 / 1154–1160 | `docs/spec/product-spec.md` +原追踪表/验收清单：产品、不变量、非目标、权限矩阵已可读。 | 合并规格须持续补当前新增协议范围；92 项尚非每条都具最终确切测试/人工结果映射。依赖各包 final。 | 跨 API/UI/代码/材料逐条人工一致性和最终追踪关闭未完成。 |
| FULL-002 / 1162–1166 | `scripts/tasks.py`、`security_check.py/evidence_check.py/build_proposal.py`、Makefile/make.cmd、`.github/workflows/quality.yml` 实际入口；`full-check` 有失败传播。 | `build-proposal` 仍是旧稿 HTML 审阅入口，未接当前 PDF/PPTX builder；原生产安全扫描106告警缺处置/复核记录，当前计数未重测；CI worker 未配置。非“缺命令文件”。 | 真实 CI/九项最终命令未跑；安全告警是原 FAILED，不能只称未验收或整体 PASS。 |
| FULL-003 / 1168–1170 | `docs/spec/glossary.md`：双时态、规划/权限、归属/放置、UNKNOWN/账本/回执等术语。 | 无新运行时核心缺口；当前新增页面/材料措辞须对齐，不凭词表覆盖全产品。 | 全产品人工术语一致性未审。 |
| FULL-101 / 1176–1178 | `/evidence/facts` 双时态实际查询；`evidence_graph.py`；`evidence_declarations.py` 追加用户声明/更正，保 supersedes/hash。 | 不具完整历史可变 status 重建；金融适配器尚非全部双时态选择。专用声明审计/更多银行观察导入依赖真实来源协议。 | 已有真实 PG 当前/历史/未来拒绝；最终跨用户与所有金融来源回放未齐。 |
| FULL-102 / 1180–1182 | 原 `/evidence/graph` +新 `/evidence/full-graph/{kind}/{identity}`；`full_evidence_graph.py` 原 35 表分母、双向 FK/typed 引用、现 verifier；新 Web 已接 EvidencePage。 | 专用 proof 内部关系/历史完整世界尚未穷尽；无原副本的可变历史保 UNKNOWN。需要明确 typed 边，不扫描 JSON 猜引用。 | 新图 31 个不同直接风险，实际新 PG 待跑；旧有限图 PG 不能替代完整新图；浏览器待验。 |
| FULL-103 / 1184–1186 | `/policy-templates` schema/validate；`full_policy_configuration.py` 十二 strict 模板，旧五 MVP 配置/hash不变。 | Schema 本体已有；引用 owner/当前授权由后续真实服务负责，Schema 成功不许可银行。 | 381 相关纯/API及兼容检查已有；十二模板全确认/行为不是本 schema 包的成功证据。 |
| FULL-104 / 1188–1190 | `/full-policies` 原 CREATE/CHANGE/RESUME/SUSPEND/REVOKE/REFRESH_TIME、不可变 Version/Command 链及 by-key；`full_policy_lifecycle.py`；605 已接付款/回拨/整组资产。 | `action_dependencies_supported=false` 仍诚实：其他 FULL 模板依赖、持仓/边界恢复未全接；SEALED缺当前证据只保原版本，不宣称档案验证。 | 8 实际 PG 已 PASS；全模板提交前后/UNKNOWN竞争矩阵及完整归档验证待验。 |
| FULL-105 / 1192–1194 | 原结构/来源预览 + `/full-policies/{id}/financial-change-preview`；`full_policy_change_impact.py`；编辑器已接真实只读 UI。 | 金融变化仅 Dated/Periodic floor；其他 10 模板、目标分配/持仓处置/未来动作 null，见优先2。 | 原生命周期预览8PG已过；新增 financial 与实际确认后差分一致性待跑，不外推。 |
| FULL-106 / 1196–1198 | `/full-policy-compilations/grammar|preview`；`full_policy_compiler.py/full_policy_compilation.py` 十二有限中文规则、PII token、独立 schema/diff、UI重新校验采纳。 | 可选 provider 是 server 注入协议，默认关闭，无网络 SDK；来源文本/模型/人工修改的完整持久链未接。有限语法不承诺任意中文。 | 71 直接风险已有；真实 provider/隐私/完整浏览器确认链待验。 |
| FULL-107 / 1200–1202 | `/policy-suggestions/periodic`；`pattern_suggestions.py/policy_suggestions.py` 月周期、真实 payee/source、精确方差、账单与分类证据；消费页原候选可读。 | 周/月末/更宽周期未支持；建议到原声明/确认尚无直接采纳 UI。仍可按已有严格配置入口手动建立；建议不会自生义务。 | 原未确认 seed UNKNOWN 正确；新真实分类确认→规律 READY 与原 UNKNOWN/HTTP/coverage负链已取得独立结果，最终 E2E待验。 |
| FULL-108 / 1204–1206 | `/policy-suggestions/seasonal`：2024–2026官方有限窗口、同节日历史/覆盖、整数分 quantile 建议、不足历史 null。 | 首次明确采纳金额→有效策略→硬保护缺桥，见优先3；更多年份/地区/旅行活动窗口未支持。 | 原 seed 无两年窗口不应 READY；真实丰富历史正例/采纳后效应/浏览器待验。 |

## 现金流与边界（6 项）

| 编号 / 原计划 | 当前实际入口与支持范围 | 真实功能差量及前置 | 验收缺证 |
|---|---|---|---|
| FULL-201 / 1212–1214 | `/planning/annual|full-annual`；`full_projection.py/full_protection_projection.py` 实际366日期×3阶段1098点、验真本金、Dated/Periodic额外floor；已有 FullJoint/执行保护消费者。 | 旧 FULL 欠付/改版未结清覆盖未知；Seasonal无采纳；未来逐账户借记仅新保守全支出上界证明，不是精确 MVP 逐账户预测。 | 原年度/Full保护/Joint实际 PG已过；全模板/消费者最终验收待。不要沿用早期“未接消费者”。 |
| FULL-202 / 1216–1218 | `/autonomy-envelope/assess|actions/{id}`；`autonomy_envelope.py` 五集合及来源/拒因，原 MVP评估；新 dynamic proof接原Autonomy当前读。 | standalone envelope 仍非全部新 FULL 执行协议专用适配；未知/不支持动作空集/UNKNOWN，不额外授权。 | 原purchase/pause/Full无grant三节点及单transfer原Timeout/银行键节点分别通过；非新完整四节点批全过；七不变量全集待。 |
| FULL-203 / 1220–1222 | 现年度/Full年度均分离当前执行，`FutureIncomeProjection` 固定 `NOT_IMPLEMENTED_NO_REGISTERED_SOURCE`，计入执行0。 | **注册规划收入源/双时态撤回替代/规划曲线显示未实现**，不是预测值实测0。Root已交新独立模块；执行输入继续不含预测。 | 真实拒注入/原边界不变已有；尚无合法预测变量增减的实际变形分母。 |
| FULL-204 / 1224–1226 | 原单动作 events、旧 global v1/full-v1 与新 `/boundary/actual-action-set/current|observe|observations`；`full_action_set_boundary_actual.py` 真实表/完整counts/动态+wholeasset、签名/原trace/精确通知。 | 原旧 global 真库UNKNOWN根因已保留，新版本不改旧hash；未适配 FULL家族使实际全局仍UNKNOWN，见优先1；自动来源订阅尚不全面。 | 新24纯+通知22直接风险已有，actual-v2金融/HTTP通知链待；旧v1真实FAIL不升级。 |
| FULL-205 / 1228–1230 | `test_boundary_properties.py/test_goal_allocation_properties.py/test_asset_allocation_properties.py` 等现 Hypothesis 整数/日期/策略/产品/归属生成与性质；不是空生成器。 | 未有覆盖全部 FULL 十二模板/新协议的统一策略×目标×产品×事件生成和独立不变量分母/分布记录。应按新协议补真实生成范围，复用原反例。 | 全部新 FULL 不变量、最终≥1000有效样例计数/覆盖与最小失败集待；旧MVP场景不自动升级。 |
| FULL-206 / 1232–1234 | `/boundary-differences/compare`；`boundary_difference.py` 两个实际冻结run重算、X→Y与原事实/约束变化。 | scope仍原 MVP financial_safe_idle；新Full保护/Joint/资产/授权全集差分与自动前后配对/UI完整入口未全接。 | 原应急额+5000→safe_idle−5000单节点PASS在原 FAILED 混合批，未造单项时长；全范围待。 |

## 多目标与修复（7 项）

| 编号 / 原计划 | 当前实际入口与支持范围 | 真实功能差量及前置 | 验收缺证 |
|---|---|---|---|
| FULL-301 / 1240–1242 | `/goals/{id}/full-model` 原 read/preview/confirm/by-key；`full_goals.py` 实际Goal+追加FullModel、base/Full双hash；UI已明确确认/恢复。 | 完整首次创建沿原Goal→FullModel两步；不能自报 current_owned/有效版本。跨目标默认 false由独立304协议覆盖，不改原模型hash。专用 FullModel typed审计尚缺。 | 真实确认/回滚/来源篡改已有；所有属性行为/最终E2E待；不能称没有持久模型。 |
| FULL-302 / 1244–1246 | `multi_goal_allocation.py` 八层词典序；`multi_goal_planning.py/full_joint_goal_planning.py` 当前真收入/归属/365+Full floors，`/planning/full-current-goal-allocation`。 | 当前期有限规划(≤8目标)已有；多期全局调度/联合计划逐分配执行消费者未接，日期下界不是真实完成预测。 | 小域独立穷举+实际Joint原保护/金额已过；整体多目标执行/最终验收待。 |
| FULL-303 / 1248–1250 | 默认Goal锁原执行门；`goal_release_provenance.py` 真ALLOCATE来源/完整ASSIGNED/现金分母；`full_goal_release_inventory.py` 精确残额；新cash-release不同协议。 | 现金-only来源可证；购买/赎回后的现金本金分割无原件时UNKNOWN，非FIFO；同Goal资产原路径已支持，跨Goal默认禁止。 | 原新6PG含现金來源/默认锁/真实释放PASS；全部资产放置/归属组合待，不能再登记“无现金执行”。 |
| FULL-304 / 1252–1254 | `/goal-release-authorizations` 专用exact scope/hash/accepted；`/goal-cash-releases` 原prepare/confirmed execute/read/by-key；当前紧急最小修复、跨版本caps、银行3腿。 | 当前单源Goal现金→保护CASH、原ASSIGNED不变；多源联合/持仓回拨缺真实分割协议。规划确认≠专用金融许可。 | 原6PG含专用授权/真实bank/UNKNOWN旧键恢复PASS；全条件/并发/撤销矩阵待。 |
| FULL-305 / 1256–1258 | `/planning/full-goal-conflicts`；`full_goal_conflicts.py` 实际Joint输入，最小冲突/witness/逐项删除可行性，完整分母UNKNOWN保留。 | 当前期有限目标约束；其他十二模板/全多期最小不可满足集未适配，不是返回全体约束充数。 | 原6PG含真实冲突/修复节点PASS，独立小域已有；更大容量/多期最终验证待。 |
| FULL-306 / 1260–1262 | `propose_minimal_goal_repairs` + `/planning/full-goal-repairs/preview` 真来源max范围临界候选；`GoalConflictRepairPanel`→原FullGoal重新preview→双hash明确新版本。 | **采纳接缝已有**。自动候选范围目前user-selected月max；月min/deadline/其他模板、多期修复未接，无原子多Goal确认，每次确认后重规划。 | 原6PG修复范围/确认源与31组件检查已有；多目标交互/最终浏览器待。 |
| FULL-307 / 1264–1266 | `dynamic_goal_reserve.py` 读节奏；`full_dynamic_goal_execution.py` preview/prepare/by-key +Root原prepare/confirm/reserve/bank/autonomy/history，typedproof允许>nominal≤原max；UI原body/key/确认恢复。 | 单Goal当期真实消费已有；全多目标联合动态/多期执行缺；OVERDUE/PARTIAL不能越原有效期/min；原v1收入未兼容。 | 当前原manifest新1PG已PASSED265.34s，scope true/global false（见E5）；当前所有族/浏览器/最终验收待，旧409原件保留。 |

## 资产与到期（6 项）

| 编号 / 原计划 | 当前实际入口与支持范围 | 真实功能差量及前置 | 验收缺证 |
|---|---|---|---|
| FULL-401 / 1272–1274 | `/catalog/products` read/versions/register；`product_catalog.py` 不可变原产品/条款/有效期版本，SQL保全trigger；新WholeAsset capture绑定原目录。 | 旧legacy决策没有新目录id，不能伪回填；专用目录审计未接；当前真实产品有限，不制造产品。 | 原5PG目录/旧迁移PASS；旧新全部run重放/漂移浏览器待。 |
| FULL-402 / 1276–1278 | `/full-policies/{id}/asset-allocation` 真实目录filter + `/full-asset-executions` whole消费者，双方MVP/Full权限、Goal、完整保护/claims与首次银行重验。 | 支持实际目录T0/T1/30D；七类别schema/数学可处理不代表7D/90D/LOW真实产品已提供；Goal新动态/完整旧版本敞口未知仍拒。 | 原规划2PG已过，实际whole1PASS但相关保护源期间变动，诊断证据不能当最终冻结集成；最终矩阵待。 |
| FULL-403 / 1280–1282 | `full_asset_allocation.py` 有限联合优化/复杂度换手/净收益/并列流动性；持久Portfolio/Batch/Consent，固定批序与原purchase真实3phase。 | 不是单产品预览相加；最多4批，不承诺跨bank原子回滚。更多组合/换仓持仓消费者仍有限。 | 实际整组确认、marker拒绝、UNKNOWN恢复/原批重放已有诊断PASS；相关最终冻结及并发/硬重启待。 |
| FULL-404 / 1284–1286 | FIXED_LADDER规划 +同whole存储/顺序执行，批目录/金额/Goal/version/到期前限制和whole aggregate守卫。 | 真实多期限产品/Goal多梯度投资未全供给；数学支持不能冒实际多到期证明。后批不能越UNKNOWN，缺适合产品应拒绝。 | 原一般组合/有限Goal规划已有；真实Goal梯度/多期限银行/截止竞争待。 |
| FULL-405 / 1288–1290 | `/full-policies/{id}/recovery-planning` 原报价/条款/损失/时间/当前许可，独立整数损失与ASK；原MVP有损early-withdraw明确确认执行。 | FullRecovery整仓规划有，部分/费用/有损专用consumer未接；原365保护与新Full缺口尚非统一所有恢复范围。 | 实际2PG原无损/100分早退损失ASK已过，原混合批FAILED保留；全组合执行/浏览器待。 |
| FULL-406 / 1292–1294 | `/full-maturity-replanning/preview` 验真原到期账本/receipt，读当前策略/目录/边界重新选择，稳定原到期×当前版本键及原prepare候选。 | **已有可运行当前重规划，不再称缺功能**；但 `dedicated_decision_recorded=false/current_prepare_consumes_reviewed_decision_hash=false`，FULL重配置新执行/回执、持久到期调度未接。 | 原6PG含撤销后合同到期/当前重规划节点PASS；新再投资回执/完整竞争场景待；PASS不等于自动续投。 |

## 不确定性与介入（7 项）

| 编号 / 原计划 | 当前实际入口与支持范围 | 真实功能差量及前置 | 验收缺证 |
|---|---|---|---|
| FULL-501 / 1300–1302 | `/finite-planning/analyze`；`finite_uncertainty.py` 有限ACTION_INTENT/TRANSFER金额/owned来源目的，USER_REQUEST或登记Evidence，不升金融等级。 | 原列计划支出区间、续租/Goal有效、损失同意、新期限、修复选择尚无对应世界adapter；注册声明来源producer不完整。不是任意变量均支持。 | 真多变量/HTTP引擎已有；全部变量来源/能力边界的最终case待。 |
| FULL-502 / 1304–1306 | 同服务每世界调用真实旧plan/revalidate/确认分析，冻结原资金来源/context、全世界保分母；无写入。 | 世界依赖原MVP完整上下文，尚非全部新FULL execution/规划家族；合法假设不能变BANK证据或实际授权。 | 原实际finite单节点PASS在FAILED混合批，后实际HTTP亦通过；全FULL族世界/最终集成待。 |
| FULL-503 / 1308–1310 | `complete_planning_signature/evaluate_finite_planning` 比类别/金额/账户/Goal/条款/版本/fee/loss/可用风险，KNOWN全一致才STABLE。 | 此有限算法已有；其他变量族先依赖501/502，UNKNOWN不假稳定；STABLE不免旧effect确切确认。 | 原类别/金额/归属/风险直接风险已有，真实同条件完整基线对比待。 |
| FULL-504 / 1312–1314 | `finite_uncertainty.py` 枚举实际有限响应分区的一步非概率minimax，完整后剩余分歧/固定ID tie。 | 不是跨多步问题树全局最优；当前分区范围依赖501合法adapter，不凭模型置信度。原一问算法可运行。 | 独立小例/真实有限引擎已有；全量确认基线/Full50族统计待。 |
| FULL-505 / 1316–1318 | `/finite-planning/sessions` START/ANSWER/REFRESH/CLOSE与两原键GET；`question_workflow.py` 原DecisionRun父链、fresh source改变REBASE、旧确认不继承；OneQuestionPage已接。 | 当前规划会话/实际重算已有；回答后的新金融行动consumer/封存检索/跨族后果尚有限，不从规划答案授新权限。 | 原3PG长链含Question CLOSE/by-key已PASS；首SUCCEEDED夹具FAIL保留，真并发/硬重启/浏览器待。 |
| FULL-506 / 1320–1322 | 每问题原世界/分区/完整动作签名后果与来源证据解释，OneQuestion UI展示；无LLM因果。 | 有限分析范围内已有，其他Full变量/新动作解释先依赖501/502/204实际adapter。 | 原计算/解释风险已有；真人理解不是自动测试，实际可读性/研究待。 |
| FULL-507 / 1324–1326 | `/interventions` 持久observe/claim/deliver/ACK/by-key；Question postcommit/遗漏恢复worker；GLOBAL exact V1/Full/actual-v2分派与新crossing postcommit。 | 全局families不齐仍UNKNOWN；自动订阅所有事实/事件与全局UI新第三DTO/档案来源未齐；现明确调用不是后台全覆盖。 | 原通知纯/部分实际链有；新actual-v2 HTTP notify/重开claimACK待，进程强杀/重启节流/真实弹窗待。 |

## 执行、恢复与对账（7 项）

| 编号 / 原计划 | 当前实际入口与支持范围 | 真实功能差量及前置 | 验收缺证 |
|---|---|---|---|
| FULL-601 / 1332–1334 | 原 `/actions`、bank/query/projection、recovery/newcashrelease/orderedassets 原identity+attempt/key状态；UNKNOWN不盲重扣；`command_delivery.py`。 | 当前多种可运行状态机并存，不是一个全九动作消息消费者；新custom协议须有独立读者，原generic delivery不可猜处理。状态名不强改旧历史。 | 原10PG投递+原金融/新release/asset/付款风险已有；九类动作全crash/重启/终态矩阵待。 |
| FULL-602 / 1336–1338 | CommandOutbox/Inbox/Attempt不可变identity、同prepare TX enqueue；`/delivery` 手动投递，用户×consumer advisory锁跨sessions；ACK≠经济成功。 | 没有默认自动后台worker；新独立协议均被此generic consumer支持尚未证成。显式enqueue只已有原action，不补权限。需要明确新consumer而非重标签。 | 原10PG连接重开/两个crash/UNKNOWN/BUSY已PASS；真实进程硬重启及九动作完整分母待。 |
| FULL-603 / 1340–1342 | `simulated_bank.py/execution_bank.py` 独立BankOperation/账本legs/原查询，与应用投影/receipt分TX；新release真实3legs、新payment4legs均已接。 | 独立账本能力已有；新协议完整历史/银行锚定范围须跟随各reader，不能由应用SUCCEEDED推效果。 | 新旧实bank故障/保UNKNOWN已有；全部协议并发/强杀/旧归档完整独立核验待。 |
| FULL-604 / 1344–1346 | 原恢复 +FullRecovery规划现金→T0→T1→到期→有损ASK，完整时间/损失；新 `/full-recovery-actions` 原USER明确ASK真实consumer和UI。 | 仅新整仓零成本T0/T1；Full新保护缺口/Goal/部分/组合/有损/成熟执行缺，见优先4。当前计划不假到账。 | 原Full规划2PG过；新银行执行链当前待Root；全组合与完整回执/LIQUIDITY_RISK终态待。 |
| FULL-605 / 1348–1350 | `full_policy_action_rechecks.py/full_asset_action_rechecks.py/full_payment_permissions.py` 原生命周期事务精确关联；无effect未提交失效，有bank/UNKNOWN保原key/claim。 | 新3家族已接Root，不再称资产未接；其他Full模板/动态Model修改时的完整依赖重查尚非全覆盖，bank仍fresh current拒绝。 | 新hook纯53/Root两风险已有；当前posthook实际撤销、SUBMITTED/UNKNOWN/竞争全矩阵待；旧6PG不能替代新hook。 |
| FULL-606 / 1352–1354 | `/full-payment-relations` 新USER已知银行身份关系/原scope授权/固定periodic prepare、ASK或AUTO、execute/by-key；HMAC本地USER/AGENT意图拒绝。 | 新关系需要银行唯一可证身份，歧义/未知拒绝；非外部账户开户/真实KYC。后月必须当前原银行观察，不猜已付款0；全部角色发行尚有限。 | 新AUTO/ASK真实2PASS848.02s核4legs/原键/audit/零写，旧FAIL保留；response-loss/并发/到期矩阵及浏览器待。 |
| FULL-607 / 1356–1358 | `/reconciliation/current`；`full_reconciliation.py` 应用现金/Goal/本金vs独立账本、完整legs/receipt/audit、UNKNOWN/新release分支；ReconciliationPage已接。 | 当前读-only人工待核对，实际人工修复落账未实现（不能伪修复）；信用卡债务/利息估值、legacy来源分割/归档完整scope尚有限。 | 原3PG含真cash/Goal/response-loss/故意差额PASS；新协议全类对账/进程重启/完整归档/最终浏览器待。 |

## 产品界面（8 项）

| 编号 / 原计划 | 当前实际入口与支持范围 | 真实功能差量及前置 | 验收缺证 |
|---|---|---|---|
| FULL-701 / 1364–1366 | `OnboardingPage/onboarding-draft` 八步原发现/声明/生活估算/应急/Goal/资产与恢复范围说明，App原跨族待核对门、原候选GET/同body恢复。 | 多步骤只到PROPOSED再跳原明确确认；compile/discover丢响应无持久终局receipt不能自动清pending。恢复规则/全部Full候选一步内确认及确定当前自主额度未齐；BLOCKED不能8ticks假完成。 | 45direct+Root导航/门已有；首次真实完整浏览器、退出恢复/可用性待。 |
| FULL-702 / 1368–1370 | DashboardPage原分层/义务/介入/最近动作；AnnualPlanningPage+FullProtectionPanel完整日阶段曲线、Full来源与差额；App已接。 | 完整Full待问/global事件→首页卡/事件自动更新与跨所有Full行动时间轴尚不全；未来收入曲线待203；不能用safe_idle冒可自动授权额。 | 原MVP Dashboard Edge有；新Full卡/曲线/时间轴整体E2E、手机待，不能仅凭旧Edge关闭。 |
| FULL-703 / 1372–1374 | PolicyCenterPage原策略图/关系/版本；FullPoliciesPanel新有限生命周期/原请求恢复/金融变更预览/固定付款与恢复消费者。 | 地图未保证全新Full协议依赖图；完整financial影响依赖105；旧历史不足保持明确缺口。 | 原页面+42生命周期相关+后Root宿主已检；全新生命周期/依赖graph浏览器与篡改提示待。 |
| FULL-704 / 1376–1378 | GoalsPage原归属+FullGoalModel双hash、原/FullJoint8层、动态储备、冲突repair→明确确认、专用回拨授权/执行与工作区恢复。 | 当期规划不等多期完成预测；多源/资产回拨依赖303/304；全部影响预览依赖105；旧完整目标首次创建沿两步不是单一新向导。 | 多项reader/direct已验、Joint/release实际有；完整Goal所有字段/延期/资产/冲突真实UI/E2E待。 |
| FULL-705 / 1380–1382及1050–1062 | OneQuestionPage +InterventionCenterPage，原一次一问/原通知sourcefresh/claim/ACK；普通更新不当金融授权。 | 全global新第三DTO消费/全集未知、全部新损失/冲突/失效自动producer尚不齐，依赖204/507/604；原问题规划答案不直接金融确认。 | 页面直接风险有；真实一问/回答重算/失效、通知节流/旧答案/完整E2E待。 |
| FULL-706 / 1384–1386 | DecisionTracePage八层原run→sources/候选/约束/原receipt/chain，EvidencePage新完整35表图+原双时态、Spending证据；GET-only。 | 按动作搜索/版本筛选确实未实现，见优先5；不是缺原件阅读。完整历史可变状态无副本时UNKNOWN。 | 原MVP证据Edge/新direct有；新图PG/新检索/篡改可读性截图待。 |
| FULL-707 / 1388–1390 | `/scenario-simulation/context|compare` +ScenarioSimulationPage：真实普通cash delta、MVP emergency候选、产品term/settlement假设，同RRRO原引擎重算，不改结果/原事实。 | 有真实有限模拟能力；尚无12 Full配置/账单/目标反事实、product risk/lock/完整optimizer/收益loss适配。产品仅影响boundary占用，不冒可购性。 | 35后端/51Web直接已有；Root已注册导航/RRRO，真实PG/参数来源边界/浏览器与完整重放待。 |
| FULL-708 / 1392–1394 | `styles.css/full-mobile-accessibility.css` 集中窄屏/焦点/目标尺寸/reducedmotion，房租预览定义列表；App skip-link/路由内容focus已接。 | 无需将“缺手机实测”改叫缺样式；全部复杂曲线/地图的完整替代说明、通知报读/键盘路径仍未穷尽。 | CSS build/direct已有；320/375/390px、200/400%缩放、真实键盘/屏幕阅读器/实际对比度/触屏全未验；不声明WCAG。 |

## 安全、实验与交付（15 项）

| 编号 / 原计划 | 当前实际入口与支持范围 | 真实功能差量及前置 | 验收缺证 |
|---|---|---|---|
| FULL-801 / 1400–1402 | `docs/security/threat-model.md/full-stride-controls.md` STRIDE/9类威胁及真实会话、candidate、漂移、原键、UNKNOWN/账本控制来源。 | 文档与实际控制并存；原106安全告警缺逐条处置/复核证据，当前计数未重测；完整脱敏/导出等控制仍有限，须真实语义处理，不全局豁免。 | 最终源威胁证据矩阵/渗透/独立审查待；不能凭矩阵关闭。 |
| FULL-802 / 1404–1406 | 106 compiler新严格provider封套、独立原Schema/源码token规则一致、未知散文不发、candidate不写权限，离线默认可用。 | 本有限可选模型防越权已有；外部provider/更多开放语法未实现，不应为验收主动开启。 | 71注入/非法字段/结构/PII直接已有；真实provider隐私评审/全路径安全审查待。 |
| FULL-803 / 1408–1410 | 原typed `audit_chain.py` /SQL append-only/immutable triggers，`verify_audit_chain.py` API/CLI，事件/head/subject/currentrefs，原history不改hash。 | 无外部签名/独立锚，但原要求“签名或只追加”已有后者；新Full独立版本链≠所有metadata typedAudit，缺完整原件应UNKNOWN。 | 原篡改/删除/乱序/截断负例与实际保全已有；全新算法/所有epoch完整权限和容量范围待。 |
| FULL-804 / 1412–1414 | `mvp_corpus.py/mvp_corpus_v2.py` 已有明确MVP/FULL_FAMILY用途、原字节/manifest/hash/源防漂移、Scenario严格图与族登记合同。 | **没有实际50族×多变体原输入/独立真值/族级split/评审冻结集**；不能拿24设计或纯fixture代替；Full原28指标/族真值producer未齐。这是交付内容缺，不是仅缺一次运行。 | 实际原件与人工冻结未有；任何成功用途字符串不能关闭；最后数据生成也须独立保原失败。 |
| FULL-805 / 1416–1418 | 原MVP `mvp_arm_executor.py/experiment_arms.py` 五臂框架/GENERAL实际服务hook、原观察/指标/phase验证存在，原数据与TOOL_TEST隔离。 | **B4/B5、Full七机制、八实际机制消融、完整Full28计算器未实现**；B0非purchase/Goal/Pay小意图、B3 recovery逐动作确认也仍有限。仅改标签不是机制。 | 24×5完整金融效果未取得更不能当Full50×7；统计/完整分母/独立oracle与收益同安全对比待。 |
| FULL-806 / 1420–1422 | `docs/research` 六任务/三条件/成年同意/问卷/匿名协议，`research_records.py` 真输入追加/更正/撤回导出与32 TOOL_ONLY风险。 | 工具和材料已可运行；三条件任务界面前置需核，负责人/联系/保存期限未定，PII模式不是全部脱敏认证。 | **0 真人/NOT_STARTED**；需自愿成年真实研究，不由代理生成；18–24为计划，不以32测试数冒人数。 |
| FULL-807 / 1424–1426 | 原故障injector/scoped runner/隔离PG/node/原银行response loss与projectionFAIL/重复键、W0固定场景已有实测记录。 | 完整新Full并发/进程硬重启/大规模workload调度与负载实验不齐；现回连测试不等强杀重启。不要另造无必要框架，补原指定scenario即可。 | 当前新功能性能NOT_MEASURED、环境/分段时延/故障完整分母待；W0 n=1混合历史不外推。 |
| FULL-808 / 1428–1430 | `export_evidence.py/evidence_check.py` 原字节/hash/run/source/部分原语义核验、缺请求/假PASSED非零；CI入口已接。 | 当前仍主要MVP出口，完整Full67/28指标/材料claims/八图/研究来源自动准入语义未齐；不是只少真实输入，也不能手写VERIFIED绕门。 | 原TOOL_ONLY正负例有；当前完整出口INCOMPLETE，最终CI无源数字拦截待，材料台账不自动升级。 |
| FULL-901 / 1436–1438 | `docs/materials/current-functional-delivery-20261006/proposal.md` +当前12页交付版PDF、页数/Poppler原件与台账。 | **成品已交，不再称只有骨架**。尚无真实完整实验/青年研究结果，正文保限定状态与商业假设；build-proposal仍未消费当前成品。 | 人工内容/全部主张一致性、完整实验补回与竞赛终稿未验；12页不等获奖/效果证明。 |
| FULL-902 / 1440–1442 | 同目录 `whitepaper.md` +30页交付版PDF、22章节覆盖与源码/失败/限制索引。 | 成品/算法技术说明已有；完整实验/真人与最终源码一致性待后续数据，不编造结果。 | 真实页数/渲染有；人工技术全文/图/源一致性未审。 |
| FULL-903 / 1444–1446 | `experiment-report.md` +14初版/28Full空值表/原run证据索引，失败/统计/NOT_RUN可读。 | 目前是**有边界的当前报告**，缺804/805实际机制/原始CSVJSON实验结果；不是完整正式实验报告。 | 无全条件比较/消融/统计分母，不写优势；报告证据审查待。 |
| FULL-904 / 1448–1450 | `demo-240s-script.md` 分段240s演示稿 +原MVP404三轮真实Edge可复用历史，不重置正式历史。 | 当前稿已有；**≤240s现场当前Full三链/异常完整备用录屏尚无新成品**，原长失败视频不升级为成功。 | NOT_RECORDED/MISSING；实际时长、同run截图/离线演示/人工可见性待。 |
| FULL-905 / 1452–1454 | `defense.md` +10页交付版PPTX、问答/证据索引、Artifact import/渲染/几何记录。 | 成品已有；新的实际实验数字/原画面缺时明确缺，不添假图；原生PowerPoint未开不等打不开已证。 | 页数/结构/渲染有；人工内容/现场原生打开/口头答辩待。 |
| FULL-906 / 1456–1458 | 当前材料 `claims_registry.yaml/figures_registry.yaml/metrics_registry.yaml/evidence-index.md/sources.json` 原计划/当前源码/原manifest复制SHA；外部官方calendar来源说明。 | 台账已有，自动效果入正文许可false；八图真实数据/原画面仍缺，所有Full指标空值非结果。依赖808与实际研究/实验。 | 外部事实/业务假设/实验主张逐项人工核验及完整自动准入待；不把SOURCECOPY当金融实证。 |
| FULL-907 / 1460–1462 | 原追踪表/验收清单、`make.cmd check/full-check`、证据出口、当前材料delivery-manifest可调用。 | 部分产品/F8机制缺口见本表；不新增一个“最终成功”工具替代原G0–G4。 | **两版最终冻结全量/全部人工/G0–G4/原屏幕录像研究数字一致性均未完成**；67项原PENDING保持。 |

## 当前实测证据的范围更正

本次直接只读以下原 `manifest.json` 与原 `output.log`，没有重跑、修改或拆造单节点时长：

| 代号 | 原证据目录（`docs/progress/evidence/`） | 原终态及可用范围 |
|---|---|---|
| E1 | `W2/durable-delivery-clock-repaired-real-pg-20261005T130035Z-660470f0` | PASSED/exit0，10PASS210.91s，scope true/global false；是真实投递范围，不是进程硬重启。 |
| E2 | `W2/full-policy-lifecycle-preview-history-original-key-real-pg-20261005T135521Z-7b52013c` | PASSED/exit0，8PASS27.34s，scope/global true；原八类持久/预览/键/SEALED范围。 |
| E3 | `W3/actual-release-sources-consent-repair-and-current-maturity-20261005T195334Z-810817b1` | PASSED/exit0，6PASS1600.41s，scope true/global false；303预览/来源/专用授权/305修复/406当前重规划/真实现金释放分别已有，不笼统说全部功能缺。 |
| E4 | `W5/actual-audit-scope-original-event-repaired-and-full-readers-20261005T180751Z-fbbaffb2` | PASSED/exit0，3PASS3728.50s，scope true/global false；审计scope/607/Question真链已有，不能取早期NOT_RUN抹掉。 |
| E5 | `W4/actual-dynamic-native-income-and-bound-context-original-key-20261006T004341Z-5985f7c9` | 00:54UTC只读原件已PASSED/exit0，1PASS265.34s，scope true/global false；当前动态超nominal、原银行丢响应、撤销后旧键恢复新范围，不是全量或全源冻结。Root仍管其运行锁。 |
| E6 | `W4/actual-fixed-payment-original-four-legs-auto-and-ask-20261005T231509Z-34a7fbee` | PASSED/exit0，2PASS848.02s，scope true/global false；原AUTO/ASK四银行legs/原键/audit，旧expect3等FAIL不改。 |
| E7 | `W4/actual-whole-asset-current-open-clock-20261005T213247Z-dadd11d5` | 原manifest PASSED/exit0，1PASS2826.45s；期间两相关FullProtection源变动，scope未全覆盖。只用作实际可运行诊断，不称当前完整相关冻结集成。 |
| E8 | `W5/actual-category-protocol-upgrade-joint-and-question-real-pg-20261005T165235Z-c356176b` | 原FAILED/1FAIL3PASS1918.29s；0011/真实类别确认/FullJoint各PASS，Question当时错误期望EXECUTED失败。后E4补其真链，不改本原失败。 |
| E9 | `W3/actual-boundary-action-events-and-finite-worlds-real-pg-20261005T152630Z-7693a74d` | 原FAILED/1FAIL1PASS206.14s；finite唯一节点PASS，Boundary另节点FAIL。没有单项耗时，不称整批成功。 |

当前材料封存原件为 `docs/materials/current-functional-delivery-20261006/delivery-manifest.json`，00:53UTC新版真实报告12页PDF/30页PDF/10页PPTX、`whole_repository_frozen=false`、`REVIEWABLE_MATERIAL_ARTIFACTS_DELIVERED_ACCEPTANCE_INCOMPLETE`。页数来自该包实际生成/解析记录，本审查没有重新渲染或人工审阅。

下一实施：Root分配上述实际产品差量与原F8必要机制；其它已有功能继续复用其原证据，只有行为/直接依赖改变才补定向风险。最后在最终代码集中全量、原生浏览器/录制、人工与研究验收；无证据继续 UNKNOWN/MISSING/NOT_RUN。任何当前规划/证据/确认接口均不因此获真实资金授权。

## 本次源码定位与观察摘要

以下只给优先差量的实际函数，不将文件存在作为完成证据：

- `domain/full_action_set_boundary_actual.py::derive_actual_action_set` / `services/full_action_set_boundary_actual.py::capture_actual_action_set`：真实已处理family与完整未处理分母。
- `domain/full_policy_change_impact.py::project_full_policy_change`：只支持Dated/Periodic；`services/full_policy_change_impact.py` 实际入口。
- `domain/full_protection_projection.py::project_full_protection`：Seasonal未采纳；`services/policy_suggestions.py` 与 `pages/SpendingEvidencePage.tsx` 真实建议/只读消费。
- `services/full_recovery_execution.py::read_full_recovery_execution_inputs/produce_full_recovery_effect/recheck_full_recovery_proof` / `domain/full_recovery_execution.py`：原费损/Whole/T0T1/时限/版本门。
- `api/v1/decisions.py` 原Query / `services/decision_trace.py::list_decision_traces` / `pages/DecisionTracePage.tsx::DecisionList`：原分页及未有搜索字段。

本文件记录功能范围和缺口，不修改STATUS/HANDOFF/原单项记录或任何生产源/原失败。后续新增源码和实测应追加新证据，不将本表反写原历史。

## Read-only source byte snapshot

2026-10-06T01:04:52.634603+00:00 UTC. These hashes identify only the sources inspected for this inventory; this is not a whole-repository freeze or a runtime proof. Later parallel work remains intact.

| Original source | SHA256 |
|---|---|
| `docs/spec/requirements-traceability.md` | `fdf69f1db667ffda35a089d8318fb6fed25a958e8a198aa4b0d6980038d15a8a` |
| `钱途有界_完整开发计划_Codex执行版.md` | `88be31edbb5f5442420942f821af744aa92c426617dbbac5b41cf2eb0b62fa06` |
| `apps/api/app/domain/full_action_set_boundary_actual.py` | `ea8e62178012da050d2292d5838ed81751d59fab54ca7fceeb85bbe42a3d9bb0` |
| `apps/api/app/services/full_action_set_boundary_actual.py` | `02202c79844300d59864f4ababc00fc81daa0cf91415756c51d25d3b8cca0746` |
| `apps/api/app/domain/full_policy_change_impact.py` | `a3a79f96b6c168b2f31f5ba2b84f5c35506275fea3ae5022987a5b26cf9a52c9` |
| `apps/api/app/domain/full_protection_projection.py` | `5841b2a88b35d951b4c2e1a0f5365b6f84de1795042660bcafe9bfa70b89d15e` |
| `apps/api/app/services/full_recovery_execution.py` | `45fa32b0db5977992041271263bd6fe18712fbece171918ba701d881bbf9607f` |
| `apps/api/app/api/v1/decisions.py` | `94359bfb81a7d242cf84db958c6c33cadbb141a672475dff8296e3e2852befaa` |
| `apps/api/app/services/decision_trace.py` | `264bf2139c916d0a241e6636d179fd13e96f5b73ab7b29a1f581337b2be5158c` |
| `apps/web/src/pages/DecisionTracePage.tsx` | `c3ca6a61cbb91a67b5e1e6457e4c2bded4d6fdf3ce4dced735ac734a0242d930` |
