# MVP-401 审计请求内复用候选

`DESIGN_ONLY / STATIC_REVIEW_ONLY / NOT_PROFILED / NOT_IMPLEMENTED / NOT_VERIFIED`

2026-10-04。此文件仅在 ignored `.runtime` 中记录当前源码的静态调用分析。没有运行 pytest、数据库查询、资金操作或 CLI，没有修改任何生产代码或测试。未来 root 的真实 Edge 必要验收将分别记录建库、迁移、seed、资金和 GET 阶段，并以标准 cProfile 定位函数成本；该 instrumented 结果不构成产品延迟 SLA，也不代替业务验收。

## 已确认的实际路径

新事件 `services/audit_chain.py:append_audit_event` 不逐次执行 Python 全历史核验。只有已有幂等事件的重放分支调用 `verify_audit_chain`；`audit_recording.py:_record` 的已有业务事实分支也做全链核验。新事件仍有完整原件捕获、模型/编码、幂等查找、SQL 触发器和 head 更新成本。不能把全部资金阶段时间归因于全链扫描。

`services/audit_chain.py:verify_audit_chain` 每次按所选 user/epoch 做预算统计、加载全部 subject/event 原文、核投影与原文一致，再调用纯域 `verify_epoch`。OPEN 按 `(kind,id)` 去重读取当前对象，并按 run id 去重进行 303 原轨迹、来源、约束、动作和回执校验；SEALED 使用原封存副本与 manifest，不借新轮次的同 UUID live row。

`dashboard_helpers.py:current_epoch_audit` 已经做到首页一次整轮核验，再用一次锚查询绑定每个选中 run；`dashboard.py:get_dashboard` 不为每张卡调用 `get_decision_audit_status`。应保留此接缝，不能提出一个现已实现的“每卡改成只验一次”作为新优化。

`decision_trace.py:get_decision_trace` 先验请求 run 的 `_stored_trace`、`_current_references` 和 `_action_links`，最后调用 `get_decision_audit_status`。若该 run 在当前 OPEN epoch 有实际 `DECISION_RECORDED` 锚，后者重验整轮，其中包含本 run 的相同工作。没有锚仍须返回 `LEGACY_UNAUDITED`，不能继承其他 run 的 VALID。该路径不是递归调用 GET。

## 静态可见重复及其边界

以下计数是从正常、支持版本路径展开所得的调用下界，不是执行计数、耗时或速度预测；含嵌套业务校验的实际次数可能更多。cProfile 才能确定其在本次必要 fixture 中的成本占比。

| 接缝 | 当前重复 | 最小候选及必须保留的检查 |
| --- | --- | --- |
| `domain/audit_chain.py:parse_subject → subject_canonical_text/subject_hash → verify_epoch/_subject_index` | service 的 parse 含两次 `build_subject` 和 canonical 原文检查，再作 `subject_hash`；域预算再编码，index 再 build/hash。正常原 subject 在这条单次 service verify 路径至少 5 次 build、9 次完整 canonical 遍历 | 内部一次生成 validated subject、精确 canonical bytes/text、namespaced digest、byte length；在同次 verifier 中复用。仍核 stored hash、索引列、tenant/epoch、scope/version、重复版本、当前原件与所有 references |
| `parse_event → event_canonical_text → verify_epoch` | parse 先核 known outer hash，再 `verify_event`，canonical text 又 `verify_event`；service 已解析 DTO 传给域，域还核 outer/hash/content 和预算。正常 event 此组合路径至少 10 次 canonical 遍历 | 内部一次完成严格解析、outer/hash、结构/content 和精确原文比对，再复用同一 validated envelope。域公共入口处理任意 DTO/raw 的强校验保持；顺序、cause、head、重复、引用语义仍逐 event 检查 |
| `_references → _immutable` | 同 source 原件可被多个 phase/event 引用；同一 event 的 EVIDENCE ref 与 EVIDENCE_CONTENT anchor 又各核 content 等字段。每个字段对两侧作 canonical 编码，巨大 content/input_snapshot 会再次完整遍历 | 同次 verifier 按精确 original 版本、精确 current 版本及字段集合复用不可变字段比较结果。每个 ref 的 kind/id/role/scope/hash/version 与 anchor 本身仍单独核验；不能把合法 status 变化当成全文相等要求 |
| `_references` 中 action/fact 筛选 | BANK_POSTING_SET/ACTION_PROJECTED 的 `any(...)` 中多次 `subject_hash(s)`；external anchors 又重复 hash 同一 fact | 从已经验证的 typed subject index 复用 digest。保持同 UUID 不同 kind 的索引、AFTER/BASIS 选择及唯一性，不只按 UUID 或声明 hash 查缓存 |
| `verify_frozen_projection` | 先 `verify_frozen_settlement`，再 `_financial_identity` 解析同一 operation/action；两事件的 BANK_SETTLED/ACTION_PROJECTED 也核相同银行事实 | 先考虑本函数内部共享已核 economic identity/result；公开 pure verifier 的所有输入仍独立验证。跨 event 复用必须包括实际 operation/action/posting 原版本与 observed clock |
| `_external_references` / `verify_external_projection` | PROJECTED 外层先 `verify_external_settlement`，内部 projection 又验同一 fact/两经济腿，随后再次解析 original request。fact `build_subject` 与 binding 校验也会解析原协议 | 先考虑同一 external projection 调用内部共享已核 settlement/request；不是让调用者用布尔 `already_verified` 跳过校验。经济结果与 memo 结果分离，两腿 exact set、19 字段 codec、真实 occurred/projected clock 和首键协议均保持 |
| OPEN 的各 run `_action_links` | 四个 phase 的 run id 不同，动作/operation/receipt 原件可相同；每次 receipt verifier 查完整真实 legs，`ledger_heads` 再核该用户全部 postings | 一次稳定只读 verifier 复用完整已核 ledger 视图；每个 run 对自身原 effect/request 的绑定仍独立检查，每个 receipt 的实际集合、金额、费用/损失、时间、transaction/evidence 仍独立检查 |
| OPEN 当前对象和 run 来源读取 | 当前对象已按 kind/id 去重，但逐个 `session.get`；每 run `_current_references` 显式 evidence SELECT、每 policy 两个 SELECT；run/parent/constraints/actions/operation/receipts 仍逐项查询 | 若 profile 显示 SQL 等待显著，再按 kind/owner 分批预取，并传私有完整 lookup。显式 SELECT 不因 ORM identity map 已有对象就自动取消执行；不可把“去掉 SELECT”与“已核验内容”混为一谈 |

