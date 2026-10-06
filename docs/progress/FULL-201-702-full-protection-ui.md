# FULL-201 / FULL-702 完整策略年度保护消费增量

2026-10-06（北京时间；证据使用2026-10-05 UTC原时间）。`FUNCTIONAL_INCREMENT_IMPLEMENTED / ACCEPTANCE_PENDING`。原FULL需求仍PENDING，按功能优先修订推进，没有改旧计划、旧失败、正式模拟历史、权限缓存或资金接口。

原需求：`docs/spec/requirements-traceability.md:63`的FULL-201（完整版计划1212–1214、432–452）与`:97`的FULL-702（1368–1370、1016–1027）。旧年度时间轴与当时缺口保留在`FULL-702.md`。本批消费真实FULL保护服务，不据此关闭原年度规划或仪表盘全部要求。

## 可运行能力

`AnnualPlanningPage`保留原90日财务执行参考、365日条件规划、未来收入/本金缺口和旧日期曲线，另接`FullAnnualProtectionPanel`。新增真实GET `/api/v1/planning/full-annual`，无金额、客户端时钟、用户/版本覆盖body或金融POST。两个区域独立读取，不能称共同事务快照。组件无必需props，已在年度页面使用；query key为`full-annual-protection`。

FULL区域展示今日初始点与未来365日期，每日付款前、付款后、本金到账后三阶段的原条件现金、保护分项、余量、原义务和本金编号；含跨月、跨年及第365日。查看日期与刷新只读。UNKNOWN保全部366日期、空阶段和空最小余量，不借原READY曲线填金额，不填零。刷新期间注明上次报告；新响应校验失败隐藏旧成功报告，无自动重试。

新增曲线只纳入已登记DatedExpensePolicy/PeriodicTransferPolicy的保守未付上限或固定金额，保留每个原policy/version、登记应付窗口、原已付null、证据和settlement_status。不把保守上限当实际发票或银行付款。当前账户局部检查显示原现金、原目标归属、在途保留、登记周期金额及可为负的剩余；`future_account_debits_complete=false`明确没有独立分配全部未来MVP账户支出，不能称完整可支付证明。

原策略状态、完整配置/确认/引用结构、原content_hash、来源问题、projection.reasons、约束、具体limitations、当前审计scope/status/complete和证据入口可展开。局部结构是从原响应提取的派生展示；完整response.text()文本另保留展示。禁用结构共享避免查询替换对象后丢原文本。hash展示不等于独立重算配置或审计/银行验真，没有改历史hash。

未来收入原状态仍未接入；响应中的执行/规划计入0不是预测真实收入等于零。SeasonalReservePolicy仅建议、adopted_adjustment=null，未采纳附加金额。`historical_full_settlement_complete=false`、`execution_support=NOT_IMPLEMENTED`、`planning_only=true`、`bank_authority=false`保持原义；原执行消费者尚未采用新增FULL曲线，不能把FULL规划安全闲置称当前自主资金。

## 响应完整性与文件

`api/full-annual.ts`使用生成的FullAnnualProtectionResponse，严格检查365/90原trace分母、今日起点和连续日期、三阶段与原trace一致、日内最小值、状态/null关系、精确整数分、无授权/未实现标志、来源/状态/发生/账户分母、原版本/确认hash/证据身份、合法窗口/假设付款日期、账户局部金额守恒及原模型摘要的内部一致性。

原JSON对象和保护字典键的合法重排序不会误拒；非数组额外同键排列不当金融差异。金额一致性使用BigInt，保持负余量，不把客户端核对用于确定资金权限。客户端不重算服务的完整业务算法、配置hash、input_digest、历史审计或银行效果；当前API没有timezone字段，首日期仅检查UTC/Asia/Shanghai两个当前支持日期中的合法原日期，不能补造时区字段。

