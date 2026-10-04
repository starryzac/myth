# MVP-304 运行代价只读审查

状态：`STATIC_REVIEW_ONLY / NOT_PROFILED / NOT_OPTIMIZED`。本文件是冻结期的源码成本分析和后续维护候选，不是性能验收，也不是 MVP-401 实现。审查日期：2026-10-04。

本次仅读取冻结源码和已有证据文件，未连接数据库、运行 pytest/coverage、执行资金动作、重置演示数据或修改源码、测试、合同。`MVP-401-preflight.md` 保持不动。统一 MVP-304 full check 在本次审查时仍由 root 执行；它的最终结果和耗时应以 root 的独立运行证据为准。

## 结论与证据边界

当前新事件的 `append_audit_event` **没有逐次调用 Python 全历史核验**。新追加的主要成本是当前事实/幂等查找、引用对象规范化及快照查重、数据库 INSERT 校验，以及 head 更新触发器对事件数的重复统计。Python 全链核验出现在既有幂等事件/业务事实重放、显式 audit verify、reset 前核验，以及已锚定的决策详情状态读取。

一个已锚定的决策详情会先核验被请求 run，再调用 `get_decision_audit_status` 核验整个当前 OPEN epoch。后者包含所有已记录 run 的 303 来源、约束、动作、回执核验；同一动作四个 phase 是四个不同 run，因此银行 posting 全历史校验可能重复发生。只读与零写入不会使这些读取成为常数时间操作。

另有既存的纯域成本：allocation/boundary 属性测试大量生成有效用例，重复执行 `compute_boundary`、配置哈希和模型校验。统一检查使用 coverage，MVP-303 的完整检查在新增 304 审计前就已耗时较长。不能仅凭 full check 静默或总耗时认定审计路径占据全部时间。

这里的“重复”“增长”来自调用结构和 SQL 文本；尚无本次查询计数、执行计划、CPU profile、峰值内存或按函数计时。以下公式是成本模型，不是实测拟合。

## 路径与事务中的实际工作

路径均相对仓库根目录。函数名比行号更适合冻结后维护定位。

| 入口 | 冻结实现的实际工作 | 增长及重要区别 |
| --- | --- | --- |
| `apps/api/app/services/audit_chain.py:append_audit_event` 新事件 | `ensure_audit_epoch` → 幂等键查询 → fact 查询 → `_save_event` 构造、规范化、flush、刷新 head | 不调用 Python `verify_audit_chain`；不能把它描述为每次加载全历史 |
| 同函数既有幂等键 | 解析原事件、比较 intent，再全链核验 | 全链成本随当前 epoch 增长；改变原事实仍拒绝 |
| `apps/api/app/services/audit_recording.py:_record` 新业务事实 | ensure epoch、find fact、逐引用捕获 BEFORE/AFTER/BASIS、副本查重、构造 anchors/changes、找 cause、append | 已做 ensure，append 内还会 ensure；同事务已有锁不会重新产生经济提交，但会有重复查找 |
| `_record` 既有业务事实 | 解析/核原事件，再全链核验；保持原副本/时钟 | 没有重新捕获全部引用；“业务重放先 return”仍包含核验。部分服务真正无状态变化的早返回不进入 `_record`，不能说所有重试都全核验 |
| `capture_audit_subject[_data]` | 校验真实对象/owner，完整列复制，构造 subject、计算 hash、生成 canonical text，再查相同 `(user, epoch, kind, id, hash)` | 相同副本不会重复 INSERT，但规范化/哈希已发生。不同 role 的同对象可再计算；同 kind/id 的不同时刻状态仍需独立副本 |
| `verify_audit_chain` OPEN | 先 SQL 预算统计，再加载整个 epoch 的原事件和原快照；构造 distinct 当前对象集合；域核验；逐 run 做 303 核验 | 当前对象按 `(kind,id)` 去重，run 按 run id 去重；没有按 action/receipt 合并四个 phase 的经济核验 |
| `get_decision_audit_status` | 查原 run、当前 OPEN epoch 和该 run 的记录锚；有锚时调用整个 epoch 的核验 | 无锚返回 `LEGACY_UNAUDITED`，不继承别的 run 的有效性；有锚是全局成本 |
| `decision_trace.py:get_decision_trace/get_action_trace` | 请求 run 的 trace/来源/解释/动作/children 核验，再读取上述实际 audit 状态 | 被请求 run 的内容会在全链阶段再次被核；storage 使用私有只读核验 helper，没有递归调用 GET 的无限循环 |
| `decision_trace.py:list_decision_traces` | 分页查询，逐项 `_stored_trace`、关系与约束核验 | 存在逐项查询/模型计算，但没有逐项调用全 epoch audit status；不能与详情入口混为一谈 |
| `audit_chain.py:list_audit_events/get_audit_head` | 事件页解析，或读取 epoch/head | 事件 limit 为 1–100；不隐式全核验。head 只是保存的 head，完整验证仍需 verify |
| `reset_archive_epoch` | 先全核验；读取 19 类真实对象归档，逐行规范化/副本查重；构造 manifest/封口事件，随后由既有 reset 清理 live projection | 逐行 capture 不是逐行全核验；封存包含当前完整业务对象，不能删减为只留 event hash |
| SEALED verify | 原事件/快照/封口/manifest/冻结 ledger 核验 | 不借用 reset 后的新 live UUID，也不执行当前 OPEN 的 303 来源/回执查询；只读取前一轮 seal 链接，不递归重验所有前轮内容 |

