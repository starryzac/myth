# MVP-303 决策轨迹实施前审查

日期：2026-10-04。状态：**PREFLIGHT_ONLY；303 未实现、未测试，本文不构成验收结论。**

前置 [MVP-302](MVP-302.md) 正在集成与完整验收；其领域代码保持冻结。**303 实现必须等待 302 全量验收完成。** 本次仅阅读计划与现有源码，并新增本文件，不修改源码、测试、数据库、迁移或生成合同。下文接口和迁移均为待冻结建议。

## 计划依据与范围

| 证据 | 本次定位 | 要求 |
| --- | --- | --- |
| [初版计划](../../钱途有界_初版开发计划_Codex执行版.md) | MVP-303，767—770 行 | 保存输入快照、约束、候选、结果和解释；任一动作可从回执追到策略版本、证据和计算过程。 |
| [初版计划](../../钱途有界_初版开发计划_Codex执行版.md) | 8.6，552—568 行 | 账户快照与 hash、策略版本、证据等级、算法、约束值、候选过滤、介入原因、请求/回执/对账应有记录。 |
| [初版计划](../../钱途有界_初版开发计划_Codex执行版.md) | API，603—604 行；10.4，653—666 行 | 计划提供 `GET /decisions/{id}` 与 explanation；轨迹按事实、策略、保护、候选、原因、级别、回执展示。 |
| [完整计划](../../钱途有界_完整开发计划_Codex执行版.md) | 13.1—13.2，725—760 行 | 保存 run、来源、版本、约束、候选、计算、自主等级、人工介入、动作、执行/对账、前后差分；解释来自结构化记录，不能由 LLM 临场编造。 |
| [完整计划](../../钱途有界_完整开发计划_Codex执行版.md) | 13.3，762—784 行；16.1，947—953 行 | 证据导出应能引用真实 run；导出默认合成/脱敏数据，避免暴露完整账号等标识。 |

303 的最小交付应是后端可读取、可核对的版本化决策记录与稳定解释，覆盖现有五类执行意图及 205 恢复/自然到期路径。哈希链、签名或全仓防篡改验证属于 MVP-304；前端轨迹页属于 MVP-403。完整计划的审计浏览器、实验包校验器及完整历史事件重放不提前整套实现。

## 可复用实体与路径

| 当前证据 | 已有内容 | 可复用方式 |
| --- | --- | --- |
| [模型](../../apps/api/app/db/models.py)，`DecisionRun`，344—368 行 | 用户、幂等键、触发类型、算法版本、as_of、input_snapshot/hash、策略/证据 IDs、result、status/completed_at | 作为决策记录主实体，不另造一套脱离动作链的日志 ID。 |
| 同文件，`DecisionConstraint`，371—390 行 | run、策略版本、约束 key、硬/软、满足/未知、金额、日期、calculation、reason | 保存可查询的约束摘要，完整 273 点边界仍可保存在严格结果 DTO 中。 |
| 同文件，`ActionPlan`、`ActionReceipt`，393—466 行 | `receipt.action_plan_id → action.decision_run_id`，并带同用户组合外键；原请求/hash、级别、金额、回执/对账时间 | 从回执反查原决策；不得用重新计算出的新动作替换原请求。 |
| [301 准备与执行](../../apps/api/app/services/execution.py)，40—200 行及 `confirm_action` / `execute_action` | 原 `BankCommand`、完整 effect/hash、prepared_validation、精确确认来源、三阶段执行与预留 | 原地接入记录入口，在同一受控快照/事务中冻结真正参与该次判断的输入。 |
| [301 回执核验](../../apps/api/app/services/execution_projection.py)，`verify_execution_receipt`，810—840 行 | 独立 operation、完整唯一 posting 集合、金额、费用/损失、经济与观察时刻校验 | 历史轨迹接口复用只读核验，不通过读取触发投影、结算或重授权。 |
| [202 上下文](../../apps/api/app/services/boundary.py)，`load_boundary_context` | 已验证现金、账单、目标、贡献、结清、生活准备金、版本、持仓、来源集合；纯边界计算有逐点 trace | 直接冻结所用 DTO 和来源副本；不能只存 source IDs 或金融 hash。 |
| [203 目标规划](../../apps/api/app/services/goal_allocation.py)、[204 资产规划](../../apps/api/app/services/asset_allocation.py) | 目标上限/FIFO lot 使用、各产品可行性/收益/退出/拒绝理由 | 保存原规划结果，解释“为什么该金额/该产品”；不另写一套计算公式。 |
| [205 恢复](../../apps/api/app/services/recovery.py)，70—103、146—169 行；[恢复来源](../../apps/api/app/services/recovery_sources.py)，`recovery_inputs` | plan、候选、实际/预计边界、来源 IDs、原始及当前授权、报价、旧合同到期动作 | 保留旧恢复协议入口，补足完整输入；自然到期标记为原合同结算，不伪装为新自主授权。 |
| [302 适配](../../apps/api/app/services/autonomy.py)，`_basis` / `_facts` / `_response`；[领域](../../apps/api/app/domain/autonomy.py) | 真实来源摘要、完整财务重验、四级结果、有限候选签名和原因 | 记录完整受信 facts 与有限变量，而非仅保存最终 level 或 hash。保持其公开评估接口的只读合同。 |

