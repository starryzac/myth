# MVP-301 收入来源与资金位置

本模块保留 MVP-203 的“当前版本确认、生效后实际收入”资格规则，为通用动作增加来源位置与动作预留。本文描述合成银行协议，不声称接入真实银行，也不将流水分类编辑当作收入证明。

## 不变身份与守恒

`IncomeOrigin` 绑定原银行交易 ID、原账户、原金额、发生/观察时间、原证据 ID/hash。内部划转不生成新 origin，也不把转入时间改为收入时间。`fragment_id = UUID5(origin_transaction_id, 'income-location:' + account_id)`；部分划转按目的账户拆位置，同 origin 回到同账户时合并到同一稳定位置。

对每个 origin，在全部账户位置上始终满足：

```text
原收入金额 = Σ(spent_cents + assigned_cents + reserved_cents + available_cents)
reserved_cents = legacy_reserved_cents + Σ(该位置仍为 RESERVED 的动作 claim)
```

`ALLOCATE_GOAL` 消耗 available 并增加 assigned；普通付款和一般闲钱申购消耗 available 并增加 spent；内部划转将已预留金额送入目的位置 available。目标资产申购不再次消费已 assigned 收入。收益、退款、内部转入、赎回本金均不会创建新收入资格。

V1 `new-funds-ledger-v1` 只读兼容保留全部既有测试：每个旧 lot 规范化为一个 origin 和位置，旧 reserved 全量保留为 `legacy_reserved_cents`。未知历史预留不绑定新动作，不能被新动作释放。只读查询不自动迁移证据；第一次合法预留才追加 V2 证明。

## 公共接口

纯函数位于 `app.domain.income_ledger`：

- `reserve_income(ledger, action_id, uses, operation, now, destination_account_id=...)`
- `commit_income(ledger, action_id, now)`
- `release_income(ledger, action_id, now, confirmed_no_effect=True)`
- `bank_location_snapshot(ledger)`：银行位置核对协议。

同 action、同命令重放幂等；改变 operation、来源片段、金额、目的账户则拒绝。`IncomeUse` 必须给出片段、原交易、当前账户、金额，不能拿别的账户余额补收入资格。

服务位于 `app.services.income_ledger`：

- `read_income_state(session, user_id, now)` 返回 ledger、证据 ID/hash。缺失明确报 `MISSING_NEW_FUNDS_LEDGER`；冲突、现有无效证明不可按缺失跳过。
- `income_lots_for_action(session, user_id, now, action_id=None)`：普通读取仅 available。指定 action 只加回该动作仍 RESERVED 的 claim，不加回 legacy、其他动作或已 COMMITTED/RELEASED 的金额；不写数据库。准备新动作尚无 claim 时须省略 action_id。
- `reserve_income_for_action`、`commit_income_for_action`、`release_income_for_action` 分别对接应用预留、独立银行结清后投影、确定无效果后的释放。事务由调用方控制。

`ActionPlan.request['execution']` 是完整 `BankCommand{effect,effect_hash}`。`income_evidence={id,hash}` 固定准备时的来源。纯时间前移的后继证明可沿已校验的 `supersedes_id` 链接受：逐层同用户、同来源、BANK_CONFIRMED、完整 hash、合法状态和非倒退已知时间，且除 `as_of` 外全部内容相同；其他预留、消费或归属变动仍要求重新准备。确认阶段不改写原经济请求。

## 银行事实与应用预留

`income-location-bank-v1` 快照保留完整 origin，逐位置给出四分量及 `active_reserved_cents`。独立银行账本使用：

```text
BANK LOT_AVAILABLE = app.available + app.active_reserved
BANK LOT_RESERVED  = app.legacy_reserved
BANK LOT_SPENT     = app.spent
BANK LOT_ASSIGNED  = app.assigned
```

阶段一的预留是应用 claim，不能伪造银行支出。银行真正结清才将 AVAILABLE 转为 SPENT/ASSIGNED，或迁入另一账户的位置。阶段三先核对同一 SETTLED BankOperation 的完整原请求，再核对变换后的完整位置镜像；仅设置 `FAILED`、HTTP 丢响应、ACCEPTED 或 UNKNOWN 均不能释放。REJECTED 也必须绑定原完整请求。

