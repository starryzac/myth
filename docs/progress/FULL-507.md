# FULL-507 持久介入：生产服务第一包

2026-10-06，本地时间。状态 `IMPLEMENTED_SCOPED_CHECKED_ACTUAL_PG_NOT_RUN`；原编号未关闭。用户功能优先修订有效。本包新增可调用通知服务，未将文件存在、纯夹具或他人的迁移通过替代本功能实际验收。

## 已完成能力

- `domain/full_intervention.py`：严格原身份/版本/hash 请求，完整已知候选世界与最小问题语义去重，未知、缺世界或被改的签名不能假作同一问题。
- `services/full_intervention.py`：读取实际当前问答原链与 fresh 有限世界，或原单动作边界观察及其 before/after 经济签名；检查完整 DecisionTrace 和原 typed audit。仅当前已确认 InterventionPolicy 可作为介入设置来源；设置不授予银行权限。
- 新观察命令追加不可变 DecisionRun/DecisionTrace、使用原 DECISION_RECORDED 和显式新 `full-intervention-v1` 算法。通知 payload 绑定原源/hash/语义/创建命令；源问答完整世界仍保存于原轨迹，未复制成一个虚假的金融结论。
- 同用户/epoch/语义唯一消息；同原命令键返回完整原回执，不同 body 拒绝。相同经济问题合并，不因生成 run/question_id 或时钟变化重新弹；新的经济问题立即保留，不被 86400 秒介入间隔吞掉。
- 固定 consumer 唯一 Inbox claim，首次 `present_once=true`，其后 false；持久通知中心仍可读取。丢回复不证明用户曾看到，`actual_human_view_verified=false`。显式收阅 ACK 是通知回执，不回答问题，不确认动作。
- 原问题被新 revision 替换时，观察新问题会将旧待投递通知/未收阅 Inbox 标为 INVALIDATED，不改原 payload。GET 依当前实际问答来源判定 STALE/UNKNOWN，零写，不把旧问题继续呈现为可操作。
- 一次请求内 list 的同一 session fresh 读取可局部复用；字典不保存于 Session、进程或授权上下文，不跨请求缓存。

## 文件与合同

新增五文件：

- `apps/api/app/domain/full_intervention.py`
- `apps/api/app/services/full_intervention.py`
- `apps/api/app/api/v1/full_intervention.py`
- `apps/api/app/tests/test_full_intervention.py`
- `apps/api/app/tests/test_full_intervention_api.py`

共享 ORM 的 InterventionOutbox/InterventionInbox、0012 不可变与单向状态迁移、两处新算法支持和 Main/GET RRRO 由 Root 单独集成；本代理没有修改旧银行、原审计 canonical、旧资金 Outbox/Inbox、旧 hash、正式模拟数据或旧篡改测试。

实际路由：

- POST `/api/v1/interventions/observe`：QUESTION 的原 session/revision/run/hash/epoch，或 SINGLE_ACTION_BOUNDARY 的原 observation ID/hash/epoch，原 key，可选已确认介入策略 ID。拒绝客户端金额、时钟、通知内容、银行事实及授权。
- GET `/api/v1/interventions`：仅可用 limit，完整库存超过 512 则明确拒绝；展示截断有实际总数与显式字段。
- GET `/{message_id}` 和 `/commands/{epoch_id}/by-key/{key}`：RR RO，原键未找到 `NOT_FOUND_NOT_FINAL`，不能据此换键。
- POST `/{message_id}/deliveries`：仅原 epoch/payload hash，固定唯一 consumer。
- POST `/{message_id}/acknowledgements`：原 epoch/payload hash/key、严格布尔 `acknowledged=true`；恢复必须核 exact original_command/request_hash/original_receipt。

服务自动 hook 必须在原问答事务提交后读取真实已提交 revision；不能在未提交 append 内调用独立 RR 读取后假称看见原件。

## 已运行直接检查

以下最终 wrapper 均 PASSED，scoped/global source_stable 均为 true；HEAD `4ccf84e973978482a1098d18c69fbfc9f011fac6`。

| 命令 | 原证据目录 | 结果 |
|---|---|---|
| pytest 单文件 test_full_intervention.py | W5/intervention-final-direct-pure-20261005T180134Z-df9a1caa | 33 PASS，pytest 9.58s，wrapper 11.377769s |
| mypy --strict 五新文件 | W5/intervention-final-five-types-20261005T180134Z-d090bd24 | 5 文件 PASS |
| Ruff 五新文件 | W5/intervention-final-five-static-20261005T180135Z-ab5db72a | PASS |
| Ruff format --check 五新文件 | W5/intervention-final-five-format-20261005T180153Z-e5b19dd6 | 5 文件 PASS |

