# FULL-105：多模板金融预览 v3 实际前端消费者

2026-10-06 本批新增独立生产 reader/组件。原FULL-105仍 **PENDING / PARTIAL**，本批不证明确认后的真实金融差分，不代替原v1/history-v2入口或实际浏览器。

## 已交付能力与宿主接口

组件 `apps/web/src/components/MultiTemplateFinancialPreviewPanel.tsx`，default export；props只有 `{ mutationBlocked?: boolean }`。根可以把它放在策略中心金融写fieldset以外，或挂独立 `#policy-impact`。根独占App/FullPoliciesPanel，本批没有改宿主、主HTTP、shared schema、backend、旧105页面。

页面默认不发请求。用户手动GET资金总览、MVP策略列表、FULL策略列表，从真实current版本选择来源；不提供自由输入policy/user/version/epoch身份。MVP来源必须原confirmation owner/policy/version/accepted/hash与当前实际用户吻合，版本授权且ACTIVE/CONFIRMED；FULL来源用原严格列表reader核确认、owner/期/current引用。缺当前live有效审计或当前确认的来源不可选。前端没有把dashboard当OPEN状态证明：界面明确每次POST由服务端重核OPEN期及完整原版本/命令链。三次GET不是同一事务，漂移会被POST的expected身份拒绝，不能拼成金融授权。

候选编辑器从该当前版本完整配置初始化，只允许原字段集合/嵌套类型和同type；不新增BANK事实、clock、结果或grant。额度和日期始终是候选规则。实际Schema/引用和跨字段验证仍由服务端完成。JSON编辑不是新增策略或确认；原配置可以显示null，但null不是已知金融零。

显式POST只有 `/policy-financial-previews/{MVP_POLICY|FULL_POLICY}/{policy_id}` 以及 `{ expected_version_id, expected_epoch_id, configuration }` 三字段。使用实际生成的 `MultiTemplatePreviewRequest/Response` 类型。读取期间使用原HTTP write-flight、界面busy及根传入的mutationBlocked；跨族未决时不发新POST，而自己的来源GET仍可读。该POST为服务端RRRO预览，没有金融写入，不需要创建持久资金重试workspace。响应错误不自动重发；编辑或重读来源会废弃旧预览，旧请求晚到不能覆盖新候选。没有CONFIRM按钮、自动配置或资金动作。

## 严格消费与完整结果

- 独立验证v3协议、hypothetical/preview/no-authority/no-write旗、实际owner/epoch/policy/version/sourcekind/template及候选对应；保留原响应文本，不重做审计引擎。
- 两原配置hash按实际Python canonical核对；仅生活/季节quantile为原typed float，整数1保留1.0、极小值按Python科学记数表示，金额仍为精确整数分。没有把financial input/fact hash充配置确认hash，也不与旧协议hash比较。
- 原before和候选after必须完整1098点，366日期×付款前/付款后/本金到账后三阶段，日期/次序/原保护分项/余量与金额完整校验。PROJECTED只允许准确MVP保护范围；PARTIAL不冒充完整资金效果；UNKNOWN的候选curve/delta仍null且保留具体原因。
- 页面可以逐日读取全部三阶段；原完整JSON和source originals可查看。分母包括原Action/持仓以及服务端source_counts，不能删原金融分母让状态变成功。
- 单产品容量逐原catalog完整展示原值/候选值/差额/原条款及排除原因，不能相加为组合购买；全持仓回收候选保原quote/fee/loss/net/arrival/判断和条件准时净额差额。其条件boundary仅原MVP范围；它不是FULL恢复后曲线或当前现金。
- 当前期Goal分配保原全部Goal和income uses、8维目标、各当前版本/分配额/延期下界/完成日和完整结果；候选不是产权或多期全局最优。
- 当前Goal现金和本金差额0只表示没有修改原事实；未来Action始终 `UNKNOWN_REQUIRES_FRESH_EXECUTION_RECOMPUTATION`。未到账收入用于当前cash/execution为0，不宣称真实未来收入为0。

UNKNOWN仍允许显示已知原before，不能拿空候选/空列表当零金融效果。旧Dated/Periodic来源有明确原入口提示，其旧组件/算法/hash未动。Seasonal候选未采纳、CrossGoal专用scope未确认、Intervention没有金额公式等具体原因由真实响应展示，不将所有12模板Schema存在等同于支持。

## 现有确认接缝与准确未实现项

