# FULL-303/304 现金归属逐来源原件证明

实现状态：新增生产只读辅助器。原七源 preview FINAL 不变；无金融回拨、无银行 grant，原任务尚未全量验收。

## 服务接缝和可用能力

`app.services.goal_release_provenance.read_goal_cash_source_proof(session, user_id, goal_id, expected_epoch_id, expected_goal_policy_version_id, now)` 只能在干净 RR / READ ONLY 当前 OPEN epoch 中调用。用户、Goal、原版本、实际时钟从真实来源核对；没有金额、银行结果、收入、权限或结论参数，也没有公共客户端 raw-input 路由。

调用原 `full_reconciliation` 的完整八表、EXACT audit、独立 bank ledger、当前 projection、逐 original command/银行完整腿/回执核验；调用原 `read_income_state` 的唯一完整 new-funds-ledger-v2 / 原 Evidence / 全 CASH scope 来源核验。额外完整读取四个原行集合（ActionPlan、BankOperation、ActionReceipt、SimulatedBankPosting），真实 count 与捕获分母及原 reconciliation 绑定，保存原 `row_copy` hash 引用和完整本请求输入 digest。原 bank posting codec 的 V1/V2 canonical 规则保持原样，不改历史 hash。

`GoalCashSourceProof` 的 VERIFIED_CASH_ONLY 要求：全用户所有已提交 ALLOCATE_GOAL 的原 effect.income_uses 与对应 COMMITTED IncomeReservation 一致，完整所有 fragment 的 ASSIGNED 恰好等于这些原用途总和；每个其他 Goal 同样保留分母。源 Goal 的现金必须有原零 opening、完整连续原现金 postings，所有增加都精确绑定已核 allocation，当前银行现金与来源总额一致。当前 owner/epoch/业务时间、完整库存、银行、应用及 audit 不符即 UNKNOWN，逐来源现金残额和完整缺失分母为 null。

输出 `sources` 保留每个原 income fragment 的 ASSIGNED、AVAILABLE、RESERVED、SPENT、原 origin bank Evidence、来源 Goal/其他 Goal allocation 数额；`original_allocation_slices` 进一步逐 allocation_action_id × fragment_id 给原分配金额、原 effect/bank/action request hash、原 policy_version_id、原回执及 Goal cash posting 引用，便于未来 releaseUses 精确扣残余。该 slice 不重写原用途。已核额为历史数据，绝不表示当前金融许可。

旧购买、赎回或 legacy cash 移动缺少原 ASSIGNED fragment 在 Goal cash/principal 之间的分割，保留完整操作数并返回 UNKNOWN；不猜 FIFO、不从总本金推造来源。非零原 Goal cash opening、丢失原其他 Goal allocation、只有成功字符串、缺完整回执/腿、原使用匹配失败、projection pending 和收入原件缺失均不得提升。使用该辅助器之前的新 RELEASE_GOAL 协议尚未存在；已有此新协议后必须由 Root 新事实 adapter 核全部 command/三腿并重算残余，不能过滤本 V1 UNKNOWN 理由后补成功。

所有输出 `assigned_income_decrease_cents=0`、`available_income_increase_cents=0`、`principal_change_cents=0`、`other_goal_change_cents=0`、`funds_released=false`、`bank_authority=false`。现金返回保护账户不是新收入，不能再次分配另一 Goal。最低保障、专用授权和跨 FullPolicy 全版本累计 usage 均由后续独立当前原件核验，本包不推断零用量。

## 原件与验证

五源 freeze：`.runtime/FULL-303-304/provenance/final-source-20261005T184625Z/manifest.json`，SHA256 `1b382c285631d11b09ffe1320ee4e8ec3f695dd7dcb264ac0eece5080b84adb3`。

- 新 domain/service 及三个 direct/integration 文件严格 mypy、Ruff、format 五源 PASS。
- 最新直接两模块 26 PASS / 1.86s，`pure-deployed.log`。合成 actual-shape/ORM doubles 仅合同风险，不是实际财务效果。
- 唯一实际候选 `test_goal_release_provenance_integration.py::test_actual_allocation_original_cash_sources_preserve_assigned_identity_and_zero_writes` 只 collection：1 collected / 2.54s，actual PG NOT_RUN。由 Root 在生成隔离库真实收入/分配/银行/回执之后核逐原 slice、每个原 bucket 保留、完整物理表零写及应用差额 UNKNOWN。没有修改正式库、seed 或旧历史。

首轮 25 FAIL（合成 effect 漏原完整 authority version refs）及 16 type RED、第二轮 3 FAIL / 22 PASS（posting fixture 缺原 predecessor/leg）及 1 type RED、后加风险夹具的 4 FAIL / 22 PASS（其他 Goal fixture 放错集合、purchase fixture 缺原 return/exit）全部日志保留。源原件分别为 `first-red-source`、`second-red-source`、`fourth-red-source`；失败 `pure-final.log` 原样保留，最终 green 使用独立 `pure-deployed.log`，未覆写失败。

## 最小真实执行依赖

Root 专用 GoalReleaseBankCommand 应独立于旧 ExecutionEffect dumps/hash，绑定本证明逐原 allocation × fragment 来源、专用明确 GoalReleaseAuthorization、当前数学最低金额和最低保障。Goal 原账户现金 −A、同 owner server-owned CASH 保护目的账户 +A、GOAL_CASH:goal −A 为三条精确原腿；原本金、其他 Goal 及全部 LOT_AVAILABLE/ASSIGNED/SPENT 行不变。成功原 releaseUses 只能减少独立已释放残余，不能更改原 assigned 分母。

累计 usage 从同 FullPolicyId 全版本的完整专用原 BankOperation command + 三腿核验，不以成功日志、空查询或确认数量推断。默认旧 Goal 桥 cross=false 及其拒绝负例保持；必须专用确认与当前金融事实二次验证才可进入 Root 的新执行。新的 bank/历史/receipt/projection 接线、UNKNOWN 查询、完整新 usage ledger、首次实际 PG、跨重启和版本全量验收均未完成。
