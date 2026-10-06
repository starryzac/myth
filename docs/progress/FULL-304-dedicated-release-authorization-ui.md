# FULL-304 专用目标现金回拨授权界面

2026-10-06 03:47 Root集成：主App接全局原授权请求恢复与跨族写门，目标页新增惰性入口；来源目标不在当前列表、当前dashboard未知时仍可核原epoch/key，严格原件匹配才清门。清门后保留原历史展示，不把它缓存成当前权限。Root App23+Goals15共38相关测试PASS14.65s（W6/dedicated-release-parent-global-gate-and-original-lookup-20261005T194448Z-ebebe7ec），四Root源ESLint PASS（1a90e2e8）；范围源稳定、全源因独立执行模块变化false。整体Web类型待当前实际回拨合同与FULL306页面源稳定后一次取得。银行执行UI、真实浏览器及集中验收尚缺。

状态：`DEDICATED_SCOPE_CONFIRMATION_UI_IMPLEMENTED`，FULL-303/304仍PENDING。本批对应原完整计划1248–1254与需求追踪表71–72行的显式紧急回拨授权部分；不缩减原跨目标默认关闭、额度/条件/版本、撤销、非紧急拒绝、产权与资产分离、真实回拨证据和回执要求。先前只读预览和后端批次文档保留为原交付，不回写旧失败或将历史声明改成成功。

独占新增7文件：`api/goal-release-authorizations.ts`、`features/goal-release-authorization-operation.ts`、`components/GoalReleaseAuthorizationPanel.tsx`、对应三个直接测试及 `tests/goal-release-authorization-fixture.ts`。未修改App、GoalsPage、共享HTTP/pending、generated contracts或金融后端。复用当前真实生成 `ReleaseAuthorizationPreview/Response/Lookup/Confirmation/GoalReleaseScope`，不手写假DTO。

真实调用流程：

1. 初始只GET `/full-policies`。用户选择当前已存在、启用、原owner/epoch一致的CrossGoalReallocationPolicy后主动准备复核；不自动创建、确认或discover策略。
2. GET当前单条 `/full-policies/{id}`、原 `/goals` 和规则列出的每个 `/goals/{id}/full-model`。保各原HTTP文本，原Goal/模型必须齐全、当前原版本/epoch匹配。用户主动POST `/goal-release-authorizations/policies/{id}/preview`，正文只有expected_epoch_id、expected_policy_version_id；不含客户端金额、时钟、资金结果、银行回执或授权旗。
3. 逐项展示规则的全部来源Goal（不只当前卡片）：原Goal policy/version、FULL模型证据及hash、完整配置hash、最低保障；展示同一owner/epoch、目的PROTECTED_CASH、实际紧急条件、单次和同策略跨版本累计cap、有效开始/结束、默认现金归属锁的有限例外。本金释放、普通跨目标再分配、生成新收入、改写原已归属收入均false。
4. 对实际JSON按Python原 `configuration_hash` 的UTF-8/sort_keys/紧凑协议重算配置、范围、原请求和原证据SHA。先完成完整scope复核，再由用户勾选明确接受，POST `/policies/{id}/confirm` 恰好5字段：epoch/version/reviewed_scope_hash/accepted:true/稳定key。POST前持久全部scope、body及其原JSON、ownerGoal、原路径和request envelope SHA；原UUID5授权/evidence identity也与服务原协议核对。该功能记录专用权限范围，未提交任何资金回拨。
5. HTTP200、网络丢失、解析错误、4xx均保原pending。恢复只用户主动GET `/commands/{原epoch}/by-key/{原key}`，没有自动POST、换键、接受或重试按钮。NOT_FOUND_NOT_FINAL保原请求；只完整RECORDED的owner/epoch/key、原5字段、全部scope、请求hash、UUID5、原证据hash和回执一致后解除本族门。STALE/UNKNOWN/ARCHIVED仍是原历史记录，不当当前授权；当前服务读取状态CURRENT也不跨请求缓存有效权限。

原HTTP来源与本地展示明确区分：preview/confirm/by-key原文均保留；lookup同时绑定完整GET原封套。若没有原文本，仅显示“JSON展示（非HTTP原字节）”。浏览器只核返回内容/原请求关系，trace_hash为实际服务引用与格式核对，未独立读取全trace或重算完整审计/银行账本，不称独立经济验真。

