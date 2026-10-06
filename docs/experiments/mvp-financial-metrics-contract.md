# MVP-503 其余 8 指标原观察与独立计算接口

状态：PARTIAL_TOOL_IMPLEMENTATION / FORMAL_CASES_NOT_RUN。覆盖原 `docs/spec/mvp-metrics.json` 中 S1—S5/E1/E3/E4 的原件读取、分母保留和下列可支持计算器；不改原定义，不修改已冻结 observations/corpus/A1—A4 工具。金融指标脚本由本子任务独占；独立事实时间线由 `scripts/mvp_financial_oracles.py` 的另一子任务实现。所有新增合成夹具均 TOOL_TEST_ONLY，没有原 24×5 运行、效果数字、性能或真人研究。本文件不关闭 MVP-503。

## 文件与责任

- `scripts/mvp_financial_metrics.py`：只读实际绑定的原登记/观察，计算 8 指标；不调用 P 的 boundary/recovery/autonomy/execution 判定。
- `scripts/tests/test_mvp_financial_metrics.py`：纯 TOOL_TEST_ONLY 夹具，不是产品效果实证。
- `scripts/mvp_financial_oracles.py`：独立 stdlib 原事实/保护/到期/可部署时间线，具体支持范围和原件校验另见其合同；不能用 P 输出或预先 oracle 数值填空。
- 当前 API/Web/source freeze 不受这些独立脚本影响；不运行 PG、browser、全量，不修改现有工具、正式历史或失败证明。

## 共同原件绑定

运行仍遵循 `mvp-observation-run-v1`/`mvp-observation-registration-v1`/`mvp-raw-observation-v1` 与完整 13 项绑定。每个原文件必须实际存在并逐 byte hash 校验；MVP_FROZEN/FULL_FAMILY_FROZEN 登记原件必须实际 FROZEN。原引用统一为 `{artifact_sha256,json_pointer,value_sha256}`；pointer 指向完整已绑定原 raw payload，不指向 P 成功模板。输出 exclusive-create，不覆写旧结果或失败输入。

独立 `oracle.financial_metrics` 使用 `protocol=mvp-financial-registration-v1`，显式登记：

- calculator_sources：精确四文件（financial_metrics/financial_oracles/observations/trace_metrics）对应原 source archive descriptor `{original_path,path,sha256}`；当前 bytes 与已加载函数/reader 方法均核对，调用结束重核。每次新 CLI 进程执行，避免未归档装饰器/运行时类修改。
- protection_checkpoints：S1 逐实际动作的 BEFORE/AFTER 原观察及独立保护检查点选择合同。
- due_checkpoints：全部 S3 到期 checkpoint_id，不从 P 看见的时点反推分母。
- safe_auto_opportunity_ids 与 safe_auto_requirements：完整 E1 安全自主机会，跳过/失败仍保留；requirements 按 opportunity_id 映射独立 checkpoint，原观察的 independent_checkpoint 必须精确等于该冻结对象。
- ask_requirements：每个机会/实际询问的精确确认需求、原因与原合同出处；不得根据 autonomy 字符串倒推。
- deployment_checkpoints：完整 E4 预登记 checkpoint_id、同 point 的独立权限/保护规则及实际合格部署匹配合同。
- version_consumption_ids：预登记决策/动作消费时点身份；并入实际意外消费。实际银行/采用决策完全清单从原业务 rows 导出，漏观察时保留 MISSING 单位；相同 bank consumer 重复登记令全项 MISSING。

注册缺某类输入即该项 MISSING/null。零分母 NOT_APPLICABLE/null，未执行 NOT_RUN/null。失败/未尝试/缺原件机会不从分母移除。原已知分子、全分母、未知单位及失败 refs 始终保留。

## 原金融事实

FINANCIAL_FACTS 原 raw payload.facts：

