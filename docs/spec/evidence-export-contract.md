# 原字节导出器合同 v2 差量

能力状态：READ_ONLY_ARCHIVE / TEN_NATIVE_GATES_IMPLEMENTED / TWO_PARTIAL_ORIGINAL_BRIDGES / PRODUCT_ACCEPTANCE_INCOMPLETE。`scripts/export_evidence.py` 保留原字节归档，下文原9组检查加v2.1审计完整门，并保留2个局部原件重算桥；另8组仍明确UNVERIFIED，当前真实原件尚不齐全，不能返回产品完整成功或关闭任务。原 v1 三文件按原 bytes/SHA 保存在 `.runtime/W1-export-semantic-prepatch-20261005T024838Z-a9b01245/original-source-index.json`；原失败、旧 DEVELOPMENT 包不改名、不改状态。

## 原入口与退出码

在仓库根目录执行；归档使用 Python 标准库、只读 `git rev-parse HEAD`，原生门调用已绑定当前 SHA 的可信纯检查器及固定 Poppler `pdfinfo.exe`。重验 RESET 时会导入原模型定义和现环境 SQLAlchemy；不建立 DB 连接、不访问 API/浏览器、不改正式历史/真实资金，不使用 LibreOffice。

```powershell
.venv/Scripts/python.exe scripts/export_evidence.py --manifest .runtime/EXPLICIT_RUN/request.json
$env:BOUNDEDFUNDS_EVIDENCE_REQUEST = (Resolve-Path .runtime/EXPLICIT_RUN/request.json).Path
.\make.cmd export-evidence
```

`--manifest` 优先于环境变量；统一任务入口无需改参数。也可 `--output .runtime/evidence_packages/NEW_ID` 覆盖请求里的目标目录。没有显式请求为 REJECTED / exit 2，不选“最新”日志。非法路径、原件 hash/尺寸不符、源码漂移、重复证据、非法绑定或目标已存在也是 exit 2。合法但缺少强制内容或内容未验证的请求生成 INCOMPLETE 诊断包 / exit 1；这不是第四条最终命令通过。最终任务入口本次未执行。

## 输入 schema

请求必须是 UTF-8 严格 JSON；拒绝重复键、NaN、Infinity。下面是结构示意，所有占位 hash、路径及 run 都必须用真实原件替换；不能直接作为验收请求。

```json
{
  "manifest_version": "bounded-funds-evidence-request-v1",
  "package_run_id": "new-package-id",
  "purpose": "DEVELOPMENT",
  "source": {
    "git_head": "actual-current-HEAD",
    "source_sha256": "actual-registered-source-digest",
    "files": {"every-registered-current-source-path": "actual-file-sha256"}
  },
  "runs": {
    "actual-run-id": {
      "purpose": "DEVELOPMENT",
      "source_context": "CURRENT_SOURCE",
      "source_sha256": "actual-run-source-digest"
    }
  },
  "artifacts": [{
    "artifact_id": "original-id",
    "role": "FAILURE",
    "path": ".runtime/actual-run-id/original.json",
    "sha256": "actual-original-sha256",
    "size_bytes": 123,
    "run_id": "actual-run-id",
    "purpose": "DEVELOPMENT",
    "source_sha256": "actual-run-source-digest",
    "validator": "STRICT_JSON_V1"
  }],
  "checks": [{
    "requirement_id": "final_four_commands",
    "artifact_ids": ["original-id"]
  }]
}
```

`source.files` 必须精确等于脚本 `inventory(root)` 的当前清单，不可省掉后来新增的源码/测试/合同/锁/已登记配置。`source_digest(files)` 是按路径排序、紧凑 JSON 序列化后的 SHA256；它包括每个文件的实际 SHA256，故未提交代码也有显式边界。范围是脚本列出的七个源码根、七类源扩展名和 CONFIG 白名单，不是所有项目文件；输入/指标定义/规则/原需求/材料需要另以 artifact 明确登记。源码原件读取后在写包前再次读取并比较清单，拒绝准备期间漂移。它不证明后续执行一直保持该源码，验收仍须生产者的前后 freeze 记录。

