# Subject 编码最小候选

`IGNORED_CANDIDATE` / `NOT_DEPLOYED` / `NOT_BEHAVIOR_VERIFIED` / `INSTRUMENTED_NOT_PRODUCT_SLA`。

精确 diff 为本目录 `candidate.diff`，仅拟改 `apps/api/app/domain/audit_chain.py` 和 `apps/api/app/services/audit_chain.py`。原 bytes 备份为对应 `.py.original`；`metadata.json` 绑定原 source 与候选 hash。当前只完成 AST parse、Ruff check、Ruff format --check，未运行 pytest/应用 import/数据库/新 GET。实际 production 原两个 SHA 未变。

候选把原 `build_subject` 原校验体完整放入 `_validated_subject_bytes`，末尾原规范编码不丢弃，返回 `(subject, bytes)`。公开 `build_subject` 仍每次完整校验；公开 text/hash 每次仍从 caller 当前 DTO 重新验证；`subject_hash` 的 `_declared` 顺序不变。`parse_subject` 保留 strict duplicate-key JSON、协议、JSON-mode UUID/model 校验和完全相同的 canonical-text mismatch 错误，只复用此次完整校验末尾得到的 bytes。没有修改 canonical/预算/codec 原函数。

新 `encode_subject_original(**fields)` 完整验证 fields，然后即时从同一份 bytes 得到文本与 `SHA256(b'bounded-funds/audit-subject-v1\0' + bytes)`。它不接受 caller hash 或 arbitrary trusted bytes，不添加 trusted 参数、不 `model_construct`、不持久缓存；私有 hash-bytes 函数仅被这两个已完整验证的路径调用。service 其他所有权、存在、epoch/索引/重复原文、captured clock、flush 行为保留。

capture 原直接流程有三次 subject 重建、五次完整 subject 编码（另有三次 RawJsonObject 规范化）；候选为一次重建、一次完整 subject 编码（另有一次 RawJsonObject 规范化）。这是代码路径/实际 caller 数证明，不是墙钟节省预测。public parse 原 JSON-mode 校验后的两次 build 改为一次；其初始 JSON-mode 校验保留。

## 冻结旧字节与错误等价

在部署前用仍冻结的旧源建立一次纯域 oracle，绑定该目录 `.original` SHA；保存有限代表输入与旧 `build_subject→text/hash/parse` 输出或异常类型/原 message。不能实施后由新函数给自身生成 expected。

代表输入复用实际已有 pure fixtures：旧 18 字段 BANK_POSTING（live external_fact_id=None 被 codec 删除）；external 19 字段与 origin=None 的 CLEARING opening v2；旧 INCOME_LOCATION zero opening v1；完整 DECISION_RUN raw snapshot/带合法坏 content hash 的 BLOCKED 源；同 UUID 多 kind；含有限 float 的 raw 原件；不同顺序、Unicode、aware UTC/等价时区。必要新增严格检查包括 copied undeclared field 的原 public hash 失败、caller 嵌套 data 原地修改后 public text/hash 必须重新读取（合法新内容改变 hash、非法 money/owner/version 必须失败），以及强制非canonical文本/重复键、深度/字节预算异常与原消息一致。

原件正例比较 exact UTF-8 bytes/text、namespace hash、restored subject 字段；坏例比较原入口异常类型/消息，不只 `raises(ValueError)`。跨 epoch/user 校验分层维持：构建冻结subject本身允许任何一致合法UUID，实际验链/捕获对当前 user/epoch 的拒绝仍由原 service/index 持有，不能写虚假构建失败断言。

部署后先运行直接 pure 模块：

- `apps/api/app/tests/test_audit_chain_domain.py`
- `apps/api/app/tests/test_bank_posting_codec.py`
- `apps/api/app/tests/test_external_bank_fact_domain.py`

这些现模块包含原坏证据、配置当前篡改、平衡但伪造回执、缺经济锚、同 UUID kind、未知协议/算法、before/after、跨action cause、SEALED/归档、预算 INCOMPLETE、18–19字段边界。新增等价 oracle 只补本次共享编码/public入口复验的必要缺口，不能以假DB或改变金额夹具完成金融链。

## 直接相关实际 PG 节点

root 单 runner 在真实链完成且授权部署后串行运行，`--no-cov`，不是每任务全量。最小相关范围：

- `test_audit_workflow_integration.py::test_one_real_transfer_records_four_decisions_and_one_bank_and_receipt_effect`
- `test_audit_workflow_integration.py::test_real_audit_insert_failure_rolls_back_projection_but_preserves_bank_and_unknown`
- `test_audit_workflow_integration.py::test_legacy_t1_has_one_unified_acceptance_and_settlement_and_readonly_wait`
- `test_audit_workflow_integration.py::test_income_augmented_action_anchors_the_actual_final_request_and_executes`
- `test_external_bank_facts.py::test_real_salary_then_consumption_have_two_bank_legs_and_actual_income_conservation`
- `test_external_bank_facts.py::test_original_key_projection_unknown_retry_never_resends_economic_legs`
- `test_audit_storage.py::test_retry_checks_actual_stored_columns_and_never_repairs_them`
- `test_audit_storage.py::test_open_missing_original_fails_but_lifecycle_and_sealed_reset_remain_valid`
- `test_decision_trace_audit.py::test_receipt_links_all_fresh_phases_and_keeps_original_ask_source`
- `test_dashboard_api.py::test_dashboard_shares_current_facts_and_never_writes`

上列均是现有真实节点，文件前缀为 `apps/api/app/tests/`。需要保留原资金/回执/唯一腿/UNKNOWN/T+1/ASK/GET全表零写入断言，无放宽 validators/seed/源金额。

## 实测方案

旧同步 driver 的 `funds-goal_allocated/purchase_prepared/purchased.pstats` caller 图干净，原 wall 为 85.938/27.125/58.313 秒；capture 127/37/100 次分别触发三次 build。部署后用同样固定 now、原 seed/FIFO/相同金额、相同前序阶段的必要既有 scenario，在单一同步进程 profile 同一资金阶段，记录阶段起止、输出、精确原 source/candidate hashes及 canonical/trace/hash等价、所有实际双腿/回执/审计唯一性。测量一个必要 representative（优先完整原 income allocation）即可先判新 build 计数是否符合设计；若仍有真实瓶颈才继续第二优化。

不得把累计项相加或据 worker 已污染 GET 累计量推算收益。现 browser wall 继续作为本轮实际instrumentation证据；未来可靠 GET 函数归因应独立处理 profile 的并发污染，不能把修测量和生产优化混为一个实现。此候选不改纯verify索引缓存、不改变current/SEALED读取策略，避免同时引入第二失效协议。
