# 隔离演示容器传输合同 v1

本工具只核对原字节并构造命令，不运行 Docker、RPC、seed、reset 或数据库事务。新辅助脚本仅在根协调器明确执行其 stdin 后连接当前 owned API 容器的原本地模拟库。所有工具夹具均为 `TOOL_ONLY`，纯测试通过不能作为金融效果、实际部署或离线三链验收。

## 输入与拒绝门

`scripts/demo_container_transport.py` 提供以下 Python 接口。协调器在**每次命令前**重新捕获当前源和容器状态，不能把此前验证对象当跨请求授权缓存。原金融权限、原时钟、原 DTO 与原 bank/recovery 决策仍由原服务执行。

```python
owned = validate_owned_transport(
    registration_path.read_bytes(),
    start_manifest_path.read_bytes(),
    actual_four_container_inspect_bytes,
    {"api": original_api_image_inspect_bytes, "web": original_web_image_inspect_bytes},
    freshly_captured_source_map,
    freshly_captured_git_head,
    require_started_inspect=True,
)
```

当前源使用 `scripts/w1_demo_deployment.py::source_state()` 同一范围和摘要算法；HEAD 是实际 `git rev-parse HEAD`。函数接受的是独立原件，不靠成功字符串决定可用性。首次原 inspect 必须匹配实际 start manifest 的 `artifact_hashes["after-inspect.log"]`；后续新原 inspect 可以用 `require_started_inspect=False`，但全部四个容器的完整 ID、原 image ID 与原 start 身份仍必须相同，且协调器必须另存这次实际 inspect 的 argv、退出码、起止、stdout/stderr 与 SHA。

必须同时满足：新 `bf-demo-<UUID>` 所有权、`bf_test_<32 hex>`、owner/run/project/name、原 start 源前后等于当前源、源摘要和 HEAD、实际 API/Web Linux amd64 image 标签、原 uv/pnpm lock SHA；四个完整不可变容器 ID、三服务实际健康、init 实际退出0。API/init 共享实际 DB 完整 ID 的 namespace，read-only rootfs、无 bind/挂载/独立网络；DB 只有 owned internal 名称网络、独立项目卷、原 postgres 54329 命令、无 PostgreSQL 主机端口。Docker 将该 named volume 短语法写入 `HostConfig.Binds` 时，仅接受唯一 `<project>_demo_pgdata:/var/lib/postgresql/data:rw`，并独立重核 `Mounts.Type=volume/Name=<project>_demo_pgdata/Destination`；真实 filesystem bind 或foreign卷仍拒绝。API/init 实际 DSN 为 `bf_demo@127.0.0.1:54329/bf_test_UUID`、SIMULATION_MODE=true、LLM_ENABLED=false。实际 Web 只可登记 loopback 端口，网络限原项目 internal/frontend。

注册端口不得冲突、不得使用原54329/8000/5173或已保留18047/15179。任何源变化、替换容器、失败命令、正式库、远端 DSN、foreign bind/volume/网络、错误 bank image 或缺原件都拒绝。原路径只接受规范仓库相对路径；重复 JSON 键、非有限数字、越界 metadata 拒绝。检查不会打印容器密码。

`OwnedTransport` 是冻结标量/tuple对象，argv 总是完整不可变 API ID，禁止用可变容器名。验证原件来源真实性依赖根协调器的实际 Docker 命令捕获；一份自行制造的 inspect JSON 不是部署证明。本模块不验证 outer process 已实际执行。

## 原 RPC 命令

```python
command = build_rpc_command(owned, original_scenario_rpc_dto)
# coordinator alone: subprocess.run(command.argv, input=command.stdin, ...)
```

结果准确为 `docker exec -i --workdir /workspace <API_ID> /opt/bf-venv/bin/python /workspace/scripts/scenario_runner_rpc.py`。stdin 是原 DTO JSON 加换行，构造前重核原 `scenario_types.py` 与原 RPC 文件 SHA、从当前原 ScenarioRPC 验证 JSON。只支持其原五种 operation / DEVELOPMENT / UUID 身份规则；不添加浏览器 clock、金额、fault、bank_result、冻结实验标签或新授权。不会导入或调用 ScenarioRunner，不改变原 RPC 返回结果。原金融调用的成功/失败仍需根协调器捕获实际 stdout/stderr/退出码，不能把 `PreparedCommand` 当执行成功。

## 原只读快照与全部审计

