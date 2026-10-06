# FULL-606：本地身份会话

2026-10-06 04:13 北京时间；功能差量已实现，正式 FULL-606 仍 PENDING。

服务器固定将本地 `bounded-user` 凭证映射为 USER，签发最长 15 分钟的 HMAC 会话。客户端不能提交角色、用户身份、金额、时钟或银行事实。新收款人关系必须由当前服务器验真的 USER 发起与明确确认，身份凭证自身不授予资金权限。

## 交付

- `domain/local_actor_session_types.py`：严格角色、owner、会话期限与当前 USER 检查。
- `services/local_actor_sessions.py`：每次读取当前配置、验证签名和 owner/期限；未配置、弱配置、篡改、冲突凭证拒绝。
- `api/v1/local_actor_sessions.py`：`POST /local-actor/login`、`GET /local-actor/session`、`POST /local-actor/logout`；HttpOnly、SameSite=Strict cookie，HTTPS 使用 Secure；响应 no-store，没有 token/密钥或资金授权。
- 两个直接测试文件：真实签名和真实 FastAPI 路由；用户依赖和时钟明确为合成夹具，不作为实际数据库或真人研究证据。

## 运行记录

现有 scoped runner 执行两个测试文件：41 PASS/2.23s，原日志与终态见 `evidence/W4/local-actor-sessions-final-direct-20261005T201313Z-ba4f9bcb`。五源 strict mypy 与 Ruff 均 PASS，分别见 `local-actor-sessions-final-types-20261005T201314Z-ee17950d`、`local-actor-sessions-final-lint-20261005T201314Z-a2846c68`，范围来源稳定。

首次静态错误（未使用 import、测试专用 `_env_file` 构造参数）日志与精确五源保留在 `.runtime/local-actor-first-static-red-20261005T2011Z`。最终五源冻结为 `.runtime/FULL-606-local-actor-final-20261005T2013Z/manifest.json`。没有扩展新的验证工具。

## 配置和限制

环境变量 `BF_LOCAL_USER_SECRET` 至少 24 个 UTF-8 字节，`BF_LOCAL_SIGNING_KEY` 必须为独立、非零的 32 字节密钥（64 个十六进制字符）。缺少配置时拒绝登录。当前尚未配置实际凭证；主应用注册、真实 DemoUser 数据库依赖、前端与周期付款银行接缝待接。

登出删除浏览器 cookie；已另行持有的 Bearer 签名会话可存续至其原到期时间。服务器角色会话并不核验真人身份，`human_identity_verified=false`，不能作为访谈或研究参与证据。现有 MVP 路由未整体迁移到角色权限；新周期付款专用入口独立消费此服务器 principal。

下一前置：Root 在唯一金融批退出后注册路由，接 FULL-606 专用关系与银行锁内约束，再检查相关真实链；最后集中验收。


## 2026-10-06 04:30 Root增量

Root已将本地身份router注册Main并接App展示面板，尚未配置真实凭证或取得真实角色数据库/浏览器结果。Main当前Schema正在生成；身份显示不授金融权限。

## Root实际身份与共享读取接入（2026-10-06 05:09）

真实Main已注册/local-actor并于App顶栏展示，生成实际Schema；ignored .env已有CSPRNG用户secret与独立signing key，私有用户凭证仅保存`.runtime/private/local-actor-user-credential.txt`，不进入日志、合同或Git。原较早NOT_CONFIGURED是原时点记录，不能代替当前配置。Local签名仍只有USER、真人未核验、金融必须另明确确认；Cookie注销仅删除当前cookie，不声称撤销已持有Bearer。

实际PG首混合1PASS/1FAIL保留，身份夹具仅恢复Cookie domain窄修后单节点1PASS8.48s/全物理零写、范围与全源稳定。证据`evidence/W4/actual-local-owner-cookie-domain-repair-20261005T210101Z-75f86e0c`。真实浏览器及606金融尚待。Root父页面40PASS13.41s（`evidence/W6/root-local-identity-cash-parent-related-ui-20261005T210813Z-5dcf8897`），包含未知现金回拨时登录/注销禁用而身份GET可达；不是金融或浏览器验收。