每个 run 显式登记原用途和源码摘要。CURRENT_SOURCE 必须与当前全部登记源码一致；HISTORICAL_CONTEXT 可以保留不同旧源码的失败/背景原件，必须维持旧 run/旧摘要/原用途，不能参与当前验收成功。artifact 的 run 必须存在，用途和摘要必须与该原 run 一致。当前验证的是这些显式声明与字节 hash 的一致性，**不证明声明中的金融运行真的发生**；manifest 将绑定状态标为 `EXPLICIT_DECLARATION_ONLY_NOT_RUN_VALIDATION`。

允许用途 DEVELOPMENT / MVP_ACCEPTANCE / FULL_ACCEPTANCE / TOOL_ONLY；用途是请求声明，不是验收结果。TOOL_ONLY 请求或原件不能完成产品验收。role 由脚本 ROLES 白名单控制。SOURCE 只能来自已登记源码根/源扩展名/配置（包括 deploy 包装和初始化源）；其他原件只能来自项目 docs、.runtime、data/scenarios、output/playwright 根，MATERIAL 额外允许根 README.md。请求本身也必须在证据根。绝对输入路径、驱动器/UNC/ADS、空路径段、遍历、Windows 尾点/尾空格/设备名、敏感 `.env*`/`.git`/缓存根及解析后越界均拒绝。中文与普通空格文件名可保存。同 ID 的大小写别名、同一实际文件重复登记者拒绝；缺失文件可以保留 MISSING 诊断记录，目录、hash/尺寸不符不能当原件。

## 实际验证能力与缺口

| capability | 实际可验证 | 不能证明 |
|---|---|---|
| UTF8_TEXT_V1 | 原字节能解码为 UTF-8 | 文档完整性、主张、来源、人工评审 |
| STRICT_JSON_V1 | 原 JSON 语法、无重复键/非有限数 | `status=PASSED`、金融正确性、实验优势 |
| STRICT_JSONL_V1 | 每个非空原行是严格 JSON；空文件 UNVERIFIED | 已运行、零失败、审计完整或有效 |
| PNG_HEADER_V1 | 原字节具有 PNG magic | 真实浏览器捕获、共同 run、图中文字、截图完整 |

这四项仅产生 CONTENT_ONLY_VERIFIED。未知 validator 名称为 UNVERIFIED；不会执行请求指定的任意模块、命令或代码。原生 check 必须显式使用 `validator: MVP_NATIVE_V2` 并给出下文实际原件引用；仅提供全部文件或 `status=PASSED` 不足。缺少引用或原件不存在则 MISSING；重验失败、原失败或不支持的协议为 UNVERIFIED。不得将 P 输出换名成为 B0/B1/B2/B3，也不得将人工模拟 actor 耗时写成人类研究；目前没有真实五臂/完整14指标独立 oracle validator。

固定保留 18 个原强制证据组：四最终命令；后端 coverage/有效性质样例；前端交互；六业务 E2E；三轮 demo；离线三黄金链；24冻结案例；五真实 baseline arms；14指标；金融链；审计链；原截图/录像；完整初版文档；八图；企划真实页数；录像真实时长；人工一致性；正式历史保全。实现 checker 不等于这些组已有当前实证。FULL_ACCEPTANCE 请求亦不构成67项 validator；其逐项原验收与缺口仍由 FULL 映射管理。

尚未支持的8组及其待接实际协议见下文。不能从 Markdown 章节数假装 PDF 页数。外层最终命令日志会在脚本返回后才完整；工具只在其他组实际齐备且归档字节最终重核后才可 exit 0 / EXPORTED，同时 `acceptance_status=EXTERNAL_EXIT_UNVERIFIED`、`task_closed=false`。外层真实任务退出及初版关闭另行验证，不能把本工具的 INCOMPLETE 原输出当一次成功 export-evidence。

## 原件与输出

目标只能是 `.runtime/evidence_packages` 的新子目录，已有目录一律拒绝。脚本从经过稳定读取/尺寸/hash 核验的原 bytes 写新副本并读回比较，避免“先 hash、再复制另一版原件”。原失败状态/换行/空白保持不变，没有去除失败记录、改审计 hash 或清理历史的逻辑。HISTORICAL_CONTEXT 也完整复制并标明非当前验证用途。

