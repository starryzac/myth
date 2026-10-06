# FULL-107 / FULL-108 / FULL-706 支出证据界面增量

2026-10-06（Asia/Shanghai）。`FUNCTIONAL_INCREMENT_IMPLEMENTED / ACTUAL_BROWSER_ACCEPTANCE_NOT_RUN`。功能优先修订下的新产品界面，三个原编号均不由本批直接关闭；不改 STATUS、原失败证据或正式模拟历史。

## 可运行能力与接线

`apps/web/src/pages/SpendingEvidencePage.tsx` 提供周期/节日只读建议、本人账户交易分页、原银行消费复核及首次类别确认。App/导航由 Root 单独接线，建议入口 `#spending-evidence`；页面 prop `mutationBlocked` 表示其它请求族阻写，自己的 GET 恢复不受此 prop 阻断。新 reader、store、page、各自测试与 HTTP 夹具共七文件。

实际 GET：`/accounts/summary`、`/transactions?limit=50&offset=…&account_id=…`、`/policy-suggestions/periodic`、`/policy-suggestions/seasonal?window_id=…`、`/transactions/{transaction_id}/category-review`。账户只能选实际本人账户，交易逐行核该账户；保留服务返回的分页分母，不将一页当完整历史。

FULL-107：展示实际历史范围、覆盖状态、来源问题、连续月份、周期/样本数量、日期跨度、金额范围、精确分数均值/方差、READY / UNKNOWN / INSUFFICIENT_HISTORY / UNSTABLE 与具体原因。只读展示完整候选、来源与服务摘要；没有候选时不推断未来不存在义务，没有策略确认/执行按钮。

FULL-108：用户先明确填写 window_id。返回后从实际 `public_windows` 选择，展示真实目标与有效窗口、通知文号/日期/官方链接、服务静态登记核对日期、全部历史比较分母和不足。未登记/尚未发布窗口保持 UNKNOWN，null 建议額不补零。没有客户端虚构全年窗口、日期或节日消费，不创建策略或资金动作。

FULL-706：原 transaction 与独立 bank_fact 分别可复核。仅支持银行 `CONSUMPTION` 借方的首次用户类别声明；先选择类别/填写原因，再主动勾选确认。发送前 fresh GET 同交易，复核 hash/epoch 必须与用户看过的原件相同；变化时拒绝发送，不能继承原确认。保存完整 user/transaction/body/body_json/key/command hash/原银行证据身份及 hash 后，POST `/transactions/{id}/category-confirmation`，body 仅 `category/accepted=true/reviewed_transaction_hash/reason/idempotency_key/expected_epoch_id`，不传银行金额/方向/收款对象或经济角色。

任何 POST 结果（成功、4xx、解析失败、网络响应丢失）均保留 pending。只有独立 GET `/transactions/{id}/category-confirmations/{epoch_id}/by-key/{key}`，逐项匹配原 command/body/hash/owner/tx/epoch/key、typed audit 事件身份/hash、原类别回执与原银行证据 id/hash，才清 pending。NOT_FOUND_NOT_FINAL、旧/latest 替代、字段不一致或存储删除失败均保持门。不会自动 POST、换键或重新分类。已确认类别/更正未支持只读。

Store 导出 `recoverSpendingEvidenceOperation/useSpendingEvidenceOperation`（`pending/busy/storage_error/transaction`），供 Root 全局写门接入。`getSessionTransactionReadonlyReference` 仅恢复 user/transaction/epoch 读取定位；不保存余额、授权等级或当前许可。tab sessionStorage 中 accepted=true 仅是待核对原命令的原字段，不是当前银行权限。

reader 保存 `response.text()` 原 JSON 文本，不称为传输原 bytes。资金字段全部须能由 JS 精确表示；唯一例外是 PeriodicPattern 的已注册 `mean_fraction_cents`，按非负整数/分数字符串展示，仍对所有实际金额、原 facts/configurations 执行 safe-integer 检查，不舍入金融金额。

## 已运行检查及失败保全

三个直接测试文件共31项，Vitest 5.22s / wrapper 6.340189s，退出0；scope/global 源均稳定：
`docs/progress/evidence/W5/spending-evidence-rational-and-original-fixture-repaired-20261005T171612Z-4fa7fdc4/manifest.json`。

全 Web `tsc --noEmit --project apps/web/tsconfig.json` 退出0：
`docs/progress/evidence/W5/spending-evidence-repaired-types-20261005T171607Z-b8715de0/manifest.json`。
七源 ESLint 退出0：
`docs/progress/evidence/W5/spending-evidence-repaired-static-20261005T171607Z-1198a1fe/manifest.json`。
最终两项各自的 scope/global 稳定性以原 manifest 为准，freeze 登记精确原值。

命令使用现有 Node 和已安装 node_modules 的原入口，不安装依赖。原 pnpm fallback exec 找不到 tsc/eslint 的终端记录不作为源码通过；改用实际本地模块入口。Vitest 首次 esbuild 子进程被沙箱 EPERM 拒绝，原 `spending-evidence-direct-ui-and-command-risk-20261005T171149Z-2f8de79c` 保持 FAILED。首次静态 no-loss-of-precision 与类型夹具字段错误原件、第一次可运行测试24PASS/6FAIL（`spending-evidence-direct-ui-and-command-risk-sandbox-retry-20261005T171322Z-38f2c0df`）均保留。后者定位合法分数字符串被通用金额检查误拒，以及测试响应错误共享 intent.body 引用；修复为精确字段限定和独立夹具响应。未改旧资金校验或原结果。

31项为 HTTP/DOM/store 单元风险检查。覆盖类别精确接受、未知字段、owner/epoch/金额/银行角色漂移、body/receipt/source/hash/事件不一致；beforePOST 持久化、每 HTTP 结果 pending、NOT_FOUND 非终局、刷新、存储失败、其它族阻写与自己的 GET 恢复；原建议窗口/样本、不足、错误 request_id 和旧成功视图清理。夹具全部标 TOOL_ONLY，不是实际银行或真人研究证据。

## 本批未覆盖与下一前置

- Root App/导航及跨请求族全局门需接入；本批不改其源。
- 后端真实类别 typed 审计/0011 迁移 PG 由 Root 当前独占执行；不把前端夹具通过当成该链通过。
- 实际 Edge 页面交互/响应丢失/原键恢复尚未运行；本批不自行启动 PG 或浏览器。
- 分类更正/撤销、建议策略确认、资金执行不在本页当前接口能力内。周期/节日完整需求原验收状态不因新界面存在而关闭。
- 完整 FULL-706 动作搜索、跨版本全链和最终真实 E2E 仍按原主进度登记；没有用分类局部回执代替全链。
- 无真人数据采集/研究、无真实资金接口、初版/完整版集中全量尚未运行。
