# MVP-401 subject 编码优化交还

`FAST_READY` / `PRODUCTION_FROZEN` / `PURE_VERIFIED` / `PG_AND_BROWSER_NOT_RUN_BY_THIS_OWNER`。

2026-10-04T15:24:43.709116Z 最后 pure runner exit0，所有此 owner 句柄已关闭。实际改动仅：

| 路径 | 最终 SHA256 |
| --- | --- |
| apps/api/app/domain/audit_chain.py | 5c1877cd4f1d1b41e5dd498ad0d07de39633aa21299d085f6c08f6e57bc4a640 |
| apps/api/app/services/audit_chain.py | d1e977b64d6f80b70bbab7d980ba891d8e5c97bcdc4859f8f52ab4dac3b8f402 |
| apps/api/app/tests/test_audit_subject_encoding.py | e3d5c7eea9bc7cc890a931f252a66423ce61a8cca28681a48c7c3f05347d740f |
| apps/api/app/tests/fixtures/audit_subject_encoding_originals.json | c3ce3c303cfce7ec8ca2bd057c870ba4844facdfc8cafa01d45b1df4bfc7dcfb |

生产最小 diff 与授权候选完全一致。完整原校验体转入 `_validated_subject_bytes`；public build/text/hash/parse 仍独立强验，原 `_protocol` 与 `_declared` 保留；capture 在一次完整校验后复用即时 canonical bytes 得到 exact text/原 namespace hash。没有跨调用缓存、trusted flag/model_construct、裁剪原件、金融语义/权限/交易/审计协议变化。

验证记录：

- `.runtime/MVP-401-subject-encoding-red.txt`：原 source SHA bd796b… 尚未改动时新 public capture 对照缺入口，真实 `1 failed`。cache 权限 warnings 已保留，不影响该真实失败原因。
- `.runtime/MVP-401-subject-encoding-first-green.txt`：7 个预先冻结原件 exact bytes/hash 通过；原件 oracle 在旧 source 下生成，永不由新实现重写。
- `.runtime/MVP-401-subject-encoding-strict.txt`：29passed/1failed 为新测试错误假定 depth 消息；旧函数实际也封装为 cannot-be-canonically-encoded。原日志保留，不称生产缺陷。
- `.runtime/MVP-401-subject-encoding-frozen-comparison.{txt,json}`：直接载入 ignored 原 bd796b… domain，17 个 old/new 原件、parse、invalid/error 与 depth/byte 错误精确等价。
- `.runtime/MVP-401-subject-encoding-{pure-green,ruff,format,mypy}.{txt,json}`：实际 argv、UTC 起止、退出码、完整原日志 hash 与4路径前后 hashes；所有 exit0，changed_during_run=[]。

相关 pure 范围为新30项 + audit_chain_domain + bank_posting_codec + external_bank_fact_domain，合计 **74 passed in1.50s**。覆盖原18字段/v2外部及NULL clearing/zero income opening/原 trace/raw坏claims、public mutable DTO重新校验与输入快照、copiedextra、严格格式/版本、tenant/epoch验链、depth及16MiB raw预算。Ruff/check、format、mypy分别0；没有全量、PG或浏览器测试。

唯一附加诊断 `.runtime/MVP-401-subject-encoding-microbenchmark.{txt,json}` 是无 cProfile、无DB的7个相同固定小原件，31交替顺序样本、每样本140次编码，old三段原capture median0.0355928s，new合并median0.008893s，profile_active=false。只是有限 pure 输入的调用成本，不代表原273点资金链或GET延迟，不能给产品SLA或真实链优化倍数。

root 接手真实原 golden/UNKNOWN投影/stored tamper 以及必要 native label 验证，阶段wall按实际无profile执行记录；本 owner保持四文件冻结，不与这些运行并发修改。优化前真实第四链 profile 的源/原件与污染 worker 限制另见 `.runtime/MVP-401-audit-performance-observed.md`，不能把新source追溯成那批旧profile的来源。
