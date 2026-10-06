# FULL-707 · 独立分项反事实差量

当前：独立后台与分项 Web 消费者已交付；原编号 **PENDING**。来源为原完整版计划1388—1390、需求追踪表102行、`FULL-707.md` 与 `current-functional-gap-inventory-20261006.md`。旧707现金/应急/期限服务、页面、算法和原哈希均未改。

新增 `/api/v1/scenario-risk-review/context` 与 POST `/compare`，只在无待写入状态的 REPEATABLE READ / READ ONLY 事务读取；Root负责注册与该路径的事务入口。请求只能包含实际 epoch/source/engine摘要及一个有界 hypothesis。没有用户、银行事实、到账、时钟、结果、回执、授权、reset或执行输入；客户端不能替换服务端结果。未知原件不能因乐观假设变成有效证明。

| 分项 | 实际可改假设 | 原计算消费者与严格边界 |
|---|---|---|
| BILL | 原账单剩余应付0—10000000分、相对原到期日±365天 | 原 `compute_boundary`；原paid/statement/BANK证据/原逾期记录不改。假设剩余0不是已付款。拒绝假设到期早于原statement。|
| GOAL | 当前实际原goal_saving版本月度min/target/max各0—10000000分且有序、期限偏移±365天 | 原 `PolicyChangeAssumption/compute_policy_change_boundary`；完整目标原件另可查看，但这里只消费原月度/期限，不称部分完成/延期成本/联合最优等全部属性已参与。原目标产权与本金日期不改。|
| FULL_POLICY | 当前完整策略版本的完整JSON候选 | 原 `preview_full_policy_financial_impact`，Dated/Periodic按真实支持报告；其它未支持、来源未知或历史无法证明原样UNKNOWN/null，不造新曲线或新确认。|
| PRODUCT | 当前不可变目录原版本risk0—5、lock/redemption0—365天、minimum0—10000000分、early_loss0—10000bps | 原 `plan_full_assets` 复算当前确认规划scope；风险、锁期、赎回、最低额参与原consumer。early_loss原consumer未消费，另显示所选本金×声明bps向上取整假设界，实际fee/loss=null，非报价。原产品/持仓不改。|

一次只比较一个分项，不声称联合最优。账单/目标结果将同请求原Full年度曲线对MVP曲线的逐阶段附加保护/支出保留到候选MVP曲线上，完整1098点校验（初始日+365日×三阶段）。只允许Full增加保护/支出，任何减少原MVP保护或多出现金的表示拒绝；当前来源账户风险继续保LIQUIDITY_RISK/safe0。该叠加是条件账户总额，不是完整未来逐账户借记调度。

产品规划沿原MVP365优化器；响应明确 `full_protection_consumed_by_optimizer=false`。原Full保护卡独立展示，不把这一规划当银行购买或整个Full可执行组合。收益是原模拟固定规则下的条件数学结果，不是测得收益或优势；手续费/实际损失、提前支取报价、资金消费者和当前资金权力均未产生。

新原件fingerprint独立协议 `scenario-risk-source-v1`，绑定实际原财务来源、全部原Evidence内容/hash/status/时间、Full当前版本、FullModel和实际目录；不冒充旧factDigest。仅当前完整范围最多10000Evidence、200Full策略、100目录产品，超限非零，不截断分母。每次读取验原银行/审计/来源；同次计算前后重核明确列出的引擎文件SHA。Root当前Full季节保护v3变动会产生真实新engineHash，用户须刷新重审；没有跨请求授权缓存。

## 源码与当前检查

后台五源精确副本与SHA：`.runtime/FULL-707-risk-review/backend-final-20261006T020902Z/manifest.json`，manifest SHA `60be0951657e42f030c23001ab6c66b02d751030717cccce9b1315fd119aa085`。

- `scenario-risk-review-direct-first-20261006T020309Z-31d804cf` 原9FAIL/16PASS保留：新synthetic helper漏实际AuditCard必需字段/goal month原事实，且小额收益取整导致测试误期望满额买入，未改旧金融算法。
- `scenario-risk-review-fixture-repaired-20261006T020419Z-0f5c1d66` 原1FAIL/24PASS保留；独立整数推导原least-turnover tie break后，必要失败节点 `scenario-risk-review-turnover-oracle-repaired-20261006T020520Z-59bb7437` 1PASS/2.41s。
- 新实际FastAPI JSON注入三负例 `scenario-risk-review-http-negative-direct-20261006T020619Z-61093fa3` 3PASS/2.55s；query/嵌套伪回执/bool金额在capture前422。合法JSON UUID正向已经调用新service和原引擎，同原请求重放一致；只用明确synthetic读源doubles，不冒PG。
- strict首测试doubles类型3诊断与后HTTP字典推断2诊断均保留；仅test类型adapter窄修，最后 `scenario-risk-review-five-source-types-repaired-20261006T020834Z-1e3181ad` strict5 PASS。前四源 Ruff `2e0ed8f8` PASS，新PG文件静态待下条追加。
- 新实际PG候选 `test_scenario_risk_review_integration.py::test_actual_bill_hypothesis_replays_preserves_all_physical_originals_and_unknown_source` 仅collection：`scenario-risk-review-real-pg-collection-20261006T020801Z-883b1770` 1节点/4.37s。**NOT_RUN**。候选由Root在owned临时库运行，真正原GET与同body复算、全物理表含metadata零写、非法结果/其他owner实体拒绝、原cash篡改后UNKNOWN/null与全表不再变。正式历史不动。