## 具体追溯缺口

1. **301 的 DecisionRun 不是完整输入快照。** `prepare_action` 当前只将 `intent + effect_hash` 放入 input_snapshot，`evidence_ids=[]`，result 仅有 action_id，创建状态为 PENDING。真正的 prepared_validation 在 ActionPlan.request；现金、lot、曝光、原产品、完整策略配置和确认来源尚未共同冻结。该 run 的 PENDING 也不能直接解释为“资金仍待执行”。
2. **执行前重验没有独立历史记录。** 确认时、应用预留前、银行新受理时会重新校验事实与权限，时钟和预留可能已变化。当前只读源码未见这些 fresh context/result 被保存为独立决策快照；不能把准备时结果当作最终受理依据。
3. **拒绝候选会在规划转换中丢失。** `execution_planning.plan_execution_effect` 将 203/204 的完整 preview 转成选中 effect；ActionPlan 的 prepared_validation 不包含当时全部产品候选、退出收益比较和目标来源上限说明。后来按最新目录重算无法解释原选择。
4. **205 存 preview 输出，不等于存足重放输入。** 恢复 run 保存 preview、plan 及证据 IDs，部分结果/通知之后还会更新；完整 source 输入、原产品/授权/报价副本并未形成统一版本化输入 envelope。旧恢复、301 新赎回与自然到期需要统一可读关联，但不能改写旧经济协议。
5. **证据 ID 不足以保存历史语义。** EvidenceItem 有等级、content/hash、有效期、observed_at、supersedes、status；策略也有版本与确认/生命周期状态。只连接当前行可能读到后来 SUPERSEDED 或已撤销状态。需同时展示“当时使用的副本”和“当前存储完整性/后续变更”，不得以当前状态否定当时已有效受理的事实。
6. **DecisionConstraint 尚未接入正常服务写入。** 本次 `rg` 仅见模型及测试夹具构造，没有生产服务创建。已有边界计算 trace 可以复用，但约束来源、满足/未知及金额解释需落到明确合同。
7. **302 评估是临时结果。** `assess_intent` / `assess_action` 不写 DecisionRun，response 只有摘要、effect 和 decision，输入 facts/候选 worlds 没有对外持久记录。不能为 303 偷偷把 GET 或原只读 assess 变成写接口。
8. **缺统一历史查询与稳定解释。** 计划中的 decisions/detail/explanation 路由尚未在当前 API 中发现。仅有当前动作/回执和恢复 run 查询，不足以返回“原来为什么选择 T1、为什么问、哪些事实导致阻断”的统一轨迹。

这些结论来自静态阅读，是 303 的待交付项，不是本次运行的新测试结果，也不撤销 301 已通过的执行安全验收。

## 建议 DTO 与查询合同

建议 `DecisionTrace` 使用严格、版本化、JSON 可序列化的 envelope，拒绝未知字段与非有限数值；金额保持整数分，时间带时区。以下是字段建议，尚未冻结名称。

