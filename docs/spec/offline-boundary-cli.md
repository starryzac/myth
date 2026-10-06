# W1 离线出口观察 CLI 合同

本工具新增 `scripts/w1_offline_boundary.py`，用于主任务实际执行已有隔离演示部署的网络观察、唯一获准隔离操作和逐容器出口探测。纯测试均为 **TOOL_ONLY**，不代表 Docker、银行模拟、三黄金链、浏览器或离线验收结果。旧源、部署、卷、失败原件和正式模拟历史均不改写。

## 运行方式与因果顺序

所有动作都要求显式 `--run`。省略该参数时只返回 `PREPARED_NOT_EXECUTED`，不读取输入文件、不调用源枚举/git/Docker、不创建目录、不连接服务。输入和输出路径可为仓库相对路径或位于所声明根目录内的绝对路径。

```powershell
.venv/Scripts/python.exe scripts/w1_offline_boundary.py --action observe --registration .runtime/w1-deployment/deploy-20261005T052942Z-852b7cff/manifest.json --start docs/progress/evidence/W1/deployment-start-20261005T053750Z-06011e24/manifest.json --output docs/progress/evidence/W1/offline-observe-NEW --run

.venv/Scripts/python.exe scripts/w1_offline_boundary.py --action isolate --registration .runtime/w1-deployment/deploy-20261005T052942Z-852b7cff/manifest.json --start docs/progress/evidence/W1/deployment-start-20261005T053750Z-06011e24/manifest.json --output docs/progress/evidence/W1/offline-isolate-NEW --run

.venv/Scripts/python.exe scripts/w1_offline_boundary.py --action probe --registration .runtime/w1-deployment/deploy-20261005T052942Z-852b7cff/manifest.json --start docs/progress/evidence/W1/deployment-start-20261005T053750Z-06011e24/manifest.json --prior-isolation docs/progress/evidence/W1/offline-isolate-NEW/manifest.json --output docs/progress/evidence/W1/offline-probe-NEW --run
```

上述路径是接口示例，未在本工具交付中执行。每次 `--output` 必须是新的 `docs/progress/evidence/W1` 子目录。既有输出拒绝覆盖。registration 必须在 `.runtime/w1-deployment`，start/prior 必须在 `docs/progress/evidence/W1`，均为原 `manifest.json`。部署源或 HEAD 若已变化，必须取得对应新部署；不能给旧 owner 更名或补写 current 标志。

`observe` 读取原 registration/start、原 API/Web image inspect 和四容器 start inspect；调用当前部署源枚举器重新计算实际源映射和实际 HEAD。随后捕获新的四容器 inspect、两网络 inspect、两个不可变 image ID inspect，并使用已冻结 transport/network 的原校验器验证完整 owner、UUID、项目、镜像、源码、锁、命名空间、容器健康、init exit、独立卷和网络附件。退出 0 的 `OBSERVED_EGRESS_ATTACHED_NOT_OFFLINE` 只表明观察成功，并列出精确计划 argv。

2026-10-05 显式表示兼容修订：首次实际 observe `offline-current-owner-observe-native-20261005T0549Z` FAILED，停在 API image 原 bytes SHA 不同；原 start 捕获 3941 bytes 与 fresh Docker 捕获 3854 bytes，严格完整 typed JSON 相同、字段差异为零。旧 851898d9 producer、原失败与源副本保留于 `.runtime/W1-offline-image-representation-revision-20261005T055236Z-e66f188e` 及原私密目录，未改为成功。新窄桥只允许 JSON 空白/对象键表示变化：以原严格 parser 再编码完整 typed JSON，必须全部相等，包括 Id/platform/digests/labels/全部剩余字段。1 与 1.0、false 与 0、重复键、任何增删字段或值变化都拒绝。两份 raw 均完整保存，各自原 SHA/尺寸及共同完整 typed JSON SHA 明确记录 `STRICT_TYPED_JSON_VALUE_EQUALITY_WITH_DISTINCT_RAW_CAPTURES_V1`；不将新原 bytes 冒充 start 原 bytes。frozen transport 仍核原 start image 原 bytes/hash；其内容必须已与本次完整 fresh image 严格相等。namespace、actual container/image ID、锁和 current source 门不变。

`isolate` 重做全部前置，不复用 observe 作为权限缓存。唯一变更是无 shell 的 `docker network disconnect <完整 frontend network ID> <完整 Web container ID>`，只调用一次，无 `--force`、重试、重连、删除、stop、reset 或宿主网络操作。操作后捕获全部四容器、两网络与两 image 原件，核对网络之外的 identity/Image/Config/HostConfig/Mounts 不变、API/DB/init namespace 不变、Web 原 internal endpoint 不变、internal 网络实际 `Internal=true` 且附件未变、frontend 附件为空、所有健康和 init exit 状态正常。失败时保留实际部分原件，不做补偿回滚。成功退出 0 只给 `ISOLATED_TOPOLOGY_EGRESS_UNVERIFIED`。

