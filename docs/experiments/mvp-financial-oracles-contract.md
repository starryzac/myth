# MVP 独立金融原件计算器合同

状态：`PURE_TOOL_IMPLEMENTATION`。本工具仅提供 S1/S3/E4 与 E1 接缝的独立整数分计算，不能关闭 MVP-503；纯夹具不是实际场景、金融效果、性能或 24×5 运行证据。运行与指标分母由 [八指标合同](mvp-financial-metrics-contract.md) 登记及保留。

原需求对应 `docs/spec/mvp-metrics.json` 中 S1 的原 M:854、S3 的 M:856、E4 的 M:865；MVP-503 整体为原 M:821–831、840–886。公式依据原需求及已登记金融语义说明，包括 [现金保护边界](../architecture/boundary-engine.md:29)、[生活准备金](../architecture/living-reserve-estimator.md)、[收入来源](../architecture/income-ledger.md)、[资产授权](../architecture/asset-allocation-service.md)。计算器没有导入 `app` 或 P 的边界、恢复、自主等级、执行计算；不调用 P 的评估结果，不接受预填保护额或安全额度。

## 公开纯接口

```python
validate_facts(original_facts) -> {
    "status": "VERIFIED" | "MISSING",
    "missing_reason": str | None,
    "raw_refs": list,
}

compute_timeline(original_facts, checkpoints) -> {
    "protocol": "mvp-independent-financial-timeline-v1",
    "facts_status": "VERIFIED" | "MISSING",
    "missing_reason": str | None,
    "checkpoints": [...],
    "execution_authority": False,
    "financial_effect_evidence": False,
}
```

`validate_facts` 仅检查原件完整性、归属、严格整数/时间和内容哈希。它没有批准金融操作，也不要求某个保护分支已支持，因此 S2/S4/S5 可以独立验证各自原件。空 checkpoints 仍明确输出 `facts_status`；空结果不能代表验证成功。

每个 checkpoint 输出原 `checkpoint_id/kind/at/scope/goal_id`，以及 `status`、`available_cash_cents`、`protected_required_cents`、`safe_authorized_deployable_cents`、`components`、`raw_refs`、`missing_reason`。支持分支为 `MEASURED`；缺原件或未支持分支为 `MISSING`，金额全部为 null。PROTECTION/DUE 的部署金额为 null。真实可算的短缺或无授权额度可以是 `MEASURED` 的零额，不把缺证当零额。重复 ID 和非对象检查点仍占一个输出单位并标记 MISSING。

## 原件输入

顶层必须有 `protocol=mvp-financial-facts-v1`、canonical UUID `user_id`、`timezone`、带时区 `as_of`、严格 `complete=true`、`tables`、`inventory`、`artifact_originals`、`policy_state_events` 和 `policy_state_events_source_ref`。支持本 MVP 的 UTC 或固定 UTC+8 `Asia/Shanghai`；不猜其他时区或 DST。各业务对象保留实际原 JSON 类型、nullable、ID、金额和时间，不补 P 判定或声明的 oracle 数值。

`tables` 与 `inventory` 必须恰好包含以下 14 键，每张表即使为空也保留原完整数组：

```text
accounts evidence_items policies policy_versions goals transactions
credit_card_bills asset_positions asset_products bank_operations
action_plans action_receipts simulated_bank_postings action_resource_reservations
```

逐表 inventory 为 `{row_ids,sha256,source_ref}`。row_ids 为全部 canonical UUID 排序数组，不得重复；sha256 为按 `id` 排序完整原行数组的独立 UTF-8 canonical JSON SHA256。source_ref 为 `{artifact_sha256,json_pointer,value_sha256}`，必须指向已绑定原 BASIS 文件中的该完整表数组。读取可保留原行顺序；比较时按 ID 排序而不改原数据。不能让 source_ref 指向包含自身文件哈希的 FINANCIAL_FACTS 形成循环。

采集先写独立 `FINANCIAL_BASIS` 原 raw 文件，再写 FINANCIAL_FACTS 保存其原表、inventory、引用和 `artifact_originals={sha256:{utf8:完整原文件UTF8文本}}`。helper 重算原字节 SHA，拒绝重复 JSON 键和非有限数；重新解析 pointer、核 valueSHA 和完整表相等。不得让 adapter 在已有输出上补 `complete`，或替未实际导出的表补空数组。

