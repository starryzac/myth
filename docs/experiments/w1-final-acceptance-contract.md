# W1 初版四命令协调器与真实检查输出合同

状态：IMPLEMENTED_TOOL_ONLY / FINAL_ACCEPTANCE_NOT_RUN。入口为 `scripts/w1_final_acceptance.py`。本差量是显式 W1 验收接线修订，不关闭 MVP-501—504，不改变原需求编号、失败原件或正式模拟历史。所有纯测试的假文件、数据库名、SQL 行和原件声明仅为 TOOL_TEST_ONLY 夹具。

## 原件保留与默认行为

原 tasks 字节与 SHA 保存于 `.runtime/W1-tasks-wire-before-20261005T051342Z-7d2f32b6/`，原 SHA 为 `f5881d9a61bc21572ec2bafc1d5595822e8eb37fd479dec240ebea6437faac3f`。原 scoped wrapper 保存在 `.runtime/W1-scoped-config-before-20261005T051608Z-55b96d06/`。旧任务 manifest、source.before/after、日志和金融失败均不修改。

默认必须指定 `--prerequisites <仓库内明确原件路径>`，返回 `VALIDATED_NOT_RUN`、ready 与逐项缺口。默认仅读取本地原件和 Git，不连接数据库、不运行 Docker、金融、浏览器或四命令。非法 JSON、重复键、路径、用途、字节 hash、注册源码或 HEAD 返回 `REFUSED_NOT_RUN / exit 2`。ready 只表示工具层前置可审查，不表示产品验收成功。

只有显式 `--run` 且前置齐备才进入真实运行路径。不能使用布尔 all_ready 或选取“最新”目录。源清单包括 exporter 注册的全部文件、真实 SHA、HEAD，以及额外跟踪代码和配置；所有命令前后重新读取。新增未提交成果也参与冻结。

## 显式前置登记

登记协议 `bounded-funds-final-prerequisites-v1`，固定字段如下；路径/hash 均必须来自真实原件，无可运行的占位模板。

| 字段 | 必须提供的内容 |
| --- | --- |
| purpose | `MVP_ACCEPTANCE` |
| source | 协调器 `source_state(root)` 的完整当前结果，包括 git_head/files/source_sha256/all_source_files |
| server | 严格 `{host:127.0.0.1,port:54329,username:bounded,formal_database:bounded_funds,admin_database:postgres,container:bounded-funds-db-1}` |
| export_request_template | `{path,sha256}`；明确 exporter 请求原件，当前全源、用途 MVP_ACCEPTANCE |
| readiness_package / readiness_index | 当前原 CLI 生成的包 manifest/index 原件描述符；index 每个文件重核原字节/尺寸/SHA，保留未完成组 |
| formal_anchor | `{baseline:{path,sha256},w0_index:{path,sha256}}`；原 W0 正式库基线和原归档索引 |
| deferred_groups | 逐组 `{status:DEFERRED_FROM_CURRENT_CHECK,producer_path,producer_sha256,protocol}`，必须是当前实现且绑定源的允许适配器 |

仅允许后端覆盖率/性质、前端交互、六业务 E2E、三轮 demo、原金融链、审计链这六组从一次当次 `make check` 产生；正式历史 after 可由协调器当次只读采集。其余组必须事先 VERIFIED。四命令组由当次前3命令和原 seed 隔离原件产生，不要求先跑一次全量来授权下一次全量。

source-bound adapter 的登记是结构就绪检查；在实际写库前，协调器还调用当前原 exporter 对前置原件重新执行语义门。保留 `UNSUPPORTED_NATIVE` 的整组仍阻止 `--run`，文件存在或先前 status 声明不能覆盖此门。缺 producer、合同或真实原件必须 MISSING。

## 新建库与原生运行路径

真实路径生成唯一 `bf_test_<32小写hex>`，不能从输入提供现存库名。只连接客户端 `127.0.0.1:54329`、用户 bounded；原实际 SQL query 为：

