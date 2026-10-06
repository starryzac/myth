# FULL-105：多模板当前版本金融修改预览 v3

2026-10-06 本包交付独立的生产只读预览域、服务和路由。**原 FULL-105 仍 PENDING / PARTIAL**；没有把模板 Schema 存在、纯测试或复用旧文件当作完成。原要求来源为 `docs/spec/requirements-traceability.md` FULL-105（原完整版1192–1194行）：修改前预览自主资金、目标、持仓及未来动作；无副作用，实际确认后差分与预览一致，UI/接口测试。当前包没有证明确认后的差分一致，也没有代替旧金融预览。

## 可调用合同与根接线

新增 `POST /api/v1/policy-financial-previews/{source_kind}/{policy_id}`，`source_kind` 仅 `MVP_POLICY` 或 `FULL_POLICY`。body 仅完整 `configuration`、`expected_version_id`、`expected_epoch_id`；UUID使用原UUIDReference，因此实际JSON字符串合法。拒绝额外query、owner、clock、amount/effect/result/receipt/grant字段。预览不会确认配置、创建版本、Action、报价、银行操作、Evidence或DecisionRun。

根需注册 `api/v1/full_policy_change_multi.router`，并在该POST路径进入依赖的 **REPEATABLE READ + READ ONLY** 事务后再读用户。当前源未修改 Main/deps/generated contracts。服务第一步独立拒绝dirty/new/deleted、非RR或非RO Session，再核真实模拟owner、OPENepoch、server now、当前版本。新资产/恢复/GoalAllocation规划规则只有当前ACTIVE才比较今日候选，future CONFIRMED不能提供今天的cap。GET-free的POST只是显式只读计算，不能拿响应或曲线hash做确认。

`MultiTemplatePreviewResponse` 保留原/候选配置、两配置hash、changed_fields、完整当前来源/数量、原Action/Position ID和当前事实摘要。新摘要协议为 `full-change-current-facts-v3`，金融输入协议为 `full-policy-multi-template-input-v3`；它们与旧 v1 和 history-v2 不同，不跨时点稳定、不相互替代。原原件包括完整MVP版本/确认链或FULL版本/command链、年度原响应、实际境界facts、全部当前Action、exposure、Evidence、catalog及相应候选输入。没有跨请求授权缓存；原clean RRRO `audit_read_scope` 只在本次函数范围复用审计证明。

金融状态有三个严格含义：

- `PROJECTED`：仅 `MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS` 的确定性保护差量；并不代表未来动作已生成。
- `PARTIAL`：实际独立产品容量、整仓候选或当前期GoalAllocation候选已计算；未覆盖的整组/未来执行仍明确列出。
- `UNKNOWN`：缺完整来源/原曲线/候选能力时after与金额差量为null，原已知before可保留；不填0成功。

## 实质计算与来源

原生活、应急、经常义务和MVP目标有独立持久来源，不能构造不存在的FULL row。MVP读取核当前原确认，还逐项核完整版本序号、previous_hash、配置hash、owner/原confirmation及每版本原Evidence；历史缺失或冲突返回UNKNOWN。原FULL读取使用当前 `read_full_policy/_binding` 的完整真实版本/命令核验，候选引用使用原 `_references`；候选不会被伪造成确认版本。

当前资金由原 `load_verified_financial_context` 重验银行、收入、占用和原Audit，原 `compute_full_annual_protection` 保留登记保护及原90天视图。新域重新计算原MVP年度与整个 `project_full_protection`，必须与捕获响应一致：今天+365未来日×3阶段，共1098点，不缩减产品、Goal、仓位或Action分母。任何被修改的点、safeIdle、产品键或原floor不能通过旧字符串flag成为有效曲线。

MVP候选用原 `compute_policy_change_boundary`：生活从真实历史coverage/类别估计器重新估计；应急改变明确额度；经常义务沿原保守发生/结清规则；Goal保留实际归属与当月已贡献。再逐点保留实际FULL额外保护、原条件付款现金负担、现金占用和原阶段顺序。原Dated/Periodic/Seasonal原件、确认、实际采纳和所有历史hash不变。不能因候选减少MVP保护而删除其他FULL付款或占用。额外保护与未来付款不是新银行现金。

## 原12模板能力矩阵