| DTO 部分 | 最小内容 |
| --- | --- |
| 身份 | `schema_version`、run_id、user_id、trigger/phase、parent_run_id、action_id（可空）、决策 as_of、记录时间、simulation。读取时间另放响应，不覆盖原 as_of。 |
| 输入 | 按 boundary/goal/asset/recovery/execution/autonomy 区分的严格输入类型；保存实际调用的 snapshot、versions、positions、products、lots、exposure、quote、预留、确认事实。保存用户明确意图，不能补造不存在的授权。 |
| 来源 | 每项 evidence_id、等级、source_type/ref、content、content_hash、有效窗、observed_at、supersedes、当时状态；保存实际使用策略的配置、配置 hash、版本/确认/有效窗及当时权限结论。输入缺证时保存 issue，不能生成占位 VALID 证据。 |
| 算法 | 各阶段实际算法/协议版本，而非一个模糊“当前版本”；应能判断当前代码是否仍支持重放该版本。 |
| 约束 | 稳定 constraint_key、来源/策略引用、硬或软、日期/阶段、required/available、可为 null 的满足状态、带符号 margin、reason_code 和计算参数。未知不能显示为满足或金额 0。 |
| 候选 | 候选产品/动作/来源与确切版本、上限、费用/损失、净额、退出计划、模拟收益、淘汰理由；保留 CASH 零操作与安全恢复停止理由。未运行的候选标明 NOT_EVALUATED，不事后猜过滤原因。 |
| 结果 | 原四级结果、financial_evaluation、当时 execution_eligible、是否/为何需要人工、已确认事实；实际/预计边界分开。302 ASK 在确认后仍保留 ASK 来源。 |
| 解释 | 确定性 reason 模板与结构化参数/引用生成的中文要点。可解释金额与取舍，不编造未保存的计算过程；未知原因使用明确缺项说明。 |
| 完整性 | 规范化 input hash 与 trace 内容 hash；来源 hash、financial boundary hash、经济确认 effect_hash、302 候选签名分开，不能混作一个授权 hash。 |
| 执行关联 | 原 action/request hash、银行 operation、完整 posting 与 receipt IDs、实际经济到账/观察/对账时间；预测差分与已确认实际差分各自标记，T1 未到账不得显示已恢复。 |

建议只读接口复用计划 `GET /decisions/{id}` 和 `/explanation`，并提供从 action/receipt 到主 run 的稳定 ID 或链接。所有链路都按用户校验，外部用户返回 404；查询不调用 execute、project、最新自主分级或写证明。

记录入口建议拆成 `build_trace(validated_inputs, outputs, source_copies)` 纯构造、`record_decision(session, event_identity, trace)` 幂等持久化、`read_decision_trace(session, user_id, run_id)` 只读核验。纯重放只能消费保存的输入与匹配算法，返回差异报告；它不能产生 ActionPlan、预留或银行动作。算法不支持时返回 UNSUPPORTED_VERSION，缺原始输入时返回 LEGACY_PARTIAL，不能回退使用最新事实。

## 持久化与最小迁移建议

优先复用 DecisionRun/DecisionConstraint/ActionPlan/Receipt。无需仅为轨迹另建一套账户或资金账本，也无需提前增加审计哈希链。

- 准备阶段的主 DecisionRun 冻结完整 trace；现有 `ActionPlan.decision_run_id` 继续指向它。确认重验、执行重验是不同 as_of 的事实判断，建议记录为子 DecisionRun，不覆盖准备输入。实际银行结果仍保存在现有 operation/posting/receipt 中。
- 若需要可靠查询所有阶段，最小关系迁移建议给 DecisionRun 加可空 `parent_run_id` 与 `subject_action_plan_id`，使用同用户组合外键和用户/动作/阶段查询索引。主 run 在动作创建前可为空，子阶段再绑定；避免在现有互相引用中要求创建时双向非空。
- schema_version、版本化 trace 内容和 trace_hash 可先存在既有 JSONB envelope；不必为每个 DTO 字段增加列。旧 recovery `result.notifications` 等可变内容放在冻结 trace 之外；内容 hash 仅核对明确的冻结范围，不能随结果通知更新而改写原决策依据。
- 若选择无 DDL 的 JSON 关联方案，必须明确应用层同用户校验与索引策略；它不具备新增组合外键的数据库保障。建议在 303 开始前冻结一种方案，不同时维护两套真值。
- 既有旧 run 不回填伪造来源、候选或计算。按旧协议提供 LEGACY_PARTIAL 与已知字段；新录制后的完整性要求适用于新 schema。演示种子可在授权重置时生成真实新轨迹，不能修改历史记录使其看起来当时已完整记录。
- 区分“决策计算完成”“动作执行完成”。新的 trace 计算成功不等于银行付款成功；已完成判断可以是 BLOCKED/ADVISE/ASK。不能继续用一个 PENDING/SUCCEEDED 标签同时表示两者。

记录必须来自同一次受控快照和真实函数返回值，不可在执行后重新读取余额拼出“执行前输入”。同一幂等准备重放返回原主 run；重复已受理操作或 GET 不追加新决策。确有新的执行前重验时使用明确阶段事件身份关联原 action，不能把网络重试次数当作新金融意图。

失败/阻断记录的事务也需明确：在最终抛错而回滚的事务里写 trace 会丢失。建议显式记录接口可提交结构化 BLOCKED 结果；执行阶段失败记录须按原有事务边界持久化，不能为了保留日志提交本应回滚的资金变更。银行已受理但应用投影失败时，旧 trace 和独立银行事实均保留，后续对账补同一关联。