数据库路径在 `apps/api/alembic/versions/0006_audit_chain.py`：

- `audit_event_insert` 核事件列投影、规范文本、摘要、各引用存在性、cause/head 关系；新 subject INSERT 校验原副本结构、摘要、owner/实际对象关系。subject SQL trigger 不是再对全文调用一次 `audit_canonical` 的实现，不能把不存在的递归序列化计入它。
- `audit_event_head` 更新 head，`audit_epoch_guard` 的 head 分支对 `public.audit_events WHERE epoch_id=NEW.id` 执行 `count(*)`，再比对真实尾事件。
- `audit_epoch_complete` 是 `DEFERRABLE INITIALLY DEFERRED FOR EACH ROW` constraint trigger。head 的每次 INSERT/UPDATE 都可排入提交时检查；它再次 count，并找实际 genesis/tail。同事务有多个新事件时可在提交阶段重复检查最终 head。
- 当前事件/subject 的主要复合索引以 `user_id` 开头；上述部分 count/genesis/tail/manifest SQL 只限定 `epoch_id`。源码中未见以 epoch_id 为首列的对应索引。这不等于已经证明 PostgreSQL 会全表扫描；实际计划取决于统计量和数据库版本，需未来 EXPLAIN 证据。

## 业务 wrapper 的引用大小与重复范围

`audit_recording.py:_root_run` 沿真实 parent 解析 correlation；同一 chain 的 phase 通常很浅，但每次 wrapper 都可重复解析。303 `_relations` 的祖先核验有 32 层上界。

| wrapper | 主要捕获内容 | 必须保留的语义 |
| --- | --- | --- |
| `record_decision` | 完整 `DecisionRun`、trace 来源 evidence、policy versions、关联 action/root 的真实副本 | DecisionRun 含 trace、规划输入输出、baseline/projected/reservation 273 点数据，属于大对象；每 phase 的原件必须可单独核验 |
| `record_action_created/record_action_transition` | 最终 request 锚、真实 BEFORE/AFTER action、accounts/产品/策略/position/goal，实际 resource claims | income 增补后的 final request hash；状态变化必须保留真实 before，不用 final row 改旧 status 来代替 |
| `_bank_fact` 接受/结算 | 实际统一 bank operation、request、结算时的真实完整 posting set | 205 legacy redemption 与 301 operation 统一到同一真实 operation；不能产生双经济事件 |
| `record_action_projected` | receipt、真实 posting set、transaction/evidence、action 与相关投影 | posting digest/原件可与结算阶段重复计算；两事件各自的真实经济关联仍需核验 |
| `record_recovery_observed` | root/action 关系、实际观察事实、有限 context | T+1 等待重放可命中已有 fact 后全核验；只读 GET 不结算，UNKNOWN 不因优化而改为成功/失败 |
| policy/goal wrappers | 原策略配置、实际状态前后、真正 invalidation、初始 goal | NOOP 不添加经济事件；原有效配置/人工确认事实不能只留展示文本 |