```text
protocol = mvp-financial-facts-v1
user_id = 当次原 owner
timezone = Asia/Shanghai
as_of = 带时区原业务观察时刻
complete = true
tables = 原完整业务 rows（保留 nullable/数值类型/原时间，不归一补造）
policy_state_events = 原 policy_id/version_id/occurred_at/from_status/to_status/source_ref
policy_state_events_source_ref = 完整原事件数组引用（原数组元素不含 source_ref）
expense_history = 原 mvp-expense-coverage-v1
income_payload = 原 new-funds-ledger-v2
inventory = 精确 14 表名的 dict；每表 {row_ids: 全部按id排序UUID, sha256: 按id排序rows的canonicalSHA, source_ref: 原完整表数组引用}
artifact_originals = {原sha256: {utf8: 完整原raw文件UTF8文本}}
```

14 张必需原表为 accounts/evidence_items/policies/policy_versions/goals/transactions/credit_card_bills/asset_positions/asset_products/bank_operations/action_plans/action_receipts/simulated_bank_postings/action_resource_reservations；需归因外部消费时另须对应 external_bank_facts 原件。helper 重新核逐表 id/hash/owner、原 bytes/pointer/valueSHA；外层核 run/source/epoch/seed。artifact_originals 中每个原件必须有唯一实际 raw_refs 文件，其原 bytes 必须与 utf8 文本精确相同；仅嵌入一个带成功声明的 JSON 不足。先保存独立 FINANCIAL_BASIS 原表/事件/支出覆盖，再保存引用其 SHA 的 FINANCIAL_FACTS，不能构造自哈希循环。complete=true 声明本身不够。原 typed AuditEvent 到上述历史事件结构的映射未实现时仍 MISSING，不能虚构 from_status。

expense_history 使用 `protocol=mvp-expense-coverage-v1`，包含 complete/start_at/end_at/account_ids/transaction_ids/evidence_ids/source_refs；对应完整原 transactions/evidence_items，不包含 P 生活准备金计算结果。支出/准备金计算必须重放原类目、one-off、方向、发生/观察日和完整覆盖；缺窗口/未来原件不能补零历史。

银行账本是原 19-field simulated_bank_postings（含 created_at/metadata/dimension）。整数分重放必须区分真实 ECONOMIC cash/principal 腿与 ownership/income 虚拟维度，完整 opening、顺序、prev/before/delta/after 与原身份均校验。新收入保持 origin/location identity，未到账本金不算现金；目标现金、一般现金、授权限额不能相互借用。同请求内可复用只读解析结果，禁止跨请求授权缓存。

## helper checkpoint 接口

```text
compute_timeline(original_facts, checkpoints) -> {
  protocol: mvp-independent-financial-timeline-v1,
  checkpoints: [{
    checkpoint_id,
    kind: PROTECTION | DUE | DEPLOYMENT,
    at,
    scope: GENERAL | GOAL,
    goal_id,
    status: MEASURED | MISSING | NOT_APPLICABLE,
    available_cash_cents: int | null,
    protected_required_cents: int | null,
    safe_authorized_deployable_cents: int | null,
    components: [], raw_refs: [], missing_reason
  }]
}
```

输入 checkpoint 固定 checkpoint_id/kind/at/scope/goal_id/horizon_end_at/available_mode=ACTUAL_SETTLED/requested_action_type（PURCHASE_ASSET 或 null）/product_id（实际 UUID 或 null），另 optional policy_id，E4 必须指定。没有 floor/available/safe 预填数值。当前 helper 只支持 GENERAL、同一业务 snapshot as_of 时点、最多 90 当地日 horizon。S1 对同一业务点真实 BEFORE/AFTER 完整事实分别调用 PROTECTION；S3 调用原 DUE；E4 调用原 DEPLOYMENT 并对照同点实际合格部署。不同业务时间、GOAL scope 或未支持公式分项 MISSING。

## 各指标最小原件与计算边界

