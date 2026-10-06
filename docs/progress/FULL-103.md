# FULL-103：完整有限策略 DSL

实施日期：2026-10-05。实施依据为原完整计划第 3.3 节、6.1 节、9.1 节、10.1 节及 FULL-103；排程采用用户明确授权的[功能优先修订](../spec/execution-amendment-v2-functional-priority.md)。本批交付可运行的确定性配置校验与只读路由，未修改原需求关闭状态。

功能状态：`IMPLEMENTED_ACCEPTANCE_PENDING`。原验收状态：`ACCEPTANCE_PENDING`。root 已将路由注册至主应用并成功生成共享 OpenAPI/TypeScript 合同；本批路由正负例在独立 FastAPI 应用挂载同一实际路由。没有数据库、迁移、确认、授权或金融执行操作。

## 完成内容和文件

- [完整域配置](../../apps/api/app/domain/full_policy_configuration.py)：固定 12 模板、严格 Pydantic 类型、逐模型 JSON Schema、确定性跨字段校验和 JSON 规范化。
- [只读候选路由](../../apps/api/app/api/v1/policy_templates.py)：模板目录、单模板 Schema 和候选校验。
- [域正负例](../../apps/api/app/tests/test_full_policy_configuration.py)及[路由正负例](../../apps/api/app/tests/test_policy_templates_api.py)：逐模板有效/无效输入、未知字段、日期/金额/范围/权限形状与旧合同兼容。
- 原 `test_experiment_error_stack.py` 仅把 `REPOSITORY_ROOT` 改为从其实际定义处 `app.db.settings` 直接导入，消除已知类型错误；原文件归档，未重跑实验工具套件，未修改 provider。

## 版本与旧合同

12 个 canonical 名称与原计划一致；外部字段为 `template_name`。配置内部继续使用 `type`。版本必须明确为 `MVP_V1` 或 `FULL_V1`，未知版本或该版本不支持的模板拒绝，不回退。

`MVP_V1` 的五种配置使用原模型类、原 `validate_configuration` 及原 `configuration_hash`，Schema 与原类逐项完全相同。原 `policy_configuration.py` SHA256 为 `e3645cd3faa23921b765bc7e952d4953489d508eb39e88065632e24d49c94657`；本批前后字节一致。新增候选接口面向 JSON，因此拒绝非 JSON Python 对象、非字符串 key、NaN/Infinity。

| canonical 名称 | MVP_V1 原 type | FULL_V1 type |
| --- | --- | --- |
| RecurringObligationPolicy | recurring_obligation | recurring_obligation |
| LivingReservePolicy | living_reserve | living_reserve |
| EmergencyBufferPolicy | emergency_buffer | emergency_buffer |
| DatedExpensePolicy | 不支持 | dated_expense |
| LongTermGoalPolicy | goal_saving | long_term_goal |
| PeriodicTransferPolicy | 不支持 | periodic_transfer |
| AssetAuthorizationPolicy | asset_authorization | asset_authorization |
| RecoveryPolicy | 不支持 | recovery |
| GoalAllocationPolicy | 不支持 | goal_allocation |
| CrossGoalReallocationPolicy | 不支持 | cross_goal_reallocation |
| SeasonalReservePolicy | 不支持 | seasonal_reserve |
| InterventionPolicy | 不支持 | intervention |

FULL 的 Recurring/Living/Emergency 直接使用原类。FULL 资产使用独立模型，避免修改或扩大旧模型的类别；FULL 目标使用独立配置，不往旧 `goal_saving` 添加字段，不自动转换旧配置或重算历史哈希。

## 12 模板字段合同

共同字段为 `name`、`valid_from`、`valid_until`；日期必须是严格 `YYYY-MM-DD`，有效窗口有序。所有金额为非布尔的整数分，范围 `0..9223372036854775807`，需要正金额的字段额外要求 `>0`。配置和所有对象型嵌套配置均拒绝未知字段。UUID 引用只校验格式，不假称其已存在或已授权。