父任务新增`api/full-annual.ts`、`components/FullAnnualProtectionPanel.tsx`并接年度页面；本批接管窄审查/加固这两个文件。新增`tests/full-annual-fixture.ts`、对应API与组件风险测试；修订旧AnnualPlanningPage测试按真实路径提供独立DTO。旧planning.ts仅有父任务导出的annualDayNumber/validateAnnualBoundary，本批不改旧reader行为，保留原4项测试。复用现annual-phase/annual-amounts响应式样式，没有修改App、GoalsPage、Onboarding、共享HTTP、contracts、后台或styles.css。

## 定向检查与原失败

使用既有scoped runner，没有扩展验证工具。证据均在`docs/progress/evidence/W6/`：

- 9个相关文件ESLint初批退出0：`fullannual-protection-ui-lint-20261005T155641Z-74168dd9/manifest.json`。reader一处索引修订后的单文件lint0：`fullannual-protection-reader-lint-repaired-20261005T155741Z-03813624/manifest.json`。最终仅两测试选择器改动，二文件lint0：`fullannual-protection-test-lint-repaired-20261005T155942Z-75ab7d61/manifest.json`。
- 首次typecheck FAILED：`fullannual-protection-ui-types-20261005T155641Z-2c4a8716/manifest.json`，真实reader的issue[field]字符串索引TS7053。父任务的`full-goal-root-integration-web-types-20261005T155554Z-88978d2f`也遇到此同源问题，原件保留。只将字段数组收窄为code/message/source_ref三个合同字段，未放宽校验；repaired类型0，最后整体Web typecheck0：`fullannual-protection-final-types-20261005T155942Z-3af84aad/manifest.json`，8.916205秒。
- 四模块首批`fullannual-protection-related-vitest-20261005T155812Z-1d47f637/manifest.json`：17PASS/4FAIL，整体FAILED。新reader8/旧reader4实际全PASS；两页面的四失败均由摘要、原JSON或独立报告重复文本/证据链接导致选择器歧义，原完整日志/源保留。
- 只限定选择器到真实区域/p标签，保持金额、null、证据、无POST等原断言，重跑两个页面共9PASS：`fullannual-protection-pages-vitest-repaired-20261005T155947Z-783be42c/manifest.json`，Vitest4.84秒、wrapper6.667810秒。共21个不同测试通过，不相加为30、不把原FAILED改PASS。

最终页面批全源及范围稳定。最终类型范围稳定；全源变化仅另一个question_workflow后台service/tests，all_source_stable=false如实保留。原字节、各失败源与最终源SHA见`.runtime/FULL-201-702-annual-protection-ui-20261005T154731Z/`。所有fixture是TOOL_ONLY合成HTTP/组件输入，不是年度金融效果、PG或实际银行证明；本批没有PG/浏览器/Docker/全量。

父任务真实后台原件可引用`docs/progress/evidence/W3/actual-boundary-event-audit-replay-and-full-protection-real-pg-20261005T153844Z-d006f762/manifest.json`：两个实际节点合计2PASS，pytest209.43秒/wrapper211.987494秒，包含一个FULL保护节点和一个Boundary事件节点。不能将合计时间算作单项耗时或本批前端实证。该PG范围稳定、全源有其他独立成果变化，原manifest事实保留。

## 未覆盖及下一依赖

真实浏览器跨日期/错误刷新/手机键盘操作和原账户/证据导航没有本批实跑；本页与后台真实实例的全链兼容仍待最终浏览器节点。初版和完整版最终全量未通过，不关闭FULL-201/702。

历史完整结清绑定及旧失效版本的未付覆盖、原执行消费者接纳新FULL保护曲线、全部未来账户支出分配、未来收入规划来源、Seasonal采纳附加金额、银行独立锚点及所有历史epoch验证仍缺。当前完整仅指已登记当前来源范围，不能提升到全部历史覆盖。原仪表盘所有卡片/实时事件/变化影响/E2E属于其他消费者与最终验收，不因本区域通过而补造完成。