当前 `_record` 的 cause 查询以 sequence DESC 排序后调用 `.scalars().first()`，**SQL 中没有 `.limit(1)`**。这一调用只消费第一个结果，不会在构造的 SELECT 中自动增加 LIMIT；驱动取回/缓冲多少尚未测量。单个 correlation 较长时，存在取回不必要匹配行的机会。

## 全链核验的 N+1 与重复计算

`audit_chain.py:verify_audit_chain` 先对整个 epoch 统计 count 和 canonical text 的 `octet_length` 总和，未超预算才加载 ORM 行。对每个永久副本执行 parse/hash/索引一致性核验；OPEN 再按 distinct `(kind,id)` 获取当前真实对象并复制。这一去重避免“每个版本都读取同一 live row”，但每个历史版本仍需验证原件。

域层 `apps/api/app/domain/audit_chain.py` 的 `build_subject`、`subject_hash`、`subject_canonical_text`、`parse_subject` 会多次执行结构校验和 canonical 转换。storage 已解析的 event/subject 进入域核验后还会再进行独立校验、摘要与引用比对。嵌套对象拷贝、JSON 编解码、键排序和模型构造都随实际内容规模增长；哈希查重命中不消除这些 CPU/临时内存成本。

OPEN 的每个已锚定 run 还会调用 `decision_trace.py`：

- `_stored_trace`：原 snapshot/hash、trace 模型/摘要、关系、constraint SQL 与复制核验。
- `_current_references`：每 source 一条 evidence SELECT；每 policy version 一条 version SELECT 和一条 Policy SELECT，并核实际原内容。显式 SELECT 不因 identity map 中已有对象而自动省去查询执行。
- `_action_links`：每 run 的 action 查询，每 action 的 operation/receipt 查询，以及真实 request/receipt 经济核验。
- `execution_projection.py:verify_execution_receipt` 的 `_legs` 调用 `simulated_bank.py:ledger_heads`，后者读取该用户**全部 bank postings**，按 ledger 顺序核链。205 的 `recovery_receipt_integrity.py` 也调用同一 helper。

因此，一笔已有 receipt 的 301 动作包含 PREPARE/CONFIRM/RESERVE/BANK_ACCEPT 四个不同 run 时，一次 OPEN verify 可重复对相同银行 ledger 做四次校验；从其中某个 run 的详情入口进入，还会先做该 run 自身的回执检查。数量取决于真实关联动作及 receipt 是否已存在，不能给所有 fixture 固定乘数。

## 可推导的增长与预算

记 `E` 为当前 epoch 事件数，`S` 为副本版本数，`U` 为 distinct 当前对象数，`B` 为存储原规范文本总字节，`K` 为已记录 run 数，`L` 为当前用户 bank posting 数，`H` 为保留的所有 epoch 事件总数。对 run r，再记 source 数 `Rr`、policy 数 `Pr`、action 数 `Ar`、parent 深度 `Dr`。`M` 为一次事务新追加事件数。

一次全核验的粗略成本包含：`B` 的多次原文/模型遍历、`U` 个当前对象读取/转换、每 run 的来源/约束/关系查询，以及每次 receipt 核验对 `L` 的完整 ledger 遍历。查询形态近似“固定统计/加载查询 + 最多 U 次 get + Σr(原 run、Dr 个 parent、约束、Rr 个 source、2Pr 个 policy、actions 和各 receipt 的查询)”。Session identity map 可减少部分 get；不抵消显式逐项 SELECT。manifest 排序另有 `S log S` 形态，事件内引用匹配存在局部列表扫描，复杂度不能只按 E 计。

若同一 epoch 连续打开 N 个详情，每个都重新读取当前 audit status，可能形成 N 次全 epoch 核验。随历史增加持续重放已有 fact/幂等键，也可能累积近似平方级工作量。**这不是“所有 fresh append 都 Python 全核验”的结论。**

fresh append 的另一条增长来自 SQL：每次 head 更新 count 当前事件，再在事务提交的 deferred trigger 重复 count。M 个追加可能产生多次 `Ei` 统计以及 M 次最终 `Efinal` 统计；连续追加 n 个事件的累计扫描工作存在平方级形态。epoch-only SQL 对以 user 开头的索引能否有效筛选未经 EXPLAIN；保留历史 H 增长可能进一步影响计划，但不能直接宣称每次都是 O(H)。

