# MVP-401 外部银行事实与 304 审计兼容升级审查

状态：**DESIGN_ONLY / NOT_IMPLEMENTED / NOT_VERIFIED**（2026-10-04）。本稿仅只读审查 [真实 E2E 银行事实方案](MVP-401-real-e2e-fixture-design.md)、现有 304 domain/storage/0006 SQL。只新增本 Markdown，不修改此前 401 设计，不实现 codec/原件/legs/DTO/迁移，不写或运行测试，不操作数据库、seed、profile。304 全量运行尚未由本稿验收；实施须等 304 验收提交后另行冻结扩展合同。

## 结论与当前实际差异

推荐优先评估方案 A 的单一 posting 链，但必须先补**明确版本的原行 codec 和合法 extension 核验**，不能只加 nullable 外键再沿用全行反射。方案 B 不改变旧 posting 表列，却需要统一 CASH 前序与完整核验；它不能把两套 cash head 称为兼容，也不能只改审计 registry 而保留两个余额真值。

当前的“加一列必定使旧 OPEN 比较失败”不是准确描述：`domain/audit_chain.py:_immutable` 按传入的**原副本字段集合**逐项比较，不比较新列。对 BANK_POSTING/ACTION_RECEIPT，调用传的是 `original.data`；只有新增 nullable 列时，旧引用未必立即报错。然而这个行为也没有证明旧 posting 可以改挂新的非空原点：原副本没有该列，新字段可能未被旧比较检查。未来需要显式核验合法 extension，不能把未检查解释成允许。

真正的 additive 风险有三个：

1. `services/audit_chain.py:row_copy` 反射当前模型**全部列**。升级后重新捕获旧 posting 会多出新 null 键，原规范文本/subject_hash 与旧捕获不同。若重新从当前全行计算旧 `posting_set_digest`，即使旧经济值未变，摘要也改变。
2. `reset_archive_epoch` 再捕获完整当前业务行；按 `(user,epoch,kind,id,snapshot_hash)` 去重。新 null 字段使同一不可变 posting 多出第二份 snapshot。SEALED 核验把所有 BANK_POSTING 副本交给 `verify_frozen_ledgers`，其 identity 集合拒绝同 posting.id 出现两次。若旧/新表示并存，可能在合法升级后的 reset 核验中被当成完整性错误。
3. 给现有 v1 DTO 随手新增可选默认字段，即使值为 null，也会被当前 `model_dump` 编码进规范文本。旧 `parse_subject/parse_event` 的重编码等于原文检查会失败。不能通过全局 `exclude_none/exclude_unset` 或改 JSON 编码消除这个问题，旧 v1 当时实际保存的默认字段也必须原样保留。

以上是源码推导的具体风险，**未运行升级测试，不能登记为真实 RED 或已验证缺陷**。

## 精确阻碍与对应接缝

