# FULL 已登记来源账户未来扣款保守下界

状态：新增纯生产域和直接风险测试已实现；strict2/Ruff2/format2 PASS。Root 同次真实 RRRO 来源生成及付款/银行消费尚未接线、PG NOT_RUN。本包不改变原 `FULL_FUTURE_ACCOUNT_DEBITS_NOT_PROVEN` 无证明负例、旧 FullProtection source_account_checks 的 future_account_debits_complete=false，亦不修改已 FINAL 的资产十一源或共享金融源码。

## 精确接缝与范围

`app.domain.full_registered_account_debits` 导出严格 `FullAccountDebitBoundsProof`、`derive_full_account_debit_bounds(effect, context, validation, projection_input, projection)`、`validate_full_account_debit_bounds_proof(proof, effect, context, validation, projection_input, projection)`。后三对象必须是同次实际已验证原执行的 projected_snapshot/projected_positions 和同一次 `project_full_protection(input)` 原结果，不收客户端事实或 permission。validator 再做纯线性派生并逐字段精确比较，不跑引擎/SQL，不跨请求缓存授权。

输入需原 user/effect/validation、业务时点与 timezone 完全一致；Full input 必须逐字段等于该次 projected_snapshot、原 versions/products、projected_positions、完整 raw claims，并且 projection.input_hash 精确为原 FullInput canonical hash。原 annual/full 两曲线均须严格 366 日×BEFORE_PAYMENT/AFTER_PAYMENT/AFTER_PRINCIPAL、1098 个点，连续日期/现金、margin 恒等式、原 Full input/curve hash、policy/source/Goal/account/claim 分母与本金 phase 原引用一致。完整额外保护、源 issue 或旧历史未验真均不被清除。

仅从每一天 BEFORE_PAYMENT→AFTER_PAYMENT 的原现金减少额求累计全部 MVP+Full 支出，保守把**全部支出归给每个已登记 periodic source account**，不猜 MVP 的具体支付账户。每个账户基数是同次原 validated post-effect cash，减其真实 Goalowned 和全部其他 claim；原 raw claims 与 exposure 同义预留逐账户取最大，不相加释放。Goalowned 与 claim 可能额外重叠扣除，明确是保守过估，不声称精确可用额。

逐日下界为该基数减累计注册支出。任何负数为 BLOCKED；缺满分母、hash/owner/来源/phase、未映射其他 Goal 或无当前 source account 为 UNKNOWN、金额 null；不是以空数组输出零或成功。其它账户现金、未来 income 与已核未来 principal 都不增加此下界：principal phase 保留核验出处但 credit=0。因此现在 SOURCE_SUFFICIENT 本身仍不够。证明只覆盖已登记当前 scope 的 365 天，不能证明未知未来收入/支出，也不能授予金融权限或替代原全局硬保护/原确认/账本核验。

输出绑定 effect/context/validation/projected_snapshot/Full input/完整 Full result/两 curve hash、user/asof/zone、原 policy/evidence/source-account 分母、每账户366日基数/每日扣款/累计扣款/最小下界及整个 proof_hash。始终 grants_authority=false、planning_only=true、account_allocation_is_exact=false、借其他账户/未来收入/未来本金 credits=0。已有 full.SourceChecks 原 false 字段不被重标签。

## 已执行必要检查及原失败

- 首纯 **18 PASS/1 FAIL6.12s**：多账户手算年期例错用了现有 helper 的十天 MVP policy，原 engine 只登记一月，因此实际 PASSED 合理。首日志/两源保留于 `.runtime/FULL-registered-account-debits/first-pure.log` 与 `first-pure-type-source/`。
- 仅将该 synthetic 场景明确登记原 MVP 持续年期，未改生产算法，受影响单节点 **1 PASS1.82s**。手算每月 Full10+Full10+MVP20，十二月加 dated60，总540分；A 原 projected700→下界160，B 原 projected500→下界−40，整体 BLOCKED，不借 A 去补 B。
- 首18已过节点包含 Goal300+raw claim100/exposure200 取max（账户700−300−200−120=80）、百万未来本金仍不能救700−720=−20，以及缺phase/day/hash/owner/source/原account/其他未映射Goal/coverage/claims/cashdelta/principal/mismatched input/result/sourceissue均 UNKNOWN/null，篡改 proof 或换 context 拒绝。
- 首两文件类型错误是循环局部 Optional 名字和测试 Optional snapshot，原日志 `first-types.log` 保留；仅窄修名字/收窄。首 Ruff I001保留 `first-ruff.log` 及原 source，随后最终 strict2/Ruff2/format2 PASS 分别 `final-types.log`、`final-ruff.log`、`final-format.log`。

这是分次18+受影响1的纯功能结果，不写为新的19项完整整批 PASS，没有金融或 PG 实证。下一由 Root fresh RRRO 服务实际生成同输入 typed proof，再在原 FullExecutionProtection 的已有 source-account UNKNOWN 分支只消费精确验真的证明；无证明仍保持原 UNKNOWN，proof BLOCKED/UNKNOWN 仍拒绝，完整全局保护/原金融权限/确认仍必须各自通过。
