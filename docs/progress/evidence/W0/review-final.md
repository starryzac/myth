# W0 最终安全边界复核

复核时间：2026-10-05 06:59:47 +08:00。复核方式：只读源码、工作树差异、原始日志和源码 SHA-256；本次复核未修改源码、未启动或重跑 PostgreSQL 测试。HEAD 为 `4ccf84e973978482a1098d18c69fbfc9f011fac6`，实际对象是保留后续成果的当前工作树。

结论：此前发现的同请求 JSONB 原地修改漏检已补上完整 posting 原件摘要检查，独立负例先 RED、修复后通过。当前限定优化路径未发现新增的跨请求授权缓存、历史哈希改写或资金权限放宽。结论限于本文件列出的源码和定向证据，不表示 W0 性能验收、MVP 总验收或任何 FULL 编号关闭。

## 1. 账本复用的实际边界

`historical_read.py` 的作用域只在无 `Session.new/dirty/deleted` 且数据库真实事务设置为 REPEATABLE READ 或 SERIALIZABLE、READ ONLY 时建立。普通 READ COMMITTED 或可写事务继续运行原完整 `ledger_heads`。

成功集合和原件摘要保存在一次 `historical_ledger_scope` 的 ContextVar 对象中，不写入 Session.info、ORM 行、审计 head 或全局结果缓存。Session、顶层事务或当前 savepoint 身份变化，以及新建、脏或删除对象，都会清空成功集合和原件摘要；正常返回或异常退出均通过 `finally` 恢复 ContextVar。作用域结束后，同一尚未结束的事务也不能继续复用该作用域结果。

每个用户的首次调用仍从实际 opening 开始检查完整 posting 集合。`simulated_bank.ledger_heads` 保留原来的数量预算、原件布局、来源、opening、前驱、顺序、时间、账本维度/metadata/账户/头寸身份以及守恒检查。新增回调仅在每行通过这些原检查后记录摘要；整次调用成功且所有原件可编码后才登记可复用成功，失败的部分集合不能变成成功缓存。

新摘要覆盖 `POSTING_V2_FIELDS` 的全部 live 字段，包括深层 JSON metadata、前驱、经济来源及所有金额/时钟字段。摘要是调用内检查材料，未写回正式 posting、审计原件或任何历史 hash。缓存返回前再次检查 Session.identity_map 中已加载的 posting；未知 posting 或完整摘要不匹配使该用户结果失效并恢复完整核验。匹配条件同时包含“原集合已知 ID”和“当前 user_id”，避免已知原件改绑 owner 后仅靠当前租户过滤逃过检查。

这补上了 plain JSONB dict 原地修改不会进入 `Session.dirty` 的缺口。没有再仅凭 dirty 标记、末端 posting 或 ledger head 判定所有原件未变。已从 Session 脱离的对象不作为缓存原件或后续账本读取来源保存；数据库在该 READ ONLY 稳定快照内的可见行仍由首轮完整核验覆盖。

## 2. 审计批量读取、原件与 OPEN/SEALED

`audit_chain.py` 仅在 OPEN 且稳定只读快照中按主体种类、每批最多 500 个 ID 读取当前行，替代重复 `session.get`。可写或含未刷新修改的调用保持原 identity-map 读取路径。

批量读取后仍逐条解析原 canonical_text，并核对 snapshot hash、租户、epoch、实体身份、种类、scope 和 snapshot_version。当前行继续经 `row_copy`、注册 posting codec 和 `domain.build_subject`；TENANT 所有权、USER 自身身份、GLOBAL_CATALOG 仅限全局 ASSET_PRODUCT，以及原件金额/协议验证仍执行。当前不可变原件缺失的 `CURRENT_ORIGINAL_MISSING` 诊断和既有应拒绝的主体集合保留。

SEALED 路径不进入当前行批量读取或 OPEN 的轨迹/账本复用循环，仍核验前序封口、当前 seal、原 head、真实 archive manifest digest 和记录数。没有把 SEALED 历史改为当前余额校验，也没有重建旧 canonical_text/hash。预算检查仍先于加载/解析原文本，完整性失败及 INCOMPLETE 均不升级为成功。

## 3. 引用规范化与两个回执路径

