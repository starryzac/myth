# FULL-604：单笔整额到期 USER ASK 客户端

2026-10-06。独立前端功能差量，FULL-604 整体仍 PENDING。复用已注册 `FullMaturityRequest/Preview/Action/Lookup/Consent` 实际 Schema，以及旧恢复客户端五身份验证、canonical/hash、UUID5、通用 HTTP/write-flight、本地签名会话读取；不改变旧恢复 store、旧金额/哈希或金融状态机。

## 交付能力与精确合同

默认 `FullMaturityExecutionPanel({policyId,userId,expectedVersionId,epochId,mutationBlocked?,showOriginalRecovery?=true})`。四个身份由 Root 的实际当前策略、accounts/demo GET 提供。面板实际 GET `/positions` 和本地 USER 会话，原持仓清单只用于选取真实 UUID，既不表示完整资金验真，也不要求旧恢复规划 READY。缺持仓/原期限不可输入伪 UUID；新的 `/full-maturity-actions/preview` 是是否能准备的唯一服务器范围证明。

准备请求闭合五字段：policy/version/epoch/position/key。preview POST 前保存完整 body/bodyJSON/SHA；真正 PREPARE/CONFIRM/EXECUTE 则保存完整原 intent（user、prepare body、action 原件、path、本次 bodyJSON/SHA）后才 POST，存储失败保留原身份并挡新键。客户端没有本金、角色、时间、银行事实或结果输入。预览为条件范围，不是到账；prepare重新读取事实，之后必须独立 GET 阅读实际冻结完整命令再确认。

CONFIRM 是用户明确勾选后的 `accepted=true`、原 epoch 和 `reviewed_command_hash`；EXECUTE 沿原 action/hash/键。POST的成功、4xx、网络丢失均不能直接清 pending。只有真实 by-key GET 严格匹配原 five-field request、双hash、bank key、UUID5 action、完整MATURE命令、原USER consent和原到期事件，才完成相应阶段。PREPARE 完成后保留固定动作 workspace；CONFIRM还需原 consent；EXECUTE需服务器已核银行与应用原回执。银行SETTLED但应用未核的UNKNOWN、NOT_FOUND_NOT_FINAL和HTTP错误保留同一原动作/键；明确勾选后仅手动恢复原body。新POST前读取最新其它family门和scope，不复用过期闭包。

reader核原命令：正整数本金、原目标/目的账户/产品/版本、MATURE、原15分钟有效窗口；计算 request/command/原action envelope hash及原key→action UUID5；核原USER角色及确认时间窗口。OriginalMaturityEvent 保留并核完整原 hash、action/bank-request/bank-operation/receipt/position 的身份对应、唯一分母与原本金/零fee/零loss；银行与应用回执不足不能仅凭SUCCEEDED/SETTLED字符串升级。SQL原时间可含固定微秒，按同一真实瞬间比对，不改原JSON或哈希。独立银行腿/完整审计/保护/当前授权由原服务器验证，前端一致性检查不冒充金融oracle。

`FullMaturityOriginalRecoveryPanel()` 是仅GET的跨页入口，不依赖当前列表、策略详情或已有登录显示，Root可挂在所有fieldset外。后端原GET也要求当前签名USER，会话401保留请求，须现 topbar 手动续同USER；客户端不保存cookie/secret/token，不自动登录。

Root全局门接 `recoverFullMaturityOperation/useFullMaturityOperation/getFullMaturityOperation` 的 pending/busy/recovering/storage_error，以及 `isFullMaturityWorkspaceUnresolved(workspace)`。本族 mutationBlocked 排除自己的门，store防止本族新键；其它family必须包含该族未决工作区。固定工作区和原确认 metadata 不是权限缓存，下一POST仍由服务/独立银行重新验真。Root挂载时传 `showOriginalRecovery=false`，只保留一个外置恢复入口。

## 实际检查与保留失败

- 首次29风险：28 PASS/1 FAIL，`docs/progress/evidence/W4/full-maturity-user-client-first-direct-20261006T043048Z-238bf5fa/manifest.json` 保持 FAILED。唯一失败负例试图直接篡改冻结intent，未调用到拒绝器；生产冻结正确生效。7源精确原字节保留 `.runtime/full-maturity-user-client-first-red-20261006T043158Z/manifest.json`。
- 独立clone负例修复后仅失败节点 PASS1、未选择的19明确skipped：`.../full-maturity-user-client-corrected-negative-20261006T043309Z-605aa7a1/manifest.json`。不能把它称为新一次29全量PASS。其余原19 API与store6/component3通过记录仍在首个 FAILED 批次，生产代码未因该测试修复变化。
- 新增readonly preview exactbody/SHA持久化后，只复测受影响component3 PASS（3.91s）：`.../full-maturity-original-preview-persistence-direct-20261006T043545Z-d6de55b6/manifest.json`。对应2文件ESLint `.../full-maturity-original-preview-persistence-static-20261006T043540Z-e3933ac5/manifest.json`，最终实际wholeWebtypes `.../full-maturity-original-preview-persistence-types-20261006T043540Z-fdd32185/manifest.json`。
- 首次ESLint7 PASS `.../full-maturity-user-client-first-static-20261006T043041Z-94c215e5/manifest.json`；修测试后单lint PASS `.../full-maturity-user-client-corrected-negative-static-20261006T043301Z-d50d575f/manifest.json`。首static/global=false是Root独立backend execution/test变化，scope=true；首type/global=false是Root backend test变化。保留各原manifest，不把并行源变化称全仓冻结。
- 最终component3与wholeWebtypes `scoped_source_stable=true/all_source_stable=false`，唯一原变化是并行 `apps/api/app/domain/full_joint_goal_execution.py`；最终2文件lint两stable均true。精确记录当前前端终态，不重复已经通过的行为或冒称冻结全仓。

## 未覆盖与限制

本子任务实际 PG/browser NOT_RUN；上述模拟HTTP/原件夹具全部 TOOL_ONLY，不证明真人USER、银行执行、迁移或FULL验收。Root独立挂载/真实链待验收。此范围仅原合同已成熟、server验证后的单仓整本金零费零损，不支持部分、多仓原子、提前有损、收益付款或自动滚存。不保证剩余负检查点已消除。失效/取消而无已核终局回执的工作区仍保守未决；当前没有新close/cancel消费接口，不能用银行缺失声明最终无效果来换键。历史SEALED/不匹配当前scope不产生新确认入口。原金融守卫、完整保护与审计由原服务保持，未扩展资金权限。