`decision_trace.py:_verify_constraints` 当前字典推导对每行调用 `_constraint_copy` 两次；这是非常小且确定的本地重复，可在 profile 显示相关模型构造成本后改成每行一次。它不是主要瓶颈已被证明的结论。

## 允许考虑的最小优先顺序

1. **一次完整核验中的 canonical/subject digest 结果复用**：优先在纯域私有 pipeline 消除相同原件的重复 build/canonical，先不改变 SQL、公开协议和 service 接口。原 bytes、digest、解析/异常顺序必须等价。
2. **同一个函数内部共享 settlement/financial identity**：如 profile 证明 external/command projection 的二次验证占比显著，只重构私有函数的中间结果，不改变公开 verifier 接收任意原件的责任。
3. **同一 RR/RO 核验中的 ledger/source 预取**：只有实测 SQL 或重复 ledger 校验占比明显才扩大到 service lookup/context。首页已是一次 full verify，不另造全局缓存。
4. **decision GET 与当前 epoch verifier 共享请求局部上下文**：可复用已经检查的原 run/constraints/receipt 资料，但整轮结果不能代替该 run 实际锚查询。此项改动面更大，先等 GET profile。

不先实现持久化 verified checkpoint、跨请求 LRU、head-only 缓存、减少原副本或增大预算；这些改变有更大的篡改与漏验风险。最小 cause SQL `LIMIT 1` 可另作为独立语义等价维护候选；当前需求聚焦已测 CPU/读取重复，不把它混入未实测优化组合。

## 请求内缓存的安全合同草案

建议只是私有 `VerificationContext`/局部 dictionaries，不是公开 caller-controlled `trusted=True`。公开 `parse_subject`、`subject_hash`、`verify_epoch`、receipt/external verifier 继续拒绝未核输入；内部复用对象只能由强校验路径创建，不能用 `model_construct` 绕过。

- 生命周期限一次顶层 verifier，或一次明确的干净 RR/RO 请求；退出后丢弃。不能按 Session 长期保存，因为同 Session 可跨事务、独立 bank/application 阶段、savepoint 和 reset。
- 原件 key 至少绑定 user、epoch、kind、id、snapshot/payload/canonical version 和**实际精确 canonical 原文**。声明 hash 只作被验证字段，不单独当信任 key；相同声明 hash 但正文/索引不同仍必须失败。
- current copy 另绑定同一快照中实际完整列值及 codec version；不能只用原 event tail/head hash，因为当前 evidence、policy、constraint、receipt 或 BANK_POSTING 内容可在 head 不变时被篡改。
- Pydantic frozen DTO 的 nested dict/list 不是深不可变。只缓存私有拥有的 detached 值/原文或不可变结果，不能因 Python 对象地址相同便复用；不向 caller 暴露可写 cache DTO。
- 每个原件第一次仍做 duplicate-key/nonfinite/strict cents/大小深度/协议/owner/epoch/索引/原文/hash 校验。预算使用精确 validated bytes 长度；current copies 同样计原预算，缓存不会扩大允许集合或把 INCOMPLETE 变 VALID。
- 只缓存确定的纯原件结果，不缓存 financial VALID/UNKNOWN/ASK、clock-sensitive receipt 判断或完整 whole-epoch status。不同 `now`、checkpoint/mode、selected epoch、未知算法必须重新按真实选项核验。
- 任何 mutation/flush/expire/rollback/savepoint/commit、独立银行结算、源 proof successor、epoch seal/reset/切换后，原上下文不可复用。最小实现应优先只在单次纯 verifier 内使用；跨顶层调用的优化要求更强的失效证据。
- SEALED 与 OPEN 保持不同语义；SEALED 不从新 live 表填缺副本，OPEN 已捕获原件当前消失继续报完整性错误，原声明缺失/legacy gap 不掩盖当前错误。
- 不合并资金三段事务，不提前释放 reset gate/User 锁，不跳过 UNKNOWN 原操作恢复和 T+1 等待，不把接收外部银行事实变成 Agent 命令授权。