| 当前源码合同 | 对扩展的实际阻碍 | 最小需要显式改变的接口 |
|---|---|---|
| `audit_chain_types.py:14–58`，12个 EVENT_TYPES；PUBLIC/SUBJECT_KINDS | 没有 external fact/projection/独立 leg；新 kind 不能只写在 context.details | 新 public subject kind、event type 与归档 kind 的一致 registry，仍保持旧项不变 |
| `AuditSubject.schema_version='audit-subject-v1'`、RawJsonObject data | 外层稳定，但 data 当前不是按 kind 固定列形状的 DTO | 以 `(kind,snapshot_version)` 注册原行布局/来源规则；原 data 不能删键或补 null 后重 hash |
| `build_subject:133–158` | kind 必须已注册，snapshot_version只能1；TENANT原行owner必须当前user，只有ASSET_PRODUCT允许GLOBAL_CATALOG | 对确切新 kind/版本分派；新外部清算 ledger 仍是租户 bank subledger，不滥用共享目录 scope |
| `capture_audit_subject_data:375–430` | SUBJECT_MODELS查真实同用户实体；构造/保存snapshot_version硬编码1 | 按实际持久原件协议选 codec/version，保留原副本复用；版本不能由客户端传任意整数绕过检查 |
| `verify_audit_chain:759–804` | 旧原文逐份parse/hash与索引绑定；OPEN current按(kind,id)只装一份当前全行；已知immutable缺原件的诊断名单固定 | 新 kind 加入真实读取/缺失检测；当前比较使用明确 live layout，而不是假称所有新列都是原v1字段 |
| `_references:1422–1445` | posting/receipt核原副本所有字段；operation按原不可变字段+已SETTLED固定结果核；EVIDENCE允许正常状态后继但content/hash等不可改 | 旧字段逐项保持精确；新 origin discriminator/外键另核合法默认与不可变关系，不宽免旧字段变化 |
| `posting_set_digest:748–762`、anchor分支1679–1719 | 旧digest是完整非OPENING原行的 configuration-sha256-v1；anchor绑定BANK_OPERATION，按operation_id选行，再要求真实ActionPlan与BankCommand/205原协议 | 保留旧digest/绑定完全不动；external fact使用新的原件锚、集合hash算法和纯事实结算核验，不造ActionPlan |
| `_event_content:234–404`、AuditPayload | payload_version仅1；correlation仅EPOCH/DECISION_RUN/POLICY/GOAL；BANK_*形状绑定BANK_OPERATION+DECISION_RUN，强制run/action/request锚 | 新事实 payload/type 显式分派，采用真实EXTERNAL_BANK_FACT相关性；run/action/ActionReceipt字段为null |
| `parse_event/_protocol` | 外层schema/canonical未知会Unsupported；新payload内correlation/字段若先被旧Pydantic拒绝，可先变ValidationError而非版本分类 | 未来 reader先识别schema/canonical/payload registry再用相应固定DTO；已知hash/seq/tenant违规仍Integrity优先，不让unknown掩盖损坏 |
| `0006:audit_subject_insert:113–174` | envelope字段严格；snapshot_version<>1拒绝；kind→表CASE只支持现表，owner/id/当前实体存在必须相符 | 后续迁移替换函数的显式版本分支、表映射及owner校验。保留v1分支，不能开放任意kind或任意表名 |
| `0006:audit_event_insert:241–353` | 外层audit-event-v1/canonical-v1、原字段投影/hash、seq/prev强核；type白名单和payload_version=1；同epoch原snapshot hash/scope/version引用 | 后续迁移注册确切type/payload版本；相同外层时保留所有字段/链头检查。原引用与新引用均不能只凭存在ID通过 |
| `SimulatedBankPosting:698–751` | 非OPENING必须operation_id/leg_ref/previous且seq>1；operation同用户FK；previous仅指同表；CASH/POSITION/PAYEE/FEE/LOSS等ledger身份限定 | A需要明确operation/external互斥和同owner FK/leg唯一，支持实际清算身份；B需要新的跨原点连续链协议，不能绕开这些约束 |
| `reset_archive_epoch`/SUBJECT_MODELS、seed/reset | 当前19业务表graph；不可变snapshot永久留存，SEALED只核旧副本；新事实不能漏归档/被原FK阻塞后强删 | 新业务原件/legs进入显式归档与清理顺序、summary版本及真实manifest；原event/head/seal/checkpoint不改 |

SQL nuance：`audit_subject_insert` 校验保存的完整 subject envelope/hash、同用户真实实体存在与owner，但没有逐列证明 `data` 等于当下整行。它允许真正 BEFORE 副本，此职责也不能简单换成“data必须等于当前row”。实际全行捕获由受信服务完成，完整原金融内容由域与原锚核验；不能把SQL的存在/归属检查说成已验证任意外部事实。

## 方案 A：统一 posting 链新增 external_fact_id

fixture 文件暂用 external_event_id 命名，本稿按本次审查的 external_fact_id 讨论同一**独立持久银行事实**。实施前应统一唯一名字，不在同一腿上留两个可变别名。

新增列可空只是物理迁移兼容，不是原件布局兼容。建议冻结 `BANK_POSTING snapshot_version=1` 的原字段为：

```text
id, user_id, created_at,
ledger_key, ledger_dimension, ledger_metadata,
account_id, position_id, redemption_id, operation_id, leg_ref,
previous_posting_id, sequence_number, entry_kind,
balance_before_cents, delta_cents, balance_after_cents, occurred_at
```

这18个字段来自当前模型及Identity/OwnedMixin，含metadata/身份/原点/前序/时钟，不能只留下金额。原文中的每个已有键和值、nested原数据和原hash照旧保留；当原data另有键时，不把它删掉当成兼容，须按原协议核验/明确不支持。

建议窄接口（仅设计）：

```text
read_live_subject_row(session, kind, id) -> FullLiveRow
select_subject_layout(kind, immutable_row_origin) -> RegisteredLayout
validate_live_extensions(original_subject, full_live_row, layout) -> None
encode_subject_row(full_live_row, layout) -> OriginalRowData
capture_subject(..., registered_layout) -> 原版本AuditSubjectSnapshot
compare_live_to_original(original_subject, full_live_row) -> None
```

