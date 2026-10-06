# 知余 Demo：本地准备、启动与新轮次

当前已为你准备并启动一个新的初始试用轮次，浏览器打开 [知余本地演示](http://127.0.0.1:19179/zhiyu.html)。**先运行 `status` 检查，不要重复运行 `start`。** 本轮尚未确认知余模板、注入收入、建立目标或执行资金动作，保留原始种子。

知余使用现有 PostgreSQL、API、Web 和模拟银行。新的轮次命令只创建独立的演示数据库与数据库用户；正式模拟库 `bounded_funds`、历史部署、旧轮次和失败日志全部保留。未连接真实资金。

当前登记轮次为 `20261006T073008Z-9e4333a75fb649b2b39b440f67590090`，数据库为 `bf_test_9e4333a75fb649b2b39b440f67590090`，API/Web 端口为 `19006/19179`。启动原件 `start-ready.log` 已核对实际库名、用户以及两个服务的环境标识。服务后续状态仍须通过以下命令检查。

## 前置条件

在仓库根目录 `F:\学校活动\工行杯\钱途有界\bounded-funds` 打开 PowerShell。使用已安装的 Python 3.12、`.venv`、Node 和 Web 依赖。现有 PostgreSQL 必须位于回环地址端口 `54329`；配置的管理员应能创建新角色和数据库。包装脚本把管理员连接的目标显式设为 `postgres`，不会在正式模拟库执行迁移、种子或重置。

API 端口 `19000`—`19005` 与相应 Web 来源地址已用于保留轮次；当前试用使用 `19006/19179`。如端口已被占用，启动会拒绝，不会停止其他进程。脚本不启动 Docker、不安装依赖、不调用原来的 `make seed` 或 `make demo-reset`。

## 检查当前已启动的试用轮次

```powershell
$env:PYTHONUTF8 = '1'
& .\.venv\Scripts\python.exe scripts/zhiyu_demo.py status --round 20261006T073008Z-9e4333a75fb649b2b39b440f67590090
```

确认 `database_check` 的名称与用户匹配，且 API/Web 均为 `round_matches: true`，再使用页面。当前审计轮次应为 `077e5bab-69a9-4950-bbe5-5f5ef531a4c7`。检查命令不产生金融动作。

`prepare` 首次创建独立 `bf_test_<32位十六进制>` 数据库及仅拥有该库的 `zhiyu_demo_<同一标识>` 用户，随后运行现有迁移和原始种子初始化；再次执行只读取已登记轮次，不重置数据。`start` 用工厂入口 `app.zhiyu_main:create_zhiyu_app` 启动隐藏的 API/Web 进程，记录实际 PID、完整参数和日志。只有 API 与 Web 代理同时返回该轮次的准确数据库标识后才报告就绪。

轮次登记保存在已被 Git 忽略的 `.runtime/zhiyu/<轮次>/`；`current.txt` 指向默认轮次。数据库口令仅保存在该轮次的 `environment.private.json`，不打印到终端，不写入跟踪文件。不要公开该文件或 `failure.private.log`。

## 开始新的演示轮次

先在原页面检查当前操作状态。若存在“结果待核实”或其他未完成操作，保留原页面与定位信息，使用原操作标识恢复；新轮次不能替代原操作恢复。

```powershell
& .\.venv\Scripts\python.exe scripts/zhiyu_demo.py new-round --api-port 19007 --web-port 19180
& .\.venv\Scripts\python.exe scripts/zhiyu_demo.py start
& .\.venv\Scripts\python.exe scripts/zhiyu_demo.py status
```

上述命令创建并启动后打开 [知余下一轮演示](http://127.0.0.1:19180/zhiyu.html)，目前该示例轮次尚未创建。新轮次采用新数据库、新用户和新数据，不清空原数据库，不关闭原服务。不同端口保留独立浏览器存储；原页面仍可查询原轮次。需要更多轮次时显式指定两个空闲端口。

查看旧轮次时使用准备命令返回的准确标识：

```powershell
& .\.venv\Scripts\python.exe scripts/zhiyu_demo.py status --round '<原轮次标识>'
```

## 检查与失败处理

`status` 检查数据库实际名称/用户，并通过廉价的 `/api/v1/zhiyu/environment` 核对 API 和 Web 代理的环境标识。它不生成账单、收入、策略、动作或回执。`registration_status` 是上次登记状态；是否当前可用以 `database_check` 和两个服务的 `round_matches` 为准。

准备失败会保留轮次、已创建资源、阶段和原始私有错误日志，不自动删除角色或数据库。启动失败会保留所启动的 PID 和日志，不终止任意进程。先读对应轮次 `round.json` 的阶段和服务日志；修复依赖或端口冲突后，使用原 `--round` 继续检查。仍有服务占用端口时不要重复启动；可以继续使用该轮次，或指定新端口创建新轮次。

脚本没有清库、删除历史、批量停止或自动清除浏览器存储的命令。页面中的收入/越界/响应丢失预置由服务端处理，金额、决定和回执来自实际模拟执行。演示验收范围与实际结果另记于本次 ZY-D01—ZY-D08 交付记录；启动成功本身不等于金融场景验收通过。
