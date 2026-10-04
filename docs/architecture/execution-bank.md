# MVP-301 独立模拟银行与资源预留

这是本地、合成银行账本协议，不连接真实银行。银行经济事实与应用投影分开提交；应用状态、回执或一次 HTTP 成功都不能替代银行账本。

## 三段事务与公开接口

1. 应用持有用户行锁，重验当前事实、正式策略和必要的单动作确认，持久化 `ActionPlan.request.execution = BankCommand(effect, effect_hash)` 与资源预留，并提交 `SUBMITTED`。
2. `services.execution_bank.process_operation(engine, user_id, action_id, now)` 开启独立事务，再次锁用户、读取已提交请求并调用 `verify_execution_sources` 重验。银行受理或结算在此提交。
3. 调用者开启新应用事务，根据银行不可变请求与 posting 投影余额、流水、持仓、目标归属、收入位置与回执。失败后沿用原 action 和原银行请求补账，不重新下单。

`BankOperationResult` 包含 `operation_id/action_id/status/posting_ids`。`ACCEPTED` 表示待结算，`SETTLED` 表示银行账本已发生效果；应用是否成功投影另行核对。读取接口不隐式结算。已受理 T1 按原经济载荷到账，无须重新获得已撤销策略的许可；新请求必须重新核验当前权限。

已受理的到账时刻是银行承诺的 `available_at`。晚查询不会把 day1 的事件改成 day2：operation 的 `settled_at` 与 posting 的 `occurred_at` 保留 day1，posting 的 `created_at` 记录 day2 实际观察/落盘。为避免倒序补写历史，两种银行写入口都在用户锁内阻止越过更早已到期而尚未处理的 ACCEPTED 请求；先处理原操作再提交新效果，不引入后台调度器。若独立链已经出现更晚分录，明确要求对账，不凭空把旧事件插入新余额之前。

`ExecutionEffect` 是严格、不可变的经济命令，银行不接收任意 posting。支持内部划转、确定金额的周期付款、目标分配、申购和整仓赎回。普通内部划转需精确单动作确认；费用或本金损失也需原始和当前授权允许，并绑定明确确认与完整报价。确认来源 `USER_CONFIRMED_ACTION / USER_ACTION_CONFIRMATION` 只授权该动作，不是持续策略。

## 数据模型与升级

0004 新增 `bank_operations`、`action_resource_reservations`，总表数为 20。既有 `simulated_bank_redemptions` 保留；每条旧请求按原 ID 回填统一 operation，旧 posting 的 ID、金额和时间不变，仅增加 operation/leg 引用。

`simulated_bank_postings` 新增维度、元数据与 leg 引用，移除对应用持仓的外键，使银行可先提交新持仓本金，再由应用建立持仓投影。银行 posting 仍有用户、现金账户及 operation 所属约束。旧赎回入口同步写统一 operation；新旧入口共同检查持仓预留及全局 `closing_position_id`，不能各自关闭同一仓。

数据库 UPDATE 触发器禁止修改 operation 原经济载荷、已结算/已拒绝终态及已解除资源记录，禁止把 UNKNOWN 退回 ACCEPTED。posting 原有不可变触发器继续生效。此处不声称具有数据库管理员无法删除或任意改写记录的防护。

降级仅允许随机 `bf_test_<32 hex>` 测试库，存在无法由旧表表达的通用操作时拒绝降级。迁移测试同时核对 metadata、空库往返，以及有真实旧结算记录的升级保真。

## 独立经济事实

所有账本都有一次明确的 OPENING 锚点，然后按 `previous_posting_id + sequence_number` 串联。非开户 posting 的 ID 为 `uuid5(operation_id, 'posting:' + leg_ref)`，同操作同 leg 唯一，余额不得为负。银行不从可编辑的应用余额自动补开户。

| 维度 | 账本与操作腿 | 含义 |
| --- | --- | --- |
| ECONOMIC | `CASH:<account>` / `cash:<account>` | 每账户合并净现金变化 |
| ECONOMIC | `POSITION:<position>` / `position` | 独立本金增加或整仓减少 |
| ECONOMIC | `PAYEE:<id>` / `payee` | 已核对合成收款身份的外部收款 |
| ECONOMIC | `FEE:<user>`、`LOSS:<user>` / `fee`、`loss` | 净额内扣的费用与损失去向 |
| GOAL_OWNERSHIP | `GOAL_CASH/GOAL_PRINCIPAL/GOAL_LOSS:<goal>` | 目标现金、本金及累计损失归属 |
| INCOME_LOCATION | `LOT_AVAILABLE/LOT_RESERVED/LOT_SPENT/LOT_ASSIGNED:<fragment>` | 同一原始收入的当前位置与用途 |
| LIABILITY | `LIABILITY_DUE/LIABILITY_PAID:<business>` | 确切本期剩余义务与已付金额 |

每操作 ECONOMIC 增减严格合计为零。有损赎回满足 `principal = cash_received + fee + loss`，不能净额内扣后再扣一次现金。收入每次消费/分配/转移在来源位置扣减、目标桶增加，合计为零。付款在独立债务维度同步减少 due、增加 paid；信用卡仅按 bill_id，普通周期按 policy_id + period，全额支付已确认的本期剩余金额，区间义务须明确 `final_total_cents`。

