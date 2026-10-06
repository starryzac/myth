# FULL-903 当前实验报告

状态：DOCUMENTARY_REPORT_DELIVERED / EXPERIMENT_ACCEPTANCE_INCOMPLETE。报告正文、指标空表与真实定向证据索引已经可读；没有新运行实验，不关闭FULL-903。所有资金模拟，没有真人收益或银行业务效果。

## 1. 原实验范围，没有缩成初版

原完整计划15.1、15.6、14.2及FULL804/805要求：至少50场景族，每族多变体，族级开发/验证/冻结split；B0手工预算、B1余额阈值、B2固定预算+固定产品、B3全量确认、B4仅模型置信度、B5忽略流动性的收益优先优化器、P完整方案。八消融分别移除证据等级、策略版本、动态生活准备金、多目标约束、流动性过滤、最小问题选择、安全恢复、审计链。全部正式实测结果待取得。

初版另有24案配额6/6/4/4/2/2、B0—B3/P及14指标，不能替代Full50族和七机制。作者/开发完整flow新颖性、原冻结真值、独立oracle、合法实际不同策略adapter均需原件。局部provider风险或MODEL_ONLY候选不能重标成完整SERVICE_INTEGRATION。

## 2. 统计口径与缺失

安全比较使用相同事实、初始权限和保护网关，分别列unsafe candidates、gateway拒绝、实际bank/app后果。拒绝不是资金违规；外生消费缺口不当作Agent造成。safe-auto机会、截止checkpoint、required Evidence/audit目标与失败步骤在运行前登记，未尝试或失败不删分母。

`results/observations.csv/json`是MATERIAL_OBSERVATION_PLACEHOLDERS，不是原实验输出：14初版+28完整计划指标均NOT_RUN，值/分子/分母/run_id为空。零分母须NOT_APPLICABLE/null，不能写100%；未完成恢复保右删失/缺失。当前完整指标分母与oracle尚未冻结，不能拿本表当预登记成功。确定性净模拟收益仅在同样安全授权约束下比较，不能以放松安全换收益。合成B0 actor不是真人耗时。

## 3. 可引用的工程定向运行

| 原件 | 原结果 | 可引用范围和限制 |
|---|---|---|
| E01 固定付款AUTO/ASK | PASSED，2个明确parameter节点，exit0，wrapper851.602285s | 相关scope稳定true、全源false；pytest848.02s为整批时长，非单动作或benchmark。原四银行腿/同key协调/只读尾部以日志为准 |
| E02 审计/问答/对账三节点 | PASSED，exit0，wrapper3734.677602s | 607节点在整批中通过；没有单项时延。并非全部九类对账或最终浏览器 |
| E03 整组资产节点 | 原PASSED，2826.45s | DIAGNOSTIC_WITH_RELATED_SOURCE_CHANGE：运行中相关保护源变化；原状态不改，但不充当前稳定验收 |
| E04 GLOBAL新批 | FAILED，exit1，wrapper10.730693s | 原日志/source保留；后续未执行节点不补PASS，独立actual-v2新源不等实际验收 |

环境、command、HEAD、source.before/after、wall_seconds与log SHA来自所列原manifest；具体数值不得扩成产品收益。HEAD相同也不表示dirty源码相同，必须读该run的实际源字节和scope。

## 4. 历史W0性能，不能外推当前

W0每路径n=1，同输入原结果复核。short API0.385204→0.327347秒；long API101.635072→19.960296秒；expanded API112.629382→138.810674秒变慢。长链Python peak249640231→280724062字节，扩大353670321→392620660字节亦增加。API、服务、profile、SQL和工作集是不同计量层；工作集含夹具导入，不能计算原生请求内省内存结论。

当前Full性能NOT_MEASURED，没有P95/P99、重复置信区间、当前SLA或总体优化倍数。详见S35完整原测量及方法限制，本报告不重标旧FAILED。

## 5. 失败样本和当前缺口

E03前置prepare500是历史fixture clock早于实际OPEN opened_at，0013守卫拒绝；新candidate只用本节点actual server UTC，未改trigger/hash/正式历史。当前源稳定的资产整链仍待。E04真实失败与旧204容量/来源误引用导致UNKNOWN保留，不能将UNKNOWN显示globalComplete。Root报告的新307preview可读但prepare完整引用核验拒绝修复中，当前无银行执行成功；这项报告状态只是负责人最新交接，未作为原生效果数据。

102 Full图v2已登记35业务表，31不同纯/HTTP风险检查是工具/合同，actualPG NOT_RUN；查ID不代表金融成功。新604/307/105实际集中节点、Full50族七机制八消融、全部并发/强杀/重启、当前性能和原最终全量未完成。

## 6. 研究与结论

原计划拟18—24成年参与者、合成账户、全量确认/静态预算/P任务比较，仅探索性。现在真人0、records0、NOT_STARTED。无访谈、问卷评分、理解正确率或信任校准效果。当前结论仅为已实现模块与所列定向证据范围；不作收益、优势、显著性、普遍安全或总体青年推断。

来源：S03（802—941、1412—1430）、S04、S06、S32、S34、S35、S39、S40及E01—E04。自动材料内容/效果门尚未通过，人工一致性审阅NOT_RUN。