输出包含 `request.original.json`、`source/<原源码相对路径>`、`archive/<artifact_id>/<原文件名>`、`manifest.json`、`index.json` 与 `failures-and-gaps.jsonl`。索引给出原请求、源码、实际可复制的 artifacts 和生成缺口表的逐文件 SHA256/字节数；manifest/index 本身不递归自索引。请求声明的缺失原件无虚构副本。生成缺口表是诊断信息，不能替代原失败日志。当前整文件读入内存，不是大录像流式归档器；未实测大文件资源表现。若写包发生 IO 失败，保留已产生的部分目录并非零，重试必须新 ID，不覆盖它。

## 已验证边界

纯 TOOL_TEST_ONLY 测试位于 `scripts/tests/test_export_evidence.py`，显式运行，不修改全量 testpaths：

```powershell
.venv/Scripts/python.exe -m pytest -q scripts/tests/test_export_evidence.py -p no:cacheprovider -p no:tmpdir
.venv/Scripts/python.exe -m ruff check scripts/export_evidence.py scripts/tests/test_export_evidence.py
.venv/Scripts/python.exe -m ruff format --check scripts/export_evidence.py scripts/tests/test_export_evidence.py
.venv/Scripts/python.exe -m mypy --explicit-package-bases scripts/export_evidence.py scripts/tests/test_export_evidence.py
```

本节点纯测试43 passed / 1 skipped（1.80s）；原生 symlink 创建因 WinError1314 无权限而跳过，真实 Windows junction/symlink 越界行为尚未本机实测。路径解析代码已有拒绝，但该未测项不能写已通过。系统 pytest 临时根和显式 basetemp 两次 ACL 失败保留于 `.runtime/W1-export-tool-20261005T035100/environment-red.log`、`pure.log`；通过结果是另外的 `pure-repaired.log`。测试用新 UUID 目录保留 TOOL_TEST_ONLY bytes，未读取金融 DB、未替换真实实验、未运行 PG/浏览器/全量。

真实 DEVELOPMENT CLI 检查的请求、stdout/stderr、源码边界与观察位于同一独立 `.runtime/W1-export-tool-20261005T035100` 目录；仅能证明命令入口及原字节诊断归档，不能证明任一金融/材料强制验收。

## v2 原生检查接口：已实现的 9 组

每个 check 的 `inputs` 使用 **artifact_id**，不能塞成功字符串或任意文件路径。该 check 的 `artifact_ids` 必须列出它及其原生索引递归引用的全部原件；引用别组未列文件也拒绝。所有当前原件必须匹配实际当前 HEAD 和全登记源摘要，HISTORICAL_CONTEXT 只允许下述原 W0 保全锚。字段名为实际脚本接口，下表没有声称真实数据已通过。

通用 `scoped` 对象含 `manifest`、`source_before`、`source_after`、`log` 四个原件 ID，直接读取 `scripts/run_scoped_check.py` 输出，核原 exit_code 严格整数0、原状态、实际 argv、带时区起止、实际 log SHA、同 run、before=after=当前全源。各金融 snapshot 使用 producer 原 `path/sha256/data_sha256/physical_tables/row_count` 元数据；重新读取原 gzip，核原 bytes/解压 bytes SHA、实际行数。v2.2显式修订最大解压为512MiB，逐1MiB流式读取完整gzip到EOF，CRC/截断/尾随损坏及超过界限拒绝；不是截断历史或放宽每epoch审计门。实际 `w1_browser_acceptance.py` gzip 写入23业务表，实际 `SELECT version_num` 结果另存于被原 manifest hash 绑定的 `metadata_heads`；`physical_tables` 是实际查得24表。读取器严格核原23表注册集合、独立头的非空/排序/无重复，再派生 `alembic_version` 行用于完整24表比较。该派生行不是原gzip里的第24表，原 gzip 行数/SHA 不改。原件本来含完整24表的结构也保留支持；不得删库表/历史负例以缩小分母。

