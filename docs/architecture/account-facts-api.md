# MVP 账户事实查询

四个端点来自初版计划第 9 节，数据均为本地合成模拟。它们提供事实读取，资金边界、授权和自主金额由后续确定性引擎实现。

| GET 端点 | 返回内容 |
|---|---|
| `/api/v1/accounts/summary` | 账户、账单、现金余额合计、已知未赎回本金、UNKNOWN 本金、账单未付额 |
| `/api/v1/transactions` | 交易与来源证据 ID；支持 `account_id`、`category`、`limit`（1–200）、`offset` |
| `/api/v1/products` | 全局模拟产品目录、版本、期限、收益基点、损失规则及目录允许操作 |
| `/api/v1/positions` | 当前演示用户持仓、产品 ID、目标与策略版本引用、时点及状态，包括历史已赎回记录 |

所有响应包含 `simulation: true`；所有金额为整数分。产品的目录允许操作不构成用户授权。`BANK_CONFIRMED` 证据仅表示模拟事实层确认，未连接真实工行。确认的支出类别须来自单独的模拟用户确认，不能从银行来源自动推导。

## 余额与观察时间

账户 `balance_cents` 是未投资现金；产品本金仅计入持仓。现金合计排除信用卡账户，信用卡负债按全部账单的 `total_cents - paid_cents` 单列，不能以最低还款额替代总欠款。目标账户现金包含在现金总量中，但不据此认定可以挪用。HELD、REDEEMING、MATURED 的本金单列；UNKNOWN 单独列出；REDEEMED 不计入在持本金。持仓金额不是当前到账现金。

汇总查询使用 PostgreSQL REPEATABLE READ；同一响应的多次 SELECT 读取同一个数据库快照。各账户事实的观察时间可能不同，因此返回 `oldest_account_observed_at`、`latest_account_observed_at` 和逐账户 `observed_at`，不宣称它们在同一时刻被观察，也不代表完整双时态回放。

## 隔离与分页

MVP 硬绑定固定合成演示用户，核对固定 UUID、external_ref 和 is_simulated；不存在或身份不一致时返回 404。用户不可通过参数切换身份；其他用户的账户、流水、持仓和账单不能进入响应。产品目录是全局共享的版本化定义。身份认证与角色权限仍属于完整版任务。

交易排序为 `occurred_at DESC, id DESC`，`total` 是过滤后的全部数量。静态种子下 offset 分页顺序可复现；并发插入时跨请求不保证没有重复或遗漏，因此该分页不能作为冻结实验数据的导出协议。正式证据导出将在对应任务使用冻结快照。

合同由实际 FastAPI 导出到 `packages/contracts/openapi.json` 与 `schema.d.ts`，`make typecheck` 检查漂移。参数错误使用已统一的脱敏错误模型与请求 ID。
