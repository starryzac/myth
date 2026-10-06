# W1 容器只读快照 512MiB 显式预算修订

原 `scripts/demo_container_transport.py`（7ba9223b…）与 `scripts/demo_container_snapshot.py`（aaa1dd1f…）保持原 bytes，64MiB 解码/生产门与全部旧失败不变。原生产器和消费器都限制 64MiB，因此只增加消费端预算不能取得大型历史容器快照。本轮新增两个独立文件：

- `scripts/demo_container_snapshot_512.py`：新只读 stdin 生产器，完整原 Base 23 业务表、独立 alembic 元数据、原实际物理 24 表、所有实际用户×所有 OPEN/SEALED epoch 的原 `verify_audit_chain`；原 owner/DSN/namespace/current runtime source、实际 READ ONLY/REPEATABLE READ、before/after 完全相同、native SQL identity 等门不变。增加显式 512MiB 原字节预算，1MiB gzip 写入，并在报告前以 1MiB 读到 EOF 核 CRC/ISIZE、完整尺寸和 SHA。
- `scripts/demo_container_transport512.py`：纯 builder/decoder，导出 `build_snapshot_command512(transport, run_id) -> PreparedCommand` 和 `decode_snapshot_output512(transport, command, stdout) -> DecodedSnapshot`，复用原不可变 transport 和类型。不得自己执行 Docker、金融 RPC 或数据库。新 builder 的实际 stdin 为新生产器原 bytes 加唯一原 JSON 配置调用，`snapshot_helper_sha256` 绑定新实际 helper，而不是重用旧 aaa1 SHA。

```python
from scripts.demo_container_transport512 import (
    build_snapshot_command512, decode_snapshot_output512,
)

# owned 必须已经在本次请求由原完整身份门和新的 fresh 隔离观察核验。
command = build_snapshot_command512(owned, "explicit_owned_run_id")
# root coordinator 捕获实际 command.argv/stdin 原 SHA、stdout/stderr/exit 后执行。
# 先要求实际 exit=0，再解码；所有失败原 stdout/原件仍保存。
decoded = decode_snapshot_output512(owned, command, actual_original_stdout)
# decoded.original_stdout/snapshot_gzip/snapshot_data 均为完整原 bytes。
```

## 协议与预算

新报告 `protocol=bounded-funds-container-snapshot512-v1`，`capacity_revision=W1_CONTAINER_SNAPSHOT_512MIB_STREAM_V1`，`decoded_byte_budget=536870912`，`stream_chunk_bytes=1048576`。builder 配置协议为 `bounded-funds-demo-container-transport512-v1`；这些值和真正整数类型严格核验。旧 v1 报告、旧 helper SHA、其他 budget 或 chunk 不能贴上新名称通过。

解码前完整原 framing 上限 720MiB、base64/压缩原 bytes 上限 512MiB、解压后原 bytes 上限 512MiB。原 metadata 32MiB parser 继续处理小配置和原 transport；大型 native framing 使用独立、同样拒绝重复键/NaN/Infinity 的完整严格 parser。1MiB 逐块解压，累计真实字节超限立即拒绝；所有**被接纳** gzip 成员必须完整到 EOF，标准 gzip CRC/ISIZE 必须通过。CRC 错误、截断、尾随非 gzip bytes 不接纳。多个合法 gzip 成员的全部解压原 bytes 都保留，不能只取第一段。

随后执行与原消费器相同的完整所有权/source/head/实际 helper stdin/唯一调用配置/readonly-RR/native SQL/原 gzip SHA/原 data SHA/after SHA/原 table denominator/实际 row_counts/all-user/all-epoch/原 AuditVerification 完整 schema与chain/reference/checkpoint/errors 门。不会重新计算历史哈希、补造 bank receipt、裁掉行/epoch、改金融判断或缩减篡改负例。未知缺表/多表仍失败；未来 Base 业务表变化须显式修订 23+metadata 的原合同。

这是 decoded 大小门，不是进程总内存承诺：原生产器仍取得完整 ORM 行并编码，消费器仍返回完整原 JSON bytes 并验证所有行；大型 payload 的峰值内存会高于 decoded bytes。本轮未测得实际大型容器性能，不填造耗时或优势。

## 原证据边界与风险验证

指定旧 host observer 原件 `docs/progress/evidence/W1/audit-current-owned-20261005T0457Z/snapshot-before.json.gz` 经新的**只读容量分析**以 1MiB 到 EOF 核 CRC：原 decoded 113098303 bytes、SHA `39ec2ef8a499d5c26cc091a63623bca31686c38d157b0c427bb581a7fade30b3`，压缩 SHA before/after 相同，原 manifest 未变且仍 FAILED。分析原件 `.runtime/W1-host-snapshot-capacity-analysis-20261005T061453Z-1c51bba0/analysis.json` 明确 `HOST_OBSERVER_SNAPSHOT_GZIP_NOT_CONTAINER_EXECUTION`。它只支持预算需求；不得复制成新容器执行结果、修复原 AdminShutdown 或关闭验收。

纯夹具全部 TOOL_ONLY，包括：小载荷与原 64MiB decoder 的完整 gzip/data/事务/全部分母结果对照；实际 65MiB JSON gzip 跨旧门而新完整原字节/SHA不变；实际 >512MiB 完整 JSON 的 gzip 被真实累计解压字节拒绝（没有假 len、改 cap 或金融判断 mock）；CRC/ISIZE/截断/尾随原件负例；原 source/helper/run/transaction/SQL/24表/row_counts/全部 epoch/reference/schema 负例。

首个新 builder 候选漏导入 re，初始纯测试 30 FAILED/6 PASSED；这些失败未走到 decoder body，不能作为金融或完整 gate 验证。源副本 `.runtime/W1-container512-first-candidate-20261005T062911Z-9813a75f` 保留；原 console 全 log 当时被工具截断，明确未完整保留，不补造。随后在相同错误候选上独立取得单节点原 RED `docs/progress/evidence/W1/container512-first-builder-tool-red-20261005T062912Z-f0bdfec5`，原 FAILED 不变。修复仅导入缺失与格式，未删负例。

尚未取得实际新 512 生产器/decoder 容器运行、大型容器快照、银行三链、离线浏览器或版本全量验收。真实容器与金融链由 root 独占执行，后续必须用新 helper actual SHA/argv/stdin/stdout/exit/source before/after 原证据。小初始旧 64MiB 容器快照通过，不证明新版或三黄金链完成。
