# 整仓安全恢复领域引擎

依据 [ADR 0008](../adr/0008-evidence-bound-safety-recovery.md)，本模块只生成模拟恢复计划，不查询数据库、不读取当前时钟、不发起银行请求、不写余额或回执。实际执行及独立银行对账由服务实现。普通资产新购仍受 MVP-204 的 READY 门槛约束。

## 公共接口

```python
plan_recovery(
    snapshot: BoundarySnapshot,
    boundary_versions: Sequence[BoundaryPolicyVersion],
    positions: Sequence[BoundaryPosition],
    boundary_products: Sequence[BoundaryProduct],
    recovery_positions: Sequence[RecoveryPosition],
    current_authorizations: Sequence[RecoveryAuthorization],
    *,
    user_id: UUID,
    source_issues: Sequence[SourceIssue] = (),
) -> RecoveryPlan
```

类型位于 `app/domain/recovery_types.py`，可从 `app.domain.recovery` 导入。复用完整 202 上下文，不接收客户端宣称的安全金额或授权布尔值。

- `RecoveryPosition` 补充真实持仓的原产品 `AssetProductTerms`、来源账户、返还账户、目标归属、购买时间、取得方式、原购买授权、已预留本金及报价。实际本金、持仓状态和原可用时点以 `BoundaryPosition` 为准，两个 DTO 的身份和归属必须一致。
- `RecoveryAuthorization` 在已有版本事实之上保留用户、策略状态、最新版本 ID；当前版本必须 ACTIVE 且与 latest 一致。原版本必须在购买时已确认生效，两者配置摘要、scope、资产类别、无损恢复开关、单次上限、锁期、风险和赎回延迟均核对。
- `RecoveryQuote` 明确绑定用户、持仓、原产品 ID/版本/条款摘要、整仓本金、费用、损失、净额、请求时点、到账时点、有效期及证据引用。整数分必须满足 `principal = net + fee + loss`，不接受浮点数和布尔值。
- `RecoveryAction` 绑定完整报价、源/目的账户、目标、原/当前授权、真实基线摘要、完整计划输入摘要和有效期。`request_hash` 是除自身以外所有动作字段的规范 JSON SHA-256；有损提案也使用该完整载荷。

来源适配层负责核验银行、购买、确认、所有权、在途请求及完整性证据；领域层再核验所有 DTO 及关联不变量。公开接口重新校验嵌套模型，防止 `model_copy` 绕过严格类型。缺源返回 `INSUFFICIENT_EVIDENCE`，预计边界为 null；矛盾的身份、金额或目录事实抛 `ValueError`/Pydantic 校验错误。

所有未结仓均需取得方式元数据。完整证明为 MANUAL 且没有原恢复权限时仅 ADVISE_ONLY；取得方式/原权限/报价缺失时只要求补证，ASK_ONCE 不会凭空产生授权或可执行载荷。来源 ID 集合排序规范化并拒绝重复；原确认配置及其 hash 保持原样。

容量上限为 100 个恢复候选、100 个当前授权、1000 个附加来源问题；完整边界仍受 202 的 10000 持仓及其余限制。证据引用单集合最多 10000 项。超限拒绝，不截断。

## 实际状态和反事实

`actual_boundary` 始终保留请求前可信事实。`projected_boundary` 是全部已选候选累计后的条件性效果；纯引擎输出 `preview_only=true`，从不宣称 RECOVERED。

T0 明确假设整仓已经到账：返还账户现金增加本金，原仓标记 REDEEMED，目标仓同时把本金归属转成现金归属，allocated 与月贡献不变，再调用原 202 边界。这里的 day0 是反事实到账后快照，不会改写真正 day0 基线，也没有添加一微秒的伪时间。

T1 不增加当前现金，只替换同一原仓的未来本金可用时点，按 202 当日 AFTER_PRINCIPAL 到账。先付款的检查点继续保留缺口。整仓替换或结清会移除旧到期事件，禁止原事件和新事件各返本一次。

