# FULL-206 原决策边界差分

实现状态：原 MVP 金融安全闲置资金差分可运行；原 FULL 验收状态：PARTIAL，未关闭。按功能优先显式执行修订推进，本包没有执行资金、重置历史或生成新的实验工具。

## 可运行接口及实际来源

`POST /api/v1/boundary-differences/compare`，operationId `compare_original_boundary_runs`。严格请求只含 `before_run_id`、`after_run_id` 和有限 `boundary_field`（默认 `execution_boundary`）；不接受用户/时钟/原快照/余额/权限/结果/原因。根任务需注册 router 和 RR READ ONLY POST 依赖。客户端按实际决策 ID 比较，不向服务提交假设世界。

同一只读快照调用原 `get_decision_trace`，要求同实际用户、时序不倒置、原完整 trace/hash 与 typed audit 核验。再次核原嵌套字节内容哈希、原来源身份/等级/状态/业务时间窗口、现金/账单/义务结算/目标归属/月贡献/本金可用时间和原策略确切确认绑定。原金融输入必须严格符合已有 BoundarySnapshot/Version/Position/Product 结构；以原 `compute_boundary` 重算，结果必须逐字段等于原决策保存的 baseline/actual_boundary。缺原件、缺原算法结果、来源错误或重算不一致时 UNKNOWN，金额和差额为 null。当前证据状态后来变化不替换旧决策原件，也不重新授权历史动作。

响应保留前后原决策及边界哈希、原业务时点、安全闲置资金 X/Y 和整数差额、实际变化组件及原值/值哈希/原输入路径、原来源 ID/哈希。单组件变化明确列该组件；多组件变化只报告联合结果，不伪分配独立贡献。解释固定由确定性代码生成，不调用 LLM。行枚举顺序和来源 digest 单独变化不充当新金额事实；原字段和字节不改。同一请求比较同 run 时复用只读原件，下一请求重新查询核验，无跨请求权限缓存。

## 必要检查

新纯/API 风险与原 boundary 相关测试、严格 mypy 六文件、Ruff/format 日志保存在 `.runtime/FULL-206-difference`。首轮纯 28 PASS；原第一次类型检查三项错误（局部变量类型及测试 Optional 缩窄）和 format 前 Ruff 长行结果保留；修复后相关 80 PASS（3.21s），六文件严格类型/Ruff/format PASS。新增请求内复用与缺原件 UNKNOWN 风险后最终 **82 PASS（3.30s）**，严格 mypy 六文件/Ruff PASS；纯夹具不算真实金融实验。

唯一新增实际 PG 候选节点 `test_boundary_difference_integration.py::test_actual_confirmed_protection_delta_replays_historical_sources_and_never_writes`：真实原 prepare 前后、真实已确认应急保护额增加 5000 分，核历史原件重算差额 -5000、同 run 实算零、错误 owner 404、全部业务行零写。仅 collection 通过；真实隔离 PG 由根任务单链排程，当前 NOT_RUN。

## 未覆盖及依赖

当前 scope 为 `FINANCIAL_SAFE_IDLE_FUNDS_ORIGINAL_MVP`，不是五集合授权动作集合的完整差分，`autonomy_action_set_difference=NOT_EVALUATED`。FULL 新多目标/多资产/八规划模板金融执行差分、持久 FULL-204 边界事件、自动前后快照配对、前端入口/E2E 和全量验收尚未完成。

生活准备金使用原已审计决策保存的估计值及其 input digest，不重新声称已独立重跑全部历史支出估计；产品使用原决策保存的合同，不把当前可变 catalogue 或规划候选当授权。多变化不声称个别新事实的反事实金额贡献。仅有原 capture 且原结果可完整重放的字段能够返回 RECOMPUTED，其余 selector 缺原 baseline 保留 UNKNOWN。这些边界不能以复用旧文件直接关闭 FULL-206。

## 根任务实际 PG 追加

本节点实际 PASS，原 manifest 为 `evidence/W4/actual-recovery-original-goal-key-and-historical-boundary-readonly-real-pg-20261005T150011Z-7e0c77f9/manifest.json`。其四节点混合批次总结果 **FAILED（1 FAIL / 3 PASS，118.09s；wrapper 120.307731s）**，不能把原批次改成 PASSED，也没有独立本节点耗时可报告。唯一失败为根任务其它 FullGoal lookup 审计写入时钟与业务时钟比较，与本六源无关。

本节点真实验证原已确认保护额增加 5000 分、原历史决策/源重新校验与重算 -5000 分、同 run 原算零、错误 owner 拒绝、只读全行零写。scoped_source_stable=true；all_source_stable=false，仅独立 finite_uncertainty/引导前端新源变化，不能称全仓冻结。本六源 HOLD 已释放。原验收状态仍 PARTIAL，不把这个有限金融差分节点当全 FULL 范围验收。