生产接入合同：`GoalReleaseAuthorizationPanel({goal,userId,epochId,mutationBlocked?})`，goal取旧Goal id/name/policy_id/policy_version_id，epoch未知不能准备新确认。Root负责在原Goal资金fieldset之外接此组件及跨族金融门；`mutationBlocked`只代表其他族，自身原GET恢复始终可达。导出 `recoverGoalReleaseAuthorizationOperation/useGoalReleaseAuthorizationOperation/getGoalReleaseAuthorizationOperation`，snapshot为 `{pending,busy,storage_error}`。Root应在App恢复该store，并将其待核对/忙/存储错误纳入所有其他金融族/演示reset门；本组件自身既检查原demo/fullpolicy/fullgoal/onboarding/question/spending/intervention族及writeflight，也保自身原body/key不被替换。Root全局接入未完成时不声称其他页面已受此新门保护。

实际模块检查：

| 范围 | 结果 | 原证据目录（docs/progress/evidence/W6） |
| --- | --- | --- |
| reader39 + store16 + panel7直接测试 | 62 PASS，Vitest6.34s / wrapper8.109224s，exit0 | goal-release-authorization-ui-final-direct-20261005T194023Z-447210e5 |
| 新7源+imports按原项目全部strict/noUnchecked等参数类型 | exit0；不是全Web types | goal-release-authorization-ui-final-scoped-types-20261005T194019Z-e1788774 |
| 新7源与前303 reader ESLint | exit0 | goal-release-authorization-ui-final-static-20261005T194019Z-fbb9ee0e |

三个check均本scope稳定；直接测试all_source_stable=true；types/lint的all_source_stable=false只因并行他方 `apps/api/app/services/full_goal_release_execution.py` 变化，scoped_source_stable=true。不把本check当全仓冻结验收。首次direct61PASS/1FAIL（测试URL未期待colon的正确%3A编码）、首次scope types因前303 reader字典索引仍可能undefined失败，原d84b1f41/5f829e1c完整保留。对应窄修为URL测试expect采用encodeURIComponent、前303严格完整三条件校验后的non-null类型断言；运行时原判定和原55项测试未改、不重跑。新scope types覆盖该reader依赖通过。

夹具均显式SYNTHETIC_HTTP_ONLY：只证明界面、摘要格式与恢复门，不是银行、PG或实际浏览器证明。Python原domain对固定请求封套实际计算的SHA256 `05e643c51e7d91c6f5d6e48a4c903cfe958877fa70cd021016f1326026ca5f29` 与UUID5 `bf0f8e71-ff96-5722-ad68-8727f655d679` 用作跨语言工具golden，不是金融授权原件。

具体未覆盖：

- Root App/GoalPage共享门与导航/懒加载实际接入、完整Web类型、真实浏览器集中验收未由本批执行。无PG/browser/full suite。
- 当前此确认DTO仍 `current_financial_amount_verified=false`、`execution_support=NOT_IMPLEMENTED`、`submits_bank_operation=false`。专用范围原记录不证明回拨候选金额、当前紧急情况、完整累计用量、原产权现金可释放或银行当前资金权限。新执行消费者另包接入，不由此页面造成功。
- 当前银行/审计/原目标模型真伪依赖原服务生产验证；浏览器不独立复算全链。服务器必须保RRRO/当前原scope重核，存储localBody不是权限证据，不引入跨请求授权缓存。
- key当前UI只生成ASCII字母/数字/._:-最多150；不新增任意key编辑功能。不明结果只GET；NOT_FOUND非终局且没有持久最终拒绝协议时可能长期锁住新确认，这个明确缺口不能用新的key或HTTP409清门绕过。
- 本批不改变正式历史、失败视频、原证据、原编号关闭状态或真实资金接口。

下一前置：Root接全局新族门并完成当前原后台串行金融链、集中浏览器；真实回拨金额/累计账本/新双腿bank操作/UNKNOWN恢复须使用其独立真实协议，后续UI不得把此范围记录重新命名为已执行回拨。
