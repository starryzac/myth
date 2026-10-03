# PostgreSQL 数据层（MVP-101）

日期：2026-10-04（Asia/Shanghai）。依据：初版计划第 7 节、MVP-101，以及完整计划第 5、9、10、18 节的后续扩展需求。

这里交付数据模型和迁移；不代表策略授权、资金引擎、记账执行、审计防篡改或业务 API 已完成。

## 连接与迁移

- SQLAlchemy 2 `Mapped` 映射，PostgreSQL 16，驱动 `psycopg`。
- `app.db.settings.DatabaseSettings` 从仓库根 `.env` 读取 `DATABASE_URL`、`SIMULATION_MODE`；进程环境变量优先。根路径由模块位置计算，不依赖命令工作目录。
- `SIMULATION_MODE=false` 被拒绝；不支持 SQLite 或其他数据库驱动。配置不含真实银行凭据。
- `create_database_engine()` 延迟连接，设置 PostgreSQL session timezone 为 UTC，启用连接存活检查；`database_session(engine)` 是提交成功或异常回滚的明确事务边界。
- 仓库根执行 `uv run --frozen alembic upgrade head`，或项目统一入口 `make migrate` / `.\make.cmd migrate`。
- 初始修订 `0001_mvp_tables` 保存明确 DDL，不在执行迁移时导入当前模型后 `create_all()`。未来更改必须新增迁移。
- 降级仅允许名称精确匹配 `bf_test_[0-9a-f]{32}` 的临时测试库；演示库、`postgres`、任意普通库和离线降级均拒绝。演示重置应通过后续种子任务完成，不使用降级删库。

## 数值、时间和所有权

所有 `*_cents` 字段都存储为 PostgreSQL `BIGINT`。应用绑定层拒绝 `float`、`Decimal` 和 `bool`；余额、账单、目标、动作、回执等分别设非负或正数约束。交易以正金额加 `CREDIT / DEBIT` 表示方向。`decision_constraints.available_cents` 允许负数，以保存真实的测算短缺。数据库整数范围为有符号 64 位，不允许溢出。

收益及提前支取损失率采用整数基点 `*_bps`，1 基点等于万分之一；后续确定性算法负责舍入规则。配置 JSONB 的金额与 DSL 语义由后续领域验证器检查，不能仅因写入 JSONB 就认为已获得授权。

所有时间戳列为 `TIMESTAMP WITH TIME ZONE`。`UTCDateTime` 拒绝没有时区的 Python `datetime`，写入和读取统一换算 UTC。通过原始 SQL 写入的客户端也必须提供带偏移时间；PostgreSQL 自身会把无偏移字面量按会话时区解释，因此这里不声称数据库能识别原始 SQL 是否丢失了时区。账单日、到期日等日历日期保留为 `DATE`，按用户时区解释。

租户表都有 `user_id → users.id`、唯一 `(id, user_id)`。账户、证据、策略版本、目标、决策、动作和回执间使用复合外键，防止引用另一用户的数据。引用删除为 `RESTRICT`，避免级联删除追溯记录。该约束不替代 API 身份认证或查询过滤，也不是 PostgreSQL RLS。

## 16 张表的职责与字段

所有表含 UUID `id` 与 UTC `created_at`。除 `users` 和全局合成产品目录 `asset_products` 外，所有表均含 `user_id`。

