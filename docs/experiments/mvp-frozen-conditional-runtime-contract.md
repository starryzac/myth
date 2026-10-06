# W1 私有完整作者案例条件运行入口

显式执行修订：`EXPLICIT_AUTHOR_V2_TO_CASE_V2_WITH_BOUND_CONTROL_SCHEDULE_V1`。完整作者原件另以不可覆盖 descriptor 进入 DESIGN 原图；Case 转换只把 scenario_id 绑定原 case_id，并把 SEED_NEW 改为可信驱动先创世、实际运行 EXISTING。整个 Case、原控制节点、五臂作者规则和未分类分母保留。不更改旧 corpus 合同、历史输入、正式模拟历史或失败证据。

生产源为 `scripts/mvp_authored_schedule.py`、`scripts/mvp_schedule_control.py`、`scripts/mvp_frozen_schedule_runtime.py` 及 `scripts/mvp_corpus_v2.py` 的显式可选注册分支。未采用这项注册的旧调用保留原合同；公共 ScenarioRunner.run 仍只接受 DEVELOPMENT。

## 已实核范围

- 完整作者原件及 DESIGN/compiler 源图：160 项直接相关纯测试 PASS/32.41s，`author-design-original-graph-related-pure-20261005T103829Z-bbfa1b95`；3 文件严格类型 PASS，最后 Ruff PASS。纯测试包含旧 corpus 32 项，不是金融实测。
- 条件选择：25 项 TOOL_ONLY 风险测试 PASS/0.54s，`conditional-frozen-schedule-dispatch-pure-20261005T104430Z-6c4f04aa`。B0/B3 必须有同一动作、同一 effect_hash 的原确认；原失败或跳过不能变成依赖结果；独立 READ_ONLY 在先前拒绝后仍可调度。B1/B2 不支持的意图和无主动恢复明确保留原机会分母。原过期/修改负例仍送原服务实际拒绝，不能在测试层伪造拒绝。
- 私有运行入口拒绝门和上述选择合计 49 项 PASS/1.83s，`frozen-conditional-runtime-pure-20261005T110319Z-6a96044a`；源前后全稳定。完整图不存在时五臂均在数据库连接之前拒绝，外部注册 SHA/原十三绑定/精确本机生成库/原输出不可覆写保留。
- 新运行入口严格类型最终 2 文件 PASS，`frozen-conditional-runtime-types-final-20261005T110354Z-a0b90724`。首次 2 个静态类型错误、当时原源和失败日志均保存在 `.runtime/W1-frozen-conditional-runtime-install-20261005T1104Z`；没有重新标记原失败。Ruff 已通过。

## 私有调用合同

`run_registered_schedule(engine,user_id,epoch_id,registration_path,external_sha,output_directory)` 只用于受控本机隔离模拟。注册文件位于 `.runtime/W1-frozen-case-run-registry`，protocol 为 `bounded-funds-trusted-frozen-schedule-registration-v1`。精确字段为 protocol、bindings、database_name、corpus_directory、manifest_sha256、typed_execution_sha256、source_inventory_sha256。bindings 仍是原十三项，五个 artifact SHA 来自真实 INPUT/ORACLE/DESIGN/RULE/SOURCE 全文。

入口先实际验证完整 freeze、typed Scenario、全当前 source inventory、loaded callable、完整作者控制原图。每个步骤新验证，不跨请求缓存授权；原 SQL RR/READ ONLY 检查当前生成库、模拟用户和 OPEN epoch。原 audit_command_guard 排除重置；不把事务保持到银行阶段。

PREPARE/CONFIRM/EXECUTE 调原 V2 provider，其他步骤调原 ScenarioRunner._dispatch。脚本 actor 在确认时实际读取同一原动作 effect/hash，写明 SYNTHETIC_SCRIPTED_ACTOR 及其冻结 source，而不是声称真人研究。原服务仍执行原授权、预留、银行、投影和审计门。

每个实际步骤保存独立调度原件、源绑定、实际单调时钟和实际返回/拒绝/跳过。只有真实成功返回进入后续 refs 清单；错误和跳过没有可引用的 result。执行返回的 ActionResponse 由原 execute 捕获之后实际 get_action 读取，明确标为 ORIGINAL_ACTION_READ_AFTER_EXECUTION_CAPTURE。保留 provider 原捕获及其经济 false 标记。

## 尚未覆盖

当前 24 个 Case 的完整冻结 constructor、开发输入全量原件差量、独立安全机会预分类、初始事实/完整 24 表原件、五臂正式真实运行尚未交付。新入口尚未在真实完整 freeze+PG 上运行；纯门验证不能替代该验收。

B3 恢复逐动作银行前确认缺口仍为 NOT_IMPLEMENTED；T02 原自然语言解析不支持内部转账，缺目的账户仍须原 DTO 实际 422，并等待实际来源绑定的澄清 actor。V2 provider 的故障注入需使用它实际创建的 Engine，不能用外层不同 Engine 包装冒认故障已触发。这些缺口仍阻止 MVP-503 关闭。

该输出是 `bounded-funds-frozen-conditional-run-result-v1`，明确不是原 ScenarioResult 顺序前缀。独立观察/指标读取必须为它建立真实原 whole INPUT → 条件记录/原服务返回桥；未接上时 A4 等仍 MISSING，不把 HTTP 200、结果状态或调度完成当作成功。

初版全量、完整版全量、性能 SLA、真人研究均未由本包验收；没有真实资金接口。
