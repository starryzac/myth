# MVP-401 首页聚合：存储与只读服务设计

状态：**DESIGN_ONLY / NOT_IMPLEMENTED / NOT_VERIFIED**（2026-10-04）。依据 [401 preflight](MVP-401-preflight.md) 与当前冻结源码进行静态分析。本任务仅写本文；没有源码、合同、测试修改，没有数据库、金融动作或 coverage 执行。以下 DTO、提取、容量与验收都是实施建议，须在 304 验收提交后由 401 冻结合同时确定，不能计作已实现或已通过。

## 1. 一个实际快照

建议 `GET /api/v1/dashboard` 在一个 caller-owned Session 中聚合，沿用 DemoUser 与一个可信 UTC `now`。依赖必须在第一次 SELECT（含 User）前设置 **REPEATABLE READ、SET TRANSACTION READ ONLY**，整个聚合在 `session.no_autoflush` 内；依赖的原事务退出后统一结束，不在卡片之间创建 Session 或 commit。现 GET 已设置 RR，READ ONLY 目前专用于 `/audit/`，401 需明确扩展 dashboard 路径或提取同类只读依赖。模拟 identity、时钟、币种与时区遵现协议，不能由 query 覆盖 user/as_of/authority。

流程：输入数量/字节预检 → 同快照账面事实 → 一次 verified financial context → 纯边界/义务 → 完整曝光及所有 scope 的本金分类 → bounded 当前事项及原回执/确认 → 单次当前 epoch 审计验证和批量 run linkage → 装配各卡片有效性。组件共享实际加载行、相同 `now`，不在浏览器合并多个独立 endpoint 的响应。

银行独立事务与应用投影仍分开。若快照处于 bank SETTLED、应用 UNKNOWN/尚无 receipt 的间隙，展示原 operation 与待对账，金融上限失去当前投影证明时 null；不能依据新 bank cash 加旧 goal/position 推导安全额度。后续 GET 可以看见投影后的新快照，但本次读取不补投影。无需为只读首页持 shared command/reset gate 或锁 User：reset 的封存与业务重建为原子事务，RR 看见完整旧图或完整新图；强制银行与应用三段合并会改变原语义。

禁止调用 `refresh_execution_exposure`、`replace_proof`、`refresh_exposure/finalize_projections`、`open_simulated_bank`、`process_operation/process_redemption`、prepare/confirm/execute、save_assessment、policy refresh、record_trace、ensure_audit_epoch/append，也不打开 decision-recording capture scope。`effective_status` 只读时间推导可用，不能为了到期状态写库。

## 2. 现接缝与最小提取

| 当前来源 | 可复用保证 | 401 需要的最小接缝 |
|---|---|---|
| `api/v1/accounts.py:account_summary` | 当前非 CREDIT_CARD 现金、持仓账面本金/UNKNOWN、账单；不推断权限 | 将查询/纯装配移到 caller-owned 服务，原 endpoint 委托它。首页账面事实可保留；它不是 BANK_CONFIRMED 当前余额证明。无现金账户来源时卡片未知，不能拿 sum([]) 冒称已证明为零。 |
| `boundary.load_boundary_context` 与 `domain.boundary.compute_boundary` | 来源验证、原 GoalOwnership、真实结清、91 日/273 阶段保护与最低余量 | 加预读取限额/可复用 rows 接缝；只加载一次 context。计算完整边界后返回首页 summary，保持原算法/hash，不镜像算法。 |
| `asset_exposure_import.load_asset_exposure` | 完整 current row set、v1/v2/v3 exposure、原申购/授权/执行关系 | 提取一次加载/完整核验 core，返回显式成功状态与所有 scope 分类。现空对象 `managed=0,pending=0` 也会在错误路径返回，**不能独自当作确证零**。现 wrapper 行为可保留，401 使用新显式结果。 |
| `autonomy.assess_action/_basis/_facts` | 原 effect、当前来源与权限、精确确认、四级评估 | 提取 caller-owned 批量 basis/assessment helper，保留现 classifier/revalidate_execution；已提交动作不用它重新分级。基线可共享，动作自身预留、权限、收款方和 confirmation 仍分别核验。 |
| `execution.get_action`、`verify_execution_receipt` | 原请求 hash、单 receipt、完整经济回单验证 | 复用校验 core 或批量装配；get_action 只支持 `request.execution`，不能拿来解释旧 recovery。其原 autonomy_level 不等于当前可执行结论。 |
| `get_recovery_run`、`verify_recovery_receipt` | 原 preview hash、当前实际 boundary 与原请求/回执协议 | 抽取单动作状态/回执读取，避免每个 recovery run 重新算 boundary；get_recovery_run 本身未对 receipt 运行完整 verifier，401 不能只看 receipt_id 就称已核验。 |
| `audit_chain.get_decision_audit_status` | 无自身 DECISION_RECORDED anchor 的 run 不继承链 VALID | 批量同快照 run/anchor 查询与 epoch verification 复用；不得每个动作调用一次完整链核验。 |

