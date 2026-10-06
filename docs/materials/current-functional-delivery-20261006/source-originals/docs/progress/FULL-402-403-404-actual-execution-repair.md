# FULL-402/403/404 原整组执行诊断与当前读取修订

状态：原编号仍 PENDING；本页不是完整版或初版全量验收。

原 W4 批次 actual-original-migration-regressions-and-whole-asset-execution-20261005T212158Z-0e45a93b 为 2 PASS/1 FAIL（58.60s），资产 prepare 返回 500；两个已通过迁移节点未重跑。11 源码与原失败逐字节档案保留于 .runtime/FULL-402-403-404-prepare500-before-20261005T213011Z-7f59758f/manifest.json。

同节点真实异常诊断 actual-whole-asset-prepare500-exception-diagnostic-20261005T213018Z-fb66ce14 FAILED/36.75s：原年度 fixture 的历史 NOW 2026-10-04T20:00Z 早于实际 seed 打开的 OPEN epoch，0013 元数据触发器按原门拒绝。未改 trigger/model/canonical/银行事实或历史。公开 middleware 仅返回 INTERNAL_ERROR；诊断包装调用原真实 prepare 后捕获并重抛原异常，没有模拟成功。

本节点在真实 seed 后取一个服务端 UTC 时间并固定 get_now；保留旧 actual_income 与 FullPolicy helper 的原银行/权限历史，未改共享 fixture/global NOW，不接收客户端时钟。本次实际节点 actual-whole-asset-current-open-clock-20261005T213247Z-dadd11d5 完整通过 1 项/2826.45s，wrapper 2832.061991s，log SHA ec40cd1ecbed3383a63a1ff9ac3eb82fd671367dd4e960a26cf70929556e6528。完成原组合确认、缺 marker 两阶段拒绝、未确认/越批拒绝、真实银行提交后响应丢失、同原键 SETTLED 但应用无 receipt 的恢复、固定批重放不推进后批、后续显式批执行、全部原回执与最终 EXACT audit。

该运行期间 Root 修改 domain/services 的 full_execution_protection 两份实际相关源；旧 wrapper scope 未覆盖它们，scoped_source_stable=true 不能作为全部相关源冻结。完整原 manifest/source.before/source.after/log 保留，运行仅 DIAGNOSTIC_PASSED_WITH_RELATED_SOURCE_CHANGE，不改其原 PASSED 状态。固定可信当前 OPEN 时钟用法不证明历史时钟、动态时钟或真实十五分钟内完成。没有真实资金接口。

后续必要窄修仅三文件：

- full_asset_execution_store.read_full_asset_execution 在原 _read_snapshot 和 _now 后进入现有 audit_read_scope，私有计算、每批原 action/receipt/trace/consent/marker 校验不变。原机制仅 clean RRRO/同 session/同事务且全原行 digest 不变时复用审计核验；dirty/write 不复用，scope 退出清除，不缓存跨请求授权。
- test_full_asset_execution_integration 只修 FastAPI 类型收窄/原 prepare 显式 import，新增 now >= 唯一实际 owner OPEN opened_at、portfolio.prepared_at == now、原 expiry <= now+15min 断言。全部原负例保留。真实 bank 已 commit 后 TimeoutError 由原调用后故障注入并保原 public500 与原异常；没有假银行结果。
- test_full_asset_execution_service 新增四个 TOOL_ONLY scope 生命周期/异常退出/真实 Session.new 禁复用/无时区时钟拒绝检查。RO SQL admission 在纯 wiring 正例中显式 synthetic；不将 opaque 返回值称金融成功。真正 RO/原地 JSON 篡改复用风险仍使用既有 test_audit_request_read_scope 的真实 PG 节点。

变更前 19 项逐字节归档 .runtime/FULL-asset-reader-scope-before-20261005T222222Z-edb84a4d/manifest.json SHA ad2340886c612d14f1871d1208a8ca375163d4edc74268d13d8fb705f232a103。第一次档案错误使用不存在测试名，partial 文件与 PARTIAL-HANDOFF.json 保留，不冒充完整档案。

本轮相关四模块 53 PASS/77.33s（W4 asset-reader-one-request-direct-20261005T222413Z-e481831e）。首轮 Ruff/format 错误与原源保留；修正仅排版且 AST 不变。最终四新风险 4 PASS/4 deselected/1.89s（W4 asset-reader-final-four-scope-risks-20261005T222730Z-95a096a6），三文件 strict/Ruff/format 全 exit0（W6 4947afd4/3cf2b546/b361f80f）。pytest cache 写入 ACL warning 保留，没有掩盖测试失败。4 是 53 的子集，不累加为57。

未覆盖：本窄修最终完整金融节点尚未重跑；原真实 audit scope 篡改风险尚未在本新增 consumer 接线后复验；Root605新生命周期分支需独立真实候选；动态时钟/大历史性能/四个业务批次或所有产品期限的普遍可执行性/独立经济效果实验/真实银行资金均未证明。最终金融链由 Root 统一冻结全部实际依赖并安排，本 agent 不自动新增 PG。
