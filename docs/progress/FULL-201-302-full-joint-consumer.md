# FULL 保护消费到当前期多目标联合规划

## 实现与验收状态

2026-10-05 UTC，功能优先执行修订下新增可运行 domain/service/router，复用原实际目标模型、收入归属、365日边界与八层精确词典序求解器。功能是有限当前期只读规划，FULL-201/302 等原编号验收仍 PARTIAL；没有关闭多期全局最优、银行执行或完整版本验收。

GET `/api/v1/planning/full-current-goal-allocation`，operationId `read_full_current_joint_goal_allocation`。根任务将 `app.api.v1.full_joint_goal_planning.router` 注册到主应用；沿用现 `/api/v1/planning/` 的 REPEATABLE READ / READ ONLY 依赖。空查询 DTO 拒绝所有客户端事实、现金、未来收入、owner、now、授权、释放目标最低等额外字段。

## 可运行能力和真实来源

服务在同一干净 RR READ ONLY Session 内调用原 `joint_goal_planning` 和 `compute_full_annual_protection`。原 Joint 只有新增私有 keyword-only `capture_inputs: Callable[[MultiGoalAllocationInput], None] | None = None` 接缝；构建实际候选后回调其 deep copy。默认三位置调用无新增 SQL，原输出与哈希算法没有改变。观察回调不能通过修改副本改变原求解；原文件 bytes 已归档 `.runtime/FULL-joint-goal-planning/original-20261005T160907Z/multi_goal_planning.py`，原 SHA `1b6ec28899d8a4d044b1ce3f0371847a0eef79563fdb8bb3a976d724b3260a1b`。

原 Goal、FullGoalModel、policy version、确认窗口、当月已贡献、实际已归属余额、当前可用未分配收入 fragment/origin/account 和银行证据完全沿原服务。新消费者不接受客户端替代 input，不调用 LLM，不创造新收入或金融事实。原 Joint 与 FullAnnual 的 source digest 因各自使用的 FullGoal/FullPolicy 来源不同可以不同；本包不假造相同 digest，而绑定实际 owner、同一 aware as_of、完整数值曲线及全部当前原来源。

新服务对原 Joint/FullAnnual 的实际 evidence ID 并集和所有捕获 SourceReference（含收入 bank_evidence_id/hash）再次 fresh owner-filtered SELECT，调用原 `_evidence(..., lock=False)` 核原 content/hash、VALID、observed_at/valid_from/valid_to。没有 FOR UPDATE、没有 write/commit、没有跨请求缓存。当前 owner/clock/hash/证据来源失效或缺失保留明确 UNKNOWN。

纯确定性接线层按完整 366日 × 每日3相位 = 1098 时点核对索引、实际本地日期、phase、现金和原 obligations/living/emergency/goal_cash/goal_minimum 五分量与 margin。Full curve 必须保留原五分量；额外 floor 只可为 full_dated_expense、full_periodic_transfer、pending_cash_reservations。完整 occurrence 清单按每个原时点复算条件支出与 outstanding 上界，核 Full cash = 原 cash − 仅已经到达其假设付款相位的登记金额；不能释放未付款保护却保留旧现金。重复 occurrence、不完整点、重新排序、额外未知分量或任一点变化都阻止成功。

绑定通过时，将 Full 的条件现金和额外保护加入原 HardProtectionPoint 的 other floor，保留全部原目标最低、已归属余额、living、emergency 和原义务，再调用**原** `solve_multi_goal_allocation` 的八层求解器。INFEASIBLE 时调用原 `find_minimal_goal_conflict`。`COMPUTED` 表示计算已完成，必须同时读取 allocation.status；INFEASIBLE 不是安全可执行。`binding.status=VERIFIED` 仅指本接线合同核对，不是独立经济效果证明。

任何 UNKNOWN 保留原注册目标分母；若捕获存在则原求解器返回各目标 amount=null、无 income uses、无 objective。若原服务本来不能构建候选（如 missing FullGoalModel/收入/完整源），新响应保留原 included/uncovered/registered_count 且 allocation/binding=null；不构造空目标假成功。失败时不会只取成功核对的部分时点去求一个更小问题。