每个候选与当前累计预计方案比较全部 273 个对应检查点：每一点余量不得下降，且至少一个原为负的点须严格改善。允许部分改善；不要求全窗最小值提高。没有新增改善就拒绝该候选，不能只为提高正余量多赎回。

按 T0、T1、固定资产及持仓 UUID 排序，不按收益排序、不拆仓、不拆单绕单次上限。已预留或 REDEEMING/REDEEMED 仓不生成新请求。返还目标本金使现金和目标保护同时增加，对通用缺口的改善为零，不会跨目标释放资金。

`uncovered_checkpoints` 保存累计预计方案仍为负的点；`first_sustained_safe_point` 是最后一个负点之后的首点，若窗口结尾仍负则为 null。T1 之前的未覆盖点不因未来预计到账而消失。

## 授权、原条款和人工提案

当前请求的报价时间必须等于可信 `snapshot.as_of`，报价未过期。服务可从权威条款重新报价；既有银行请求的重放由服务查询原请求，不重新规划。

当前策略有效期约束发起请求，报价有效期与当前策略结束时点取较早者作为动作有效期。已接受请求的未来到账不要求策略届时仍 ACTIVE；撤销不能抹掉既有银行效果。剩余购买管理额度不会限制减仓，但整仓仍必须同时满足原/当前单次上限。

自动路径只接受原产品完整、明确零费无损的请求赎回条款，并核对真实锁定结束及到账延迟。原持仓始终使用其原产品版本；不会用当前新版本覆盖旧 v1 条款。目录 `created_at` 只要求当前已知，允许历史仓晚于购买才导入；产品生效区间须覆盖历史购买。

正费用或损失始终生成 ASK_ONCE 提案，哪怕已有 `allow_early_withdrawal_with_penalty=true`。不加入自动步骤、不修改累计预计安全方案，不提供本模块的有损执行或假成功确认。该执行闭环保留到 MVP-301。

自然合同到期是原经济事件结算。若旧本金可用时点已到而尚未对账，返回 `RECONCILIATION_REQUIRED`，由独立银行对账服务结清后再计算。不会因当前权限撤销消灭应收本金，也不会以反事实修饰旧未结事实。

## 摘要边界

`plan_hash` 绑定算法版本、202 真实财务摘要、全部候选原产品/报价/取得方式和当前授权。候选/授权输入重排不改变输出，证据引用重排不改变摘要。原产品完整 terms_digest 变化会改变计划绑定，即使变化仅涉及展示收益；这些收益不参与恢复金额、安全判断或 202 财务 hash。

## 独立字面真值及验证

| 输入 | 预期 |
| --- | --- |
| C=70000，保护100000，T0整仓50000 | actual=-30000；条件到账后 C=120000、margin=20000 |
| 同一缺口，T1整仓30000 | 前5检查点 -30000，day1 AFTER_PRINCIPAL及以后0；最小值仍 -30000 |
| T0整仓10000、T1整仓20000、额外T1整仓50000 | 只选前两仓；首次到账仍缺20000，额外同日返本不改善负点而被拒绝 |
| 原30000于day5返本，提前整仓T0返还 | 所有预计检查点现金100000，day5不再加30000 |
| C=70000、goal cash=20000、其他保护80000、goal principal=30000 | 返还后 C=100000、goal cash保护50000，通用缺口仍30000 |
| 整仓30000，fee+loss=100 | ASK_ONCE绑定净到账29900；自动预计方案仍保留原缺口 |

真实红绿日志位于 `docs/progress/evidence/MVP-205-domain-*.txt`，分别覆盖 T0、T1、累计方案、目标/来源、双授权、报价、输入严校、历史目录导入及完整性。独立性质测试由审查代理维护，使用自己的字面273点账本，不用领域私有 helper 生成真值。领域日志仅证明纯计算与守卫；银行经济效果、并发、回执和本地通知必须由服务/HTTP测试另行证明。
