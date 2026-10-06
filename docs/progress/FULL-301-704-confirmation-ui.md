# FULL-301 / FULL-704 完整目标双hash确认与原键恢复增量

最终类型证据已取得：`evidence/W6/fullannual-protection-final-types-20261005T155942Z-3af84aad` 整体Web tsc PASS，Root App/Goals/Onboarding 五源自54模块与局部lint捕获后未改，复用此当前检查；scoped稳定，all=false仅独立question工作流变化。原两次年度reader/Root tsc FAILED仍保留。年度消费者21不同测试通过、原页面和Full新三阶段查询均只读，见 FULL-201-702-full-protection-ui.md。未跑浏览器或最终全量。

Root 接入增量（2026-10-05 23:53 北京时间）：App mount 恢复原完整Goal命令并将 pending/busy/storage_error 纳入其他资金请求门；策略中心、演示重置与引导声明暂停新写。GoalsPage只将资金分配控件置入金融fieldset，完整模型与自己的原键GET在外，不被自身门锁住。Onboarding新写及同原body/key手动重放都纳入他族门，自己的原件只读核对仍可用。

定向四模块 App19/Goals10/Demo12/Onboarding13，共54不同测试 PASS/8.71s，wrapper10.60995s；`evidence/W6/full-goal-app-own-read-cross-family-gate-module-cmd-20261005T155342Z-c619fa1b` 全源与范围稳定。Root局部lint5源 PASS：`full-goal-app-goals-onboarding-local-lint-20261005T155438Z-2d797bce`。首次直接pnpm入口 WinError2未启动测试，原输出目录76d3a819保留；使用既有Windows cmd入口后才实际运行。整体web类型首FAILED `full-goal-root-integration-web-types-20261005T155554Z-88978d2f`，错误在新增FullAnnual reader字符串索引，该模块负责者正在窄修；尚未取得最终整体类型PASS。不称真实浏览器或全量验收。

2026-10-05。`FUNCTIONAL_INCREMENT_IMPLEMENTED / ACCEPTANCE_PENDING`。原需求仍PENDING，遵循功能优先执行修订；本批未关闭任务，未改旧计划、正式模拟历史、失败记录或金融后台。

原要求：`docs/spec/requirements-traceability.md:69`（FULL-301；原完整版计划1240–1242、485–500）与`:99`（FULL-704；1376–1378、1039–1048）。本批在既有只读模型、当前期联合规划与只读预览基础上，补完整目标的明确确认和持久原请求恢复；原只读交付及其当时缺口保留在`FULL-704.md`，本页记录后续差量。

## 已实现能力

`FullGoalModelPanel`保留原目标/策略/版本绑定的模型读取、缺模型UNKNOWN、完整属性及只读影响预览。用户明确点击预览后，展示服务器规范化完整配置与FULL/base两份hash；填写理由并勾选复核后，才可单独确认。修改候选、理由或刷新模型会取消旧接受；目标或原版本切换重建预览工作区，不能借旧预览确认新版本。

`POST /api/v1/goals/{goal_id}/full-model/confirm`只发送服务器预览的完整规范化配置、expected_version_id、expected_epoch_id、reviewed_full_hash、reviewed_base_hash、accepted=true、原理由和一次生成的稳定idempotency_key。发送前严格保存原user/goal/policy/path/body/body_json/key/hash到sessionStorage，并冻结嵌套对象。此记录只是恢复元数据，不提供权限；无法读取/保存、坏数据或摘要能力缺失时阻止新确认。

原确认请求摘要复用真实`full_goals._request_hash`协议：排序JSON键、UTF-8、SHA256，绑定user/goal/epoch/原预期版本/完整配置/reason/key/accepted。本批以独立Python stdlib生成的TOOL_ONLY golden与浏览器Web Crypto结果交叉核对；这仅验证原请求一致性，不重算银行效果、审计链或历史哈希。Web Crypto需要HTTPS或受信任localhost；不能用不安全摘要兜底。

无论POST成功、响应丢失、解析错误或4xx，原请求都继续pending。刷新仅恢复原记录，无自动POST。用户可明确点击只读`GET /api/v1/goals/{goal}/full-model/commands/by-key/{encodeURIComponent(key)}`；原完整键作为单一路径段，无query。NOT_FOUND的not_found_is_final=false保留原body/key，不生成新请求。手动重放需另行明确勾选，严格发送同一原目标、body、周期、版本、双hash和键，不自动重试。

仅RECORDED且原canonical request逐字段匹配、请求hash重算一致、goal/policy/epoch/key/双hash/原previous/new版本绑定、原回执结构、original_verified/audit_chain_verified/configuration_is_server_canonical等实际返回合同全部通过，才删除本族恢复记录。额外字段、主体漂移、同前后版本、错误hash、伪授权、未验证审计或删除失败仍保门。客户端并未独立验证金融账本；服务的审计验证报告与独立效果验真严格区分。

