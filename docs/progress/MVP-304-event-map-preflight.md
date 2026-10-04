# MVP-304 真实事件与事务接缝预审

状态：`PREFLIGHT_ONLY`，2026-10-04。本文在 MVP-303 owner 冻结后进行只读源码检查；303 完整验收前不实现 304 源码、迁移、合同或测试。本文建议尚未冻结，也没有运行资金动作或 304 验证。基线说明见 [MVP-304-preflight.md](MVP-304-preflight.md)。

后续收敛的最小事件集合、准确函数位置/执行顺序与幂等行为见 [MVP-304-hook-design.md](MVP-304-hook-design.md)；本文第 2 节的备选事件名由该设计合并，不应照表全量重复追加。

## 1. 任务依据与现状

初版计划 MVP-304 要求 append-only 审计事件、`scripts/verify_audit_chain.py`，以及修改一条历史记录后验证失败。初版 8.6 和完整计划 13.1 要求从事件追到原 run、事实、证据、策略版本、约束与候选、自主等级、人工确认、请求、回执及对账；13.2 要求解释来自原结构化轨迹。13.3 的完整实验导出目录和 FULL-803 的后续强化不应提前扩展成签名服务、外部存证或第二套资金账本。

源码依据：仓库根目录两份开发计划的初版 8.6/MVP-304、完整版第 13 节；`apps/api/app/db/models.py:478` 的 `AuditEvent`；下表列出的现有服务；`scripts/tasks.py:199` 的 seed/reset 和 `:209` 的 audit-verify 入口。

当前 `AuditEvent` 已有同用户 sequence、idempotency key、event hash 唯一约束，previous hash、payload version、经济/观察时间、correlation/causation，以及同用户 run/action/receipt/causation 外键。模型未提供 head/epoch。当前生产服务没有正常追加 `AuditEvent` 的调用；demo seed 只会修改、删除旧事件。预留的验证脚本尚不存在。303 的 `trace_hash` 与 `input_hash` 已保存，查询及解释仍明确返回 `NOT_IMPLEMENTED`，不能据此宣称审计链已实现。

## 2. 应写入真实事务的事件清单

以下事件名是建议协议名。事件只有随表中事务成功提交才存在；回滚中的记录不能变成“业务已发生”的事件。公共 append helper 应接收现有 `Session`，不得内部另开事务、提交或调用银行。

