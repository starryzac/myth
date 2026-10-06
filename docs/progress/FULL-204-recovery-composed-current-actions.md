# FULL-204 周期与整仓恢复动作组合

记录时间：2026-10-06 11:38 北京时间，按功能优先修订二交付。入口 `GET /api/v1/boundary/recovery-composed-action-set/current`，当前策略中心原动作集合中另有手动只读核对入口。新协议 `full-policy-action-set-boundary-recovery-composed-v4`，完整保留原 composed-v3/actual-v2 结果和各自原哈希；不替换已有算法或历史记录。

服务在同一 RRRO 请求中捕获一次原实际库存，将该同一 capture 交给原周期和新恢复生产者。纯函数复算两族，要求 exact original 输入、owner、epoch、时点和原 input hash 全部匹配。整仓恢复仅完整证明 T0/原 ASK 有限族；只有完整且唯一、全部经济 Effect 字段匹配的同笔候选才替换旧候选。原未知动作、未证明的购买/在途恢复、归属释放、Joint 和其他未适配项仍可见且保持 UNKNOWN。旧配额和 16MiB 输入容量保持显式拒绝，不截断分母。

本次没有资金、权限或通知写入，`notification_support=NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4`。完整某一族不等于全局完整；合成 T0 示例包含 50,000 分 ASK 候选但原购买未知，所以整体仍 UNKNOWN。

已运行：`W3/exact-recovery-composed-v4-and-original-v3-risks-20261006T032850Z-366675df`，新10+原v3八项共18 direct PASS55.71s；4源 strict `db604ff8`、6源 Ruff `89877c44`、Main/deps 两源 strict `54bf461d` PASS。当前实际 API 合同生成 `c4eb4d7b` PASS。直接测试包括同一请求 capture 只读复用、原 v3 保全、T1/不完整分母/不同 capture 不能移除原候选；不是PG证明。

新 Web 读取器核对实际 generated DTO、原 v3、全部仓位/动作/策略分母、ASK、不授银行权限、完整组合映射和两个新摘要；原响应原文保留。初批 `d770ea8b` 17项中16通过，money 测试在调用生产 reader 前试图摘要非法浮点导致 FAILED；生产规范整数门未改，只改测试不摘要非法浮点后15 reader PASS `0b33d2d8`，两项面板PASS复用原初批。初整体类型 `c600f969` FAILED3，为 generated 有默认值字段的 optional 类型；运行时已要求字段存在，随后将验证后结果用 Required 显式细化，不以默认空数组掩盖缺字段。最新整体类型/静态和当前冻结清单后续补记。

具体未覆盖：当前组合实际 PG/全部原表零写、原历史trace消费者、v4通知订阅、浏览器均 NOT_RUN或未实现。T1、到期、部分、有损和多仓原子执行没有升级为支持；Release、Joint 仍在独立功能交付。FULL-204 仍 PENDING，正式21/92，原失败和正式模拟历史保持。


2026-10-06 11:58 北京时间：功能优先继续。恢复组合 v4 新增 POST /boundary/recovery-composed-action-set/observe 和 GET /observations/{run_id}，只写原 decision/audit metadata、不写金融或授权、不生产通知。完整输入/来源/父快照冻结；原请求 key 回读先于新捕获，真实父链逐项回读；UNKNOWN 不产穿越。10 纯冻结/篡改风险 8b5f4803 PASS82.62s，2 HTTP owner/clock/注入风险 60baee64 PASS9.96s，4 strict9f5f92f5、API-test strict b4a05ce6、6静态 b60ff952 PASS；实际 generated contract a0c60742 PASS。新增真实PG候选只有 collection，执行未开始；前端持久请求/历史回读功能正实现。原目前只读 recovery Web 15 reader +2 UI通过，整体类型2f8d8134/7静态76cc929b已通过。604下一整仓v2 Web 24风险/5静态/独立types通过，Root真实宿主两门2风险70788a82通过；新版服务器 chooser+原v1 workspace已可调用，真实下一仓PG/浏览器未跑。

唯一金融 session95780 原通知+图谱仍RUNNING、无终态；启动源码610件及原日志保存，当前共享功能已变，本次仅诊断，不作当前源码验收。成熟本金用户确认执行、Release producer、805真实GENERAL机制选择独立包在实现；Root待稳定合同才集成共享银行/曝光/历史算法接缝。没有金融PG并行、正式库reset/migrate、旧失败/历史hash修改或跨请求授权缓存。正式关闭仍21/92，FULL逐项PENDING；真人0、实际七机制/八消融0、未测指标NULL，最后集中验收未运行。