| requirement_id | inputs / 原生 schema | 实际重新检查 / 具体限制 |
|---|---|---|
| final_four_commands | `commands.{bootstrap,seed,check}.{scoped,task_manifest}`；所有 task 子命令原日志；`seed_isolation` | 读取原 `scripts/tasks.py` manifest：目标、run、当前源、全部真实 child exit0 与原日志；检查 bootstrap两依赖安装、seed三步骤、check全部 Python/Web/E2E 步骤。seed还必须有下一节实际 SQL 隔离原件，默认库 child exit0不通过。第四条本工具退出由外层封闭 |
| backend_coverage_and_properties | `scoped,scope,coverage,hypothesis`；原 coverage.json 与插件 observed JSON，当前 `docs/spec/mvp-coverage-scope.json` | 用当前 `verify_mvp_coverage.py` 重算完整后端≥85%、登记24core逐module≥95%、原4性质函数真实有效样本≥1000；实际 source/config/scope/counter/verifier before=after、coverage_append=false。invalid/retry不加成功样本，不相加max_examples。旧 smoke缺新bindings会失败 |
| frontend_interactions | `scoped` 的真实 Vitest 原日志 | 当前 App、dashboard和4页面的6核心测试文件都须原日志出现；实际总 test files≥6、实际测试数≥当前静态定义数；失败/skip/todo拒绝。它检原交互测试执行，不能替代实际E2E或画面人工审阅 |
| six_business_e2e | `scoped,browser_manifest,playwright_results,oracle_source,reset_adapter_source`，manifest全索引原件及checkpoint snapshots | 原 `w1_browser_acceptance.py` 的真实UI分类、same run、实际PW退出0；原 spec前6条名称全部有单次passed/duration>0，无retry/skip/error。全部HTTP/截图/录像/index bytes同run；READ_ONLY全24表不变，MONEY原整数账本oracle，RESET必须原封存/新epoch/种子比较门 |
| three_demo_rounds | 上项输入加 `round_snapshots`（3个原snapshot metadata） | 原spec第7条、同一实际scenario精确round-1/2/3-reset-after各一次；完整round原件严格来自对应round-N-reset-before的BEGIN，epoch互异并重算原round oracle。所有RESET均重验typed adapter；all7有7次独立case初始化+3次round=10次总reset，不能把初始化reset丢弃。total/round计数分别记录 |
| original_financial_chain | `scoped,oracle_source,snapshot_pairs[{before,after}]` | 仅准确当前 `.runtime/drive_mvp404_browser.py` 原pure money oracle：整数分、原始银行分录连续、账户资金守恒、原件不变、epoch/head关联。此组不等于完整权限或14指标；银行SETTLED与应用UNKNOWN两层独立，UNKNOWN不一概推现金零变 |
| formal_history_preservation | `before_result,after_result,before_snapshot,after_snapshot,formal_baseline,w0_index`，原W0 index全文件 | 原 `verify_formal_preservation.py` current SHA，原23表/408行/原W0 baseline payload SHA，before=after；legacy审计3表仍空，不造新genesis；原W0 index每项实际SHA重核，所有原失败保留。历史before/baseline/index可HISTORICAL_CONTEXT，after须当前 |
| frozen_24_cases | `registry`，registry每case `input_artifact_id/oracle_artifact_id`；开发集原index | 读取 `bounded-funds-frozen-cases-v1`：FROZEN_MVP用途/时点、≥24 unique ID、N/G/C/L/V/T最低6/6/4/4/2/2，真实input/oracle bytes与freeze前时间；开发ID/输入SHA/去顶层元数据semantic SHA不得复用；oracle有非空expected。只验证冻结结构与隔离，独立金融oracle效果须五臂/14指标另外补全，不能用本组直接关MVP503 |
| proposal_actual_pages | `pdf` 原PDF bytes | 固定本机Poppler对原bytes实际解析、未加密、真实结构10—12页，记录parser SHA；不信声明页数/Markdown章节数。空白TOOL PDF只是checker测试，不是企划内容/排版验收 |
| audit_chain（v2.1增量） | `scoped,observer`，原 `scripts/w1_audit_observe.py` manifest、原完整24表gzip与初始owner原bytes副本 | 全user×全部OPEN/SEALED独立原domain/fulltyped reference+seal/archive重算，与同READ ONLY/RR快照里实际原service完整model_dump逐字段相等；缺原owner-copy的旧运行仍诊断，pure-only `snapshot` 输入仍局部UNVERIFIED |

