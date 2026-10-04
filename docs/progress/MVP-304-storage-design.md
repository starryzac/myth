# MVP-304 持久化、物理只追加与演示 epoch 重置设计

状态：`DESIGN_ONLY`，2026-10-04。303 完整检查 `20261004T051331Z-0c360f9c` 尚由 root 执行，生产源码、测试、迁移及生成合同继续冻结。本次只新增本文，没有运行测试、seed/reset、迁移或资金动作。本文是后续实现方案；需 root 与[域合同](MVP-304-domain-design.md)、[事件接缝](MVP-304-event-map-preflight.md)一并冻结，不能标记 304 已完成。

## 1. 推荐选择：事件永久保留，业务副本按 epoch 封存

建议保留 `audit_events` 的所有原行及预留合成 `User`，新增 `audit_epochs` 和 `audit_subject_snapshots` 两张表。普通追加和演示 reset 均不得 UPDATE、DELETE 或 TRUNCATE 原事件和副本。reset 先保存旧业务图的完整副本并封存旧 epoch，随后只重建当前业务数据，最后记录新 epoch 的真实 seed 摘要。旧事件的 run/action/receipt 引用改为同用户、同 epoch 的永久副本引用；causation 仍使用保留的事件 FK。

这一选择使“审计事件物理只追加”与固定演示 UUID 重用同时成立，不需给审计 DELETE 提供例外，也无需给所有业务表增加 epoch。封存不是结算、撤销或对账成功：旧 UNKNOWN、T1 ACCEPTED、尚未投影的请求完整保留原状态，下一 epoch 是新的合成实验，不能继续拿旧请求操作新账户。

| 选择 | 新持久数据与改动 | 重置保护及成本 | 本次建议 |
| --- | --- | --- | --- |
| 保留事件 + 永久 subject 副本 | 两新表；事件加 epoch/协议/原规范文本；替换三项 resettable live FK；保留 User | 无事件删除旁路；旧副本需在首次引用及 reset 前实际捕获；历史解析必须指定 epoch/hash | 采用 |
| sealed archive 后删除 active 事件 | head、archive 及旧事件原件；受限搬移/删除函数；核验需合并两处存储 | 正确实现可以保留逻辑历史，但 `audit_events` 物理只追加承诺不成立；仍需完整业务原件、特殊删除权限和两处重复/遗漏检查 | 范围更大，不采用 |
| 所有业务行也永久保留 | 所有 owned 表及唯一键、确定性 UUID、业务查询增加 epoch | 可维持所有 live FK，但改变现有业务主键、查询及银行账本边界 | 留给确有需求的后续版本 |

## 2. 源码核对结论

| 当前接缝 | 已查到的事实 | 304 必须处理 |
| --- | --- | --- |
| `apps/api/app/db/models.py:478` | AuditEvent 有同用户 seq/key/hash 唯一约束，run/action/receipt/causation RESTRICT FK；没有 head/epoch/写保护 | 不仅加一个 RESET 事件；保护事件及独立 head，替换会随 reset 消失的三项引用 |
| `apps/api/app/services/demo_seed.py:373` | `_clear_demo` 先 UPDATE audit causation，再 DELETE AuditEvent、业务依赖图和 User；还清空 source 自引用及 303 nullable run links | 完全移除 audit UPDATE/DELETE 与 User DELETE；业务自引用只在完整旧图已归档后清理 |
| `demo_seed.py:716` | 单 `Session.begin`、固定 exclusive advisory xact lock、预留身份/产品检查、clear/insert/open-bank/summary | 保留单事务和原冲突检查；增加 seal/archive/new-epoch，任何失败一并回滚 |
| `demo_seed.py:657` | `_summary` 遍历全部 `Base.metadata.sorted_tables`，当前包括 audit_events；新 audit 表也会进入 counts/hash | 使用显式业务表 allowlist，不能把保留历史误算进确定性业务摘要 |
| `test_execution_seed.py:34`、`test_decision_trace_reset.py:109` | 现有 20 表摘要及成功 seed 后整库相等断言，失败 reset 则要求整库相等 | 成功改为业务图相等 + 审计按规定增长；失败仍完整整库相等；保留旧验收证据不改写 |
| `execution.py:301`、`recovery.py:128` | 应用预留、独立银行、应用投影是三个独立事务，User 行锁只覆盖各段 | 只让 reset 拿 User 锁不能阻止它插入两个阶段之间，需整个逻辑命令的 reset gate |
| `docker-compose.yml`、`.env.example`、`db/settings.py`、`alembic/env.py`、`db/testing.py` | API、迁移及测试管理连接默认共用 `bounded`；源码没有单独 runtime 非 owner 角色配置 | 不声称已经存在角色隔离；先证明普通 DML guard，再明确 owner/管理员边界 |

