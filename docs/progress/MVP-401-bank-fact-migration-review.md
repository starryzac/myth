# MVP-401 外部银行事实存储与迁移只读审查

状态：`DESIGN_ONLY / NOT_IMPLEMENTED / NOT_VERIFIED`。日期：2026-10-04。本文仅依据冻结源码和设计文档进行静态分析；没有运行数据库、migration、pytest、coverage、金融动作、正式 seed/reset，也没有编辑应用源码、脚本或合同。MVP-304 全量验收仍由 root 执行，本文不提前实现 401/404。

依据：[真实 E2E fixture 设计](MVP-401-real-e2e-fixture-design.md)、[401 前置设计](MVP-401-preflight.md)、[首页存储设计](MVP-401-dashboard-storage-design.md)。本文的字段、协议版本与 SQL 是实施建议，不是已存在接口或已通过证据。

## 推荐与两方案比较

推荐 **A：一个独立外部银行事实表，复用 `simulated_bank_postings`，新增可空 `external_fact_id` 原点**。工资与消费是银行观察到的事实，不是 Agent 命令，不能塞进要求真实 ActionPlan 的 BankOperation。独立原件保存身份、请求、银行结果与投影状态；现金仍沿现有 `(user_id, ledger_key, sequence_number)` 唯一连续链追加。原五类命令和 205 返本解码保持原语义。

| 方案 | 真实连续性要求 | 最小实施成本与结论 |
| --- | --- | --- |
| A：独立事实，统一 posting | 同一 `CASH:<account_id>` 的任何新腿，前序都取现统一银行头；命令、返本、工资、消费能互相接续 | 新增一表、一可空原点、约束和明确新协议；复用全链读取和现前序 FK。推荐 |
| B：独立事实与 legs 表 | 两表必须共享单一现金序号/头；前序需要表达跨表真实腿，不能分别各有 seq1 和独立余额 | 还需统一 head/leg 身份表或异构前序机制，改写所有 ledger 读取、排序、完整集合校验、封存与投影。若仅 union 两张各自独立的流水，不能证明唯一现金真值。不推荐作为 401 最小扩展 |

A 仍是金融协议变更，不能仅加测试 helper 或用已有 `PAYEE:` 名称绕过合同。401 可提供受信 runner 内部调用接缝，公开事件按钮和 reset 控制台留给 404。

## 当前源码提供的约束及不能直接复用之处

| 静态来源 | 当前行为 | 实施影响 |
| --- | --- | --- |
| [models.py](../../apps/api/app/db/models.py)：SimulatedBankPosting | 同用户 account/redemption/operation/previous 复合 FK；唯一 ledger sequence、operation leg；非 OPENING 必须 operation/leg/previous，before/after 非负且守恒 | 新外部原点需迁移 entry CHECK；保留已有 FK/唯一约束，不对旧行补造 operation |
| [simulated_bank.py](../../apps/api/app/services/simulated_bank.py)：ledger_heads | 读取该用户全部腿，逐账验证真实 opening、前序、连续 seq、余额接续与不倒退的 occurred_at | 可复用 A；不能改成 `max(sequence)` 或只查最新余额以掩盖中间缺腿 |
| 同文件 validate_bank_projection | 以完整 CASH/POSITION 头核对应用账户/持仓；`allow_unprojected` 仅补现 command/legacy 已结算无 receipt 差额 | 新事实银行已发生但应用未导入必须显式纳入差额；严格默认仍拒绝旧应用投影假完整 |
| 同文件 require_settlement_order | 查询现 BankOperation 的到期 ACCEPTED；不认识外部事实，也不等于所有已结算未投影的统一队列 | 必须扩展统一先后检查，不能把新增事实插入视为无需对账 |
| [execution_bank.py](../../apps/api/app/services/execution_bank.py)：process_operation | 从 action.request.execution 严格解码 BankCommand、核 effect/request/hash、原业务键、来源，独立提交银行事实 | 不接受工资/普通消费。原 decoder/identity 不能因新事件而放宽 |
| 同文件 open_execution_anchors / validate_income_locations | opening 仅受信 seed/import；工资 origin 必须精确绑定已有银行 INCOME transaction/proof，完整备查位置与原始 origin 核对 | 不能把 `source_ledger` 中 available 数或新账户余额当到账；运行中新发行位置须绑定新外部事实而不是调用 seed opening 猜数 |
| [0003](../../apps/api/alembic/versions/0003_simulated_bank.py)、[0004](../../apps/api/alembic/versions/0004_execution_bank.py) | posting 的普通 UPDATE 被拒绝；BankOperation 请求等不可变，终态不可改。现 posting 并非 DELETE/TRUNCATE 都被物理禁止 | 应准确称“不可变字段及完整性核验”，不能虚称全部银行业务表物理永久 append-only。受保留保护的是 304 事件/副本/epoch |
| [asset_exposure.py](../../apps/api/app/domain/asset_exposure.py)、[asset_exposure_import.py](../../apps/api/app/services/asset_exposure_import.py) | v1/v2/v3 是明确固定列清单；来源水位包含现事实时间集合 | 当前 v3 没有 external origin/fact。忽略新表后继续称 complete 是错误 |