| 当前来源 | 已存在的合法确认入口 | 本批仍缺的金融差分接缝 |
|---|---|---|
| MVP经常义务/生活/应急/目标及原MVP资产 | 原 `PATCH /policies/{id}`，完整canonical配置、expected_version_id、accepted、reviewed_hash、reason、稳定idempotency_key；原PolicyCenter继续承担明确复核/持久恢复 | v3本身不提交；确认服务没有接本次v3完整事实/输入/1098曲线差量验证，配置hash吻合不等于同金融截面或确认后的实际差量已核 |
| FULL原8持久模板 | 原 `POST /full-policies/{id}/change`，完整canonical配置、expected_version_id、accepted、reviewed_hash、reason、原稳定key；原FullPoliciesPanel及原键GET保持 | 当前v3 Asset/Recovery/GoalAllocation仅PARTIAL规划；改变linked版本/引用后的全依赖与365保护需fresh重核。没有自动迁移旧持仓、自动回收/分配或新未来Action |
| 旧Dated/FutureDated历史/Periodic | 原v1与已安装history-v2预览、同原明确确认入口 | v3不覆盖未证明的今天/过期/账单/重复Periodic历史，不拿UNKNOWN替代原正确preview |
| Seasonal/CrossGoal/Intervention | 合法配置声明可走原生命周期；季节额采纳/专用Release consent/Question分别有原独立协议 | 新候选声明不是新采纳额、具体回拨effect或银行grant；对应金融影响依然UNKNOWN，不应自动确认并声称预览一致 |

因此只有**配置规范化/显式原生命周期声明**能沿现有服务衔接；本批不提供从财务预览直接确认的捷径。后继若要求FULL105“确认后差分与预览一致”，须新协议fresh核原源/原版本、明确的reviewed金融输入和实际确认后重新投影；不能仅copy候选hash、既存confirm结果或零事实写声称完成。

## 实际检查与失败保全

六个新Web源：reader、组件、两个直接test、fixture适配TS及原JSON。JSON为原Python纯域函数输出的明确 `TOOL_ONLY_SYNTHETIC_DOMAIN_DERIVED_NOT_PG_OR_BROWSER`；其3MB完整1098/各产品/全仓/Goal结果只作消费者夹具，非实际金融、PG或浏览器证明。没有剪短分母。

首默认Vitest被本机sandbox esbuild `spawn EPERM`阻断，原evidence `multi-template-preview-ui-first-direct-20261006T045847Z-e2aa9aac`保留。经允许的本机测试进程运行首轮20PASS/5FAIL：新reader误用通用scalar `*_cents` 检查合法两个delta map，原 `a95b91a2` 保留；只对这两个原map逐值校验，不放宽金额。修正后25PASS。首次wholeWeb types `3fbf8821`只有本reader counts闭包TS18046，原源码已归档；窄类型收窄后新增必要回收差额/unknownArrival风险。

最终 **26直接测试PASS/4.71s/0skip**，5TS源码ESLint exit0，两者global与scoped源稳定。原项全部负例保留：owner/epoch/version/source、grant/future income、config hash、金额、1098次序/分母、delta/原目录/全仓/Goal分母、额外POST字段、UNKNOWN非0、source owner错配、没有自动POST、跨族门与旧结果失效。

- [26直接风险](evidence/W6/multi-template-preview-ui-final-direct-20261006T050217Z-782a1150/manifest.json)
- [5TS源码静态](evidence/W6/multi-template-preview-ui-final-static-20261006T050213Z-6ac2362d/manifest.json)
- [实际项目strict选项的本5源码及imports类型](evidence/W6/multi-template-preview-ui-owned-strict-types-20261006T050420Z-99917ede/manifest.json)
- [整体Web类型原失败](evidence/W6/multi-template-preview-ui-final-current-web-types-20261006T050213Z-b79269f3/manifest.json)

本包owned strict通过，使用实际项目compilerOptions、限定本5TS入口和原Vite/Node/Vitest声明及实际imports；不是wholeWeb类型成功。整体Web这次exit2且apps/web+contracts scope稳定，仅他方 `registered-action-set.ts` 未对release/joint Family union收窄。已交根及对应owner修复，不共改、不重复整体typecheck。global两独立API新增source变化如原manifest记录。旧源和失败在 `.runtime/FULL-105-multi-ui/first-source-20261006T045830Z/`、`red-source-20261006T045947Z/`、`types-red-source-20261006T050149Z/` 保留。

## 仍未覆盖

根宿主入口/global gate实际集成、真实浏览器/手机/键盘、当前RRRO数据库source与物理全表零写、实际确认后差量、完整模板金融能力与未来动作、性能和全量验收均未由本批测得。原12模板支持边界遵循后端 `FULL-105-multi-template-v3.md`，不能关闭FULL105。没有运行PG、Browser、正式reset或真实资金。