```python
command = build_snapshot_command(owned, "unique_observation_run")
# root retains command/stdin original SHA, original stdout/stderr and actual exit first
decoded = decode_snapshot_output(owned, command, original_stdout_bytes)
# root writes fresh files only, exclusive create; never overwrite old evidence
snapshot_bytes = decoded.snapshot_gzip
```

命令为同一 immutable API ID 中的原 Python `-`。`scripts/demo_container_snapshot.py` 原字节经 stdin 传入，不改 Dockerfile、不要求镜像包含新工具。配置明确 owner/run/API ID/源摘要/HEAD/全部镜像中原 API 与配置源 SHA，以及原 helper SHA；其源不是新的金融实现。

辅助脚本先核原 `require_test_database` 与实际 namespace DSN，再建立 engine。actual `REPEATABLE READ` + `SET TRANSACTION READ ONLY`，分别原 `SHOW` 读回；实际 SQL 返回同 owned database、bf_demo、127.0.0.1[/32]:54329。读取原 `Base.metadata.sorted_tables` 全部23业务表，独立 SQL 读取 `alembic_version`，核实际 public BASE TABLE24。SELECT 行保持原值，仅将原 UUID/日期转JSON字符串供原快照序列化；不修改任何历史业务行或其哈希。完整 JSON 原字节 gzip(mtime=0)，记录原/压缩尺寸和SHA、每表行数与物理查询。压缩 bytes 以 base64 放入唯一 stdout JSON framing，原封保留。

同一 RO/RR 事务对全部原 simulated users × 每个原 OPEN/SEALED epoch 调用**原** `app.services.audit_chain.verify_audit_chain`，保存完整 `model_dump(mode="json")`，不使用通用 hash 替代原 canonical/subject/reference/archive/chain 算法。再次读取全部原行与源，对同快照 before/after 原 bytes 逐字相等才可返回PASSED；任何原审计失败返回非零并保留实际失败 body。无 epoch 用户不能声称完整审计。

解码器只做纯帧检查：原命令/run/helper/owner/源绑定、实际RO/RR/SQL端点、gzip原 bytes/hash/尺寸、解压预算64MiB、原模型 literal23表+alembic24表分母、全部原用户×epoch 无缺失/重复/foreign、原 `AuditVerification` 严格JSON schema及 chain/reference/errors 字段。成功也只返回原 stdout/gzip/data bytes；不写文件，不重算或改写历史金融哈希，不宣称审计结果是独立产品验收。协调器必须先保存失败/退出原件再调用解码器。metadata/stdout JSON预算32MiB，原完整行 JSON预算64MiB；超限明确失败，不截掉原行或负例。

## 真实边界及部署源时点

旧 owner `7970d1487f8f4fa29ad09e0ad4a37149` 的注册和 `deployment-start-20261005T042404Z-a7e8b20b` 原件保持历史；后来 API 修复使旧 image 源不可作为当前可用版本。本轮根任务另登记 owner `f3f9703e75684a62888f73804e02d1db` / `.runtime/w1-deployment/deploy-20261005T044255Z-f3f9703e/manifest.json`；实际 start 完成与当前原件绑定由根任务执行，本子任务没有调用其金融/RPC/快照。

容器 inspect 的 network 名称不能证明 `Internal=true`，API namespace 的 host publish 也不能证明实际 host HTTP reachable（此前实际 API8000映射为空）。当前返回 `DB_API_SHARED_LOOPBACK_NAMESPACE; NETWORK_INTERNAL_FLAG_UNVERIFIED; WEB_EGRESS_UNVERIFIED`；Web普通 frontend bridge 出口仍未受控，不能称整个 Compose 离线。后续若改 Web 只 internal，需要新显式修订、新 source/image/start/原 network inspect、外部IP/DNS失败原件及真实浏览器请求捕获；主机 loopback relay 通过 immutable owned Web 内原 nginx 接入需另验证工具存在、流传输/HTTP语义和真实连通，当前没有实现或证明该 relay。

同 RO/RR 事务的 before/after 相等只证明本次读取稳定，不证明整个演示期间无并发变更。金融三链、跨 reset 历史保全、正式表/卷未变、完整录屏、真实断网及最终命令闭环仍属于根任务的实际验收，当前均不由这些纯工具测试关闭。

纯验证入口：`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -p no:tmpdir scripts/tests/test_demo_container_transport.py`，仅本新工具；Ruff/format/mypy同三新Python文件。实际命令/退出/源 SHA 冻结到交付manifest，未运行Docker/PG/浏览器/全量。