| 表 | 主要字段与约束 |
|---|---|
| `users` | `external_ref` 唯一、`display_name`、`timezone`；`is_simulated` 必须为真。 |
| `accounts` | `external_ref/name/account_type`，仅工行 CNY；`balance_cents >= 0`、`observed_at`；用户内外部标识唯一。类型包括 CASH、GOAL、CREDIT_CARD、CASH_MANAGEMENT、FIXED_DEPOSIT。 |
| `transactions` | 账户、可选证据、来源标识、正金额、方向、记账后余额、类别、交易对手引用、一次性标记、类别确认状态、发生/观察时间；`(account_id, source_ref)` 唯一。 |
| `credit_card_bills` | 账户、证据、来源标识、账单/到期日、总额/最低应还/已还、状态；金额范围和日期先后约束，账户内来源唯一。 |
| `asset_products` | 产品代码与正版本号唯一，类别、风险级别、本金波动、最低起投、锁定/赎回天数、收益/损失基点、JSONB 到期及提前支取规则、自动买卖标志、生效区间。它是模拟属性目录，不直接授予用户操作权限。 |
| `asset_positions` | 账户、准确产品版本记录、可选目标和策略版本、本金及累计模拟收益、购买/到期/可用时间、持仓状态；本金和收益非负。 |
| `evidence_items` | 五级证据、来源、JSONB 内容、SHA-256 字符串、有效区间、观察时间、前一证据、状态；冲突和未知可明确保存。 |
| `policies` | 名称、策略类型、生命周期状态、更新时间；状态值受限，但状态迁移规则留待 MVP-103。 |
| `policy_versions` | 所属策略、版本号、JSONB 配置/确认记录/影响分析、摘要、确认与生效时间、修改原因、证据 ID 数组、前后内容哈希；策略内版本号唯一。配置必须为 JSON 对象。 |
| `goals` | 策略及准确版本、可选目标账户、金额/日期/归属额、月度 min/target/max、重要程度、最低保护、可减少/可延期/跨目标许可、资产策略；范围有序；版本必须属于同一策略与用户。 |
| `policy_proposals` | 发现/编译来源、原文、编译器版本、JSONB 候选、证据数组、候选状态、确认后的策略引用、用户内幂等键。候选保存不产生授权。 |
| `decision_runs` | 幂等键、触发原因、算法版本、计算时点、JSONB 输入快照及哈希、策略版本/证据 ID 数组、结果、状态与结束时间。 |
| `decision_constraints` | 决策、可选策略版本、约束标识、硬软标记、满足/未知、需求/可用分值、日期、JSONB 计算过程、原因码；决策内约束标识唯一。 |
| `action_plans` | 决策、准确策略版本、来源/目标账户、目标、产品/持仓、动作类型、正金额、自主等级、状态、用户内幂等键、JSONB 请求与哈希、授权及过期时间。默认等级 BLOCKED。 |
| `action_receipts` | 动作、尝试序号、外部模拟回执标识、成功/失败/未知、实际金额/费用/损失、JSONB 响应、发生与对账时间；动作内尝试序号及用户内回执标识唯一。 |
| `audit_events` | 用户内连续序号键、事件/聚合类型、聚合/关联/原因 ID、可选决策/动作/回执外键、幂等键、载荷版本与 JSONB、前后哈希、发生/观察时间；用户内序号/幂等键/事件哈希唯一。 |

高频索引覆盖用户、交易账户与发生时间、证据来源、决策计算时间、动作状态和审计时间。SQL 唯一约束提供索引支持，但幂等副作用、事务内账本守恒、权限重验均须由后续执行服务实现。

## 现金、持仓和目标归属的统计口径

`accounts.balance_cents` 表示该账户已记账、尚未放入产品持仓的现金；现金管理和定存账户类型提供容器分类，投资本金与收益由 `asset_positions` 单独保存。不能把同一本金既留在现金余额又计入持仓。申购与赎回的原子记账在 MVP-301 实现。

未赎回资产不是可支付现金。总资产展示未来可采用现金加未结清持仓的口径，但必须按持仓状态避免赎回后重复计数。目标的 `allocated_cents` 是资金归属记录，不能再次加到现金和持仓之上。信用卡应还负债由账单保存，不通过允许现金余额为负来表达。

策略没有可随意指向其他版本的“当前版本”字段：`(policy_id, version_number)` 给出版本顺序，后续生命周期服务必须结合策略状态、确认记录和有效期选择可用版本；动作和目标引用准确版本。JSONB 中的多证据、多版本 ID 数组为重放快照，当前不逐元素施加外键，后续领域层必须验证存在性与所有权。

## 实测与限制

`apps/api/app/tests/test_migrations.py` 在真实 PostgreSQL 16 上创建随机 `bf_test_<uuid>` 库并在 finally 清理，绝不回滚演示库。测试包括：

- 16 表数量、所有金额 BIGINT、全部时间戳带时区、全部命名 CHECK 到位；
- 两轮 upgrade/downgrade，最后再 upgrade；每轮 Alembic `compare_metadata` 检查类型和默认值，完整结构快照比较列、CHECK、外键、唯一约束和索引；
- 负余额、跨用户策略版本引用、无时区时间、浮点金额、重复银行来源、重复动作幂等键、重复策略版本、非对象 DSL 配置被拒绝；
- 默认配置读取、非模拟/非 PostgreSQL 拒绝、非测试库名称及离线降级拒绝。

MVP-101 红灯日志为 `docs/progress/evidence/MVP-101-red.txt`；最终本任务测试日志为 `MVP-101-green.txt`，11 项通过。初轮 7 项通过的 `MVP-101-green-initial.txt` 含缓存目录 ACL 警告，最终运行已消除。项目整体验收由主代理运行 `make check` 后记录。

本任务没有实现策略版本不可变触发器、审计只追加控制/链验证、真实授权认证、业务 API、种子数据或银行接入。不能据数据库结构宣称这些后续任务完成。
