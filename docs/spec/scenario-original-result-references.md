# Scenario 原返回引用与预登记拒绝执行修订

执行修订 `W1_SCENARIO_ORIGINAL_RESULT_REFERENCE_V1`，2026-10-05。新增运行能力用于同一请求内串联真实服务，不改变原编号关闭规则，不将开发风险用例标为冻结实验。

引用格式为 `{"$ref":{"step_id":"先前步骤","pointer":"/result/字段"}}`。只有严格向后的真实结果可用；错误、未来步骤、同一步、循环及非法 RFC6901 转义均拒绝。原结果字节和引用值分别记录 SHA256；展开返回独立副本，原结果中的 `$ref` 只是数据。输入及展开累计字节、节点、深度分别受限，先检查再复制。没有跨请求授权缓存。

可预登记严格 `expected_error:{code,status_code}`。实际服务恰好返回该错误才继续，原错误仍保留；不匹配错误终止。意外成功保留真实返回与 `EXPECTED_ERROR_NOT_OBSERVED`，终止后续步骤，不能伪造拒绝或撤销已发生效果。公共 demo/RPC 小请求合同未新增任意实验输入。

旧 `scripts/mvp_corpus.py` 的字符串引用是另一合同；旧冻结工具、SHA与原32项纯测试证据保留。本修订尚未接入正式 corpus 冻结：需要显式 V2 adapter 校验完整 Scenario/Step、合法 fault-kind、完整原 input/source/oracle 哈希，不能静默改写旧字符串引用或旧 purpose。`ScenarioRunner.run` 仍拒绝未核验的冻结执行。

当前验证：34项纯引用/预算/预期错误风险通过；5源文件 strict mypy、Ruff 通过。真实PG首轮原风险5 PASS、新节点因测试读取不存在的模型字段FAILED，原件不改。测试修正为从真实配置复算原摘要后，新节点独立1 PASS（5.35s）：原模板候选ID与审核摘要引用，实际REVIEW_MISMATCH拒绝后继续原confirm_proposal，仅一份真实PolicyVersion，零ActionPlan/BankOperation，原银行和审计属性VALID。不是24×5，也不是全量验收。

原件目录：

- `docs/progress/evidence/W1/scenario-original-references-real-pg-20261005T065640Z-cd57a290`：首轮5 PASS/1 FAIL，78.25s。
- `docs/progress/evidence/W1/scenario-original-references-final-real-pg-20261005T070014Z-97009127`：真实新节点1 PASS。
- `docs/progress/evidence/W1/scenario-original-references-final-pure-20261005T070009Z-06a9a200`：34 TOOL_ONLY PASS。
- `docs/progress/evidence/W1/scenario-original-references-final-types-20261005T070009Z-45f15028`：5源文件类型通过。
- `docs/progress/evidence/W1/scenario-original-references-static-20261005T065632Z-2e63341b`：静态通过，之后只修测试类型和真实字段。

未覆盖：通用声明、目标建立、策略变更、恢复/协调、冻结登记、正式actor、全部24案例、独立14指标与真实计时。原20/92计数不变，真人研究NOT_STARTED，真实资金接口关闭。