```sql
SELECT current_database() AS database, inet_server_addr()::text AS server_address, inet_server_port() AS server_port, current_user AS database_user
```

先只读验证正式 `bounded_funds` 及 W0 索引。实际 Docker inspect 使用原生格式字段投影，记录真实容器 ID、名称、Running、Compose ownership labels 和 NetworkSettings；不输出 Config.Env 密码。只接受 bounded-funds/db 容器及宿主发布 `127.0.0.1:54329 -> 5432/tcp`。SQL 中实际 Docker 内部 IP/5432 保留不改，不伪写成宿主 IP/端口。

原管理连接先查 `pg_database` 实际 preexisting=false，再执行生成库的真实 quoted CREATE DATABASE。创建原件记录实际查询、原行、admin SQL identity、SQL 和带时区执行时间。seed 前后各记录独立实际 SQL identity，输入 exporter 的隔离协议为 `bounded-funds-final-isolation-v1`，phase 字面为 before/after。creation、owner、seed task run、Docker binding 与原件引用均明确关联。

子进程 `DATABASE_URL` 仅为新建库；清除继承的 BF_、COMPOSE_、PG*、旧 DATABASE_URL、PYTHONHOME/PYTHONPATH、PYTEST_ADDOPTS、COVERAGE_FILE、旧 evidence request/context 等控制量。密码只从 `W1_FINAL_DB_PASSWORD` 读取，URL 编码后传子进程，不写入原件。四目标固定为实际 Windows `cmd.exe /d /c make.cmd bootstrap|seed|check|export-evidence`；每个目标经原 scoped wrapper，读取其唯一真实 EVIDENCE_DIRECTORY 和唯一 native run_id，逐实际 child 原 argv、日志、exit、源绑定收录。没有拼出虚拟 task manifest。

任一失败均保留已产生 raw、源码记录、原 argv/日志/退出及阶段状态。只在本进程已实际成功创建的唯一库上 DROP；原库存在则拒绝创建，也不删除它。清理后查询 VERIFIED_ABSENT，失败为未证实，不靠本地变量宣称已删。正式库在失败路径仍尝试真实 READ ONLY/RR 后置采集，不能靠环境标志推定零写。此运行路径尚未实测。

## tasks 当次检查输出

tasks.source_state 与 scoped.sources 增加真实存在的 `.dockerignore`、coverage scope/ini 登记；不存在文件不补造。后端原 pytest 参数和性质 counter/cov 参数保留，前端原 `pnpm --dir apps/web test` 参数保留。两者经真正 `run_scoped_check.py` 执行，原 binary 由既有 command() 实际解析，因此 Windows pnpm.cmd 可用；returned.requested_argv 保存原目标 vector，manifest.command 保存实际执行 vector。

`scoped_run(label,*args)` 返回 `{run_id,requested_argv,paths:{manifest,source_before,source_after,log}}`。只有真实 wrapper exit0、before=after=当前原登记源、HEAD、原 log SHA、实际 argv 均相等才可登记输出。

`register_acceptance_group(requirement,scoped,extra_paths)` 用于 backend/frontend。额外 backend 原件分别为 scope、coverage、hypothesis；原 coverage-property-gate 仍实际执行。`register_acceptance_originals(requirement,check,artifacts,runs)` 用于后续原浏览器/审计的嵌套 inputs；原 producer run_id 保留，原用途必须当次 MVP_ACCEPTANCE，旧 DEVELOPMENT 原件拒绝重新归属。它只核当前源和原字节绑定，实际语义由 exporter 原门重算。

协调器传 `BOUNDEDFUNDS_FINAL_CONTEXT` 指向新建 W1 原件，协议 `bounded-funds-final-context-v1`，含 owner_run_id、database、source、deferred_groups。仅 native check 结束时调用 `final_acceptance_outputs()`，写新 `.runtime/quality/<native-run>/acceptance-outputs.json`，然后原 native manifest 登记 `acceptance_outputs:{path,sha256}`。没有 context 的常规检查不产生验收 bundle。

