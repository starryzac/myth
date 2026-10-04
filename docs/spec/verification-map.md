# 日常最小验证集

本表按实际影响选取，不能替代初版/完整版两个全量验收节点。快检查先过再运行依赖的昂贵检查；独立快检查可并行。失败只重跑失败节点及受影响范围，已经通过且行为未变的结果复用。

| 修改范围 | 先跑的快检查 | 必须覆盖的实际使用链路 |
| --- | --- | --- |
| 金额格式、标签、布局 | Web typecheck、lint、money/App 对应交互测试 | 修改页面的真实浏览器检查；无金融行为变更时复用资金链结果 |
| 首页 DTO/只读聚合 | API Ruff/mypy、dashboard read contracts | dashboard API 的同租户、未知来源、RR/READ ONLY、零写；改变金融字段再验证相应黄金阶段 |
| 共享资金上下文/自主分级 | context/clone、相关域测试 | execution_context；goal→asset 自动链及策略失效，按变更选 transfer/payment/redeem |
| 收入账本/外部银行事实 | income/external domain、posting codec | 工资→目标/申购；消费保护目标与预留；首键重放及 UNKNOWN 只补投影；新 SQL 原件约束 |
| 审计版本/原件 codec | audit domain、v1/v2 codec | 实际最终请求/回执锚定，原 OPEN/SEALED 历史与 checkpoint 保持，相关篡改拒绝 |
| 初始化或复位数据 | 固定生成器/源账本纯测试、类型 | fresh native 源与黄金资金链；原 reset key 重放零写；旧来源场景明确选 legacy 初态 |
| 数据库迁移 | 迁移 Ruff/mypy，模型定义 | 空库往返/metadata；真实旧版本历史升级、原行/列/hash 保留；新约束的普通 DML 回滚 |
| 策略生命周期/配置预览 | 编译、版本、边界纯测试 | 修改/暂停/到期与旧动作权限，资源占用及 UNKNOWN 保留；预览 RR/READ ONLY 零写 |
| 仅文档或格式，行为未变 | 对应格式/链接/AST 等必要检查 | 复用既有结果，不重跑金融链或全量 |

当前 MVP-401 的具体节点见相关 `test_dashboard_*`、`test_external_bank_*`、`test_execution_context.py`、`test_autonomy_service.py`。入口/API 变化时同步实际生成的 OpenAPI/TypeScript 合同，再做 Web 类型检查。

MVP-402 的具体选择：`policy_change_types/domain.boundary` 用 `test_policy_change_boundary.py` 和原边界域测试；共享 `financial_read` 与预览用 `test_policy_change_preview_api.py`、`test_dashboard_api.py`；月度来源解析用 `test_boundary_service.py` 的真实月度正反节点；策略失效资源与 UNKNOWN 用 `test_policy_execution_lifecycle.py`；原编译只读读取用 `test_policy_compilation_read.py`。Web 共享策略表单变化覆盖 `policy-form.test.ts`、`PolicyCenterPage.test.tsx`、`GoalsPage.test.tsx`，类型检查后再做对应真实页面验证。仅示例和日期说明变化复用既有金融集成结果。

测试夹具变化也按实际依赖判断：`goal_fixture` 被收入、目标配置、资产配置、恢复与边界测试复用。可信 setup 新增或更新来源后，要在 setup 完成处刷新实际完整敞口；有意破坏来源后不能刷新来掩盖失败。局部两个节点通过不能宣称其他消费者已通过。原始错误日志与原断言保留。

每批记录命令、退出码、原始日志与对应代码版本；同一工作树版本可以复用一份源码指纹。完整证据索引在任务关闭或版本验收整理。慢链路按建库、迁移、seed、资金操作、审计核验和读取计时；嵌套函数的累计时间不能相加。隔离数据库并行需先观察单次资源与稳定性，再限制并发；单条资金链始终保持原顺序。

零Goal建立或income epoch继承变更：test_goal_creation_income_epoch.py、test_execution_goal_creation.py、test_policy_execution_lifecycle.py、未来Goal变更节点及test_pure_epoch_successor_keeps_prepared_income_identity[False]；检查旧收入资格、非空预留身份、UNKNOWN、跨月不补零、原经济内容及重复建立零写。只改变时点的合法继承也需覆盖这些实际使用链路。
