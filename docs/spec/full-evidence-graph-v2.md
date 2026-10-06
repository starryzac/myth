# 完整版持久原件证据图 v2

状态：后端函数/路由可调用；主应用注册、真实 PostgreSQL、前端与集中验收由后续批次取得。不是原 FULL-102 关闭证明。

`GET /api/v1/evidence/full-graph/{kind}/{identity}` 返回 `persisted-full-evidence-graph-v2`。仅允许一次可选 `known_at`（aware ISO 时间，不能晚于服务器）；未知/重复查询参数422。当前模拟用户与当前服务器时钟来自现有受信依赖，不能由查询提交 actor、余额、成功或权限。不存在同用户且在知识时点内的根404。服务必须已经处于干净的 REPEATABLE READ / READ ONLY 事务，不自行写入或将可写事务降格为只读证明。

## 图根与实际分母

当前显式登记35张 Base 业务表。USER仅当前模拟用户；PRODUCT/PRODUCT_CATALOGUE明确为共享目录；其余均按实际user_id筛选。`alembic_version`是迁移元数据，不是图实体。新增实际业务表若尚未登记，会返回 `UNREGISTERED_PERSISTED_TABLE` / UNKNOWN，而不是默认为覆盖。

根类型包括 ACCOUNT、TRANSACTION、BILL、GOAL、POSITION、BANK_OPERATION、BANK_REDEMPTION、POSTING、EXTERNAL_FACT、AUDIT_EPOCH、AUDIT_EVENT、AUDIT_SNAPSHOT、FULL_POLICY、FULL_POLICY_VERSION、FULL_POLICY_COMMAND、EVIDENCE、PROPOSAL、POLICY、POLICY_VERSION、DECISION、ACTION、RECEIPT、PRODUCT、PRODUCT_CATALOGUE、USER、CONSTRAINT、RESOURCE_CLAIM、COMMAND_OUTBOX、COMMAND_INBOX、COMMAND_ATTEMPT、INTERVENTION_OUTBOX、INTERVENTION_INBOX、ASSET_PORTFOLIO、ASSET_BATCH、ASSET_CONSENT。

FULL_GOAL_MODEL根是实际 `EvidenceItem.source_type=FULL_GOAL_MODEL_V1` 的别名，identity必须是该证据行ID；不会从旧Goal属性猜完整版模型。

每张表登记 actual_owned_count、known_count、captured_count、complete、captured_rows_hash。后者是新v2 `configuration_hash({"rows":按实际UUID排序的原行数组})`，不是旧审计或银行哈希。原列经现有raw canonical序列化，POSTING特意保留全部实际SQL列，包括旧审计codec为保持v1形状不展示的nullable external_fact_id；旧codec原样保留。

知识时间取原 created/observed/confirmed/completed/updated/accepted/settled/captured/reconciled等实际知悉或发生时间的最大值。valid_from、到期、available_at等将来业务日期不当成今天已知时间。它只决定所读原行是否在知识时点可见，不能将当前可变字段复原为历史。

## 引用与完整性

从实际FK以及明确的 evidence_ids / policy_version_ids / Full确认 / FullGoal模型 / Full版本reference_snapshots字段产生有向边，允许双向导航。不会任意扫描JSON字符串里的UUID，更不会把自然语言声明的success、bank_authority变成关系或授权。用户owner列不把所有图根强制连到一张巨图。

审计事件引用优先连到同epoch、kind、entity_id、snapshot_hash的唯一真实 AuditSubjectSnapshot。归档副本继续存在而当前行已被合法reset删除时，不把归档丢失和当前行不存在混为一谈。原副本typed协议、身份及hash逐一核对；事件canonical原文与实际id、owner、payload/hash比对。副本仅在确有当前实体时增加当前身份链接。

一次请求内 `audit_read_scope` 包住实际 `full_reconciliation` 和原 `get_decision_trace`。完整原审计首次验真保持；节点额外显示证据内容hash、当前账户/持仓与银行余额、Goal产权、真实服务回执/银行腿、完整typed决策、当前Full版本/命令链、当前FullGoal模型、不可变产品登记各自的检查。所有复用只在当前请求上下文，不保存权限或跨请求缓存。

`REFERENCES_RESOLVED`只表示该有限图的已登记引用解析与所执行检查没有已知不足。节点 `NOT_CHECKED/NAVIGATION_ONLY` 必须保持其原义。VERIFIED证据内容hash不证明声明真实；有效原回执不授当前权限；Full规划确认不授银行权限。顶层及节点 grants_authority / execution_authority / financial_success_inferred固定false，服务不修账、不重授权、不改原件。

## 容量与具体未覆盖

- 每普通表10000行、POSTING100000行、总120000行、源原文64MiB；超限保留实际count并明确UNKNOWN，不截断后称完整。
- 连通呈现最多2048节点、20000边；expected_node_count / expected_edge_count是已捕获原件中实际连通数量，不是丢掉未捕获历史后的全库声明。源分母不完整时这些连通数量不能被解释为全部关系分母。
- 每请求最多64关联决策typed验真、32 Full策略验真，超限明确UNKNOWN。产品目录原读取器和完整审计/银行读取器自身原预算与未知门仍保留。
- 原FULLGoal模型须当前原绑定服务验真；旧模型与当前版本不匹配时明确UNKNOWN，不用当前确认修复历史。
- 历史可变节点仅保留身份/知悉时间，原内容及row_hash返回null；本包不重建某过去时点的余额、Goal、策略状态或旧UNKNOWN回执变迁。存留不可变原副本/流水可导航，但不是完整历史世界重建。
- 专用执行proof/quote的全部内部JSON关系、任意用户声明/LLM推测引用及全图经济因果尚未穷尽；无精确字段适配不创建猜测边。只读显示原字段不等于金融验真。
- 正式数据库实测、原回执完整链实际节点、FullGoal实际模型图、跨用户真实负例、前端展示与最终集中验收待补。原graph-v1七类接口、历史原哈希、全部旧篡改测试保持不变。

## 检查与候选

29项直接纯/HTTP风险检查实际PASS2.70s；新增Full确认链接2项与两条相邻路径共4项PASS2.28s，合计31个不同直接节点。6源strict / Ruff / format通过。原首16FAIL/8PASS及41条types错误、修复中13FAIL/11PASS及单条行长失败的日志与对应源码独立保留，未改标成功。

唯一真实PG候选：`app/tests/test_full_evidence_graph_integration.py::test_actual_all_registered_sources_reverse_links_and_bank_tamper_are_read_only`。使用原demo_engine建新隔离库与正式seed_demo，实际确认一个规划InterventionPolicy；由真实GET读Account/反向Evidence/Full策略、全35表分母、原bank/audit验证、未知参数/未来知识/不存在根拒绝及全physical表零写；再保留原账户余额篡改负例并核Graph UNKNOWN/读取零写。仅已collection，未在本包执行。Root需要先注册新router后串行运行。
