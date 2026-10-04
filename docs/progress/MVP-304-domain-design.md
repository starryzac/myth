# MVP-304 域合同：规范化、哈希链、只读核验与检查点

状态：`DESIGN_ONLY`，2026-10-04。303 完整检查 `20261004T051331Z-0c360f9c` 尚在运行，源码、测试和生成合同保持冻结。本文只交付后续实现合同，不代表 304 已实现、迁移或通过测试。依据为 [304 预审](MVP-304-preflight.md)、[真实事件映射](MVP-304-event-map-preflight.md)、初版 8.6/MVP-304、完整计划 13.1–13.2，以及现有 `AuditEvent`/303 DTO。

## 1. 实现边界与持久化配合

域层只校验和构造已有事实，不调用银行、重跑资金算法、追加数据库记录或读取当前时钟。服务层从同一个事务/只读快照提供实际事件、head 和不可变引用副本；域层不能把调用方自称的 `user_id` 当作数据库租户核验。

保留 `AuditEvent` 的既有字段与语义，新增明确的 epoch 和 envelope 协议。`created_at` 在 DTO 中称为 `appended_at`，首次追加显式赋值后参与 hash，不能先算 hash 再接受 DB 默认时间。现有同用户 sequence/key/hash 唯一约束应改为同用户、同 epoch 的约束；现有 `Integer` 序号的 v1 上限为 `2**31-1`，达到上限明确拒绝，不能绕回或截断。

本合同与 storage 当前预审的“保留 AuditEvent 与预留模拟 User，封存 epoch/subject snapshots”方案配合。普通运行与演示 reset 都不改写旧事件；reset 归档旧业务副本、封存旧 head 后创建新 epoch。若 root 选择另一种 reset 存储方案，仍必须满足本文相同的 epoch、seal、原副本解析和 checkpoint 合同。该选择尚未作为迁移落地。

当前 run/action/receipt 的 live FK 会阻止删除旧演示业务图。后续迁移若采用不可变副本引用替代这些 FK，必须以 DB 受限写入和 same-epoch snapshot 验证替代，不能只移除 FK。`causation_id` 仍指向保留的同用户、同 epoch 原 AuditEvent，并由追加接缝核较早 sequence。epoch/head/seal/snapshots 的 UPDATE/DELETE/TRUNCATE 和任意 INSERT 同样需要受保护；不提供普通 session 可自行打开的 bypass。

## 2. 公开纯域 API

建议放入 `domain/audit_chain{,_types}.py`，名称和行为在后续 TDD 前由 root/storage 一起冻结：

```python
canonical_bytes(value: JsonObject, *, raw: bool = False) -> bytes
intent_digest(intent: AuditIntent) -> Digest
build_event(intent: AuditIntent, *, event_id: UUID, epoch_id: UUID,
            sequence_number: int, previous_hash: Digest | None,
            observed_at: datetime, appended_at: datetime) -> AuditEnvelope
verify_event(event: AuditEnvelope) -> None       # 无效时抛 AuditContractError
same_intent(event: AuditEnvelope, intent: AuditIntent) -> bool
verify_epoch(events: Iterable[RawAuditRecord], *, head: AuditHead,
             expected_user_id: UUID, references: ReferenceBundle,
             checkpoint: AuditCheckpoint | None = None,
             checkpoint_mode: Literal["PREFIX", "EXACT"] = "PREFIX") -> AuditVerification
build_checkpoint(head: AuditHead, *, captured_at: datetime) -> AuditCheckpoint
verify_seal(seal: AuditEpochSeal, previous: AuditEpochSeal | None) -> None
```

`build_event` 不接受调用方 `event_hash`，由实际序号、prev hash 和首次追加时钟计算。`verify_event` 重新校验复制模型及嵌套 JSON，不能依赖 Pydantic `frozen=True` 阻止嵌套字典被改。`verify_epoch` 需要完整 epoch 序列和独立 head；分页的一段不能返回完整链 `VALID`。`ReferenceBundle` 是服务已经按 tenant/epoch 查出的不可变副本集合，不是 HTTP 输入。

