# ADR 0015：恢复命令完成状态读取原结算，当前边界独立展示

状态：TARGETED_POSTGRES_VERIFIED；完整三轮与初版全量待验。

这是 W1 演示状态读取的显式修订，不改原25/67编号、权限、银行状态、经济效果或历史哈希。原完整 Edge `output/playwright/w1-20261005T053623Z-68eecf56` 六业务PASS，三轮第一轮FAILED。原trace显示同一个AUTO_REDEEM从COMPLETED在后续真实定存消费后变成BLOCKED，37快照、原视频/截图、失败及owned库VERIFIED_ABSENT保留。

原 `get_recovery_run` 仍以当前边界报告RECOVERED/PARTIAL_RECOVERY等当前观察。演示命令的COMPLETED现在另据同一原DecisionRun的SUCCEEDED及完成时钟、完整原动作集合、原SETTLED银行请求和实际SUCCEEDED回执读取；每条回执调用现有 `verify_recovery_receipt`，核完整原请求、独立银行分录、交易、证据、金额、费用和损失。无动作仅允许原NO_RECOVERY_NEEDED且原计划实际边界READY；未结算、UNKNOWN、缺回执仍保持原保守门。没有新增授权缓存，没有写结果标签或重评原经济效果。

界面原 `recovery.actual_boundary` 与当前恢复状态继续如实显示；命令说明明确原结算已完成、当前边界另见实际结果。原COMPLETED命令的重试只读返回，不为后续新缺口重跑旧幂等身份；新恢复需求须有新明确请求。

真实PG原件：`demo-recovery-history-actual-red-20261005T062443Z-b801059c` 在BLOCKED断言真实RED（105.16s）；`demo-recovery-history-actual-green-20261005T062753Z-8a667bcc` 业务修复通过，但测试误匹配错误文案，原FAILED保留；修为原错误码后 `demo-recovery-history-final-native-20261005T063313Z-cda2159e` **1 PASS/110.38s**。此单场景经原HTTP工资/目标/消费/自动赎回/定存实际购买与独立银行消费，证明后续风险仍LIQUIDITY_RISK、历史命令完成、原actions不变、GET/state/重试全23业务表零写、旧审计canonical_text不改，并在只读RR中逐一拒绝本金/费用/损失的未提交回执篡改。不是三个独立PG案例或三轮验收。

相关八文件mypy（明确namespace package bases）、Ruff及format通过；初次类型与工具路径失败保留。未覆盖：新源完整三轮、最终七项同run、断网黄金链、初版覆盖/性质分母与四命令关闭。