bundle 协议 `bounded-funds-native-check-evidence-v1`，字段 `{producer_path,producer_sha256,owner_run_id,task_run_id,source,checks,artifacts,runs}`。checks 是真实 native validator inputs；不是 passed 标签。artifacts 为原相对路径/实际 SHA/字节数/原 run/用途/源摘要，runs 保留当前各 native producer 绑定。当前实现只登记 backend/frontend；浏览器、三轮、金融、存活期间审计的 adapter 尚待 root 接入，缺任一组即 MISSING。W1_FINAL_OUTPUT_GROUPS 必须和实际实现一致，不能只加名字。

## 第四命令关闭边界

首次 exporter 不读取自身尚未结束的 task manifest。前3命令、seed 隔离和18组真实原件经原生语义门、包索引最终重核后才可能 EXPORTED / exit0；其 manifest 保持 EXTERNAL_EXIT_UNVERIFIED、OUTER_TASK_EXIT_PENDING、task_closed=false。

协调器在 exporter 原 child 与外层 scoped/task 实际结束后，检查原 task source/HEAD、唯一实际 exporter child argv/exit、CLI 源 SHA/起点、原 terminal JSON 的 package_run_id、包 manifest/index SHA，然后逐 index 原 bytes 再重核。仅此时另写 closure.json 的四命令 VERIFIED，包原件不回填、不重写。closure 的 task_closed 仍为 false，需求关闭由审查者决定。

对于 gzip 原件，通用内容项明确 `NATIVE_PROOF_ONLY`，不把压缩数据假标为 UTF-8/JSON；其原字节归档和金融 snapshot 的解压/数据 SHA/行数/完整24表性质由专门 native checker 验证。通用 UNKNOWN/UNVERIFIED 内容诊断不能被当成整组验收。

## 验证与尚未覆盖

纯测试覆盖路径/严格 JSON、隔离目标、源与 indexed bytes 漂移、不能弱化分母、允许真实 deferral/拒绝缺 adapter、子环境净化、Docker/SQL actual binding、后端原 argv/cov 环境恢复、真实 wrapper wiring、native 原件/用途/run 保留、第四条原 exit/CLI/hash/全部18组与 task_closed 边界。synthetic fixtures 的 SQL/容器/包声明没有执行，也不证明运行路径。

本次没有 PG、Docker、金融、浏览器、最终覆盖率或四命令实测。剩余接口是 root 的 browser/alive-audit adapter、exporter 尚未支持整组的实际 producer/checker，以及真实冻结案例/五臂/14指标/离线链/材料/媒体/人工审阅原件。新工具不得绕过这些缺口。原失败 scoped 记录保留在 `docs/progress/evidence/W1/final-coordinator-pure-first-20261005T052709Z-83d01e2a/`：63PASS、1FAIL；失败是测试错误要求删除应当被新库 URL 覆盖的 DATABASE_URL，已修测试断言，不更改原输出。

最终工具验证：74 pure PASS / 1.56s，4文件 strict mypy PASS、Ruff PASS、format-check PASS。原记录分别为 `final-coordinator-pure-final-20261005T053445Z-ee507b24`、`final-coordinator-types-final-20261005T053446Z-122cb7db`、`final-coordinator-static-final-20261005T053447Z-3befcdd4`、`final-coordinator-format-final-20261005T053510Z-06126fd6`，均在 `docs/progress/evidence/W1/`，实际 exit0、all_source_stable=true、scoped_source_stable=true。新增 nested wrapper 纯例证明当 check 内层 child 也打印 EVIDENCE_DIRECTORY 时，选择严格 owner-label 的唯一外层原件，而非最新目录。最终源码 SHA 和原保留路径另在新冻结 manifest 登记。