| 真实事实／建议事件 | 现有接缝与事务 | 必须绑定的原始对象与差分 | 重试及失败边界 |
| --- | --- | --- | --- |
| 显式保存评估／`DECISION_RECORDED` | `decision_assessment.save_assessment:34` 独立 `Session.begin`，用户锁；最后调用 `decision_trace.record_trace:359` | EVALUATION run、原 intent/options、schema/算法版本、原 trace hash；BLOCKED/ADVISE 也是已完成的决策 | 同 key 相同评估返回原 run；302 的只读 assess 不追加。决策完成不等于资金成功 |
| 准备及各阶段冻结轨迹／`DECISION_RECORDED` | `decision_trace.record_trace:359`，由调用方控制原事务 | PREPARE/CONFIRM/RESERVE/BANK_ACCEPT/RECOVERY_PLAN/CONTRACT_SETTLEMENT 的 phase、run/parent/action、as_of、input hash/trace hash | 只在首次录制成功时追加。再次录制相同轨迹返回原事件；GET/list/explanation 不追加 |
| 计划已创建／`ACTION_PREPARED` | `execution.prepare_action:40` 的应用事务，ActionPlan 与 PREPARE 一起提交 | action、原 effect/request hash、原自主等级、状态、计划 run、资源/收入声明 | 原 key 返回原动作，不产生新计划或第二个事件。若采用 trace 事件承载此事实，可合并，须避免重复计数 |
| 人工确认已接受／`ACTION_CONFIRMED` | `execution.confirm_action:226` 的应用事务；先写 confirmation Evidence，再重验并录制 CONFIRM | `USER_ACTION_CONFIRMATION` 证据 ID/content hash、accepted 原事实、effect hash、confirmed/expires 时间、CONFIRM run、状态变化 | 后续验证失败会回滚证据和确认，不留“确认成功”事件。原 ASK 不能改写成 AUTO；重复确认返回原结果 |
| 声明已预留／`ACTION_SUBMITTED` | `execution.execute_action:301` 第一个应用事务；RESERVE、资源/收入预留、SUBMITTED、epochs/exposure 一起提交 | RESERVE run、真实 claim IDs/金额/状态、原 action/effect、收入预留、前后状态 | 已提交后遇银行或投影异常，声明仍在；不能用审计重试释放或新建动作 |
| 银行实际受理／`BANK_ACCEPTED` | `execution_bank.process_operation:48` 独立银行事务；`execution_sources.verify_execution_sources:418` 在该事务内新建 BANK_ACCEPT trace，随后创建 BankOperation | 原 request/effect hash、operation/action IDs、fresh BANK_ACCEPT run、requested/available 时间、ACCEPTED 状态 | 已有原 operation 时跳过新授权；不能因每次读取/重试再记一次受理。验证 trace 与受理都随银行事务回滚 |
| 银行实际结算／`BANK_SETTLED` | `execution_bank._settle_operation:432`，仍在 `process_operation` 银行事务 | 原 operation、实际完整 posting legs 的 IDs/hash/金额/方向、settled_at、ACCEPTED→SETTLED | T0 可与受理同次提交两个有序事件；T1 到期才追加原 operation 的结算，不重新授权、不新受理 |
| 应用已投影并生成回执／`ACTION_PROJECTED` | `execution_projection.project_execution:843` 的 savepoint，外层为 `execute_action:393` 的第三个事务 | 原 operation/settlement 事件、receipt ID/request hash/金额/fee/loss/actual posting set、原 transaction/evidence IDs、声明消费与前后状态 | ACCEPTED 返回 None 时不能记资金完成。已有合法回执直接返回，不产生第二回执/投影事件。savepoint 或外层回滚则事件一并回滚 |
| 丢失响应或投影失败／`ACTION_UNKNOWN` | `execution._mark_unknown:483` 的独立应用事务 | 原 action/operation IDs、已知状态、UNKNOWN、声明仍占用、可信观察时间；错误分类只说明观察失败 | 不臆测银行失败，不释放声明。重复同状态重试不冒充新经济事件；若需要多次诊断，单独定义 observation 协议 |
| 可证实无经济效果后失效／`ACTION_INVALIDATED` | `execution._record_bank_refusal:498` 独立应用事务，在用户锁下核原 operation 和非 OPENING legs | 独立 absence 或原 REJECTED/无 legs 的证明、action、释放的 claim/收入、状态/epochs 差分 | 无法证明 absence/rejection 则记 UNKNOWN，不能写“撤销成功”。此前银行已提交的事件不会回滚 |
| 恢复决策及原合同到期／`DECISION_RECORDED`、必要的 `RECOVERY_PLANNED` | `recovery.run_recovery:128` 第一事务；RECOVERY_PLAN 与各 CONTRACT_SETTLEMENT trace、ActionPlans 一起提交 | recovery run、0 至多项 action、来源、原合同、`new_authority=false`、原 request hashes | 合法零动作也是一次决策，不是假回执。成熟合同不是新 AI 投资授权；原 idempotency key 返回原动作集 |
| 205 赎回实际受理/结算／`BANK_ACCEPTED`、`BANK_SETTLED` | `simulated_bank.process_redemption:389` 独立银行事务；`:523` 录 BANK_ACCEPT；`_settle:347` 创建守恒 legs | 原 immutable BankRequest、SimulatedBankRedemption、原 action/position、成熟/赎回来源、统一 BankOperation 对应关系、两条实际 legs | legacy request 与其 `LEGACY_REDEMPTION` BankOperation 是同一经济操作，不双记受理/结算；已有原请求跳过新授权 |
| 恢复投影/回执／`ACTION_PROJECTED` | `recovery.run_recovery:335` 应用事务，每 action 的 `begin_nested:364` 调 `recovery_projection.project_request:161`；随后 finalize epochs/exposure | 原 request/postings、receipt、PRINCIPAL_RETURN transaction/evidence、真实 position/account/goal 差分 | ACCEPTED 只能表示等待/REDEEMING，无回执、无 recovered cash；UNKNOWN 不投影。一个 savepoint 失败不删除其他成功动作或银行事实 |
| 恢复批次结果／必要的 `RECOVERY_OBSERVED` | `recovery.run_recovery` 第三个事务中真实更新 run/通知后 | 实际成功/等待/失败 action IDs 与各原回执，不把 mutable notification 当冻结决策内容 | 批次观察与每 action 经济事件分开；不因单项报错把已提交批次投影伪装成整体回滚 |
| 明示策略确认/新版本／`POLICY_CONFIRMED`、`POLICY_VERSION_CHANGED` | `policy_lifecycle.confirm_proposal:572`、`change_policy:642` 在原 session savepoint；API `database_session` 控制最终提交 | 原确认 evidence/hash、version/config hash、生效/过期范围、前后版本、实际 invalidated 和 retained inflight action IDs | 原 confirmed proposal/change key 返回旧结果；改 payload 的 key 冲突。候选/LLM 输出不等于授权 |
| 暂停/撤销/显式时钟刷新／`POLICY_STATE_CHANGED`、必要的 `ACTION_INVALIDATED` | `_stop_policy:726`、`refresh_time_states:764` 的原 savepoint；用户/策略锁 | 真实状态差分、原版本、经济生效与观察时间、实际被失效计划、保留 SUBMITTED/UNKNOWN | 状态未变不复制生命周期事件；重复暂停仍可能检查/失效新计划，需按实际 action transition 去重。GET 的 effective_status 不持久化、不追加 |
| 新目标的零初始投影／建议 `GOAL_INITIALIZED` | `goals.create_goal_projection:47`，使用调用方事务，用户/策略锁 | 原策略版本、账户、goal ID、零 ownership/contribution evidence 与独立 opening | 原 goal 返回旧结果；零初始化不能重开已有正额资金事实。此为事实初始化，不是资金转账 |