确认回执显示该命令原首次形成版本、原状态、确认时点、失效/在途行动名单；不能把历史receipt.current_version_id当作现在最新版本。查询提取的回执显示为派生结构，原查询response.text()文本另外保留；直接POST响应有原HTTP文本。bank_authority=false、receipt_is_current_authority=false、dedicated_audit_event=false、epoch_archive_verified=false保持原义，不推断当前银行权限或独立归档验真。

收到确认或完成原键核对后使真实goals/policies/dashboard/full-model/goal-allocation/joint-current-goal-allocation/annual-planning等Queries失效重新读；历史回执和当前模型分开展示。归属/联合规划/模型仍分别读取，不声称共同事务快照。跨目标回拨保持false/null，仅当前真实bridge支持字段可提交。

## 根页面接入

Panel props：`{goal: Pick<Goal, 'id'|'name'|'policy_id'|'policy_version_id'>, blocked?:boolean}`。blocked仅用于其他请求族及全局写入限制；不能把本族pending/storage_error放入blocked，否则会挡住自己的恢复。Panel自身阻止新确认，但自己的只读核对仍可用；另一目标pending显示原目标ID，不能换目标新写。

`features/full-goal-operation.ts`导出`recoverFullGoalOperation()`、`useFullGoalOperation()`，snapshot为`{pending,busy,storage_error}`。根App挂载应恢复此记录，并将其纳入其他资金写入/重置门。begin自身先恢复demo/full-policy/onboarding各族，任何pending/busy/storage_error及原writeflight都阻挡；非金融只读仍可使用。本批未改App、GoalsPage、共享HTTP/writeflight、onboarding源、contracts或后端，根集成由父任务承担；完整模型区域和原键恢复区域需位于旧资金fieldset外。

## 文件与验证

新增`features/full-goal-operation.ts`及风险测试；窄改`api/full-goals.ts`及测试、`components/FullGoalModelPanel.tsx`及测试、`tests/full-goal-fixture.ts`，追加局部响应式/focus/wrap样式。原修改前字节、独立golden和最终源SHA保存在`.runtime/FULL-301-704-goal-confirmation-20261005T152250Z/`。

使用既有scoped runner，没有扩展检查工具。真实命令：

- `pnpm.cmd --filter @bounded-funds/web typecheck`：退出0，8.941352秒。原manifest：`docs/progress/evidence/W6/fullgoal-confirmation-types-20261005T154006Z-cf8f04db/manifest.json`。
- 7个相关TS/TSX文件ESLint `--max-warnings 0`：退出0，3.738081秒。`fullgoal-confirmation-lint-20261005T154007Z-bb496241/manifest.json`。
- `pnpm.cmd --filter @bounded-funds/web exec vitest run src/api/full-goals.test.ts src/features/full-goal-operation.test.ts src/components/FullGoalModelPanel.test.tsx`：API8、store26、Panel10，共44项PASS，Vitest4.78秒、wrapper6.567519秒。`fullgoal-confirmation-related-vitest-20261005T154042Z-4f6f6e00/manifest.json`。

类型/lint的全源与相关源前后稳定；Vitest相关源稳定，但并行另一个工作包的三个policy_suggestions后端文件变化，所以all_source_stable=false，不能冒充全仓固定版本验收。44项均是合成HTTP/组件与纯请求恢复风险验证；没有本批PG、真实浏览器、金融执行或全量。旧RO测试保留，无失败改标或历史重置。

## 具体未覆盖及下一依赖

根App跨页全局门与GoalsPage只读区域分离尚需父任务接入和模块检查；真实确认→新版本→归属/规划重新读取，以及丢响应/刷新恢复的完整浏览器链未运行。最终代码初版/完整版全量、手机与键盘实际操作截图待集中节点。

NOT_FOUND及已知4xx仍非持久最终拒绝证明，因此会保留pending；当前API没有可验证的最终拒绝收据，不能换新键逃门。这是明确的可用性缺口，需要后端原请求终局协议，不能本地清空掩盖。sessionStorage同tab/同API恢复，不是跨tab/跨设备权限协调；禁用存储时新写阻挡。

专用FULL模型审计事件、epoch归档验证、独立银行效果验真、跨目标回拨、全局多期最优及完整365日额外模型财务影响仍未实现；该确认只使用现有真实执行bridge，未扩大金融权限。其他FULL-301字段行为与FULL-704冲突修复/资产配置/到期延期全链尚待后台能力和最终实证，不因本批确认UI通过而关闭原任务。