纯测试只证明合同/风险分支。内容覆盖原世界分母、语义去重、原源失效、恶意 extra/资金输入、严格 bool、原 ACK/hash 身份、同 Inbox 不重建、只读不写、真实 router 拒绝未知 query/body。测试库没有被启动。

首版 static FAILED（格式/未用 import）、五文件类型 FAILED（测试字典类型和 PG 夹具缺 user_id/counterparty_ref）均保留原日志和原 source.before/after；没有改写成成功。之后仅修新文件。

## 实际数据库候选与未覆盖项

唯一待 Root 运行节点：`test_full_intervention_api.py::test_actual_persistent_question_delivery_dedup_stale_sources_and_ack_keys`。

候选使用真正 seed_demo 默认 clearing、正式 prepare、小域四世界 Question START、实际已确认 86400s InterventionPolicy，检验原键和语义去重/一次 claim/重启 GET；随后真实 INCOME 两银行腿改变来源，再 REFRESH/ANSWER 产生新问题，核新问题不被节流、旧消息持久失效、ACK/原键恢复、全金融表不被通知修改。每次读取原全 physical snapshot 比较，生成金融数据只有明确正式 external fact 操作。该节点尚未执行，次数、金融成功与性能实测均未填。

具体未覆盖：

- Root 仍需自动 Question/Boundary producer hook、介入中心 UI 和实际数据库证明；目前只有明确调用的生产入口，没有后台订阅消费者。
- 原全局 BoundaryCrossed 流、全部五动作集合与全部 11 触发原因映射尚未完成。本包单动作源范围明确，`global_action_set_complete=false`。
- 只合并同问题/同经济语义重复，尚无不同无权限事实提示的定时 DEFERRED 释放器；没有为配置间隔伪造调度执行。
- Source DecisionRun 被 reset 归档而从当前表移除后，当前 get_decision_trace 无 sealed 来源读取器；通知读取会拒绝，不把 retained payload 自动认作已验真的 ARCHIVED 成功。归档来源解析及 reset 保全由 Root 后续处理。
- Deliver 丢回复恢复当前 GET 只有 `previously_claimed`；前端不得凭布尔清原门。拟补实际 Inbox claim 的原身份/consumer/hash/time 读取字段，另版本记录，不能伪造原 Delivery body 或“人已看到”。
- 全量验收、真人研究与真实浏览器投递/收阅未运行；正式数据库未 migrate/seed/reset，银行真接口未开启。

## 2026-10-06 显式差量：收件原件与介入中心

前节初版原文及其失败/成功证据保留。新版本已补 `InterventionView.original_inbox_claim`：读取真实固定 consumer Inbox 的 owner/message/epoch/hash/created_at/received_at/state，且 `actual_human_view_verified=false`。这修复了前节仅有 `previously_claimed` 无法安全读回的缺口，未补造原 Delivery 请求或人眼呈现。初版五文件字节已保存于 `.runtime/W5-intervention-first-final-20261005T1804Z/manifest.json`。

该差量最终直接纯测试为 34 PASS（pytest 8.04s，wrapper 9.585761s），证据 `W5/intervention-claim-original-final-pure-20261005T180851Z-3e3ddfc0`；五文件 strict types PASS：`W5/intervention-claim-original-repaired-types-20261005T180851Z-2a51996d`；Ruff PASS：`W5/intervention-claim-original-final-static-20261005T180851Z-e0caa330`。三份最终 manifest 均 scoped/global 稳定。初次 claim 类型默认值推断失败的原件保留；修复为确切 Literal 默认值，未改变旧输入/回执/hash。

介入中心新增七个 Web 文件：

- `apps/web/src/api/interventions.ts`、其直接 reader 测试；
- `apps/web/src/features/intervention-operation.ts`、其直接原请求恢复测试；
- `apps/web/src/pages/InterventionCenterPage.tsx`、其直接交互测试；
- `apps/web/src/tests/intervention-fixture.ts`（明确 TOOL_ONLY，非金融/投递实测）。

页面使用实际生成的 DTO，只读列表/单条原件，不自动领取、收阅、回答或执行。领取需要用户点击；收阅需要未勾选的明确 checkbox。发送前保存完整原 body/key/hash；所有 HTTP 结果保持 pending，OBSERVE/ACK 必须另按原 key 读取 exact original_command/request_hash/receipt；DELIVER 必须另 GET 核实际固定收件身份，不能凭布尔或 NOT_FOUND 清门。丢响应后重开不 POST，不重新弹或证明人曾看到。跨族阻挡下仍允许自己原请求的只读核对。

