# W1_READONLY_COMPLETE_DATABASE_ORIGINAL_COLLECTOR_V1

私有生产脚本 `scripts/mvp_readonly_collector.py` 只在显式调用时连接本机127.0.0.1:54329的generated bf_test。默认金融/HTTP/Runner方法不附加SQL，无数据库创建、seed/reset、业务写、授权缓存或金融oracle。真实PG由root排程；纯SQL spy不是数据库或经济实证。

## 调用与原注册

```python
from pathlib import Path
from scripts.mvp_readonly_collector import collect_readonly

manifest = collect_readonly(
    {"path": str(actual_registration_path), "sha256": external_registration_byte_sha},
    Path(new_original_directory),
)
```

CLI：`uv run --frozen python scripts/mvp_readonly_collector.py --registration <原绝对路径> --registration-sha256 <外部完整原SHA> --output <全新目录>`。路径必须在当前workspace，现有输出目录及同名文件拒绝，写入全部exclusive create；旧失败原件不覆盖。

原注册exact keys：protocol=`mvp-readonly-collector-registration-v1`、bindings=原13完整字符串、database_name=实际generatedbf_test、as_of=原逻辑aware业务时刻、role=`BEFORE|AFTER|FINAL|SNAPSHOT`、source_ref=`{path:绝对原GLOBAL_SOURCE文件,sha256:原source_sha256}`、root_registration_ref=当前provider原完整descriptor或null。最后一项非null时重读其原bytes并核同bindings/db；collector_registration_ref单独绑定采集原注册，不冒充provider注册。原SOURCE.files保持原repo-relative path/sha，purpose与13绑定相同；全部当前API runtime/migrations、config/lock、新collector必须在SOURCE。不造V1登记包装，不改Case/RULE/SOURCE原字节。

允许 `DEVELOPMENT`、`MVP_FROZEN`、`FULL_FAMILY_FROZEN` 三种原purpose；同一次绑定必须一致。DEVELOPMENT真实数据库捕获仅是未冻结原件，13bindings形状与source登记不证明registeredCorpusValid，不升级为正式24案例/五臂实验。尚无完整24真实冻结时，root首个PG必须另记录DEVELOPMENT_UNFROZEN_CAPTURE及原corpus未冻结状态。

## 实际SQL与完整性

自身NullPool Engine使用原DatabaseSettings，只允许原本机PG端点；不输出密码/环境/完整URL。单Connection REPEATABLE READ外层事务，首statement SET TRANSACTION READ ONLY，随后原SQL current_database/PID/完整txid/current snapshot/RRRO/数据库时钟。校actualdatabase而非只看URL。

完整非系统physical table inventory必须精确public23Base+alembic_version，共24；未知schema/partition/foreign/RLS/多余或缺表拒绝。原列集合、UDT类型、NULL性质及formatted type/长度与实际Base契约匹配；各列含JSONB/TEXT完整取出。逐表actual SELECT无LIMIT，原完整行按ID排序，独立同事务COUNT核完整性；所有原列保留，nullable不剔除。用户表只owned id，各业务表只同user_id，asset_products唯一GLOBAL_CATALOG业务例外；alembic_version另标SCHEMA_METADATA，不作为业务全局权限。真实User必须is_simulated且唯一OPEN epoch与原绑定一致，原genesis/seed也核对应。

JSON保存原对象/NULL/整数和有限数，DB money列不转float/Decimal/bool；既有恶意JSON金额类型也原样保留让独立oracle拒绝。UUID/date/aware datetime仅按原审计序列化表达转JSON；不会回写内容或历史hash。JSONB另保存同SELECT的原CAST AS TEXT，保SQL NULL与JSON null、原存储数字文本区别；已有canonical_text/seal/request/result TEXT逐字保存，不重新编码历史哈希。单原文件512MiB，超限拒绝，不裁剪/截断。

事务正常退出后才写全新原件，source/原注册及loaded module函数/声明类方法在每次采集前后fresh复核；同调用只复用已解析登记，仍重读bytes。这里证明一份一致RR snapshot里的owned行，不能独立证明提交后另一事务的最新状态、过去空日覆盖、完整HTTP调用目录或阶段commit。

## 五份原raw与后续接缝

每份protocol=mvp-raw-observation-v2、原13bindings、collector_registration_ref；若有原provider登记则附其root_registration_ref，原purpose不升格。

