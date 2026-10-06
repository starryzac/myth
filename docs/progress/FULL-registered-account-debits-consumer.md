# 原执行接入注册账户保守支出证明

2026-10-06：功能修订二的固定付款必要差量。不是 FULL-606 金融验收通过，原编号仍 PENDING。

`services/full_execution_protection.py` 在原用户写锁内开启新的 REPEATABLE READ READ ONLY 事务，读取当前完整 FULL 来源。针对已有原执行 validation 的实际 projected_snapshot 构造保护输入与结果，然后派生严格 `FullAccountDebitBoundsProof`。没有 HTTP 开关、客户端证明、跨请求缓存或新权限。

`domain/full_execution_protection.py` 新增可选 typed proof；原没有证明的周期支出负例仍 UNKNOWN。消费时使用自己重算的原 effect/context/validation/full input/full result 精确再派生比较全部证明。当前原账户现金扣目标归属、其他占用及每个账户承担全部登记最坏支出；未来收入和本金、其他账户借入全部为零。证明下界为负则 BLOCKED，覆盖/来源/关联不完整或旧证明则 UNKNOWN。证明 PASSED 后仍需全年聚合边界非负，原确认、权限、银行防重复与旧经济效果检查继续独立生效。

没有修改原 effect/result DTO、旧 canonical 字节、历史哈希或原授权判断。新增只在当前调用内消费证明，不把保守下界冒充精确未来账户预测。

实际模块检查：`evidence/W4/registered-account-bounds-consumer-direct-20261005T213512Z-e62baad4`，16 PASS / 3.78s（原12保护负例与新4消费风险），包含正总现金但单账户不足拒绝、当前占用改变拒绝、hash篡改拒绝和原确认仍要求；`evidence/W4/registered-account-bounds-consumer-types-20261005T213949Z-076f9dd0`，strict三源 exit0。

首多资产当前时钟诊断批 `actual-whole-asset-current-open-clock-20261005T213247Z-dadd11d5` 在 Root 改动这两个 shared 文件期间运行，实际相关来源发生变化；不能凭其 wrapper 未列这些文件时的 scoped=true 声称完整相关冻结。当前两源 HOLD，等待该批终态后格式和静态检查，再以完整相关源的单金融批验证资产及付款。当前实际付款、风险对账、605原键撤销恢复未取得新终态，不能用纯测试冒充银行成功。

## 终态后共享源格式与接线核对（2026-10-06 06:25）

资产session67815已退出0（诊断1PASS47min），Root才解除两FullProtection源HOLD，原未格式源与Main/dependencies/lifecycle精确保存在 .runtime/root-605-compiler-protection-before-20261005T2225Z。仅import排序/两源格式变化；原16语义检查复用，不新增重复全量。五related strict/static分别 W4/registered-account-proof-and-605-compiler-shared-final-types-20261005T222236Z-2d28d8bb / static-20261005T222236Z-3b827674，均exit0。当前实际606 AUTO/ASK仍未通过新proof节点；源证明模块通过不能替代真实账户消费实证。

## 2026-10-06 06:58 实际占用时点修复

W4/actual-fixed-payment-dynamic-goal-605-and-question-recovery-20261005T225228Z-94f466f7 原首 AUTO 节点 FAILED/20.42s（后六 NOT_RUN），真实prepare拒绝 OTHER_EXPOSURE_CLOCK_NOT_PROVEN；原失败精确保留 .runtime/root-606-actual-exposure-clock-red-20261005T2257Z/manifest.json。

原 `load_all_asset_exposure` 每次用全部当前账户/动作/持仓/回执/银行posting/operation/reservation/externalFact row-digest 重建原 statement 并核 canonical 原件、水印及原收据，允许原观察 epoch 不晚于当前请求时点。金融行在观察后改变会产生原 INVALID_ASSET_EXPOSURE/source issue，不能仅以时间差补零。新 account-debit 证明错误要求 epoch 等于当前clock；Root 改为未来 epoch 仍 UNKNOWN，过去 epoch 仅在有原 evidence 引用且原完整 verified execution context 无source问题时消费。原statement/content/hash/as_of不改，不发布新Evidence、不绕过完整原验证、不释放任何占用。claims仍 max(raw, originalExposure)。

38相关直接PASS/12.61s（3新时点风险+原19registered+4consumer+12protection，不与旧批相加）；strict2/Ruff2通过。原命令 durable-exposure-current-row-risks-20261005T225531Z-a7e94ef7、types-20261005T225532Z-8c93c5d8、static-20261005T225532Z-c0c78842。精确2源 FINAL .runtime/root-registered-account-exposure-clock-final-20261005T2258Z/manifest.json。实际 failed AUTO 原node已另开唯一重跑 session51121 / W4/actual-fixed-payment-durable-original-exposure-clock-repair-20261005T225659Z-ac2a3a0d，目前尚无终态，不先宣称银行通过。


2026-10-06 07:41 北京时间：固定付款原AUTO与ASK真实节点终态PASSED2/848.02s，wrapper851.602285s，W4/actual-fixed-payment-original-four-legs-auto-and-ask-20261005T231509Z-34a7fbee。相关scope稳定true，全源false仅独立新模块/UI/命令变化；原四条银行经济/负债分录、同原键恢复、完整审计与物理零写尾部已通过。此前prepare拒绝/expect3等FAILED及精确源保留，未改生产银行或旧记录。相关金融HOLD已解除，Root继续单一共享负责人。

