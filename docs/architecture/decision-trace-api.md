# 历史决策轨迹 API（MVP-303）

所有资金动作和来源均为合成模拟。主合同为decision-trace-v1，算法/来源/策略/输入/候选/约束/结果和两个独立hash被冻结；历史解释不使用新事实重算。语义与事务边界见[ADR0011](../adr/0011-immutable-decision-traces.md)。当前任务验收状态见[MVP-303](../progress/MVP-303.md)。

| 接口 | 用途 |
|---|---|
| GET /api/v1/decisions?limit=20&cursor=... | 按原as_of/id稳定分页，limit为1–100，next_cursor为空表示无后页。 |
| GET /api/v1/decisions/{run_id} | 冻结原轨迹、确定性解释、当前来源状态附注、子阶段IDs及动作/银行/回执关联。 |
| GET /api/v1/decisions/{run_id}/explanation | 同一冻结轨迹的结构化中文解释；旧记录或未知算法不能临场补解释。 |
| GET /api/v1/actions/{action_id}/decision | 从动作和回执链查原主run，返回与主历史接口相同的envelope。 |
| POST /api/v1/decisions/assess | 明确保存一次评估，严格body为idempotency_key、intent及可选amount_options_cents；不生成动作、预留、确认或银行请求。 |

查询只接受表内字段；客户端user_id、时钟、权限和任意worlds拒绝。用户身份与时钟来自服务器。跨用户404，内容或关系不一致409，非法查询/输入422；读取时钟早于原as_of时返回409 DECISION_NOT_YET_OCCURRED。原POST /actions/assess及GET /actions/{id}/autonomy继续只读。ActionResponse新增decision_run_id主记录入口。

显式保存有限金额偏好时仅支持同一对本人账户划转，2–8个不同正整数分金额。原候选生成器使用独立的同库REPEATABLE READ/READ ONLY事务；所有候选、原共同来源摘要和逐世界上下文被保存。请求中的原intent仅保存用户提交意图，有限候选的实际计算金额由完整options集合提供；不把不确定集合转换为单笔可执行载荷。回答后仍需重新准备与重验。相同幂等键和原输入返回原run，改变原输入409。

## 读取字段

completeness为COMPLETE、LEGACY_PARTIAL或UNSUPPORTED_VERSION。前者表示冻结合同和关联核验完整，不代表当前资金权限或304哈希链通过。read_at与原as_of分开；原trace/explanation稳定，current_references另附正常SUPERSEDED/策略状态变化。当前原内容/配置不同于冻结副本、约束投影删改、动作/回执与原经济请求不一致均拒绝。旧run原快照和已知结果按legacy字段展示，不回填不存在的当时证据。

301的PREPARE、CONFIRM、RESERVE、BANK_ACCEPT分别保存实际阶段输入和ExecutionValidation，全部原边界检查点及资源预留调整计算保留。ASK确认后仍保留原等级；确认证据只能满足本笔原effect_hash。RECOVERY_PLAN保留完整原恢复输入/候选；CONTRACT_SETTLEMENT标记原合同到期，不能理解为新的自主权限。205旧恢复的BANK_ACCEPT保存它实际采用的validation_inputs，financial_evaluation为NOT_EVALUATED，约束为空；不声称运行301新决策算法。

input_hash与trace_hash仅用于该冻结记录的一致性；effect_hash绑定本笔经济后果，boundary_hash绑定财务计算，候选signature用于动作稳定性比较。证据与策略另保存原声明hash和实际capturehash，两者不一致时完整性标INVALID；其它来源有效性问题由原结果解释BLOCKED。audit_chain/audit_chain_status当前明确NOT_IMPLEMENTED，304后续实现。

来源content和策略configuration保留有界原始JSON，包括导致原决策拒绝的错误金额类型或owner声明；这不放宽受信金额和typed行owner。VERIFIED仅表示声明hash与实际副本一致，财务或权限有效性仍由原结果说明，不能把hash一致当作有效来源。

显式录制遇到实际不存在的来源时，保存 `inputs.missing_evidence_references` 中的原引用ID及原BLOCKED原因，缺失对象不生成占位证据或有效副本。只有这一明确记录路径允许缺失引用；默认来源capture继续严格缺项404，实际存在的跨用户引用仍404。

历史回执只读核验完整银行分录、金额/费用/损失、经济与观察时钟以及原交易证据；不投影、不结算、不更换当时权限。T+1在途查询不会把预计到账改为实际到账。