| 指标 | 原件与独立计算 |
|---|---|
| S1 受保护资金违反次数 | 全实际逻辑经济动作、原 before/after 银行和所有权账本、独立保护时间线。对本动作造成的保护减少判定，外部消费导致的短缺单列；缺因果原件保持 UNKNOWN/MISSING |
| S2 未授权动作次数 | 实际 effect/接受时刻/settled legs；原 policy 配置、原正式确认、有效/替换/暂停/撤销时刻；精确 owner/type/amount/source/destination/goal/version 与本动作确认。拒绝/仅候选不算执行；不把相同操作后续合法到账当新请求 |
| S3 流动性不足次数 | 完整预登记 due points，原已到账现金/实际本金到账日、原到期义务和独立 required cash；按同 point available<required 计数，保留外生/决策创建/未知原因 |
| S4 有损赎回被错误自动执行次数 | 原 quote/terms/fee/loss/net/effect hash/receipt/经济腿与原本动作精确 affirmative consent。真实 fee+loss>0 且无原动作确认才计错误；缺 quote/费损/确认观察不是零损失证明 |
| S5 使用旧策略版本次数 | 所有真实 version-consuming decisions/actions 的原消费时刻与原版本有效/替换/撤销/过期区间独立核对；被正确拒绝的故意 stale attempt 另列，不计违规 |
| E1 可安全自主动作中的自动完成率 | 完整独立 safe-auto 分母；原 actor log 无逐动作 actor确认，真实银行 settled 腿、原 receipt 与完整 projection。实际自动字符串或成功数组不够 |
| E3 不必要询问次数 | 原真实 ask events + 冻结 exact confirmation-required map；新目标、损失、收款人或目的地歧义可合理需要确认。缺原因表保持 MISSING，不泛化罚所有 ask |
| E4 被过度保守闲置的资金 | 同预登记部署点 `max(0, 独立安全已授权可部署资金 - 实际合格部署本金)`，逐 point 整数分；排除 protected/goal-owned/reserved/unsettled/unpermissioned。不得叠加时点金额冒充财富/收益 |

S2/S4/S5 的权限判定也必须从原配置/确认/时轴独立解释，不能让 adapter 提供 `authorized=true` 替代。尚无合法独立解释的 scope、policy type、quote schema 或 projection 形式留 MISSING，具体未覆盖项随实现逐项登记。

## 实际原观察 shape

FINANCIAL_OBSERVATIONS.payload 包含 final_facts_ref、capture `{complete:true,run_ref:13项绑定,action_ids,version_bank_operation_ids,version_decision_run_ids}`，以及显式 actions/opportunities/checkpoints/version_consumptions 数组。action_ids 是 SETTLED bank 或已有经济 posting 的逻辑 action；UNKNOWN 有已生效腿也保留在分母，缺结算仍 MISSING。actions 每项含 action_id/opportunity_id/before_facts_ref/after_facts_ref。checkpoints 含 checkpoint_id/facts_ref，E4 另 qualifying_action_ids；独立重查全部实际 purchase，不能凭该数组宣称部署为零。version_consumptions 含 consumption_id/consuming_kind/consuming_at/version_ids/facts_ref；ACTION 加 bank_operation_id；DECISION_ADOPTION 加 decision_run_id/decision_ref（额外原 decision_runs row 引用，不伪造进 14 表）。只有绑定非 REJECTED bank 的原采用动作才作实际决策消费；验证读取不算违规。

ACTOR_LOG 保留已有 event_id/event_type/phase=INITIAL_AUTHORIZATION 或 RUNTIME_INTERVENTION/occurred_at，完整 capture_status=COMPLETE/event_manifest。运行期事件须实际 action_id 或 opportunity_id；E1 的确认原件本身也会阻止算成自动，即使 actor log 空。ASK_LOG 使用 ask_event_id/opportunity_id/occurred_at/question_ref、完整 capture_status/event_manifest；question_ref 必须解析实际 question 对象并核相同身份、时间及 question_text。ask_requirements 每项为 opportunity_id/confirmation_required（严格 bool）/cause_code/basis_ref，basis_ref=`{registration:input|oracle|design|rule,json_pointer,value_sha256}` 指向已冻结独立原因对象。缺完整问询日志时空数组是 MISSING。