手动登记当前问题先实际 GET 当前 workflow 和完整、VALID DecisionTrace，核相同 revision/run/hash，再发原身份。当前问题的来源变化先阻止提交；新的服务拒绝仍保原 key。通知 UI 只提供到一次一问页面的读取链接，不将显示的选项直接变成答案或金融确认。库存实际分母/展示截断、STALE/UNKNOWN、单动作而非全局边界订阅范围均保留。

最终前端检查：

| 命令 | 原证据目录 | 结果 |
|---|---|---|
| Vitest 三个直接 reader/store/page 文件 | W5/intervention-center-exact-originals-repaired-unit-20261005T182622Z-6ae09859 | 33 PASS，3 文件；Vitest 5.23s，wrapper 6.378073s |
| Web tsc --noEmit --project apps/web/tsconfig.json | W5/intervention-center-final-types-20261005T182633Z-9fd4d77e | PASS |
| ESLint 七新文件 | W5/intervention-center-final-static-20261005T182633Z-7a978473 | PASS |

三份最终 manifest 均 scoped/global source_stable=true。首轮 Vitest 被环境 esbuild spawn EPERM 阻止，原 FAILED 保留；获准启动已存在的单元测试子进程后 32 PASS/1 FAIL，定位为测试回执与本地 intent 共用对象，原篡改断言没有删，夹具改为独立副本后 33 PASS。失败原件 `intervention-center-first-direct-unit-20261005T182443Z-0876761f`、`intervention-center-direct-unit-native-spawn-20261005T182507Z-e8b18856`；旧夹具精确字节保存在 `.runtime/W5-intervention-ui-fixture-alias-red-20261005T1826Z/intervention-fixture.before.ts`。

页面及 hook 已可供 Root 接入 App/global write gate；自动 Question/Boundary producer、归档 source resolver、真实 PostgreSQL 唯一候选、真实浏览器投递/收阅仍未实测。本包没有扩大实验/验收工具，没有自行运行 PG 或金融链，FULL-507 原编号仍未关闭。

## 2026-10-06 显式差量：V1 全局动作集合原观察通知

新增 `GLOBAL_ACTION_SET_BOUNDARY` 请求 discriminator。公开输入仅原观察 ID、原 trace hash、epoch、可选当前 InterventionPolicy ID、原 key；不接金额、时钟、角色、消息内容、complete 或权限标志。`global_boundary_intervention_source` 必须真实重核原完整 typed GlobalBoundaryObservation 及父观察链，初始观察没有比较语义、不完整集合及来源 UNKNOWN 拒绝。消息以原集合前后签名/用户/epoch 语义去重，只有该新分支保存 `global_action_set_complete=true`；原 QUESTION/SINGLE_ACTION_BOUNDARY 仍 false，其序列化黄金 hash 已与保存的修订前源码比较一致。所有通知、投递及收阅的 bank_authority/answers_question/execution_eligible 保持 false。

新 `_message` 和原命令读回重核来源原件、父 run、request/source hash、owner/epoch、语义和原 attention。GET 额外计算实际当前完整 V1 集合：同 owner/epoch/算法/scope/集合签名才 CURRENT，当前不完整为 UNKNOWN，当前集合变化为 STALE。原历史回执不会授权、复活终态或证明人眼呈现；DELIVER/ACK 仍消费原唯一 Inbox 与原 hash/key。通知仅由显式调用 observe 创建，`global_boundary_subscription=EXPLICIT_OBSERVATION_ONLY`，没有后台 worker 或自动全局订阅。

文件：domain/services `full_intervention.py`、`db/full_models.py` 的 InterventionOutbox source CHECK 单处、新 `0014_global_action_set_notifications.py`，四个新直接/迁移/集成测试文件。与 Root 保存的修改前 full_models 完整文本比较，只有上述 CHECK 变化。0014 revision 为 `0014_global_notifications`（兼容 Alembic 原32字符版本列），down_revision=0013；只拓展 CHECK，没有原表数据 UPDATE、DELETE、TRUNCATE。downgrade 检出任何新 kind 原行即拒绝；0012 的不可变原件、单向状态和留存触发器不变。

检查与原件（均为模块检查，不是完整版验收）：