| 预算 | 冻结值 | 解释 |
| --- | --- | --- |
| storage full verify | 10,000 events；20,000 subjects；events+subjects 原文本 64 MiB | 预统计后超限返回 `INCOMPLETE/LIMIT_EXCEEDED`，不加载整套 ORM；统计 SQL 本身仍有成本 |
| pure domain verify 默认上界 | 100,000 events；512 MiB；original/current subjects 各最多 100,000 | 与在线 storage 的较小预算不同，不能拿纯域上界宣称在线能核 512 MiB |
| 单 event / subject | 1 MiB / 16 MiB | 单对象合法不保证 epoch 总预算足够 |
| trace | 10 MiB，depth 32，有限节点/来源/策略/约束 | 多 phase trace 和真实 BEFORE/AFTER 版本都进入副本预算；273 点向量是合法内容，不可为速度省略 |

64 MiB 只是存储原文本预算，内存还包含 ORM 对象、模型、字典、列表和多轮转换，可能是原文的多倍；它不是 64 MiB RSS 上界，更不是延迟 SLA。假设每个 phase trace 接近 10 MiB，四份已接近 40 MiB，再加版本副本和其余原件可能较早触及预算。这是上界示例，**没有测得当前 fixture 达到此规模**。

new append 本身不做全 verify 的累计预算预检；之后的详情/retry/reset 可能返回 INCOMPLETE 或拒绝继续。不能为了通过 reset 绕过完整核验，也不能把预算超限当成 VALID。SEALED 轮次会保留，reset 不清零全局 H。

完整 execute/run_recovery 使用 `audit_command_guard` 覆盖既有独立三段事务；audit CPU/SQL 发生在实际资金事务内，可能增加 User 锁和 shared reset gate 持有时间。这个时间不能通过合并三段经济事务、缩短 outer guard 或提前释放 reset gate 来“优化”。当前 freeze 不作此类更改。

## 已有耗时可以说明什么

| 已有原证据 | 实际结果 | 不能推出的结论 |
| --- | --- | --- |
| [MVP-304-hooks-final-green.txt](evidence/MVP-304-hooks-final-green.txt) | 8 passed，386.77s；定向真实 PG integration，无 coverage | 不是单纯 audit 核验时间或稳态每笔动作延迟；包含 fixture/migration/setup、业务过程、多个核验/断言及 teardown |
| [MVP-304-hooks-reset-final.txt](evidence/MVP-304-hooks-reset-final.txt) | 1 passed，99.56s | 不能把 99.56s 标为 reset 函数独占耗时；该用例还含完整 action、bank、receipt、锁等待证明和 fixture |
| [MVP-304-hooks-core-income-reset.txt](evidence/MVP-304-hooks-core-income-reset.txt) | 2 passed、1 failed，241.93s；原 reset Future 的 60s 测试等待阈值不足 | 不是生产资金缺陷或 reset 一定死锁；最终增加合理观察窗口的独立用例完整 GREEN，原银行提交/receipt/旧 epoch 封口都有实际终态 |
| [MVP-303-check-09-uv.log](evidence/MVP-303-check-09-uv.log) | 304 新增审计前，1229 passed，6381.27s；`pytest --cov` | 不是 304 审计耗时 baseline，更不是与 304 同条件 A/B profile；只证明既有全量检查已有显著运行代价 |

`test_asset_allocation_properties.py`、`test_goal_allocation_properties.py`、`test_boundary_properties.py` 均设置 `max_examples=200, deadline=None`。纯域 allocation 的源码多次重新校验输入、配置和候选，并调用 `compute_boundary`。长时间没有新的 pytest 输出可以处于这些生成/计算段；本报告未测量 coverage 的额外比例，也不把 root 的 CPU 活跃观察变成审计占比证据。

## 后续最小等价候选与风险

以下按预计改动面排序，**均未实施**。未来是否维护应先取得同 fixture 的分项测量并确定对应任务边界，不能在当前冻结验收中直接改。