后台最终五源 Ruff `scenario-risk-review-final-five-static-20261006T020954Z-38fcefc3` exit0。Root已经独立注册真实路由、RRRO及生成Schema；不属于本包修改shared源码。

旧失败日志/source原件完整保留。纯风险检查、真实HTTP DTO解析、收集1PG节点分别是不同证据层，不以测试夹具当实测金融结果。Main/deps/App/generated/旧707以及203十四源均非本差量修改范围。

## 可运行 Web 消费者

新 `ScenarioRiskReviewPage` 只有可选 `mutationBlocked:boolean` props，建议入口 `#simulation-risk` /「分项反事实评估」，由Root接共享导航。当前页面主动GET原上下文，再由用户选择一个实际原对象、编辑有界假设并显式POST只读比较。没有客户端金融计算、额外原Session/Trace查询、策略确认、资金提交、银行模拟写或reset。原完整HTTP响应保留原JSON文本；用户修改假设即隐藏旧结果，原epoch/source/engine变化需重新读取审阅。重放仅用户显式点击同原完整body，没有自动POST或重试。只读POST沿已有HTTP短时write-flight门，跨族阻挡时自己的GET仍可读取。

reader使用实际生成的ScenarioRisk aliases，复用原年度/FullModel/105金融预览解析器与原canonical SHA（包含Seasonal原quantile typed float规则，金额始终整数）。有限核对原owner/epoch/版本/body/hash、全部1098阶段与整数金额恒等、原paid/逾期、目录各产品版本与terms、真实完整分母。新比较hash核其实际协议，未把新source/hash当旧factDigest，也未在客户端重造银行或审计引擎。

账单假设剩余0明确不是已付款；GOAL仅月度与期限假设，原产权与其它Full属性仍可查看，未被消费者使用的属性明确未覆盖。FULL_POLICY展示原消费者PROJECTED或UNKNOWN/null；原今天/逾期义务保留。PRODUCT展示原规划/候选拒绝原因/所选条件本金和假设loss上界，实际fee/loss=null、early_loss/full保护未被优化器消费均明确说明。无来源时BLOCKED/UNKNOWN，不补零或假成功。

- 新五个TS/TSX文件 ESLint `scenario-risk-web-owned-lint-first-20261006T022438Z-fe1544ae` exit0。
- 首次 `scenario-risk-web-direct-first-20261006T022443Z-b49d2d95` 原2FAIL/36PASS保留。合成目录只有1条、原optimizer的2条候选完整返回，严格reader因此拒绝；原六源/夹具副本在 `.runtime/FULL-707-risk-review/source-before-web-catalog-fixture-repair/`。仅补夹具完整目录与原生成器输出，未弱化生产门。
- 最终相关两模块 `scenario-risk-web-complete-catalogue-fixture-repaired-20261006T022550Z-361a4e07` **38PASS/7.56s**，包括readonly边界、未来收入0、阶段缺失/金额/哈希/owner/source篡改、原paid、PRODUCT假设界、FULL UNKNOWN、跨族阻POST/GET可读、原body重放与不自动写。
- `scenario-risk-review-fixture.json`/TS明确 **SYNTHETIC_SOURCE_DOUBLE_HTTP_SHAPE_NOT_PRODUCT_PROOF**。BILL与产品条款/原优化器输入来自新后台纯helper；其余未知形状为显式HTTP合成夹具。它们不是实验、PG、浏览器或银行成功证据。完整产品分母与1098点均保留。新fixture生成过程原字节/先前时点对齐前版本保存在本包 `.runtime`，没有覆盖旧产品实证。
- 整体Web类型由Root在相关模块冻结后统一，当前本包类型终态待Root返回；不因Vitest/ESLint通过声称类型完成。

Root整体类型 `907365bc` 原 child2 / SOURCE_CHANGED 保留：实际三诊断分别为ownership callback收窄、test discriminated configuration、可选reasons数组。前两项新TS局部收窄已由本包完成，变化两节点 `scenario-risk-web-narrow-typed-local-check-20261006T022810Z-45d3f228` 2PASS/29SKIP/4.27s、两源lint `cf209ff0` exit0；第三项仅渲染 `(full.reasons ?? [])`，reader仍强制实际数组。对应页面节点 `scenario-risk-web-optional-reasons-ui-check-20261006T022904Z-4d606282` 1PASS/6SKIP/4.98s、该源lint `11dbdf3d` exit0。原38结果不改，未把skip当通过，也未为类型注解重复整套测试。六个新Web源现在HOLD供Root唯一最终类型检查。

## 仍缺

真实PG、真实浏览器/手机、当前源码冻结完整重放与原权限验收均NOT_RUN。目标额外Full属性及多期联合求解、全部12模板金融候选、产品Full保护下完整组合、收益/费损报价与实际执行并未因此补齐。共享宿主接线与最后整体类型由Root完成；没有实验效果/真人研究/性能数据，不关闭原编号。
