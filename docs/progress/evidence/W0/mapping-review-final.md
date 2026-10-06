# W0 需求映射与验证选择最终复核

复核日期：2026-10-05（Asia/Shanghai）。范围仅为文档、原需求保真和工具证据复核；本次没有修改源码，没有运行 PostgreSQL、浏览器或全量测试，也没有关闭任何任务。

## 结论

[FULL 差量映射](../../../spec/full-delta-map.yaml)完整包含 **67 个唯一原 FULL 编号，67 项全部 PENDING**。每项保留原完整计划的任务标题、正文、行号，追踪表的完整需求、原验收和来源行号，并给出当前复用文件、测试/历史证据、具体未覆盖增量及唯一主要关闭归属。现有文件、历史 PASS、前置设计与映射本身均不能关闭 FULL 项。

[显式执行修订](../../../spec/execution-amendment.md)保留 W0 → W1 初版验收 → W2—W8 的排程及原关闭含义。复用不会删减初版 25 项、完整版 67 项，也不会代替初版或完整版全量验收及原人工审查。

| 主要关闭工作包 | 原 FULL 项数 |
| --- | ---: |
| W2 | 13 |
| W3 | 13 |
| W4 | 8 |
| W5 | 10 |
| W6 | 8 |
| W7 | 8 |
| W8 | 7 |
| 合计 | 67 |

## 实际复核及证据入口

- 当前运行 `python scripts/verify_full_delta_map.py`，退出 0：原计划/追踪表/映射编号集合完全一致；原任务正文和验收文本精确一致；主要关闭分区无重漏且与修订案精确对应；185 个复用文件存在；两份原计划字节哈希未变。这仅证明结构、原文与引用，不证明 67 项实现完成。
- [verification-tools-repaired/manifest.json](verification-tools-repaired/manifest.json)记录三条命令均退出 0；[02.log](verification-tools-repaired/02.log)保存上述映射核验，[03.log](verification-tools-repaired/03.log)保存 benchmark、selector、orchestration **22 个纯工具测试 PASS**。三份原日志哈希均与 manifest 一致；其四个源文件哈希与本次复核的当前文件一致。
- [verification-selector-repaired/manifest.json](verification-selector-repaired/manifest.json)记录选择器退出 0；[selected.json](verification-selector-repaired/selected.json)与[selection.log](verification-selector-repaired/selection.log)字节相同，结果哈希与 manifest 一致。当前选择器及 YAML 配置哈希均与该 manifest 一致。
- 上述选择记录中的 `test_verification_selection.py` 附带源哈希为随后格式整理前的版本；本次当前测试文件为 `fc3ab5e1934dfd8388659c340ace35e2668d0a445cb440fa416622aa883155f4`。最终纯测试证据由 `verification-tools-repaired` 绑定这一当前版本。旧选择 manifest 保留原样，不将旧测试源哈希当成当前测试执行证明。
- 原选择器因漏登记 `execution_projection.py` 的退出 2 记录保留在 [verification-selector](verification-selector)。修订后的规则明确包含这一回执消费者，与 `recovery_receipt_integrity.py` 使用相同完整保护集合；未减少原测试。

## 选择结果与实际通过的界限

[机器可读验证图](../../../spec/verification-map.yaml)依据原 `verification-map.md` 与父批次 PG manifest 建立。实际 repaired 选择输入为六个审计/历史/账本及回执服务、三个质量/性能脚本和 FULL 映射，共 10 个路径。

结果明确为 `SELECTED_NOT_RUN`、`executed=false`、`full_suite_selected=false`，未识别路径为空。阶段为 quick_static → quick_pure → integration_pg → benchmark，选中 4 个快静态命令、4 个纯测试文件、29 个 PG 函数选择器和三个固定场景的基准要求。

**29 是去重后的函数选择器数，不是 pytest 参数化收集数或实际 PASS 数。** 原 PG 首批的 26 个收集案例先于第七个 historical-read 负例加入；选择记录覆盖当前源码并扩入原存储保护。选择器没有运行这些 PG 节点，不能把退出 0 或选中列表表述为这 29 个节点已通过。实际运行仍须以对应 PG 原日志、退出码、最终源码和数据绑定为准。

选择保留原审计编码/纯域篡改负例、完整八个存储保护选择器、真实历史 GET 的 RR/READ ONLY/零写、UNKNOWN/T1、跨用户、原件改写/缺失及实际资金 POST 消费者。未知金融路径返回 `NEEDS_SCOPE_REVIEW` 和退出 2，附保守关键消费者及明确未覆盖项；不得把未知范围静默标为 GREEN。独立快检查可并行，失败后停止依赖慢链路，金融链/PG 归属仍顺序执行。

## 未覆盖与后续关闭条件

1. 映射保存的是 W0 开始时点的只读上下文，不是最终运行源码指纹。当前 `HANDOFF.md`、`scripts/tasks.py`、orchestration 测试相对映射时点发生后续修改，核验器如实列出；父批次最终开始/结束指纹及原日志负责绑定实际候选版本，不能只靠 HEAD SHA。
2. 本次不复测金融守恒、重复副作用、UNKNOWN、迁移或审计负例。上面的历史证据/选择列表不代替 W0 当前版本必要 PG 运行，更不代替未来 FULL 新增机制的直接测试与集成。
3. 本次不检查浏览器显示、移动端、无障碍及完整 E2E；UI/DTO/API 语义改变时仍需生成合同、前端类型和实际页面验证。只改质量编排不触发资金集成，但仍须保留所有不同门禁与独立命令行为。
4. 性能测量正在父批次独立运行。本复核不报告加速比、内存收益、SLA 或总体投入下降；纯工具测试通过也不是性能实测通过。短历史、MVP-401 长链、扩大历史须同数据/版本/冷热/审计范围对照，正常墙钟与插桩/profile 单独报告。
5. W1 尚需 MVP-404 和 MVP-501—504 的原验收：连续三轮、六真实 E2E、24 冻结案例、覆盖分母与性质、离线三黄金链、录屏、原文档及初版全量节点。未提交 MVP-404 只作候选复用，不在 W0 关闭。
6. W2—W8 的完整 DSL、双时态、持久消息、365 日、多目标/多资产、定存梯度、minimax、完整界面、冻结实验和实际材料仍按每项差量关闭。FULL 编号必须保留各自实现、原验收、直接风险测试、必要文档/迁移/示例、进度和源绑定证据，工作包必须交付可运行能力及具体未覆盖项。
7. 真人青年研究尚未开展，没有真实参与者或问卷数据；研究工具与开展状态分别验收，不以合成回答补齐。最终人工审查、真实材料页数、录屏和代码/数字/权限一致性不由 green tests 自动完成。

本复核仅支持“W0 映射与选择准备已可核查”，不单独判定整个 W0 性能与安全验收通过，也不改变原失败、正式模拟历史、原计划或任何任务状态。