现有 PolicyVersion、银行 posting/请求/operation 保护主要针对 UPDATE，DELETE 仍用于当前演示清理。304 不更改经济事实规则，不把旧表的合法 reset 删除伪装成资金撤销。本文没查询运行中 DB 的 `pg_roles`，上述角色结论来自配置，不是当前数据库权限验收。

## 3. 最小关系结构

### 3.1 `audit_epochs`：head 与最终 seal 放在同一行

| 字段 | 约束与含义 |
| --- | --- |
| `id`, `user_id`, `epoch_number` | UUID PK；User RESTRICT FK；`UNIQUE(id,user_id)`、`UNIQUE(user_id,epoch_number)`；ordinal 正整数连续，不能用 epoch UUID 排时间 |
| `schema_version`, `canonical_version`, `status`, `opened_at` | v1 协议；OPEN/SEALED；可信追加时钟；每用户至多一 OPEN epoch 的 partial unique index |
| `previous_epoch_id`, `previous_seal_hash` | 同用户前 epoch FK；第一 epoch 两者 null；以后必须为直接前一 sealed epoch 及其真实 seal，禁止跨用户、跨号或循环 |
| `event_count`, `last_sequence`, `last_event_id`, `last_event_hash` | 对 v1，count 等于连续最后序号；对外至少 1；tail 同用户同 epoch Event FK；不会依赖核验时重新取 max 充当可信 head |
| `genesis_event_id`, `genesis_event_hash` | 精确首个 EPOCH_STARTED；同用户同 epoch FK；与原 seq=1/previous_hash=null 一致 |
| `sealed_at`, `seal_canonical_text`, `seal_hash` | OPEN 均 null；SEALED 必有完整原规范文本及域分隔 digest；seal 绑定真实最终 head 和 archive manifest |
| `archive_manifest_hash`, `archive_record_counts` | reset 前保存的完整旧图以及本 epoch 已保存的原副本集合；按受控 kind 计数，包含 0；不是业务 dataset digest |

创建 epoch 时内部 head=0 只允许存在于一个尚未提交的事务内。deferred constraint trigger 在提交前要求真实 genesis、连续 count/tail/genesis 与 head 一致，不能提交一个“空链有效”的 epoch。head/event 相互引用的 FK 使用显式 ALTER 和适当 deferred 检查，不靠关闭 FK 解决 DDL 循环。

### 3.2 `audit_subject_snapshots`：同实体允许多个真实状态版本

保留 `id` UUID PK 便于现有 `OwnedMixin`/检查工具使用；必需字段为 `user_id, epoch_id, kind, entity_id, scope, snapshot_version, canonical_text, snapshot_hash, captured_at`。对 `(epoch_id,user_id)` 建组合 RESTRICT FK，主解析唯一键为 `(user_id,epoch_id,kind,entity_id,snapshot_hash)`。`role=BASIS|BEFORE|AFTER` 属于事件 reference，不是这张表的唯一键：相同内容可以被多个角色/事件复用，不同 before/after/final 内容按 hash 共存。

