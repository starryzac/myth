# FULL-701：首次引导功能增量

root主应用集成（15:16 UTC）：已接第9个#onboarding导航、挂载recoverOnboardingDraft/useOnboardingDraft、其他策略/目标资金表单和FULL新请求的pending/busy/storage门，以及Demo新动作与重置阻挡。自身原候选GET核对按钮保持可用，未自动POST。App18+Demo12两个模块实际30PASS4.74s/wrapper6.391138s/all+scope稳定；types/lint exit0。原manifest：evidence/W6/app-onboarding-original-candidate-navigation-and-demo-gate-modules-20261005T151601Z-76953342，types151528Z-e9328fea、lint151529Z-194fe297。它们是HTTP fixture模块检查，不能当真实首次浏览器流程；下方交付时尚未App接入的历史文字以此增量更新。

2026-10-05，`FUNCTIONAL_INCREMENT_IMPLEMENTED / PRODUCT_ACCEPTANCE_PENDING`。原FULL-701仍为PENDING。本批按功能优先执行修订推进；未运行真实浏览器、数据库或全量，不改变原需求、失败记录或正式历史。

原需求来源：`钱途有界_完整开发计划_Codex执行版.md:1003–1014、1364–1366`；原验收为 `docs/spec/requirements-traceability.md:96` 的完整首次浏览器流程、退出恢复与确认行为、未确认不生权和可用性审查。8步是填写/浏览位置，不能当作8项完成或授权勾选。

## 已可运行能力与每步范围

| 原步骤 | 本批实际能力 | 尚未覆盖 |
| --- | --- | --- |
| 1 读取模拟账户规律 | GET账户摘要、当前dashboard与demo周期；原交易50条分页，保total/offset/来源、分类与一次性标记；原响应另存 | 三个GET不是同一事务快照；不把一页交易称完整规律分析 |
| 2 提出房租、账单和还款候选 | 先GET原提案列表；仅用户主动点击POST原discover；显示实际新建/复用ID和跳过理由，没有自动确认 | 现服务仅月房租及信用卡账期候选；其他账单/贷款规律未接。发现丢响应缺原请求持久回执，不凭后来列表推断成功 |
| 3 用户确认持续时间和优先级 | 读取实际周期义务候选，填写有效期/重要程度；展示完整原配置及拟配置；主动POST创建USER_DECLARED新候选，绑定来源proposal；原金额/payee/auto_execute/其余priority字段保留，原提案和hash不改 | 实际明确确认沿既有策略中心；尚无向导内一体确认链和完整浏览器证明 |
| 4 历史日常支出提出准备金 | 用户主动GET原生活估算；完整日/窗口分母、rank、覆盖缺口、排除/分类条件、input_digest和建议配置可读；READY后第二次主动声明候选 | INSUFFICIENT_HISTORY保持null/UNKNOWN，不能声明不存在的候选；独立明确确认沿策略中心。估算结果退出后重新GET，只保存参数与已登记声明身份 |
| 5 设置应急 | 原自然语言rules编译POST；再GET同原编译，保存原编译/提案/hash/输入及服务日期锚；问题和假设逐项复核 | 确认前仍候选；明确修订/确认沿策略中心，不自动补字段或授予权限 |
| 6 创建未来目标 | 原rules编译、原候选GET、完整目标配置/问题可读；跳转既有明确确认及目标账户登记入口；现有目标数量单独显示 | 没有自动创建目标；向导尚未接FULL额外属性双hash确认和目标原键恢复UI。完整新目标/后续资金归属链待集中浏览器验收 |
| 7 确认资产授权和安全恢复 | 本地逐项收集作用域/实际目标、资产类、总额/单次上限、到账/锁定/风险容忍与两种恢复条件；展示完整配置；主动创建MVP原DSL候选，原键核对；原资产策略仍从服务读取 | 新声明本身无银行/执行权限；确认仍沿策略中心，具体执行/损失确认由原服务验证。无Scenario实验接口调用，无演示固定模板替代自定义候选 |
| 8 自主资金与权力边界 | 实际dashboard当前财务安全闲置额/保护额/余量/缺口，null为UNKNOWN；显示约束来源及候选/未来收入/损失边界 | 财务safe_idle不是自动执行额度；资产权限/具体执行门未合并成金额，当前可自主执行金额明确UNKNOWN，不推断成功 |

