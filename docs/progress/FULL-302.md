# FULL-302 新增资金联合分配

2026-10-05 13:34 UTC 实际增量：新增 `services/multi_goal_planning.py`、`api/v1/planning.py` 的 GET `/api/v1/planning/current-goal-allocation` 和 `tests/test_multi_goal_planning_api.py`，已接主应用与严格 RR READ ONLY。原全部 Goal 保留分母，逐项核实际完整模型/当前旧策略版本/归属/本月贡献；银行独立投影、原收入账本、完整当前审计和原365日保护曲线同请求重新核验。缺完整模型或银行篡改时 UNKNOWN、不删目标、不补零金额；候选只用当前版本确认和生效之后已到账未归属的实际收入，不能把旧现金或未到账收入当新来源。

必要真实证明按失败链分别保留：首四节点 [原 manifest](evidence/W3/full-goal-confirmation-and-actual-joint-planning-real-pg-20261005T132102Z-070b9fd2/manifest.json) 为 **FAILED，3 PASS/1 FAIL，27.67s**，scope/global稳定。两个目标模型节点（确认/重放/篡改、完整失败回滚）及联合规划缺模型/银行篡改节点通过；正向节点错误地预期确认前旧收入应立即获分配，实际原窗口正确给0。修为原 `ingest_external_fact` 模拟工资两真实银行腿后，第二次正向 [FAILED](evidence/W3/actual-joint-confirmed-window-payroll-real-pg-20261005T132442Z-c64c5652/manifest.json) 因测试读取不存在的旧 `account_id` 字段（实际 DTO 为 `source_account_id`），生产不变、原失败源留存。按真实碎片关联账户修正后 [单节点 PASS](evidence/W3/actual-joint-payroll-original-shape-repaired-real-pg-20261005T132545Z-0687ce86/manifest.json) 为 **1 PASS/12.07s**，wrapper14.853545s，scope/global均稳定；实际正金额、原工资交易与碎片、当期上限/预算、延期下界及两次GET物理27表零写已核。

该适配器明确 `ACTUAL_CURRENT_UNASSIGNED_INCOME_ONLY`、`ALL_ORIGINAL_365_DAY_RESERVES_RETAINED`、`grants_authority=false`。原365日目标最低储备全部保留，资金池可能保守；证明仅登记的当期有限问题精确解，不是完整版原多期问题全局最优。完整跨目标回拨、未来多期调度、最小修复公共路由和实际行动执行仍未覆盖。原追踪表 PENDING、整体goal ACTIVE。下方保留最初纯算法交付与历史限制，其“尚缺SQL”已由本增量部分覆盖。

状态：**当期纯求解能力已实现，真实财务适配及完整验收待集成；编号未关闭**。本批按功能优先执行修订推进，不替代初版或完整版全量验收。

## 完成内容与文件

- `apps/api/app/domain/multi_goal_allocation.py`：严格服务器事实 DTO；`solve_multi_goal_allocation` 返回八级整数词典序最优、不可行或 UNKNOWN。输入最多 8 个目标、128 个当前收入碎片及 1098 个实际保护检查点。
- `apps/api/app/tests/test_multi_goal_allocation.py`：直接能力及风险测试。24 个小域案例由独立分钱穷举求真实目标向量，与生产求解结果比较；穷举仅在测试内使用。

资金池仅包含已到账、尚可用、尚未归属的收入碎片。每个碎片保留 `fragment_id / origin_transaction_id / account_id` 及原银行证据；同一来源拆到多个账户可合法表示，其可用合计不得超过实际到账额。原已归属目标资金、已消费或预留收入、未到账未来收入均不作为新资金。收入发生时刻不得早于该目标原确认及生效时刻。每个实际时间点的义务、生活、应急、原目标现金及其他保护均不得侵犯；真实负现金风险返回 INFEASIBLE，不丢弃风险点。

## 明确的当期数学合同

原计划八级顺序保留：硬义务零违反；应急/生活零违反；当期最低贡献短缺总和；重要性加权的该最低缺口；目标延期；月 target 距离；不超过 max；实际资金调动次数。最低保证是硬约束，月贡献 min 是资金紧张时报告的软缺口；前两级及 max 由硬可行域保持为零。

只有当期决策、原当前资金和当前归属参与求解。第五级是未完成目标的**延期下界**，最早次日完成仍不能推断真实完成日；结果逐目标保留 `delay_censored` 和 `completion_date=null`。当期确能补齐目标时，给出假设执行当天完成的规划日期。已完成目标的历史延期为本次决策不可改变的常数，不重构其历史日期。延期成本另列下界，不能替代原第五级目标。

求解器按月 min/target/max、目标剩余额及完成端点分段；对整数流可行域的临界顶点求前七级，再以原账户到目标的非零资金边集合求最少调动。没有枚举每一分钱、浮点大权重或调用旧单目标求解器。相同账户内归属登记不算现金移动，但仍保留原收入使用明细。精确搜索超过登记的组合节点上限时 UNKNOWN，清空可执行金额和 incumbent，不把未证最优解输出为最优。

所有结果为 `CURRENT_PERIOD_PLANNING_ONLY`、`grants_authority=false`。原件真实性由 SQL 适配器核验，纯 DTO 通过不等于银行事实或策略授权通过。

## 已运行检查

三条 `scripts/run_scoped_check.py --task W4` 命令只绑定上述两个源文件，分别运行该测试文件、两文件严格 mypy、Ruff。原命令、退出码、日志和 source-before/after 保存在：

- `docs/progress/evidence/W4/multi-goal-allocation-direct-pure-20261005T125326Z-25f2a0f5/manifest.json`：41 PASS，0.76 秒。
- `docs/progress/evidence/W4/multi-goal-allocation-direct-types-20261005T125327Z-ad63c8de/manifest.json`：严格 mypy 两文件 PASS。
- `docs/progress/evidence/W4/multi-goal-allocation-direct-static-20261005T125328Z-fe60d2e6/manifest.json`：Ruff PASS。

测试包含 20,000,000 分的真实量级整数输入，扩大金额后节点数与相同两目标小金额输入一致；这只是算法直接测试，不是数据库性能测量。其他反例包括来源拆片超发、确认前收入、预留/消费不可用、所有保护点及独立其他保护、悬停目标保留分母、容量不足 UNKNOWN 和篡改输入。

## 未覆盖与下一前置

尚缺实际 SQL/HTTP 联合分配服务、原 Goal/PolicyVersion/FullModel/银行收入与保护曲线逐项验真，以及真实隔离 PostgreSQL证明。当前仅当期有限问题，未实现多期已到账时间调度或对未来实际完成日的预测；不把 censored 下界当真实总延期。实际执行、同键恢复和新旧策略竞争继续由原动作系统负责，不能凭求解结果获得权限。完整 FULL-302 验收仍待最终集成与全量节点。