可信模拟导入可显式调用 `open_execution_anchors(..., income_ledger=...)` 建立独立开户事实。预览、准备、确认、执行不从可编辑应用余额反推开户或来源。Seed V6 没有完整收入资格账本，缺证明的目标分配仍关闭。

## 应用结清投影

`project_execution(session, operation, now)` 从完整独立分录投影五类动作；内部保存点保证调用方捕获异常后也不会提交部分余额更新。ACCEPTED 返回 None，保留预留；SETTLED 生成稳定 UUID 回执和流水证明。回执列出该 BankOperation 的全部非 OPENING 分录 ID，包含经济腿、目标归属、收入位置和债务备查腿。

同账户目标分配可以只有归属及来源备查分录，无虚构现金流水。多源申购逐个真实现金 debit 写流水，position proof 用 `execution-purchase-v1` 列出 `purchase_transaction_ids`；保留首笔 `transaction:purchase` 兼容链接。返本账户由 effect 显式绑定，不能靠列表排列推导。赎回保留原本金身份，现金只增加扣除明确 fee/loss 的 net，归属损失同步反映到原目标。

本金已受理但未结清不变成现金。计划主动退出属于尚未受理的计划元数据，不生成 BANK_CONFIRMED 本金可用事实；固定合同到期另按明确条款与真实结清起点核对。原策略之后停止不会取消已经独立发生的经济事实，但不能借投影扩大授权或改收款对象。

## 验证证据与范围

- 独立纯函数性质一组 100 个有效、0 个无效样例（不累加复跑）：原金额 1000、spent 100、assigned 100、legacy reserved 100、available 700；转移 x 后再归属 x，四分量分别为 100、100+x、100、700−x。
- `MVP-301-income-compatibility-suite.txt`：85 项通过，包含原 MVP-203 domain/service 兼容组及收入新测试；其中生成样例仍仅按上述单次 100 个计算。
- `MVP-301-income-seed-v6-red.txt`/`...-green.txt`：真实版本断言和外键清理失败到 18 项通过；临时 PostgreSQL、20 表、重复逐表等值，无正式演示库重置。
- `MVP-301-income-projection-transfer-*`、`...purchase-*`、`...goal-stub-red.txt`、`...goal-green.txt`：公共投影接口的真实失败及成功；目标最初两次 fixture 将策略确认设得晚于原收入，被正确资格门拒绝，不能算作投影实现失败。
- `MVP-301-income-projection-atomic-*`：银行已结清后出现冲突仓位，真实复现部分现金写入；保存点修复后完整回滚应用投影。
- `MVP-301-income-projection-first-suite.txt`：首轮 9 项真实 PostgreSQL 投影检查全部通过，包括 T0、T1、显式损失、账单、目标、申购及重放。
- `MVP-301-income-final-suite.txt`：最终本子任务 37 项通过 / 73.44 秒；单次 Hypothesis 100 passing、0 failing、0 invalid。包括 V1/V2、严格 epoch 后继、五类投影、exact/range 本期 paid、多源 20000+30000 分申购、未受理退出仅保存计划，以及种子重跑。`MVP-301-income-ruff-final.txt` / `...-mypy-final.txt` 记录 11 个源码文件静态通过。
- `MVP-301-income-projection-typed-evidence-behavior-red.txt` / `...-green.txt`：银行结清后，将目标本金零改为布尔 false 并重算证明摘要，旧 Python 数值比较错误放行；规范 JSON 类型比较后拒绝。前一个同名前缀的 `...typed-evidence-red.txt` 是 SQLAlchemy 未强制写入 JSON 类型变化导致旧 hash 不匹配的 fixture 失败，保留记录，不算行为 red。
- 首次 import 拼写错误、试图修改数据库不可变 BankOperation 而触发的 fixture 错误均保留原日志，不冒充业务 red。定向收入 commit 的早期测试使用明确构造的 SETTLED 请求和位置两腿，仅证明来源投影；后续 execution_projection 使用实际 `process_operation` 验证银行完整链。

本文不将定向测试视作 MVP-301 全量验收，不将 302 自主级矩阵、303 解释链或 304 审计哈希链标为完成；最终整合由对应阶段验收负责。
