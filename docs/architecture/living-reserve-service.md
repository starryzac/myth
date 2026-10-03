# MVP-201：生活准备金只读接入

服务接口为 `estimate_living_reserve(session, user_id, now, configuration) -> ReserveEstimationResponse`。它验证 PostgreSQL 中的合成交易覆盖证明、原始银行事实及独立类别确认，然后调用 `app.domain.living_reserve` 的纯函数。服务没有 INSERT、UPDATE、DELETE、flush 或 commit，不保存候选、策略、决策、资金、目标或执行权限；内部禁止查询触发自动 flush。

返回字段：`simulation=true`、`user_id`、UTC `as_of`、`estimation`（纯估算结果和逐日/逐窗口解释）、`source_evidence_ids`、`input_digest`、`source_issues[{code,source_ref,message}]`、可空的 `candidate_configuration` 与 `candidate_configuration_hash`。READY 表示数据支持这次可审核估算；不是策略已确认或可执行。只有后续明确审核、确认形成有效策略版本后，生活准备金才能参与硬约束。

## 时间、输入和读取一致性

可信 `now` 必须带时区，只由服务端调用方提供。当前账户日界支持 Asia/Shanghai（UTC+8）与 UTC。取当前本地日期之前的完整自然日，不把当天尚未结束的支出混入样本。默认 56 个完整日、14 日重叠窗口、分位数 0.8；seed v2 在上海 2026-10-04 零点的区间是 2026-08-09 至 2026-10-03。

只接受经过现有严格 DSL 规范化的 `living_reserve`。lookback 上限 366 天、账户上限 100、当前用户交易读取上限 100000；配置或容量越限明确报 422。用户不存在报 404，时钟或时区不支持报 422。缺账户不是“零支出完整历史”，返回 INSUFFICIENT_HISTORY。

HTTP GET 使用已有数据库依赖的 REPEATABLE READ，保证多次 SELECT 来自同一事务快照；其他调用方也应提供一致的只读事务。所有事实、证据与账户查询限定相同 user_id，不混入其他用户的数据。客户端不能提交 `complete=true` 或其他覆盖结论。

## 历史覆盖不是交易首尾

银行余额、第一笔与最后一笔流水、每天至少出现一条交易，都不证明导入没有遗漏。当前接入只接受已有 `EvidenceItem` 中的 `BANK_CONFIRMED / SIMULATED_TRANSACTION_HISTORY_COVERAGE`，使用 `transaction-history-coverage-v1` 协议和共享 `history_coverage.py` 摘要函数。

覆盖证明必须满足：

- 属于当前用户，状态 VALID，内容摘要一致，valid_from/observed_at 已可知且未过有效期；
- 明确相同用户、时区、日期区间及当前全部账户范围；每个账户均有交易数量和银行事实摘要，零流水账户也有明确零条摘要；
- 声明期覆盖所需历史区间，最后声明日已经闭合；证据观察时刻不得早于该日次日本地零点，也不得晚于本次 now；
- 重新读取完整声明期的事实并复算逐账户摘要一致，不能只检查当前 56 日子集的首尾。

当前支持唯一的一份未 SUPERSEDED 覆盖证明；多份并存视为冲突，不任意选取或拼接覆盖范围。覆盖缺失、状态未知、未来、过早声明完整日、账户范围变动、删除流水或更正银行事实而未更新覆盖，均返回 INSUFFICIENT_HISTORY，基础/建议金额和候选配置为空。只有覆盖成立的日期才允许把没有入选消费的日总额记为零。

覆盖声明只证明本合成数据集的交易导入快照完整，不代表真实银行所有消费已经采集，也不代表未来支出已知。固定种子不会滚动到真实今天；当真实日期前进而缺少新增完整日时，返回不足是预期行为。

## 银行事实与可编辑分类分开

每笔声明期交易必须有同用户 BANK_CONFIRMED 原始证据，状态和时间有效、内容哈希正确，并严格绑定交易 ID、账户 ID、借贷方向、分金额、交易后余额、发生时刻与收款方。共享银行快照还绑定原证据 ID、来源、时间及内容摘要。覆盖摘要故意排除 category、category_confirmed、is_one_off，用户调整这些应用字段不应伪装成银行流水变动。

