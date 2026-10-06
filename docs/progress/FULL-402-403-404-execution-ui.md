# FULL-402/403/404 整组资产执行界面

状态：`IMPLEMENTED_ACCEPTANCE_PENDING`。三个原 FULL 编号继续 `PENDING`。这是实际服务器组合消费者的生产界面增量；本包测试是显式 TOOL_ONLY HTTP/存储/组件检查，不是银行、PostgreSQL、浏览器或版本全量验收。

原要求沿用 `docs/spec/requirements-traceability.md:77–79`：402 的逐约束过滤，403 的有限多资产、安全先于收益及复杂度/换手，404 的长期目标定存梯度与每批权限/截止期。实施顺序依据已登记的功能优先修订；已有产品只读页和原演示控台均保留。本包不修改 Main、App、ProductsPage、共享 HTTP、后端合同、银行路径或正式历史。

## 交付能力

新增七个源码：

- `apps/web/src/api/full-asset-execution.ts`、同名直接测试。
- `apps/web/src/features/full-asset-execution-operation.ts`、同名直接测试。
- `apps/web/src/components/FullAssetExecutionPanel.tsx`、同名直接测试。
- `apps/web/src/tests/full-asset-execution-fixture.ts`（明确 TOOL_ONLY，不是产品实证）。

用户主动读取现有 Full 资产策略、原 MVP 资产权限、Goal 和可用原 epoch，选择既有版本/范围与服务器支持的 PORTFOLIO/FIXED_LADDER 模式。各来源读取不是同一数据库快照，提交前由服务器重核。没有客户端金额、产品参数、未来收入、clock、银行结果或 receipt 输入。

只读预览使用实际 `POST /api/v1/full-asset-executions/preview`。reader 校对同 owner/epoch、显式九字段原请求、Full/MVP/Goal 版本、原规划/保护结果、完整原组合 hash/配置 hash、每批金额/目录版本/terms digest/原 BankCommand 与原规划对应关系、全部 1098 个保护点。UNKNOWN/null 保持未知，不提供保存成功按钮。SHA 和有限字段一致性检查不替代服务器权限引擎、原 effect 哈希算法、独立银行账本或审计验真。

`POST /prepare` 前保存完整原请求及 canonical SHA；包含原 DTO 的 null/default 字段。服务器准备结果的 whole 原件必须独立原键 GET 才能解除待核对门。准备仅保存候选，不表示整组预留资金。

用户复核每个原批次、金额、条款、来源和 whole hash 后，单独 checkbox 确认整组，调用原 `/portfolios/{id}/confirm`。完整 Confirm body/key/hash、原 portfolio 全字节 JSON、owner/epoch 在 HTTP 前持久保存。原 `/commands/{epoch}/by-key/{key}` 的 CONFIRM 原件需完整匹配才清门；不能借 PREPARE、latest 或另一个 key。保留原 Consent Evidence 与当前 Evidence 匹配状态，历史原确认不表示当前银行权限。

逐批执行请求只有 `accepted:true, reviewed_portfolio_hash, expected_epoch_id, expected_batch_number, expected_action_id`。每次用户明确点击一个固定原批，持久保存该完整 body/hash/portfolio；没有新的 EXECUTE idempotency key。前批 UNKNOWN 保留原 portfolio/action/bank key，后批停止。只有独立 `GET /portfolios/{id}` 核该指定原批的 SUCCEEDED/RECONCILED + SETTLED + receipt + 原 trace 核验，才能解除这次门；另一批成功不能替代。GET 不执行后批，新批仍需要新的用户确认点击。

POST 成功、网络中断、解析失败或 4xx 均不会解除门。NOT_FOUND_NOT_FINAL 不是最终未提交证明。用户先读原结果，若仍未决，只能额外 checkbox 手动恢复同一完整原请求；没有自动 GET→POST、自动重试、换键、新组合或偷推进批次。sessionStorage 无权限/损坏、删除失败均锁住新写且保留原件。刷新只恢复存储，不自动 POST。

## Root 接入合同

