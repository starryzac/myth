# FULL-307 / FULL-704 当前月动态节奏只读UI

根集成（2026-10-06 00:24 北京时间）：GoalsPage GoalCard 添加惰性“查看动态月储备与真实进度”，位于金融写 fieldset 外；目标页刷新、原贡献结束以及完整Goal确认/原键核对后的刷新失效 dynamic-goal-reserve 前缀，完整确认同时失效 full-annual-protection。原目标策略版本仍是 reader 必须匹配的当前 prop，不把旧报告当新版本。两直接模块 GoalsPage11 + FullGoalModelPanel10 共21PASS4.27s；原日志 W6/dynamic-goal-root-page-and-confirm-invalidation-20261005T162212Z-dbed6cf6。整体Web tsc PASS：dynamic-goal-root-integration-types-20261005T162339Z-69fc6226；三根源ESLint PASS：dynamic-goal-root-integration-lint-20261005T162339Z-4eaac0d9。新风险节点证明写门开启仍只读GET、展开前不读、刷新再次GET；仍是HTTP fixture模块检查，不称实际浏览器/资金链验收。原agent21不同新reader/component和该根21范围分别登记，不能替代FULL关闭。

2026-10-06北京时间（证据UTC为2026-10-05）。`FUNCTIONAL_INCREMENT_IMPLEMENTED / ACCEPTANCE_PENDING`，FULL-307/704保持PENDING。原要求见`docs/spec/requirements-traceability.md:75、99`，分别对应原完整版计划1264–1266与1376–1378、1039–1048。原后台规则/早期状态及真实PG补录保留在`FULL-307.md`，本页仅新增前端消费差量，未改历史hash、正式模拟记录、旧失败或原需求。

## 可运行能力

新增`api/dynamic-goal-reserve.ts`与`components/DynamicGoalReservePanel.tsx`，使用当前真实generated DynamicGoalReserveResponse。只调用`GET /api/v1/goals/{goal_id}/dynamic-reserve`，严格UUID路径，无query/body、客户端时钟、金额、source或用户覆盖。无确认、分配或资金POST，读取不受本族其他写操作恢复门阻断。

默认组件props `{goal: Pick<Goal,'id'|'name'|'policy_version_id'>}`；query key `['dynamic-goal-reserve',goal.id,goal.policy_version_id]`，无必需blocked/write props。父任务接GoalCard只读区域；切换目标/原版本重新查询并绑定原ID/version，旧响应不能冒当前版本。读失败显示错误/request_id并隐藏旧成功报告，刷新期间注明上次报告，不自动retry；完整response.text文本保留，禁用结构共享避免丢原文本。当前Goal列表版本若旧于服务latest，明确拒绝并要求重读原目标，不从旧报告借权限。

展示当前自然月、实际归属、本月原贡献、剩余目标、真实超额归属、原basis-points进度、剩余日历月槽、未截断节奏、动态累计目标、原名义月target及二者可为负的差额。动态累计目标包含本月原贡献，此贡献不加回可用资金；条件新增建议是扣除原贡献后新收入/原365保护预算内的只读结果。

显示符合当前原版本窗口的实际可用收入、原365硬保护预算、希望新增/条件建议、月最低与硬保证短缺、实际逾期天数和截至现在原延期成本。硬保证/到期阻挡的建议保持null，不填零。已完成状态的新增0是计算结果，不伪造实际完成日期；本月贡献已超新max时保留原贡献与0新增，不回退历史。停用/到期/证据不足/流动性风险的未证明字段保持null，UNKNOWN/EXPIRED/INACTIVE不借旧模型。

源问题、原证据链接、input_hash、原algorithm/reasons、limitations可查看。服务没有返回timezone、确认/生效窗口日期、min/max或收入fragment明细，前端不造这些字段；原月范围/deadline在GoalCard独立读取。月份仅检查服务as_of对应当前UTC或Asia/Shanghai自然月中的合法值，不按客户端今天或30日近似。不同页面/请求各自快照，不能组成共同事务快照。

保留`ALL_ORIGINAL_365_DAY_RESERVES_RETAINED`、preview_only=true、grants_authority=false、future_income_included_cents=0、actual_completion_date=null。原参与目标的重复保护尚未释放，预算可能保守。动态金额尚未接入原nominal动作执行或联合调度；没有服务execution_support字段时页面只说明此真实能力边界，不伪造响应字段。新的FULL保护/联合规划后续包未在本模块视为已接入。

## 严格reader及验证

校验模拟/无授权合同、来源ID、原目标/版本、合法当前月、状态/null关系、原摘要格式、精确整数分、进度0–10000、非负金额/日历槽、允许负target差额、所报告累计与名义target差额一致、建议不超过所报告希望新增/剩余目标/可用实际收入/原保护预算，以及COMPLETE/超上限/硬保证null等状态一致性。只核响应内完整性，不重算完整节奏算法、账本、input_hash、历史审计或银行效果；客户端不决定金融金额或权限。

新增`tests/dynamic-goal-fixture.ts`和两模块测试，明确全部TOOL_ONLY合成HTTP/组件数据。复用既有full-goal-fields/read-only样式，没有修改App、GoalsPage、原GoalCard、Onboarding、共享HTTP/写门、contracts、后台或styles。父任务负责GoalCard集成与其必要模块检查；刷新原Goal确认/分配后可使`['dynamic-goal-reserve']`失效读，不自动执行建议。

使用既有scoped runner；原manifest均在`docs/progress/evidence/W6/`：

- 首批5文件ESLint0：`dynamic-goal-reserve-ui-lint-20261005T161250Z-c1a11c36/manifest.json`，4.326852秒。只新增两状态风险测试后，两文件lint0：`dynamic-goal-reserve-ui-added-test-lint-20261005T161512Z-821fc00f/manifest.json`，3.625807秒。
- 首批19项相关测试PASS3.08秒，`dynamic-goal-reserve-ui-vitest-20261005T161321Z-aa6508df/manifest.json`；完整原19项源另保存`first-19-pass-source/`。新增完成/已超上限/partial风险后最终reader13+组件8，共21PASS，`dynamic-goal-reserve-ui-final-vitest-20261005T161515Z-c766ce9f/manifest.json`，Vitest3.28秒/wrapper5.152402秒。不相加成40项，不计作金融实证。
- 最终整体Web类型0：`dynamic-goal-reserve-ui-final-types-20261005T161511Z-db3d716f/manifest.json`，9.243716秒。最终测试/types及以上lint全源与范围源稳定。

本批没有实际PG、浏览器、Docker或全量。父任务已有后台真实2节点PG原件`docs/progress/evidence/W3/dynamic-goal-actual-month-contribution-and-source-real-pg-20261005T134730Z-f91a7c48/manifest.json`，pytest90.58秒/wrapper92.576334秒，scope稳定、all=false为同期独立full-policy-operation前端变化。它证明实际原到账/nominal行动贡献后同月防重复、只读和来源篡改保守返回；不是本批UI或动态金额执行证明，不将2节点总时间当单项。

## 未覆盖与下一依赖

GoalCard接入、真实浏览器/月初月底/原贡献事件重读、手机键盘与证据导航、本项目当前代码最终全量尚待父任务统一安排。缺跨请求共同快照、原收入fragment明细/窗口UI、动态金额真实执行和联合调度、全部历史审计/银行独立锚点验证；没有伪造接口字段或把有限报告包装成完整资金验真。

完整原源/最终SHA/19项源归档与交接见`.runtime/FULL-307-704-dynamic-goal-ui-20261005T160818Z/`。当前功能可调用真实只读合同，原验收项仍PENDING。
