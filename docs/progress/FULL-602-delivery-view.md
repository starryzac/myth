# FULL-602 原命令投递视图增量

2026-10-05。`READONLY_FUNCTION_IMPLEMENTED / ACCEPTANCE_PENDING`。原 FULL-602、FULL-703、FULL-706 均不因此关闭，追踪表与正式模拟历史未改。依用户“功能优先、最后集中验收”的显式执行修订推进。

原完整计划 `钱途有界_完整开发计划_Codex执行版.md:1336–1338` 要求持久Outbox/Inbox及写入、提交、发送、接收边界故障注入、重启恢复和单次副作用证明。此增量只交付该功能的真实只读前端消费者；后端实现与PG证据由父任务独立负责。

## 可运行页面与边界

`DeliveryPage` 无参数，父任务可接入 `#delivery`。真实 `GET /api/v1/delivery?limit=50` 显示原持久消息列表，点击原消息只调用 `GET /api/v1/delivery/{outbox_id}`。API模块没有 POST、enqueue、deliver 或新动作创建入口；页面没有资金写按钮、自动重试或后台投递。服务错误只保留选定消息身份，下一次由用户明确只读核对同一原消息。

显示原outbox/root/action/epoch、payload哈希、Outbox/Inbox、原动作和银行当前状态、阻止原因、繁忙状态、原错误及所有连续编号的尝试历史。列表最多50条，接口没有总量或分页，不宣称全部历史；列表和详情分别查询，不伪称同一事务快照。归档消息继续展示历史，不重建旧动作。

`UNKNOWN`/`UNRESOLVED` 明确提醒银行可能已受理或结算，不能据此断言现金未变。WAITING_CONFIRMATION 明确必须原动作确认。历史 `SERVICE_RECEIPT_VERIFIED` 标记不代替当前 `service_receipt_verified`：两者不一致时显示警示；即使当前原服务报告核验通过，仍显示 `economic_verified=false`，不称独立资金验真。历史尝试的核验结果明确是该次结果。

当前 `DeliveryView` **没有**原请求hash、经济后果/effect hash、bank idempotency key；对应字段显示未提供，payload hash不冒充effect hash，也不调用另一个action接口拼出伪root。下一窄后台合同增量可以从原Outbox直接提供这些字段，前端届时再按真实原值显示。

reader校验模拟标志、无独立经济核验标志、五项原身份、root/action一致、连续尝试编号、唯一attempt、原结果身份、时间和具体回执状态。声明 `service_receipt_verified=true` 必须同时有当前原动作、SUCCEEDED/RECONCILED与银行SETTLED；这是响应一致性检查，不是独立核验银行。展示原服务结果不新增权限。

每次已通过reader的列表/详情响应以 `response.text()` 原文本留在本页内存中，前后读取不覆盖，关闭页面后不保留。其范围不包含传输层字节或错误响应的网络抓包；原文本不是验收归档。禁止结构共享将新响应文本误绑定到旧对象。本页不建立跨请求授权缓存。

## 实际模块检查

| 范围 | 结果 | 原manifest（docs/progress/evidence/W6/） |
|---|---|---|
| 页面/reader/fixture五文件ESLint | 退出0，全部源稳定 | `delivery-readonly-lint-20261005T131141Z-73fc575d/manifest.json` |
| 窄修reader后的project typecheck | 退出0，范围内源稳定；另full_goals.py由后台并行变更 | `delivery-readonly-types-r2-20261005T131259Z-708c8504/manifest.json` |
| 窄修reader的ESLint | 退出0，范围内源稳定；同上并行变化 | `delivery-readonly-reader-lint-r2-20261005T131300Z-cea260f1/manifest.json` |
| reader7项、页面5项Vitest | 12 PASS，退出0，Vitest3.30秒，wrapper5.141029秒，全部源稳定 | `delivery-readonly-vitest-r2-20261005T131335Z-32f71274/manifest.json` |

首Vitest `delivery-readonly-vitest-20261005T131207Z-98d3e277/manifest.json` 保持FAILED（11PASS/1FAIL）。原因是reader对类型允许的完整DTO错误比较非身份字段；归档原reader SHA `402bb3b4754032ab35bece4f2e4bd6633f4529d209f11ab6d5a82e4f58796de6` 后窄修为五项原身份比较，未弱化root/epoch/payload/attempt负例。原源和失败证据保存在 `.runtime/FULL-delivery-readonly-ui-20261005T130736Z/`。夹具均明确合成reader/组件测试，不能作为银行效果或持久队列运行证据。

## 具体未覆盖

手动再次投递、跨页面待核对写入门与恢复语义尚未接入；当前故意只读而非用超时自动重建动作。原request/effect/bankKey缺字段；外部资金锚点未验证；列表总量、分页、同事务读取和动作详情反向链接不在本接口范围。边界故障注入、服务重启恢复、单次副作用、真实PG/浏览器、最终初版与完整版全量节点均未由本批运行。本批无迁移/seed/真实资金接口操作。