## 3. Envelope 与 payload

`AuditEnvelope` 为 `extra=forbid`、严格类型、有界 JSON 的不可变 DTO。所有 nullable 字段都以显式 null 参与 hash，不因省略默认字段改变语义。

| 字段 | v1 合同 |
| --- | --- |
| `schema_version` / `canonical_version` | 精确为 `audit-event-v1` / `audit-canonical-json-v1` |
| `simulation` | 必须是 bool `True`，拒绝整数 1 |
| `id`, `user_id`, `epoch_id` | UUID；epoch 属于该保留的模拟用户 |
| `sequence_number` | 严格正整数，`1..2**31-1`；epoch 内从 1 连续增长 |
| `previous_hash` | 序号 1 必须 null，其他序号必须前一事件的 lowercase 64hex |
| `event_type`, `aggregate_type`, `aggregate_id` | 注册事件类型与对应 typed aggregate；禁止任意 UUID 充当合法业务聚合 |
| `correlation_id`, `causation_id` | correlation 的种类在 payload 内显式声明；causation 可空但受第 6 节限制 |
| `decision_run_id`, `action_plan_id`, `action_receipt_id` | 可空 UUID；非空时必须与 payload 引用及真实关系一致 |
| `idempotency_key` | 原字符串 1–160 字符，不 trim、不重写；同 tenant/epoch 唯一 |
| `payload_version` | 严格正整数；仅注册的 `(event_type, payload_version)` 可做语义核验 |
| `payload` | 第 4 节公共结构及对应事件 v1 结构，不接受额外自由字段 |
| `occurred_at`, `observed_at`, `appended_at` | aware datetime，规范化为 UTC，保留微秒；含义见第 5 节 |
| `event_hash` | lowercase 64hex，由除自身以外的完整 envelope 计算 |

`AuditIntent` 是稳定语义请求：`user_id, event_type, aggregate_type, aggregate_id, correlation_id, causation_id, decision_run_id, action_plan_id, action_receipt_id, idempotency_key, payload_version, payload, occurred_at`。它不包含分配的 sequence、prev hash、事件 ID、观察/追加时钟或 event hash。epoch 是服务在用户锁下选择的可信边界，不能由客户端指定。

### 3.1 规范化字节及 hash

规范化只接受 UTF-8 可编码字符串、对象、列表、null、bool 和协议允许的数值。字典 key 必须字符串；拒绝非 JSON 对象、Decimal、set、bytes、NaN/Infinity 和循环/深度超限结构。JSON 文本入口拒绝重复 key，不得让最后一个值悄悄覆盖之前的引用或金额。

UUID 以 lowercase 标准带连字符字符串表示；datetime 固定为 `YYYY-MM-DDTHH:MM:SS.ffffffZ`，date 为 `YYYY-MM-DD`。字符串不做 NFC、空白 trim 或大小写变换。对象按原 Unicode key 排序；列表保留协议顺序，不能为了让 hash 相同而重排 FIFO、候选或 posting 顺序。集合型引用由 builder 明确按 `(kind, id, role, snapshot_hash)` 排序，完全重复引用或相同 `(kind,id,role)` 的冲突副本拒绝；同对象的 BEFORE/AFTER 副本可以不同。

