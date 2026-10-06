# W1 显式网络执行修订：单 owned Web 出口断开

这是当前已登记 Compose 的网络执行差量，不修改原金融合同、Dockerfile 或原部署历史。`scripts/demo_offline_network.py` 只做纯验证/argv构造；未执行任何 Docker、断网、relay、金融或浏览器操作。当前原 Compose 的 Web frontend bridge 出口尚未受控；原 start 成功不能作为离线验收。

## 准备和实际调用顺序

1. 根协调器从 Docker 实际重新捕获四容器 inspect、API/Web image inspect、两个 owned network inspect原stdout，登记实际命令/退出/时间/源 SHA。原 registration/start 字节保持不变。先用已冻结 `validate_owned_transport` 对 fresh 四容器检查，当前源码必须与登记镜像相同；不存在文件/新来源不能推测为完整。
2. `build_transition(owned,before_containers_raw,before_networks_raw,current_source,current_head)` 必须核实际 internal network `Internal=true`、frontend `Internal=false`、所有 owner/run/project/purpose/immutable ID；internal原网络只连接owned DB和Web（API/init共享DB namespace），frontend只连接ownedWeb。任一foreign/formal container/额外网络拒绝。返回唯一 argv：`docker network disconnect <完整owned frontend network ID> <完整owned Web ID>`。不生成强制disconnect、删除网络/容器/卷或global network动作。
3. 只有根协调器执行返回argv；每transition之前/之后立刻实际重新capture容器和网络inspect原bytes。实际命令及stdout/stderr全部原件保留，失败停止依赖路径；不重试金融请求。退出0不能代替拓扑和源重核。
4. `validate_transition` 接受before/after四容器+两网络原bytes、actualcommand原JSON+stdout/stderr，以及重新实际读出的source/head。其 `command_raw` 协议为 `bounded-funds-network-transition-command-v1`，字段 `owner_uuid,owner_run_id,source_digest,source_head,argv,exit_code,started_at,finished_at,stdout_sha256,stderr_sha256,containers_before_sha256,containers_after_sha256,networks_before_sha256,networks_after_sha256`；时刻有时区且finish>=start、exit严格整数0、所有原bytes SHA重新读核，不能仅写PASSED flag。

源码和两个原network的Id/Name/Labels/Internal/Driver/Scope/IPAM/Options不可改变；仅frontend `Containers` 从唯一ownedWeb变为空。internal endpoint原bytes保持不变。四容器Id/Image/Name/Config/HostConfig/Mounts完全相等，API/DB/init整个原NetworkSettings相等；Web实际Networks仅剩原internal且原endpoint相等。after actual健康与init原exit0仍需成立，不能换image、bind或金融权限。

纯验证返回 `TOPOLOGY_ONLY_EGRESS_UNVERIFIED` / `offline_accepted=false`，只证明所提供原件描述的受限网络变化。根协调器实际capture的命令真实性、当前运行时与后续金融效果仍须独立证据。后续fresh inspect无法再通过原transport的“Web双网络”初始门，应保留这次 transition observation 与before validated immutable identities，使用当前扩展核single internal后态；不得改写旧start或偷偷放宽冻结初始门。

## 主机入口和断网实证尚缺

直接 internal network 的主机published port此前已观察不到可用 API映射；不能由配置推断入站已通。候选入口是根任务新 loopback HTTP relay：只listen127.0.0.1固定登记端口，`docker exec -i <immutable owned API ID>` 中已知原Python建立到已验证 owned Web internalIP:80 的 TCP，Web原nginx仍同源代理到原API。允许显式记录修改Connection:close这一传输header；body原bytes不变，无金融retry、无任意endpoint/shell、无新增publicclock/amount/fault权限。这是根任务实际实现/验证的后续合同，本纯模块没有实现relay或声称可用。

离线三链验收必须另取得：after network inspect Internal=true/完整owner对应；DB/API/Web实际namespace逐个外部IP连接与DNS解析的原命令、目标、期限、原失败errno/输出、退出/时间（每个端点单独结论，不从API失败推定DB/Web）；relay监听/源/immutableargv/原HTTP request/response bodyhash与健康；真实Edge全部请求仅localhost的原捕获、同run完整三黄金链原回执/全部24表/原全审计、历史保全/录像。配置无外网、pull_policy=never、浏览器route拦截或TOOL_ONLY输出均不替代实际断网。未知/未跑明确MISSING/NOT_RUN，root统一实际执行。

原frontend network可以保留为空，不清理原卷或历史。仅stop不移除项目的既有原件保留；恢复连接若确有需要须新明确transition+前后实际原件，不能借恢复改写验收结果。本修订不改变25/67原要求状态。