`economic_role` 来自 BANK_CONFIRMED payload，并进入覆盖摘要。CONSUMPTION 映射为消费；INCOME、OPENING、INTERNAL_TRANSFER、ASSET_PURCHASE、CREDIT_CARD_PAYMENT 映射为非消费。未知或缺失角色不能被当作零消费后继续给精确估算。即使把内部转账、资产申购、信用卡还款改成 food 并补类别确认，也不能突破非消费角色。房租和水电为外部消费，但默认没有列入基本生活类别。

消费若标记 `category_confirmed=true`，还必须存在唯一、有效且当前匹配的 `USER_DECLARED / SIMULATED_USER_CATEGORY_CONFIRMATION`。服务先按 transaction_id 收集全部未 SUPERSEDED 的类别确认并要求唯一，再核对 category 与当前分类相符、confirmed 严格为 true，以及内容哈希、状态和观察时间有效。不能先筛选匹配类别而忽略另一份冲突确认；更正分类时旧证明必须明确标为 SUPERSEDED。只改分类字段、删除确认记录、借用其他用户确认、未来确认或坏哈希均使估算不足。明确标记为未确认的交易按规则解释排除。

is_one_off 是当前应用事实，不冒充独立用户确认。它进入计算输入摘要，纯估算器根据配置决定是否排除；服务测试把 450000 分的一次性支出改成 food 并添加匹配类别确认后，仍验证启用排除时不抬高建议，关闭排除时才按实际金额进入窗口。

## 计算与解释

纯估算器对入选消费按本地日求整数分总额，再求全部连续 horizon 日窗口。默认 56−14+1=43 个窗口。0.8 转成精确分数 4/5，nearest-rank 为 `ceil(43×4/5)=35`，取升序第 35 项，不插值、不平均。银行金额、窗口总额、缓冲与最终结果保持整数分，概率计算不使用二进制浮点乘法排名。

默认 v2 样本的独立真值与真实 PostgreSQL 测试一致：基础准备金 77900 分，配置缓冲 50000 分，建议 127900 分。缓冲在这里是待审核配置，不声称已经产生用户确认权限。候选保留完整 living_reserve 方法、类别、窗口与边界重确认设置，不能仅将这次金额直接当作永久固定约束。

`input_digest` 覆盖规范化配置、实际域输入（包括当前分类和一次性标记）、使用的证据 ID/摘要/状态/观察时刻及来源问题。响应列出原证据引用，便于追溯同一次输入；它不是全数据库密码学证明或银行签名。算法结果还含逐日数额、窗口、被纳入/排除的交易和覆盖缺口。

## 已运行验证

测试使用 PostgreSQL 16 随机 `bf_test_<32hex>` 数据库，真实迁移和 seed v2。`MVP-201-service-red.txt` 保留实现前模块缺失；`MVP-201-service-integration-first.txt` 保留纯估算过滤尚在实现时的真实整合失败；`MVP-201-service-seed-green.txt` 验证独立算例；`MVP-201-service-boundaries.txt` 中首批 27 项通过。分类冲突的实际失败及修复后 9 项相关回归记录在 `MVP-201-service-category-conflict-red.txt` / `MVP-201-service-category-conflict-green.txt`，同时验证旧证明明确 SUPERSEDED 后新分类可正常使用。

冻结源码后的完整服务组为 28 项通过（52.36 秒，`MVP-201-service-final-green.txt`）；Ruff 与严格 mypy 两文件均通过，分别记录于 `MVP-201-service-final-ruff.txt` 和 `MVP-201-service-final-mypy.txt`。

覆盖种子真值、重复输出、全 16 表快照不变、覆盖缺失/冲突/未来/未闭合/账户与时区不符、删除交易、原事实更正、独立类别确认失效、450000 分一次性排除、非消费伪装、明确未确认类别、日界与当天交易隔离、其他用户和空账户、非法配置及容量上限。此处没有实现自主资金边界、历史补采、银行真实连通、生活策略确认写入口或执行器。
