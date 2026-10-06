# FULL-706 证据溯源：当前持久化只读视图增量

2026-10-05。`FUNCTIONAL_INCREMENT_IMPLEMENTED / ACCEPTANCE_PENDING`，FULL-706 保持 PENDING。按用户“功能优先、最后集中验收”修订推进，没有修改原编号、失败记录或正式历史。

原要求见 `钱途有界_完整开发计划_Codex执行版.md:1064–1075、1384–1386`，追踪表为 FULL-706。完整要求包括动作搜索、版本筛选、事实/候选/约束/干预/回执全链、哈希核验及真实E2E，不能仅凭本页关闭。

## 可运行能力

`#evidence` 查询真实 `GET /api/v1/evidence/facts`；可填写事实有效时和系统知悉时、来源类型/引用。时间必须带时区，未来知悉时交原服务器拒绝，不用浏览器时钟代替后端规则。展示原有效区间、observed_at、内容hash、SUPERSEDED状态及实际 SUPERSEDES 引用；组/整体的 CONFLICTED、UNKNOWN、容量不足和问题逐项显示。没有把旧状态重新推断成历史成功。

`#evidence/{kind}/{UUID}` 查询真实 `GET /api/v1/evidence/graph/{kind}/{identity}`，支持 EVIDENCE、PROPOSAL、POLICY、POLICY_VERSION、DECISION、ACTION、RECEIPT。节点与有向边只来自响应，并按真实原字段核对引用；选择节点、种类筛选和引用目标导航不添加推测关系。断链或尚未知悉的目标保持具体问题，不补假节点。`REFERENCES_RESOLVED` 明确只指读取范围内引用解析，不是回执资金核验或哈希链独立验证。

reader 校验查询时间/来源筛选与返回绑定、owner/root/key身份、实际关系字段、模拟与无授权标志、容量边界和未知状态。原内容如果含超安全整数，不展示可能被JS舍入的解析副本，保留完整原JSON文本。组件切换深链时重建查询上下文，避免显示旧root成功。HTTP原响应文本由 response.text() 留存，未称为传输层原字节。

主要文件为 `apps/web/src/api/evidence.ts`、`pages/EvidencePage.tsx`、对应测试、`tests/evidence-fixture.ts` 及本页局部样式。无金融写入、修订事实接口或授权操作。App深链由父任务接入。

## 实际验证

最终 types、相关10文件 ESLint和测试选择器最终 ESLint 均退出0，原件与 FULL-702 共用：`docs/progress/evidence/W6/annual-evidence-types-final-20261005T125334Z-cbca64cc/manifest.json`、`annual-evidence-lint-r2-20261005T125009Z-2e0996ae/manifest.json`、`annual-evidence-test-lint-final-20261005T125334Z-ec9c90d4/manifest.json`。

第一次四文件Vitest批次共19项，API年度4项和证据7项通过，3个页面选择器因原JSON也包含同文案而失败；该批原件 `annual-evidence-related-vitest-20261005T125050Z-9cca9bd9/manifest.json` 保持 FAILED。只收紧两测试文件的目标选择器，重新两个页面8项全通过，原件 `annual-evidence-pages-r2-20261005T125338Z-4b06dfad/manifest.json`，全部源稳定。相关19个不同测试已通过，不是全量验收；夹具不是实际银行资金证据。

## 未覆盖与下一依赖

新事实修订/冲突处理写入未接入，服务明确列出的外部银行锚点和可变动作/策略完整历史重建仍未验证。服务遍历是原引用方向，没有宣称全对象反向闭包或完整“事实→全部回执”路径。完整候选差异、约束与干预搜索、历史全epoch哈希链/外部资金核验、真实篡改负例浏览器集成和截图尚待最终节点。本批不关闭 FULL-706。
