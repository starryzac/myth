# W1 离线浏览器工具合同：四原业务用例，三黄金链

本文件是显式 W1 工具接线差量，不改变原 MVP 编号、金融规则或关闭条件。工具默认 `PREPARED_NOT_EXECUTED`，不读输入、不调用 git/Docker、不打开 socket/数据库/浏览器。纯夹具全部为 `TOOL_ONLY`。文件存在、纯门通过、Docker 拓扑或有限 egress 探测都不能称作实际离线黄金链已完成。

入口是 `scripts/w1_offline_browser.py`。只有 `--run` 加五条明确原件路径才可能继续：`--registration`、`--start`、`--isolation`、`--probe`、`--relay`；可加新 `--output output/playwright/<fresh-run>`。没有 newest 搜索、默认 formal 库、宿主 API/DB 启动、迁移/seed/reset 脚本、容器/卷删除、网络 disconnect/restore、relay stop 或成功结果输入。生命周期只限自身 Playwright 子进程。

## 准入与当前源

注册原件须来自 `.runtime/w1-deployment`，其他前置来自 `docs/progress/evidence/W1`。只支持注册的 `bf-demo-<32hex>` / `bf_test_<same32hex>`、原四服务完整不可变容器 ID、注册原镜像 ID、无宿主 bind mount 和原本机 namespace `127.0.0.1:54329`。注册、start、隔离和 probe 原 bytes/SHA/当前 HEAD/原源枚举必须一致，前置文件在全部执行期间不可漂移。工具源单独完整绑定，不能冒充旧容器映像已包含新工具。

原 transport 7ba、network b0、snapshot aaa1、旧 032b CLI 和 FAILED isolation 记录不改。新的 endpoint accounting 桥独立重核原 before/after/disconnect 全原件；不再次 disconnect，也不把旧失败改成成功。当前 probe 必须为新 reobserve 的 `EGRESS_BOUNDARY_OBSERVED`：实际 API、DB、Web 各原 route/IP/DNS stdout/stderr/argv/exit/stdin 都需重核，用原分类器重算，不能接受 status 字符串代替。镜像 raw 表示不同只能由原完整 typed JSON 等价规则接入。

relay 必须由原 `scripts/demo_loopback_relay.py` 产生，处于 RUNNING，精确监听 `127.0.0.1:15183`，绑定同 owner/API/Web/source/start/registration。允许的 transport 变化仅 `Connection: close`；金融 body 原字节不变，重试数为零。先前与最后 relay manifest 分别保存，新请求原 client/forwarded/response/stderr HTTP 全部按原 ordinal 收录并重新核 SHA 和唯一真实 `docker exec` argv；工具不拥有 relay 生命周期。

五条初始前置原 bytes 只在 run 开始各保存一次。导出时可调用 `validate_readiness(paths, current, head, originals=<五条初始原bytes>)` 重建原分母：四条不可变前置仍须等于其原路径，relay 使用真实初始 capture 的请求数，不以持续增长的 manifest 回填。当次最终 relay 原件另外保存。导出包必须显式登记所有外部原路径及 SHA，包括 private inspect、probe/observation、start image logs 和各 relay HTTP 原件；仅允许 helper 旁读任意文件不能替代包内 artifact 分母。

## 原 UI 行为和分母

原 `apps/web/tests/e2e/w1-demo.spec.ts` 字节不改。新 output 下生成 `.spec.mts` 观察 wrapper，先挂 BrowserContext 原 request/response/failed 与 websocket 捕获，再 import 原 spec。Playwright loader 的 testDir 只包含 wrapper，原 spec 不作为另一 collected 文件。原所有 UI 写入、GET 断言、原动作/报价/回执检查、固定流动性普通消费前置、ASK 原确认、READ_ONLY/钱守恒/reset oracle 全保留。

精确四个 named case、每项一次、Edge/msedge、单 worker、零 retry、零 skip：工资实际目标分配和申购；自然语言目标修订确认/零归属后真实新收入授权分配；普通大额消费后的真实无损恢复；实际定存流动性消费前置和原报价损失确认回执。三黄金链含该额外损失 case，共四条业务测试。不是 all7，也不是三轮。四项各自原 beforeEach UI 明确确认 reset 均要实际成功并通过原 reset oracle；不删任何 reset。

所有实际 browser HTTP/静态资源 URL 必须精确 `http://127.0.0.1:15183`。无 route/mock/请求或结果替换；附加 hooks 只观察和拒绝不合格记录。页面 context 请求及原 GET-only helper 请求的完整多重集须与当次 relay 新请求原件精确相等，包括方法、path/query、body SHA；不允许遗漏或额外未知请求。所有响应原 body 保存，实际 relay wire HTTP 另存。WebSocket、failed request、非回环 URL、500、无原件或不完整响应都失败。禁用 service workers 防止绕过 context 捕获。此门不声称主机/整个 Edge 的防火墙隔离；容器有限边界与当次应用 URL 原件是两个独立证据。

新 wrapper/config 的加载及真实浏览器执行尚未测得。只运行纯工具测试和新生成文件的语法检查时，不得描述为离线演示通过。原 native Edge PNG、文字、trace、真实 WebM 和逐 HTTP 原件仍由实际运行产生；不得渲染录像/截图。

## Broker 和读取性质

浏览器只提交原 scenario/epoch、checkpoint label/mode 或原 action_id/goal_id。前四 case 的私有 RPC 限 `verify_legacy_recovery` 和 `ingest_goal_income`，原固定 DTO/可信 native clock/实际固定模拟输入由容器原 ScenarioRunner 执行；不接受金额、余额、时钟、权限、hash、结果、fault 等自选金融输入。真实 stdin、stdout、stderr、exit、argv 均保存；不得把 legacy recovery 伪造为现代 ActionResponse。

每 checkpoint 先运行 source-bound fresh read-only reobserve，核四容器、两不可变 network ID、两原 image ID 与已隔离原拓扑完整相等，然后实际 docker exec 原 owned API。新的 `scripts/demo_container_transport512.py` 须提供 `build_snapshot_command512` / `decode_snapshot_output512`，新 stdin producer 是 `scripts/demo_container_snapshot_512.py`。缺新接口时明确 NOT_IMPLEMENTED，禁止回退成截断或缩小旧历史。预算是显式固定 512 MiB，完整 gzip/hash/CRC/EOF、physical24/model23+alembic、所有模拟 user×epoch 原 VALID 验证、source/identity/RO/RR 和同事务 before/after 零写均由新 decoder 核。最大容量 RSS/CPU 尚未实测。

gzip 是原 helper 返回的完整24表 bytes；只在调用冻结旧 money/reset oracle 的视图中分离 `alembic_version`，完整原件不改。migration heads 单独保持逐 checkpoint 相等。READ_ONLY 比较完整24表原 bytes，不能只比资金表或截取 hash。每个 expected_epoch 必须等于实际 UI/原 open epoch。原 typed-column adapter 只恢复映射 datetime/date/UUID，原 JSONB/整数/posting 字段不改。

最后父进程在 owned 容器仍存活时完成完整只读 snapshot/all-user×epoch 原审计，再做新只读 topology/egress probe 和 relay 原件核验，最后 SOURCE_EQUAL。失败原件不覆盖；停自身 child 后仍尝试最终 alive 审计，未完成明确 FAILED_UNVERIFIED。部署、库、卷、relay 保留给主任务管理。输出 task_closed=false，MVP/FULL 关闭仍依赖主任务真实整体验收和后续 exporter offline validator，不能由本工具自行关项。