页面局部手机堆叠、长ID换行、明确label与focus样式已实现；没有宣称全产品无障碍或真人可用性已完成。本向导不重复完整12模板JSON目录。

## 实际请求与原请求恢复

- 只读：`/accounts/summary`、`/transactions?limit=50&offset=…`、`/dashboard`、`/demo/state`、`/policy-proposals`、`/policies`、`/goals`、`/living-reserve/estimate`及`/policy-compilations/{id}`。
- rules编译仅显式用户点击，原body `{text,engine:"rules"}`；服务user/epoch/日期/时区先保存。旧compile无client key，不造键；丢响应仅从原text/编译版本、原提案及原编译日期唯一匹配的GET恢复。跨日/空源/多候选无法匹配保持pending，不自动POST。
- discover仅显式POST `{}`。已知实际响应后再GET原提案核对回执IDs才保存；未知响应仅列出现有候选，保留pending。列表不是该未知请求的持久回执。
- 结构化候选真实POST `/policy-declarations`，原完整body `{configuration,idempotency_key,expected_epoch_id,source_proposal_id}`；键为符合原ASCII规则的`onboarding-<kind>:<UUID>`。3/4/7分别提交实际recurring_obligation/living_reserve/asset_authorization完整配置。后端完整严格DSL校验，客户端数值/日期/作用域检查不等于服务授权。
- 原body/key/实际user和周期在发送之前保存。POST返回还须GET `/policy-declarations/{originalEpoch}/by-key/{encodeURIComponent(originalKey)}`，完整协议、原body、epoch、候选/Evidence身份及两hash匹配才解除本地pending。RECORDED只证明原候选存在；`grants_authority=false`、`receipt_is_current_authority=false`。专用审计事件为false，不伪称该新接口记录了专用事件。
- NOT_FOUND的`not_found_is_final=false`保留原请求/写门；用户可只读继续核对，或明确手动重放同原body/key。没有自动重试、新键或重新规范化body。用户/周期变化不向新周期重放；历史记录保留。声明日期只是本地来源锚，原键恢复绑定实际原user/epoch，不把日期改变当新声明身份。
- 编译与发现的未知请求不能借声明的重放能力继续POST。坏session、存储读取/保存失败、其他demo/FULL家族pending/busy/storage_error以及实际writeflight均禁止新的引导写入。所有候选存储不包含授权/完成flag；没有跨请求授权缓存。
- 当前无持久终局拒绝合同。已发送声明若错误/NOT_FOUND仍不能据此清pending；安全保存原件与门，不能依旧键409或删除session粗暴恢复。该缺口继续登记。

真实后端合同由root生产安装/生成，参见 `apps/api/app/services/user_policy_declaration.py`、`apps/api/app/api/v1/user_policy_declaration.py`、`app/domain/policy_configuration.py`。其专项PG原件为 `evidence/W2/structured-user-candidate-original-confirmation-real-pg-20261005T144122Z-418b5478/manifest.json`；该后端检查由root执行，本批不把它称向导浏览器证明。确认继续使用既有confirmProposal(proposal_id,configuration_hash,accepted=true)，本批不在向导发confirm。

## 文件与root接入

新增 `apps/web/src/pages/OnboardingPage.tsx`、`api/onboarding.ts`、`features/onboarding-draft.ts`、各相关测试和 `tests/onboarding-fixture.ts`；styles.css仅追加onboarding局部规则。示例数据是明确的 `SYNTHETIC_HTTP_FIXTURE_ONLY`，金额/状态不当作金融或真人效果。无迁移、种子、后端、App、contracts、旧sharedpending或工具修改。