| 模板 | 最小计划字段及确定性约束 |
| --- | --- |
| RecurringObligationPolicy | 原 payee_id、amount_rule(exact/range/bill_balance)、due_day(1..31)、prepare_days_before、auto_execute、priority；原范围、类型与日期校验完整保留。 |
| LivingReservePolicy | 原 horizon_days、method(rolling_window_quantile/lookback_days/quantile/categories/exclude_one_off)、extra_buffer_cents、reconfirm_on_boundary_crossing；horizon 不超过 lookback，分位数有限且在 `(0,1]`。 |
| EmergencyBufferPolicy | 原 amount_cents；不从候选推断账户余额。 |
| DatedExpensePolicy | window(start/end)、amount(min/target/max)、priority、must_not_reduce_policy_ids；窗口有序且位于明确有效窗口内，金额 min≤target≤max，priority.minimum≤amount.max，保护引用去重。 |
| LongTermGoalPolicy | target_cents、deadline、monthly_contribution(min/target/max)、importance(0..100)、minimum_guarantee_cents、allow_partial、allow_deferral、deferral_cost_cents_per_day、asset_policy_id、cross_goal_reallocation_allowed、cross_goal_reallocation_policy_id；minimum≤target，月范围有序，deadline 在明确有效窗口内；无延期时成本必须为零；跨目标开关与明确策略引用同时存在。 |
| PeriodicTransferPolicy | source_account_id、payee_id、amount_rule(exact/range)、due_day、prepare_days_before、single_action_cap_cents、auto_execute；拒绝 bill/model amount，金额上界不超过单次上限。固定关系是否已经确认须由后续服务核验。 |
| AssetAuthorizationPolicy | 原 scope/goal_id、allowed_asset_classes、总额/单额、赎回延迟/锁定期、风险级别、恢复/提前支取开关；scope 与 goal_id 一致、单额≤总额、类别唯一。FULL 类别仅 CASH、CASH_MGMT_T0/T1、FIXED_DEPOSIT_7D/30D/90D、LOW_RISK_TERM；MVP 原 FIXED_DEPOSIT 保留在 MVP_V1。 |
| RecoveryPolicy | scope/goal_id、asset_policy_id、triggers、single_action_cap_cents、max_redemption_delay_days、allow_auto_recovery_without_penalty、max_fee_cents、max_loss_cents；scope 一致，触发仅 BOUNDARY_SHRINK/AUTHORIZATION_REVOKED/POLICY_EXPIRED/LIQUIDITY_SHORTFALL 且唯一；持续自动恢复的 fee/loss 只能为零，有损动作须后续人工流程。 |
| GoalAllocationPolicy | 至少两个唯一 goal_ids、max_single_allocation_cents；固定 method=lexicographic_v1、funds_scope=NEW_UNASSIGNED_SAFE_FUNDS。不能改为既有目标资金或未来收入；实际安全资金与算法由 FULL-302 服务负责。 |
| CrossGoalReallocationPolicy | enabled 默认 false、source_goal_ids、emergency_conditions、destination_scope=PROTECTED_CASH、single_action_cap_cents、total_cap_cents；关闭时额度为零且无触发；启用要求非空来源目标/紧急条件/明确起止日期，`0<single≤total`；只允许义务/生活/应急短缺，禁止普通收益再分配。 |
| SeasonalReservePolicy | holiday_code、window、lookback_days、minimum_historical_windows、quantile、essential_categories、adjustment_cap_cents；窗口有序，lookback 覆盖窗口长度，历史窗口数为正，类别唯一；advice_only 和 requires_confirmation 必须 true，历史建议不能自动扩大资金边界。 |
| InterventionPolicy | 有限 must_ask_on、deduplicate_by_boundary_event、minimum_reask_interval_seconds、safety_events_bypass_throttle、silent_when_action_set_unchanged；强制保留新收款人、越权、新类别、新损失、新风险、偏好修改、无预授权冲突修复；安全事件不受节流、动作集合未变静默及事件去重均不可关闭。 |

目标的 `current_owned_cents`、`effective_policy_version_id`、状态、owner、确认和 grant 均不是候选字段，输入会拒绝。当前已归属金额与有效版本须来自实际 Goal/账本/生命周期。用户开关只是待审配置，校验成功不产生持续权限。

原第 6.1 节示例的 `template`、`evidence_level`、`requires_confirmation` 属于编译候选封套；本接口采用明确的 `template_name`/`dsl_version`/`configuration` 封套。示例中的符号策略名称如 `rent_policy` 不能冒充 UUID；实际引用解析属于 FULL-106/证据服务，不能由校验器造 ID。

JSON Schema 保留 Pydantic 的结构、枚举、范围、必填和 `additionalProperties=false`。标准 JSON Schema 不表达两个字段值的大小比较；响应明确 `cross_field_validation_required=true`，关系约束由同一实际域模型及 `/validate` 执行，不声称只跑 Schema 即完成跨字段验证。

