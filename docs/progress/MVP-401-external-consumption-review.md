# MVP-401 外部消费的完整投影与待对账边界

状态：`DESIGN_ONLY / NOT_IMPLEMENTED / NOT_VERIFIED`（2026-10-04）。仅静态读取冻结源码及 [E2E设计](MVP-401-real-e2e-fixture-design.md)、[preflight候选选择](MVP-401-preflight.md)。没有运行数据库、CLI、pytest或coverage，没有编辑源码/合同；唯一新增本短文。完整检查仍由 root 持有。

建议采用窄而诚实的合同：**外部消费先作为已发生银行事实入账；能够证明资金位置的才完整投影，不能证明时保留 SETTLED + projection UNKNOWN。** 不调用 Agent 权限/金融安全上限否认该消费，不通过自动取消动作、改目标归属或补未来返本完成投影。

## 源码证据

| 现来源 | 实际约束 |
| --- | --- |
| [models.py](../../apps/api/app/db/models.py)：Goal/AssetPosition/ActionResourceReservation | 实际使用 Account、AssetPosition、IncomeLedger/IncomeReservation 和持久资源 claims；没有名为 IncomeAllocation/AccountPosition 的持久表。Goal.allocated 是现金归属加未结清本金，不是可直接消费的余额 |
| [boundary.py](../../apps/api/app/services/boundary.py)：_goals | 现金归属由 allocated−实际本金及完整 ownership proof核验；归属超过账户余额报 GOAL_CASH_EXCEEDS_BALANCE；月贡献与归属须同一as_of |
| [domain/income_ledger.py](../../apps/api/app/domain/income_ledger.py) | 每个origin金额等于 AVAILABLE+RESERVED+ASSIGNED+SPENT；活动预留精确绑定action/use；release必须证明无经济效果 |
| [execution_bank.py](../../apps/api/app/services/execution_bank.py)：validate_income_locations | 银行 LOT_AVAILABLE=应用available+active_reserved，LOT_RESERVED只表示legacy_reserved；不能把银行LOT_AVAILABLE全部当未预留钱 |
| [domain/execution.py](../../apps/api/app/domain/execution.py)：_reservation_amounts；[execution_exposure.py](../../apps/api/app/services/execution_exposure.py) | exposure与claims是重叠预留，不能相加；goal预留属于已有goal_cash。ACCEPTED/UNKNOWN/已结算未投影不能被假定无效果 |
| [execution_projection.py](../../apps/api/app/services/execution_projection.py)：_goal_apply；[execution_reservations.py](../../apps/api/app/services/execution_reservations.py) | 现目标归属变化由真实goal经济/备查腿支持；月贡献仅真实ALLOCATE_GOAL增量。预留释放需原operation不存在且动作已取消/失效，或原请求精确REJECTED；没有普通外部消费减少归属的协议 |

## 建议的实际投影合同

**银行层与位置层分开冻结。** 已确认消费 C 的原身份、实际银行时间、CASH −C/外部收款方 +C、真实前序/seq/before/after及两腿守恒先独立提交；即使侵入预留/目标，银行结果仍SETTLED。bank result固定的是完整 **ECONOMIC两腿集合**。收入位置归因属于后续projection result的独立memo集合/digest；它不修改原银行结果，也不再产生现金腿。这是新协议建议，现BankOperation完整腿合同不改变。

投影前须取得原事实之前的已验证账户/完整income/goal/exposure/claims副本，核实际银行前序与应用before，并按银行顺序处理未投影前缀；不得拿消费后的坏current来源反推或修补before。完整来源有缺失、重复或无法解释的历史占用，直接记录unreconciled，不假造来源。

对目标账户之外的CASH账户，定义：B为真实消费前现金，G为该账户已证目标现金；A为全部真正AVAILABLE收入片段；I为全部RESERVED收入，包括legacy；R为活动一般现金预留中**未由I覆盖的部分**。R必须逐原action/effect/source配对扣除重叠，goal现金预留已包含于G，不重复扣；legacy/exposure无法唯一配对时不得猜max或盲求和。

`U = B − G − ΣA − ΣI − R` 是已证非追踪、未预留一般现金，必须非负。按 `(origin.occurred_at, origin_id, fragment_id)` 固定顺序优先消耗A，`q=min(C,ΣA)`，剩余 `C−q` 来自U。只有 `C−q≤U` 且全部before与实际legs相符，才进入完整投影：

