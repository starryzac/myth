# FULL-301 / FULL-704：完整目标原确认查询

2026-10-05，功能增量已实现，原需求关闭仍 PENDING。新增 `services/full_goal_commands.py`、`api/v1/full_goal_commands.py`、实际风险测试；root注册主路由、提前RR只读事务与生成合同。没有迁移、金融写入、原 full_goals 服务修改或正式历史修改。

`GET /api/v1/goals/{goal_id}/full-model/commands/by-key/{key:path}` 按实际用户/目标/原完整确认键查询，返回唯一原模型证据、原完整规范化请求、request_hash、原版本回执和 audit_chain_verified。核模型来源、双配置/hash、原版本确认/编号、轮次身份及原审计链；后来的目标修改不能把原请求替换成当前模型。最大1,000个原模型，超限拒绝；额外用户/clock/query拒绝。保存前端候选需用服务器预览返回的规范化配置，服务明确 configuration_is_server_canonical=true，不宣称保存任意原始JSON书写字节。

`receipt_is_current_authority=false`、`bank_authority=false`。查询不重放确认；NOT_FOUND非终局，无法证明未知请求从未提交。审计 opened/sealed使用写入时钟，模型 confirmed_at使用原可信模拟业务时钟；分别核验，不能跨域相比较。专用模型审计和独立归档恢复未实现，epoch_archive_verified=false。重置后原Goal已不在当前表时不伪造归档模型。

## 实际检查与原失败

- 5文件 strict/types 与最终Ruff通过：`evidence/W2/full-goal-original-request-and-new-read-route-types-20261005T145931Z-d7d76ee2`、`...static-20261005T145931Z-39ff9356`；修clock后的3文件strict实际 `...audit-verification-actual-types-20261005T150443Z-d1e014e0`。新test单文件strict/Ruff `...existing-immutable-guard-types-20261005T150557Z-2fc653c5`、`...static-20261005T150557Z-e985fb63`。
- 最终隔离实际PG单节点 **1 PASS / 7.00s，wrapper8.776933s，all/scoped source stable=true**：`evidence/W2/full-goal-original-query-existing-immutable-guard-and-evidence-real-pg-20261005T150640Z-a6fd967c/manifest.json`。
- 实际核NOT_FOUND非终局/全表零写、斜线原键/规范化原body与回执、不同Goal404/用户query422、后续真实修改后原回执保持、原版本SQL改写守卫拒绝且不变、原证据hash篡改GET409且不修复。
- 首四节点批 `evidence/W4/actual-recovery-original-goal-key-and-historical-boundary-readonly-real-pg-20261005T150011Z-7e0c77f9` 保持 **FAILED / 3 PASS 1 FAIL / 118.09s**（scope稳定、全源仅无关新文件变化）。本查询错误比较审计写入与模拟业务时钟；其他恢复规划两节点和真实边界差分单节点已通过，不能把原manifest改成全过。
- 第二窄跑 `...full-goal-original-request-audit-clock-separation-real-pg-20261005T150507Z-6b5b0abb` 保持 **FAILED / 7.61s**：查询已成功，测试错误企图改写本就不可变的原 PolicyVersion。已改为断言数据库拒绝该改写，并增加可变证据hash篡改拒绝；没有禁用守卫或删除负例。原源保存在 `.runtime/W2-full-goal-command-original/`；首类型缺失导出/长度、后一次误用AuditVerification.complete失败均保留。

前端正式双hash确认与全局未知请求门尚待接入；没有浏览器/全量成功声明。后续需要真实终局拒绝合同、当前授权查询与实际页面确认/恢复验收，才能关闭 FULL301/704。
