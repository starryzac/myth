# FULL-706：原动作与类型化版本精确检索

2026-10-06。`FUNCTIONAL_INCREMENT_IMPLEMENTED / SHARED_REGISTRATION_AND_ACTUAL_PG_PENDING`。原 FULL-706 状态与旧证据不改。本增量补实际检索消费者，旧 `/decisions` 分页、单项八层轨迹、哈希、原失败与正式历史均保持。

## 可运行合同

新增独立 `app.api.v1.full_decision_search.router`，GET `/api/v1/decision-search`。Root 注册 Main 并把该前缀纳入 GET 的 RR/READ ONLY 依赖后可公开调用；独立 FastAPI 纯 HTTP 测试已能实际调用。服务仍自行检查 clean RRRO，未注册时不伪称主应用接线完成。

query 只允许 `action_id` 或 `action_key` 二选一，可与 `policy_version_id`、`epoch_id` 相交；仅 policy version 查询也可。`limit=1..100`、`offset=0..100000`，拒绝重复参数、未知字段、伪 now/user/facts/amount/authority。UUID 采用既有 UUIDReference；HTTP 分页文本先核 ASCII 整数再严格转类型。原键不截断、不模糊、不 trim 改名，URLSearchParams 保留其完整字符。

`search_decisions(session,user_id,query,now)` 仅从实际固定 owner 查找。动作不存在、外 owner、未来创建、未知版本或外 owner/epoch 是 404，不是空成功。原 Action.request 必须与 request_hash 一致。动作范围直接使用 `DecisionRun.subject_action_plan_id` 和 `ActionPlan.decision_run_id`；epoch 仅为实际 `DECISION_RECORDED` 的 owned persisted link，**搜索不将其称为审计链验证**。审计 epoch 的 wall append 时钟不与模拟业务时钟比较。

MVP version 过滤只用原 `_stored_trace` 的 schema/hash/索引/约束/已支持算法重验后，显式 `trace.policies[].id` 与 `trace.constraints[].policy_version_id`。不将任意 JSON 中叫 version/id 的文本、客户端 UUID 或 UUID 字符串相似性当来源关联。`VERIFIED_TYPED_CAPTURE` 只指冻结副本与类型/原索引相符，不是事实真实、当前确认或资金权限；声明内容原 INVALID 可以保留为当时拒绝的依据。

每次新调用 fresh SQL count 和原件，不跨请求缓存。当前决策业务知悉范围核 created_at/as_of/completed_at；元数据显示 actual owned、known、selected、captured、typed verified、unverifiable。动作原 FK 关联和审计录制关联也先独立 count、再集中读取并核完整分母；源容量先查输入/结果/关联请求/审计原文的字节数，避免逐记录重复关系查询。任一集合 >1024 或来源 >64MiB 返回 UNKNOWN/null，不采样后宣称全数。

逐项返回原 run/as_of/status/phase/actionIDs/epochIDs、snapshot/trace hash、原指针、MATCHED 或 UNVERIFIABLE、具体问题；LEGACY_PARTIAL、UNSUPPORTED、损坏原件计入全部 scope，不吞掉它们以输出 0 条成功。旧 trace 的损坏保留 INVALID；它不会得到“已核匹配”计数。`source_hash` 绑定原查询（不含分页）、完整捕获的 DecisionRun/关联行与实际摘要，分页源发生变化则前端拒绝合并。

`SEARCHED` 只意味着当前限定持久决策范围已完成类型化检索。恒定 `absence_is_final=false`、`grants_authority=false`、`financial_success_inferred=false`、`audit_chain_verified=false`、`archived_records_searched=false`。零结果不是没有动作、没有提交、没有资金效果或请求终败的证据。

## Root 精确集成接缝

1. Main 导入 `from app.api.v1 import full_decision_search` 并 `api.include_router(full_decision_search.router)`；旧 decisions 不改。
2. `get_session` 现 GET full_read 前缀追加 `/api/v1/decision-search`，保持 RR/READ ONLY；没有本增量数据库写/迁移。
3. 重生真实 OpenAPI/schema。Response 内包含原 Query 模型；目前新前端显式按实际 DTO 声明，未冒充已生成 schema。
4. 原 DecisionTracePage 列表可插入 `DecisionSearchPanel ownerUserId={actualListOwner}`，建议 key 绑定实际 owner；本包没改宿主页/App/shared hooks。新的结果链接仍去原 `#decisions/{run_id}` 八层视图。