最小新服务草案：`read_dashboard(session,user_id,now,*,action_limit=20)->DashboardResponse`；内部只读 context 显式持有行集合、verified component 结果和本请求 epoch 结果。辅助 `read_verified_exposure_all_scopes(...)`、`read_pending_action_views(...,basis,...)`、`get_decision_audit_statuses(...,run_ids,limits=...)` 属 401 提取建议，当前不存在。不要把其它服务的私有函数复制一份，更不能为减少查询省去其严格核验。

## 3. 已自主配置：证明链与归属

可信当前本金依赖的是以下完整链，不是非空 `AssetPosition.policy_version_id`：

1. 同用户唯一 VALID/BANK_CONFIRMED 的完整 SIMULATED_ASSET_EXPOSURE，hash、时间、水位与 current accounts/positions/actions/receipts/transactions、v2 bank requests/postings、v3 operations/resource claims 全集合一致；301 执行必须 v3。缺、冲突、过旧、坏 hash 或 UNKNOWN/未投影使精确合计不可证明。
2. 每个当前 position 有唯一有效 SIMULATED_BANK_POSITION。manual acquisition 经完整校验明确排除；automatic 必须 `synthetic_auto_purchase` 且存在唯一 MATERIALIZED 动作声明，原 action/request、receipt、position 的 owner/product/goal/principal 对齐。
3. 银行申购 DEBIT 交易及其证据有 ASSET_PURCHASE 经济角色、金额/时钟/资金账户对齐；execution-purchase-v1 用 `execution_sources.acquisition_transactions` 核完整原 purchase legs，禁止一笔交易 materialize 两次。银行证明显式绑定原 action/receipt/transaction IDs。
4. `_scope` 验历史 asset_authorization 的 configuration/hash、确认及来源，scope 与 position/action.goal_id 对齐；不是用当前最新策略重写原授权。当前暂停/撤销不能删除既有本金归属或自动把本金变成一般现金。301/205 成功回执继续调用既有完整 verifier；manual/UNKNOWN 不升级为授权申购。

现 exposure 的 `managed_principal_cents` 包含所有未 REDEEMED 的已核自动 position，包含 REDEEMING；首页应拆出 `held_or_matured_cents`、`redeeming_cents` 和待对账状态，而非把全部看成可即时支配。保留 `managed_current_principal_cents` 为已配置总本金口径，明确子项包含关系。已 REDEEMED 不计当前本金；未来返本、yield 不计现金或可自主额度。SETTLED 未投影不能猜新本金/现金，配置卡 null 并显示原在途事项。

一次完整 exposure 核验之后按原 scope 分 general 与每个 goal，position ID 集合互斥且总和按 ID 去重；不能每个 goal 重新全库载入并核验。pending_purchase/reserved_cash 是在途/约束，独立列出，不与已 materialized 本金或现金相加成总资产。老 `RESERVED_UNDEBITED` 与 301 `EXECUTION_RESERVED` 按原声明语义区分，不能把仅 PLANNED preview 统一算作实预留。