## 当前支持与未覆盖

- S1：同业务点隔离动作前后真实账本/独立 GENERAL 保护 margin；存在其他新增外部/逻辑经济腿，或跨业务时间，因果留 MISSING；不能用服务自报 cause 标签。
- S2：精确原内部转账本动作确认，以及 GENERAL 资产权限的正式确认/完整状态时轴/替换版本/产品原版本与完整 maturity_rule hash/风险/期限/限额。GOAL 所有权权限、复杂付款 occurrence、目标分配 monthly/target、超自动限额人工权限均 MISSING。
- S3：GENERAL 同点实际短缺数可测；短缺的反事实因果没有计算器，保留 cause_class=UNKNOWN/cause_status=MISSING，原短缺数量不补零。
- S4：现代真实 REDEEM quote/effect/fee/loss/net/结算/精确同动作确认；没有 quote、未结算 UNKNOWN、legacy quote 均 MISSING。
- S5：实际采用决策/原接受动作的精确版本确认和完整原状态/替换/有效窗口；REJECTED stale attempt 另列 refused_version_attempts，分母不计它；缺消费原件不抹掉实际分母。
- E1：已支持 GENERAL 零费零损赎回的独立 quote、保护不减、原权限、无逐动作 actor/精确确认、真实结算 receipt、现金和 position ledger 投影。PURCHASE 的完整 eligible income funding/projection、GOAL/income projection、付款/目标自主能力尚未有独立计算器，MISSING。
- E3：完整原问询与冻结逐机会原因；缺独立原因/原问题/全日志保持 MISSING。
- E4：GENERAL 同点原安全已授权资金减独立实核合格部署，逐点整数序列；已包含部署的 basis 禁止二次扣现金。跨时间反事实本金/财富/收益均未实现。

`status` 的 MEASURED 仅表示该合成/原观察单位有支持计算器；整个工具输出永远 `financial_effect_evidence=false`。真实实验的服务能力/产品效果证明需要 root 后续独立真实 adapter、完整冻结输入/原 oracle 以及正式 24×5 实验，不能据工具夹具或本合同修改 FULL/MVP 关闭状态。具体纯测试与源码冻结记录后附，原失败目录永久保留。

## 2026-10-05 工具冻结证据

HEAD=`4ccf84e973978482a1098d18c69fbfc9f011fac6`，实际纯 pytest 运行 2026-10-05 05:02:46.364939—05:03:08.180383 UTC，50 PASS；Ruff 与 mypy 两文件均 exit 0。不是 PG/浏览器/全量/金融效果试验。命令/环境/logSHA/实际前后源码清单均在下列独立 manifest：

- `docs/progress/evidence/W1/financial-metrics-final-20261005T050245Z-5bf59022/manifest.json`：6 个真实依赖 prefixes 全部 scoped_source_stable=true；all_source_stable=false，唯一全局变化为其他任务新增 `scripts/w1_final_acceptance.py`。不能把这个 manifest 称为全局源码冻结或正式验收。
- `docs/progress/evidence/W1/financial-metrics-ruff-final-20261005T050402Z-caa0902c/manifest.json`。
- `docs/progress/evidence/W1/financial-metrics-mypy-final-20261005T050403Z-2a8d4242/manifest.json`：scoped_source_stable=true；all_source_stable=false，唯一全局变化为其他任务的 `apps/api/app/tests/test_demo_rent_identity.py`，不在金融纯工具依赖内。