RESET 日期规范化已发现旧 pure checker 对 UTCDateTime 的 python_type 返回object，无法把实际JSON字符串还原datetime。旧 checker 原件不改、不删任何RESET负例；v2接入 `scripts/browser_checkpoint_oracles.py` 原pure `reset_oracle(before,after,baseline)`，只修模型映射的日期/UUID规范化，再调用旧checker全部原断言。显式修订 `W1_TYPED_COLUMN_NORMALIZATION_V1`：旧checker SHA `5f45cf3879c8fc74354bc6129e67f72788aa9ed0d1d6c2dbaf9f62531727ee35`、adapter SHA `1ac1bdbfe1ca8bd243f1dd3e959a13812c5cd6af045cb6f231af023b580eaac0` 均固定核对；manifest原 `reset_oracle_method` 的路径/SHA/revision须一致，不能只信 all_original_assertions_retained flag。缺少、源漂移或重验失败时 MISSING/UNVERIFIED，不把规范化修复当金融成功。整数分/JSONB原值不转换为宽松字符串比较。

## 种子隔离原件合同（producer 尚待最终协调器实现）

`seed_isolation` 指向 `bounded-funds-final-isolation-v1` 原JSON，必须来自当前登记 `scripts/w1_final_acceptance.py`；缺该producer即 MISSING。字段：`producer_path,producer_sha256,database,seed_task_run_id,owner_run_id,engine_target,server_binding,identity_before,identity_after,creation_original`。database严格 `bf_test_[0-9a-f]{32}`；engine_target 是 **客户端** 实际 `127.0.0.1:54329/同database`；原run=owner_run，seed task run须相同目标绑定。不得提交带密码的原URL。

两 identity 字段分别引用 same-owner-run 原SQL结果，含 `phase,captured_at,query,rows`；必须 actual SQL query：

```sql
SELECT current_database() AS database, inet_server_addr()::text AS server_address, inet_server_port() AS server_port, current_user AS database_user
```

每次恰1原row，数据库相同、实际server地址/严格整数端口符合下述server_binding、用户非空，时间包围seed开始；creation_original含实际 `database,owner_run_id,preexisting=false,sql=CREATE DATABASE "bf_test_UUID",executed_at`，必须在seed前。同run原source/命令/日志仍须通用门。它规定可信协调器必须捕获什么原件，**本次TOOL_ONLY schema测试不证明SQL或seed实际发生**。最终协调器尚未登记，当前四命令组不能依一份自写 success JSON 通过。

明确协议修订 `W1_CLIENT_SERVER_ENDPOINT_SEPARATION_V1`：原合同要求实际SQL端点恒等于客户端127/54329不适用于宿主机Docker端口映射，原合同/工具原字节已保存在 `.runtime/W1-export-producer-prepatch-20261005T035610Z-305038ee/manifest.json`。不改原SQL结果，不把容器返回的内部IP/5432伪写成127/54329。

`server_binding.mode=NAMESPACE_LOOPBACK_V1` 仅接受实际SQL `127.0.0.1:54329`；这是新Compose共享db命名空间的候选拓扑，不是对其已运行的声明。`HOST_PUBLISHED_DOCKER_V1` 另须 `docker_inspect` 原件ID与实际完整64位 `container_id`：该原stdout数组恰1项、同owner-run、Running=true、原 `/bounded-funds-db-1` 名称与 `bounded-funds` project / `db` service labels、原 Ports 精确 `5432/tcp → [{HostIp:127.0.0.1,HostPort:54329}]`。实际SQL必须返回同一原inspect中唯一非空容器network IP与整数5432。unknown mode、公开HostIp、错端口/labels/ID、多个server、混run或缺IP拒绝。Docker实际创建/inspect命令真实性仍由当前可信最终协调器与原日志/绑定负责，不接受TOOL_ONLY夹具作为产品证明。

## v2.1 尚未支持的8组：保留原范围

这8组即使文件齐备、JSON全PASSED也明确UNVERIFIED。以下是待接原生产者的最低接口合同，并非已实现的任意新“group report”。优先复用原生输出，缺协议不补造数字或套错误审计算法。