GoalOwnership 的 `allocated=cash_owned+principal_owned` 已由 `_goals` 对实际 positions、cash 及完整归属证明校验。首页 goal 总额与配置本金是交叉视角：goal principal 可以包含在 managed total，goal cash 已包含 protected.goal_cash；二者不得再次从现金扣或额外加到资产分块。未明确映射的 GOAL 账户现金使用原 UnassignedGoalCash 显示受保护未归属现金，不归入一般闲置。

## 4. 待处理动作、确认与恢复建议

同用户 ActionPlan 的候选查询不能只按“最近 N 个”或仅 status PLANNED。pending 条件至少包括 PLANNED/AUTHORIZED/SUBMITTED/UNKNOWN，以及 bank ACCEPTED/UNKNOWN、bank SETTLED 缺有效 receipt、成功 action 缺 receipt/关联冲突；后一种须可见为完整性/待对账问题，不能过滤掉。CANCELLED/INVALIDATED/FAILED 只有已核无经济效果且资源已释放才是终态，银行仍有受理/结算/未知事实时继续 pending。跨用户条件用于 action、operation、receipt、run 查询，不靠 UI 过滤。

先 SQL count 候选集，再按固定严重性与 `(created_at,id)` 稳定排序取 limit+1；建议默认 20、最多 100。批量载入关联 run、operation/legacy request、receipt、claims 与所需 proof，识别真实重复关联而非 first() 选一条。返回 pending_total/list_complete/has_more；不能只因第一页没 ASK 就输出 NONE。401 无资金 POST，列表只给实际可用的读取/解释入口。

| 实际阶段 | 读取与显示 |
|---|---|
| 未受理的 301 PLANNED/AUTHORIZED | 原 effect/hash 校验后做当前 `assess_action` 等价只读评估；authority/feasibility 仍按原 validator。保留 prepared_level、当前 decision.level、evaluation_only。 |
| 原 ASK_ONCE 已确认 | 精确 effect 的 `read_execution_confirmation` 验 action/effect_hash、USER_CONFIRMED_ACTION、server accepted、观察/有效期；显示 confirmation_required=true、confirmation_satisfied=true、是否当前 execution_eligible。原 level 保持 ASK_ONCE，不重复问同一有效确认，不把 authorized_at 或请求中的 accepted 布尔当证明。已过期/原后果变化不能继续称已确认可执行。 |
| 任意 operation 已存在或 action SUBMITTED/UNKNOWN | 现 assess_action 明确 NO_RECLASSIFICATION_AFTER_ACCEPTANCE；读取原决定/确认作为历史说明及当前 bank/receipt，不重新 AUTO/ASK 授权。原同意过期不要求已提交的同一银行效果再次确认。 |
| bank ACCEPTED、T1 未到账 | 等待结算，给原 available_at；到期 GET 仍不执行结算，不把计划到期当实际到账。 |
| bank SETTLED、无 receipt 或投影失败 | RECONCILIATION_REQUIRED，保留原 action/operation IDs，展示“银行已提交，应用待对账”；不叫资金失败已退回，不造新 operation。 |
| 真实 receipt 存在 | 301 用 verify_execution_receipt；205 用 verify_recovery_receipt。只有成功验证的真实执行金额/费用/损失才称已投影。 |

**ActionPlan 之外的 205 ASK 缺口必须保留：** `run_recovery` 只为 AUTO steps 建动作；有损 ASK 存在 saved RecoveryPlan candidate，`get_recovery_run` 返回 ASK_ONCE 而 `actions=[]`。所以首页需另一个 bounded `recovery_proposals` 读集合：同用户 SAFETY_RECOVERY run 的原 input_snapshot/preview/trace 校验，保留 run_id、原 request_hash/quote、原 as_of、费用/损失及读取时是否仍可证明。它是 REVIEW_RECOVERY_PROPOSAL，不伪造 ActionPlan ID，不宣称已有 confirm_action 权限。旧建议可能过期或当前缺口已变，显示历史待复核与只读解释；只有重新走实际执行准备才有当前具体经济确认。不能因为 ActionPlan 为空把已知有损提议藏成“无需介入”。