内容 hash 只提供当前保存内容的一致性核验，不能据此宣称已防止管理员同时篡改内容与 hash；跨事件链与更强防篡改证明留 MVP-304。解释中的“审计哈希链验证”在本项应明确 NOT_IMPLEMENTED，而非输出通过。

## 待实现验收矩阵

以下均未在本次运行，必须待 302 验收后按真实 TDD 执行。

| 场景 | 必须断言 |
| --- | --- |
| 五类 301 动作成功，以及 205 自然到期/恢复 | 回执能沿同用户链找到原输入、策略版本/确认、证据等级/hash、完整计算与原因；自然到期不补新授权。 |
| ASK 准备→确认→执行 | 准备与 fresh 重验有各自时钟/依据；级别保留 ASK，确认绑定原 effect；不把人工参与改为 AUTO。 |
| 产品选择与目标分配 | 原 T0/T1/fixed/CASH 候选、明确退出日期/收益比较、拒绝理由，以及目标 min/target/max、合格 lot/FIFO/本月贡献可定位；后改目录不改变历史解释。 |
| BLOCKED、ADVISE、无动作结果 | 明确记录未执行及原因；无 effect 建议保持 NOT_EVALUATED，无虚构金额、回执或安全结论。 |
| 有限金额世界稳定/分歧 | 保存 2..8 候选、共同来源 hash、白名单范围与原结果；分歧无可执行单一载荷；展示每世界依据不等于再次授权。 |
| T1 等待、晚查询、撤权后已受理结算 | 准备/预测/实际/观察时刻分开；未到账仍风险，已受理事实不因撤权删除；GET 不结算。 |
| 金融事实/策略/证据后来变化 | 旧 trace 仍展示当时输入与版本；当前变化只作另附说明，不重写原判断。原引用被篡改则完整性失败，不偷偷重算掩盖。 |
| 重复幂等键、网络重试、UNKNOWN 与补账 | 原主 run/action/operation 不重复；可区分新的重验阶段；故障不会丢掉原依据或制造第二次资金效果。 |
| 来源、约束、结果或回执单项篡改 | trace 内容 hash/关联核验失败；现有 receipt posting 校验继续生效。不能把 303 内容核对称为 304 哈希链已完成。 |
| 跨用户 run/action/evidence/receipt 组合 | 404 或明确完整性拒绝，无越权快照；迁移组合外键拒绝跨租户关系。 |
| 历史旧 run、未知算法/协议版本 | 返回 LEGACY_PARTIAL / UNSUPPORTED_VERSION；不猜候选、不补证、不用最新策略复现旧授权。 |
| 解释与读取 | 相同冻结 trace 产生相同解释；理由可追到具体字段/计算；重复读取所有业务表零写入，不调用外部模型编造金融原因。 |
| 迁移与性能边界 | 升降级可测，已有动作/回执外键不破坏；trace/candidate/source 数量有上限，列表使用稳定分页，不一次加载所有历史。 |

## 实施前需冻结的语义与分工

1. **记录时点与只读边界：** 哪些显式命令创建主 run，哪些阶段创建子 run；302 assess/GET 继续只读。是否增加单独“保存本次评估”命令应明确，不让页面读取自动产生记录。
2. **历史完整性与重放：** 完整 schema 从哪个版本开始，旧记录如何标部分；当时快照、当前引用完整性、当前权限状态各自表达；何种算法版本可重放，不能把历史解释当作现时可执行判断。
3. **不可变内容与运行状态：** 冻结输入/候选/结果的 hash 范围、可变通知/银行进度的边界、不同 as_of 的子阶段关联与失败记录事务；303 不声明 304 审计链已实现。

建议领域 owner 负责严格 Trace DTO、规范化内容核验与确定性解释；持久层 owner 负责最小关系迁移、记录与查询、历史版本兼容；执行 owner 负责准备/确认/银行/对账接缝，确保不改变三阶段资金事务；独立审查 owner 负责从真实 receipt 反向核对、跨用户和篡改/晚到/旧版本测试。API/合同、示例和最终完整验收由 root 统筹。具体文件修改权在 302 验收后再分配。

## 本次工作记录

只用 `rg`、`Get-Content` 阅读计划、模型、301—302 与相关规划/恢复源码，并新增本文件。未运行测试、迁移、种子或合同生成；没有 303 红绿证据、通过数量或运行交付。下一步是等待 302 全量验收，再冻结上述语义与任务边界。