## 最小表与原点迁移

建议后续迁移 `0007_external_bank_facts`，不改写 0006。新增 `external_bank_facts`（名称待 root 实施时冻结），沿用 OwnedMixin 的 UUID/id/user/created_at 与 `(id,user_id)` 唯一键。

| 字段组 | 建议字段与约束 |
| --- | --- |
| 原事实身份 | `protocol_version`；`event_type` 仅 INCOME_CREDIT / CONSUMPTION_DEBIT；`account_id` 同用户 FK；固定银行 adapter/source 标识、counterparty 标识、`external_ref`；`business_key` 与 `idempotency_key` |
| 原金额与时间 | 严格正整数 `amount_cents`、currency；aware UTC 的 occurred_at/observed_at；原 request 的 canonical text/hash。canonical text 保留协议真实原文，金额不能 bool/float/string；JSONB 可作索引投影，不替代原文 |
| 银行状态 | ACCEPTED / SETTLED / UNKNOWN / REJECTED；accepted/settled 时间、实际银行 result canonical text/hash、完整 posting ids/digest。状态 UNKNOWN 只指银行结果待核，不能把已知 SETTLED 改成 UNKNOWN |
| 投影状态 | PENDING / PROJECTED / UNKNOWN；一次性实际 projected_at、projection result canonical text/hash，原 transaction/evidence/location 身份与 before/after 摘要。银行 SETTLED + projection UNKNOWN 是可表达组合 |
| 初始出处 | 所属 capture epoch 的同用户身份/原协议来源；预先由服务器分配并冻结 transaction/proof IDs，保证两事务同一事实；不引用虚构 action/run/receipt |

最少一表即可分别保护银行字段和投影字段，不必借用 Agent ActionReceipt；投影结果是该事实自身的银行事实导入回执。新表不新增指向 Transaction/Evidence 的双向 FK 环：原投影结果保存 typed 身份并经读时/审计核验；若实施选择直接 FK，必须先解决 reset 删除图和失败回滚，不能只靠改删除顺序绕过环。

必要索引：`UNIQUE(user_id,source_id,external_ref)` 与 `UNIQUE(user_id,idempotency_key)`；业务原身份不因 REJECTED 后换键变成第二次真实到账；待处理索引 `(user_id,bank_status,projection_status,occurred_at,id)`。不同用户可以有相同银行 ref，但所有请求、结果、腿、读和重放必须同时核 owner。

现 posting 新增 `external_fact_id UUID NULL`，复合 FK `(external_fact_id,user_id)` → 原件 `(id,user_id)`，`UNIQUE(external_fact_id,leg_ref)`。保留原 operation/redemption/sequence 唯一和 FK。entry 规则改为：

