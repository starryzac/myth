# ADR 0007：授权范围内的单产品资产配置

状态：VERIFIED for MVP-204（2026-10-04）。完整质量门 `20261003T221328Z-b46e2a5e` 通过733后端/4前端/1Edge，113源码摘要逐项一致，详见[验收记录](../progress/MVP-204.md)。前置MVP-203提交e0f7ac0。依据初版8.3、MVP-204及完整计划9.3/10；本项不实现执行器、恢复动作、四级自主判定或定存梯度。

## 范围和权限

一次指定当前 asset_authorization 策略，输出只读单产品候选及保留现金；preview_only/financial_only，不产生 AUTO_EXECUTE。支持 general_idle_funds 和一个 goal 的内部资产放置。活期 CASH 是零操作选项，不生成“申购活期”动作。

服务校验当前最新版本、确认绑定、有效期、用户及配置哈希。目标范围还须验证当前 goal_saving 版本和 Goal 投影一致，当前配置.asset_policy_id 与选定资产授权匹配，资产配置.goal_id 指回同一目标，两条授权均当前有效。截止日取已确认配置，不信任可变投影字段单独提供权限。未来、暂停、过期或旧版本不能发起新配置；既有财务保护不因此消失。

候选限额同时满足 allowed_asset_classes、无本金波动、风险等级、lock/redemption 上限、起投、单笔上限和剩余自动管理额度。本金波动不进入本项自动候选，不能因用户风险数字较高就放开。费用或损失不明确、或当前正常买入/退出有正费用，不纳入零费候选；提前支取损失由 MVP-205 处理，不当作无损提前回款。

## 资金归属及财务后态

一般资金只取 CASH 账户中尚未归属任何目标的现金，确定性按账户UUID分配来源；不能使用信用额度、未来收入、目标账户未映射现金或管理账户余额当作可支付现金来源。

目标范围上限为该目标已证明的 cash_owned。候选金额 x 只减该目标账户现金和本目标 cash_owned，同时增加同目标 principal_owned 和一个反事实持仓；allocated 与本月 contributed 均不变，其他目标、未映射GOAL现金和203收入来源不动。不能用 general safe_idle/max_allocatable 直接限制目标内放置；一般闲钱为0时仍可能有合法目标候选。

每个产品使用自己的明确退出计划求最大安全金额，并重算202完整90日/273检查点，包含买入当下；候选本金到账和收益计息必须使用同一退出时间。反事实持仓及可用日期明确标为计划，不冒充银行实际持仓/到账证据。目标本金返还时现金与同目标保护同步增加，不能释放为一般闲钱。baseline 已有风险或来源不足时关闭新购。收益不进入现金或本金边界。

## 目录及退出合同

旧产品版本、UUID及旧持仓关联保持不变。新明确条款追加相同product_code的v2/new UUID；新购仅考虑在as_of已被观察、已生效且未失效的最高版本。created_at在未来的回溯生效记录不能被读取为当前已知产品。

固定到期合同使用 `fixed-principal-return-v1`，明确 CALENDAR、guaranteed=true、term_days、settlement_delay_days、principal_return_bps=10000、rollover=false、auto_rollover=false，并与目录的风险、锁定、赎回延迟一致。

T0/T1 使用 `planned-principal-return-v1`，明确 CALENDAR、guaranteed=true、settlement_delay_days、principal_return_bps=10000、rollover=false、auto_rollover=false；它证明按计划申请后可无损结算，不表示当前已经申请或到账。须同时具备产品 auto_redeem_allowed 和当前授权 allow_auto_recovery_without_penalty，本 ADR 将后者在本预览中解释为允许无损计划退出。未来实际请求仍须重新核验当时最新授权。

正常退出费用均由同一maturity_rule中的 `yield_rule` 明确：protocol=`simple-annual-yield-v1`、basis=`ACT_365`、annual_yield_bps与目录一致、simulation=true、fee_cents=0、purchase_fee_bps=0、redemption_fee_bps=0；accrual分别为 `UNTIL_MATURITY` 或 `UNTIL_REDEMPTION_REQUEST`。缺字段不默认零费或百分之百本金保证。完整JSON摘要绑定产品版本，边界的纯财务摘要仍排除收益。