关键约束：

- 旧命令/旧opening posting仍按原v1布局捕获；新增 external_fact_id 在这些旧原点必须真实为null，未知新列或改变原点不允许悄悄省略。先核full live row、原owner/原origin和全部已知extension条件，再按明确版本编码，不能用“对old.data只取交集”的通用filter。
- 旧18字段的任何修改都仍是Integrity；“新列NULL兼容”不豁免金额、metadata、operation_id、previous、created/occurred等值。nullable新列从null改成外部fact引用，必须被明确拒绝，不因原副本没有它而漏检。
- 新external腿可以仍为kind=BANK_POSTING，但需登记snapshot_version=2与完整新布局（含external_fact_id/真实origin判别）。新的非OPENING入口约束为operation与external_fact恰有一个，leg_ref/prev/seq连续，同用户事实FK与每fact leg唯一；opening另有明确初始化合同，不能借此给工资现造正余额opening。
- 既有五类命令继续是原BankCommand/授权协议。它们新产生的command-origin腿也可继续固定v1布局、extension必须null；这使旧command核验和hash算法保持一致。不可根据“当前所有模型列”临时决定旧原件版本，或将已存在posting从v1转标v2。
- 同一已存在不可变posting必须选唯一原始布局；重新capture/reset应复用原hash，不创建第二个只多null的新副本。不通过在seal ledger校验中任意挑最后snapshot来掩盖重复。若将来确需同fact两种表示，应另有跨版本逐字段等价证明并核所有原文，不能在本最小扩展默许。

v1 `posting_set_digest` 应仅对其完整v1原行集合计算，旧银行结算/回执事件重试从原fact查找返回原event，不能以新全行重新制造锚再撞幂等。给新external腿另定带fact identity/协议版本的集合算法，例如 `bank-external-posting-set-v1`，原文及排序全部固定；不要沿用旧BANK_POSTING_SET名称、operation锚和configuration算法身份冒称同一结算协议。

这不是“删除新列修hash”：旧original从来不重编码成新schema；当前新列本身有独立必须满足的合法性检查，新external行完整保存新列，新字段不可变关系受新协议核验。无法唯一证明一行来自v1原点时，报明确版本/完整性问题，不能退回裁剪。

## 方案 B：独立 external legs 的兼容代价

独立表不会使旧row_copy多出列，旧subject/hash更容易保留。但现CASH的`previous_posting_id`是同表FK，当前冻结ledger核验要求同ledger连续seq、前后余额一致、原occurred时间单调：

```text
旧command cash head → 新external cash leg → 后续新command cash leg
```

如果external只在新表，后续旧表command不能把previous FK指向它；若仍指旧command，balance_before又已被external改变，旧完整cash链断裂。仅union两表查询或新增一张“最新余额表”不会修复这个约束。A2/A3在工资后继续真实Agent动作，因此这个问题不能推到“本轮没有后续命令”。

真正B至少需要统一ledger entry/head索引及**新追加腿协议**：所有新现金事件（包括Agent命令腿）共享一个线性seq/prev源，关联真实原点kind/id与完整原腿hash；保留旧v1前缀和可信转换边界，不重写旧posting.previous/seq/clock/原hash。新legs纯核验同时验证旧前缀tail、跨表前序/余额连续、同user/dimension，以及每次真实经济效果的完整腿。旧命令授权/effect仍可为v1，但新的银行腿表示须显式版本化，不能说连posting协议也原封不动。

若为解决同表FK又把external镜像/桥接行放进旧posting表，应重新按A的原点与hash合同审查，不能以B名称回避。两表同一经济腿可以有索引/副本，但经济效果只计一次；typed(kind,id,hash)不能被无来源union去重替代。跨kind UUID可以相同，原点和anchor必须带kind/hash，不能靠UUID全局唯一。

因此B的改动面通常大于A。没有一个无需迁移/无新金融核验的“test-only external legs”捷径。

## 新外部事实的独立版本化与权限边界

最小业务协议建议为`bank-external-fact-v1`，仅INCOME_CREDIT与CONSUMPTION_DEBIT，由受信server fixture/adapter提供真实原事件。固定fact_id/user/account/external_ref/source/counterparty/严格正整数amount/原occurred，持久原请求canonical/hash；可信接收observed、实际银行settled与审计appended各自保存。不能让浏览器传最终余额、BANK_CONFIRMED或Agent授权等级。

