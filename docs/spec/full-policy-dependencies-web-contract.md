# 当前完整策略依赖界面

新增 `api/full-policy-dependencies.ts` 与默认导出 `FullPolicyDependencyPanel`。使用实际生成的 `FullPolicyDependencyReview`，唯一请求为无 query 的 `GET /api/v1/full-policy-dependencies/{policy_id}`。不保存权限、不执行 POST、不自动刷新。

组件 props：`{ policyId: string; currentVersionId?: string; onReviewCurrentVersion?: (binding: { policyId: string; versionId: string; reviewHash: string }) => void }`。宿主应提供详情当前版本 ID。身份或版本改变后工作区重新等待手动 GET，不采用先前 query cache；网络或原件校验失败隐藏旧结论。

原读取入口重新核配置摘要、原响应摘要、完整当前根数量/ID、所属用户和周期、确认时及当前引用、准确 role/kind、引用边和当前派生状态。声明强连通分量按当前实际 FULL_POLICY 引用核对；同 UUID 的 MVP 引用不能制造 FULL 闭环。UNKNOWN 保留分母及原因；无来源不展示“无环”成功。MVP 派生 EXPIRED 与原 snapshot ACTIVE 两者均保留，不重写原引用摘要。

`input_hash` 作为服务返回的原输入摘要保留；由于响应不包含完整审计验证输入，本客户端不声称独立重算它或独立验真金融/银行。`review_hash` 对完整响应（除自身）重新计算，原 HTTP 文本按返回对象弱关联保留。查到原引用、声明完整或摘要一致均不产生银行授权。

可选“查看原当前版本变更工作区”按钮只在本次图完整且所选当前版本存在时传身份与本次 reviewHash。该回调没有配置、accepted、原因或 key，不能提交变更；宿主仍需沿原真实版本预览与明确新确认链。闭环不是财务不可行或最小冲突，所有模板动作重查、持仓/边界恢复和金融冲突求解仍明确未由此界面验证。

测试夹具来自纯 domain 对明确合成声明的序列化（TOOL_ONLY），HTTP 为本地替身，不能作为实际 PG/Edge/银行或真实用户研究证据。组件未提供写入、自动修复、自动确认或执行按钮。