## 可调用接口

- `GET /api/v1/policy-templates`：按原顺序返回 12 模板、FULL type、五个 MVP type 与可用版本。
- `GET /api/v1/policy-templates/{template_name}/schema?dsl_version=FULL_V1`：返回精确模型 Schema、其 canonical SHA256、版本及跨字段验证要求。可显式查询五模板的 `MVP_V1` Schema。
- `POST /api/v1/policy-templates/validate`：返回显式默认值的 `normalized_configuration` 及其原 canonical `configuration_hash`。所有成功响应均 `candidate_only=true`、`authority_granted=false`；校验响应还标记 `reference_validation_pending=true`。无效候选返回 422，域错误不回显原用户值。

```json
{
  "template_name": "LongTermGoalPolicy",
  "dsl_version": "FULL_V1",
  "configuration": {
    "type": "long_term_goal",
    "name": "买车",
    "target_cents": 3000000,
    "deadline": "2027-10-01",
    "monthly_contribution": {
      "min_cents": 180000,
      "target_cents": 200000,
      "max_cents": 250000
    },
    "minimum_guarantee_cents": 100000,
    "allow_partial": true,
    "allow_deferral": false
  }
}
```

root 注册接缝：在 `app.main` 导入 `from app.api.v1.policy_templates import router as policy_template_router` 并在 `create_app()` 调用 `api.include_router(policy_template_router)`。本分工没有修改 main.py、共享 OpenAPI、模型、迁移或旧生命周期。域入口为 `validate_full_configuration(template_name, configuration, version="FULL_V1")`、`template_schema(template_name, version)`；后续服务应在配置校验之后独立读取 owner、引用、证据、有效版本及确认，再进行授权判定。

## 已运行验证

原始命令输出保存在 `.runtime/FULL-103-candidate-schema/`。测试均为 Schema/候选 API 能力，未执行数据库、浏览器、金融或全量。

```powershell
uv run --frozen python -m pytest apps/api/app/tests/test_full_policy_configuration.py apps/api/app/tests/test_policy_templates_api.py apps/api/app/tests/test_policy_configuration.py -q -p no:cacheprovider
uv run --frozen mypy --explicit-package-bases apps/api/app/domain/full_policy_configuration.py apps/api/app/api/v1/policy_templates.py apps/api/app/tests/test_full_policy_configuration.py apps/api/app/tests/test_policy_templates_api.py apps/api/app/tests/test_experiment_error_stack.py
uv run --frozen ruff check <上述五文件>
uv run --frozen ruff format --check <上述五文件>
```

- 首轮相关纯/API **381 PASS / 1.90s**，首轮 mypy 的两个错误原日志保留：FULL 资产继承时不兼容可变列表、测试负例变量需显式类型。改为独立 FULL 模型并补注解，没有放宽旧资产类。
- 修正后相关纯/API **381 PASS / 1.86s**；严格 mypy **5 文件 PASS**；Ruff **PASS**；format **5 文件 PASS**。测试运行含原依赖的 TestClient 弃用提示，未为该提示变更依赖。
- 12 个模板均有可运行正例、未知字段负例及相应约束负例；五个 MVP 的 model 身份、Schema、归一化与 hash 对比一致。新旧源码原件和初始格式/type 失败未覆盖。

## 具体未覆盖项及后续依赖

- 主应用路由与共享 OpenAPI/TypeScript 合同已由 root 集成；前端使用流程和完整应用验收尚未完成。
- FULL 新类型/字段尚未接确认、ACTIVE、授权与执行；当前 MVP 执行引擎不能因新候选校验成功而接受新增资产类别或目标权限。
- UUID/payee 存在性、同 owner、引用策略种类、当前版本、有效确认及来源证据尚未进行 SQL 核验；引用格式正确不代表事实或授权有效。
- FULL-301 的真实目标状态、FULL-302 词典序分配、FULL-304 紧急回拨执行、FULL-108 历史建议、FULL-507 去重状态机由其后续服务承担；本批仅提供对应有限配置。
- 未取得完整版本全量验收、性能实测或真人研究。原 FULL-103 关闭状态不因本批纯测试自动改变。

下一前置条件为采用此合同接 FULL 生命周期/编译和目标服务，保留原 MVP_V1 及历史配置哈希。
