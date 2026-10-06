# FULL-401—405 / FULL-704 产品与期限只读消费者

状态：生产前端已实现，直接模块检查已完成；本批未运行 PostgreSQL、真实浏览器或版本全量。原六个 FULL 编号继续 PENDING，没有将旧文件或 HTTP 夹具当成关闭证据。按用户功能优先修订推进，不改正式模拟历史、后端权限、目录原件或原失败。

原验收来源：`docs/spec/requirements-traceability.md:76` 至80的目录旧 run 重放、产品过滤、有限组合、目标梯度及早退损失；第99行 FULL-704 仍要求全目标归属、冲突、范围、到期与修改流程。本页只实现已存在只读合同的产品/期限展示，不能关闭这些完整要求。

## 可运行能力与集成

新增无参数 `FullProductsPage`，建议 Root 接 `#products`，导航“产品与期限”。本页没有 POST、金融写入门或自动重试，不需要新增全局 pending store。App/GoalsPage 路由与共享合同由 Root 独占，本批未修改。

- `GET /api/v1/catalog/products`：展示所有已登记不可变原产品版本、当前整行源匹配/漂移、有效窗口、原金额/风险/本金波动/锁定/赎回延迟与完整原条款 JSON。历史原件即使漂移也保留，UNKNOWN 的具体 issues 不隐藏。服务端不可变校验声明不等同客户端独立金融验证。没有静默调用登记 POST。
- `GET /api/v1/positions`：按真实 `available_at` 分桶，单独显示 `maturity_at`、原本金、状态、目标与购买授权版本。日期为 null 保留 UNKNOWN，不按产品名称补日期，不将持仓本金加入今天现金，也不把 MATURED 状态称银行实际到账。
- `GET /api/v1/full-policies` 和 `/{policy_id}/asset-allocation`：只选实际 AssetAuthorizationPolicy，用户主动读取当前期限过滤理由、财务/单批上限、今日条件批次、真实源账户分量、收益比较期、换手/复杂度及梯度结果。查询只使用真实 `planning_comparison_days`（1—365，默认90）、`planning_max_components`（1—4）、`planning_max_turnover_cents`、`planning_funds_use_date`、`planning_mode`；这些参数只收紧规划，不创建权限。FIXED_LADDER 的单一到期日不能伪称多到期梯度。
- `/{policy_id}/recovery-planning`：只读既有 RecoveryPolicy、购买归属、原报价/有效期、独立损失/费用/净额、条件回款时刻、限额理由、ASK_ONCE 与无损步骤。可给带时区的更早 `planning_deadline_at`，不发行新报价，不提交赎回。有损候选不混进无损现金计划。展示原未覆盖阶段和持续安全首阶段点；客户端核这些点与服务端原曲线绑定，没有替代服务端金融算法。

当前保护合同明确为 ORIGINAL_VERIFIED_365_DAY_CURVE。365未来日期加初始日，每日3阶段共1098个点，**不是1098天**；90仅为默认收益比较期。未来收入计入0、planning_only=true、bank_authority=false、execution_support=NOT_IMPLEMENTED 始终可见。未知金额/计划/曲线保留 null，不画0成功金额。

Reader 校验当前 generated DTO 的 UUID、时间、整分安全范围、原 product/version/terms/catalogue 绑定、365完整三阶段、查询约束回传、单批账户金额守恒、今日 purchase_at、批次数及真实梯度；既有 RecoveryQuote 没有 simulation 字段，沿真实合同读取，不虚构这个字段。保留原 HTTP 响应文本供详情阅读。目录单版本 GET 返回 CatalogVersionView 无 simulation，现共享 HTTP 门要求 simulation；本页使用已返回完整版本列表，没有擅改共享门或伪造新 endpoint。

## 新文件

- `apps/web/src/api/full-products.ts`
- `apps/web/src/pages/FullProductsPage.tsx`
- `apps/web/src/api/full-products.test.ts`
- `apps/web/src/pages/FullProductsPage.test.tsx`
- `apps/web/src/tests/full-products-fixture.ts`

复用既有卡片、field、annual-amounts、readonly-raw 样式，没有修改共享样式或其他页面。夹具明确 SYNTHETIC_HTTP_FIXTURE_ONLY，其中原件/哈希/结果均是检查 reader 的合成输入，不能证明 PG、收益优势、产品适配金融正确性或浏览器实际可用。

