# MVP-303 本地验证环境恢复

2026-10-04，定向PG验证期间Docker Desktop/54329端口离线。审计与域缺源测试均仅连接超时，0行为断言执行，不记生产RED或验收通过。

本次启动已安装的 `D:\DockerDesktop\Docker Desktop.exe` 后，实际后端日志报 `dockerInference` remove失败（The file cannot be accessed by the system），引擎已停止。`docker desktop stop --timeout 30` 实际exit0；只读进程核对确认所有Docker Desktop/backend/docker CLI进程退出，docker-desktop WSL停止。未改应用代码以规避环境错误。

## 精确恢复范围（执行前核对）

仅在 `C:\Users\starryzac\AppData\Local\Docker\run` 内非递归重命名以下0字节运行时socket/reparse对象，原对象以 `.stale-20261004-mvp303` 留存；任何对象缺失、进程运行或目标名称已存在立即停止。此目录已有20260718的同类stale留存，不触碰它们。

- `dockerInference`
- `dockerEthernetVfkit`
- `userAnalyticsOtlpHttp.sock`

不删除文件或目录，不处理容器卷、VHDX、settings、镜像、项目库或测试证据。启动成功后仅 `docker compose up -d --wait db` 恢复已有服务，再确认端口和PG连接。后续结果按实际补记。

参考为Docker官方CLI [stop](https://docs.docker.com/reference/cli/docker/desktop/stop/) 与Docker项目公开问题 [#448](https://github.com/docker/desktop-feedback/issues/448)、[#625](https://github.com/docker/desktop-feedback/issues/625)。问题报告仅作为故障线索，恢复是否成功以本机真实状态为准。

三个对象的首个 `Rename-Item` 仍报系统无法访问此文件，立即停止，未成功修改任何对象。下一步只对父运行目录做一次非递归目录重命名：精确原路径 `C:\Users\starryzac\AppData\Local\Docker\run` → 同父路径 `run.stale-20261004-mvp303`。执行前再次确认无Docker进程、原目录自身不是reparse point、全部直接子项均为0字节reparse socket、无子目录、目标不存在；保存全部8个原端点（包括旧stale端点），创建新的空run。此为可逆保留，不涉及Docker数据目录或删除。

该目录重命名实际exit0，原8个端点已保留。下一启动实际越过Inference初始化，但在另一个 `C:\Users\starryzac\AppData\Local\docker-secrets-engine\engine.sock` 报相同1920错误，仍未恢复PG。再次执行官方stop，进程全部退出。只读核对该目录恰含2个0字节reparse socket（engine.sock及20260718旧stale），无秘密内容文件。精确新增恢复范围：该目录 → 同父 `docker-secrets-engine.stale-20261004-mvp303`，同样非递归保留、检查无数据/子目录/进程/目标冲突，再创建同名空目录并启动；不读取或移动秘密存储。

第二目录保留实际exit0。但之前失败启动在新run内留下新的dockerInference，第三启动又在该点失败；官方stop随后失败。仅结束逐PID核对路径的本次已崩溃Docker启动/stop进程，未结束其它WSL或数据进程。实际无Docker进程后核对：run只有1个0字节reparse socket dockerInference，docker-secrets-engine为空。下一精确范围是run → 同父 `run.stale-20261004-mvp303-retry2`，保留新失败端点并创建空run；两个临时目录都为空后才启动一次。已有两个保留目录不动。

最终干净启动成功：实际docker info返回ServerVersion 29.6.1；`docker compose up -d --wait db` exit0，原容器efea8d0e3a67（原创建时间2026-10-03 23:31:24 +0800）恢复Healthy，端口127.0.0.1:54329。未重建容器、删除卷、重置种子或升级Docker。12:58通知三owner重跑受影响定向验证；只有实际断言结果才能补入验收。
