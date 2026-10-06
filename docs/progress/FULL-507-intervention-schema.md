# FULL-507 独立消息持久底座

功能优先修订二下的增量；原 FULL-507 仍 PENDING。Root负责共享两模型与0012迁移，独立生产者/前端另见相应记录，不从新增两张表直接关闭编号。

`InterventionOutbox` 保留原来源run/hash、原问题身份、不可变payload/hash与用户/epoch语义唯一键；`InterventionInbox` 只有固定消费者唯一claim与显式收阅原键/原请求/原回执。原金融 CommandOutbox/Inbox 与其九类资金协议不改。收阅不回答、不确认行动、不授资金权限。Inbox的原ack key按同user全通知命令命名空间唯一，不凭跨epoch键清理另一个消息。

0012只新增两个表、索引、owned FK和guard。payload/source/归属/创建时点不可改；状态仅限定前进，终态原件不可变；拒绝DELETE/TRUNCATE；已有消息时拒绝downgrade。新消息源run不建阻止原归档的FK，保留audit epoch引用。既有历史哈希、canonical文本与旧迁移未改。

实际检查：W3/actual-full-floor-bank-boundary-and-retained-intervention-schema-20261005T174905Z-1f66a41d 三节点全PASS114.19s（wrapper117.129229s），范围source稳定；global仅独立新功能源变化。第一节点真实0011→0012：所有旧物理行逐行一致，只新两表空，metadata完全匹配；空表回退后旧行一致；实际claim/ack写入后篡改payload/语义/消费者/hash、缺ack原件、DELETE/TRUNCATE、终态改回、改回执及带历史降级均拒绝，拒绝前后所有实际物理表不变。这是迁移约束夹具，不是产品问题生产或真人看过证明。

Root五源strict PASS eb364921，最终七Root源Ruff PASS4fcbbed0；首次生成DDL的17条行长失败27ccfbac与当时新迁移/测试副本保留在`.runtime/intervention-schema-static-first-failed-20261005T1746Z`，只换SQL空白和测试JSON比较形式。Main新注册的单条import排序失败cfca853d保留后窄修。正式模拟库未迁移/重置。

未覆盖：自动生产hook、实际消息生命周期/丢回复/原键恢复、reset后封存源解析、原archive manifest对通知保全的专用覆盖、真实浏览器一次呈现观察及全部11介入原因。消息首次claim只证明运输元数据，无法证明用户实际看到；不补造分布式exactly-once。下一前置为生产者实际PG与原问答提交后绑定，再接持久介入中心与集中验收。
