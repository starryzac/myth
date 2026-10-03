# MVP-104：确定性历史候选发现

实现接口为 `discover_policies(session, user_id, now) -> DiscoveryResult`。`now` 必须由可信调用方提供带时区时间；HTTP 入口不接受用户时钟。服务复用 `PolicyLifecycleError`，只写 `PolicyProposal` 与派生 `EvidenceItem`，不写任何余额、流水、账单、持仓、目标、策略版本、动作或回执。

结果字段为 `simulation=true`、`user_id`、`rule_version=mvp-104-v1`、UTC `as_of`、`created_proposal_ids`、`reused_proposal_ids`、`skipped[{reason_code,source_ref}]`。候选必须经 MVP-103 显式确认，才能产生策略与不可变版本；发现本身不授予权限。

## 观察窗口与房租规则

窗口为用户本地“今天及此前 59 天”，含首尾共 60 个自然日。当前支持 `Asia/Shanghai` 固定 UTC+8 与 `UTC`，其余时区明确报错。租金按 `occurred_at` 本地日期筛选，并同时要求 `occurred_at`、`observed_at` 不晚于调用时间；账单按 `statement_date` 筛选。运行时间与窗口边界不进入修订去重键，所以窗口移动而实际参与事实不变时仍复用原候选。

房租按用户、CASH 账户、非空收款方分组，仅处理 `category=rent`、非一次性 DEBIT。分类只用于路由，不要求 `category_confirmed=true`，也不把该提示当作用户确认或未来租期证据。收入、内部转账、资产购买、卡还款、其他账户类型不参加。

一个分组的窗口内记录必须覆盖至少两个连续自然月，每月恰一次、日号相同、金额相同。不是从包含矛盾的数据中挑选一对恰好匹配的记录。同月重复、月间断档、日号或金额漂移均不生成该对象候选。生成 `recurring_obligation`，名称“房租预留候选”，金额为观测区间 `range[min,max]`；当前种子的两笔均为 180000 分，故区间为 `[180000,180000]`，日号 28。

## 信用卡周期规则

仅处理用户自己的 CREDIT_CARD 账户。窗口内已出账账单至少两个，按 **到期日期所在月** 判断独立连续账期，且到期日号相同。出账日期不能晚于用户本地今天；原证据必须已经可知。已经出账而将来才到期的账单可以参加。历史已还账单用于证明周期，不会被服务重新标记为未还。

种子中 9 月 1 日出账、9 月 20 日到期，以及 9 月 30 日出账、10 月 20 日到期，是两个独立连续账期。8 月 1 日账单在当前窗口外。候选名称“信用卡还款预留候选”，使用 `bill_balance/account_id` 动态账单规则，内部收款方合同为 `credit-card:<account UUID>`，不会把本期 145000 分固化为未来还款金额。

两种候选统一由配置 DSL 校验和规范化：`auto_execute=false`、`prepare_days_before=0`、默认优先级结构；`valid_from=null`、`valid_until=null`，不从历史推断租赁或授权期限。

## 证据绑定与明确限制

参与的原始证据必须存在、归属当前用户、为 `BANK_CONFIRMED/VALID`，其 `observed_at`、`valid_from` 不在未来，且 `valid_to` 为空或严格晚于运行时间。重新计算 canonical JSON SHA256 必须等于存储哈希。UNKNOWN、CONFLICTED、失效、缺失、未来或内容损坏都使该分组不成立。

交易内容强绑定 transaction ID、account ID、借贷方向、分金额、交易后余额、发生时刻、收款方。JSON 按规范化值严格比较，避免布尔值被当作整数金额。账单核对 evidence/source_ref 与账单 source_ref 一致，以及总金额、已还金额、出账日、到期日、状态；如果原 payload 提供 account_id/bill_id，则也必须匹配。一个证据 ID 不能同时支撑本用户的两条账单。现有种子账单 payload 没有 account_id/bill_id，本任务保留该已版本化数据，使用现有来源字段与外键绑定，不宣称已经完成真实银行身份图证明。

派生证据等级为 `BANK_OBSERVED`，来源类型为 `DETERMINISTIC_POLICY_DISCOVERY`。内容含规则版本、规则命中说明、对象键、修订摘要、观测窗口、原事实 ID/来源与必要字段快照、原证据 ID/来源/哈希/观察时刻、候选配置摘要，并明确 `future_obligation_guaranteed=false`。候选关联全部原始证据及一条派生证据。服务不会伪造 `USER_CONFIRMED_POLICY`。

MVP-103 的证据守卫同时识别该发现协议：派生 `sources` 中每条原始证据必须仍在本次已验证引用集合中，仍为 BANK_CONFIRMED，且摘要、source_type、source_ref、observed_at 与当时快照一致。缺项、重复、递归/自引用或当前事实证据更正会返回 `INVALID_EVIDENCE`，即使更正者重新计算了当前内容哈希，也不能直接确认旧候选；需要重新发现并审核。该检查同时用于现有版本的授权资格读取，证据后来变化时保守拒绝授权。这里只做本协议的一层来源校验，不宣称实现通用证据图或真实银行不可变证明。

## 幂等、候选修订与事务

逻辑对象键为 `SHA256(user_id, rule, account_id, payee_id)`；修订摘要包含规则版本、按事实日期排序的规范化源事实/证据快照和规范化候选配置摘要。数据库幂等键为 `discovery:<object digest>:<revision digest>`，使用已有用户+幂等键唯一约束。

- 同一修订仍为 PROPOSED：返回原 proposal ID，不新增证据或候选。
- 同一修订已 REJECTED 或 EXPIRED：返回 CLOSED_REVISION，不复活。
- 同对象有 CONFIRMED 候选或 confirmed_policy_id：返回 ALREADY_CONFIRMED，不重新授权，即使观察发生变化或原策略后来暂停/撤销。
- 实质观察改变且模式仍成立：新增修订，旧 PROPOSED 变为 EXPIRED。
- 模式不再成立、源证据失效、或事实移出窗口：本轮不再保留的旧 PROPOSED 也变为 EXPIRED。

已确认、拒绝、过期记录的状态和配置不被发现服务改写。当前抑制按本发现协议的候选关联识别同对象，不做跨任意手工策略的通用语义匹配。

调用方持有总事务；服务内部保存点允许失败回滚本次全部候选与派生证据，同时保留调用方进入保存点之前的工作。先锁定用户行，再锁定该用户已有候选、证据、相关事实。与 MVP-103 确认共享用户行锁，使同用户 discover/discover 和 discover/confirm 串行化；默认写事务隔离级别为 PostgreSQL READ COMMITTED。不会提交调用方事务，也不会回滚普通数据库或执行外部支付。

## 验证边界

测试全部运行在 PostgreSQL 16 随机 `bf_test_<32hex>` 临时库，使用真实迁移与演示种子。证据日志位于 `docs/progress/evidence/MVP-104-*.txt`：首次模块缺失 red、修订重入 red、对应 green 与边界检查。包含种子恰两候选、资金与权限表内容不变、同事实次日去重、并发唯一、关闭候选不复活、事实更正/失效过期、证据拼接与篡改拒绝、跨用户隔离、数据库触发器注入中途失败回滚，以及显式确认后才形成权限。资金边界计算引擎尚未实现，本任务不以占位测试宣称通过该后续能力。