完整性边界：这不是数字签名或外部银行认证。相同的伪造行、伪造清单和重新计算的 SHA 可以内部一致。外层必须验证冻结的 run/case/arm/owner/source/seed/epoch 等 13 项绑定、文件真实存在及捕获出处。helper 不能独立证明 collector 在第一次导出前没有漏行；`complete=true`、清单和来源绑定须由真实生产采集路径建立。外生消费归因另需 `external_bank_facts` 原件，不把 posting 上外部身份单独当成消费原因。

金额拒绝 bool/float，所有使用的金额为 signed64 整数分；非负字段另核非负，差额保留符号，溢出拒绝。所有日期/时间来自原件；有效期终点严格排除。原 evidence content_hash、版本 configuration content_hash 独立重算；选用 BANK/USER 原件必须在该时刻已观察、当前有效且唯一，冲突或过期不回退选一条。

## 政策原状态与确认

`policy_state_events` 当前支持原最小事件 `{policy_id,version_id,occurred_at,from_status,to_status,source_ref}`；source_ref 指向实际原事件对象，五个业务字段完整相等。版本归属须正确；状态连续、最后状态与原 policy 一致；各已确认版本必须有确认时刻原 ACTIVE/CONFIRMED/MODIFIED 转换。原不可变 configuration、confirmation、USER_CONFIRMED_POLICY 证明须完整绑定 owner/policy/version/reviewed hash/confirmation/effective 时刻。

`policy_state_events_source_ref` 指向独立 FINANCIAL_BASIS 中完整原事件数组（不含 per-row source_ref），helper 与全部事件去 source_ref 后精确比较。空历史也需原空数组证明；逐条来源不能代替完整历史。该检查阻止从已捕获原数组中择掉中间事件；首次捕获前完整性仍由真实 collector/source/epoch 绑定负责。

当前未实现 typed `AuditEvent.payload.changes` 到上述状态合同的映射。现有 typed 原事件不能由 collector 猜出 from/to 后填成简化成功事件；需要增加保留原 audit ID、payload、source_ref 的可复核独立映射，未做前返回 MISSING。改版时截断旧版本窗口；自然到期停止新周期，已形成欠款继续保留；暂停/撤销旧周期无法唯一恢复时不补造 debt 记录。

## 现金、保护与时间线

实际现金只取完整 ECONOMIC `CASH:<account_uuid>` 银行账本末值，并只计 CNY 的 CASH/GOAL 账户。核 opening、从 1 连续序号、前驱 ID、before+delta=after、资源身份、维度/metadata 不变和时间顺序。非 opening actual cash 腿必须有原 external fact 或实际 SETTLED bank operation 身份；允许 operation 同时保留 legacy redemption identity，但不凭 legacy request 重建金融效果。虚拟 GOAL_OWNERSHIP/INCOME_LOCATION/LIABILITY 腿不加现金；accounts 展示余额、持仓本金、T0/T1 名字、未来收入或未提交退出计划也不加现金。只输出银行实际变化身份及来源渠道，最终系统/外生因果判定仍须外层相应动作与原 external fact。

支持 GENERAL，观察点必须恰好等于该份原 facts.as_of；前后需各自采集同一逻辑操作的真实快照。预测窗最多 90 个本地日。过去或未来观察点不以当前状态回填；GOAL scope 尚未实现。

保护额分项由以下原事实计算：