## 输出与明确未覆盖

响应包括原 `original_joint`、完整 `full_protection`、逐源/点数/原输入与新 input binding hash、真正新 allocation/conflict、原因与限制。`simulation=true`、`planning_only=true`、`grants_authority=false`、`execution_support=NOT_IMPLEMENTED`。根前端可以展示原结果与新 Full 约束差异，不能仅凭 state 或 binding 成功当银行 grant。

当前支持可验真的 DatedExpense 原登记上界对原目标预算的实际收紧；未实际付款金额仍为保守上界，条件曲线不冒充银行结算。旧版本/暂停/撤销/过期策略欠付未知继续沿 FullAnnual UNKNOWN，不删除分母。周期转账指定来源账户只有当前余额检查，`future_account_debits_complete=false`；即使聚合现金和当前该账户够用，新联合结果仍 `FULL_FUTURE_ACCOUNT_DEBITS_NOT_PROVEN` UNKNOWN。当前账户流动性风险另保 `FULL_CURRENT_SOURCE_ACCOUNT_LIQUIDITY_RISK`，不借另一账户伪可行。完整未来账户扣款分配是后续依赖。

Full Seasonal 未实际采纳额外金额时只保原 advice 状态，不编造季节 floor。当前期贡献、原最低保留和有标记的延期/完成下界不是多期全局最优；未释放旧目标保护、未支持跨目标产权重分配、未创建 action/confirmation/bank operation，未扩大 FULL 模板银行权限。未来收入不用于今天分配。

## 必要验证与原失败

8源：新 domain/service/router、原 Joint 的最小 capture、四个直接 domain/service/API/integration 测试。日志和源 bytes/SHA 归档 `.runtime/FULL-joint-goal-planning`。

- 首纯风险实际 1FAIL/37PASS7.89s：测试把原 monthly_target=15 的原最佳金额误期望18，实际原 budget18/分配15，新 Full budget11/分配11。原源/结果保留，窄修期望不改求解器。
- 首组合实际 1FAIL/58PASS12.64s：测试误期望成功说明 reasons=[]，原 solver 真实返回精确当前期最优/延期有截尾说明。原日志/源保留，改为严格核与原 allocation.reasons 一致。
- 修后125相关纯/domain/API风险 PASS49.95s，含原 multi_goal_allocation、planning_contract、full_protection_projection；纯 ORM doubles 不是实际银行/数据库证据。capture 副本恶意改动不改变原三位置输出/hash/SQL数量，源 drift/未来/失效证据/日期/全分量/缺点/Full历史/账户风险均保 UNKNOWN。
- 首 strict mypy8 PASS；最新重复因根正在新增分类服务的 audit_chain/transaction_category 两依赖17类型错暂 RED，日志保留，不冒称当时全依赖通过。根修复后精确新结果追加，不改原 RED。
- Ruff8/format8 PASS。实际 PG 仅 collection：1 node collected2.32s，没有执行数据库或金融测试，没有跑全量。

唯一 PG 候选：`app/tests/test_full_joint_goal_planning_integration.py::test_actual_full_protection_is_consumed_by_original_goals_and_unknown_sources_stay_readonly`。根实际运行在新 generated bf_test：原真实 Goal+双 hash FullModel确认+原 ExternalFact INCOME，登记明确 planning-only dated 上界后目标预算变为10000、1098原分量不减、原 Joint/hash不变、同原 income refs、全物理表 GET 零写/假 query422，再核 periodic 来源 UNKNOWN 和真实现金篡改 UNKNOWN。该功能 fixture 用原可知 margin 构造测试声明，不是实验案例调参或独立 oracle/effect 指标。状态 NOT_RUN，必须根真实闭合结果后再登记。

下一前提：根注册 main/router、保持只读依赖、修根并行分类类型错误，然后只串行运行该必要节点。实际金融、银行/应用事务、UI/全量验收仍未测；不能据本纯包关闭原全部要求。