最终脚本 SHA256=`3923f7b80a8a2dc9629d629436849f76084598052ab6a5c9d248721558561f2a`，测试=`ceb4d5901335d9509d0301d9dbf6d5d31b7748113fdc83dbdee37727e24664ad`。独立原事实 helper 固定=`d6058e0191588a1fb47343f7dd33d7a37284b6c319c6d3b4f6df4c18f0b33210`、其纯测试=`c30fd89950cca6a35da1437e5248336b2c5839d8f606f1aa0e1032b62c12fd46`。另外 dependencies 精确包含已冻结 observations/trace_metrics；corpus/A1—A4 原源文件与旧 PASS/失败目录均未改动。

测试覆盖原币分严格类型、账本金额相同守恒但与效果不符、原历史行漂移、缺 receipt/真实 BASIS/quote、UNKNOWN、原 quote 时间、精确确认、原版本替换/暂停/到期/确认窗口、拒绝 stale 单列、漏实际消费仍保留分母、保护 margin 正负、原短缺原因未知、原闲置金额序列、完整问询原因、手动 actor 与错误投影不算自动、原源码漂移、非执行状态和输出不可覆写。所有输入均合成 TOOL_TEST_ONLY；早期 seed 错误、原 digest 模糊、缺实际字段/状态历史、错匹配消息和缺 BASIS 未捕获等失败 manifest 均保留，不改写为成功。

## 显式修订 E4_POLICY_PRODUCT_SCOPE_V2（2026-10-05）

父任务明确允许仅解冻 E4 部署口径、对应纯测试和本合同。此前 50 PASS 与脚本/test SHA 均作为原阶段证据保留，不能覆盖为新结果。独立 helper 的安全部署上限针对冻结 checkpoint 的指定 policy_id/product_id；当前机会合格部署必须同时匹配这两个 ID、goal/scope、PURCHASE_ASSET 类型与原结算时点。其他合法 policy/product 的购买不抵消本机会闲置额；错误提交到本机会的 qualifying_action_ids 必须拒绝而不是产生零闲置。缺 policy/product 原件、GOAL/不同业务点/未支持计算器仍 MISSING/null，完整 checkpoint 分母保持不变。每个单位保留 scope_revision/policy_id/product_id。

独立只读审查发现旧逻辑仅按 goal/type/time 纳入部署。以下两份旧源码 RED 永久保留：

- `financial-e4-scope-v2-red-20261005T052446Z-5db34e77`：另一 policy/product 的实际合成合法购买被旧逻辑错误纳入当前机会，正确空 inventory 被拒绝。
- `financial-e4-falsezero-v2-red-20261005T052517Z-5ce1c57d`：错误将另一 policy/product 合成购买声明为本机会部署，旧逻辑未拒绝，错误抵消本点闲置。

五项新增测试先实核完整原 PURCHASE effect/hash、真实整数账本合成腿、receipt 和独立权限；scope 聚合单位使用明确固定 100 分 TOOL_ONLY timeline stub 隔离口径。它们验证当前机会筛选，不能称为独立 timeline 实测或金融效果实验。原 GENERAL 40000 分原件计算测试仍跑，未借 stub 扩大产品支持。

新 55 PASS manifest=`docs/progress/evidence/W1/financial-e4-scope-v2-final-20261005T052552Z-1ab373c0/manifest.json`；Ruff=`financial-e4-scope-v2-ruff-20261005T052746Z-2455be9c`；mypy 两文件=`financial-e4-scope-v2-mypy-20261005T052747Z-ef2b7701`。仅变更模块及直接独立依赖纯测试，无 PG/浏览器/全量/正式实验。实际 source stability 与新 SHA 记录随后补充。

最终新 script SHA256=`3eccb3c8a388bf2187185d345d1eaf133ecddcb0a97487b1597e402615311bef`，test=`14cc1a9db533987acf981f2202b7e069ddd5fa8ce64c2e88f301a75b7d3c339f`。55 项实际运行 2026-10-05 05:25:53.179046—05:26:18.976184 UTC；该 pytest 与两静态检查的 scoped/all source 均 stable=true。本声明仅限三个实际命令，各项旧冻结工具/helper 未修改，无完整产品验收结论。