| requirement_id | 仍缺实际原件/可信 validator 合同 |
|---|---|
| offline_three_golden_chains | 新独立Compose/镜像source digest、实际load/start/ready原命令、真实网络隔离/网络请求捕获原件；同run三条黄金链HTTP+完整原金融/audit snapshots；需区分浏览器route阻断与整机离线，含新增收入自动分配的原链 |
| five_baseline_arms | 同冻结输入/独立oracle、B0/B1/B2/B3/P实际不同服务机制/原adapter source SHA、逐arm原policy/decision/action/receipt/账本；人工arm必须说明synthetic actor，不能换名P输出或冒充真人耗时 |
| fourteen_metrics | 原观测结果、独立oracle SHA、逐case/arm原分子分母/不适用理由、重新计算14项及对应原样本；现runner trace4指标另实现，金融E2/E5之外12项尚不齐。不得从flags或无分母平均造优势 |
| screenshots_and_recording | 原实际capture producer/Playwright session/run、原HTTP/截图/video/trace一一索引与SHA、截图关键点及完整录像session对应；现browser artifact hash重核不能替代完整录屏内容/来源门 |
| complete_mvp_documents | README/spec/architecture/data/policies/evidence/experiments/deployment全部原文档与逐主张真实引用；当前完整性和内容reviewer缺失，目录存在不够 |
| eight_figures | 原八图ID、实际文件/数据/生成脚本SHA、图中轴/数字/图例与原分母重核、实际内容审阅，8空白图片或文件名不够 |
| recording_actual_duration | 原完整240秒脚本录像、备用完整录像、可信本地实际容器解码与duration/帧/音视频轨道及same-run原capture；当前未注册完整视频decoder，不推测WebM时长 |
| manual_consistency_review | 真人/具名评审的实际时间、逐主张/页面/图/录屏片段对照原源与数字、未覆盖/分歧记录、same-run/source binding；未开展不能补造review passed |

## 第四条命令的因果顺序与本轮验证

先取得 bootstrap/seed/check 的真实完整原件并通过种子隔离门，再调用本CLI。CLI记录本次真实 argv、run、开始时点、exporter SHA，复制原 bytes 后重核整个index、返回包manifest/index SHA。只有18组全部真实可验且MVP_ACCEPTANCE、非TOOL_ONLY时才允许 EXPORTED/exit0；它同时明确 OUTER_TASK_EXIT_PENDING / EXTERNAL_EXIT_UNVERIFIED / task_closed=false。随后外层 tasks/scoped 真实结束的 exit0、原日志SHA与同包SHA/run必须由外部closure工具检查，才可对第四条命令与MVP504下结论。当前另8组未支持及实际原件缺口使真实成功路径仍不可达。

本轮纯测试扩展验证原生scoped源码/失败/exit/log/时钟负例、TOOL_ONLY隔离、9组未支持不变绿、实际PDF parser9/10/12/13页、seed隔离正式/remote/端口/owner/旧库/SQL/时钟负例。all-ready测试用**显式TOOL_ONLY checker stubs**，仅证明最终synthetic guard，绝不是18组产品效果。真实PG、浏览器、最终覆盖率、冻结实验、PDF企划、录像/部署、全量均未由此子任务运行。最终本轮精确命令/exit/SHA见新冻结manifest（交付时登记），此前66pass1skip日志保持原样。

## v2.1 原生产者桥接：局部重算不能关闭整组

本轮只改 exporter、其纯工具测试与本合同。新增 `partial_original_recomputation_bridges` 显式登记 `audit_chain/screenshots_and_recording`；原9完整门之后增加1个严格完整audit observer门，其余8组UNVERIFIED分类保留。局部桥接成功也返回UNVERIFIED，记录 `observations` 与具体 `uncovered`；包仍INCOMPLETE/非零。缺原件MISSING、篡改或原失败UNVERIFIED。原件和源码冻结目录是新路径，先前75/78个纯测试及全部原失败记录保持原字节。

`audit_chain.inputs={scoped,snapshot}` 使用原gzip/独立metadata。源码重核后只导入固定当前 `app.domain.audit_chain`、types、SQLAlchemy原models和audit service的 `_head/SUBJECT_MODELS/_snapshot_version`，再用当前纯typed-column normalizer；**不创建session、不连DB**。严格核完整23业务表+独立迁移行、全部physical row ID无重复；所有audit event/subject必须属于原注册user/epoch，不忽略legacy/orphan。逐原epoch重新解析canonical event/subject、保存列与canonical投影/追加时间、原subject索引hash/scope/version、OPEN实际当前typed原件。构造真实 `ReferenceBundle(subjects,current_subjects,original_errors)`，调用原 `domain.verify_epoch`，不是通用字符串hash。保留原service event/subject/bytes上限。

SEALED部分按原 `0006_audit_chain.py::audit_archive_manifest` 的全subject集合与C排序形成entries/counts，使用原 `domain.archive_manifest_bytes` 和原 namespace `bounded-funds/audit-archive-v1\0`，重验原seal/head/前序/顺序/hash与全索引。使用新增专用bounded编码不改原字节/算法，也不放宽event/subject bounds。legacy无注册head不能变VALID。