当前 GET 由 `api/dependencies.py:get_session` 使用 RR；audit 与 dashboard additionally `SET TRANSACTION READ ONLY`。公开 service 的直接调用可能不具有该快照/清洁前提，所以未来不能自动在所有 Session 缓存 current-state 查询结果。

## 实测及最小回归证据

Root 已计划在必要 Edge 验收 fixture 中分阶段计时，并分别 cProfile 资金操作与 GET。避免额外重复 GET；profile 记录真实开始/终止、返回结果和源码绑定。这里只建议读现有 profile 的 `ncalls/tottime/cumtime`：canonical 的 nested `convert`、`build_subject/subject_hash/parse_subject`、`verify_event/_known_event_integrity`、trace 模型/verify、`ledger_heads`、external settlement/projection、SQLAlchemy/driver execute。累计耗时不能相加为总墙钟；cProfile 不完整覆盖其他线程/子进程，instrumentation 本身有成本。

只有实际热点成立才实施单个最小候选。回归沿用户新政策使用模块与直接相关节点，不立即全量。必要证据包括原 canonical/hash exact 等价、v1 18 字段与 v2 19 字段分界、坏原文/重 hash 篡改/索引错配/跨 tenant+epoch/同 UUID 多 kind、合法后续状态、OPEN captured 原件缺失、SEALED 不借新 live row、raw 坏源 BLOCKED、legacy 不掩盖当前错误、完整 receipt+双腿、UNKNOWN/T+1/ASK、PREFIX/EXACT、预算 INCOMPLETE、GET 全表零写入。

若扩大到请求局部 service cache，另需证明同调用输入 mutation 和 flush/savepoint/rollback/事务切换失效；不能只测正常命中。如果候选仅为纯 verifier 局部 token，不额外发明跨请求失效协议。性能改进只报告同必要 fixture 的实际函数计数/阶段墙钟，不宣称产品 SLA 或未经测量的倍数。

## 静态源绑定

以下 SHA256 为本次只读内容，未运行性能实测；正式必要验收如来源改变应以 root 新 manifest 为准。

| 相对路径 | SHA256 |
| --- | --- |
| `apps/api/app/domain/audit_chain.py` | `bd796bd1ede119e2d1381ff0cb18e26aa962ab8e74ee8aed61cc839f4ba05464` |
| `apps/api/app/domain/external_bank_fact.py` | `cedbc5fa9311f0f102799caeb8a48e81b7a0af2be7af14a761214bca4f649b9a` |
| `apps/api/app/services/audit_chain.py` | `fc9119223e3ce19e37ba1b02b8fe518fbb21cbe267cf5c296d15528c20049bae` |
| `apps/api/app/services/audit_recording.py` | `0ec595aa675f8285630d33958b642ebe7856ef7ed5943d99c4581694f3ec050e` |
| `apps/api/app/services/decision_trace.py` | `f1566cbd9fefa4ffa9cb6f3154a5bebce5424246df8554db4c71bef0b8944c47` |
| `apps/api/app/services/dashboard_helpers.py` | `da0adace6d2123a1b8a21c919eebbea879ae7a3076b37cad32e7e78f51cb2f8c` |
| `apps/api/app/services/dashboard.py` | `e0da53dc4232312e95d35a9571e7179badd1d63c9d272ab2e5e31ea1e2f4e823` |
| `apps/api/app/services/execution_projection.py` | `3087f536b33ed99f92bd32f139811a0e0630dcb7ba413df7c0c439b0f64cbd91` |
| `apps/api/app/services/recovery_receipt_integrity.py` | `f6c8c444a6a06e32ec3132f0561781a8d52f7cf58707d09af2e59480c5367946` |
| `apps/api/app/api/dependencies.py` | `76da0ec6a821fc1a47994e51986f2c20e07f775720a40e44418459d5f98b9a64` |
