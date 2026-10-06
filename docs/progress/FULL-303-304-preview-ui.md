# FULL-303/304 当前期现金归属回拨预览界面

实现状态：`READONLY_PREVIEW_UI_IMPLEMENTED`，原FULL-303、FULL-304仍PENDING。原要求见 [需求追踪表](../spec/requirements-traceability.md) 第71–72行与原完整计划1248–1254、517–524：已归属资金默认不可跨目标移动、归属与资产放置分开；显式CrossGoalReallocationPolicy限定紧急回拨，原验收要求保留默认关闭、条件/额度/版本、撤销、非紧急拒绝及回拨原证据回执。

此批只新增独立reader/panel，消费实际 `POST /api/v1/goal-reallocation/preview`，Root拥有生产路由/RRRO注册、真实Schema生成和目标页集成。面板读取既存FullPolicy目录，用户选择原CrossGoal规则后主动请求只读预览。正文恰好五个UUID：policy、source_goal、expected_policy_version、expected_goal_policy_version、expected_epoch。没有客户端金额、时钟、回执、授权、reset或实际回拨结果；没有自动POST、自动重试、创建规则或金融执行按钮。

组件接入合同：`GoalReallocationPanel({goal,userId,epochId})`；goal为现 `Goal` 的id/name/policy_id/policy_version_id，userId为当前账户实际用户，epochId为当前原epoch或null。Root可以按目标版本/epoch设置key；面板收到原绑定变化后不显示旧结果，只主动新预览可重算。未知epoch或历史/异用户规则不能请求。预览只读，即使其他金融族写操作待恢复也可读取；必须由Root保留原资金门，不用此结果释放任何未知写请求。

已实现：

- 按原FullPolicy响应显示默认关闭、实际生命周期状态、版本、确认/引用状态、有限日期、单次和同策略跨版本累计cap、允许源目标/紧急条件及原配置/确认JSON。规划确认不等于资金授权，旧Goal桥仍不允许跨目标。
- 显示原银行现金、目标现金锁、在途占用、未归属现金、当前硬义务→生活费→应急底线三层前缀短缺及数学最低下界。用BigInt复核整数分关系，不把数学下界当可执行候选，不称全年多期全局最优。
- `candidate_amount_cents=null`、累计用量UNKNOWN/null、数学UNKNOWN/null、原模型缺失、原归属缺失均明确显示；不能用0替未知。原服务报告可释放现金数学上界不改变本金资产放置；未来收入和本金释放严格为0。
- 展示目标原allocated总額、应用/银行现金与本金各自状态、原归属证据/持仓引用、FullGoal最低保障模型状态/证据/原base版本。服务原报告不是浏览器独立银行验真，不赋予回拨权。
- 展示全部原reasons/source_issues、当前BEFORE_PAYMENT保护点、版本/epoch、对账/完整保护/source绑定hash及完整原HTTP响应。HTTP409/解析失败隐藏旧成功，用户只能主动刷新原目录后重审。

五个独占新文件：`apps/web/src/api/goal-reallocation.ts`、`components/GoalReallocationPanel.tsx`、相应reader/component测试及 `tests/goal-reallocation-fixture.ts`。夹具明确 `SYNTHETIC_HTTP_ONLY`，不是银行、PG、金融效果或实际浏览器实证；不修改原Goal/App/shared/contracts/金融后端。

实际验证：

| 命令范围 | 结果 | 原证据目录（docs/progress/evidence/W6） |
| --- | --- | --- |
| Vitest reader+panel2模块 | 50+5=55 PASS/3.59s；wrapper5.437937s、exit0、本5源stable | goal-reallocation-ui-repaired-direct-20261005T184723Z-4fc397cf |
| ESLint本5新源 | exit0、all/scope stable、wrapper3.525032s | goal-reallocation-ui-repaired-static-20261005T184720Z-2346c1f2 |
| 当前真实生成Schema下全Web类型首次 | FAILED：本reader六处unknown收窄、他方八维tuple夹具；失败原件保留 | goal-reallocation-ui-generated-types-20261005T191802Z-540b5eb6 |
| 本reader类型收窄后ESLint五源 | exit0；运行时金额/身份/权限判定保留 | goal-reallocation-ui-types-narrowing-static-20261005T191929Z-a000f255 |

Vitest终态manifest的 `all_source_stable=false`，唯一并行他方改动是 `apps/api/app/tests/test_intervention_public_json.py`；`scoped_source_stable=true`。本模块结果不能当全仓最终冻结证明，不因无关新测试重复本模块。首次suite失败（测试正则未转义斜线）为reader50 PASS、panel transform FAILED，原 `goal-reallocation-ui-direct-20261005T184636Z-2cb50dc5` 和 static `goal-reallocation-ui-static-20261005T184628Z-0031ed18` 完整保留，不重标。

直接风险范围：五身份/用户/epoch/两个版本；数学三层完整性/增量/最低/null/原唯一性范围；原使用计数缺失不能假0；原Goal现金/本金口径/归属总额/原保护margin与当前阶段；专用grant/执行无权限旗；原cap/默认关闭配置；HTTP原body仅五字段/409；用户主动预览、刷新清旧响应、无规则或epoch未知、没有实际回拨按钮。

具体未覆盖：

- 真实生产入口/RRRO、隔离PG全表前后不变、原银行/审计/收入/目标全部原件，真实浏览器操作与最终初版/完整版集中验收待Root执行。本批没有PG、browser、full suite。
- FULL-303同目标真实资产调整、跨目标默认拒绝和原归属账本守恒不由合成HTTP UI关闭；继续保留原后端负例与真实链路要求。
- FULL-304专用原用户确认、完整跨版本usage ledger、原Goal grant/effect/bank key、双腿执行与UNKNOWN恢复、回拨证据/结算回执未实现；本页无回拨按钮，不能凭规划规则确认冒出资金权限。
- 当前数学只原三层保护，不是完整FULL保护、多Goal/多期联合最低修复；实际在途来源不能判定时保留UNKNOWN，不夸大数学最优。
- 读到的source/hash及归属状态只核响应的一致性。浏览器未独立重算全审计/银行账本/输入hash原件，不宣称独立经济验真。

Root已注册实际入口、生成 `FullGoalReallocationPreview`/`ReallocationPreviewRequest`，并接Goal只读区。首次类型失败只做局部类型收窄：保存已验证math/floor/reasons局部引用、明确BigInt reduce类型；不改变运行时原数值/状态/权限判定，不重复55项已过直接测试。原类型待验快照 `.runtime/FULL-303-304-preview-ui-types-pending-20261005T185216Z-70371dee` 与首次失败完整保留。另模块tuple夹具已由其owner窄修；最后整体Web类型由Root合并两方稳定源后统一执行，本交付不声称已过。

下一前置：Root统一完成当前Web类型与owned PG/集中浏览器。正式回拨须另包原授权/原命令/累计账本/执行恢复，不能放宽旧目标桥。

2026-10-05T19:40Z追加：新专用授权reader的strict7+imports类型检查覆盖本reader，发现并修正第80行完整三条件校验后的Record索引non-null类型断言。运行时原数学/金额/状态判定及55项旧直接测试不变；旧75d5390a快照保留。`goal-release-authorization-ui-final-scoped-types-20261005T194019Z-e1788774` 和 `...final-static-...fbb9ee0e` exit0且本scope稳定，仍不是全Web类型证明。专用范围确认已有独立新界面，详见 [新交付](FULL-304-dedicated-release-authorization-ui.md)，不改写本早期只读批次的证据边界。
