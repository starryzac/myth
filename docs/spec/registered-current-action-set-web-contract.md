# 当前登记动作集合 v5：只读客户端

2026-10-06，显式产品消费增量。原 FULL-204 仍 PENDING。

`RegisteredActionSetPanel` 是独立默认导出，宿主由 Root 接线。它不接受用户、权限、金额或客户端时钟。首次挂载没有请求，用户点击“刷新当前登记动作集合”才请求 `GET /api/v1/boundary/registered-action-set/current`。它没有 POST、观察、通知或金融执行路径，因此其它族的写门不应阻挡该 GET。

`getRegisteredActionSet()` 使用实际生成的 `RegisteredActionSetSnapshot`。`parseRegisteredActionSet(value, originalText)` 核对 supplied object 与原文本、模拟环境及全部无授权标记，复用冻结的 v4 reader 保留 v2/v3/v4 原快照。Release/Joint 以各自原 DTO 收窄，核对用户、周期、时点、键、UUID、整数金额、来源/动作分母、原未决动作、当前来源问题、严格结果 hash。客户端按原组合规则重建新增、精确 shadow 替换、UNKNOWN 回退、候选分母、未支持项及最终 snapshot hash；Joint 预期目标还必须等于原 Actual dynamic goal 键。Release 纳入只允许原 `ASK_ONCE` 与新的明确 USER 确认标记。Joint 不同分配量仍明确未实现。

完整性仅表示服务端登记的生产者范围，不表示全应用、任意手动意图、完整物理数据库或银行权限。页面逐项显示付款、恢复、归属释放、目标联动分母，所有候选/缺项/未决原动作和不足原因，保留 actual `table_coverage` 的实际 count/hash。生产捕获是 24 个必要业务表及 3 个辅助消息表；不将这些 27 表冒充物理全表。完整原响应及 v4、Release、Joint 的原 hash 可展开核对。

刷新期间隐藏缓存的旧结论；失败刷新显示错误并隐藏旧完整结果。每个 GET 重新从 API 取得原件，没有客户端或跨请求授权缓存。`originalRegisteredActionSet(response)` 仅返回该响应的原文本，不能授予权限。

## 可证明边界

客户端没有完整输入、银行账本或独立审计原件，不能独立重算服务端资金安全、Release 全部配置的笛卡尔分母或 Joint 金融解。它核对响应内的明确分母和组合一致性，不把自洽 hash 当成银行验真。实际来源完整性/金额/许可由当前只读生产 API 负责；缺来源与截断必须返回 UNKNOWN，前端不能补 0 或隐藏缺项。

新增 JSON 夹具是 TOOL_ONLY：来自现有纯 domain 风险夹具，显式加入三张空辅助消息表后重算，包含完整零 Release/Joint、精确 Joint 替换、缺 Joint 保留旧候选、缺 Release 来源及非空 Release 排除。其值没有经过 PostgreSQL、银行、浏览器或真人研究。

## 本模块检查

- 30/30 Vitest：27 reader 风险、3 交互风险，`W6/registered-action-set-current-web-direct-repaired-20261006T051318Z-7e6539bd`，exit 0、scope/global 稳定。
- 5 TS/TSX 源 ESLint，`W6/registered-action-set-current-web-static-repaired-20261006T051312Z-5a01393a`，exit 0、scope/global 稳定。
- 原 wholeWeb 类型 FAILED `b79269f3` 保留；本模块已用真实类型 overload 修正其 Release/Joint union 诊断。最终 wholeWeb types 待 Root 宿主接线后统一运行，不能以 Vitest/ESLint 称类型检查通过。
- 本模块未跑 PG、真实浏览器或 FULL 全量，未关闭编号。
