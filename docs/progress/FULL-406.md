# FULL-406 到期后的当前策略重规划

2026-10-06 03:35更新：Root已接生产Main及POST RRRO依赖，真实OpenAPI/TS合同生成退出0；新实际PG候选仍NOT_RUN。下方由agent限定未改Main的记录为模块交付时点，不能当当前未注册。当前仍只读重规划、未消费reviewed决策hash，不自动续投；完整新决策/执行/回执验收缺口保留。

状态：`READONLY_CURRENT_REPLANNING_IMPLEMENTED`；FULL-406仍`PENDING`。原要求与验收见需求追踪表第81行、完整原计划1292–1294行：到期使用当前策略和边界重新配置，不沿用旧产品；保留到期前撤销、改策略、产品变更、新决策与回执、幂等到期事件的全项验收。

本批新增独立domain/service/router及两测试模块，不修改Main、依赖注册、原审计/执行/到期服务、正式事实、历史哈希、原失败或模拟历史。生产入口为 `POST /api/v1/full-maturity-replanning/preview`，只接受 `maturity_action_id/current_asset_policy_id/expected_epoch_id` 三个UUID；允许实际JSON字符串UUID，拒绝额外query、金额、客户端时钟、产品、结果、回执与权限。Root负责原REPEATABLE READ/READ ONLY会话注册与真实Schema生成。

读取原用户的ASSET_MATURITY行动、完整原持仓/请求/ActionReceipt/SimulatedBankRedemption/独立BankOperation，按原 `verify_recovery_receipt` 核验过去到期回执。原银行已经SETTLED而应用回执尚缺仍为UNKNOWN，不能假定本金未变；无观察结果不称终局。保存完整原行与canonical绑定hash，不改任何原件，也不把旧回执当当前授权。

已实收本金进入当前scope的全部已核财务事实，不新增为收入、不独占预留返还金额。重新读取当前策略版本、当前确认/有效性、原epoch审计、实际财务来源与当前已验证产品目录。原到期目标与当前策略目标/scope必须一致；撤销或当前确认无效阻止新候选。MVP调用原 `preview_asset_allocation` / `select_asset`；FULL调用原有限规划 `read_full_asset_allocation`，没有新银行消费者时仅PLANNING_ONLY。旧产品只能被当前引擎再次独立选中，不能由原购买记录自动续投。

MVP当前可行结果返回既存 `/api/v1/actions/prepare` 的意图候选：原 `purchase_asset`、当前policy_id及稳定idempotency_key，不接受或提交预览金额/产品。键由原到期BankOperation、当前epoch、策略和版本决定，同版本重复预览保持同键；新版本必须重新审阅，不能替换未知旧请求。原prepare会重新计算，未消费本预览decision hash，实际新金额/产品/effect必须再次核验。本端点不自动prepare、enqueue、执行或retry。

每次读取前后核原RRRO及无new/dirty/deleted，十二个明确引擎源的原字节SHA在本请求前后必须相同。source_hash保留原epoch/event/currentpolicy/财务/目录/证据/问题/engine输入，不称全仓冻结证明。所有资金/写入/自动续投权限旗为false，未来收入为0；`dedicated_decision_recorded=false`、`current_prepare_consumes_reviewed_decision_hash=false`真实保留。

实际模块验证（2026-10-05 UTC）：

| 范围 | 实际结果 | 原证据目录（docs/progress/evidence/W3） |
| --- | --- | --- |
| 本模块直接风险/FastAPI合成HTTP | 30 PASS，pytest7.16s / wrapper8.957044s，exit0 | full-maturity-replanning-final-direct-20261005T191801Z-4a57b8f8 |
| strict mypy五源 | exit0，5source无问题 | full-maturity-replanning-final-types-20261005T191802Z-2f51bb21 |
| Ruff五源 | exit0 | full-maturity-replanning-final-static-20261005T191802Z-04ee7a77 |
| 真实PG候选收集 | 1 collected，exit0；没有运行金融节点 | full-maturity-replanning-pg-candidate-collection-20261005T191112Z-06659319 |

三个最终check均 `scoped_source_stable=true` / `all_source_stable=true`。PG候选收集后只有新增域一致性负例及测试import修正，最终strict/Ruff覆盖当前五源；收集不是数据库或产品证明。首次direct27PASS/1FAIL、首次types/static失败与中间import排序失败均完整保留，后续check使用全新目录，不改旧状态。

直接风险包括：原完整行hash与当前配置scope一致性、撤销与权限无效、跨目标拒绝、当前版本/来源/目录不明、实际当前原域产品选择、FULL规划不能冒银行授权、稳定键及变版本、原principal不独占、SETTLED回执缺失UNKNOWN、异用户/非到期动作拒绝、三身份JSON与额外输入/query拒绝、过期epoch、RRRO与待写session门。FastAPI合法JSON测试调用原新service/domain和实际选择引擎；财务/回执/审计读取为显式SYNTHETIC doubles，不能把30PASS当真实金融效果。

具体未覆盖：

- 原计划到期前撤销/改策略/产品变更全部真实金融链、当前API注册后的隔离PG全表不变与浏览器尚NOT_RUN。已写单一真实PG候选 `test_actual_contract_return_after_revocation_is_not_current_rollover_authority`：原声明/确认/真实购买/撤销/合同到期/原recovery/原回执/只读重规划/同事件重放/全表前后相等；仅收集，尚未证明可通过。
- 新独立DecisionRun/决策trace、用户新effect确认、原prepare消费reviewed决策hash、实际新执行及新配置回执未接。当前返回的是可调用旧prepare的意图候选，不是旧回执授权下的自动重新投资。
- FULL当前AssetAuthorization确认仅规划，未接银行消费者；不将PLANNING_ONLY升级执行成功。跨目标默认关闭及专用回拨授权不能借用本端点。
- 新稳定键不自带当前结果丢失恢复UI或新资金权；所有真实执行仍须原公共写入门、原键查询/核对与新effect复核。
- 本批无真实PG运行、browser、全量、性能或真实资金接口。FULL-406完整关闭须集中验收覆盖原全部要求，文件存在和合成PASS不能关闭编号。

下一前置：Root注册该router及RRRO依赖并生成actual schema，再串行运行既存owned数据库候选和新增改策略/产品变更链；决策trace/实际新候选确认与回执属于后续原执行消费者接线。


## 2026-10-06 04:30 Root增量

六实际PG批终态PASS/1600.41s，wrapper1611.67274s；见 `evidence/W3/actual-release-sources-consent-repair-and-current-maturity-20261005T195334Z-810817b1`，范围来源稳定/全源独立变化。仅相应节点实证，非完整版本验收。具体旧缺口与原失败保留。