目标储备策略未来自然到期停止新贡献，不释放已经归属的资金，也不自动撤销仍有效资产授权下的同目标无损变现。因此买入时检查两条当前有效链；后续计划主动赎回的请求有效窗取资产授权，仍受目标使用截止日限制。实际执行按届时最新资产授权重验；这个解释不允许有损退出、跨目标变更或忽略资产授权撤销。

## 日期和净模拟收益

一次请求的共同比较窗口为一般范围90个自然日；目标范围为 min(90, deadline本地日期−1−今天本地日期)。本金 available_date 必须早于目标截止日，即最迟前一本地自然日可用；不是强制前一日零点前。窗口小于等于0保留现金。

每产品一个确定的正常退出方案：固定产品只持有一个term、之后现金收益0，不虚构续作；T0/T1计划在窗口末前完成无损结算，请求日向前扣显式结算延迟，等待期间不计息。请求采用as_of加整数自然日；若授权更早失效，则向前取仍严格早于valid_until的最后整数日，提前到账后的剩余窗口收益0。固定合同买入后自动到期返本不依赖未来主动赎回授权。计划请求不早于锁定结束、到款不超90日/目标期限；不能排出合法方案则拒绝该产品。

`net_simulated_yield_cents = principal_cents * annual_yield_bps * earning_days // (10000 * 365)`，全程整数，乘积后只向下取整一次。比较各产品最大合法金额在上述方案中的净模拟收益；本金余款保留现金。只比较这个有限候选集，不宣称遍历所有可能退出日、再投资路径或FULL全局最优。并列先保留现金零操作，其余优先无损流动性更短、赎回延迟更短，最后按稳定product_code/version/UUID消歧。选择摘要包含收益和退出计划，财务边界不因修改展示收益而增加。

手算基准：本金1000000分，T0=150bps/T1=180bps/固定30日=220bps，H90净模拟收益3698/4389/1808；H5为205/197/固定不可用；H6为246/246，优先T0；H7为287/295，优先T1。上述字面真值与公开纯函数接口的定向验证已通过，记录见[独立oracle](../architecture/asset-allocation-oracle.md)，整体验收仍以完整质量门为准。

## 自动管理额度与在途占用

新版本不能重置历史占用。服务按历史授权的稳定scope键（general或goal_id）累计全部同scope策略和旧版本的未结清自动管理本金；HELD/REDEEMING/MATURED保留，UNKNOWN关闭精确推荐。手动持仓必须有明确人工购买、原始交易和无自动授权/动作关联的可信证明，不能仅凭policy_version_id为空推断手动。

增加服务器合成 `SIMULATED_ASSET_EXPOSURE / asset-exposure-v1` 完整快照证明，绑定当前余额、完整持仓/动作/全部回执及关联摘要，覆盖最新经济事实水位；目标范围还与归属证明同一epoch。服务重算并核对集合和摘要，不接受客户端直接提交的managed_total。空集合也须完整声明；缺失、冲突、未来事实或陈旧声明返回不足。

已物化成功申购与其持仓只占一次；必须有明确动作→成功回执→银行交易→持仓关联，不能按相同金额猜测。可证明未扣账的待申购同时预留现金及管理额度，不能与新候选争用。SUBMITTED/UNKNOWN、部分执行、失败但有不明/已执行回执、结清关联不足等返回 EXPOSURE_RECONCILIATION_REQUIRED/null；不能累加attempt回执金额或仅凭取消/失败状态释放预留。v1适配细节和可支持的证明形式须另记来源协议文档，无法证明的情形关闭新购而不猜净额。

实际行锁、提交、原子预留、资金与持仓记账、正常/异常回执由301处理；本项只验证当前一致快照下的候选。

## 验证与分工

纯域/DTO/单测由web_foundation实现；来源协议/适配/PG测试由foundation_review实现；requirements_audit实现独立小整数枚举oracle和性质测试；root负责版本化目录/种子、HTTP、合同、ADR和完整验收。目录旧版本不可改，已接受迁移不回改。每组先记录真实失败再实现；原有回归和最终make check必须通过，随后核对源码摘要、更新进度并提交。
