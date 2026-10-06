# W1只读完整原件收集器

本次交付私有 `scripts/mvp_readonly_collector.py`、46项纯风险测试和原注册/输出合同。显式调用后只在本机generated bf_test、真实模拟User与唯一OPEN epoch中，以一个REPEATABLE READ/READ ONLY事务采集全部24物理表原列，包括23Base表和Alembic；JSONB原存储TEXT及现存审计canonical TEXT另保留。全部原件只写全新目录，不回写数据库或修历史hash。

调用为 `collect_readonly(registration_ref, output_directory, workspace=ROOT)`，外部原注册SHA及原13bindings/source bytes独立绑定。允许DEVELOPMENT未冻结捕获，不能把形状校验当作正式corpus冻结。输出五份raw及manifest，只声明CAPTURED_RAW_NOT_ECONOMICALLY_VERIFIED，阶段/经济/权限证实保持False。

最终46 TOOL_TEST_ONLY纯风险测试通过24.72s；strict mypy两文件、Ruff/format通过。最初43PASS/1FAIL、随后30PASS/15FAIL及初始static失败全部原log和相关源码保留在 `.runtime/W1-readonly-collector-checks-20261005T1040Z`。第一处纯夹具compile的future flags与原loaded-code guard不同，修改纯夹具；第二处自身声明类校验错误纳入typed别名，改为实际AST ClassDef声明。没有放松原源hash或业务权限。

真实PG、数据库零写与实际physical格式尚未由本子任务执行，root负责下一链。未覆盖完整expense历史范围、actor/HTTP/error真实调用库存、A3原verifier response、run attestation及经济/完整阶段独立判定；typed head/current subject是清楚标来源的新捕获投影，不能冒称历史存储原件或已验证审计。

最终源码/完整log/旧失败/受保护core与桥源码SHA在同目录 `final-source-proof.json`。source稳定声明仅针对本工具及明列保护源，不声称整个并行仓库静止。本次不关闭MVP-503、原正式24/五臂实验或任何14指标金融效果。

11:17UTC显式窄修V1_NO_PARAMETERS_DRIVER_FIX：root首个实PGc92d1f0f因测试假设genesis已有Subject失败，原件保留；第二18102def原13.21s失败是真实psycopg空参数字典解析目录SQL百分号。归档原冻结collector/test后，只改无参数执行分支为no_parameters=True，不改SQL/owner绑定/完整表门。新驱动回归原实现1FAIL/1PASS保留，修后48纯风险PASS30.00s、types2/Ruff/format通过，新的归档与source proof在 `.runtime/W1-readonly-collector-no-params-20261005T1117Z`。不把实际失败改成成功，真实第三次PG仍由root排程。

root第四实际PG501e18cd通过1PASS21.58s，manifest `evidence/W1/readonly-collector-exact-migration-original-real-pg-20261005T112041Z-501e18cd/manifest.json` 为PASSED/ex0/scoped_source_stable=true；并发Web测试源变更使all_source_stable=false，不宣称全源稳定。第三3ad63e9b捕获/前后零写已通过，实际失败为root测试使用迁移简称0007而原DB为0007_external_bank_facts，原失败仍保留。

本真实DEVELOPMENT范围覆盖24表fullcols/NULL、3条原event canonical TEXT、23条原subject TEXT、6个accounts行（其中两个CASH）、18条seed bank postings、五raw及14表逐原rowID/hash/pointer、原SOURCE全部bytes归档和before/after零写。rawmanifest SHA为de38a6189fc95c64881c0a2669a6ddadbe5e0b1f737e50c445e919e17a3655c2；原registeredCorpusValid、formalCompar、economic False保持。本PG不是正式24×5金融或指标验收；原三真实RED均留。