独立事实原件与投影原件建议kind分别为BANK_EXTERNAL_FACT、BANK_EXTERNAL_PROJECTION；B另需实际leg/index kind。旧subject外层/canonical算法可以保持audit-subject-v1，**新kind的自身业务协议显式v1**；对已有BANK_POSTING形状的改变则用snapshot_version=2。真正协议名字、字段和不可变范围须实施前冻结；不能只因RawJsonObject能装任意JSON就认作支持。

建议新审计事件最少EXTERNAL_BANK_FACT_SETTLED与EXTERNAL_BANK_FACT_PROJECTED：correlation kind为EXTERNAL_BANK_FACT/id真实fact；分别aggregate为fact与projection；run/action/ActionReceipt字段为null。projection因果指同epoch较早的原settled事件，真实同fact相关性；银行事件没有Agent决策因果时保持null，不虚构DECISION_RUN或ACTION_CREATED。

新anchor应精确绑定原fact request/银行结果、完整external腿，以及真实projection/Transaction/EVIDENCE/income-location后继；纯核验不能调用五类`verify_frozen_settlement`伪造action。工资是银行信用事实，消费是已发生用户事实；它们不产生AUTO/ASK，也不补齐站立申购/目标/恢复授权。后续Agent动作仍走原策略、effect、303轨迹与真实回执路径。

若外层字段不变，最小wire扩展可保持audit-event-v1+canonical-v1，但新type采用payload_version=2以及单独固定的PayloadV2/Envelope解析分支；PayloadV1字段及默认值一字不动。V2显式加入external correlation/原件结构，不在旧context中藏新事实协议。也可另设event schema v2，但改动更大；需连同SQL envelope/hash namespace一起冻结，不能两套reader猜同一文本。

当前v1旧reader并不保证把所有合法未来payload都归类Unsupported：新的correlation字面量可能先被旧Pydantic拒绝。未来reader需要原文版本分派，未知结构明确UNSUPPORTED_VERSION而不假VALID；同时已知tenant/head/seq/prev/原文hash和可验证旧字段损坏仍判Integrity，不能以提高版本号隐藏实际篡改。不能承诺未更新304二进制可以验证V2，部署必须先有兼容reader与对应SQL，再允许新writer。

金额/经济腿使用signed64整数分，delta可负；before/after按现银行非负约束。原配置/证据snapshot的finite float保留原规范文本，不经JSONB numeric重算原hash。银行事实occurred可能早于观察，但旧posting链occurred不能倒退；必须明确拒绝不可插入的迟到事件，或在新协议区分原fact occurred与实际settlement/ledger append时间，不能改写旧时钟伪造顺序。

同原key/相同原内容返回原fact/legs/projection与原observe/settled/appended，不因后来clock生成第二效果；同key异内容拒绝，换key同external_ref重复也拒绝。UNKNOWN/银行已结算未导入时保留同fact身份，应用retry只补原projection。新Evidence用新稳定ID与合法SUPERSEDED后继；旧content/hash/observed/window不改。EVIDENCE status正常后继可被原immutable范围容许，不能借此改旧payload。

## 原文、当前引用、seal与升级顺序

旧event/subject规范UTF-8文本、event_hash/snapshot_hash、head、seal、可信checkpoint及旧genesis声明均不UPDATE、不补字段、不重hash；304 SQL普通DML拒绝仍保留。新migration只能新增表/列与精确registry分支，不能改0006历史文件或禁用append-only trigger修旧数据。旧canonical版本不借机升级排序、Unicode/float/时钟编码。

OPEN核验仍加载真实当前对象，逐原immutable字段核对，并验证新增原点/extension规则；新kind的已捕获immutable原件删除必须CURRENT_ORIGINAL_MISSING，而不是因没加到固定名单就忽略。旧policy确认、原request/receipt/303原件和证据不可变范围不放松。SEALED只消费旧epoch保存副本，不能读取重建后同UUID新行或把当前版本codec应用到旧原文。

新的reset归档必须完整包含fact/projection/legs及其初始化来源，先核真实链，再封口，原账目/副本/manifest全部保留；不能遗漏新表后依靠CASCADE清理。旧已seal的manifest counts是其当时所有snapshot版本，旧hash不改变；未来新seal可包含新kind/版本的entries，其实测manifest自然不同，不能倒改旧seal。seed summary新增表/列需要显式版本与实测新图摘要；原seed经济版本与旧genesis摘要不被新反射行shape默默重写。

