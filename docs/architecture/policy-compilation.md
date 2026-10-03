# MVP-105：候选编译持久化与编辑

`compile_candidate(session, user_id, text, now, engine="rules", llm_enabled=False, provider=None)` 调用纯编译器并持久化原文和结果。`revise_compilation(session, user_id, compilation_id, configuration, now)` 接受用户编辑后的完整配置，保留原编译记录。两者返回 `CompilationResponse`：`simulation=true`、`user_id`、`compilation_id`、原始 `compilation`、本次 `configuration` 及 `configuration_hash`、`proposal_id`、`proposal_status`。`compilation` 始终表示原编译结果；编辑后的有效候选配置放在独立 `configuration` 字段。

服务没有真实银行或模型网络适配器，不触碰余额、交易、账单、持仓、目标、策略、策略版本、动作或回执。生成完整候选仅产生 PROPOSED，之后仍须通过已有确认接口审核配置摘要并明确接受。结果中的 `proposal_status` 是该候选当前的记录状态，不是执行权限。

## 原文、时间和范围

`compilation_id` 是原始 `EvidenceItem` UUID。记录为 `USER_DECLARED / POLICY_COMPILATION`，保存原文、用户、引擎、编译器版本、可信本地日期锚点、时区、完整编译结果及可选模型证据引用。原文最多 2000 字且非空。服务器必须提供 aware `now`；当前支持 Asia/Shanghai（UTC+8）和 UTC。域编译器只收到 `CompileContext{reference_date,timezone}`，不读取服务器时钟。

原编译记录在服务内只追加，不 UPDATE。数据库当前没有对所有 EvidenceItem 的 UPDATE/DELETE 触发器；该约束是应用写路径保证，内容哈希及协议绑定负责发现来源被改写，不能声称拥有管理员不可篡改存储。

原文、锚点、时区、引擎和编译器版本的 canonical JSON SHA256 形成自然幂等来源。相同原文在相同本地日期、同引擎版本下重复编译返回原记录。次日新调用 compile 是新的日期锚点；针对已有 compilation_id 的 revise 始终保留旧锚点和旧解释，不能因服务器跨日重新解释“明年”等相对日期。

原文及模型证据的 `source_ref` 同时绑定输入与完整输出：`compilation:<input digest>:<CompilationResult digest>`，共 141 字符。自然幂等查询按输入摘要前缀查找原记录，若发现多个原记录明确返回 `COMPILATION_CONFLICT/409`，不任选一条。加载和确认时均重新计算完整编译结果摘要；只改 configuration、issues 或 assumptions 后重算内容哈希也不能替换固定输出。本协议不假定管理员无法同时改写所有独立字段。

可生成和编辑的完整配置仅限 `goal_saving`、`emergency_buffer`，并独立通过严格 DSL 校验。跨目标调拨必须为 false；额外 status、accepted、auto_execute、user_id 等非该 DSL 字段被拒绝。金额只能为分整数。deadline 和 valid_until 若早于本次可信服务器本地日期则拒绝，保留原锚点不会允许一个今天已过期的期限。

不完整规则编译结果保存 draft、issues 和 assumptions，configuration/proposal 均为空。服务不把未知月度金额、投资限制或模糊约束默默补成授权；人工可通过 revise 填入完整且可审核的配置。

## 人工修订与幂等状态

完整配置的摘要与 compilation_id 组成候选幂等键。人工编辑额外追加 `USER_DECLARED / POLICY_COMPILATION_EDIT`，保存原编译 ID、原编译内容哈希、原日期锚点、编辑后配置及摘要。编辑行为本身不伪造 `USER_CONFIRMED_POLICY`。

同 compilation 的不同完整配置创建新 PROPOSED 修订，并将旧 PROPOSED 过期；原文和历史配置保持原样。同配置重放直接返回其原候选和当前状态，已 REJECTED/EXPIRED 的候选不会恢复。A→B 后重放 A（包括重放初始 compile）不使 B 过期。任何同 compilation 的候选已确认后，所有新的 revise 请求返回 409；后续修改应走策略版本 PATCH，而不是从编译入口恢复授权。

数据库事务归调用方。服务使用保存点并先取得用户行锁，与编译、修订、发现、确认使用相同用户级串行顺序；正常写事务为 PostgreSQL READ COMMITTED。并发相同编译/编辑只有一个原记录、一个对应修订。中途插入失败时，新编辑证据、候选插入及旧候选过期一起回滚。

## 可选模型边界

`CandidateProvider` 仅定义同步 `propose(text: str, context: CompileContext) -> dict[str, Any]` 协议，没有 SDK、密钥、网络实现或自动启用逻辑。默认引擎为 rules；rules 模式无论开关如何都不调用 provider。LLM 模式要求服务端显式启用和注入 provider，不能从候选文本或模型字段启用。

模型只能返回候选 JSON 对象。服务对输出再次独立做 JSON、严格 DSL、当前日期及 MVP 范围校验。格式正确但配置违规的模型结果作为带 issues 的草稿保存，不生成 proposal；非 JSON 对象直接报错，不落部分记录。模型不能决定证据等级、用户身份、确认状态或权限。

原文仍保存为 USER_DECLARED；另追加 `MODEL_INFERRED / POLICY_COMPILATION_MODEL`，标记适配器类型、原文、上下文和实际候选。原编译证据记录模型证据 ID 与内容哈希。人工编辑模型候选后，候选保留原文、模型、编辑三个来源，既不会丢失模型来源，也不会把它升级为用户确认。相同 LLM 输入和锚点重放复用既有结果，不再次调用提供方。

| 条件 | 响应 |
| --- | --- |
| LLM 开关关闭 | `LLM_DISABLED`，422；不调用 provider |
| 开启但 provider 缺失 | `LLM_UNAVAILABLE`，503 |
| provider 抛出异常 | `LLM_UNAVAILABLE`，503；保存点回滚 |
| provider 输出非严格 JSON 对象 | `INVALID_LLM_OUTPUT`，422；无部分记录 |
| JSON 对象违反配置或范围 | 带 issues 的已保存草稿，configuration/proposal 为空 |

## 确认时的来源一致性

生命周期 `_evidence` 在基础归属、状态、观察时间和内容哈希检查后，还验证本协议的一层绑定：原编译来源摘要必须能由原文、锚点、时区、引擎和版本复算；人工编辑的 compilation_id/source_hash 必须指向本次已验证的 USER_DECLARED 原文；LLM 原编译的模型 ID/hash 必须对应本次验证集合中的 MODEL_INFERRED 模型证据。

因此原文、原编译结果或模型输出同 ID 被更正，即使重新计算了其当前内容哈希，也不能直接确认旧编辑/旧模型候选。异常为 `INVALID_EVIDENCE`。同一检查用于已有版本的授权资格读取，来源之后失效时保守拒绝资格。最终授权仍绑定用户实际审核并接受的规范化配置摘要。这里不做通用递归证据图、模型推理认证或真实银行签名证明。

## 验证记录

服务测试在真实 PostgreSQL 16 随机 `bf_test_<32hex>` 临时数据库中运行迁移和种子，未使用 SQLite。日志在 `docs/progress/evidence/MVP-105-service-*.txt`：包含初始缺失模块 red、真实来源改写 red 及对应 green；覆盖完整/不完整结果、资金事实不变、跨日锚点、并发唯一、关闭修订不复活、确认后的编辑阻止、租户和来源完整性、LLM 开关与恶意字段、provider 异常、数据库触发器注入失败回滚，以及合法模型候选经人工编辑后确认。外部模型服务与真实银行连通不属于本次实测范围。
