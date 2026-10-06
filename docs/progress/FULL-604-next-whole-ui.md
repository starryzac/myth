# FULL-604：服务器选择下一整仓前端消费者

## 本包可运行能力

2026-10-06 新增独立 `FullRecoveryNextPanel` 与 actual generated DTO reader。用户明确点击后，读取当前本地 USER 身份、调用真实 next-whole-v2 只读 preview，展示原完整候选分母、原顺序、原截止、scope现金、待恢复额、条件准时净额、未覆盖负检查点和真实 server-selected 仓位。没有金额、仓位选择、clock、quote、银行结果或 role 输入。

本包严格复用既有 v1 金融工作区：preview 返回的 `selection.next_v1_request` 是服务器原选择，用户明确准备时，实际将发送的完整五字段 v1 body、path、hash 在原 `beginFullRecoveryOperation` 中保存，随后调用真实 `/full-recovery-actions/prepare`，服务端再次核全部事实、ASK 权限和 USER。**本页面没有调用 v2 prepare 的按钮，也不将 v1 请求假记成 v2 请求。** 确认、执行、UNKNOWN 原键核对与手动同 body 恢复交由既有 `FullRecoveryExecutionPanel`/原工作区。

全部模拟资金；新反复 root 只绑定一个原 Action，不自动推进后一仓，不宣称多仓原子完成。FULL-604 仍 PENDING。银行实际 v2 candidate 尚未执行，本页面没有真实浏览器实证。

## 文件与宿主合同

- `apps/web/src/api/full-recovery-next.ts`：actual `components.schemas.FullRecoveryNextRequest/Preview/Lookup` aliases，strict request/draft/preview/lookup parser、`previewRecoveryNext`、`lookupRecoveryNext`、原 JSON 保存及仅真实 root GET 可产生的 marker。原 v1 reader/hash/工作区源码未改。
- `apps/web/src/components/FullRecoveryNextPanel.tsx`：props `{policyId,userId,expectedVersionId,epochId,mutationBlocked?,onPrepared?}`，与旧恢复 Panel scope 相同。`onPrepared` 仅表示准备 POST 返回或原 GET handoff完成，不是资金执行/独立验真/解除其它请求的成功通知。
- 两个同名 API/组件直接测试与 `tests/full-recovery-next-fixture.ts`。夹具明确 `TOOL_ONLY`，复用旧 synthetic 原模型，并按新协议建立只读响应；不假称 PG/银行/真人确认。

Root 可在 `FullRecoveryExecutionHost` 的当前实际 owner/开放epoch门内，原执行 Panel 前并列挂载新 Panel。两者共用唯一原恢复 store，Root 跨族 mutationBlocked 继续传入；自己的 root GET 不被本族或其它族未决门锁住。Root 所有 Main/deps/App/FullPoliciesPanel/Host/generated 文件未由本包编辑。实际 Main/router/RRRO/schema 已由 Root 完成，schema 原件 `0e0c8111`，不是本包真实 HTTP 测试。

## 原件与恢复边界

四字段 root 草稿属于只读预览定义，保存完整 body/JSON/hash，刷新不会自动网络请求。格式损坏或存储无权限时保留原字节，本入口禁止新请求；不凭此草稿声称金融请求已发送。实际金融请求仍以原 v1 store 的五字段 body/path/hash 为唯一 pending。

root 映射键只由原 root 字符串和明确 `full-recovery-next-whole-v2` 协议 hash 决定，不随仓位、版本或轮次改变。preview 的完整计划与 v1 proof 精确匹配 owner/epoch/version、选中仓位/产品/原购买版本/金额/目的账户及 T0/T1 delay，原截止必须相同。1098曲线和候选数据使用既有完整 planner reader；客户端只校返回内部一致性，不重造银行/授权/审计算法。`input_hash` 严格采用实际 producer 的 Python `now.isoformat()` 字段（UTC JSON `Z`转为该字段的 `+00:00`），不改嵌套原 JSON 或旧 hash 协议。

