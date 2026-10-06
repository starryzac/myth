# FULL-606 本地身份面板差量

功能已实现；FULL-606 原验收仍 PENDING。本包只交付本地签名会话的前端显示与手动登录、注销、只读核对，不授权金融动作。

## 可运行能力与接入

新增 `api/local-actor.ts` 严格读取实际 Python DTO；`LocalActorSessionPanel` 无必需 props，可传 `mutationBlocked` 阻止登录/注销，自己的 GET 核对始终可达。Root 负责顶栏与跨族写入门接线。后端注册与真实生成 DTO 别名仍待 Root 完成，当前类型逐字段来自已冻结的真实 Python 合同。

登录仅发送固定 `bounded-user` 与用户输入的本地凭证，不接受角色、用户、金额或时钟。密码使用非受控输入，提交即清空；不存浏览器存储、React 状态、原响应或日志，不把服务错误正文回显。登录响应之后单独 GET 核对当前 Cookie 身份，POST 成功本身不展示身份。失效、网络错误、响应解析失败或注销后仍存在会话都清空当前 principal；不自动重试、不自动续期。未知写入只能手动 GET 核对后再决定下一步。到期计时只清空显示，不代替服务器验真。

GET 返回的 principal 只显示原用户、角色、签发、到期和会话 UUID。它不是金融权限缓存；银行权限、金融确认和真人核验保持 false。每个新金融请求仍应由服务器独立验证身份与原银行权限。

## 文件

- `apps/web/src/api/local-actor.ts`
- `apps/web/src/api/local-actor.test.ts`
- `apps/web/src/components/LocalActorSessionPanel.tsx`
- `apps/web/src/components/LocalActorSessionPanel.test.tsx`
- `apps/web/src/tests/local-actor-fixture.ts`：明确 TOOL_ONLY HTTP 合成夹具，不是实际凭证、浏览器或银行实证。

## 验证

现有 scoped runner 调用 Vitest 两个直接文件，42 PASS / 3.06s；原封套耗时 4.165221s，不混作测试耗时。终态：`evidence/W4/local-actor-ui-first-direct-20261005T201854Z-f7844b95/manifest.json`。覆盖严格 DTO/额外字段、期限、精确登录请求、401、网络丢失、禁止自动重发、凭证不回显/存储、单独 GET、注销后仍有会话和到期清空。

五源 ESLint 退出 0：`evidence/W4/local-actor-ui-first-static-20261005T201847Z-3825293d/manifest.json`。五源及依赖按仓库实际 strict TypeScript 参数检查退出 0：`evidence/W4/local-actor-ui-module-config-types-20261005T201932Z-1ed3165b/manifest.json`，封套 2.397004s。三次最终检查 source 与 scope 均稳定，不是全 Web 或版本全量验收。

首轮类型命令在仓库根目录解析类型库，产生 TS2688；原失败 `evidence/W4/local-actor-ui-first-scoped-types-20261005T201847Z-50ac0590` 完整保留。源未因此改写；修正为现有 `pnpm --filter @bounded-funds/web exec tsc` 模块入口后通过。

完整原 argv、退出码、源 before/after 与原日志 SHA 在以上封套中；不额外扩展验证工具。

## 未覆盖与下一前置

- 当前接口尚待主应用注册与 generated alias 校对，未配置、生成或提交任何真实本地凭证；401/404 不表示登录成功。
- 无实际浏览器 Cookie、HttpOnly、SameSite、HTTPS Secure 或真实数据库 User 端到端证据；界面文字仅说明服务器合同。
- 注销删除 Cookie，不证明另外持有的 Bearer 全局撤销；其可能存续至原期限。
- 原 MVP 全部路由尚未整体转换为角色权限；身份面板不补造此能力。
- 无真人身份/研究参与证明，也没有真实资金接口。
- Root 后续接顶栏和跨族门，生成 schema 后窄校对别名；最终集中进行真实链与全量验收。


## 2026-10-06 04:30 Root增量

Root已将本地身份router注册Main并接App展示面板，尚未配置真实凭证或取得真实角色数据库/浏览器结果。Main当前Schema正在生成；身份显示不授金融权限。

## 后续真实 Schema 接入（2026-10-06）

Root 已注册真实 localActor 路由并生成 OpenAPI（W6/current-local-actor-and-observation-openapi-20261005T202914Z-7c55e58f）；本 UI 类型随后改用 generated LocalSessionResponse，运行行为未变。原42合成检查保留；本批实际 strict 模块类型包含该 alias/面板/原测试夹具。Root 报告已配置本地 USER 凭证并接顶栏，本文不读取、生成或声称已实测登录凭证/Cookie；当前实际浏览器验收仍未由本包执行。最初交付时的未配置/未注册状态作为历史记录保留。

## Root实际身份与共享读取接入（2026-10-06 05:09）

真实Main已注册/local-actor并于App顶栏展示，生成实际Schema；ignored .env已有CSPRNG用户secret与独立signing key，私有用户凭证仅保存`.runtime/private/local-actor-user-credential.txt`，不进入日志、合同或Git。原较早NOT_CONFIGURED是原时点记录，不能代替当前配置。Local签名仍只有USER、真人未核验、金融必须另明确确认；Cookie注销仅删除当前cookie，不声称撤销已持有Bearer。

实际PG首混合1PASS/1FAIL保留，身份夹具仅恢复Cookie domain窄修后单节点1PASS8.48s/全物理零写、范围与全源稳定。证据`evidence/W4/actual-local-owner-cookie-domain-repair-20261005T210101Z-75f86e0c`。真实浏览器及606金融尚待。Root父页面40PASS13.41s（`evidence/W6/root-local-identity-cash-parent-related-ui-20261005T210813Z-5dcf8897`），包含未知现金回拨时登录/注销禁用而身份GET可达；不是金融或浏览器验收。
