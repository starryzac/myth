# MVP-203：目标新增资金只读预览

接口：`preview_goal_allocation(session, user_id, goal_id, now) -> GoalAllocationResponse`。结果包含 simulation、user_id、goal_id、as_of、allocation、source_evidence_ids、input_digest、source_issues。HTTP 不接收客户端余额、时钟或完整性声明。服务只读取，不生成转账、不消耗 lot，也不承诺 exactly-once；实际原子消费属于后续执行层。

## 新资金账目进口协议

使用唯一、未 SUPERSEDED 的 `BANK_CONFIRMED / SIMULATED_NEW_FUNDS_LEDGER` EvidenceItem，状态 VALID、hash 和 user_id 匹配，valid_from/observed_at 已知且未过期。以下为协议结构（数额仅作结构示例）：

```json
{
  "simulation": true,
  "protocol": "new-funds-ledger-v1",
  "user_id": "<UUID>",
  "complete": true,
  "as_of": "2026-10-04T01:00:00+00:00",
  "scope_account_ids": ["<CASH account UUID>"],
  "lots": [{
    "transaction_id": "<original CREDIT transaction UUID>",
    "account_id": "<same original CASH account UUID>",
    "bank_evidence_id": "<original BANK_CONFIRMED evidence UUID>",
    "bank_evidence_hash": "<canonical content SHA256>",
    "original_cents": 200000,
    "prior_unspent_cents": 170000,
    "spent_cents": 20000,
    "assigned_cents": 10000,
    "reserved_cents": 30000,
    "available_cents": 140000
  }]
}
```

`scope_account_ids` 为当前全部 CASH 账户 UUID 的排序清单；lots 必须完整列出截至 as_of 这些账户全部原始 BANK_CONFIRMED / CREDIT / INCOME 交易，包括当前目标版本确认以前的旧收入。每原始交易仅一个 lot，不能因版本不合格就从完整账目中删除旧 lot。

金额必须为非负整数分，并同时满足：

```text
original = 原始交易 amount_cents
original = spent + assigned + reserved + available
prior_unspent = original - spent - assigned = reserved + available
```

spent 表示实际已消费累计金额，assigned 表示成功归属累计金额，reserved 包含在途及 UNKNOWN 预留。prior_unspent 表示扣除已消费和成功归属后、扣在途预留前的金额，不是另一个可任意引用的旧余额。只可用 available；不能把整笔收入再算一次，也不能因为部分已消费就丢弃整笔剩余。

银行证据必须绑定原始 transaction/account/方向/金额/时间/经济角色及当前证据摘要；经济角色取自银行 payload，不能从可编辑 category 推断。内部划转、OPENING、退款和本金不成为新收入 lot。本项只接受原始入账 CASH 账户中的可用额；迁移账户不能创造新资格。

账目 as_of 不早于已采用余额快照的最新 observed_at，且与所选目标的 ownership 和本月 contribution 证明使用同一 epoch。各 Account 最后观察时间可以不同，不要求每个账户都恰好在这一时刻发生变动。账目还必须覆盖截至服务器 now 已发生、已观察的全部 CASH 流水的 occurred_at 和 observed_at 水位，包括 DEBIT 消费和内部转出；余额尚未刷新或金额碰巧相同，均不能放行水位之后仍有新事实的旧账目。按来源账户汇总 available 不超过该账户扣除目标归属现金后的余额。

只有当前 ACTIVE、有效且明确确认的版本可产生候选；来源交易须实际发生且已知，并处于该版本 confirmed_at 与 valid_from 之后。缺少完整账目、金额不守恒、原始来源不符或 epoch 不一致时返回证据不足，而不是伪造新增资金 0。完整且可核验的空资格集合可以产生正常的零额度或最低不足解释。

## 共享读取与候选边界

MVP-202 的只读来源适配提取为 `load_boundary_context`，返回 snapshot、versions、positions、products 和验证来源。原 `compute_user_boundary` 仅包装该共享结果，公开响应保持兼容。目标预览复用相同账户、所有目标归属、真实账单、历史周期、生活、应急和产品事实，不复制另一套边界适配逻辑。

目标必须属于当前用户，并绑定最新且当前有完整确认资格的策略版本；暂停或撤销等状态返回 INACTIVE_POLICY，不要求通过新收入账目来恢复权限。可用版本才读取新收入协议；纯域仍按当前版本时间锚点二次筛选。无论当前能否分配，始终向纯域传递共享上下文的完整策略集合；未来生效的已确认目标当前不产生建议，但其未来最低储备仍参与 baseline_boundary，不能因缺少当前分配资格而释放保护。预览将可用新资金按稳定次序拆分，保留 baseline_boundary 与 candidate_boundary；没有落库消费步骤。

缺证明、风险或不可用版本时 suggested_cents 为 null。只有完整来源确实支持零额度时才返回零；最低不足使用 MINIMUM_SHORTFALL，不能把低于最低额的零散值当作合格候选。候选无论重复读取多少次都不会自动增加目标累计归属或本月贡献。

## 验证与限制

真实 PostgreSQL 测试使用随机 bf_test 数据库与完整迁移。正例：原收入 200000 分，已消费 20000、已成功归属 10000、在途预留 30000，剩余可用 140000；当前目标剩余 target 为 110000，建议仅分配 110000，lot 保留 30000。相同输入重复预览一致且完整 16 表快照不变。部分消费后只降低可用剩余，不把整个 lot 重新启用或全部丢弃。

原始记录保留缺模块、缺来源失败门、暂停状态、旧账目之后消费/转出、未来目标基线丢失保护等真实失败与对应回归。验证包括完整性、旧 lot 不遗漏、双重守恒、重复原始引用、原始账户绑定、当前银行摘要、银行经济角色、未来事实、账户现金上限、当前版本旧收入隔离和用户隔离。未来有效目标的预览基线须与相同服务器时点的 compute_user_boundary 完全相等，且 suggested_cents 为 null。共享上下文重构还必须通过原 MVP-202 的 44 项服务及 HTTP 回归。

协议是受信任的合成进口声明，不是实际银行完整资金追踪或外部签名。导入者负责依据执行结果维护 spent/assigned/reserved；本项验证声明的身份、范围、时间和守恒，不声称已经实现写入这些事实的执行账本。并发预览可以引用同一可用 lot，因此不同目标的候选不能直接相加当作已预留预算；MVP-301 执行前须重验并原子占用。

初次目标适配组 26 项通过，58.01 秒（`MVP-203-service-final.txt`）；补充未来有效目标基线反例后，先有 1 项真实失败（`MVP-203-service-future-baseline-red.txt`），最小修复后的完整目标适配组 27 项通过，55.33 秒（`MVP-203-service-future-baseline-green.txt`）。共享重构回归原 44 项服务加 3 项 HTTP 共 47 项通过，94.27 秒（`MVP-203-boundary-context-regression.txt`），后续单点修复没有再改共享适配器。Ruff、format 与严格 mypy 三文件在最终修复后再次通过；初次静态检查原始日志保留在 `MVP-203-service-ruff.txt` / `MVP-203-service-mypy.txt`。完整项目验收和状态由主任务统一记录。
