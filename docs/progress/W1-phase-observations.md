# W1 原执行三阶段事务观察差量

状态：IN_PROGRESS。2026-10-05 root授权接入冻结候选V1及7项定向模拟PostgreSQL风险；本文件不关闭MVP-503/初版验收，也不称独立phase proof或经济验证完成。

## 实现与保留

新 `execution_observations.py` 在provider单invocation ContextVar中捕获原live Session的真实数据库/backend/txid、原所属action/request/hash、claims/bank/postings/receipt/account/evidence及外层commit/rollback事件。callback只接不可变bytes、每phase重读原source/13bindings/user/OPEN epoch；原source/effect/autonomy/hash不重写，不缓存权限。无observer默认仍原Session.begin、无额外SQL。原execute/bank/project函数签名、原Session构造、monkeypatch故障入口和UNKNOWN保留分支均保持。

修改 `execution.py`、`execution_bank.py`、`execution_projection.py`、`experiment_arms.py`，新增helper及 `test_execution_phase_observations.py`。provider现有四stage仍NOT_PHASE_PROOF/economic=false；新增raw `live_phase_observation_refs` 只标 CAPTURED_NOT_INDEPENDENTLY_VERIFIED。保存点commit不算外层phase3commit，连接池复用backend不误要求不同PID，独立事务需要不同真实txid。

freeze `.runtime/W1-phase-observer-candidate/freeze-c8fb53dd7e904966af7b3a9fe2824207.json` 的16项原件及4源码baseline在接入前逐项核SHA相同。原四源码再次byte archive至 `.runtime/W1-phase-integration-20261005T074541Z-f726870b/archive-manifest.json`。新helper/test采用不存在路径创建，原candidate/RED/正式模拟历史均保留；没有迁移或新seed数据改动。

## 当前验证

- Ruff实际6源PASS：`evidence/W1/phase-installed-ruff-20261005T074612Z-e19c069a/manifest.json`。
- strict mypy实际6源PASS：`evidence/W1/phase-installed-mypy-20261005T074613Z-c4353ed9/manifest.json`。
- candidate AST/compile/strict shadow6types/Ruff/gitapply--check通过，仅静态；其初期mypy失败日志仍保留于.runtime candidate。
- 7项实际PG此刻NOT_RUN：等待root声明源码冻结后单一序列运行，每项使用原temporary_database生成的独立 `bf_test_<uuidhex>`，不连接/重置正式业务历史。root声明组PG须在本序列结束后运行。

7项覆盖计划：正常三个原outercommit/different SQL txid；bank提交后原响应丢失；projection保存点成功后outerrollback；bank提交后观察失败保留经济腿；reservation提交前观察失败真实rollback；成功idempotent不编造未到bank/project；无observer默认金融管道及ContextVar不泄漏。这里只列候选范围，实际结果须追加原manifest，未测不补成功。

## 未覆盖与下一前置

SQLAlchemy嵌套事件过滤、默认实际管道及7项金融边界尚需PG。wrongcontext/foreigncallback等更广负例只列设计，原7未包括。rawphase原件尚未被独立proof消费者严格验完整闭环；无bank provider拒绝捕获仍NOT_IMPLEMENTED，GOAL基线/独立14指标/原24×5仍缺。root下一步在完整API runtime/source冻结后运行定向PG并保留失败原件；所有CASE冻数据和FULL编号关闭状态不由此更新。

## 2026-10-05 原V1结果与V2显式执行修订

以上NOT_RUN段落是V1接入前状态。后来V1实际7项均PASS301.58s；原wrapper `evidence/W1/phase-installed-real-pg-module-20261005T074922Z-ba00b6ac/manifest.json` 保留SOURCE_CHANGED/exit0，唯一变化为root独立声明测试positions→asset_positions。`.runtime/W1-phase-integration-20261005T074541Z-f726870b/precise-scope-review-f844ce5c3a554248bd4b67b4cba03137.json` 独立核233个实际依赖SHA配对稳定；不重写原wrapper为PASSED，不把V1观察升级独立证明。首console pytest collection失败原件仍保留。