| 候选 | 等价条件/潜在收益 | 风险与必要验证 |
| --- | --- | --- |
| cause SELECT 增加 SQL `limit(1)` | 保留相同 user/epoch/correlation/action 和 sequence DESC 语义；只取真实最新 cause，避免不需要的结果传输 | 要确认唯一 sequence 的排序、无 cause/跨 action 情形及最终 event canonical 不变 |
| head count/genesis/tail/manifest 查询增加真实 user predicate，或未来迁移补匹配索引 | 利用实际 epoch owner 和已有复合索引；保留真实 COUNT 与尾/首检查 | 数据库所有权、触发器 search_path/SECURITY DEFINER 与非 owner 旁路防护须继续严格；不能用保存的 event_count 代替真实统计。索引收益须 EXPLAIN，不凭源码宣称 |
| 同一次稳定核验按 kind 批量预取当前对象、run sources/policies/constraints/actions/receipts | 降低逐项 SELECT；保持完整相同原件及 owner/role/hash 检查 | 缺失已捕获原件、GLOBAL_CATALOG、重复版本、跨用户和 same UUID different kind 仍须准确报错；必须维持同一只读快照，不能预取后漏读 |
| 一次核验内对精确原规范文本的 parse/canonical/hash 复用 | 避免同不可变副本反复转换；可使用 `(user,epoch,kind,id,hash,exact_text)` 等完整身份 | hash 单独、对象地址或 head-only 缓存不够；nested dict 非深不可变，当前列/正文可被 owner 篡改，不能跨事务长期复用未核原件。所有列投影检查仍保留 |
| 一次稳定核验内只验证一次实际银行 ledger，复用已验证的完整 rows | 减少四 phase 的相同 `ledger_heads` 工作；每 run 的请求/effect 原点和每 receipt 具体 legs/时间/守恒/证据仍核验 | 不能跳过特定 receipt 验证。flush、savepoint rollback、独立 bank 提交、当前 clock/事务变化必须失效；UNKNOWN 投影失败和 205 unified operation 仍需原边界 |
| 原协议不变的一次 canonical 转换内部复用 | 将同值的重复模型/文本/摘要生成合并为经验证的内部结果 | raw/strict JSON、有限浮点、bool cents、大小/深度预算和全部 canonical/hash golden/property 必须等价；不能悄然改变 hash 协议 |

持久化“增量核验 checkpoint”或跨请求全链缓存改动更大，涉及 owner 篡改旧原文、tail 删除、reset/seal、legacy gap 与当前真实投影变化，不能视为当前最小修补。仅缓存 head hash 无法证明旧主体原件或当前金融事实未变。

不接受以性能为由跳过来源/原件/真实经济核验，删除 BEFORE 副本，只保留展示文本，放宽 budget 后宣称已完成核验，屏蔽/xfail 长测试，或改变 UNKNOWN/T+1/人工 ASK 语义。完整三段交易与 demo-reset guard 保持原约束。

## 未来测量与等价维护的验收边界

冻结 full check 完成且维护变更边界确定后，才可在独立临时数据库对相同有效 fixture 收集：分入口耗时、SQL 次数/返回字节、parse/hash/model 调用次数、CPU profile、峰值内存、以及 protected count/cause/manifest 的 EXPLAIN。应分别量化 fresh append、fact replay、GET detail、list/head、OPEN/SEALED verify、reset；不能只记录 suite 总秒数。

任一优化须保留原 event/subject canonical、hash、sequence、真实关系及状态；既有实际原件篡改、已捕获当前原件缺失、同 UUID 不同 kind、合法后续状态、raw 坏来源、legacy 不掩盖当前错误、budget INCOMPLETE、205/301 receipt、UNKNOWN 原操作恢复、readonly 零写入、reset gate/封口、PREFIX/EXACT 和数据库非 owner 防护都应继续通过。缓存还需专门证明 mutation/flush/rollback/跨事务失效。

本次没有新的性能实测，没有运行上述未来检查，也没有百分比或速度倍数结论。当前可交付结论是：优先测量重复 canonical/trace 校验、详情入口全 epoch 核验、逐 run 银行 ledger 核验及 SQL head count；最小 cause LIMIT 与索引/owner predicate 候选需独立等价验证后再实施。

## 19:18 补充：当前完整检查的只读资源采样

上述主体是静态审查。root 后续在原完整 check `20261004T094808Z-2e990d57` 运行期间进行两次低频观察，未运行其它 pytest/coverage，也未修改冻结源码。19:16:09 与 19:18:19 的 `pg_stat_activity` 各观察到一个 `bf_test_*` 连接，状态均为 `idle in transaction / Client / ClientRead`，且测试库 UUID 已变化。这两个瞬间未显示数据库锁等待，不能据此证明整个运行没有等待或确定正在执行的测试名。