- 首相关纯检查 58 PASS / 58.47s：`W5/global-notification-first-pure-20261005T234822Z-59e313e2`，包含旧通知直接风险；scope/global 均稳定。
- 最后新增纯检查 25 PASS / 44.00s：`W5/global-notification-final-direct-20261005T235134Z-5b92259e`。scope 稳定，global 不稳定，仅独立 `apps/web/src/api/full-recovery-execution.ts` 新文件变化；不能称全仓冻结通过。
- 最后窄修的 DDL 类型标注及新增旧 SINGLE 黄金 hash 2 PASS / 3.38s：`W5/global-notification-final-type-only-and-legacy-hash-20261005T235400Z-fda1b8f2`；与上25项有一项重叠，不累计为27独立用例。当前新模块共有26个直接纯风险例。
- 八文件 strict types / Ruff / format 均 PASS：`W5/global-notification-repaired-final-types-20261005T235400Z-80ce9817`、`...repaired-final-static-20261005T235400Z-c376b960`、`...repaired-final-format-20261005T235401Z-d6073c01`；三者 scope/global 均稳定。
- 首最终类型检查 SQLAlchemy `__table__` 的静态 FromClause 标注错误保留于 `W5/global-notification-final-types-20261005T235134Z-797a0c87`（FAILED）；精确原测试源码保留 `.runtime/global-notification-first-types-red-20261005T2353Z`。修复仅 `cast(Table, ...)`，没有降低 DDL/金融守卫。
- 两个真实 PostgreSQL 候选仅 collect：`W5/global-notification-real-nodes-collection-20261005T235135Z-d26c5eac`，2 collected；scope 稳定/global 因同一独立 Web 新源变化不稳定。实际迁移与实际消息生产、重放、唯一 claim、ACK、新应用恢复、全财务表零写及 EXACT 审计尚未运行。

范围和缺口：这里的“global”严格绑定 `POLICY_BACKED_SERVER_PRODUCERS_V1` 完整分母，任意手工意图未覆盖；当前不支持的 Full producer 保留 UNKNOWN，不能空集成功。本冻结版本只解析 V1 GlobalBoundaryObservation，另包 `full-policy-action-set-boundary-full-v1` 的新 typed DTO/当前读取分派仍须显式窄接，不能降格为 V1。现有 Web 七根/旧通知 reader 未自动扩展新 kind；全局通知产品 UI、自动来源订阅、归档来源 resolver、真实 PostgreSQL 节点、浏览器呈现及正式验收仍 PENDING。不得据源文件存在关闭 FULL-507 或 FULL-204。

### 随后显式窄接：FULL typed 来源与版本分派

上述 V1 FINAL 原件保留 `.runtime/global-notification-v1-final-20261005T2356Z/manifest.json`，没有改写旧文件或失败记录。基于另包已冻结 FULL 来源，InterventionMessage 现在严格接受 `GlobalBoundaryObservation | FullGlobalBoundaryObservation`；新 source getter 按原 trace 的 `global_action_set` 精确版本分别调用两个独立验真 helper。当前集合读取按原 snapshot.algorithm_version 精确选择 V1 或 FULL getter，未知版本拒绝/UNKNOWN，没有降格或隐式 fallback。没有新迁移/权限字段，旧两种消息黄金 hash 与 false flag 保持一致。

支持的 FULL 来源范围为 `POLICY_BACKED_FULL_SERVER_PRODUCERS_V1`：已实现原 MVP 与实际动态 Goal producer；其它未实现 FULL family 仍保完整原分母并 UNKNOWN，任意手工意图仍未覆盖。新的 complete 只证明对应已声明集合域的原输入/来源验真，不意味着所有金融功能或银行权限完备。

新增三个版本风险例并重测三个旧/V1直接接缝：6 PASS / 39.36s，`W5/global-notification-full-version-direct-20261005T235840Z-776500cf`；scope 稳定，global 不稳定，仅 Root 并行新增 `api/global_boundary_postcommit.py` 及其测试。最后三文件 strict / Ruff / format PASS：`W5/global-notification-full-version-final-types-20261006T000004Z-a9b24aa3`、`...final-static-20261006T000004Z-a0dc9f5f`、`...final-format-20261006T000004Z-0b075a86`；后两者 scope/global 均稳定，types 原 manifest 保留精确状态。首类型检查两个标注/非显式测试常量导出错误保留 `W5/global-notification-full-version-types-20261005T235840Z-f4715f5c`；三个精确修改前源码保存 `.runtime/global-notification-full-version-first-types-red-20261005T2359Z`，最后修复仅显式 union 类型标注及从同一个原定义模块导入 NOW，不改实际判定或输入值。

两条真实 PG 候选仍只收集未执行，旧/新集合原件均不当实测结果。新全局 UI、Root 新 postcommit 来源捕获的实际效果、归档来源、真实浏览器投递/收阅及完整验收待后续，不关闭原编号。
