# 固定购买的原计划与实际到账上界

状态：W1 实施。两条动态购买/超上界节点已在真实隔离 PG 取得 GREEN；它们所在批次另有失败，全部相关模块及自然时钟浏览器验收仍待取得。范围：既有初版固定购买，不修改原经济效果、原计划、历史哈希或产品条款。

准备动作时，`execution_planning` 已将固定本金预计可用时刻写入原 `purchase_exit`，并绑定 `latest_arrival_at = 预计时刻 + 15 分钟`。纯执行重验按当前银行时钟重算边界、产品和授权，并要求重新计算的本金可用时刻不超过这个不可变上界。此前到账投影额外要求实际可用时刻不超过准备请求的点估计，致使晚一秒的合法执行虽然通过重验、银行实际提交，投影仍失败。

真实失败记录：`docs/progress/evidence/W1/financial-blockers-red-20261005T012647Z-c289df02/output.log`，动态时钟正例在 `execution_projection._purchase` 报 `Actual contractual maturity exceeds the immutable purchase bound`。同批超过原上界的负例已通过；整批有其他失败，不能作为验收通过。

投影现在使用原 `latest_arrival_at` 作为硬上界，同时核对原点估计等于原有效起点加原合同期限及到账延迟，earning/liquidity 天数与原条款一致，实际银行结算不早于原有效起点。实际仓位以原银行结算时刻形成合同到期时间，保留原计划字节。执行时的当前安全、权限、资源和有效期重验保留；不增加权限缓存、不放宽已有到期或金额约束。

验证范围：跨请求推进一秒的真实购买、超过原上界的拒绝、原效果/计划/银行原件/回执与审计。三轮实际页面演示将使用自然推进的服务器时钟，不以固定测试时钟代替。

局部 GREEN 原件：`docs/progress/evidence/W1/stage-and-zero-target-red-20261005T013112Z-24e5afc5/output.log` 中这两节点 PASS；该批为 2 FAILED / 4 PASSED，保留完整失败状态。随后 `demo-runner-quote-direct-risks-20261005T022024Z-dfc45b4f` 两条固定购买节点均PASS；整批32 PASS / 4 FAIL，四项失败另行修复，未计初版验收。