决策事件以 303 冻结轨迹为内容锚点，不重新执行算法。动作/银行/投影事件以真实原请求、posting set、回执和最小状态差分解释执行结果；可通过 correlation 关联同一原 run，通过 causation 指向同一用户已经追加的直接原因事件。批次包含多个动作时不得让后一个 action 的 causation 随意引用另一个 action 的结算。

`record_trace` 是集中录制锚点，但不是所有经济事件的唯一写入点。BANK_ACCEPT trace 当前发生于银行新受理验证过程中，银行实际 ACCEPTED/SETTLED 事件仍需在银行服务添加；只记录 trace 会漏掉 T1 后续结算及 UNKNOWN 后同请求恢复投影。旧 run/动作没有当时审计记录时，读取应明确未审计/旧历史，不由 GET 回填。

候选发现/编译以及曝光/收入/ownership proof 的多处自动 supersede 是可选的进一步事实事件，不建议 MVP-304 逐条照抄成独立事件。原 decision sources 和提交事件应绑定用到/生成的完整证据 IDs 与 hash；若未来新增明确导入/人工修正入口，再单独定义事件协议，不能声称已覆盖尚未存在的入口。

## 3. 303 只读与 UNKNOWN 必须保留

- `decision_trace.get_decision_trace:675`、`get_action_trace:711`、`list_decision_traces:752` 以及结构化 explanation：查询与完整性核验，不 append、补轨迹、刷新生命周期、对账或修复。
- `recovery.preview_recovery:70`、`get_recovery_run:429`，302 autonomy/intent assessment，goals/policies 的 GET，以及原 readonly receipt verifier：保持零写入。访问日志不写入这条资金/授权审计链。
- `api/dependencies.get_session` 的 GET 使用 REPEATABLE READ；303 服务另有 `no_autoflush`。新 audit 查询/verify 必须在同一个稳定快照内核链与 head。若 `POST /audit/verify` 保留计划中的接口，也只返回核验结果，不借 POST 添加事件。
- 合法 source status supersede 或 policy lifecycle 后续变化只作 current reference 注记，不改封存 trace/hash；当时已捕获 INVALID 的内容及 declared hash 不能被验证器“修正”，也不能使合法历史 BLOCKED 记录无条件失败。
- 真正银行结算已独立提交、HTTP 响应丢失或应用 savepoint 失败时：银行 ACCEPTED/SETTLED 事件必须存在；应用保留原 action、资源/收入占用、UNKNOWN，无应用回执。重试同原请求只能追加首次恢复投影/回执事件，原 PREPARE/CONFIRM/RESERVE/BANK_ACCEPT 和银行事件保持不变。
- T1 ACCEPTED 无 posting/receipt 时只能等待；到实际可用时结算原合同。政策随后撤销不能追溯抹掉原已受理合同，也不能给尚未受理的新请求开绿灯。occurred_at 记录经济事实时间，observed_at 记录可信观察时间；迟到投影不能倒排 sequence。

