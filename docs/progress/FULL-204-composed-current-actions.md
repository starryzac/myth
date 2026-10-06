# FULL-204 当前周期划款与原动作集合组合交付

记录时间：2026-10-06 10:50 北京时间。执行修订二仍有效，功能交付优先；本包不关闭 FULL-204，也不替代两版集中验收。

可运行入口：`GET /api/v1/boundary/periodic-action-producers/current` 与 `GET /api/v1/boundary/composed-action-set/current`，均在真实当前 owner/OPEN epoch 的 RRRO 中读取。策略中心原动作集合面板提供“核对周期划款与当前动作”按钮；只有用户点击后才读组合接口，刷新失败时隐藏先前完整结论。

新协议 `full-policy-action-set-boundary-composed-v3` 完整内嵌原 actual-v2 输入、输出和各自哈希。只有原全周期策略、关系、命令、确认、付款事实分母完整，且映射到唯一同笔原付款候选时，才替换那一个候选。未确认的新 ASK 动作不继承旧同意。缺失、在途、冲突、多重映射或来源未知继续 UNKNOWN；Recovery、Release、Joint 未适配项继续显式保留。新的组合快照不改原历史哈希、不授予银行权限，不跨请求缓存授权。

直接证据：8 项组合数学/替换/未知风险 `W3/composed-periodic-original-and-remaining-unknown-risk-20261006T022623Z-a5172a07`，17.81 秒 PASS；4 源 strict `9d4713a7` 与静态 `cce2369f` PASS。原 Main/deps 两文件 strict `2533cf61` PASS；实际 OpenAPI 生成 `03a4bb00` PASS。

Web 的 18 项原件/哈希/候选映射读入风险与 2 项实际本地 HTTP 调用/失败隐藏风险，共 20 项 `W3/composed-current-actual-get-and-hidden-stale-ui-risk-20261006T024108Z-9f2a2115` PASS，3.55 秒。当前整体 Web 类型 `W7/current-risk-and-composed-web-types-fixed-20261006T024102Z-b819f4f1` PASS；四受影响文件 lint `7571e0c1` PASS。旧类型九处错误 `9ba72441`、三个未使用变量 `ad6204c8` 和首静态错误 `3c137a77` 保留；仅窄修类型和导入/格式，不放宽风险判断。Web 夹具来自纯数学构造，明确 SYNTHETIC DOMAIN RESPONSE ONLY，不能作为银行、PG、审计或浏览器事实。

具体未覆盖：周期 producer 唯一真实 PG 候选仅 collected，尚未运行；组合 v3 的真实当前 PG/浏览器完整回读未运行；组合通知为 `NOT_IMPLEMENTED_FOR_COMPOSED_V3`，不冒用原 actual-v2 通知协议。完整 Recovery/Release/Joint 家族、T1/到期/部分/有损赎回、真实性能仍未覆盖。原 actual-v2 本体定向实际通过 `5295d9e7` 是独立证据；通知与 35 表证据图的串行真实验证 `2c8fd940` 当前 RUNNING，不能称 PASS。

原失败、原算法/输入/结果哈希、正式模拟历史均保留；正式关闭仍 21/92，FULL 逐项 PENDING。真人 0/NOT_STARTED；两版集中验收 NOT_RUN。