整个银行独立commit→应用projection逻辑命令复用`audit_command_guard`；实际写事务在User锁前`transaction_gate`，与reset同key。新schema部署/迁移需在明确维护边界处理，而不是靠这个业务shared gate冒充全局DDL锁。GET/CLI/核验继续RR READ ONLY，未知或超预算不回填/建立epoch。保留原legacy_history缺口分类，不通过升级把LEGACY_UNAUDITED改成完整历史VALID。

## 真实升级金标准（全部待实施/待运行）

| 金标准 | 升级前准备与实际触发 | 必须保存的真实断言 |
|---|---|---|
| U01 原件与原字段保持 | 在隔离PG用304真实命令创建OPEN链、普通BANK_SETTLED/ACTION_PROJECTED、205原合同；另建SEALED链及可信checkpoint。记录全部原文/hash/原18posting字段，再合法migration | 原字段/原数据/count相等；新增列仅为声明NULL；旧event/subject/head/seal/checkpoint字节与摘要完全不变；旧OPEN/SEALED/303核验分类相同，不将legacy改绿 |
| U02 additive实际RED | 在升级后实际重新capture同一旧posting或实际reset，观察全行反射新增NULL所致第二snapshot/重复ledger风险 | 保留真实RED再修codec；修后旧原件复用原hash、immutableposting无多余schema副本、reset后旧seal/offline核验通过。若合法迁移从未产生该错误，只报告实测，不能把本稿推导充RED |
| U03 不许裁剪掩盖 | 同一已捕获旧posting分别合法受控地改变delta/before/after、metadata、operation、prev、seq、occurred/created，以及仅把新external_fact_id改非null | 每种具体改动应被数据库或OPEN核验拒绝；金额守恒+重hash仍不放过原值变化。新列NULL兼容不为非NULL改挂提供豁免；不能只测三金额 |
| U04 新V2 full原件 | 真工资双腿+真实projection、普通消费双腿+projection；新subject/type/payload/hash注册齐全 | 独立fact/projection/leg完整原文含所有新列/关系；缺腿/多腿/错counterparty/owner/digest/源type拒绝；同UUID不同kind按typed关系核验；无造Agent action或许可 |
| U05 后续Agent链连续 | old command→external工资→真实allocate/purchase→external消费→真实无损恢复 | 一个用户CASH连续seq/prev/before-after，完整经济守恒；external只计一次，Income location不二算现金；后续五类命令原授权/请求/effect核验仍生效。B必须实证跨表连续，不只验证各表内链 |
| U06 幂等/时钟/待对账 | 同keylater-clock重放、异内容/换键同external_ref、银行settled后projection失败、只读clock推进后原fact对账 | 原canonical/hash/时钟与经济效果一份；失败保留银行事实、GET零写、原键只补projection；迟到occurred按明确协议处理，不回写原链；工资/消费不授予策略 |
| U07 mixed/unknown/损坏 | 同epoch旧payload1与新payload2、新snapshot2；未知版本/algorithm；同时原旧hash或head真实损坏 | 新reader已知mixed完整核验，未知明确Unsupported；实际已知完整性错误不能被unknown降级遮盖；老客户端不被宣称V2已VALID；PREFIX旧checkpoint保留，EXACT面对真实追加不冒称原范围未变 |
| U08 reset/archive/current删除 | 连续新事实与projection后reset；银行commit/projection间reset并发；新原immutable删除；新epoch复用UUID | 先真实归档再seal，原manifest与old originals可离线核；gate不穿插清掉未投影fact；失败全库回滚；OPEN已知原件删除Integrity，SEALED不误查新同UUID；旧尾删/清空和预算门保持 |
| U09 纯schema与序列化兼容 | v1原文包含default null/空数组、原raw finite float；新增DTO可选字段/新reader解析，API/CLI回读 | v1重编码等于原文且旧摘要相同，无exclude_none/字段交集捷径；新内容完整覆盖新字段。核验仍零写，工程health不能替代此升级证明 |

升级前golden来自未改304公开核验/实际PG原件，保留source SHA/版本/clock/退出码。新reader逐条验证原文与全部字段后比较旧结果，独立原事件双腿手算另外验证业务事实；不能用新codec生成新预期自证兼容。实际新fact/run身份不同会产生新hash，这是正确结果，不要求与旧操作摘要相等。

建议实施顺序：先冻结A/B与原行/事件分派合同，真实U01/U02 RED与v1解析兼容；再迁移/SQL/codec/current核验；再新的纯事实完整腿与projection、合法来源后继；最后真实外部触发后的Agent金融链、reset/unknown/readonly与401 Edge。全部定向/生成合同/完整check与源SHA验收完成前，本稿与所有升级矩阵保持未完成。