全局介入聚合按 RECONCILIATION_REQUIRED、CONFIRMATION_REQUIRED、REVIEW_REQUIRED、UNKNOWN、NONE 表达，并保留原因/实际事项 count。NONE 仅在 action/proposal 集合完整、来源/审计没有未知或错误，且没有当前需要用户处理的事项时表示“当前无已知待处理确认”；不宣称未来所有动作获授权。已核 ASK satisfied 不计需要再次确认；来源不足、列表截断、预算不足不可变为 NONE。

## 5. 局部有效性及最小 DTO 草案

每张卡片独立 `state/issues`，不要用一个 status 字符串把所有卡清空或涂绿。账面账户行仍可在 exposure 缺失时展示，标 APPLICATION_PROJECTION 与观察时间；bank/projection 不一致时不能把账面值称当前独立已核余额。无法证明的金融值用 null，来源完整且真实零才 0。来源错误不是合法空目标/无动作集合；真正空集合必须有完成读取的 complete=true。

提议结构（伪合同，仅文档）：

```text
DashboardResponse:
  simulation=true, user_id, as_of(aware UTC), timezone
  account_facts: {state, book_cash_cents: int|null, accounts,
                  oldest/latest_observed_at, bank_projection_state, issues}
  boundary: {state, financial_only=true, status,
             safe_idle_cents: int|null, minimum_margin_cents: signed int|null,
             deficit_cents: int|null, protected_cents_by_reason: map|null,
             input_digest, boundary_hash, constraining_date,
             blocking_constraints, calculation_notes, explanation_complete, issues}
  goal_ownership: {state, cash_owned_cents: int|null, principal_owned_cents: int|null,
                   allocated_cents: int|null, unassigned_goal_cash_cents: int|null,
                   items, items_complete, issues}
  managed_assets: {state, managed_current_principal_cents: int|null,
                   held_or_matured_cents: int|null, redeeming_cents: int|null,
                   general_principal_cents: int|null, by_goal,
                   pending_purchase_cents: int|null, exposure_as_of,
                   excluded_manual_count, unknown_position_count, issues}
  next_obligations: {state, items, complete, issues}
  pending_actions: {state, total, items, list_complete, has_more, issues}
  recovery_proposals: {state, total, items, list_complete, has_more, issues}
  intervention: {status, reason_codes, known_required_count, complete}
  audit: {epoch_id: UUID|null, status, scope="CURRENT_LIVE_EPOCH",
          anchored_run_statuses, complete, issues}
```

Action item 最低为 action_id/decision_run_id/kind、status、prepared_level、current_decision|null、confirmation_required/satisfied、bank_operation_id/bank_request_id/status、receipt_id/receipt_verified、amount/fee/loss、prepared_at/read_at/available_at、audit_status、source issues 与真实只读 explanation identity。需要区分“无 operation 已核”和“operation 信息无法证明”，不能只给一个含糊 nullable bank_status。无当前评估的已提交动作 current_decision=null，带 CURRENT_CLASSIFICATION_NOT_APPLICABLE；原等级另存。

未知 scope/exposure/goal ownership、金额来源错误、同用户关联冲突按依赖影响相应卡片，不用“其它卡可读”降级真实 Integrity。已知 audit INTEGRITY_ERROR 影响的原金融对象不能继续宣称已核授权/当前安全；原账面事实仍可保留并标未核。audit INCOMPLETE/LEGACY/UNSUPPORTED 不等于确证金融为零，也不等于已完整审计。组件 issues 最多返回一页及 issue_count/complete，不能静默截掉否定理由。

