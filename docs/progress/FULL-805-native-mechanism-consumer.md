# FULL-805 私有 GENERAL 机制执行消费差量

2026-10-06，功能状态 `IMPLEMENTED_PRIVATE_DEVELOPMENT_ADAPTER`；原编号验收 `PENDING`。源码：新 `services/full_native_mechanism_steps.py`、原 `services/full_native_cases.py` 窄分支、新 `tests/test_full_native_mechanism_steps.py`；合同 `docs/experiments/full-native-mechanism-consumer-contract.md`。不改旧 public routes/shared finance/模型/hash/原失败。

实际能力：真实 signed USER invocation 内，服务端固定 RULE → 已连接的 private FullExperimentAssetRequest → 原 prepare/ASK confirm/execute；原键 clean RRRO query 恢复，与完整实际 service bytes/hash、严格原 refs 和分母接原 mixed 入口。adapter 无 planner、authority/source 缓存，无银行协议扩展。

必要检查，均 TOOL_ONLY，无 PG/browser/银行实测：

- 初次 `W7/full-native-private-mechanism-pure-20261006T045135Z-71fb3cdd`：32 PASS、9 FAIL/5.68s；失败为新 fixture 漏传 compute_boundary 原三个参数。旧17 mixed 风险均通过；原 FAILED 不改。
- 初次 type `W7/full-native-private-mechanism-types-20261006T045146Z-78a435d3`：6 项新 test 类型错误；生产两个模块没有类型诊断。归档当次三源见 `.runtime/full-native-mechanism-before-20261006T044801Z/*.first-pure-red.py`。
- 修正并补 private runner 接线后 `W7/full-native-private-mechanism-repaired-direct-20261006T045416Z-636040fc`：两个直接文件合计 **45 PASS/3.75s**，wrapper 5.540031s，scope/global stable=true。
- 最终三个文件 strict mypy `--follow-imports=silent`：`...repaired-types-20261006T045416Z-d6addd34` PASS，scope/global stable=true。
- 最终 Ruff：`...final-static-20261006T045416Z-88c8d328` PASS，scope/global stable=true；format --check 三文件通过。

风险覆盖：client locator/金额/角色/时钟/成功字段拒绝；无注册/无 Cookie/过期 Cookie/错误 epoch 不准备；ASK strict accepted；原 action/owner/epoch/AUTO/NOT_FOUND 拒绝新执行；B4 原拒绝不回退；UNKNOWN 原 key 查询不依赖新 RULE/Cookie；原 confirm/effect hash 和 execute fault上下文委托；service bytes 替换拒绝；mixed 原件/ref/计数保持。签名测试实际用合成 credential/空客户端，SQL与资金服务全部 explicit doubles，不把这些 tests 当真实银行成功。

provenance 修订：首次 runner before SHA `76e650608cda9a4c732e3078aed83fc29f435310251669f5c9a35a8dbb2051f4`；精确 before、candidate 和 patch 保存在 `.runtime/full-native-mechanism-before-20261006T044801Z/`。04:48:02.8148413Z 首写，收到金融 HOLD 后保存 candidate/patch 并约04:49:04Z 恢复 before；这次恢复与用户“不回退代码”规则冲突，已向 Root 如实报告，未删除新增功能/失败。父任务该轮金融 provenance 需明确中途变化，即使 before/after相同也不能全快照验收。Root金融终态解除后核 current==before，再接完整保留差量并格式化，04:51:34.6824217Z；最终定向检查才作为稳定 source 证据。聊天曾给约04:52Z粗估，按文件原件时间更正上述日期。

下一前提：Root 独占串行金融验证真实 private consumer + same-key fault recovery/receipt/守恒；提供真实服务器签名凭证及 exact RULE bytes/source 登记。所有经济/实验指标未测 `null`；7臂/50×7/8消融/真人均未执行。本包不关闭 FULL-805。