- 真信用卡账单 `total-paid` 唯一计入；账单支付政策不重复生债。原出账时间、账户、总额/已付和状态须与 BANK 证明相符。
- 周期 exact 固定额、range 上限；due_day 夹到短月末，确认/版本有效窗内才生成，且 due 不得超出 horizon_end。已到期窗口的已形成历史未付仍保护。历史及当日周期必须有完整同观察时刻银行累计 paid 证明；缺证明不推定零。未来无预付原件的已确认义务保留全额。
- 生活准备金从覆盖 lookback 的完整既往自然日消费求全部连续 horizon 窗口和，按数学 nearest-rank quantile 取值，再加原 extra buffer。概率用十进制字面值 Fraction，避免二进制浮点 ceil 偏移。只有银行 CONSUMPTION/DEBIT、原 USER_DECLARED 类别确认且属于配置类目才纳入；按原 one-off flag 排除。银行转移、购买、还款和本金返回不算生活消费。零历史日仅由完整历史覆盖原件支持。
- 应急和生活底线按剩余本地日任一有效时段保守覆盖；授权仍按精确时刻判断，不在未确认/未生效时借用权限。同一政策多个版本覆盖同一日而无法独立恢复日内规则时为 MISSING。
- 原 goal ownership 拆为现金与本金、账户与 position 原 IDs；目标现金在暂停/到期后仍保护，GOAL 账户未映射现金也保护。owned cash 与实际银行现金核对，不与虚拟 memo 重复相加。最低承诺按预测窗、有效期与 deadline 相交自然月份：本月已证明贡献抵月最低，未来月不预扣；月最低总额与累计最低归属缺口取 max，再以剩余 target 封顶。原本金只减少目标未完成额，不加实际现金。

严格保护下，预测窗已知未付义务在开始时占用对应现金，日后付款使现金与未付保护同额下降。因此无需伪造日后交易即可用各日 `cash - all_pending_obligations - owned_goal_cash - goal_minimum - active_floors` 求有符号余量。组件保留完整本地日余量、原分项与纳入消费 IDs/windows/rank。没有未来收入或未到账本金增量。

`expense_history` 必须为原 `mvp-expense-coverage-v1`，有 complete/start_at/end_at/account_ids/transaction_ids/evidence_ids/source_refs；覆盖全部账户和历史窗，transaction/evidence inventories 与实际原行精确相等，至少一条 source_ref 指向完整覆盖登记原对象。仍由真实 collector 为“无漏历史”的声明负责。

## E4 安全已授权可部署额

DEPLOYMENT 额外必须指定原 `policy_id`、`product_id`、`requested_action_type=PURCHASE_ASSET`，当前只支持 GENERAL 原 asset_authorization。共同条件包括精确有效最新原授权、允许产品类别、零本金风险授权、lock/delay 原上限、管理上限、单笔上限、产品最低购买额和完整原收入所有权。

原 `income_payload` 为 BANK `new-funds-ledger-v2` 完整原内容，与 BANK evidence 精确相等。逐 origin 保留原银行 INCOME/CREDIT transaction/evidence hash/发生与观察时刻；fragment UUID5 保持 origin/account 身份，spent+assigned+reserved+available 按 origin 守恒。四份金额须与原 LOT_* INCOME_LOCATION 账本头及 origin metadata 相等，reserved 与原行动 reservation uses/legacy reserved 对账。转移不重置 origin occurred_at；只使用发生时刻不早于本授权实际确认/生效时刻、当前位于一般现金账户且未分配/未保留的 available。不能把本金返回当新收入或把目标 owned cash 放进一般收入额度。

管理本金由原 BANK position 的 manual/auto acquisition 分开；自动来源必须绑定原 SUCCEEDED/RECONCILED action、SUCCEEDED receipt、交易和原版本。仍 HELD/REDEEMING/MATURED 的一般自动本金继续占 cap，不因新版本换分母；手工持仓不占自动 cap。未提交 PLANNED/AUTHORIZED purchase 的原 CASH reserve 同时占用现金和管理本金一次，需与原 action.amount 精确相等。UNKNOWN/SUBMITTED purchase 或 ACCEPTED/UNKNOWN bank operation 未完成原对账时不提供精确 E4。

产品无保证本金返回条款时按整个预测窗占用。明确 `fixed-principal-return-v1` 的原完整 CALENDAR、guaranteed、10000 bps、无 rollover、term/delay 条款才允许候选固定本金归还；实际 v2 kind/auto_rollover/yield_rule 原字段有明确支持，仅支持费用为零的 simple annual yield，预测收益不加可部署现金。同日返还不能提前救该日付款；任何基线短缺都令新购买额为零。存在费用或未提交的按需退出时刻则 MISSING，不推测更高额度。

支持额是 `min(财务占用上限-原待提交现金reserve, 当前合格新收入, 管理上限剩余, 单笔上限)`，已知产品不获授权或低于最低额为实算零。外层在同一个预登记检查点核真实合格部署本金，并计算 E4 差额；没有尝试的机会照常保留，不能用 P 的已完成动作生成分母或把各时点金额叠成财富。

