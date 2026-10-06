# 原件 V2 与私有冻结执行合同

状态：工具和私有入口已实现，正式 24 原件尚未冻结、正式正路径 NOT_RUN。不能关闭 MVP-503。

2026-10-05 显式接入修订保留旧 `mvp_corpus.py` 及其32原测试。新 `scripts/mvp_corpus_v2.py` 使用独立 draft/freeeze-v2 协议，绑定当前21 kind 的实际 DTO、严格向后对象引用和原 expected_error。`RUN_RECOVERY×FAIL_APPLICATION_PROJECTION` 新合同只中断指定原银行已结算后的应用投影；默认NONE不增权限，旧 recovery 金融代码不改。原件/候选归档 `.runtime/W1-recovery-corpus-artifact-integration-20261005T084406Z-b4b0f5fe`。162相关pure、六源types/Ruff通过；相关实际恢复两节点2PASS/246.05秒，`recovery-original-key-projection-real-pg-20261005T084739Z-43e4b007`。这些结果不是24×5或全量验收。

V2 Case INPUT 的 intended_purpose 与执行片段必须从作者原件开始一致。完整未经改写的原 INPUT 字节SHA注入 typed Scenario 的 frozen_case_sha256，另外保存原片段、转换片段、完整typed片段SHA，避免自指。每次 `prepare_frozen_case` 完整fresh重验归档字节、外部登记manifest SHA、seed/source/design/oracle/五rule、配额和开发流程排除；不存在跨请求授权或验账缓存。

私有 `scripts.mvp_frozen_runtime.run_registered_frozen_case` 接收实际Engine、可信caller owner/epoch、外部登记registration字节SHA及全新输出路径。注册只从 `.runtime/W1-frozen-case-run-registry` 读取，拒绝额外字段/开发用途/正式共享库/外部路径。每次核原 Case/typed输入/source inventory，再在真正本机生成bf_test的RR/READ ONLY事务查询current_database、simulated user与OPEN epoch。事务结束后再次fresh重验，才调用当前原 `ScenarioRunner._execute`；loaded原方法CodeType与登记实际源重编译比对。公共 `ScenarioRunner.run` 仍拒绝所有冻结用途，Web/RPC不接ready token，也不接注册和冻结回传结果作为可信授权。

初态必须由trusted harness先在新生成bf_test调用原seed，再登记实际epoch；输入mode=EXISTING，不能在已有模拟历史reset/seed。新输出目录exclusive创建，原result/error保留；执行后的source再次复核。输出固定financial_effect_evidence=false，真实ScenarioResult须由独立经济、五臂、14指标观察器另核，不能仅凭EXECUTED宣称资金或实验成功。不存在完整原件时在SQL前拒绝。

命令入口 `python -m scripts.mvp_frozen_runtime --registration <原注册> --registered-sha256 <外部可信digest> --user-id <实际owner> --epoch-id <实际epoch> --output-directory <新私有输出目录>` 默认仅重验；只有显式 `--run` 才执行。当前无正式正路径原件、无120次实验结果，相关纯拒绝风险不能替代真实金融验收。

未覆盖：V2 Case与五臂provider原机会/时钟注册之间的显式适配；独立14指标传递源注册；全部24完整输入/oracle及冻结；正式P/五臂执行、实际actor计时和全原件；八图和最终材料。缺口保持，不补造成功。

## 私有入口实际定向结果

22 TOOL_TEST_ONLY拒绝风险/1.70秒与两源types通过，final-pure-20261005T090028Z-7e305dc8 / final-types-20261005T090029Z-598c25f4。首pure21PASS1FAIL证实audit_command_guard自己会先数据库connect，原失败原件保留，完整原件门移到guard前并在guard内再次重验后GREEN；四type RED保留。无正式24、无frozen实际正路径。