## 4. 最小 append 与数据库保护建议

1. 冻结一个明确的 event envelope/version 与 payload schema。hash 绑定事件 ID、tenant、sequence、previous hash、type/payload version、全部关系 IDs、原规范 JSON payload、occurred_at/observed_at。固定 UTF-8、排序 key、UTC 时间、整数分；不接受 caller 提供 event hash，不以 JSONB 显示顺序散列。若 `created_at` 参与 hash，显式冻结时间后再算，不能让 DB 默认时间产生未绑定的内容。
2. 沿用现有用户行 `FOR UPDATE` 的锁顺序，串行追加同用户事件；空链也受同锁保护。读取/核验可信 head 后分配 sequence，再在同一原事务写 event 与 head。不使用 `max(sequence)+1` 裸读并把 unique 错误当并发方案；不在外层持有用户锁时再开新 Session 追加。
3. 同用户/type/不可变 subject/协议派生稳定 key。相同 key 首先核现有事件和原语义，返回原事件；不同原 payload/关系拒绝。重试的当前时钟不得覆盖原 observed_at，不能因新时钟把正常重试误判 payload 冲突。暂停/撤销 API 当前没有请求 idempotency key，须从真实 transition 生成稳定键；纯 no-op 不追加。
4. 追加前核所有 typed/live 关系属于同用户，causation 只能指向已有较早 sequence 的同用户事件；aggregate/correlation 目前不是 typed FK，不能仅凭 UUID 放行。事件不得引用其他用户的 run、action、receipt 或证据。严格验证 payload 协议，未知协议返回 UNSUPPORTED，不跳过后继续报完整。
5. DB 对 audit_events 的 UPDATE/DELETE 加拒绝 trigger，并保护 TRUNCATE；runtime 角色没有 UPDATE/DELETE/TRUNCATE 权限，不拥有表。INSERT 也应经过经验证的追加接缝（受限函数或等效 DB 校验），不能允许普通 SQL 插入孤立、断链或错误 head。head/seal 只允许与合法 append/reset 原子配套更新，不留下通用可写旁路。
6. 明确威胁边界：应用和普通 DB 写者防护、同库独立 head 核验，不等于能抵抗 table owner/superuser 禁用 trigger 并同时改链/head。运行配置当前是否分离 runtime 与 migration/reset 角色须在实施前实查；此处仅建议，未宣称已有角色隔离。FULL 外部签名/锚定不得提前包装为 MVP 结果。

