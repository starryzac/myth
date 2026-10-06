# FULL-604：原恢复动作的本地 USER API 消费入口

状态：API_IMPLEMENTED / SYNTHETIC_ROUTE_CHECKED / FINANCIAL_INTEGRATION_NOT_RUN / FULL-604 PENDING。

新增 `apps/api/app/api/v1/full_recovery_execution.py`，只委派已经实现的 `app.services.full_recovery_execution`，不修改共享执行、银行、审计、Main、依赖或正式历史。接口没有客户端金额、行情、clock、quote、receipt、permission 输入。

| 实际路由 | 原严格请求/响应 |
|---|---|
| POST `/api/v1/full-recovery-actions/preview` | 五字段 FullRecoveryPrepareRequest → FullRecoveryExecutionPreview；当前只读计划/原 scope proof/可为空 execution_effect |
| POST `/prepare` | 同五字段 → 原 ActionResponse；固定原 key/epoch/policy/version/position，当前原守卫未装时拒绝，不退回旧规划 |
| GET `/by-key/{idempotency_key:path}` | ≤120 非空完整原键，支持编码斜线；FullRecoveryExecutionLookup 保原完整 body、client/server hash、Action、epoch、USER consent；NOT_FOUND_NOT_FINAL 不证明失败或可换键 |
| POST `/actions/{action_id}/confirm` | expected_epoch_id、reviewed_effect_hash、严格 accepted=true；真实 USER 逐次同意，由原服务保存独立 consent 原件 |
| POST `/actions/{action_id}/execute` | expected_epoch_id、reviewed_effect_hash；沿固定原 action 恢复，原 service 重核 scope/consent/银行原 key，不创建替代动作 |

每条路由拒绝额外 query，原请求模型拒绝额外字段并保 strict money/bool。全部路由包括只读 preview/lookup 都要求既有签名 local USER 会话、同服务器用户和当前有效窗；没有登录为401，角色/owner/有效窗不符为403。身份不是资金授权，原生产服务与独立银行每次继续重验。FastAPI 的同请求依赖复用不保存跨请求授权结论。

Root 接缝：注册新 router；将 POST preview 与 GET by-key 加入原 `SessionDependency` 的 REPEATABLE READ / READ ONLY 路径；然后从真实 Main 生成 OpenAPI/TS。Main/deps/contracts 不属于本包，本包没有自行注册或伪造 generated alias。写 endpoint 只通过原 engine/service 控制事务。

实际范围仍是原当前已确认 FULL RecoveryPolicy 与 linked asset 权限共同支持的**整仓、零 fee/loss、T0/T1、同 scope、真实准时窗口**。到期需先原结清/核对，组合/部分/有损未实现，不将 proof 当银行资金授权。预览假设身份不是准备动作身份；prepare 仍按原 pipeline 实时核事实/hash。即时缺口窗口在后续墙钟可能已不能准时到达，必须如实返回风险，不延长截止点。

## 本次最小验证

九个真实 FastAPI JSON/严格路由风险检查 PASS（3.85s），全部使用显式 synthetic doubles，无 SQL/银行执行：

`docs/progress/evidence/W5/full604-current-user-json-routes-20261005T234457Z-f54f316c`。

覆盖合法字符串 UUID → 原 DTO；三个写入口 exact原body/USER/engine 委派；非法金额/clock/query/accepted拒绝；完整编码原键和 non-final无记录；缺身份401；wrong-role/wrong-owner/expired403。写 stub 明确409 sentinel，没有伪造成功金融回执。

首次 strict 类型失败原件为 `full604-current-user-api-types-20261005T234457Z-e3ca5826`，原 source 归档 `.runtime/FULL-604-api-first-types-before-20261005T234555Z/`。仅测试 NOW/USER 的真实原定义 import 和 tuple 类型注解窄修，没有改变生产 route/请求/fixture/断言，所以复用上述九节点行为证据。最终二源 strict 类型、Ruff实际 exit0：`full604-current-user-api-types-repaired-20261005T234616Z-e5833ae6`、`full604-current-user-api-static-repaired-20261005T234616Z-169551a5`。这些证明 route/parser 能运行，不是真实 PostgreSQL、浏览器、守卫已装或 FULL 验收。

## 具体未覆盖

Root Main/RRRO/真实 Schema 接入、完整 Web reader/原请求持久门/显式确认执行 UI、真实本地会话→银行→UNKNOWN 原键恢复金融链、T1/到期/损失/组合/部分执行、正式 Full 全量验收尚未在本包完成。旧 service/adapter 风险及失败原件均原样保留，编号不关闭。


## 2026-10-06 09:15 Root 实际终态

2026-10-06 09:15 北京时间：唯一Root金融63872已真实终态PASS1/1104.83s，wrapper1108.651942s；W4/actual-signed-user-t0-full-recovery-original-bank-key-20261006T005301Z-71383f32，相关scope稳定true/global仅新独立203/706/history变化false。实际签名USER原T0整仓零费用零损失、ASK确认、银行受理丢响应、撤销后同原key恢复及重复全物理零写通过；不证明T1/现实墙钟即时/到期/部分/有损/组合。当前无Root金融RUNNING，共享HOLD解除供Root必要集成；旧失败不改，正式21/92及FULL PENDING不变。下一接日期历史显式新算法，再原105 failed-node与实际新204/102节点串行。