页面default无props；建议路由`#onboarding`，导航标题“首次引导”。root独占App集成。App挂载必须调用`recoverOnboardingDraft()`并订阅`useOnboardingDraft()`，其snapshot为`{draft,busy,storage_error}`；其他资金写入/重置门加入`busy || draft.pending !== null || storage_error !== null`。无效session也要阻新写；不能先访问向导才恢复。向导自己内部处理其他demo/FULL/writeflight，本族pending只能原身份GET或用户手动同原声明恢复，不能拿导航进度当已确认。

sessionStorage保存原输入、step、已知原编译/声明身份及未决原请求；首次退出恢复是草稿恢复，不是服务权限缓存。严格固定字段/来源锚检查，坏数据保留字节并锁新写；没有静默丢弃或清空旧记录。

## 已运行检查和原失败

以下均通过`uv --cache-dir .uv-cache run --offline python scripts/run_scoped_check.py --task W6 --label <…> --source-prefix <本批8源> -- <原命令>`记录原命令/终局退出/源前后hash/日志。只跑此批及直接风险测试，没有PG/browser/full suite。

| 原命令 | 实际终局 | 原manifest |
| --- | --- | --- |
| `pnpm.cmd --filter @bounded-funds/web typecheck` | exit0，wrapper5.710533s；全源/本批源稳定 | `evidence/W6/full701-onboarding-ui-types-final-20261005T151045Z-faafd603/manifest.json` |
| `pnpm.cmd --filter @bounded-funds/web exec eslint src/pages/OnboardingPage.tsx src/pages/OnboardingPage.test.tsx src/features/onboarding-draft.ts src/features/onboarding-draft.test.ts src/api/onboarding.ts src/api/onboarding.test.ts src/tests/onboarding-fixture.ts --max-warnings 0` | exit0，wrapper4.045996s；全源/本批源稳定 | `evidence/W6/full701-onboarding-ui-lint-final-20261005T151045Z-bfdea205/manifest.json` |
| `pnpm.cmd --filter @bounded-funds/web exec vitest run src/api/onboarding.test.ts src/features/onboarding-draft.test.ts src/pages/OnboardingPage.test.tsx` | 45/45 PASS，Vitest4.76s/wrapper6.533919s，0skip；全源/本批源稳定 | `evidence/W6/full701-onboarding-related-vitest-repaired-20261005T151049Z-55c8508d/manifest.json` |

45项分母：API16、草稿/原请求16、页面13。覆盖完整原body/key保存、坏存储、其他家族门、未知结果保持、NOT_FOUND非终局、错body/epoch/hash/权限拒绝、同键手动声明重放、compile唯一GET恢复/跨日拒绝、discover未知不得据列表解除、原金额/来源不变、生活完整分母与null、资产范围/cap/风险/恢复条件。纯HTTP夹具测试不算正式金融/浏览器效果。

首次typecheck8错误与首轮Vitest6失败/39通过原件未更改：`full701-onboarding-source-types-20261005T144234Z-5be4d8e8`（闭包narrowing/生成optional字段）及`full701-onboarding-related-vitest-20261005T150821Z-3eb15074`（5处测试匹配器错误期待原fetch exception，实际共享HTTP正确显示连接中断；1处原JSON与li重复文字）。修复测试定位保留原风险断言；另把财务safe_idle的展示明确为财务额，不冒自主执行额度。首轮失败测试完整字节、类型失败三源、前期源码/原样式保存在`.runtime/FULL-701-onboarding-20261005T142524Z/`；每次原失败源hash/log继续保留。该目录的时间串是工作包标识，最终source-final.json另记录实际UTC时间。

## 下一前置与不关闭项

root接App导航与全局门后，集中真实首次8步/退出刷新/明确确认浏览器链；补FULL目标额外属性确认UI、权限/执行条件合并后的自主额度，以及其他义务发现能力。人工可用性审查/真人研究未开展；日常模块通过不能替代这些证据。原追踪状态和编号全部保留，FULL-701不关闭。
