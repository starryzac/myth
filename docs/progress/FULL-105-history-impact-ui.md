# FULL-105：history-v2 独立只读消费者

## 当前能力与范围

2026-10-06 实现新的 `FinancialChangeHistoryImpactPanel` 和严格 reader。它手动调用真实独立 `POST /api/v1/full-policies/{policy_id}/financial-change-preview-history`，显示已核验原版本大于1的未来 Dated 修改曲线、原历史完整分母、精确差量及 UNKNOWN 具体原因。**26 项定向 HTTP/组件合成夹具通过；实际 generated alias 已接，Root 已接宿主，PG、浏览器未运行，FULL-105 PENDING。**

旧 `FinancialChangeImpactPanel.tsx` 和 `full-policy-financial-preview.ts` 原字节未改；App、FullPoliciesPanel、Main、dependencies、generated contracts 不归本包修改。旧 v1 输入、数学、哈希和旧失败保持。后台分支合同详见 [history-v2 服务记录](FULL-105-history-impact-v2.md)。

## 新文件与宿主接口

- `apps/web/src/api/full-policy-change-history.ts`：`previewFullPolicyHistoryFinancialChange(binding, body)`、`parseHistoryChangePreview` 与原 JSON WeakMap。只复用现 HTTP/write-flight、原严格 FullPreviewRequest body、原年度验证及有界 canonical hash；没有额外 Session/Trace/策略 GET 管线，也不在客户端重造金融/历史核验引擎。
- `apps/web/src/components/FinancialChangeHistoryImpactPanel.tsx`：独立可运行组件，props 为 `policyId,userId,expectedVersionId,expectedVersionNumber,candidateConfiguration,expectedEpochId?,mutationBlocked?`。当前实际版本号须2–128；宿主从真实 PolicyView/当前 epoch 传入。仅适合显式新 history-v2 分支；v1 仍保旧组件。
- 两个同名直接测试模块及 `tests/financial-history-preview-fixture.ts/.json`。JSON 由 `.runtime/FULL-105-history-web/build-fixture.py` 调用真实新域模型、原数学函数和完全 synthetic 原件构造，标注 `SYNTHETIC_PURE_MODEL_HTTP_SHAPE_NOT_PRODUCT_PROOF`；没有 DB/浏览器/银行 observation，不能被改称产品实证。

首次交付为显式 **provisional / NOT_GENERATED** DTO，原源码、文档及检查原件保存在 `.runtime/FULL-105-history-web/final-provisional-source-20261006T025855Z/`。随后 Root 从实际 Main 生成 `FullPolicyHistoryChangeFinancialPreview`，本 reader 已以真实 generated alias 派生类型；可选字段只在既有 runtime 校验其存在后返回 Required 消费形状，`history_proof` 仍允许 null。Root 的独立 `FullPolicyFinancialImpactHost` 按真实当前版本大于1选择本分支，传已解析 owner/version/epoch，其余仍用旧 v1。本族预览没有持久金融 pending、确认 key、accepted、资金执行或自动重试。

## 消费语义

reader 要求新 top/financial/curve 三个协议明确匹配，原请求 expectedVersion/config 与返回 canonical 字段/hash一致，outer/impact owner、epoch及当前版本绑定。完整 proof 原文本保存并检查 source digest、false 授权/结算旗、捕获数量和有限当前版本/原确认 envelope 的内部一致性。PROJECTED 必须完整当前 owner/epoch/version、实际版本分母、当前原配置/hash与原确认一致；完整银行、审计、所有历史连续链与原件有效性由服务端负责，这不是前端独立经济或历史验真。

原/候选1098点、366日期、每日期3阶段、全部产品分母、原本金时点、整数分/有符号差量与曲线hash严格校对；不能把负数截成0，不接受unsafe money、少一阶段或旧 v1 curvehash。新源摘要 `full-change-preview-history-actual-facts-v2` 单独展示，不能与旧事实hash比较或拿曲线hash授予权限。

UNKNOWN保留服务端 before（也可null）、after/deltas null、真实具体原因；missing proof 明确 MISSING。存在原 proof 时，UNKNOWN仍是诊断原件，不标为当前核验成功。季节或组合不能重建的结果以 `ORIGINAL_FULL_CURVE_NOT_RECONSTRUCTIBLE_FOR_THIS_HISTORY_BRANCH` 显示，不删除保护floor伪装成功。当前Goal/Position的0只是原事实只读未写；未来Goal分配/处置仍UNKNOWN。

首次渲染不发POST；用户显式点击。其它族门阻新请求，宿主 owner/epoch/version/config 改动隐藏旧结果；拒绝/解析错误隐藏先前成功，不自动重试。原返回的 server as_of 展示，预览不自动确认、不改当前Action、不发银行命令。

## 实际定向检查

| 命令范围 | 原件与结果 |
|---|---|
| 新reader22 + component4 Vitest | [26 PASS / 5.46s](evidence/W2/repeated-dated-history-web-owner-guard-final-direct-20261006T025728Z-e6f0b0d2/manifest.json) |
| 五个TS/TSX ESLint | [PASS](evidence/W2/repeated-dated-history-web-owner-guard-final-lint-20261006T025725Z-4da65785/manifest.json) |
| 原Web strict参数、五源及真实imports scoped tsc | [首次临时 DTO PASS](evidence/W2/repeated-dated-history-web-owner-guard-final-types-20261006T025726Z-ab15a52d/manifest.json)；不是整体Web tsc |
| actual generated alias 替换后的相关五源及 imports strict tsc | [PASS](evidence/W2/repeated-dated-history-actual-generated-alias-types-20261006T032255Z-24eaf501/manifest.json)，全源及相关 scope 稳定 |
| actual alias reader ESLint | [PASS](evidence/W2/repeated-dated-history-actual-generated-alias-lint-20261006T032256Z-43f9a006/manifest.json)，全源及相关 scope 稳定 |

两次 Vitest 启动路径错误原日志 `41f36630`（root不对、0用例）、`945d013c`（root/config重复）；初次 scoped tsc根 type 查找失败 `0654379d` 均保留。它们没有跑金融或改变生产逻辑。正确 Vitest `--root apps/web --config vitest.config.ts` 的首次26PASS `0272e946` 与初次 scoped types37a70938也保留。最终 scope配置仅使用现Web严格参数和实际已安装 typeRoots，无安装、豁免或新验收框架。

## 具体未覆盖

实际 Main注册/generated alias 已由 Root 完成（schema generate `9486234d`、Main/deps strict `e1391b9c` 为 Root 原件）；宿主也是 Root 独立交付，不将其接线声明当本包浏览器实证。整体Web类型由 Root 合并后一次执行。真实新服务PG candidate仅 collected1、未执行；真实HTTP、前端浏览器/手机、确认后第三版本全链一致性和最后全量均NOT_RUN。支持范围仍只是完整已核验的严格未来Dated原历史；其它模板、当日/过期/欠付历史、季节组合不可重建、未来Goal/Position/Action重算均未补造。旧前端/后台/API/失败及正式历史不覆写。

2026-10-06T03:22Z 的 alias 差量仅删除手写类型、导入实际 schema 并派生精确类型，没有修改任何运行表达式、reader/Panel 行为或测试夹具。未重复运行此前已通过的26行为案例。改前原字节与当前 schema SHA 存在 `.runtime/FULL-105-history-web/source-before-actual-schema-alias-20261006T032246Z/manifest.json`；新的终态 source freeze 单独保存，不覆盖 provisional 原件。