`FullAssetExecutionPanel` 的 props 为 `{ mutationBlocked?: boolean, userId?: string, epochId?: string }`；可仅传他族 `mutationBlocked`。可选实际用户/epoch 会参与来源核对。组件自身原 GET 始终可越过自身 pending 门；自身 pending 不应作为传入的他族 blocked，否则用户恢复写也会永久锁住。

store 导出 `recoverFullAssetExecutionOperation()`（异步）、`getFullAssetExecutionOperation()`、`useFullAssetExecutionOperation()`。snapshot 为 `{pending,busy,recovering,storage_error}`。Root 负责把本族加入全局旧资金/其他族门并把其他族门传给本组件；Root 新增 Payment 族由外层门覆盖，未在本包互相 import 异步 store。组件应放在 ProductsPage 的全局旧资金 fieldset 外，使原恢复 GET 可达。

## 已运行检查及保留失败

真实生成合同来源：`W6/actual-payment-and-fixed-batch-consumer-openapi-20261005T212057Z-cc785ce2`，Root 的实际 Main 生成退出 0。本包使用实际 generated FullAsset* aliases，没有手写替代合同。

- 首 reader/store：`W5/full-asset-execution-ui-first-direct-20261005T211934Z-fca52bc7`，51 PASS/8.11s，wrapper 9.244892s，全部与相关源码稳定。
- 首组件：`W5/full-asset-execution-ui-component-first-direct-20261005T212212Z-263b7d50`，6 PASS/9.54s，wrapper 10.697676s，全部与相关源码稳定。
- 首类型：`W6/full-asset-execution-ui-first-types-20261005T212238Z-ef458160`，FAILED/exit1 原记录保留。仅新合成夹具错误使用规划词汇 FIXED_30D 而原配置需 FIXED_DEPOSIT_30D，以及已检查非空 cash_uses 的 optional 类型；生产 reader、后端金融判断和负例没有放宽。
- 修正后模块严格类型：`W6/full-asset-execution-ui-final-types-20261005T212330Z-1fb032dc`，exit0，wrapper 3.254206s，相关源码稳定；全仓源码有另一 agent 的独立 Payment 三文件变化，不能声称全仓冻结。
- 七文件 lint：`W6/full-asset-execution-ui-final-lint-20261005T212331Z-52ab6ea7`，exit0，wrapper 3.349668s，同样仅相关源码稳定，全仓独立 Payment 三文件变化保留。
- 最终相关三模块：`W5/full-asset-execution-ui-final-direct-20261005T212356Z-0eb705ce`，**57 PASS/9.14s，0 skip**，wrapper 10.246843s，全部与相关源码稳定。这是同一 32 reader +19 store +6组件测试分母；前面重复运行不累加成更多独立测试。

命令完整 argv、退出码、原 stdout/log hash 和 source.before/after 均在对应目录 `manifest.json`。Vitest 只使用合成 fetch/HTTP、WebCrypto 和 sessionStorage，未调用 PG、真实金融服务或浏览器。类型使用原 strict/noUncheckedIndexedAccess/noUnused 等模块参数；lint 使用项目实际配置与 `--max-warnings 0`。

## 具体未覆盖与下一依赖

Root 尚需接 ProductsPage/App 全局门；本包没有真实浏览器/移动屏幕/键盘视觉验收，也没有本批真实银行多资产执行证明。Root 的单链 PG 正在独立运行，其完成前不写成通过。

真实七类产品尚未齐备，界面只显示服务器返回的原目录/梯度，不能据此宣称 7/30/90/180 天都有。长期 Goal 归属投资、跨期限/截止期梯度、近期开支冲突、各限制独立金融负例、并发双组合、硬重启、全部 crash 边界、到期再配置仍需各自真实证据。FULL-402/403/404 原全部验收要求不缩减。

整组为 UNRESERVED，跨银行操作原子性/回滚为 NOT_AVAILABLE；SERVICE_RECEIPTS_VERIFIED 只表示原服务回执，经济实验验证仍 false。未接真实资金，未实测效果/性能，未开展真人研究；本包不关闭原编号或完整版本验收。