OPEN原service还要执行原decision_trace的stored trace、current references、economic receipt/action links等当前服务核验。纯bridge不模拟SQL查询，不用domain VALID代替这些服务门。完整门只接受实际当前可信 `scripts/w1_audit_observe.py --run --browser-manifest <owned-original> --output <fresh>` 及原scoped退出0。读原 `bounded-funds-audit-observation-v1`，producer SHA before/after/current相等，source_before/after相等且全部登记金融API及原scope文件实际SHA当前一致。actual_transaction严格原 `SHOW transaction_isolation=repeatable read`、`SHOW transaction_read_only=on`、原SQLidentity恰1row与owned bf_test_UUID相同。原snapshot的physical_query/24表/每表row_counts/原bytes一致，snapshot_after_data_sha256必须相同；所有原模拟users都须有完整epochs。

原 `owner-manifest-initial.json` 必须在observer原artifact_hashes内，`owner_manifest_initial_copy` 精确指向它，原bytes SHA等于owner_manifest_initial_sha256，原run/ownedDB一致、捕获当时INCOMPLETE且未退出。该副本只能在实际producer运行时复制；旧run没有副本不能追补冒充原。当上述原件齐全，所有user×OPEN/SEALED的实际原 `verify_audit_chain` 完整model_dump必须与独立重算全部字段相等，才能让audit整组VERIFIED；不能挑成功epoch或自写status摘要。此条件已实现，当前旧observer只作诊断。子任务未新跑DB，完整门的合成测试仍TOOL_ONLY。

`screenshots_and_recording.inputs={scoped,browser_manifest}` 从原manifest artifact_hashes重读check声明的全部原件，严格same browser run、当前源before=after。原capture source scope须完整包括当前金融API、Web源码/测试、合同，并逐原scope文件核实际当前SHA；外层scoped仍核全部登记源码前后/current，不能省金融文件或通过缺source map变绿。兼容原生产者相关范围而不虚构其曾记录其他独立工具。读取每session原 `actual-browser.json`、同目录trace.zip、原WebM、PNG/native.txt和HTTP/body。ZIP不解压到磁盘；成员路径/重复/加密/数量/128MiB解码预算都拒绝。只接受原Playwright trace v8 `test.trace/0-trace.trace`、真实Edge channel、context/userAgent、recordVideo配置、实际完成fullPage screenshot calls。原test.trace video attachment的原SHA1资源与外部WebM的实际字节SHA256一致；SHA1仅复用Playwright原资源协议，**不是金融审计算法**。PNG实际逐chunk CRC与完整有界zlib scanlines重验，超预算/损坏/缺body text拒绝，绝非仅magic检查。HTTP每原metadata必须有实际body/index与时点。

这能证明被可信原capture producer绑定的部分来源与字节结构，不能证明240秒/备用录像/所有关键截图/实际解码帧/画面内容。WebM仅原trace attachment完整字节关联，未声称解码。合成TOOL_ONLY WebM测试故意只提供marker，不作为产品视频。原失败session保留 original_status=FAILED，不会被局部检查变绿。尚缺当前实际成功全session、全部原keypoints、真实duration/frames与独立内容review；`recording_actual_duration/manual_consistency_review`继续独立UNVERIFIED。

其余7组最小producer差量保持原范围：离线三链必须独立部署source/image/实际startup/真实egress+host port+HTTP观察；五臂必须同冻结input逐arm真实不同adapter与原policy/action/receipt/bank；14指标必须完整每case分子分母/N/A与原独立oracle重算；完整文档必须逐原章节/主张引用与内容核查；八图必须原图/原数据/脚本/数字与轴图例审阅；240秒录像必须可信实际容器解码/轨道/帧与备用原件；人工一致性必须实际具名逐项时间/结论/未覆盖。不以新schema成功flags替代原生产者证据。

## v2.2 显式快照容量、真实 argv 与三轮 scope 修订

