# FULL-606 同 USER 会话续登录

功能差量：LocalActorSessionPanel 新增可选 allowSameUserReauthentication（默认 false）。金融 pending 阻挡时，只有手动服务器 GET 已确认 USER 或会话不存在，或该 USER 已显示到期，才允许用户明确登录服务器固定 bounded-user。NOT_READ/UNKNOWN/身份写入待 GET 核对仍不允许；当前其他角色不能走有限续登录入口。注销继续受 mutationBlocked 阻挡。实际写请求由原 HTTP 写门持有，组件只检查当前 flight 并订阅，未新增权限缓存、自动登录、角色选择或公共 token 存储。

Root App 需显式启用有限 prop；其 pending 金融原件继续保留。17 个组件 HTTP 夹具检查通过，真实模块类型及两源 ESLint 通过；这是单元检查，未证明实际 HttpOnly Cookie 到期/续登录/UNKNOWN 银行恢复或真人身份。精确 source、scope/global 字段和原检查路径见 FINAL。

首次候选错误地在已经持有写门的公共 HTTP 入口外再加锁，10 PASS/7 FAIL 的原检查保留：evidence/W4/same-user-financial-pending-renewal-direct-20261005T214135Z-19f1143d。随后删除重复锁，保留 handler 当前 flight 门与订阅；原两处 test nullable 类型 RED 保留。最终 PASS 是新 run，不改原失败记录。