事件追加失败应使其所属业务事务一起回滚。对已独立提交的银行事务，不允许应用记录失败反向修改银行事实；若银行结算事件与结算在同事务，事件写失败会阻止该次银行提交，后续原请求可重试。不能吞掉审计写异常然后仍宣称该事务被完整审计。

## 5. 删尾检测与核验器最小设计

单独检查相邻 previous hash 和当前内容 hash 无法发现删除连续尾部，删除整条链也会得到“空链有效”。因此链外必须有预期尾部。

建议最小新增每用户/epoch 的受保护 head：expected sequence/count、tail event ID/hash、protocol/epoch、必要的前 epoch seal。每次追加与 head 更新同事务。readonly verify 按 sequence 检查从 1 开始、无缺号/重复、first previous hash、逐条原 envelope/hash、causation/tenant 关系、协议、最终 count/tail 与 head 一致，并核 303 原 trace hash 和原请求/receipt 锚点。正常的当前 policy/evidence 状态变化不参与原 hash 重算。

核验结果区分 VALID、INTEGRITY_ERROR、UNSUPPORTED、旧历史未审计；不能将未实现/未知版本视作 PASS。脚本使用同一服务语义、返回机器可读的 user/epoch/head/count/error 与正确 exit code，不能调用 seed/reset 或在核验过程中“修链”。API 列表分页按 sequence/epoch 固定 cursor，不能用 occurred_at 当链顺序。

同库 head 能检测只改事件的删尾/清空/中间删除；若具备同时重写 events 与 head 的管理员权限，则它不是外部证明。验收证据中至少保留一次独立预期 head/count/hash 的 manifest，支持向脚本提供可信 checkpoint；它可以检测相对该 checkpoint 的删尾或整链替换，但不能声称尚未观察的未来尾部也有外部锚定。更强签名/独立存证留给 FULL-803。

## 6. 演示重置的硬接缝与建议选择

当前 `demo_seed.seed_demo:716` 在一个事务中取得固定 advisory lock，检查预留模拟身份/产品，`_clear_demo:373` 更新审计 causation 后删除事件、receipt/action/run/来源/账户/User，再重插相同确定性 ID 的合成事实和银行 openings。`scripts/tasks.py` 的 seed 与 demo-reset 都走此路径。直接启用审计 append-only guard 会在 UPDATE/DELETE 处拒绝，保留 audit 行又会由现有 run/action/receipt/User 外键阻止后续删除。这里只追加一个 RESET 事件解决不了这个结构冲突。

建议 MVP 采用显式、仅用于预留合成身份的受限 archive-and-reset 事务，冻结为审计生命周期例外，而不是暗中放宽普通追加保护：

1. 保持 reserved ID/external_ref/is_simulated 校验、用户/advisory 锁、固定合成数据与产品冲突检测；校验整个旧链/head，再封存旧 epoch 的完整 canonical 事件、预期 head 和可追溯的冻结 trace/request/receipt 内容。旧 epoch 若无 304 事件也明确标旧历史，不伪造当时审计。
2. 在**不可被 runtime 修改或删除**的 archive/seal 中保存旧 epoch。封存必须复制真正内容，不能只存会被 reset 删除的外键或文件路径；同固定 UUID 在下一 epoch 重用后，旧事件解析始终使用原 epoch 的封存内容。
3. 只允许单一受限 reset 操作移动/清理该 reserved demo 的 active 审计及业务行。ordinary SQL/API 不可获得通用 bypass；拒绝让任意 session `SET` 一个开关即能删任意用户事件。保护函数/角色/触发器边界要同时覆盖 archive/head 的写入与删除。
4. 旧 epoch seal 与新 epoch genesis/RESET 事实绑定，保留旧尾 hash、reset 身份/原因、可信 observed_at、新 seed version/dataset hash；epoch 来自不能一同删掉的持久元数据。下一 epoch 自己 sequence 从 1 开始且明确引用前 seal，而不是把旧历史丢掉后称从未发生。
5. 校验、封存、清理、重建、sealed head/new genesis 一起提交；任一步失败整体回滚。不允许银行动作运行期间并发 reset。reset 三次后各旧 epoch 可核验，新 dataset 与既有 seed 契约一致，其他用户事件/事实、全局产品与已有原始测试证据都不清理。