| 原模板 | v3可运行能力 | 明确未覆盖/UNKNOWN边界 |
|---|---|---|
| RecurringObligationPolicy | MVP源当前或再次修改，原365引擎重算完整保护差量 | 旧已产生义务/结清有歧义保原 `HISTORICAL_OBLIGATION_RECONCILIATION_REQUIRED`，不把range上界当实际账单，不补结清 |
| LivingReservePolicy | 原当前版本完整来源；按候选真实历史范围/类别/quantile/extra buffer重估并重算1098点 | 无coverage或历史不足UNKNOWN；不是LLM/客户端的生活金额 |
| EmergencyBufferPolicy | 当前或再次修改的明确buffer真实前后floor、safeIdle、minMargin、逐原产品金融容量差量 | 非当前来源或完整保护不明UNKNOWN；不授可执行金额 |
| DatedExpensePolicy | 保留旧v1及已证未来Dated历史v2原入口和算法 | 此新v3返回应使用旧准确入口的UNKNOWN；没有改写旧今天/过期/结清语义 |
| LongTermGoalPolicy | 原MVP `goal_saving` bridge的当前保护/最低贡献候选重算，保留实际现金/本金/当月贡献 | 不是新的full-model双hash候选；FULL最低保障/容忍度/资产引用版本和新多期未来分配还需对应原Goal模型确认/consumer，不将其余金额填0 |
| PeriodicTransferPolicy | 保留旧v1未来付款的准确预览入口；v3保原已证明付款/source约束 | 此v3不重建未证明的重复Periodic历史或新外部关系授权；已银行settled但应用未对账保UNKNOWN |
| AssetAuthorizationPolicy | FULL源同实际scope、完整immutable catalog与exposure，按原asset候选函数比较每产品风险/lock/delay/成熟条款/零损赎回及原365FULL容量、实际现金、single/total caps | 每产品容量不是可相加组合，不新运行完整whole optimizer，不卖出旧仓；换scope/Goal要求新的完整来源；MVP资产本包未接新适配；收益和组合购买总额不是已证明金融效果 |
| RecoveryPolicy | 原完整持仓/产品版本/quote、原独立loss校验、当前linked asset；按原候选函数逐仓比较原deadline下fee/loss/arrival/单次cap和lossless资格 | 不伪造价格，不计回收本金为当前cash；候选内旧有损boundary显式 `ORIGINAL_MVP_ONLY_NOT_FULL_RECOVERY_CURVE`，不称FULL恢复后曲线；实际触发、多仓时序/原子完成和未来银行效果未算；没有deadline的准时金额差量null |
| GoalAllocationPolicy | 同完整FULL-bound当前期原joint input，以本声明Goal集合及single allocation预算上界运行原8维solver；全部原Goal和income lot保留，非选择Goal仅为此假设不分配 | 不改变原Goal确认/所有最低保护，不切income lot绕cap，不把新收入当future income；未来/不在原完整Goal分母的候选UNKNOWN；新规划不是实际产权，也不是多期全局最优 |
| CrossGoalReallocationPolicy | 候选完整Schema和实际引用解析，原before可读 | 具体返还金额必须另经专用scope consent与实际effect，本包UNKNOWN/null，不以enabled/cap直接回拨 |
| SeasonalReservePolicy | 原实际adopted保护在before完整保留；候选完整Schema和当前来源可检查 | 新候选hash没有本配置显式原采纳proof，UNKNOWN/null；不根据quantile/上限生成或释放实际adopted金额 |
| InterventionPolicy | 实际原完整配置和问答规则边界可复核，原before保留 | 没有可由mustAsk/throttle推定的金额公式，UNKNOWN/null；ACK或问答也不是银行授权 |

原12模板中没有 `FutureIncomePolicy` 或 `QuestionPolicy`。实际FULL203登记的是用户明确接受的月度条件假设；FULL505为世界筛选的Question workflow。它们不是第13/14个策略模板。本预览未来收入计入当前cash及execution严格为0，不推测工资/银行承诺；问答策略变化不能直接赋金额、PolicyVersion或bank grant。

当前Goal归属现金和持仓本金的两个delta0仅表示本只读调用未修改原事实；资产/恢复/目标规划的候选差量另有字段。所有 `future_action_delta` 是 `UNKNOWN_REQUIRES_FRESH_EXECUTION_RECOMPUTATION`。本包未自动作废或重算原Action、未自动提交确认或执行。实际确认后必须按新当前版本/新引用重新验证，不能把本次假设性保留原FULL负担当作证明确认后所有依赖仍有效。

## 直接检查与原失败保全

首轮31 PASS/1 FAIL：新测试夹具总额度10000却沿helper默认单次cap1000000，合法被原Schema拒绝，未到目标negative；原日志 `multi-template-v3-first-direct-20261006T043945Z-4565ea8e` 保留。首轮47类型诊断和2unused静态诊断亦保留；主要为不同分支重用局部变量类型、catalog真实字段不符及fixture Optional收窄。原源码在 `.runtime/FULL-105-multi-v3/initial-source-20261006T043853Z/` 和 `first-red-source-20261006T044028Z/`，没有覆盖原失败。

修正后41直接风险 PASS/12.00s，随后补当前source时间门与一个实际future-confirmation容量负例。最终 **42 PASS / 12.59s，0skip**；六新Python strict（`--follow-imports=silent`限定范围）、Ruff及format均exit0。所有检查的owned scoped source稳定。独立其它任务并发的global source变化如各manifest原实测登记，不能说全仓金融源冻结。

- [最终42直接风险](evidence/W2/multi-template-v3-final-active-source-direct-20261006T044553Z-50ea9a71/manifest.json)
- [六新文件strict](evidence/W2/multi-template-v3-final-active-source-types-20261006T044554Z-d21557d3/manifest.json)
- [静态](evidence/W2/multi-template-v3-final-active-source-static-20261006T044554Z-a6eaa5df/manifest.json)
- [格式](evidence/W2/multi-template-v3-final-active-source-format-20261006T044554Z-6be39dd9/manifest.json)

测试使用明示synthetic literal/ORM originals和FastAPI service doubles，只证明域计算/合同/源链拒绝。覆盖完整1098点与FULL负担、原SOURCE tamper/分母缺失、strict money/clock/authority、资产cap/scope/确认、恢复原quote与cap/全仓、联合Goal cap与完整分母、unsupported非0，以及合法JSON UUID/服务端owner/clock转发。未运行任何金融DB、浏览器、完整suite、当前或旧PG节点；没有把纯测试包装为PG证明。

## 后继真实依赖

根接线及真实schema生成后，应增加实际隔离RRRO API/物理全表零写节点，至少验证MVP两版本和FULL资产/恢复/联合Goal当前来源、source漂移UNKNOWN，以及原预览与实际确认后同截面差分。前端v3消费者尚未实现；旧v1/history-v2消费者保留。没有本包PG候选/run或性能测量。未来动作/完整组合/所有模板条件变更的实际金融适配及原FULL105完整验收仍未完成，不关闭编号。