编码为 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`，不使用 `default=str`。v1 普通 event payload 只含整数数值、状态、标识、时间字符串和既有 digest；quantile、收益参数及原内容留在引用的完整 subject snapshot，不重新发明一个有损的金融副本。原始 snapshot 允许真实有限 float，固定原规范文本并标明版本；不能把 1.0 转为 1 或把原 -0.0 改为 0.0 后宣称 303 原 hash 未变。

```text
event_hash = SHA256(b"bounded-funds/audit-event-v1\0" + canonical_bytes(envelope_without_event_hash))
intent_hash = SHA256(b"bounded-funds/audit-intent-v1\0" + canonical_bytes(stable_intent))
snapshot_hash = SHA256(b"bounded-funds/audit-subject-v1\0" + original_canonical_snapshot_bytes)
seal_hash = SHA256(b"bounded-funds/audit-epoch-seal-v1\0" + canonical_bytes(seal_without_seal_hash))
checkpoint_hash = SHA256(b"bounded-funds/audit-checkpoint-v1\0" + canonical_bytes(checkpoint_without_checkpoint_hash))
```

上述 namespace 前缀不是秘密、签名或外部锚定。303 的 `trace_hash`/`input_hash`、请求/effect/configuration hash 仍按各自既有算法验证；不能拿新的 audit canonical hash 替换旧 digest。算法版本是原锚点的一部分。

### 3.2 数值及资源边界

受信普通 event payload 中的整数均为 strict signed64，拒绝 bool/浮点/字符串冒充分值。`*_cents` 标量允许负数，以保留 signed posting delta、资金缺口、margin、前后差分；principal、fee、loss、实际执行金额等非负/正值限制由相应事件 schema 追加，不能全局把 signed money 截成 0。金额向量或 posting 列表使用 typed schema，不能通过任意 `_cents` 字段藏对象。原始 Evidence.content/Policy.configuration 及 303 内相同 raw 区域保留错误 bool/有限 float/owner 声明，由原记录的 source issues 说明拒绝，不能被通用金额验证器“修正”。

v1 限制：单普通 event canonical envelope 最多 1 MiB、JSON 深度 32、节点 250000；每事件最多 10000 个 references、10000 个 missing evidence IDs。单完整 subject snapshot 最多 16 MiB、深度 64、节点 1000000，足以保留原 10 MiB 的 303 envelope 及包装。head/seal/checkpoint 各最多 64 KiB。一次 `verify_epoch` 默认最多 100000 个 events、512 MiB 累计事件字节、100 条诊断；逐条流式处理，不读成无界列表。超过预算返回 `INCOMPLETE/LIMIT_EXCEEDED`，绝不把已验证的前缀称全链通过。后续脚本可按受控预算逐 epoch 完整验证，不能跳过超额 epoch。

## 4. 引用副本与原 hash 锚

公共 payload 的字段固定为 `fact_key, correlation_kind, references, anchors, changes, missing_evidence_ids, legacy_origin`，拒绝额外字段；空集合显式为 []，无 legacy 为 null。`fact_key` 是 1–160 字符的协议派生事实身份，独立于 caller 幂等 key，例如原 operation 的一次 SETTLED、原 action 的一次 SUBMITTED、原 policy 的一次真实状态转移；需要同 tenant/epoch/event_type/fact_key 唯一。`changes` 最多10000项，只保存本次实际差分，不把新 read_clock 塞进差分。

所有已存在实体引用统一放在 `payload.references`，每项字段为 `kind, id, scope, user_id, role, snapshot_hash, snapshot_version`。`role` 为 `BASIS|BEFORE|AFTER`，分别用于原决策依据或本次真实前后状态。`scope` 仅允许 `TENANT` 或 `GLOBAL_CATALOG`。TENANT 的 typed owner 必须等于 envelope.user_id；GLOBAL_CATALOG 仅允许精确版本的 `ASSET_PRODUCT`，owner 为 null，并必须是原 effect/trace 实际引用的版本，不能以“全局”绕开账户、策略或证据的 owner 检查。

参考种类限于：`DECISION_RUN, ACTION_PLAN, ACTION_RECEIPT, POLICY, POLICY_VERSION, EVIDENCE, ACCOUNT, GOAL, ASSET_POSITION, ASSET_PRODUCT, BANK_OPERATION, BANK_REDEMPTION, BANK_POSTING, RESOURCE_CLAIM, INCOME_RESERVATION`。添加种类必须更新版本化 registry，不默许未知种类。top typed IDs、aggregate 和 correlation 都要找到相同 epoch 的对应 reference；不用泛化 UUID 猜关联。

副本 envelope 固定为 `schema_version="audit-subject-v1", canonical_version, simulation=True, user_id, epoch_id, kind, id, scope, snapshot_version, data`，snapshot_hash 是原 canonical 文本的域分隔 digest，存于其索引而不在被散列文本内自引用。这里 user_id 表示捕获此副本的 tenant；GLOBAL_CATALOG 的实际 catalog owner 仍为 null。data 完整保留实际 row 身份、typed owner、原内容及关系，不只保留将被 reset 删除的 live FK。副本保存为不可变 canonical UTF-8 文本（或 JSONB 中的原 canonical 文本字符串），以保留原始数值类型及既有 303 hash；解析后的 JSONB 投影不能取代这个原副本。快照索引为 `(user_id, epoch_id, kind, id, snapshot_hash)`；同对象随真实事件有多个状态副本时按 hash 精确解析。核验旧 epoch 必须使用旧 epoch 的副本，不能在 reset 后拿同 UUID 的新账户/策略/回执解释历史。

每项 `AuditChange` 字段固定为 `kind, id, field, before, after, before_snapshot_hash, after_snapshot_hash`；哈希指向相同实体对应 BEFORE/AFTER references，新增对象的 before 与 before_snapshot_hash 可同时为 null。field v1白名单为 `status, autonomy_level, policy_version_id, amount_cents, executed_cents, fee_cents, loss_cents, reserved_cents, balance_cents, delta_cents, allocated_cents, new_authority, available_at, settled_at`。标量类型与相应 subject schema 一致：分值 strict signed64、状态字符串、UUID标准字符串、时间规范字符串、new_authority bool；实际非负字段保留该限制。change 值必须等于对应完整原副本的值；未变化字段不虚构差分。列表/复杂FIFO/边界计算由原完整 trace/subject 解析，不用 arbitrary field path 绕过白名单。事件最小必需锚与 cause 见第6节，不能只填一个合法空 payload 冒充 BANK_SETTLED/ACTION_PROJECTED。

`payload.anchors` 每项字段固定为 `kind, reference_id, snapshot_hash, digest, hash_algorithm`，明确绑定对应 reference 的版本，按 `(kind, reference_id, snapshot_hash, hash_algorithm)` 排序且拒绝重复。registry 对种类验证原算法：

| 锚点 | 必须核验的原内容 |
| --- | --- |
| `DECISION_TRACE` | 原 schema/算法、run/user/phase/as_of/parent/action、input_hash、trace_hash 和整个原 snapshot_hash；调用原 303 纯域校验，不跑算法 |
| `EXECUTION_REQUEST` | 原 request_hash/effect_hash、不可变 effect 与 action 身份；完整 request 按原合同核验 |
| `BANK_POSTING_SET` | 实际该 operation 的完整有序 leg 集合、原 IDs/方向/余额/金额/时间；不能省一条 leg 再称完整 |
| `ACTION_RECEIPT` | 原 receipt、request、完整 legs 和实际 transaction/evidence 对应；消费原只读完整性语义 |
| `POLICY_CONFIGURATION` / `EVIDENCE_CONTENT` | 原声明 digest、actual capture digest 与原内容；VERIFIED 仅为两者相等，INVALID 的原 BLOCKED 记录仍可合法入链 |
| `RESET_DATASET` | 实际 seed version、dataset digest、上一 epoch seal；不能把规划中的 seed 值当实测结果 |

body 内的 raw source.user_id 可能是原拒绝原因，不当作 typed owner；typed row owner 和可信 effect/context/output 仍严格核验。合法 SUPERSEDED、撤权及后续状态变化不重写旧 snapshot，也不使历史 chain hash 变化。核链不新授予当前权限。

首次 append 的副本必须从本事务实际已使用的对象生成并核 live owner/原关系，不能由 caller 上传任意合法形状 JSON 后替换实际资金事实。active epoch 的 readonly ReferenceBundle 同时提供尚存原对象的不可变字段比对：原 trace/input hash、来源 content及metadata、policy configuration、request/effect、receipt和posting内容不得改变；正常 balance/status/生命周期推进只作为当前附注，不要求与旧状态全等。原 immutable 内容变化报告 `ORIGINAL_CONTENT_CHANGED`，不能因为旁边还有旧 snapshot 就把当前篡改隐藏为完整核验。SEALED/reset epoch 不读取同UUID的新 live对象，只核其受保护原副本及seal；两种解析范围在结果中明确。

当时真正缺失的证据放在 `payload.missing_evidence_ids`，不得生成虚构 EVIDENCE reference、snapshot 或 VALID copy。此集合须精确匹配原 303 的 missing 引用和原 BLOCKED/source issue；实际存在的外用户 ID 始终拒绝，不能伪装成 missing。缺失当时来源不等于所有引用都可忽略：本事件的 run/action/receipt 等必要实体没有副本属于 `REFERENCE_MISSING`。旧历史没有审计事件时明确 `LEGACY_UNAUDITED`，不由 GET 补事件。

## 5. 三种时间与不受后续时钟影响的幂等

`occurred_at` 是已经发生的原业务/银行事实时间；`observed_at` 是该事实首次被本次记录接缝可信观察的逻辑时钟；`appended_at` 是事件首次写入所使用的 DB 追加时钟，对应显式 `AuditEvent.created_at`。序号决定链顺序，迟到投影的 occurred_at 可以早于前一事件，不能按 occurred_at 或 observed_at 倒排 chain。普通发生型事件要求 occurred_at 不晚于其可信逻辑 observed_at；future available_at、策略未来 valid_from 等仅作为 payload 中的计划/有效期，不冒充已结算发生时间。

模拟逻辑时钟可不同于真实 DB 墙钟，所以不全局要求 `observed_at <= appended_at`，也不要求墙钟随 sequence 单调。三个时间都参与首次事件 hash，重试不能重写。服务分别传 `observed_at` 与 `appended_at`，不能用一个当前 now 替代全部原时间。

稳定 key 使用 `(event_type, immutable subject, true transition identity)`；bank accepted/settled 分别以同一原 operation 和事件类型去重，不能把 legacy redemption 与其 BankOperation 双算为两个经济操作。一个 action 的 UNKNOWN 多次 no-op 只有一个真实状态转移；独立的新观察若确需审计，应使用单独版本化观察类型，不能改旧 UNKNOWN。

追加流程在用户/epoch 锁下先查 key：已存在时核原 event/hash、完整稳定 intent、引用副本与 head 一致性后原样返回；不重新分配 sequence，不写任何时间或 head。`same_intent` 比较原稳定字段、payload、原 occurred_at，排除新调用的 observed_at/appended_at，以及分配的 id/sequence/previous_hash。新时钟不是冲突；改变金额、原请求、cause、correlation、原事实时间或其它稳定关系必须 `IDEMPOTENCY_CONFLICT`。

调用方从已提交实体读取原 requested_at/settled_at/confirmation time/transition identity 形成 occurred_at，不能重试时再以 now 编造一次原事实。尚无固定原时间的首次生命周期 transition 只在实际状态改变时构造 intent；以后 no-op 不再 append。查询已有 key 发生在刷新时钟/重新构造首次事实之前，原 payload 中不得混入 `read_at` 或本次重试 now。相同原事实不同 key 同样不能双记：service/DB 的 `(epoch,event_type,immutable subject,transition identity)` 事实唯一约束须与 key 配合，不能只依赖 key 唯一。

## 6. Tenant、correlation 与 causation 的严格语义

`payload.correlation_kind` 是 `DECISION_RUN`、`POLICY` 或 `GOAL`；普通动作及银行事件 correlation 为原主决策 run，子 phase 的原 parent 链必须实际到达该 root。policy 生命周期使用所属 policy；goal 初始化使用实际 policy 或 goal，registry 固定其一种，不允许任意同用户 UUID。aggregate 和 top IDs 与 reference 副本全部逐项核同一用户/epoch和关系。

causation 只能指向已有、较早 sequence 的同用户同 epoch 事件；不能指向自身、未来、另一 epoch 或另一 action 的经济事件。它是业务直接原因，不是自动填上一条链事件。无原因的 root 事件必须在 registry 明确允许 null；相邻 previous_hash 与业务 cause 独立。

| 事件 v1 | 必需主体/锚与 cause 规则 |
| --- | --- |
| `EPOCH_STARTED` | epoch 首条；无 cause；INITIAL/LEGACY_ADOPTION 或 RESET 标记，后者必须带原 seal |
| `DECISION_RECORDED` | 精确原 trace/run；root PREPARE/EVALUATION/RECOVERY_PLAN 可 null；子 phase 指向其真实较早主/阶段决策，原 action/root 一致 |
| `ACTION_PREPARED` | 原 action/effect/主 PREPARE；cause 为该 action 的 PREPARE `DECISION_RECORDED`；若以决策事件合并承载则不重复加本类型 |
| `ACTION_CONFIRMED` | accepted 原 confirmation evidence/effect、CONFIRM run；cause 为同 action CONFIRM 决策；ASK 原等级保留 |
| `ACTION_SUBMITTED` | RESERVE run、原 action、实际 claim/income reservation 差分；cause 为同 action RESERVE 决策 |
| `BANK_ACCEPTED` | 精确 operation/原 request/BANK_ACCEPT trace；cause 为其真实 BANK_ACCEPT 决策，同 action/root；205 也绑定实际 legacy 受理输入，不声称301重验 |
| `BANK_SETTLED` | 同 operation 的完整原 legs、settled_at；cause 为该 operation 的 BANK_ACCEPTED，不借另一个 action 的受理补原因 |
| `ACTION_PROJECTED` | 同 action 的 receipt、原 request/legs/transaction/evidence、声明消费；cause 为该原 operation 的 BANK_SETTLED；ACCEPTED 无回执不能出现本事件 |
| `ACTION_UNKNOWN` | 同 action 的实际状态转移和占用；cause 为已知原提交/受理；不能以异常推断银行 REJECTED 或释放声明 |
| `ACTION_INVALIDATED` | 原 action、可证无效果/REJECTED 或实际策略失效原因、释放差分；cause 为相应生命周期/拒绝事件，或 registry 允许的可信首次失效根事实 |
| `POLICY_CONFIRMED`, `POLICY_VERSION_CHANGED`, `POLICY_STATE_CHANGED` | policy、准确版本/config/原确认证据及真实前后状态；首确认可 null，后续 cause 为该 policy 最近真正生效的确认/变更/状态事件 |
| `GOAL_INITIALIZED` | 原 policy version、goal/account 和真实零初始化证明；cause 为原已记录策略确认；不能重开正额资金 |
| `RECOVERY_OBSERVED`（若采用） | 原批次实际 actions/receipts/等待/失败集合；cause 为同 recovery 主决策；不是新结算或回执 |

最终 registry 仅纳入 root 实际接入的上述类型，不能宣称不存在入口的发现/导入/每次 source supersede 都已被审计。T0 同事务 accepted→settled、205 批次多 action 必须分别有正确 cause，顺序不赋予另一个主体因果关系。

启用 304 前已有 accepted 操作后续结算/投影，不得补造过去的 BANK_ACCEPTED。允许明确 `legacy_origin={reason: "UNRECORDED_BEFORE_ACTIVATION", references: ..., original_request_hash: ...}`，cause 可 null，仅 registry 中这种旧事实 continuation 例外有效。其链的已记录字节仍可核验，但引用覆盖标 `LEGACY_UNAUDITED`，不能称整个经济生命周期完整审计。新 304 操作缺应有 cause 直接失败；不以 legacy 标记绕过。

## 7. Head、seal、checkpoint 与删尾/清空检测

`AuditHead` 字段：`schema_version="audit-head-v1", simulation=True, user_id, epoch_id, epoch_number, status=OPEN|SEALED, event_count, last_sequence, last_event_id, last_event_hash, genesis_event_id, genesis_event_hash, previous_seal_hash`。公开 head 必须有 EPOCH_STARTED；`event_count == last_sequence >= 1`。首次创建内部的 count=0 只在同事务追加 genesis 前短暂存在，不是可宣称 VALID 的公开空链。缺少已注册 epoch/head 是 `HEAD_MISSING`；未启用的原历史单独报告 `LEGACY_UNAUDITED`。

追加在既有用户锁顺序下读取/核可信 head，分配 `last_sequence+1`，同原事务插入 event、必要的 snapshot，再原子更新 head。不能用无锁 `max(sequence)+1` 或吞 unique violation 实现串行。append failure 回滚所属业务事务；已独立提交的银行事务不被应用失败反向改写。head 的保护与 event 保护同等必要。

`AuditEpochSeal` 为 `audit-epoch-seal-v1`，绑定 user/epoch/epoch_number、完整封存 head、previous_seal_hash、实际 reset reason/identity、seed/dataset digest、sealed_at、seal_hash。SEALED epoch 不追加；下一 epoch genesis 引用原 seal。epoch ordinal 连续、user 一致、seal 链无循环，不能清掉元数据后伪装首次 genesis。业务原 snapshot 在 seal 前真实归档；重置后解析依赖原 epoch，不读同 ID 新事实。

`AuditCheckpoint` 为 `audit-checkpoint-v1`，绑定 simulation/user/epoch/epoch_number、genesis ID/hash、expected_count/last_sequence/tail ID/hash、previous_seal_hash、canonical_version、captured_at 和 checkpoint_hash。由完整 verified head 导出，验证器不能边发现损坏边“更新到当前值”。它是可信调用方保存的独立预期值，不是任意客户端上传后自动信任的锚；checkpoint 自身 hash 只能检测字节不一致，不能认证作者。

`PREFIX` 模式要求已观察 checkpoint 的整段历史仍保留：checkpoint.sequence 不得大于当前 head，走到该 sequence 时 ID/hash 必须完全匹配；合法新追加允许存在，新的完整尾部仍与可信 head 核。`EXACT` 模式要求 head 的 count/tail/genesis/epoch 与 checkpoint 全等，任何新增事件也报告预期范围不匹配。使用旧 epoch checkpoint 时必须供应并核原 sealed epoch/snapshot/seal 及连接到当前 epoch 的 seal 链；不能拿 active epoch 同 UUID 代替。不供应原 sealed 内容返回 `INCOMPLETE/CHECKPOINT_EPOCH_UNAVAILABLE`。

| 变动 | 必须失败的比较 |
| --- | --- |
| 中间删一条、重排、重复或跳号 | 从 1 连续 sequence、ID/key 唯一、previous_hash 与较早 cause |
| 删最后一条或连续尾部 | 实际 count/tail 与独立 head 不符；即使余下相邻 hash 全对也失败 |
| 清空全部 events | 注册 head 仍非零，genesis/count/tail 缺失；不能返回“空链有效” |
| 同时改内容并重算该条 hash | 后继 previous_hash、受保护 head 或 checkpoint 不符；最后一条重hash由 head/checkpoint 检测 |
| 同时改 events 与 head | 普通角色由写保护拒绝；若管理员绕过，独立可信 checkpoint 检出已观察前缀不符 |
| reset 后复用原业务 UUID | epoch+snapshot_hash 解析旧副本；新实体不可替代原锚 |

同库 head 检测只改 events 的删尾/清空。具备 owner/superuser 权限并同时重写 events/head/seals、取消保护的攻击者超出普通 DB 写者防护；未经独立保存的未来尾部也没有外部预期。可信 checkpoint 可证明相对已经观察的历史未变，不能声称签名、可信时间戳或 FULL-803 外部存证已经实现。

## 8. 只读核验顺序、结果与版本

服务在同一个 REPEATABLE READ/READ ONLY 快照内取得 epoch/head/events/references/checkpoint；不 refresh 生命周期、不投影、不结算、不 append、不修 head。域按 sequence 流式执行：

1. 核 expected tenant、epoch/genesis/head/seal 与预算，不接受按 occurred_at 排序的输入；重复/缺序、跨 epoch 直接报告。
2. 对每条辨认 envelope/canonical/payload 协议；已知协议严格重新 parse、计算 hash、核 previous_hash 和独立 IDs/key 唯一。
3. 核 typed aggregate/correlation/关系、每个 subject snapshot 的原字节/hash/owner/epoch及原金融锚，cause 必须存在且较早、类型及主体匹配。
4. 到 checkpoint 指定 sequence 比较原 ID/hash；结束后比较完整 count/tail/genesis/head；必要时继续核 previous seal 链。
5. 分别输出链字节完整性、原引用覆盖和 checkpoint 覆盖，不用单一“防篡改通过”代替范围。

`AuditVerification` 明确字段：`schema_version="audit-verification-v1", simulation=True, user_id, epoch_id, status, chain_status, reference_status, checkpoint_status, actual_count, expected_count, actual_tail_id/hash, expected_tail_id/hash, verified_through_sequence, errors, warnings`。每条诊断为 `code, sequence_number|null, event_id|null, reference|null, message`，固定排序为 sequence/code/reference，最多 100 条并明确 `errors_truncated`。验证读时钟仅是响应附注，不参与旧 event hash。

整体 `status` 只有以下值，脚本不能把未知或预算不足当成功：

| 状态 | 条件 / 脚本退出码建议 |
| --- | --- |
| `VALID` | 完整已知协议、全部必要引用、完整 head、所要求 checkpoint 均核通过；0 |
| `INTEGRITY_ERROR` | 确定的内容/hash/序号/tenant/cause/引用/head/checkpoint不一致；1 |
| `UNSUPPORTED_VERSION` | 未知 envelope/canonical/event type/payload version 或不能解释的原锚算法；2 |
| `LEGACY_UNAUDITED` | 原历史未录制，或明确 legacy continuation 缺原生命周期事件；3 |
| `INCOMPLETE` | 缺预期 epoch/checkpoint/archive、输入为分页前缀或超资源预算；4 |

未知 schema/canonical 不能用新规则重算并宣称 VALID；已知 envelope 的未知 payload 可以报告可见结构/hash结果，但语义覆盖仍 UNSUPPORTED。未知之后不跳过该条继续报全链完成。不把303真实坏源 INVALID/当时MISSING证据等已声明 BLOCKED结果当链损坏。独立可确定的违规优先 INTEGRITY_ERROR，其次 UNSUPPORTED_VERSION、INCOMPLETE、LEGACY_UNAUDITED；其余分项保留真实状态。

`verify` 只证明已记录事件和原关联是否一致，不能证明从未录制的业务事实不存在，也不能因链 VALID 给当前动作放行。覆盖是否完整另由实际事件接缝验收证明。304 正式接入前，303 的 `audit_chain_status` 继续 `NOT_IMPLEMENTED`；后续只对存在正确审计事件的原记录给出对应状态，旧记录不自动回填。

## 9. 后续最小 RED/GREEN 目标与本次交还

后续纯域组应先真实 RED 再实现：dict 顺序/UTC微秒/JSON往返稳定； signed delta和margin/strict bool金额拒绝；0.8 原配置与坏raw副本保留；后续观察/追加时钟重试原样且稳定payload/occurred/cause改变冲突；单条改内容/重hash、末尾/中间/全部删除、head/checkpoint一起替换；跨tenant/epoch/错aggregate/correlation/cause/同UUID新epoch拒绝；T1迟到occurred不倒排sequence；未知协议、legacy、预算不足不PASS。真实PG并发、权限保护、reset封存、独立银行提交和只读API由各owner在303验收后分组证明，本文不新增测试矩阵或运行测试。

本次新增仅 `docs/progress/MVP-304-domain-design.md`。执行仅 `rg`、`Get-Content`、`Test-Path`、定向 `git status/diff --check` 和文档写入，核对两份计划、两份304预审、AuditEvent/IdentityMixin/UTCDateTime、303纯域/服务合同及任务脚本入口。未运行 subprocess 测试、资金动作、迁移、seed/reset 或验证脚本。未修改任何生产源码、测试、生成合同或既有证据。

下一步前置：root 完成303完整验收，统一冻结事件 registry、reset存储方案、epoch/head/snapshot保护与本文公开API；随后才可授权304实现和真实TDD。本文的所有304结果仍为设计要求，不是已通过的证据。
