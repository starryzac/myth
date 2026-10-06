# W1 Docker 端点运行计数显式修订与只读复核

真实原件 `docs/progress/evidence/W1/offline-current-owner-isolate-native-20261005T0557Z/manifest.json` 为 **FAILED**；其中实际唯一 disconnect exit=0 已执行。不能重跑 disconnect、恢复外联、改旧状态或重置历史。只读定位原件 `.runtime/W1-offline-endpoint-accounting-review-20261005T060137Z-d6410025/analysis.json`：四容器 identity/Image/Config/HostConfig/Mounts 均未变，API/DB/init namespace 未变；Web 只脱离原 frontend，internal 原端点不变。原 frontend `Status.IPAM.Subnets["172.23.0.0/16"]` 的运行计数由 `IPsInUse=4 / DynamicIPsAvailable=65532` 变为 `3 / 65533`，与释放一个 IPv4 端点一致。冻结 b0 校验器在“完整网络定义相等”门拒绝这些动态计数；该原失败不变为通过。

新增纯模块 `scripts/demo_offline_endpoint_accounting.py`，方法为 `W1_DOCKER_NETWORK_STATUS_ENDPOINT_ACCOUNTING_V1`。原 transport 7ba、network b0、snapshot aaa1、旧 CLI 032b 与所有原负例不改。新方法首先使用原校验器验证真正原 before/after/command/stdout/stderr/source/head。只有原安全检查已经抵达 `Original network definition/ownership changed` 时才可进入计数差量分支，其他失败拒绝。

新分支对原两网络逐字段核对：internal 的全部字段、Status、附件必须完全相同；frontend 必须无附件且 Internal=false。只允许真实 Web 原 IPv4Address 所属、原 IPAM.Config 声明的同一 IPv4 子网发生两项运行计数变化。计数须为真正非负整数，IPsInUse 精确 -1，DynamicIPsAvailable 精确 +1，前后各自总数都等于子网完整地址数。提供 Status 却计数未变化、多个子网变化、额外字段、IPv6/未知格式变化、标签/ID/配置/命名空间/镜像/原字节绑定变化均拒绝。比较副本只用于逐字段比较；原 bytes 和实际 command hash 完全不改。新结果明确记录计数前后、原 guard 拒绝消息、方法和原网络/command SHA；结果仍为 TOPOLOGY_ONLY_EGRESS_UNVERIFIED。

## 新只读入口

```powershell
.venv/Scripts/python.exe scripts/w1_offline_reobserve.py --action observe --registration .runtime/w1-deployment/deploy-20261005T052942Z-852b7cff/manifest.json --start docs/progress/evidence/W1/deployment-start-20261005T053750Z-06011e24/manifest.json --prior-isolation docs/progress/evidence/W1/offline-current-owner-isolate-native-20261005T0557Z/manifest.json --output docs/progress/evidence/W1/offline-isolated-reobserve-NEW --run

.venv/Scripts/python.exe scripts/w1_offline_reobserve.py --action probe --registration .runtime/w1-deployment/deploy-20261005T052942Z-852b7cff/manifest.json --start docs/progress/evidence/W1/deployment-start-20261005T053750Z-06011e24/manifest.json --prior-isolation docs/progress/evidence/W1/offline-current-owner-isolate-native-20261005T0557Z/manifest.json --output docs/progress/evidence/W1/offline-isolated-probe-NEW --run
```

这些命令是接口示例，子代理未执行。省略 `--run` 不读输入、不创建目录、不调用 git/Docker。这个 CLI 根本不提供 isolate/restore/delete/stop 命令。它先验证实际原 isolate producer before/after=032b、source/head 稳定、原 registration/start bytes、完整 private 原件 SHA/尺寸、真实单次 disconnect 整数 exit=0、原完整 API/Web image before/after 和新窄拓扑规则。旧 FAILED 状态明确保存在新 `prior_actual_status_retained`；只有新 fresh 两次观察完成才给新的观察结果。

每次捕获新的四容器、两个**不可变 network ID** inspect、两个不可变 image ID inspect，在 observe/probe 前后重核。所有 namespace/config/Id/image/current source/健康/init exit/内部附件以及隔离后的全部网络字段均须与原真实 after 一致。镜像表示兼容仍只允许完整 typed JSON 相等，并保留各 raw SHA。私密 `*.original` 与公开 `*.redacted` 使用原双保存机制，公开表示明确不是原字节；未验证 ACL，不输出 DSN/密码。新 producer、计数桥、源枚举器与原冻结依赖 before/after 均严格绑定。

observe exit0 返回 `ISOLATED_TOPOLOGY_REOBSERVED_EGRESS_UNVERIFIED`。probe 使用原 032b 的固定 API socket/DNS 和 DB/Web busybox 库存/帮助/路由/IP/DNS 探测规则；缺工具、未知错误和超时仍为 DEFERRED(exit3)，结构/源/原件失败为 FAILED(exit1)。全部三容器固定探测满足才给有限 `EGRESS_BOUNDARY_OBSERVED`。始终 `offline_accepted=false`、`financial_operations_executed=false`、`disconnect_executed_by_this_run=false`、`three_golden_chains=NOT_RUN`。

工具纯夹具均标为 TOOL_ONLY；不是 Docker、离线或金融效果证据。实际新只读观察/探测、relay、浏览器仅 localhost 全请求、三黄金链及原完整审计/截图/录屏仍由主任务取得。IPv6 动态分配或未来 Docker 的其他 Status 格式未支持；不能依据当前规则直接算通过。