第二次原始输出已留存 [resource observation](evidence/MVP-304-live-resource-observation.txt)：Python PID56356 CPU 时间 2906.34375 秒，启动时间 17:49:24；容器瞬时 CPU 0.00%、内存 96.55 MiB。目录中启动环境和唯一 session23914 保持原 run。查询连接使用 `PGOPTIONS=-c default_transaction_read_only=on`，实际返回 `read_only=on`，仅读系统统计，不读取或更改金融行。

这是两个资源采样，不是函数 profile、性能对照、金融请求延迟或完成验收；不能计算审计开销占比。CPU 时间增长与测试库变化支持继续等待同一运行。源码成本候选和全部未来等价检查仍未实施。

## 原计划中的验收归属

2026-10-04 root 核对两份原始计划及追踪表；本节只确定后续实测归属，不完成任何后续任务。

- 初版计划 779–782 行：MVP-401 实现余额分层、下一义务、自主资金及解释入口，三黄金链路状态实时反映。首页须通过实际写后刷新和接口一致性验证，不能保留旧绿色状态或以工程 health E2E 替代。
- 初版计划 789–797 行：MVP-403 决策可视理解；MVP-404 无手工数据库操作连续演示三次。详情全链核验与 reset 成本需在真实演示中观察其可用性。
- 初版计划 833–836 行：MVP-504 断网完整演示三黄金链路；完整版 1448–1450 行及追踪表 FULL-904：四分钟现场版和备用录屏。240 秒是演示总时长，不能改写为某单接口 SLA。
- 完整版计划 1424–1426 行及追踪表 FULL-807：并发、重启、重复请求、UNKNOWN 和大规模性能/故障实验。环境负载、时延、故障点、原始记录与独立效果一致性证明归属该既定任务；当前采样不计 FULL-807。

在上述原计划中未找到毫秒响应阈值或 P95 SLA；决策/恢复时延作为实验指标，仍须真实测量。“实时”与四分钟要求也不能凭 suite 总秒数证明。若实际交互或演示无法满足计划，需要针对具体瓶颈保持协议等价地修复，并重新运行受影响的验证，不能据没有数值 SLA 就宣称体验已达标。

## 本地诊断工具已准备，尚未启用

ignored `.runtime/profile_mvp304_calls.py` 已静态审查并冻结，SHA256 `7e6317acc6ba931a688b4af7a713155b3fb638f57d4918dabf9e653384b603fa`。只在明确指定一个完整既有 nodeID、且本次仅收集该一个节点时启用；未指定节点完全惰性。root 实际核对下述 TRANSFER_INTERNAL 节点在原1300 collection中恰好一次。没有导入/调用插件或开启第二pytest；当前23914不受影响。

每次诊断在当前repo的`.runtime`子目录新建UTC/UUID目录，记录工具hash、真实节点、call起止UTC/墙钟、原始pstats、函数累计/自身耗时及三个测试阶段和session退出码。仅call、本线程被instrument；fixture、其它线程/进程不被profile，cProfile自身有额外代价。分类为`INSTRUMENTED_TEST_CALL_NOT_PRODUCT_SLA`，不计延迟SLA或验收。只有setup/call/teardown齐全且通过、真实session退出0才显示PASSED。

该本地诊断辅助并非发布运行时依赖。当前check真实终态后，若需要定位实际耗时，可以显式调用以下命令；先归档完整check及其coverage，不能把profile统计当新full run或覆盖已有原始证据：

```powershell
$taskProfileNode = 'apps/api/app/tests/test_decision_recording_integration.py::test_all_five_receipt_chains_preserve_frozen_real_preparation_and_fresh_phases[TRANSFER_INTERNAL]'
$env:MVP304_PROFILE_NODE_ID = $taskProfileNode
$env:MVP304_PROFILE_OUTPUT_DIR = '.runtime/mvp304_profiles'
$env:PYTHONPATH = (Join-Path (Get-Location) '.runtime') + [IO.Path]::PathSeparator + $env:PYTHONPATH
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONUTF8 = '1'
uv run --frozen pytest $taskProfileNode -p profile_mvp304_calls --no-cov -rP --durations=1
```

上述为未来调用说明，命令尚未执行，不存在实测profile、优化或速度倍数结论。