- BUSINESS_SNAPSHOT：24表完整原行、原physical/column inventory、逐表columns/row_ids/count/独立SHA、JSONB storage TEXT、SQL/参数/真实connection身份。捕获complete仅指同事务数据库行范围。
- FINANCIAL_BASIS：全部24实际表及时间/原snapshot_ref，不删Alembic。
- FINANCIAL_FACTS.payload.facts：原`mvp-financial-facts-v2`、14表精确集合与每表row_ids/sha256/source_ref指BASIS原数组，artifact_originals包含其完整原UTF8；原全audit_events及原subject snapshots数组和source_ref交独立typed policy映射器，不造from/to事件。仅唯一当前合法时窗的实际SIMULATED_NEW_FUNDS_LEDGER银行evidence可直接引用income_payload原content；不按余额构造income。缺唯一来源/expense coverage保持缺项。
- AUDIT_DATABASE_ORIGINALS：现存event/subject TEXT与其完整原行，current full business rows原snapshot指针；head由真实head列投影、checkpoint是本捕获新生成的typed数据合同，明确标DATABASE_HEAD_COLUMN_PROJECTION/NEW_CAPTURE_CHECKPOINT，不能冒称已存历史原件。current subject是本快照full row typed投影，bank posting的审计original-layout另标，完整physical row不删NULL/external_fact_id。所有integrity/financial flags=false，没有调用verify_epoch或P金融判断。
- TRACE_SNAPSHOT：23Base业务表/role/数据库完整coverage与原snapshot_ref。actual execution_http_ids、A3实际verifier response、actor/ASK/TIMING/error/stack等真实调用原件另由driver提供；缺字段保留，不能用空数组把无HTTP模拟调用当零动作，不能模板生成因果栈。AUDIT_DATABASE_ORIGINALS不会冒充旧A3的AUDIT_ORIGINALS verified checkpoint记录。

A1可从完整FINAL原bank_operations/redemptions/receipts及提交、失败、UNKNOWN等原ActionPlan取得实际逻辑动作分母；HTTP只是额外路径。无HTTP native调用仍须保留这些完整原表，不能用execution_http_ids=[]省略实际动作。collector目前不声明HTTP调用inventory完整；driver可在有实际完整调用原件时追加其覆盖证明。A4原run_recovery抛出的projection_errors与随后READ_RECOVERY的RECONCILIATION_REQUIRED状态分别保存，后者不自动算另一异常。

manifest status仅CAPTURED_RAW_NOT_ECONOMICALLY_VERIFIED，原stage/economic/independent proof False保持。source_scope和collector原注册是来源绑定，不能替代独立oracle及实际run-to-Capture attestation。

## 真实风险验点与未覆盖

root下一链应在新generatedbf_test核实际24表/23Base+Alembic、User唯一OPEN/全列NULL、原canonical TEXT逐字、JSONB原text、14表pointer/valueSHA及事务RRRO identity；再核wrong db/owner/epoch/source漂移、列或长度漂移/RLS、output EEXIST、失败时业务零写。金融结论另测，不凭这些原文件关闭MVP-503或正式24×5。

未覆盖实际PG/真实金融/Browser；历史expense lookback证明、实际HTTP/actor/monotonic/故障stack、A3原verifier response、actual run attestation、完整阶段proof/14独立指标、GOAL baseline。typed投影失败保留已有原TEXT和full rows，输出MISSING；不存在完整原件时不补成功。

工具验证：46 TOOL_TEST_ONLY纯风险测试通过，24.72s；strict mypy两文件、Ruff及格式检查通过。SQL spy和合成原件仅验证工具合同，真实PG由root下一链验证。

V1_NO_PARAMETERS_DRIVER_FIX：root首两次真实PG失败原件保留，第二次18102def在目录SQL的`LIKE 'pg_%'`出现psycopg placeholder错误。仅query空参数分支改为原SQL+`execution_options={"no_parameters":True}`，非空owner绑定保持原参数、不插值SQL；表目录、只读/完整性合同不减。新增纯驱动回归在原实现1FAIL/1PASS，修后原46加2回归48 TOOL_TEST_ONLY PASS30.00s，strict types2/Ruff/format通过。原46冻结proof与源码不覆盖；实际PG需root再次验证。
