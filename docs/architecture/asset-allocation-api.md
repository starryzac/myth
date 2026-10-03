# 资产配置预览 API

`GET /api/v1/asset-policies/{policy_id}/allocation-preview`：在服务器一致快照上读取当前策略及授权，比较有限产品候选。无查询参数；客户端不能指定时间、余额、管理占用、目标归属或申购金额。用户身份来自当前模拟用户依赖，外部用户策略和不存在的策略返回404。

响应包含 simulation、user_id、policy_id、as_of、allocation、source_evidence_ids、input_digest、source_issues。allocation 包含当前policy/version/scope、产品拒绝原因、财务及授权限额、退出日期、净模拟收益、最多一个选中产品及保留现金、来源账户用量、基线/预留调整/候选边界和selection_hash。

`READY` 只说明本次快照在所列条件下有可解释候选；选中CASH时suggested_cents=0，不产生买入动作。来源不足时相关精确推荐和额度为空；当前策略失效关闭新购。所有结果带 preview_only/financial_only，不是AUTO_EXECUTE或执行回执。

目标配置使用既有流程：确认目标策略并创建零归属目标；确认引用该goal_id的资产策略；确认修改目标策略，将asset_policy_id指向该资产授权。两条当前确认链及双向引用匹配后，才允许从这个目标已有归属现金内规划，不因创建或绑定授权认领账户旧钱。

同一未改变快照反复GET应返回相同selection_hash且全16表不写。资金划转、来源消费、预留提交、持仓及回执由MVP-301执行并重验；UI自主等级在302，业务页面在401–404。

实现依据：[ADR0007](../adr/0007-single-product-allocation.md)。当前验证进度见 [MVP-204](../progress/MVP-204.md)，本文不是已完成验收声明。