原件是[域合同](MVP-304-domain-design.md)的完整 `audit-subject-v1` envelope canonical UTF-8 文本，不是 JSONB 重新输出。digest 使用该合同的 namespace 前缀与原字节。文本解析后核 kind/id/epoch/捕获 tenant；TENANT 的实际 row.user_id 一致；GLOBAL_CATALOG 只允许原精确 ASSET_PRODUCT，实际 owner 为 null，但捕获 tenant/epoch 仍存在。此处不把 source.content 内可能已坏的 user_id 当成 typed row owner。

不得把 float 转成 int、把 raw 错误金额改成正确金额，或改写 303 原 `trace_hash`、request/config/content hash。单副本 16 MiB/深 64 的域限制及 DB 字节上限要同时执行；大型 epoch 按实体流式保存和核 manifest，不存成一个无界巨型 JSON。PostgreSQL JSONB 会改变文本/数值显示，原文本保存的理由见[官方 JSON 类型说明](https://www.postgresql.org/docs/16/datatype-json.html)。

首次 reference 的完整副本在实际使用该对象的事务内保存。BEFORE 在真正修改之前注册，AFTER 在 flush 后注册；若事件/业务回滚，注册也回滚。保存重复内容先核已有 hash 与原字节，直接复用，不能 UPDATE captured_at。正常旧副本不要求和当前 mutable balance/status 全等。

### 3.3 修改 `audit_events`

- 新增 `epoch_id`、envelope/canonical version、`canonical_text`；`created_at` 显式冻结，域名为 appended_at。原字段和 payload JSONB 保留为可查询投影，哈希与解释消费原 canonical 文本；两者必须对应，不能出现两套事实。
- **从全 user 序号改为每 epoch 序号**：替换既有 `(user_id,sequence_number)`、`(user_id,idempotency_key)`、`(user_id,event_hash)` 为加 `epoch_id` 的约束。每个 v1 epoch 从 1 开始。域沿用现有 Integer 的 `2**31-1` 上限，不在 304 顺带改为 bigint 或全局序号。
- 增加 `UNIQUE(id,user_id,epoch_id)` 与同用户同 epoch 的 causation FK；追加核 cause.sequence < new.sequence 和原主体直接因果，不能只核同 UUID 所属用户。
- 移除 **仅审计表**对 live decision_runs/action_plans/action_receipts 的三项 FK。top ID、aggregate、correlation 对应的 `payload.references` 必须在 DB INSERT guard 中逐项查到 `(user,epoch,kind,id,snapshot_hash)` 永久副本。已有业务表之间的 FK 不动。仅移除 FK 而没有这项强制检查属于失败实现。
- 增加 `(user_id,epoch_id,event_type,payload.fact_key)` expression unique index，阻止同一真实事实换 key 双记。references 的 kind/id/role 不重复、anchor 与真实副本一致由严格域/服务核验，DB 至少保护引用所有权和确实存在。
- 列表按 `(epoch_number,sequence_number)`；业务发生时间不用于链排序。cursor 严格解析且与 tenant/epoch/核验边界绑定，不能分页前缀冒充完整核验。

已有非 v1 AuditEvent 不能重新算 hash 冒充正常 v1 genesis。最窄兼容是新增 header 对这些旧行保留 null、原列/hash 一字不改，v1 INSERT guard 拒绝新增这种行，旧唯一约束保留为 legacy partial unique index。读取标 `LEGACY_UNAUDITED/UNSUPPORTED`。第一次 activation/reset 另以 `LEGACY_AUDIT_EVENT` 私有 archive kind 保存其真实原件及当时还存在的引用图，并记录真实捕获 epoch；以后只按该副本解释，不能使用复用 UUID 的新 live 行。新 v1 cause 不得指向未录制的旧 legacy 事件。此为有标签的观察归档，不补造过去的受理/结算记录。

## 4. 不依赖可设置开关的普通 DML 保护

推荐使用 trigger 保护，不先把 304 扩展为新的部署/认证系统。当前 owner 连接执行普通 SQL 也必须触发 guard。owner/superuser 主动 ALTER/DROP 保护或改系统目录属于明确的管理员边界，不能据此宣称外部存证或管理员不可篡改。

| 对象/操作 | DB 规则 |
| --- | --- |
| Event UPDATE/DELETE/TRUNCATE | 无条件拒绝，包括 demo reset；TRUNCATE 必须有 statement trigger，不能只做行 DELETE guard |
| Snapshot UPDATE/DELETE/TRUNCATE | 无条件拒绝；SEALED epoch 不可追加 snapshot |
| Snapshot INSERT | 校验版本、原文本 digest、owner/epoch/kind/id、受控实际 row 捕获；禁止客户端任意 JSON 当原金融副本。BEFORE 先注册，AFTER 后注册；原 raw 区域按既有 INVALID/MISSING 合同处理 |
| Event BEFORE INSERT | User/epoch 锁、已知协议/类型、原规范字节与完整 envelope hash、OPEN 状态、真实 head、连续 seq/prev、因果与永久副本引用；失败使所属业务事务失败 |
| Event AFTER INSERT | 唯一的正常 head 推进路径：从真实新行推进 count/seq/tail，首条设 genesis；最后 reset seal 也由这条路径完成 |
| Epoch 直接 UPDATE | 拒绝；guard 不仅看 `pg_trigger_depth()`，还核嵌套事件推进、OLD→NEW 正好 +1、真实行/prev、实际 count、genesis、所有未允许修改字段不变。把 head 改成同值也不能成为修链接口 |
| Epoch DELETE/TRUNCATE | 无条件拒绝，旧 seal/ordinal 不可移除 |
| Epoch INSERT | 只允许合法初始化或 reserved-demo 前一 epoch 已真实封存后的下一 ordinal；内部空 head 必须同事务有正确 EPOCH_STARTED。提交时 deferred guard 再核。不能任意插一个伪 head |
| SEALED 后状态/head/seal/archive 元数据 UPDATE | 拒绝；无重开、重算 seal 或 retroactive repair |

`pg_trigger_depth()` 单独不足：其它触发器也能形成嵌套。head guard 必须依据本次真实插入事件、旧 head、当前 count/tail 和允许的完整 delta 验证。触发函数可用 SECURITY DEFINER 让非 owner INSERT 角色无法直接 UPDATE head，却由受控 AFTER INSERT 推进；使用固定安全 search_path、schema-qualified 对象、无 caller 表名/SQL/角色参数，撤销 PUBLIC 的非必要 EXECUTE。在同事务安装函数及权限，避免公开窗口。原则见[PostgreSQL 16 安全函数说明](https://www.postgresql.org/docs/16/sql-createfunction.html)。

普通 event/head/seal 的合同没有 float，因而可实现受限整数 JSON canonical 编码和 pgcrypto SHA-256，真实 PG 与 Python bytes fixture 必须一致。domain-separated 的 NUL 前缀使用 bytea 拼接，不能往 PostgreSQL TEXT 写 NUL。snapshot raw float 原件不经这套整数编码重写，只核其固定原文本 digest、来源与域合同。`pgcrypto` 是否可用是迁移实测前置，不能把当前镜像名当已经安装证明。[官方 digest 接口](https://www.postgresql.org/docs/16/pgcrypto.html)

当前 compose 默认管理连接仍是管理员/owner 配置；不另设密码、不删卷、不把 admin URL 当 runtime 非 owner 验收。304 定向 PG 测试应在生成的测试数据库使用临时非 owner、非 superuser、无表/函数/schema 所有权的普通角色，证明 SELECT/合法 INSERT 成功、直接 head/历史改删和 ALTER guard 失败；普通角色没有任意 epoch/head UPDATE 或 TRUNCATE grants。测试 role 名随机且只清理本次 role，不修改现有 bounded。已有 owner 连接还应证明普通 UPDATE/DELETE/TRUNCATE 被 trigger 拒绝。PostgreSQL 权限项是各自独立的，参见[GRANT](https://www.postgresql.org/docs/16/sql-grant.html)及[TRUNCATE](https://www.postgresql.org/docs/16/sql-truncate.html)。

该测试证明机制对普通角色有效，不等于本地 API 已配置非 owner 连接。若后续真要宣称“API 凭据本身无 ALTER/DROP 权力”，才另行启用非 owner DATABASE_URL 与明确 migration/admin URL；现有持久卷须显式 bootstrap，不能依赖首次 initdb 脚本自动重跑。MVP 当前共享服务角色也不是用户级 DB 认证/RLS；这里证明 tenant 引用一致与服务访问隔离，不声称能抵抗持有管理员凭据者。

## 5. 追加与只读 helper 合同

建议后续 storage owner 实现下列 public helpers；调用方已有 Session/事务，无内部 commit、银行调用或自动修复：

```python
ensure_audit_epoch(session, user_id, *, appended_at) -> AuditEpoch
capture_audit_subject(session, user_id, epoch_id, kind, entity_id) -> AuditSubjectSnapshot
append_audit_event(session, intent, *, observed_at, appended_at) -> AuditEvent
get_audit_head(session, user_id, epoch_id=None) -> AuditHead
list_audit_events(session, user_id, *, epoch_id, limit, cursor=None) -> AuditEventPage
verify_audit_chain(session, user_id, *, epoch_id, checkpoint=None, mode="PREFIX") -> AuditVerification
```

顺序固定：原业务 write gate → User FOR UPDATE → epoch/head 锁 → 同 key/真实 fact 查询 → 原副本/intent 核验 → 首次分配序号及时间 → INSERT event（guard/after-head）→ 原业务事务提交。空链也有用户锁；不是裸 max+1。相同 key 先核原 event/head 和完整稳定语义后返回，不能先用本次 now 生成另一 payload。same-intent 排除新观察/追加时钟和分配的 id/sequence/prev；原经济时间、请求、cause、金额或引用变了就 409。不同 key 同 fact 不生成第二条经济事件。

active 核验还调用 303 的原内容检查及已有 receipt verifier，允许合法生命周期变化作注记，原不可变内容改了不能被副本掩盖。sealed 核验只消费原 canonical snapshot/seal，按 DTO/ReferenceBundle 复用原 hash/关系/receipt 语义；不能向现有 Session 查同 UUID 的新 active 行。不重跑决策算法。receipt 的离线副本核验需要先将现有只读规则整理为可消费 DTO 的共用验证核心，不能为了 reuse 旧函数而构造假 ORM Session/临时 live 数据或再投影一次资金。

GET/list/verify/脚本从同一个 REPEATABLE READ、只读且 no-autoflush 快照取得所有内容。list limit 1..100，严格 cursor；verify 用域预算流式读取完整 epoch，以独立 head/seal/checkpoint 做删尾检查。旧历史、未知协议、缺 archive 或预算不足沿用域明确结果，不能 PASS。没有事件的业务事实不能由 GET 回填，也不能因核链有效就断言没有遗漏业务接缝。

## 6. 完整 archive allowlist 与 manifest

归档必须是显式业务表清单，不遍历所有 metadata，否则新 snapshot、event、epoch 会被递归归档。清单共 19 张现有业务表：

```text
users                         accounts
evidence_items                transactions
credit_card_bills             asset_products
policies                      policy_versions
policy_proposals              goals
asset_positions               decision_runs
decision_constraints          action_plans
action_receipts               simulated_bank_redemptions
simulated_bank_postings        bank_operations
action_resource_reservations
```

User 仅 reserved demo；17 张 owned 表严格 `user_id=DEMO_USER_ID`；Product 只复制该旧图实际引用的精确版本与固定 seed catalogue 版本，原全局表不删、不覆盖。按真实 table→kind registry 包装完整 row，字段包括原 parent/action links、constraints 投影、mutable recovery result、confirmation 原证据、完整 request/receipt、全 posting legs、资源声明。私有 archive kind 补齐 `USER, TRANSACTION, CREDIT_CARD_BILL, POLICY_PROPOSAL, DECISION_CONSTRAINT`，不把它们强塞成普通业务事件；旧审计行可单独以 `LEGACY_AUDIT_EVENT` 私有 kind 捕获，不递归 snapshot 以前的审计副本。收入预留目前在真实 evidence/ledger 内容中，不虚构 INCOME_RESERVATION 数据表。

manifest 是按 `(kind,entity_id,snapshot_hash)` 排序的原副本 hash 索引及每 kind 计数，分隔/编码使用明确 canonical 版本；同时绑定所有本 epoch 事件引用的历史状态副本和 reset 前的每个最终 business row。保留 BEFORE/AFTER/final 三种实际版本时总副本 count 可以大于 live row count，分别记录 final graph counts 与 archive snapshot counts，不能混为一个数量。seal 核所有已引用副本存在，并核 final graph 对 allowlist/实际表逐行完整覆盖；只给行数或所有 hash 拼成无分隔字符串都不充分。

snapshot 在业务自引用清空前捕获；否则 parent_run_id/subject_action_plan_id/supersedes_id 原关系已丢失。因 source 真坏导致 BLOCKED 的原副本仍可封存，保留 declared/captured digest 和 INVALID；事件/head/原 trace/receipt 出现新篡改则拒绝 reset，不能借重置洗掉损坏。原缺源保持 MISSING 标记，无虚构完整原件。

## 7. 演示重置的精确单事务顺序

建议 `seed_demo` 仍返回确定性业务 SeedSummary；审计元数据由独立查询/CLI envelope 返回，不混入该 equality 值。business seed 继续 `mvp-301-v6`、原固定 UUID、金融事实、产品版本和金额；摘要以新增 `seed-summary-v2` 协议明确只含上述 19 业务表（不含三张审计表）。未来 summary 数量从 20 改为 19 是范围声明变化，不能更改已有303验收日志/manifest来伪造历史一致。

重置实施过程：

1. `Session.begin` 内先取得现有 fixed demo advisory **exclusive transaction lock**，再预留身份/User 行锁和现有产品冲突校验。没有用户时先创建唯一合法合成身份；有用户只校验/保留 identity，不 DELETE User。
2. 取得旧 OPEN epoch/head；从稳定原状态核旧链、head、原 trace/request/receipt 与全部永久引用。无旧 v1 epoch 时显式 INITIAL/LEGACY_ADOPTION，不补造旧业务事件；旧非 v1 audit 原件另作有标签捕获。
3. 按第6节复制旧完整图；核 final graph/历史副本覆盖，计算实际 archive manifest。此时任何业务自引用均未清空。保存本次 reset key、可信身份/原因与旧 epoch 原 seed digest。
4. 在旧 epoch 最后追加 `DEMO_EPOCH_SEALED` 元事件，绑定原 pre-seal head、实际 archive manifest/counts、reset 身份/原因。该事件的 AFTER INSERT 先正常推进 head，再从**真实最终 head**构造独立 `audit-epoch-seal-v1` canonical seal/hash，置 SEALED；无单独直接 UPDATE head 接口。seal 的自身 hash 不放进自身散列数据，不能等同于 tail hash。旧 seal 记旧 epoch 实测内容，不能把计划的新 seed digest当已经完成。
5. 只清空本 demo 的业务自引用和 303 nullable run links，按现有 FK 依赖顺序删除17张 owned 业务表。永久 event/epoch/snapshot、User、全局产品、其它用户数据以及文件验收证据完全保留。
6. 在同 Session 重插固定业务 facts、独立 bank openings/exposure；userinfo 初次 INSERT 或只重置允许的显示字段，预留 id/ref/simulation 冲突仍拒绝。flush 后取得实际19表 business summary/hash。
7. 建立下一连续 OPEN epoch，在其 seq=1 追加 `EPOCH_STARTED`。`previous_hash=null`；元 payload 绑定前 epoch ID/独立 seal hash、本次 reset key、实测 seed version/summary protocol/dataset hash。epoch ID不是银行 operation ID，也不伪造新决策run。
8. 提交前 deferred head/epoch 检查再次核 genesis/tail/count/seal链。以上 archive/seal/delete/insert/open-bank/new-genesis 一起提交；任何失败恢复旧业务图、旧 OPEN head、原 nullable关系，并且无孤立副本、新 seal 或新 epoch。

首次安装/seed 则没有 pre-existing epoch 可封存，直接在真实 initial seed 之后建立首个 EPOCH_STARTED；不能为了对称额外建一个假空旧 epoch。同 reset key 在上一 seal及当前 genesis 已匹配时验证后返回原 summary，不封第二次；同 key 的 reason/target/稳定参数不同拒绝。CLI 每次显式新 reset 才生成新 key；重试必须保留原 key。

`EPOCH_STARTED` 与 `DEMO_EPOCH_SEALED` 是元事件：aggregate/correlation 为 EPOCH，cause 可 null；不能强制它们有一个虚构 run/policy/goal。其专门 payload 的 `epoch_transition` 必须由域 registry 明确声明，携带 genesis 或 seal 的上述字段。普通事件无此额外字段。metadata 本身按真实 epoch/head核，不制造自引用 snapshot/hash 循环。该小项需和域草案一起冻结。

### reset 与三个独立事务的并发

同 User FOR UPDATE 仅保证一个阶段提交原子性。execute_action/run_recovery 必须在**全部阶段外层**持有同固定 demo advisory key 的 session-level shared lock，银行阶段仍使用原独立 Session/commit；reset 的 exclusive xact lock等待整个逻辑调用结束。guard 使用专用固定连接，先锁 gate 再取任何 User锁，finally 显式 unlock，连接返回池前确认无锁；不能借持有 User锁再开另一 Session等待 gate形成死锁。单事务写入口和可独立调用的银行入口至少先取 shared xact gate，再User锁；reset 内 seed银行调用使用原已锁连接，不另开连接获取shared锁。

读取不拿 write gate，稳定快照与永久副本使旧读仍一致。测试应证明 reset 在 reserve→bank、bank→projection 和 recovery各段暂停点等待，而不混用新数据；异常释放共享锁，旧 UNKNOWN/等待状态可在后续显式合成重置中真实封存。不新增跨阶段大事务、不回滚已提交银行事实。当前没有后台补结算 worker；以后若引入，任务必须带 epoch fence并拒绝处理 sealed旧 epoch，不能仅凭复用业务 UUID操作当前图。

## 8. 迁移与必须执行的定向真实 PG 目标（本次均未执行）

建议 migration `0006_audit_chain` 接 `0005_decision_trace`，单DDL事务创建两表/索引/FK/整数canonical与保护函数，调整event约束/引用，最后安装guard。升级保留已有user/business/原trace字节和经济数值；不批量写假 DECISION/BANK events。源码初始化接缝记录真实 activation/genesis，不由 migration给未发生的运行历史补授权。

降级只允许生成的 `bf_test_<32hex>` 名称，offline及正式库一律拒绝；即使测试库，存在任何 v1 event/epoch/snapshot也必须拒绝丢历史降级。空新表且旧 live FK完整可恢复时才删除guard/两表/新列、恢复原0005结构；legacy行缺原live对象也拒绝恢复FK。检查在任何DROP之前进行，失败不得留下半降级。不要自动清空历史来让降级测试过；临时DB整体销毁是独立既有fixture清理。

| 定向组 | 真实行为与断言 |
| --- | --- |
| 首次追加与幂等 | 同用户并发连续 seq/count/head；不同用户互不引用；同key不同后来时钟原样重放/零写入；改稳定payload或同fact换key409；未知协议拒绝/明确 unsupported |
| 普通 SQL 保护 | owner普通UPDATE/DELETE/TRUNCATE事件/副本/head拒绝；非owner角色合法append成功且无ALTER/DROP权力；direct head rewinds、同值UPDATE、伪造genesis/head/seal、无真实副本引用拒绝；PUBLIC/helper/temp schema不可构成旁路 |
| 内容与删尾 | 在**随机测试库**以明确管理员故障注入改body/重hash、删末尾/中间/全链、改snapshot、改head，分别验证邻链/head/seal/checkpoint失败；该组与普通DML拒绝组分开，不把管理员绕过声称普通角色漏洞 |
| 历史与跨用户/epoch | 原active immutable内容变化拒绝、正常 lifecycle变化注记；原INVALID/MISSING BLOCKED仍可核；sealed旧同UUID从旧canonical副本解析；跨user snapshot/causation/correlation及跨epoch cause拒绝；无源不补造 |
| reset成功 | 真实prepare→confirm→execute→receipt、恢复/T1/UNKNOWN状态原件；连续三次reset每次旧seal/archives可完整核、新genesis指向正确seal；业务19表同原seed摘要、审计行数有明确增长；未引用/多版本final row覆盖完整 |
| reset失败原子性 | 链坏在clear前拒绝；archive/hash/seal故障、clear后salary insert故障、bank opening故障、new genesis/deferred检查故障分别整库dump前后一致；原parent/action/supersedes和head状态保留 |
| 并发reset | 三段真实命令暂停时reset等待；提交/报错后才archive；并行reset新key顺序成独立epochs，同key只一次；shared gate finally释放、无池泄漏/死锁 |
| tenant与目录 | 其它user拥有完整run/action/request/receipt/postings及审计链，reset前后全部原字节/head一致；预留id/ref非模拟占位与产品漂移拒绝；全局产品和项目质量证据不清理 |
| migration | 全新0005→0006 schema/metadata一致；已有303业务trace原bytes不变、旧audit明确legacy；空测试库降/升级；带任何审计历史的downgrade拒绝且状态/数据不变；正式名称/offline拒绝；检查compoundFK/index/guard是否实际存在 |
| readonly | audit GET/list/verify、303读、现有receipt verifier、CLI verify整库before/after一致；stable snapshot；没有补事件、修head、刷新策略、bank调用或autoflush |

成功reset的旧 `database_snapshot == baseline` 不能继续作为全库相等验收，因为历史必须保留。未来测试应拆为明确19表 business snapshot equality与审计增量/原seal不变；失败reset和所有readonly测试继续用全表原始dump，包括两张新增表，不弱化回滚/零写入验证。先保存具体行为RED，再修实现，保留 `MVP-304-storage-*` 原始日志；root统一运行全量check，owner不并发扩大覆盖。

## 9. 本次执行记录与交还

仅写入 `docs/progress/MVP-304-storage-design.md`。定向阅读两份304预审、域草案、模型、compose/env/session/testing/Alembic、seed/reset及现有执行/恢复事务与seed测试；使用 `rg`、`Get-Content`、定向 `git status`。为核 PostgreSQL JSON/权限/安全函数语义只读访问上述官方16文档；未探测生产DB权限、未运行资金动作、测试、迁移、seed/reset或验证脚本。一次误读不存在的 `app/schemas/execution.py` 只报路径不存在，已改为实际 `domain/execution_types.py`定向读取，没有据此编造合同。

304所有验证项目均为未来验收要求。推荐冻结：永久event/User、两表、per-epoch seq、canonical原副本、严格head推进/最终seal、完整19表allowlist、summary-v2、shared/exclusive reset gate及管理员边界；根303完整验收结束后才授权源码/TDD。