1. OPENING：operation/external/redemption/leg/previous 全空，seq1、before0、delta非负。
2. 非 OPENING：operation 与 external **恰一非空**；leg、previous 非空、seq>1。external 原点不能带 redemption；旧 operation+legacy redemption 组合继续有效。
3. 原点不可变；外部结算 ledger 的 ECONOMIC 身份显式注册。新现金腿仍绑定原 account，外部清算腿无用户 Account/position。

旧 posting 的新列均 NULL，不对旧行做 UPDATE/backfill，不停用 immutable trigger。旧 BankOperation/request/hash/ActionPlan/evidence 与 opening 完整列保持原值；DDL 新列带来的投影字段差异另作版本兼容，不冒称全列集合从未变化。

## 余额、完整腿与新资金位置

| 真外部事实 | 两条 ECONOMIC 腿 | 应用投影 |
| --- | --- | --- |
| 工资 S | 原 CASH +S；明确工资付款方清算账 -S | 一条 CREDIT/INCOME transaction 和精确银行 proof；原账户只增加 S |
| 普通消费 C | 原 CASH -C；明确消费收款方清算账 +C | 一条 DEBIT/CONSUMPTION transaction 和精确银行 proof；原账户只减少 C |

推荐独立 `EXTERNAL_SOURCE:<fixed_counterparty>` / `EXTERNAL_PAYEE:<fixed_counterparty>` 账型，ledger_key 在同用户银行命名空间中；它们是银行侧清算，不是新用户工资账户，不计入首页现金。每个真实事件 ECONOMIC delta 总和 0、恰两腿，严格 leg_ref/身份/符号/金额/时间/完整集合核验；不得接受多一条隐藏 cash 腿或少清算腿。

非负余额协议下，工资来源账必须在**新隔离 fixture 初始导入**建立固定受信银行准备金 opening；记录来源、固定额度、协议和初始清单，它不是用户收入。不得在每次工资前临时补足，不得修改旧 opening，也不得用一个新用户 Account 接工资后内部转账冒充到账。清算准备金不足或用户现金不足应拒绝/保留待核事实，不能生成负头或静默修正。

推荐在新的固定可信 fixture 初始化事务内完成准备金导入，再由明确 seed-start 协议绑定实际 bootstrap 清单/原 opening 副本；这是可证的初始合成银行资本，不是普通 `ensure_audit_epoch` 将旧业务宣称无历史缺口。当前 seed_demo 是先 insert/open bank 再 start_seed_epoch，可作为这一顺序参考；它没有该准备金能力，须后续明确扩展及证明，不能直接加 SQL。若 runner 先照原 seed_demo 完成，审计 epoch 已存在，之后新增准备金必须走明确注册的受信 bootstrap 事件及原件验证，不能绕过审计。generic activation 看到已有业务仍须 LEGACY，不能把两种初始化混用后宣称完整 VALID。

所有腿复用全 ledger head，按银行实际时间追加，旧现金腿可成为工资前序、工资可成为购买前序、消费可成为返本前序。原事件迟到且 occurred_at 早于该 ledger 头时，最小协议明确拒绝自动追加并保留待处理原因；不倒改原 occurred_at，也不重排旧腿。第一轮 E2E 只安排稳定阶段，不能因此宣称任意乱序 ingress 已实现。

INCOME_LOCATION 是备查维度，不与两条 ECONOMIC 腿再求同一现金合计。工资 origin 的 transaction/proof UUID、真实金额 S、原银行 hash 和发生/观察时间在银行原结果冻结，应用按同一原结果创建实际行。新位置可先建真实零 opening，再以该 external 原点发一条 AVAILABLE +S 发行腿；其他 bucket 保持零。发行必须核完整工资事实，不能由 `available_cents` 输入决定。现 `open_execution_anchors` 只作为约束参考，不能在银行阶段查询尚未投影的 Transaction 再自举来源。