显式修订 `W1_PHASE_RECHECK_PRODUCER_V2`：按root授权只接入helper/provider两目标、新纯观察测试；原四执行core及executor不改。冻结候选 `.runtime/W1-phase-observer-v2-candidate-20261005T081357Z/freeze-20261005T083901Z-71bc1698/manifest.json` 已完整核原bytes，当前两原件再archive于 `.runtime/W1-phase-v2-integration-20261005T084051Z/original-manifest.json` 后apply精确patch。root后来新增owned User的23表before/after捕获保留；原旧V1未捕users的原件不补造。

V2每actual execute生成service_call_id贯穿scope/RawPhase/原capture封套，原terminal valueSHA相联。原outer完成后新增NullPool RR/READ ONLY连接，再核source/registry/user/OPEN epoch，读原完整xid8状态/独立SQLID/PID及7局部表+ownedUser原行、SQL/参数/inventory。当前同集群postgres角色实际PG16.15函数能力原测原件 `.runtime/W1-phase-proof-capability-20261005T081357Z/manifest.json`，只证明自身新只读事务，不是业务phase实测。完整协议见 `docs/experiments/mvp-phase-observations-contract.md`。

实际安装后的53 TOOL_TEST_ONLY纯测试PASS1.13s：`evidence/W1/phase-v2-installed-pure-final-20261005T084453Z-e9a8d5c2/manifest.json`；strict mypy3、Ruff3、format3分别PASS：`phase-v2-installed-mypy-final-20261005T084454Z-aa9e3353`、`phase-v2-installed-ruff-final-20261005T084455Z-47944980`、`phase-v2-installed-format-final-20261005T084456Z-05e257c1`。四原manifest实际scoped/all_source_stable=true。首次安装mypy四项test re-export错误原FAILED/修订前测试保留；只改新测试直接原import，不改生产行为。

V2业务PG/浏览器/金融本批NOT_RUN，root独占后续实际序列。原stage/phase/economicFalse及CAPTURED_NOT_INDEPENDENTLY_VERIFIED保留；独立消费者、no-bank完整失败封套与经济oracle仍缺，目标/GENERAL未支持点、正式24/五臂/初版全量未借此关闭。旧7PG测试raw protocol-v1断言须root另作显式测试schema修订；现交付不改该旧测试原件。

后续root另行授权 `W1_PHASE_RISK_TEST_SCHEMA_V2` 必要差量：旧7测试原文件SHA6c08fbe14b6aa33ba9d45da58357874f33121a7ea61aec13cadff3083a05f0c0逐bytes再归档，现SHAe89c75d666a1f8d8942841e9df94d2c2e0028663989952a9cc3e64e4658b72f6。AST核7个原测试及49个原assert完整保留，仅V1协议常量更新为V2，新增至95assert。正常3commit及projection rollback节点核call_id、原terminal valueSHA、新Probe独立txid/PID/RRRO/实际target_status与8scope原rows inventory；未改金融源或删减负例。

实际strictmypy4/Ruff4/format4/7节点collection PASS，原证据分别 `phase-v2-risk-schema-mypy-20261005T085226Z-db4ac4fa`、`phase-v2-risk-schema-ruff-20261005T085226Z-7b964fb5`、`phase-v2-risk-schema-format-20261005T085227Z-6816a145`、`phase-v2-risk-schema-collection-20261005T085228Z-72e0149a`。这仅collection，不是实际PG；root后续最小实际3节点为正常3commit、projection savepoint后outerrollback、bankcommit后capture failure。proof消费者保持新.runtime候选开发，False原字段不提高。

root后来实际运行上述三个节点，原 `evidence/W1/phase-v2-independent-recheck-real-pg-20261005T085339Z-efd70eb8/manifest.json` 为PASSED/exit0，原log实际3PASS158.10s（wrapper160.015866s）。原manifest scoped_source_stable=true；all_source_stable=false，唯一差量root并行新 `scripts/mvp_frozen_runtime.py`，不在本phase直接风险依赖/实际provider登记的3个工具SOURCE集合，原声明不改。这里只记录这三个实际风险与独立新事务原件断言，不代替其余四节点/完整经济oracle/离线validator/正式24×5。此次复核只读原manifest/log，没有重跑PG、没有升级原False或经济结果。独立validator继续仅新.runtime候选。