`decision_trace.py::_ReferenceReads` 在一次 OPEN 审计调用内创建。该调用按审计用户查询 DecisionRun，`_stored_trace` 继续验证 trace 与 run 的 user/run/parent/action 身份；证据和精确 PolicyVersion 查询均有租户条件，策略状态取自该精确版本所属的 Policy。

`evidence_copy`/`policy_copy` 仍生成经过合同校验、深拷贝 JSON 的完整原件副本，内容比较只排除生命周期状态。原始引用顺序、`MISSING`/`UNCHANGED`/`STATUS_CHANGED`、`allow_missing=False` 的拒绝和内容篡改错误保留。公开普通调用未传 `_reads` 时保持原逐项路径。当前生产调用没有把这个私有对象移出审计函数，或用于授权判定。

约束投影只把同一行重复的 `_constraint_copy` 减为一次，保留原集合、字段和相等比较。`execution_projection` 与 `recovery_receipt_integrity` 只把重复全账本核验接到上述有条件作用域；动作、银行操作、完整腿集合、回执金额、原请求、原时钟和交易证据的独立验证继续执行。实际写入调用没有稳定只读作用域，因此不会复用之前读取的授权或账本成功结果。

## 4. 已核对的真实证据

以下记录由根代理执行，本复核读取原日志及 manifest，没有重新执行：

| 证据 | 实际结果与范围 |
| --- | --- |
| [最终定向 PG 日志](verification-pg-repaired/pytest.log)、[manifest](verification-pg-repaired/manifest.json) | 20 passed，exit 0，pytest 680.07 秒；包括七项新作用域/篡改节点、只读事务、T1/UNKNOWN、两条回执篡改路径、OPEN 缺失/SEALED 保持、预算拒绝及原资金 HTTP 准备→确认→执行→回执。 |
| [JSONB 缺口原 RED](verification-red/02.log)、[RED manifest](verification-red/manifest.json) | 原地修改负例先得到 `DID NOT RAISE PolicyLifecycleError`，exit 1；修复后同一断言进入上述最终 20 项并通过。原失败日志保留。 |
| [第一次定向 PG](verification-pg/pytest.log) | 15 passed、1 failed；T1 准备因旧夹具/默认 native 初态不匹配被拒绝，原 409 和断言保留。 |
| [冻结原源码重现](verification-red-baseline-correctpath/pytest.log)、[manifest](verification-red-baseline-correctpath/manifest.json) | 同一 T1 节点在冻结原源码上仍得到 INCOMPLETE_EXECUTION_FACTS，exit 1，证明该准备失败先于 W0 优化。 |
| [先前快检查纯域日志](verification-fast/05.log) | 72 passed；范围为当次 manifest 指定的审计编码/域及编排/工具测试，不能代替最终 PG 或全量验收。 |

最终 PG 对七项新作用域测试的证明包括：同一 RR/READ ONLY 作用域两次核验仅运行一次；新作用域和作用域外调用重新核验；READ COMMITTED/可写事务不复用；new/dirty/deleted 不刷新也会失效；其他 Session、顶层事务变化、savepoint 变化及异常退出不泄漏成功；全部 ledger heads 和审计 head 不变时，下个请求仍拒绝非末端 posting 改造并保持 409 零修复；同请求未标脏的 JSONB 非末端原地修改同样被拒绝。

`verification-red/02.log` 和最终 PG 日志的当前 SHA-256 均与对应 manifest 相符。以下复核对象的当前 SHA-256 均与最终 PG 的 `source_before` 一致：

| 文件（相对仓库） | SHA-256 |
| --- | --- |
| apps/api/app/services/historical_read.py | 1c4aab98ff41e6f1374fbb7056bcff5339562675fb62d7040485ca88152779ca |
| apps/api/app/services/simulated_bank.py | a4ed0593f1447938102469d02665877155ae90bfb1719967262214c3d83f9aba |
| apps/api/app/services/audit_chain.py | fcc4877ad89df6063aef94336580a9a63cc19fbe499473a33e6d4a3c9778d25a |
| apps/api/app/services/decision_trace.py | 5306cf81a02a668825647e39d80a21564612eed79e05d3a142b057ad61cff40c |
| apps/api/app/services/execution_projection.py | adb8395f54aaeec8c2a10bbf16c6c2d7ff9ddf68b20491146acaa8610af6ae8e |
| apps/api/app/services/recovery_receipt_integrity.py | 9df2a1d2cb08a98260cef8e455e73b82c8602cea4e0fb4d5e164ee825e4c8290 |
| apps/api/app/tests/test_historical_read.py | e2438a5738b82958a3c8a1679c1ba566e733364603148497e9a8e5a329cdd5b0 |