重复 root 优先用户显式 GET 原完整 `bound_request` 与 v1 Action。其绑定方式明确为 `PROJECTION_OF_VERIFIED_V1_ORIGINAL_AND_EXACT_ROOT_KEY`，`original_v2_request_separately_recorded=false`；它不是独立 v2 原请求收据。原 Action 仅展示；要交回原工作区，用户再点击 handoff，保存原 v1 PREPARE intent 后，仅调用真实原 v1 GET并由原 store完整比对关闭 pending。**handoff 不发送 POST。**

root GET 自身保持可用；NOT_FOUND_NOT_FINAL 不证明资金未提交，不关闭原金融 pending。HTTP成功、4xx、解析错误、网络丢失均不自动清原金融门。准备返回后 pending仍保留，后续确认/执行须原独立GET、真实USER逐次同意、完整原effect及当前服务器/银行守卫。

宿主版本/owner/epoch改变后，保留原草稿与历史root GET，原记录不替换成当前版本；handoff及旧preview的新prepare停用。撤销后的原Action仍可只读显示，终态原件不复活。原workspace未终局时，不生成新 root 来替代该动作；后续请求只能显式发起并重新读取真实当前计划。

## 实际定向检查

| 检查 | 原件与实际结果 |
|---|---|
| 新reader16 + 新component8 | [24 PASS / 7.62s](evidence/W5/next-whole-recovery-web-final-direct-20261006T033401Z-54216ac4/manifest.json)，纯 HTTP/组件夹具；全源与相关 scope 稳定 |
| 五个新TS/TSX及真实imports，原Web strict参数 | [PASS](evidence/W5/next-whole-recovery-web-final-types-20261006T033358Z-8f155dac/manifest.json)，不是整体Web tsc；全源与相关 scope 稳定 |
| 五个新TS/TSX ESLint | [PASS](evidence/W5/next-whole-recovery-web-final-lint-20261006T033358Z-e6dd88a8/manifest.json)，全源与相关 scope 稳定 |

覆盖 server-only body、blank/extra/role/clock拒绝、完整分母/顺序/hash/UTC表示、前后金额/delay不一致、UNKNOWN/null、NOT_FOUND非终局、原marker丢失、假v2收据、真正GET marker、编码斜杠key、原v1 body/path保存、prepare响应丢失、无自动POST/换键/推进、原root GET→仅GET handoff、版本变化/撤销原读取、其它族门和存储错误。

第一次默认sandbox启动 Vitest `7fcb78d4` 因 esbuild `spawn EPERM`在收集前退出；原日志保留，实际用例未运行。首次类型 `2a4b810a` 的三个 unknown-array closure诊断已保留，窄改局部常量完成正确收窄。首22行为PASS `022139a4` 与修后type `62e99298` 保留；随后只增加计划/proof金额与T0/T1 delay一致性及两项相关风险，再运行最终24，没有运行原金融或全量套件。改前原字节分别保存于 `.runtime/FULL-604-next-web/source-before-first-type-fix-20261006T033126Z/` 和 `source-before-plan-proof-consistency-20261006T033332Z/`。

## 具体未覆盖

Root 宿主实际接线/整体Web tsc另行登记；本包未运行浏览器/手机/E2E。next-whole实际银行候选只 collection，T1当前原deadline、银行实际到账/UNKNOWN恢复、多仓人工序列均未证明。旧v1真实T0恢复结果不能升级为本入口或全部FULL604通过。MATURE需原到期reconcile协议；部分本金、允许损失/费用和多仓原子协议未实现。服务器在 prepare/confirm/execute 仍可能因当前保护/策略/身份/期限变化拒绝，预览不延截止、不承诺资金成功。原 v1 算法/hash/store、旧失败、原金融行与正式模拟历史均保留。