目标归属是对 ECONOMIC 资产的分类，不是第二份资产。分配使目标现金增加，同时减少可由实际现金减目标归属推导的未归属部分；目标申购 cash 转 principal；有损退出 principal 减少、cash 净额增加、loss 增加，应用 allocated 相应减损，本月贡献不回写为退款。不能将目标维度与现金、本金再次相加得到总资产。

多资金源申购逐账户写现金腿，POSITION 只增加总本金。应用须为每个真实 debit 建立流水；返本账户由经济载荷 `return_account_id` 明确绑定，不能从可重排的资金腿列表推断。`purchase_exit` 记录已显示的退出计划与到账上界；主动赎回计划尚未受理时不能作为真实未来回款，实际后态按尚无到账事件重验。固定合同按实际购买时钟重算到期和延迟，必须仍满足原已显示上界。

## 收入位置开户与镜像

`open_execution_anchors(session, user_id, as_of, *, goal_balances=None, income_ledger=None)` 仅供可信种子或完整导入使用。收入原始身份必须绑定已发生 BANK_CONFIRMED CREDIT/INCOME 流水、账户、金额、时间及证据哈希；不得把内部划转、退款或本金返还开户为新收入。

正常 `create_goal_projection` 在先用户锁、后策略锁的顺序下创建新目标。核对既有银行投影及完整曝光后，仅为该新目标建立 GOAL_CASH=0、GOAL_PRINCIPAL=0 两个锚点，并在同事务刷新完整曝光。绑定的实际 CASH/GOAL 账户可以已有余额，但新目标不能据此认领现金。既有目标的重复创建在开户之前返回，缺失锚点或正 allocated 投影绝不能在此路径补写为银行真值。

`validate_income_locations(session, user_id, bank_location_snapshot(ledger), now)` 对照完整 origin/location 集、不可变元数据、各桶余额及已知时间。银行 AVAILABLE 等于应用 available 加尚未进入银行的 active reservations；银行 RESERVED 只镜像原导入的 legacy reserved。应用 phase1 的资源声明不是银行实际扣款，不能因预留而重复扣一次银行余额。

内部划转的 fragment ID 由 `location_id(origin_transaction_id, destination_account_id)` 决定，原收入金额和发生时间保持，同源同位置可合并。历史 v1 导入与新的位置化 v2 协议由收入服务适配；银行接口不会用当前应用余额为缺失镜像补值。

## 幂等、并发及失败

`reserve_resources` 在用户锁内校验精确请求、现有预留与独立银行容量。CASH/POSITION/INCOME/GOAL_CASH 不能超银行账本，BUSINESS 成功消费后继续占用，MANAGED 按正式策略及完整曝光计算。新的预留不能抢占旧入口已受理持仓；旧入口也不能绕过新的持仓预留。

同 action 的同资源请求返回原记录，改变金额或资源身份冲突。银行同幂等键只接受原完整请求；不同键仍受 business_key、closing_position_id 唯一约束和银行用户锁保护。回执丢失、UNKNOWN 或应用投影失败保留原资源；`resolve_resources` 仅允许独立 SETTLED 消费，明确 REJECTED 或无银行请求且动作已 CANCELLED/INVALIDATED 才释放。

当前财务重验与授权重验处于银行用户锁内，和策略生命周期使用相同用户锁，防止校验后撤销再受理。批次编排、通知、HTTP 错误语义及完整应用投影由上层服务负责；本模块不实现后台调度器或真实银行重试网络协议。

## 定向验证记录

- `MVP-301-bank-own-and-legacy-suite.txt`：真实随机 PostgreSQL 测试库，46 项通过，86.13 秒；覆盖 13 项新银行、3 项资源、12 项迁移、5 项旧银行及 13 项旧恢复服务。
- 最终 `purchase_exit` 合同增加后，`MVP-301-bank-purchase-exit-and-detached-position-green.txt` 中采购及撤权两项通过；该次旧测试因误写排序字段失败，修正后 `MVP-301-bank-detached-position-green.txt` 独立 1 项通过，3.57 秒。原始失败日志保留。
- 0004 有意移除银行本金对应用持仓投影的外键，因此旧 205“删除投影应触发 FK”断言不再适用。适配后的测试实际删除投影，保留同一仓位的独立本金与 VALID 银行证据，再断言完整曝光读取关闭，不削弱原来源完整性要求。
- `MVP-301-bank-final-mypy-green.txt`：9 个相关文件严格 mypy 通过；同一源码 Ruff/format 全部通过。整体验收、HTTP、来源消费和投影故障由主任务另行记录，本节不代表完整 MVP-301 已完成。
- 公开目标创建补零锚点后，`MVP-301-bank-public-goal-opening-red.txt` 保留正常流程失败；两项新增目标测试验证实际成功分配及既有正投影不能补开户。旧目标 HTTP fixture 的可信独立收入导入由该测试 owner 另行适配并验证。
- T1 时序真实三个失败保存在 `MVP-301-bank-late-t1-red.txt`，定向四项通过记录在 `MVP-301-bank-late-t1-green.txt`。最终 `MVP-301-bank-goal-and-timing-final.txt` 36 项通过，101.06 秒：16 项新银行、2 项公开目标、5 项旧银行、13 项旧恢复服务。`MVP-301-bank-goal-and-timing-mypy.txt` 五个最终修改文件类型检查通过，Ruff/format 同时通过。