1. 原transaction ID/evidence ID只创建一次；消费role=CONSUMPTION，Account从真实cash腿得到after，不从期望UI余额赋值。
2. 被消费片段AVAILABLE−use、SPENT+use，origin金额/原件不变；每个use追加同external fact因果的备查对腿。它们与账户、交易、余额/覆盖/income/exposure后继及投影事件在**同一投影事务**提交；任何一步失败全部回滚。
3. RESERVED、ASSIGNED、既有目标cash/principal、原action/claims和月累计贡献金额均不变；新完整proof使用新ID/hash与共同as_of，旧body/hash不改。goal归属/月贡献可续同值证明，不能只推进其中一份时间。
4. 验候选income守恒与银行位置、完整goal归属、所有仍占用资源的backing及新完整曝光。结果允许真实LIQUIDITY_RISK：生活/义务/应急保护是财务约束，不是拒绝已发生消费的权限门。

本稿的完整归因仅覆盖上述CASH一般消费，不扩展GOAL账户支出或goal drawdown。其他已发生消费仍保存真实银行原件；位置/归属未明确时保持unreconciled，不造账户/原点或否认消费。

最小projection结果保存 `fact_id/account_id/bank_economic_digest/before_snapshot_hash/attribution_as_of/algorithm_version/tracked_uses/untracked_cents/memo_digest/transaction及proof IDs`。memo时钟是实际归因提交时钟，原银行occurred另存因果，不倒改原消费时间。UNKNOWN只保存真实阻断码及冲突原件引用，不填写已完成结果。状态/字段迁移遵preflight一表合同，不能覆盖银行原件。

## 无法完整投影时及怎样恢复

| 情况 | 必须保留的结果 / 可恢复条件 |
| --- | --- |
| C只使一般金融保护出现缺口，但位置/claims有真实backing | 完整PROJECTED，展示真实风险；不是权限拒绝 |
| C挤占active/legacy预留；或受影响来源的原operation已ACCEPTED/UNKNOWN/SETTLED未投影，无法证明可独立归因 | 银行SETTLED、projection UNKNOWN，引用具体action/claim/fragment；原预留不释放、不把原动作改失败。先对账原经济结果；只在精确无效果证明后才能原协议取消/释放，再按同fact重试归因。已结算动作必须先按原银行顺序投影；未来受理的、不占本次资金的本金返还不提前当现金，也不仅凭ACCEPTED标签否认消费 |
| C使G超过剩余现金，或需要选择某目标/ASSIGNED片段承担支出 | UNKNOWN，保留原Goal/ASSIGNED/月贡献及银行消费。现协议缺少目标支出映射；不能按priority/FIFO降低某goal或把消费冒ALLOCATE_GOAL的反向操作。后续须明确的消费归属原件、goal drawdown/memo及来源版本核验，才可解除；单纯让用户接受一个总额不足以说明各目标/source如何变化 |
| before来源/重叠映射/旧经济顺序不唯一，或projection实际失败 | UNKNOWN保留完整银行原件和经济腿；修复真实来源/顺序后同键重试。不能重开income、改old proof或重放cash腿 |

现cancel/release入口没有新external pending协议，不能宣称可以直接调用它解决上述状态；需要后续pending-aware reconciler核原请求、腿及before副本。无法恢复时保持真实待对账，GET不修复，reset不替代解决冲突。银行after与应用before分别显示为已发生银行事实/旧应用投影；依赖完整源的金额为null，不拼绿色安全。

## 待验证的最小真实案例

| 案例（整数分） | 后续真实PG/API oracle，当前未运行 |
| --- | --- |
| B=100,G=20,A=30,I=10,R=20,U=20；C=40 | cash100→60；AVAILABLE30→0/SPENT+30，非追踪现金消费10；G/I/R均不变，真实完整投影 |
| 同before；C=60 | 银行cash40/收款方+60，经济sum0；可完整归因仅50，projection UNKNOWN，不偷偷释放10预留；应用与所有memo/后继整体无半套提交 |
| B=100,G=80，无其他来源/预留；C=30 | 银行cash70，UNKNOWN；不把Goal.allocated或ASSIGNED降10，不改月贡献；曝光/边界不假完整 |
| 一原action同时CASH/INCOME及exposure占用；另有goal-scope预留 | 证明原重复视角只计一次；匹配不完整应UNKNOWN，不能用错误double-count或max推导合法完整结果 |
| golden A3→C1 | cash450000→100000，goal10000、ASSIGNED10000与SPENT190000不变；工资AVAILABLE原已0，C350000来自旧一般现金，真实缺口25000 |
| 真projection INSERT失败及原键重试；真已受理动作冲突 | 银行两腿/原hash不变，memo与应用原子回滚；GET全表零写；原键只补一次投影，未对账action不自动释放；foreign user完全不变 |

上述范围可让黄金链消费完整投影，也能如实承载侵入情形。它没有实现通用目标支出或取消所有预留的能力，不将“银行已发生”与“应用已完整解释”混为一件事。