消费对已有可用 income locations 的扣减规则须固定且可核，例如同账户按 origin occurred_at/id 顺序仅消耗 AVAILABLE，形成 AVAILABLE→SPENT 成对备查腿；总消费 C 与被追踪来源用量及其余非追踪现金分开记明。不能释放 RESERVED/ASSIGNED/goal owned 以凑可用来源，不能把实际消耗标成未消耗。若普通用户消费会侵入这些既有归属，最小 adapter 应保留明确对账/归属冲突状态，不假称支持任意消费；第一轮 fixture 选择足够非归属可用现金且仍能触发真实边界风险的 C。

## 银行提交、投影、幂等与 UNKNOWN

整个事实接收/原事实重试命令使用现 `audit_command_guard(engine,user_id)`；每个实际事务在 User 行锁前调用 `transaction_gate`，与 reset exclusive 使用同一键和顺序。保留独立银行事务与应用投影事务：

1. 验原键/owner/request，再检查完整账头和统一先后队列。银行事务保存原件、实际双腿及备查腿、银行结算结果和实际审计锚并提交。
2. 应用事务重新验证同一原件与完整腿、应用原 before，按实际 delta 创建 Transaction/proof 和余额/覆盖/exposure/income 后继，再保存一次投影结果及审计。更新合法源生命周期，旧 content/hash 不变。
3. 实际投影失败时回滚整套投影；银行 SETTLED、原腿和原审计保留。可以在后续独立事务标 projection UNKNOWN；GET 不能恢复或补写。原键重试只补投影，不能再动银行现金、再生成 origin 或回执。

相同身份/key 与相同语义请求返回原事实；更换 retry observed_at 不变原发生事实，比较口径须明确。相同 key 不同账户/金额/role/counterparty/原时间为 409；外部 ref 相同但换 idempotency_key 也不能重复经济效果。投影 IDs 由原结果固定，靠唯一性和只读完整核验拒绝第二套导入。

统一队列须同时看 command/legacy 与 external 的银行 pending、已结算未投影，不能仅复用当前 require_settlement_order 就宣称安全。处理冲突时保留已观察事实，不静默丢弃工资/消费；最小 runner 在无未解决 UNKNOWN 的阶段驱动。接受任意外部实时交错需要后续明确银行排序/预留侵入协议，不能通过 reset 消除 UNKNOWN。

## SQL 保护的最小范围

新事实 INSERT 检查 owner/协议/类型/严格标量/时间/请求与结果 hash、合法状态；UPDATE trigger 拒绝原身份、金额、时间、request 的改变。bank SETTLED/REJECTED 的原银行结果永久固定；projection PROJECTED 的结果永久固定。允许的状态迁移和第一次投影填充必须用明确列集合检查，不能 `exclude all mutable JSON`。银行结果固定后仍允许第一次应用投影，故不能照抄 BankOperation 的全行终态锁。

建议 deferred constraint trigger 在 external fact/其新腿事务结束时验证 SETTLED 的精确双 ECONOMIC 腿和完整备查腿、delta 守恒、owner、原点、前序/seq/余额/时间；能以 SQL 列与固定 protocol 计算的规则应直接核验。完整全 ledger 和源/曝光证明仍由金融/纯域 verifier 核验，不能声称 SQL 已复现整个来源引擎。测试须分别证明直接普通 DML 被 SQL 拦下，及 SQL 未覆盖的改动被只读核验报错。

不削弱现 posting UPDATE trigger，audit events/subjects/epoch/head 的 UPDATE/DELETE/TRUNCATE 保护不加 GUC 豁免；数据库 owner 的禁 trigger/DDL 能力仍是明示管理员边界。业务 live 表的受限 demo 清除必须先在原独占 gate 内完整封存旧 epoch；物理永久副本保留原银行事实。不能把业务表现有 DELETE 能力等同于能安全清掉未知经济结果。

## 完整来源、曝光版本与水位

建议显式 `asset-exposure-v4`，固定清单加入所有 external facts 的原 identity/request/bank result/projection result，以及 posting.external_fact_id。v1/v2/v3 的原清单/算法保持可核；库存在新外部事实时，旧 v3 不能因为不认识该表而宣称当前 complete。新后继 proof 捕获完整集合，不重写旧 exposure body/hash。