所有金额整数分，signed margin 保留负值；null 单独语义。服务端 int64 仍须前端 Number.isSafeInteger 护栏，超范围显式契约错误而不舍入。这里只给最小 summary，不返回完整 273-point trace；当前“为何 X”用同响应的保护/约束/hash/as_of 解释。点击再次计算的边界 endpoint 必须展示新 as_of，不能把新结果当旧 X 的证据。动作解释使用真实原 run/action 入口，GET 不保存新 run。

## 6. 下一义务的同算法提取

domain.boundary 目前内部构造 bill/recurring obligations：账单 unpaid=total-paid；recurring 使用原确认版本、月末截断 due_day、exact 或 range 上限/已知 final_total、真实 occurrence settlement，bill_balance 未出账不生成虚构负债。历史未结清 occurrence 可能产生 MISSING_OCCURRENCE_SETTLEMENT；不能凭“没有流水”当已付或零。

最小提取一个纯 occurrence builder 供 compute_boundary 与 dashboard 同用，返回 id/kind/bill_id 或 policy_version_id/period、**原 due_date**、实际 projection_date=max(today,due_date)、remaining_cents、金额口径 EXACT/CONFIRMED_MAX/FINAL_TOTAL、证据与 blockers。今日保护付款日不能覆盖原日期，首页仍能标逾期。按原 due_date/id 取最早日期的所有同日事项，保留 total/has_more；某一义务缺源时 state=INSUFFICIENT_EVIDENCE，不能只输出已知部分并宣称“下一笔确定就是这一笔”。窗口明确 91 日；窗内确证无义务仅代表该窗口。

## 7. 批量 audit：无 N 次完整链，也不弱化原件

当前 get_decision_audit_status 每次找到自身 DECISION_RECORDED 后调用全链 verify，首页对 N 个动作直接调用 get_action_trace 会导致 N 次完整链，且每条解释重复当前金融查询。最小提取如下：

1. 在同 Session 批量验证所选 live run 的 owner/identity，读取同用户 current OPEN head，再一次查这些 run 的 DECISION_RECORDED anchors。缺自身 anchor 返回该 run LEGACY_UNAUDITED，不能继承另一个 run 的 VALID。
2. 在本请求按 epoch 缓存**完整** `verify_audit_chain` 结果一次，保留原 canonical/hash/head/ancestry/原 snapshot、current immutable originals、303 constraint/parent/action 与 receipt 检查。同一 reset 快照中 live rows 与当前 epoch 必须对应；不借旧 SEALED 同 UUID anchor 给新 live row 背书。
3. 对确有对应 anchor 的每个 run 绑定本次 epoch 结果；未知/缺失/完整性错误原样返回。不替换成“链头 hash 没变所以 VALID”。不能跨请求用 `(user,head_hash)` 缓存：current 原件可能改动而 head 不变，304 的 immutable/current 检查不可省略。可缓存单请求 loader 的实际 receipt 验证结果，不能缓存跨快照许可。
4. 数量/字节在装载 canonical 文本前检查，超 dashboard 审计预算返回 INCOMPLETE，不读取截断前缀后声称 VALID。建议提取 `AuditReadLimits` 参数，304 原默认保持不变；401 可较小上限。没有 audit history 时只返回 legacy，不 ensure epoch。

正常只需一个当前 live epoch 完整验证；不在每次首页读取所有已封存历史。UI明确 CURRENT_LIVE_EPOCH，完整历史保留专用 CLI/审计页范围。获取原 receipt/决策内容与当次 audit 结果共享 rows/verification context，是复用验证，不是用未核快照替代它。

## 8. 容量及失败边界（建议值，待实测冻结）