## 实际检查与保留失败

均由原 `scripts/run_scoped_check.py --task W6` 执行；源前后与各 scope 在下列运行均稳定，没有把合批失败重新标成功。

1. 初次旧类型错误记录在 `.runtime/FULL-401-405-products-ui-20261005T1706Z/types-first-failure.md`，两源原字节保存在同目录 `types-first-source`。修正真实 JSON 默认字段的 Required 类型，未修改 generated contracts。
2. `full-products-ui-module-first-20261005T172246Z-5064aa4f`：原42 reader PASS、3页面 FAIL、总44 PASS/3 FAIL，失败为原JSON与摘要重复匹配，原日志保留。
3. `full-products-ui-types-after-narrow-fix-20261005T172236Z-74a87a77`：FAILED，合成 fixture 缺实际 `first_sustained_safe_point`；补真实字段、点/触发器关联和展示，未放宽服务合同。
4. `full-products-ui-module-repaired-20261005T172648Z-99c5400a`：原45 reader PASS、4页面 PASS/1 FAIL，整体 **FAILED**；末个失败仍为费用摘要与边界0金额匹配歧义。Vitest总4.32秒，wrapper6.140883秒，不称50全通过。
5. 修正为“原费用”字段下的精确断言后，只重跑相关页面：`full-products-ui-fee-summary-page-repaired-20261005T172756Z-05971668` **5 PASS/0 FAIL**，3.07秒，wrapper4.882266秒。此前45 reader 与其生产/fixture源字节一致，按最小节奏复用，不重跑金融链。这里是分次45+5个节点通过，没有声称同一次全量50 PASS。
6. 整体 Web `pnpm.cmd --filter @bounded-funds/web typecheck`：`full-products-ui-final-types-20261005T172644Z-606b6f44` exit0，wrapper11.708664秒；五源 ESLint `full-products-ui-final-local-lint-20261005T172644Z-6458881f` exit0。随后仅页面 test 的费用字段限定/标题变更，其最终单文件 ESLint `full-products-ui-fee-summary-test-local-lint-20261005T172751Z-6b559c2a` exit0，生产、fixture、reader 字节未再变。

证据目录前缀为 `docs/progress/evidence/W6/`，每目录的 manifest、原 output.log、source.before/after 保留。命令分别是 `pnpm.cmd --filter @bounded-funds/web exec vitest run src/api/full-products.test.ts src/pages/FullProductsPage.test.tsx`、修后只运行 `src/pages/FullProductsPage.test.tsx`；静态只显式五源或末次页面 test，不运行全量 test/e2e。

最终逐字节归档及 SHA：`.runtime/FULL-401-405-products-ui-final-20261005T172952Z-54f50b2f/manifest.json`，SHA256 `e0c19e51d1802bd7f414dd263553e8cc8c02dd75e8efe06afcf55833338ee26b`；含各原 proof、保留失败路径以及最终文件与原 source.after 的比较。生产 reader `38ed049c76125d3d2dd3379149b4da403bd9da527f9f96fb68a49e723d8133be`，页面 `656f913c7b73cfd20fec6021163e9188e27623b00c3969a4fbb8f971b2f3a3a6`。

## 未覆盖与下一依赖

导航集成、真实浏览器/移动端/键盘验收未在本批运行。旧决策按不可变目录当时版本重放仍未实现；页面明确 legacy_decisions_bound_to_this_catalogue=false，不能用展示历史版本替代 FULL-401 原验收。

当前实际目录哪些类存在以服务器返回为准；root此前实际仅取得单一30天批次，7/90天缺类不得用 fixture 补实证。现规划是今日有限组合/whole-position恢复，不是未来多期定存购买日程、分笔赎回或自动续期。原 rollover/auto_rollover 未给字段显示 UNKNOWN，续期执行接口缺失。

完整 FULL 新 dated/seasonal 储备及完整未来借记保护以服务返回 limitations 为准；本页不扩大原365保护范围。早退有损 ASK 只表示仍需独立选择/明确确认，原FULL限额、bank_authority=false与执行未接保持。跨目标冲突修复、实际金融 consumers、真多类产品适配/收益并列/梯度与早退全场景、原件恢复回执和初版/完整版集中验收仍由后续原验收节点补证。