`asset_exposure_import` 的来源 epoch 水位必须加入 external 的 occurred/observed/accepted/settled/projected 时间，以及新原结果实际引用的交易/证明；继续覆盖所有现 bank request/posting/operation/claims。任何有关行发生在 proof epoch 之后，旧完整证明不可继续用于当前金融判断。来源加载、bank projection/unprojected、income location 集合都要识别同一个新协议，不能只更新 hash builder。

银行已 SETTLED 尚未投影时，同 RR 读取能展示实际 pending 状态和账面余额，但可自主额度等依赖卡片必须暂不可证明；不得从旧 Account + 新 legs 拼出 READY。303/304 老历史解释保留原副本与时点，正常 source status/SUPERSEDED 后继只附注；原内容/hash/关联篡改仍报完整性错误。

## 0006 原审计与快照兼容风险

当前 0006 SQL 的 subject kind→实体表映射、snapshot_version=1 限定，domain SUBJECT_KINDS/事件 anchor registry 和 storage SUBJECT_MODELS 都明确封闭；加新表后不能仅扩 Python dictionary。后续 migration 必须同步 SQL insert 验原 owner/table、纯域原件/完整腿解码、typed 同 UUID 不同 kind、OPEN current original missing 检查、SEALED 仅永久副本核验和读前资源预算。新 external subject/current 缺失应是错误，不能略过。

推荐保留现审计 envelope/canonical 算法的既有解析能力，显式注册新的外部事实事件和 payload/protocol；external 采用自身 v1，新增形状的 BANK_POSTING snapshot 采用显式 v2，v1 旧副本继续支持。需要 domain 与 SQL 同时允许指定 kind/version 的规则，不能把所有未知版本泛化 VALID。事件锚绑定 external 原件和完整 posting set，不冒用要求 BankCommand 的 EXECUTION_REQUEST/BANK_SETTLED 解码。未知新能力按 UNSUPPORTED，已知原件篡改先报完整性错误。

DDL 后 `row_copy` 会包含 `external_fact_id:null`，但旧 BANK_POSTING v1 canonical text 没有这个键。当前 `_immutable(original,current,original.data)` 按**原副本字段集合**核验，静态上允许新增字段而不重哈希旧原文；实施须另校验旧 v1 所对应 live 行新原点必须 NULL，防止把旧腿悄悄改归新事实。不得为求新形状一致重写旧 subject/event/seal/checkpoint 文本。旧 hash、manifest 和 checkpoint 保持逐字节相等；新副本按新版本另存，不能替换已有版本。

SEALED 完整归档必须同时核新外部事实原件/经济腿/投影及全 ledger opening/前序/余额，不因 live reset 删除而调用当前 BankCommand 解码。现 `verify_frozen_ledgers` 可复用连续性基础，但新外部完整腿、受信清算初始资金与 income 发行需新增明确原协议核验，不能只证明余额代数便声称经济来源正确。

## reset 与清单边界

当前 SUBJECT_MODELS 是明确 **19 种业务实体**，同时用于 reset archive 和 seed summary；其中 USER/catalog 的 scope 不同，不能用 Base 全表递归封存旧 audit。新 external 表需扩**业务归档 allowlist**，逐实体原文/version/hash 全部归档，seal manifest 按实际完整索引及 counts 绑定。当前 gate 下 old seal、live 清除、新 epoch、固定 seed/open bank仍全事务原子；任何失败回滚全部关系和当前 head，不丢已归档原件或另留半个新 epoch。

建议将归档 allowlist 与固定 financial seed summary allowlist 明确分开：保留 `SEED_VERSION=mvp-301-v6` 及既有 v2 的 19 表固定财务 dataset 口径，新增 external 的实际 counts/元数据独立报告并声明不在旧 summary 范围；新的 archive 是包含 external 的 20 种业务原件口径。若最终产品要把新事实纳入 seed dataset，须显式 summary v3，而不能偷偷改变 v2。所有审计表仍排除于 financial dataset；保留 epoch 的增长不是财务 seed 不确定。

