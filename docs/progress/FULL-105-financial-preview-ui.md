# FULL-105 财务修改预览前端增量

状态：PARTIAL_IMPLEMENTED / PENDING。本批交付独立生产消费者；不是初版/完整版全量验收，不关闭 FULL-105。

原验收追踪：`docs/spec/requirements-traceability.md` 第59行，F:1192–1194，要求修改前预览自主资金、目标、持仓与未来动作；无副作用，实际确认后差分与预览一致，并有 UI/API 测试。后台冻结依据 `.runtime/FULL-105-financial-preview-final-20261005T230458Z-cfcda20b/HANDOFF.md`；实际接口类型来自当前 Main 生成的 `FullPolicyChangeFinancialPreview`，生成核对原件 `W6/actual-financial-change-preview-openapi-check-20261005T231402Z-c4151f7d`。

本批新增以下五个 Web 文件，未改旧 FullPoliciesPanel、App、GoalsPage、HTTP、生成合同或金融后台：

- `api/full-policy-financial-preview.ts`：真实 `POST /api/v1/full-policies/{policy_id}/financial-change-preview`，body 只有 `expected_version_id` 与 `configuration`；严格 reader 保留实际原响应文本。
- `components/FinancialChangeImpactPanel.tsx`：手动预览，完整366日期、每天三个阶段，原/候选金额、负差額、逐产品上限，目标/持仓的当前零写差分与未来 UNKNOWN 分开显示。
- 对应两个 reader/Panel 测试文件以及 `tests/financial-preview-fixture.ts`。夹具明确为 HTTP_SYNTHETIC_NOT_FINANCIAL_PROOF，不是数据库、模拟银行或浏览器证据。

宿主接线：默认导出 `FinancialChangeImpactPanel`，props 为 `{ policyId, userId, expectedVersionId, candidateConfiguration, mutationBlocked? }`。候选由原编辑器传入；仅用户点击才 POST，无自动请求或重试，无确认按钮。不用 key/accepted/金额/时钟注入新的金融请求。`mutationBlocked` 表达其他族的未决请求。原/候选/version/user props 变化立即隐藏旧预览；晚到的前一请求不能重新发布。当前响应没有独立 `user_id`，userId 为宿主身份显示，归属由服务器原 owner 检查；前端不宣称自行核验银行或 owner。

reader 校验真实 `simulation/hypothetical/no-authority/no-write/future-income=0`，policy/version 与发出的原 body 一致；服务端可填默认配置，但每个已提交字段必须仍与返回规范化候选一致。Dated/Periodic 配置 SHA 与实际规范化 JSON、候选完整曲线 SHA 与 `hypothetical-full-curve-v1` 输入 hash/1098 原点交叉核对。该核对仅是内部表示一致性，不重造金融引擎或审计链。未知 template 的浮点 DSL 不套整数金融命令编码算法；其 server configuration hash 作为原字段展示。

1098点不截断：修改前校验0..365日与三阶段、完整顺序；候选与原 day/date/phase 与本金来源绑定一致，金额为可精确表示的整数分。PROJECTED 的标量和逐原 product 差额逐项等于 after−before（BigInt 比对），产品分母不能缩减；候选最低余量与原完整点一致，候选 curve hash 不容修改。UNKNOWN 的候选/全部 delta 保持 null，原曲线可保留，不能填0；目标未来分配 null、持仓未来处置 UNKNOWN。已 REDEEMED 持仓分开显示原记录本金和当前未返还0，不把原本金当现有可用本金。

支持口径与后台一致：目前只有 DatedExpensePolicy、PeriodicTransferPolicy 的未来保守待付替换；今天、逾期、已到期月、其他保护和占用保留。其他模板/缺来源/旧版本欠付未证明显示具体 UNKNOWN。future_income计入0不是未来真实收入0。当前目标分配和本金 delta0只表示事实未写，不是未来优化效果0。原行动 IDs 保留，确认后必须重新取得当前事实并重算；预览没有资金权限。

本次实际检查及原件（完整命令在各 manifest.command）：

| 检查 | 真实终态与范围 | 原件 |
| --- | --- | --- |
| 首次 Vitest 启动 | FAILED，esbuild spawn EPERM，未删原件 | `W6/policy-financial-preview-ui-direct-20261005T231621Z-669952c1` |
| 允许启动后的两个模块 | 20 reader PASS；2 Panel PASS、2 Panel FAIL。整个 wrapper 仍 FAILED | `W6/policy-financial-preview-ui-direct-permitted-20261005T231646Z-ae69da09` |
| 面板修复中间失败 | 缺币种/UNKNOWN表达修复时发生一行递归格式替换错误，4FAIL，旧源码/日志保留 | `W6/policy-financial-preview-ui-panel-final-20261005T231755Z-dade0dba` |
| 修递归后的面板 | 3PASS1FAIL，测试 query 抓到第三项产品负差额。限制原2个总览 dd 后重跑；断言不删除 | `W6/policy-financial-preview-ui-panel-format-fixed-20261005T231843Z-d3f4d191` |
| 最终 Panel | 4PASS，Vitest3.27s，wrapper4.398096s；scope稳定，全source因独立宿主变化不稳定 | `W6/policy-financial-preview-ui-panel-query-fixed-20261005T231929Z-a4366fde` |
| 首5源 lint | exit0；scope稳定 | `W6/policy-financial-preview-ui-lint-20261005T231622Z-2d605384` |
| 最终 Panel lint | exit0 | `W6/policy-financial-preview-ui-panel-format-lint-20261005T231839Z-7951b789` |
| 最终 Panel test lint | exit0 | `W6/policy-financial-preview-ui-final-test-lint-20261005T232013Z-02f0e2c7` |
| 最终整体 Web types | exit0，覆盖 Root 当前 nullable Goal/epoch 宿主守门与所有生成类型 | `W6/policy-financial-preview-ui-final-web-types-20261005T232013Z-c63e5406` |

24个唯一直接测试得到可复用 PASS：20个来自仍保 FAILED 的混合原批，最终 reader、reader测试和共享夹具 SHA 与该批 source.after 完全相同；另4个来自最终 Panel 单模块。不得写成“同一批24全过”。原 type 失败、递归失败、查询失败和 sandbox 失败及 exact before 源均保留在 evidence 与 `.runtime/FULL-105-ui-*before-*`。本批没有运行 PG、浏览器、Docker、完整check或正式库操作。

尚未覆盖：实际确认后的差分一致性；10个其他模板完整财务影响；候选目标求解/未来行动生成；历史版本未结清覆盖；所有资产可购性/银行独立经济效果验证；宿主接线的真实浏览器、移动与整体无障碍验收。根协调器接旧 change editor 后安排真实金融和集中验收。原编号保持 PENDING。
