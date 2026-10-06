# FULL-702 年度时间轴：当前只读合同的功能增量

2026-10-05。`FUNCTIONAL_INCREMENT_IMPLEMENTED / ACCEPTANCE_PENDING`，FULL-702 保持 PENDING。执行顺序遵循用户“功能优先、最后集中验收”的显式修订；没有修改原需求、STATUS、正式模拟历史或原失败记录。

原要求见 `钱途有界_完整开发计划_Codex执行版.md:1016–1027、1368–1370`，追踪表为 `docs/spec/requirements-traceability.md` 的 FULL-702。此处交付年度时间轴，不能据此关闭整个资金仪表盘。

## 可运行能力

`#annual` 使用真实 `GET /api/v1/planning/annual`，无客户端金额、时钟或权限覆盖。页面并列展示原90日执行参考与365日条件规划，保留今日初始点和365个未来日期；逐日期显示付款前、付款后、本金到账后三个原阶段、现金、保护分项、余量和来源编号。曲线只画服务端已证明的最小日内余量，未知日期保留缺口，金额 null 不填零。

未来收入显示原 `NOT_IMPLEMENTED_NO_REGISTERED_SOURCE` 与原因；未核验本金到账日期的实际持仓单独列出。年度规划不授予执行权限，未来点不等于已到账现金。服务端来源问题、约束、输入摘要、当前审计报告和证据入口逐项保留；审计报告展示不等于独立银行验真。原 JSON 响应文本完整可读，它是 HTTP response.text()，未宣称抓取了传输层原字节。

年度 reader 使用生成的 `AnnualProjectionResponse` 类型并校验实际日期、366个点、三阶段、365/90分母、整数分、未知值、来源身份、模拟标志与无授权边界。错误结构不会包装为成功页面。文件为 `apps/web/src/api/planning.ts`、`pages/AnnualPlanningPage.tsx`、对应测试、`tests/annual-fixture.ts` 和 styles.css 的本页局部增量。App路由由父任务接入；金融后端未改。

## 定向验证

- 最终 typecheck：`docs/progress/evidence/W6/annual-evidence-types-final-20261005T125334Z-cbca64cc/manifest.json`，退出0，全部源及范围内源稳定。
- 10个相关文件 ESLint：`annual-evidence-lint-r2-20261005T125009Z-2e0996ae/manifest.json`，退出0。两测试选择器修订后仅检查这两文件：`annual-evidence-test-lint-final-20261005T125334Z-ec9c90d4/manifest.json`，退出0，全部源稳定。
- API年度4项与证据7项在 `annual-evidence-related-vitest-20261005T125050Z-9cca9bd9/manifest.json` 中实际通过。该批19项有3项页面文本选择器失败，整个批次为 FAILED；范围源稳定，App另由父任务并行变更。
- 修复只涉及选择器而未弱化原件/负例，重新运行两个页面共8项，`annual-evidence-pages-r2-20261005T125338Z-4b06dfad/manifest.json` 为 PASS，退出0、全部源稳定。共19个不同测试经上述批次通过，不能相加为27或把原 FAILED 改为 PASS。

证据目录均在 `docs/progress/evidence/W6/`。HTTP夹具是组件/reader单测，不是实际PG、银行、浏览器或财务效果。首次可选 notes 字段的类型失败与页面选择器失败原件保留。修改前样式、各轮原文件及最终源SHA保存在 `.runtime/FULL-702-706-readonly-ui-20261005T122914Z/`。

## 具体未覆盖

原仪表盘的全部卡片、事件流/SSE、目标与资产完整变化前后影响、移动端实际截图与E2E未由本批验证。未来收入来源未实现；无已核验本金到账日期就不能纳入曲线。当前审计范围之外的所有历史epoch和银行外部锚点没有另验。真实集成、浏览器及初版/完整版全量验收留在最终节点，本次不关闭 FULL-702。