清除依赖：先完整 seal，随后同 demo user 清 postings，再清 external facts，最后其 account；永久 User/audit epochs/events/snapshots 保留。原 action/request/trace FK 删除图保持现顺序。external 的可空 live links 如形成依赖必须只对本 demo user 在原事务清空，其他 user 不碰。reset 不能作为修复未知银行真值的捷径：若完整核验不能证明可封存，拒绝 reset。已知银行 SETTLED + projection UNKNOWN 也必须完整保留其原银行/待投影状态于 seal，不能伪装已到账投影。

## 实施后必须取得的真实证据（全部未运行）

只在 `temporary_database/require_test_database` 生成的随机 `bf_test_<32hex>` 中开展真实 PG RED→GREEN；正式 migration 由 root 在源码冻结、完整 checks 和历史保留证据完成后执行，正式 seed/reset 不属于此设计。

| 验收组 | 必须保留的实际 oracle |
| --- | --- |
| 0006→0007 历史无改写 | 先以真实 0006 准备现五类命令/205、OPEN 和 SEALED epochs/checkpoint；升级前后旧业务列/UUID/request/content/hash、原 posting/opening、audit canonical bytes/hash/seal/checkpoint 完全相等；新增列仅 NULL。旧 API/verify/PREFIX/EXACT 按原合同仍真核验，不能因新列误报 |
| 新原点 SQL/tenant | 双原点、无原点、跨用户 fact/account/previous、重复 ref/key/leg/seq、非法方向/金额/清算腿、原件 UPDATE 与 terminal result 改写实际被拒绝；更换 key 不产生第二效果。普通 DML 与管理员 DDL 边界分别记录 |
| 完整工资/消费链 | 固定 literal S/C，银行现金与清算账对腿 sum0、非负、全链连续；旧 opening不变；actual INCOME/CONSUMPTION transaction/proof、余额/覆盖/曝光/income 完整后继；来源/备查不重复加钱。接原 purchase/目标/返本，证明异构原点确实同一现金链 |
| 新工资来源/消费位置 | origin实际金额S绑定新银行事实，首次 AVAILABLE发行精确；消费 AVAILABLE→SPENT规则可核；旧origin/reserved/assigned不改认归属。存在未支持侵入/迟到/清算不足时保留明确失败，不假 PASS |
| 真两事务 UNKNOWN | 在银行真实提交后让实际投影 INSERT失败；银行原件/腿/audit保留、应用投影整体回滚。GET全表零写；解除故障同键重试仅一份transaction/origin/result、经济腿不新增；再次重放按明确合同全表相等 |
| 顺序/完整性/预算 | command pending/settled-unprojected与external交错的实际 gate/order；断前序/缺腿/多腿/旧完整proof水位/已捕获新原件被删报错。装载前计数/bytes预算，超限 INCOMPLETE，不部分 VALID |
| reset 保留/隔离/回滚 | actual external→command→external 三阶段后 reset 两次；旧外部/银行/审计原文在永久归档可完整核验，新 live 财务dataset稳定，epoch增长真实；另一user逐行不变；seal后任一实际注入失败全事务回滚。command bank→projection间 reset确实等待共享 lifetime gate |
| 降级安全 | 空新协议库可在随机库降回0006并恢复原 constraints/函数；存在 external 原件/腿/清算opening/v2或新事件的 live **或 retained audit history** 时明确拒绝降级，不能删表丢原件或将新腿置NULL冒旧协议。正式库不做降级测试 |
| 真实首页与 Edge | runner隔离DB/时钟，经实际银行服务/公开原五类动作到真实API/Web/Edge；工资前后、消费风险、恢复、T1、ASK、UNKNOWN阶段逐步留网络身份/截图/全表与腿摘要。不能用source_ledger收入位置、新工资账户、手改余额/旧proof、route.fulfill或404控制台替代 |

上述是实现前的验收要求，不包含任何已通过数量。唯一新增产物为本设计文档，全部应用/测试/合同保持冻结。
