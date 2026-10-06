# FULL-304 专用紧急回拨授权与原件读取

2026-10-06 03:35更新：旧金融98461已退出0，专用授权公开路由、RRRO预览/原键GET及新审计算法已注册，真实OpenAPI生成通过。下方未注册/冻结为旧时点记录；本授权实际PG仍NOT_RUN。独立实际三腿服务与专用授权页面继续实现；原件读取新增32项风险与旧审计/对账共120定向PASS2.91s，6共享类型与Ruff通过，新历史投影精确核验分支仍待补。不能据此授金融金额或关闭FULL-304。

本包功能实施记录；FULL-304 仍 PENDING。普通 CrossGoalReallocationPolicy 的规划确认不会授予金融权限。专用确认只记录用户明确接受的范围，真实回拨执行事务正在另包实现，当前响应准确保留 `execution_support=NOT_IMPLEMENTED`。

## 可运行模块

- `domain/full_goal_release_authorization.py`：有限时窗、当前周期、策略版本及配置摘要、源目标完整模型及最低保障、具体紧急条件、单次与累计额度的严格合同。累计以同一策略身份所有版本计量。默认锁仅可由这个专用范围内的紧急授权覆盖。
- `services/full_goal_release_authorization.py`：完整当前审计及策略、源目标原件重查；只读范围预览；明确 accepted/审核范围哈希确认；不可变 Evidence 与 DecisionTrace 保存原请求；原周期、原键查询及撤销后的原件恢复。确认不创建 ActionPlan、BankOperation、银行腿或资金回执。
- `api/v1/full_goal_release_authorization.py`：POST policies/{id}/preview、POST policies/{id}/confirm、GET commands/{epoch}/by-key/{key}。请求仅身份、审核摘要及明确确认，不接受金额、银行结果或客户端时钟。
- `domain/full_goal_release_audit.py` 与 `services/full_goal_release_reader.py`：只识别新增 `goal_release_execution` 协议；核对原 Goal 的 MVP 版本外键、原服务请求和银行请求摘要、原现金来源证明、逐 allocationAction×fragment 的 releaseUses、完整三腿及原内部转账交易/回执。原银行经济事实验真不要求当前授权仍有效，不把返回 CASH 的原归属款变为新收入。

这些模块尚未注册到主应用；共享 Main/dependencies/audit/DecisionTrace 当前由正在运行的真实数据库批次冻结。只有解除冻结、注册新算法和路由、生成真实合同后才能作为主应用入口使用。

## 已运行的相关检查

- 专用范围严格合同 33 项：PASS（W3 `dedicated-goal-release-consent-direct-20261005T183413Z-47277a20`）。
- 507 原直接测试34项、公开 JSON 的身份解析及 strict 负例6项合并40项：PASS10.15s（W5 `intervention-and-release-public-json-dependency-repaired-20261005T184107Z-902f78ca`）。其中授权合法 JSON 只是实际 FastAPI 请求进入原 service 的显式替身；不能代替 PG。
- 授权3生产源、实际 PG 候选及相关公开 JSON 测试共6源 strict：PASS（W3 `dedicated-release-scope-and-public-json-repaired-types-20261005T184731Z-0c676c26`）。最终7源 Ruff PASS（`dedicated-release-consent-final-ruff-20261005T190349Z-3f462a57`）。
- 新原件读取身份26项：PASS0.98s（`dedicated-release-original-identity-direct-20261005T190349Z-667986d8`）；3源 strict/Ruff 最终 PASS（`43bcbc3a`、`a157b64a`）。这些使用显式合成原件，只能证明协议身份、摘要和篡改拒绝。
- 首次4个类型错误、公开 JSON 依赖夹具错误、后来测试类型问题及新读取器首次 lint/type 问题均保留原 FAILED manifest 和 `.runtime` 精确失败源码；只修对应错误，不覆盖失败日志。

## 尚未覆盖

- 真实 PG 节点 `test_actual_dedicated_permission_binds_scope_replays_after_revocation_and_changes_no_money` 尚未运行；公共生产路由尚未注册。
- 完整银行源现金残余、策略所有版本累计用量、独立接收前重查、真实三条腿、应用投影及 UNKNOWN 同键恢复正在开发。仅有专用声明不能证明金融金额安全或执行成功。
- 归档周期之后的授权来源解析尚未接入；不能声称正式重置后仍可从新表查到旧授权。
- 当前源证明对购买/支取后的现金与本金片段无法明确分割时保持 UNKNOWN；不补造 FIFO、份额分摊或新收入。
- FULL-304 全量验收、浏览器实际动作及最终92项集中验收未取得。

## 下一依赖

Root 当前唯一金融 session98461 退出并记录真实终态后，注册只读事务路由及 `full-goal-release-authorization-v1` 算法，生成真实 OpenAPI。随后由独立执行包复用本原件读取器，在单条隔离金融链上覆盖专用授权、撤销/版本变化、累计额度、银行三腿、无新增收入与原键恢复。