在任何现 helper 的 `.all()` 之前，用 SQL count 和 JSON/text 字节总和准入；只做 count 后截断金融事实会制造假完整集合。建议金融基础：accounts≤100、goals≤100、当前 boundary policy/product 各≤100、positions/bills/actions≤10000，证据/transactions/receipts/claims/postings 另行明确总数及共享 **64 MiB 原件预算**；具体历史上限不能超过现算法支持量。事实过大使依赖卡 INCOMPLETE，不借少量最近记录补成当前总额。原 exposure 的完整 current-set 需求不能改成 SELECT LIMIT。

待处理默认20/max100、恢复建议默认10/max100、返回 issues/约束/轻解释有界并带截断标记；响应建议≤512 KiB，过大必须显式页完整性，不能把金额和未知原因静默删除。审计可提议5000 events/10000 subject versions/32 MiB（小于304默认10000/20000/64 MiB），限额不是已测性能保证。无后台补录、无数据库持久 cache/新表/迁移。保证 SQL/全链查询次数随卡片种类及实际 selected epochs 增长，而非 N×全链；细粒度动作验证仍有界 N，可后续共享 loaders 提取，不能先省验证。

## 9. 真实 API / Edge 准备入口与待验收

下列只是现源码中可用于未来 401 测试准备的来源，**本任务未调用、未运行**：

- `temporary_database`/当前迁移及 seed 构造隔离随机库；account/boundary/managed manual-only 真零场景参见 test_boundary_service、test_asset_allocation_service。缺/坏 exposure 必须与确证 manual-only 零区别；已绑定 policy_version 但缺 purchase/action/receipt/交易链必须仍不可证明。
- 真实 AUTO 当前本金使用 test_execution_goal_creation 的 public_zero_goal、实际策略确认、prepare/execute 申购及 receipt；目标本金与 general 分开且交叉总额不重复，原策略后续修订/撤销不改变过去来源。老资产 fixture 的标记不能冒称真实301执行链。
- ASK 未确认/已确认/过期用现 execution API 的真实 confirm；同一 exact effect consent。SUBMITTED、T1 与旧205 ASK 使用 test_recovery_service 的真实 run/preview；旧 saved ASK actions=[] 的事项仍可见。
- UNKNOWN/bank SETTLED 无投影利用 test_audit_workflow_integration 现真实 INSERT 故障边界，保留真实独立银行提交；读前后所有表完全相等。并发暂停银行提交后、原投影前，在另一只读 Session 读取首页；原阶段放行后整份刷新，证明没有拼接假上限。
- 工资新资金目前 test_goal_allocation_service.income_ledger 等 fixture 构造 BANK_CONFIRMED 交易/完整资金来源；它不是现成产品银行收入导入 API。实施前须确认可复用合法银行入账/账本接缝，在 fixture 中同步原完整 bank proof/ledger/exposure，而不是只改 Account.balance、opening、源码返回或网页金额。大额消费同样需要真实受信消费事实与独立银行账本一致入口；现 recovery_fixture 的风险准备不能自动当作消费入账 UI 已实现。缺合法准备接缝就显式补401测试适配或标该黄金链未完成，不能提前冒称404控制台可用。
- API实际断言：SHOW RR/READ ONLY、一次共享 now、全表前后相同、foreign隔离、局部null、极限/截断不NONE、坏源rawbook保留、合法status变化与当前immutable篡改、单epoch verifier调用一次而每run anchor明确；统计SQL/loader调用只能证明复用，金额与经济结果仍需真实原件验证。
- Edge E2E 经实际 msedge→Web代理→FastAPI→随机PG，使用真实 API准备场景，禁止 route.fulfill固定金融响应。保留browser/channel、网络完整响应、截图及run/action/receipt关联；A/B/C三条黄金链与确认/UNKNOWN/待结算只读刷新都要覆盖。原 health E2E只提供连通性，不计401金融通过。

401实施时先冻结金额/局部状态/事项与原件有效性合同，再做真实API RED→GREEN、最小提取回归、UI单元与真实Edge，最后统一check/源码路径hash/证据归档。本设计不预填任何验证数字，不释放304源冻结，也不提前执行后续任务。