最终 PG manifest 记录测试过程中两个实验/选择器测试文件变化。这不是完整工作树冻结证明；上述六个生产文件及新账本边界测试单独匹配，故本复核只将对应风险节点视作源绑定的定向证据。

## 5. 原负例及未验证项

现有测试差异中，T1 历史节点仅增加显式 `goal_client=legacy-income` 参数，没有删除或减弱其 ACCEPTED、无回执、流动性风险、撤权后不结算、原轨迹/解释不变及全物理表零写断言。账本、执行/恢复回执、审计缺失和预算篡改负例继续保留；新增 JSONB RED 未替换成伪成功。此处没有 FULL 关闭、历史证据覆盖、正式模拟库重置或真实资金接口启用。

复核时[当前三个固定场景性能 manifest](performance-20261004T225248Z-a5290990/manifest.json) 仍为 `INCOMPLETE`，尚不能宣称三个场景全部实测完成，也不能给出加速比例或延迟目标达成结论。先前 [BASELINE_FAILED 记录](performance-20261004T222631Z-7382acf6/manifest.json) 保持独立。

本次未验证：SERIALIZABLE 分支的独立运行节点；对所有字段、所有租户组合的穷举故障注入；完整历史负例全套；全部既有 recovery_fixture 消费者；真实浏览器、本地长期并发/资源上限及初版/完整版全量验收；真人研究与真实用户效果。回调失败不登记成功和 owner 重绑定摘要检查已做源码复核，未另增专用重 PG 节点。本报告不据代码存在、旧文件复用或 20 项定向通过关闭任何未覆盖需求。

## 6. 2026-10-05 收尾前独立复核

再次只读比对 [closure 源清单](closure/manifest.json)：293 份源码的当前与归档字节、六份辅助脚本的当前与副本 SHA 全部相符；197 个受保护初始路径与 16 个 MVP-404 文件哈希相符。旧 tracked MVP-301—403 证据及 domain/db/alembic 的 Git diff 为空。最终 PG 日志仍确为 20 passed，SHA `f2e8bc1fd1e6eedc064053d9eb61015b32f63b685f4063ba7b02b51e46ea5f32` 与 manifest 相符；金融源码、历史边界测试及所选负例仍匹配通过时版本。旧 untracked 证据没有覆盖全部文件的初始字节清单，此限制保留，不提升为全历史逐字节证明。

此次性能状态以 [修复夹具的新清单](performance-fixture-repair-20261004T232505Z-5daa2f9b/manifest.json) 为准：三个基线 PASS，短/长显式复用原 PASS，扩大另行新测；候选仍运行。上节旧性能状态是当时快照，不改原文冒充最终状态。此复核没有编辑生产源码、运行 PG 测试或执行清理；W0 仍等待性能、formal-final、cleanup 终态。

## 7. 实际收尾结果（2026-10-05 09:01 +08:00）

root session82682已exit0，三个基线/三个候选PASS，新清单SAME_DATA_COMPARISON_PASSED。正式库收尾23表408行、逐表/整体摘要、审计零与0007均未变，LEGACY_UNAUDITED保留。四个自建性能库按已复核清单清理PASS，原运行manifest不变。[最终绑定](closure-final/manifest.json)实际再核293源、6工具和16个MVP-404文件，六条报告entry逐一匹配本次结果路径/SHA/源码指纹/数据摘要；每场景API/服务及基线/候选四份响应相等。

收益混合和峰值增加见[实测表](performance-results.md)，不提升为稳定性能或全量验收。本次收尾只扩展证据绑定和进度记录，没有修改金融源码或原失败记录；原20/92与67FULL PENDING保持。原未验证项与旧untracked初始字节覆盖限制不变。