## 具体未覆盖项

以下不猜测，逐检查点 MISSING；完整原件验证仍可单独供其他指标使用：

- GOAL scope 部署、跨目标调配、非零本金风险配置及其他时区/超过 90 日窗口。
- typed AuditEvent 状态独立映射；暂停/撤销/同周期改版的历史债务映射；相互覆盖的同日底线版本。
- 已替换且当前已失效的旧确认原证据，尚缺历史有效性映射时不会回退使用；需保留原确认有效窗与替换出处才能扩展历史版本。
- 开始时尚未生效的未完成目标、预测窗内目标承诺到期、deadline 已过但未完成最低承诺的继续保护映射。
- 既有 ACCEPTED/UNKNOWN 本金赎回的未来排程；未提交 `purchase_exit_plan`，以及 `planned-principal-return-v1` 按需产品缺独立 bound candidate exit-time selector。
- 费用不为零或收益协议不支持的候选现金流；缺原自动购入身份、源 LOT 账本或累计 paid/ownership/month contribution 证明。
- 导出前漏行、未绑定外生消费或被修改且重新签入的“原件”；当前工具不是外部真实性签名验证器。

这些限制不允许 adapter 插入解释后的 oracle 数字绕开。扩展需独立公式、原件字段、正负纯例和真实注册/采集后才把该分支转为 MEASURED。

## 验证证据

`scripts/tests/test_mvp_financial_oracles.py` 的 Fixture 仅内存构建可追踪纯合成原件，覆盖手算保护/到期/额度、短月、自然到期旧债、真实账单不重复、十进制 quantile、支出排除、目标归属、收入原时刻与转移、现金虚拟腿隔离、权限与 cap、损坏账本、错 hash/owner/time/type、未知状态/产品与原件缺失。未运行 PG/browser/全量。原第一轮失败与修后检查由 W1 独立 scoped evidence 保留；最终命令、源 SHA 与检查状态从各 manifest 读取，不以本段文件存在代表产品验收。

原 `output/playwright/w1-20261005T033611Z-5216c9ce/checkpoints/0007-independent-case-start-before.json.gz` 只用于只读原字段核对（Action/Receipt SUCCEEDED、maturity_rule 内 kind/auto_rollover/yield_rule、manual/auto acquisition identities），没有改写、转换成成功 FINANCIAL_FACTS，或对该真实快照声称已有全金融计算结果。

2026-10-05 最终 scoped 纯工具验证，绑定 HEAD `4ccf84e973978482a1098d18c69fbfc9f011fac6` 与新增两份 Python 文件，三个 manifest 均 exit=0、`scoped_source_stable=true`、`all_source_stable=true`：

| 检查 | 实际结果 | 原证据目录 |
|---|---|---|
| 独立 pure | 84 PASS，pytest 0.78s，scoped wall 1.777176s | `docs/progress/evidence/W1/independent-financial-oracle-pure-final-20261005T044448Z-ac8eb469/` |
| mypy strict | 两文件 PASS，使用 `--explicit-package-bases` | `docs/progress/evidence/W1/independent-financial-oracle-types-final-20261005T044448Z-1940ae1f/` |
| Ruff | 两文件 PASS | `docs/progress/evidence/W1/independent-financial-oracle-static-final-20261005T044449Z-9a73bacd/` |

源码 SHA256：`scripts/mvp_financial_oracles.py=d6058e0191588a1fb47343f7dd33d7a37284b6c319c6d3b4f6df4c18f0b33210`；`scripts/tests/test_mvp_financial_oracles.py=c30fd89950cca6a35da1437e5248336b2c5839d8f606f1aa0e1032b62c12fd46`。首轮 4 FAIL/58 PASS 仍在 `independent-financial-oracle-pure-first-20261005T042949Z-653f24f8`，明确是生成了 horizon_end 之后下一期义务的工具边界错误；不改失败输入或原记录。修复后中间 80 PASS 与最终 84 PASS 分开保存。以上全部为 PURE_TOOL_TEST，不是 MVP-503 实际效果或验收。