该方案的准确承诺是“普通运行的事件只追加，演示 reset 显式封存换 epoch”，不是“audit_events 物理表永远无删除”。若必须字面满足所有物理审计行永久不删，则应选择保留旧业务行、按 epoch 隔离新 demo 的替代方案；这需要把 epoch 扩展到 run/action/receipt/来源/确定性 IDs 与当前查询，范围明显更大。实施前需由 root 冻结选项，不应为了赶 304 用同库无保护 GUC、无归档 DELETE、改审计 FK 为 NULL 或禁用所有 trigger。

## 7. 实施后的必要验证目标（未执行）

| 目标 | 必须证明的真实边界 |
| --- | --- |
| 正常追加、key 重放、并发 | 真实 PG 同用户连续 head/sequence；重放不新增事件、改原 payload 拒绝；不同用户隔离 |
| DB 只追加 | 普通 UPDATE/DELETE/TRUNCATE 被拒绝；正常 append 成功；reset 例外不可由普通角色泛用 |
| 内容与链篡改 | 原 payload、关系、时间、sequence、hash 或中间记录改变失败；修改内容并重算其自身 hash 仍与后续/受保护 head/checkpoint 不符 |
| 删尾/清空 | 尾部、中间、全部事件删除均与 expected head/count 不符；外部 checkpoint 明确报告同库 head 被一并替换的差异 |
| 实际动作链 | ASK 的原人工 proof、四执行 phase 与真实银行请求、T0/T1 legs、回执/声明消费，205 legacy 同操作不重复计数 |
| 独立提交与故障 | 实际银行 commit 后响应丢失/应用 savepoint 失败：银行事件仍在，原 UNKNOWN/声明仍在，无回执；原请求重试只生成首次投影事件 |
| 生命周期和历史 | 更换/撤销/过期事件只描述真实 transition；合法 supersede 不毁原 trace；历史/未知协议不伪造已审计 |
| 只读 | 303 GET、audit GET/verify、preview、302 assess、receipt verifier 的全相关表 dump 前后一致，无 autoflush/修链 |
| 演示 reset | 三次 epoch/head/seal/archive 验证；旧同 UUID 解析不串入新数据；reserved 身份冲突拒绝，其他用户原内容保留，失败整体回滚 |
| 脚本与验收 | audit-verify 真正存在且 exit code 有效；独立 RED/GREEN、静态检查、完整 check 和证据 source/hash 归档后才能改 COMPLETE |

以上是后续实施验收目标，不是已写测试或通过结果。本次交付仅此预审文档；303 源码、合同、owner 测试与此前真实 PG 证据继续冻结。

## 8. 本次执行记录与交还边界

- 修改文件：仅 `docs/progress/MVP-304-event-map-preflight.md`。
- 已执行：PowerShell `Get-Content` 定向读取两份计划、现有预审和所列模型/服务/事务；`rg` 核服务定义、审计引用和脚本入口；`Test-Path scripts/verify_audit_chain.py` 返回 False；`git status --short -- <本文路径>` 核新文档。
- 测试结果：本次未运行测试、资金动作、seed/reset、迁移或 audit-verify，不将源码检查记为 runtime 证据。
- 下一步前置：root 完成 303 正式验收后，先冻结事件协议、head/删尾威胁边界及 demo reset 选项，再进入 304 的独立 RED/GREEN 与迁移实施。