前端只在明确按钮后 GET，无 mount 自动查、无自动重试/POST/确认/执行。改查询立刻隐藏旧结果；不同 owner 响应拒绝；原 JSON 文本完整保存。未知总匹配显示“未知”，原银行/资金状态不从 SUCCEEDED 字符串推断。读入口 `getDecisionSearch(query,owner)`，组件 `DecisionSearchPanel({ownerUserId:string|undefined})`。

## 有限范围与具体未覆盖

- **FULL version filter 尚未适配**：同 owner 原 FullPolicyVersion 存在时返回 `FULL_UNSUPPORTED`、UNKNOWN、总数 null，保 scope 原分母；不存在/他人拒绝。未支持的确切类型家族包括 FullProtectionProjectionInput.policies、actual-v2 冻结全局 FULL versions、FullAssetExecution 原 portfolio/proof、专用 GoalReleaseEffect、FullPaymentRelation 和 FullRecoveryExecution proof。动态 Goal 的 FullModel Evidence 也不能被错误当 FullPolicyVersion。后续须按精确 schema/算法及原 verifier 追加引用，不通用扫 UUID。
- SEALED archive 的原 DecisionRun/ActionPlan 常已删除；本包只查当前物理行，不根据档案标题或旧键猜重建。完整归档检索与旧 partial 轨迹无法复算处仍 gap。
- 搜索不是审计/receipt/银行 verifier 的替代；完整八层和当前金融关系继续由原 GET detail 的 verifier 决定。搜索不提升 audit 状态，也不替代原 Unknown-key recovery。
- 主应用接线、真实 PG、浏览器和最终完整版验收待 Root；本包不关闭 FULL-706。

## 实际定向检查

- `W6/decision-search-exact-key-full-unknown-pure-20261006T012417Z-f9d6ce7e`：27 synthetic domain/service/真实 HTTP 纯风险 PASS，2.02s。真实已持久金融效果不在纯夹具的证据等级。
- `W6/decision-search-final-five-types-20261006T012417Z-c6855ff8`：后端五源 strict mypy PASS。
- `W6/decision-search-final-python-static-20261006T012609Z-73e4342f`：五源 Ruff PASS；formatter 已实际通过，仅 owned 源。
- `W6/decision-search-web-owner-repaired-final-20261006T012543Z-c86993bd`：两前端模块27风险 PASS，3.04s，wrapper PASSED/exit0、all/scoped source stable；此前 25PASS/1FAIL 原件不改。
- `W6/decision-search-final-local-lint-20261006T012452Z-f7f8683f`：五新 Web 文件 lint PASS。
- `W6/decision-search-one-pg-candidate-collection-20261006T012610Z-9150ae6e`：唯一真实 PG 候选收集1个；**NOT_RUN**。wrapper PASSED、scoped source stable；global source 在其他任务并行时变化，未声称全仓冻结。
- `W6/decision-search-web-first-types-20261006T011657Z-cb69fbea`：当时五新 Web 源的 whole-Web tsc 实际 PASS。追加本模块容量风险后的最终 `W6/decision-search-final-web-types-20261006T012609Z-eefa8756` 为 **FAILED/exit2**，仅诊断非本包 `apps/web/src/features/future-income-operation.ts:51` 的 workspace 可能为 null；本包五源无诊断且 scoped source stable。该整体结果仍为 FAILED，已通知其负责人及 Root，不修改他方源，也不以旧 PASS 替换此次失败。

原前端失败 `decision-search-web-direct-risk-20261006T011736Z-15a7577e` 为测试 helper 的默认参数将 explicit undefined 替成 synthetic owner。窄修测试，未放宽生产 owner 校验；原源在 `.runtime/FULL-706-search/web-owner-fixture-failed-20261006T0118Z`。Startup EPERM 的 `decision-search-web-first-pure-20261006T011657Z-247c47fd` 原 FAILED 也保留；获批本地子进程运行后才取得测试结果。旧 frontend 首次 whole types 已 PASS，新增容量断言后的最终 types 另留原件，不复写旧结果。

根唯一实际节点：`test_full_decision_search_integration.py::test_actual_action_key_and_captured_version_search_is_zero_write`。生成 bf_test 原 prepare（不执行）→原 GET decision 捕获确切 typed version→按 ID/key/版本相交→未知/外 scope/伪财务输入拒绝→所有实际物理表前后完全相同。现在只有 collection，未造 actual PG/金融成功。