## 最终依赖窄修后的静态追加

根已窄修分类服务的17类型错误并保留原 RED/源。未重复125 pure；仅8源 strict mypy再次实际 PASS，原 `types-final.log` RED不改，新 `types-upstream-repaired-final.log` 单独保留。最终8源 bytes档案 `.runtime/FULL-joint-goal-planning/final-source-20261005T163734Z/manifest.json`，SHA `81c8451a84f9cd1fb84265c3d6e8aea5718d0da2538c67725e1046467e8c9ef0`。该初始 manifest 精确记录捕获当时 upstream17错；此追加/新check附件记录后来实际 PASS，不重写 manifest 为新成功。这里只冻结该8源，不声称全仓源稳定。PG/银行/全量仍 NOT_RUN。

## 首次真实 PG 与必要夹具窄修追加

2026-10-05 16:42 UTC 根任务实际运行的 W5 混合三节点已闭合，manifest `docs/progress/evidence/W5/actual-full-joint-and-explicit-category-patterns-real-pg-20261005T164204Z-eee000f7/manifest.json`：整批 **FAILED，2 failed / 1 passed**，pytest 43.82s、wrapper 48.791912s，exit1；scoped_source_stable=true，all_source_stable=false，仅独立问答前端两测试源并行变更。原 manifest/source/log 未改，不能把它改名成 FullJoint 成功或拆造本节点耗时。

FullJoint 实际节点已走过新 allocation=10000、全部1098保护点/hash/原 Joint 保持和所有物理表零写断言，随后周期义务测试声明因夹具 `synthetic-confirmed-joint-payee` 无已知银行身份而被原配置门正确拒绝 INVALID_EXECUTION_SOURCE，完整节点仍 **FAILED**。只将本新 integration 夹具 payee 改成隔离 seed 中真实已有银行来源的 `synthetic-landlord-001`，未修改 service、原 seed、bank/history hashes、授权门或任何风险断言。

原失节点源码另存 `.runtime/FULL-joint-goal-planning/actual-first-pg-failed-fixture-source-20261005T1650Z`；窄修当前 integration SHA `a46766df23013c3cb2062362997aa1855506755dbb3abf3d0360f5066d6922b4`，strict1/Ruff/format PASS。其他7源保持最终档案原 SHA。根随后唯一 session17027 串行包含该修正节点；截至本追加尚未收到其闭合结果，保持 PENDING_REAL_RERUN，不补成功，不重复125 pure。

混合批中建议初态未确认→UNKNOWN/实际HTTP零写/覆盖篡改拒绝节点 PASS，类别正链失败由根独立修复；这不扩大 FullJoint 实测分母。新的独立只读前端见 `FULL-201-302-full-joint-ui.md`，其合成直接检查不是实际金融或银行证据。

## 实际修正节点闭合结果追加

根唯一 session17027 已退出1。原 manifest `docs/progress/evidence/W5/actual-category-protocol-upgrade-joint-and-question-real-pg-20261005T165235Z-c356176b/manifest.json` 保持整批 **FAILED，3 passed / 1 failed**，pytest1918.29s、wrapper1924.938617s；scoped_source_stable=true，all_source_stable=false（独立前端和后续生产源并行变化）。FullJoint 的上述唯一实际节点 **PASS**，0011迁移和真实类别正链也 PASS；唯一失败是独立 Question 测试错误期望 EXECUTED 而原结果 SUCCEEDED，由其责任方另修。

这里仅登记 FullJoint 单一节点原结果 PASS，不称整批成功、不拆造单项耗时。该节点实际覆盖已确认 FullGoal、实际收入、原完整保护消费者、1098点/当前期八层重算、新 dated floor 收紧、周期来源 UNKNOWN、真实篡改拒绝及动态全物理表 GET 零写。此前 FAILED 源/日志/manifest 全保留；未重复125 pure或已通过的实际节点。原8后台 HOLD 已由根解除，银行执行、多期全局/未来账户扣款、正式全量仍未在本包关闭。