本次仅 exporter/其纯测试/本合同，原3文件字节与SHA保存在 `.runtime/W1-export-snapshot-bound-before-20261005T053741Z-3ee138fb/`。旧123PASS/skip及旧观察失败原件不改。实际原件位于 `docs/progress/evidence/W1/audit-current-owned-20261005T0457Z/snapshot-before.json.gz`，压缩8,227,118字节，完整解压113,098,303字节，data SHA `39ec2ef8a499d5c26cc091a63623bca31686c38d157b0c427bb581a7fade30b3`。原observer为 FAILED/OperationalError(AdminShutdown)；本次只证明其原快照容器可完整读取、byte hash/24表/原行计数一致，没有把失败改成审计VALID。

旧64MiB是容器解压门，确实小于此原件。新 `MAX_SNAPSHOT_DECODED_BYTES=512MiB` 固定代码值，不接受输入自改；每次最多读1MiB，剩余界限+1字节探测EOF/overflow，完整gzip成员/CRC校验后才能解析严格JSON。原压缩SHA、解压SHA、bytes、完整物理表集合、全部每表原row_counts以及存在时原total row_count仍逐项严格核对，不截断、不删除历史。正式库保全快照也使用同一有界读取器，其原23表/408行/零审计/原baseline/hash门保持。PNG、trace、视频容器的界限及解码器不在本次差量。

流式仅约束解压输出和读取块；JSON解析仍需要完整bytes和对象，尚未测最大512MiB的RSS/CPU/多快照高水位，不能宣称资源SLA。512MiB+1及压缩炸弹负例使用显式TOOL_ONLY的小预算边界同算法验证，没有造一个假的实际512MiB金融运行。

原service每epoch `VERIFY_EVENT_LIMIT=10000`、`VERIFY_SUBJECT_LIMIT=20000`、`VERIFY_BYTE_LIMIT=64MiB` 完全保留，桥接器在解析该epoch各canonical原件前先核三门。对原失败快照只读逐原epoch统计：完整24表34,715行、5epochs；最大event52、subject7,688、canonical21,301,070字节，均在原门内。统计属于 RAW_ORIGINAL_ANALYSIS_NOT_AUDIT_VERIFICATION；尚未测正在运行的新all7的最终原件，不补造需求或成功。

四命令 check 的 backend 门按真实 argv 解释：认可原 `uv run --frozen python -m pytest apps/api/app/tests scripts/tests ... --cov --cov-config=docs/spec/mvp-coverage.ini`，以及当前原scoped wrapper的真实`--`后原vector；不再要求两个token连续。严格保留两个完整target、一次全后端`--cov`和原scope配置，拒绝echo/字符串伪命令、少target/单test/重复target、narrow --cov、append/no-cov、marker/keyword/deselect/ignore/collect、CLI ini覆盖或非冻结launcher。四命令仍核bootstrap/seed/check的全部原child、原日志/exit/source-before=after/current/HEAD、真实seed隔离，不靠pytest字符串过门；coverage/性质本身仍由独立501门重算。

三轮 scope 由原Playwright第7条实际spec.id按原UI规则派生scenario；只接受该scenario下精确6个具名 before/after checkpoint，各一次、before为BEGIN、after为RESET、相邻且round1→2→3。round_snapshots必须逐字段等于三个原reset-before的snapshot metadata；不接受虚构round-complete/after-reset/别的scenario或重复epoch。遍历全部checkpoint的旧RESET oracle不删，all7实际总10 reset与3 round reset分别记录；rounds单独运行则1case初始化+3 round=4总reset。任一初始化reset篡改仍拒绝整组。

新增纯例使用TOOL_ONLY checker spies验证10次dispatch和严格名字scope，不能证明真实UI或金融；实际旧113MB容器读取、原预算整数统计是另一个明确原件层级。此次未运行PG、Docker、浏览器、金融、全量或当前完整产品验收；正在运行的实际all7由root唯一执行，不能预填成功。

本次最终精确原件：`docs/progress/evidence/W1/export-snapshot-pytest-round-pure-final-20261005T060509Z-cd2f8307/` 实际182PASS、1SKIP/9.65s（旧symlink WinError1314能力边界保留）；types `...types-final-20261005T060510Z-6ed43b04`、Ruff `...static-final-20261005T060511Z-a181dfcc`、format `...format-final-20261005T060512Z-09193dd2` 2文件均PASS。四run exit0，all_source_stable/scoped_source_stable=true；前179PASS/1skip候选亦保留，不改写旧123PASS/skip证明。最终SHA、原capacity统计副本和原源保留路径另列新freeze manifest。