`probe` 要求本同版本 producer 生成的真实 isolate 原件；核其 source before/after、每项私密原始字节 SHA/尺寸和原 disconnect argv/exit/time/raw hashes，重新调用冻结校验器。新四容器和网络必须与隔离后原件完全一致（仅健康状态的必要实际条件重新核验）。探测前后再次完整 inspect；镜像、配置、命名空间、内部附件或源/HEAD 漂移均使观察失败。它不调用金融 RPC、seed 或数据库查询。

## 原件与公开表示

`manifest.json` 协议为 `bounded-funds-offline-boundary-v1`。保存实际 command argv、UTC 开始/结束、真实整数退出码或 null、outcome、stdin/stdout/stderr SHA；超时保存原部分输出并标记 `TIMEOUT_UNVERIFIED`，无法启动保存实际异常类型/errno 并标记 `SPAWN_FAILED`，均不捏造子进程 exit=0。

每次原 bytes 用新文件 `*.original` 保存到独立 `.runtime/w1-offline-boundary-private/<fresh>`，含实际 inspect 中的 DSN。原件 SHA 与字节数在公开 manifest 绑定。公开目录只保存 `*.redacted`：JSON 重新编码，DSN 密码和 password 字段/密码 Env 显式替换，另存其 SHA 和尺寸；每项明确 `REDACTED_REENCODING_V1_NOT_ORIGINAL`。公开脱敏表示绝不当作原捕获 bytes，校验器读取私密原件。工具 stdout 仅状态、出口码和公开 manifest 路径，不输出 DSN/密码。该独立目录未宣称拥有已验证 Windows ACL；不要将私密目录直接对外分享。

producer 原源 before/after 字节、部署源枚举器原源 before/after、当前源映射和实际 HEAD before/after均记录。冻结 transport/network 的实际 before/after SHA 必须等于既定 SHA；本工具不修改这些文件。任何事后漂移返回 FAILED。初始源枚举与最终枚举不能替代金融历史守恒证据。

## 固定探测和未验证条件

API 使用 owned 不可变容器 ID 的原 `/opt/bf-venv/bin/python` 执行捕获的只读 stdlib 脚本。原 `/proc/net/route`、`/proc/net/ipv6_route`、`/etc/resolv.conf` 保留。固定 TCP `1.1.1.1:443` 和 `8.8.8.8:53` 的各自 socket timeout=3 秒；记录原异常类型/errno/message/时间。只有实际 errno=ENETUNREACH(101)/EHOSTUNREACH(113) 才算这两次探测不可达；CONNECTED、超时、未知 errno 不算成功。DNS 为独立原 Python 子进程 `getaddrinfo("example.com")`，外层 4 秒有界；原 argv、输出 bytes/base64、exit 和耗时保留。只有原 gaierror 的 EAI_NONAME(-2)/EAI_AGAIN(-3)、非超时消息和严格固定调用才算本次未解析。嵌套/外层超时均未验证。IPv6 Linux REJECT(0x200) 不可达占位默认项保留原文，不能当可用默认路由。

DB/Web 分别先真实 `/bin/busybox --list` 与 `ip/wget/nslookup/timeout/cat --help`，确认这些 applet 实际存在且原帮助退出 0。再捕获原 `ip route show` / `ip -6 route show` / resolv.conf。IPv4 路由与 resolver 空/失败、IPv6命令失败、任一默认路由均未验证。两个固定 IP 使用原 `timeout 4 /bin/busybox wget -T 3 -O - http://<固定IP>:<固定port>/`；既保留实际原失败，也严格要求 network unreachable/no route，退出 124/137、超时/未知错误/缺工具不算成功。DNS 为固定 `timeout 4 /bin/busybox nslookup example.com`；非零原明确 REFUSED/SERVFAIL/NXDOMAIN/不能解析才可观察，超时不算。工具或版本输出不匹配时允许 DEFERRED，不能改成假 PASS。

三个容器同时满足 actual topology/current 绑定、无可用 default route、上述两个固定 IP 实际不可达、DNS 本次原未解析，退出 0 返回 **EGRESS_BOUNDARY_OBSERVED**。这是所记录端点的有限观察；`offline_accepted=false`、`financial_operations_executed=false`、`three_golden_chains=NOT_RUN`。任一探测缺失/超时/未知返回 **DEFERRED_EGRESS_UNVERIFIED，exit=3**；结构、源、原件、命令或执行失败返回 **FAILED，exit=1**。不得把 isolate 或 probe 的零退出当三链离线验收。

## 尚未覆盖

- 本交付没有实际 Docker inspect、disconnect、IP/DNS probe；这些由主任务使用原部署后执行。
- 宿主 loopback relay 的真实健康/HTTP body、浏览器全部请求仅 localhost、原业务 HTTP 回执、原审计链、三黄金链、截图/录屏和完整离线验收由独立 producer 提供。
- 两个固定 IP 与一个公共 DNS 名称只定义本次观测边界，不证明所有外部路径永久不可达。
- 新 DNS Compose 配置是否实际可用，必须看新镜像/新 owner 的 inspect 和探测；不改旧 f3/7970 owner 原证据。
- 超时或缺少 applet 的具体行为保留为未验证，不能用失败退出字符串、预期配置或 pull-policy 代替实际观测。
- 纯 fixture 输出中的 TOOL_ONLY 为检查器测试输入，不能作为产品成功证据或解除 MVP/FULL 关闭门。
