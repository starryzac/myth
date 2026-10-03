# 生活准备金估算接口

`GET /api/v1/living-reserve/estimate` 在一个 REPEATABLE READ 快照内读取模拟用户事实，返回可复核的生活准备金估算。不会创建策略、候选、决策或资金动作。

参数：`lookback_days=56`、`horizon_days=14`、`quantile=0.8`、`extra_buffer_cents=50000`、`exclude_one_off=true`；`essential_categories` 可重复传入，默认 food、transport、daily_necessities。历史和窗口最长 366 天，窗口不得长于历史，金额为非负整数分。多余参数（包括 user_id、as_of、complete）返回 422；用户、时钟和覆盖证明由服务端读取。

响应包含 simulation、user_id、as_of、estimation、source_evidence_ids、input_digest、source_issues、candidate_configuration 和 candidate_configuration_hash。estimation 提供完整本地日区间、每天金额、全部重叠窗口、精确分位数排名、排除原因与覆盖缺口。READY 表示本次输入足够估算，不是执行授权。INSUFFICIENT_HISTORY 时基础值、建议值、候选配置和配置摘要为空。

前端后续可将配置展示给用户复核；第一次明确确认前不能转为硬约束。原始 JSON 金额遵循后端 signed BIGINT 范围，业务界面接入时仍须处理 JavaScript 超出安全整数范围的精度问题。

真实临时 PostgreSQL 的 HTTP 测试保存了接口实现前的 404，以及实现后的三组通过结果：默认独立算例 77900 + 50000 = 127900 分，重复请求完全相同；仅 food 类得到 57400 分；失效覆盖不返回零或仅缓冲建议。所有请求前后比较完整 16 表快照，确认无写入。见 `docs/progress/evidence/MVP-201-api-red.txt` 与 `MVP-201-api-integration-first.txt`。这些是合成数据计算验证，不是用户消费实证。
