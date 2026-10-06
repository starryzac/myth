# FULL-605 持久整组资产依赖重查

状态：新独立服务已实现；Root 生命周期接线、实际 PG 风险与原编号验收待完成。本包不改变既有整组资产、银行、审计或生命周期源码，不运行 PG、浏览器或金融动作。

## 可运行接口与接线

新增 `apps/api/app/services/full_asset_action_rechecks.py`，导出：

```python
recheck_full_asset_actions(
    session: Session,
    user_id: UUID,
    full_policy_id: UUID,
    epoch_id: UUID,
    now: datetime,
    lifecycle_command_id: UUID,
) -> tuple[list[UUID], list[UUID]]
```

Root 在 `full_policy_lifecycle._record_command` 的 `AssetAuthorizationPolicy` 分支中调用。调用发生在实际 User writer 锁和既有生命周期事务/savepoint 内，新版本/状态已产生，但新 FullPolicyCommand 行尚未插入。原键重放在 `_record_command` 前返回，必须继续跳过此函数。服务不提交、不建银行操作、不撤回银行操作，不更新原 Parent/Batch/Consent。迁移依赖为已存在 `0013` 的三个持久组合表。

返回值分别为本次真实新增 INVALIDATED 动作与需保留的在途/未核实原动作 ID；第二集合也包含缺原件、缺动作、身份或归属异常，不能将其都译为银行已受理。未改变 scope 的原动作和已严格验真的终态不在两个返回集合内。

## 核验与状态边界

当前策略必须为实际同用户、同 OPEN epoch 的 AssetAuthorizationPolicy；复用原 `_versions` 核完整不可变 Full 版本原件，另读不按 owner 过滤的全部 policy_id 版本分母。Full UUID 只与整组原 request 中的 Full UUID 比较，绝不传给原 MVP PolicyVersion 外键查询。此时不调用要求干净 RRRO 的 `read_full_policy`，也不假称已核尚未插入的新 command 链；此前命令链由原生命周期入口负责。

从当前原用户/epoch 的所有 Parent 与 Batch 建立真实关联分母。复用 `read_portfolio_original` 验 whole 原 request/hash、原冻结组合/hash、全部原批次/order/command/hash/目录绑定及 epoch；复用 `read_consent_original(current=False)` 保留原确认原件，不把已保留确认当当前许可。原 Full 版本配置/hash 必须等于冻结组合原配置/hash。无法证明的关联和孤立 Batch 保留在途；缺 Batch 时仍从完整、同身份/hash的原冻结组合保留原动作 ID，不减少原分母。

每个动作复用 `verify_original_batch_action` 验原 request/hash、marker、完整 BankCommand、原 ASK_ONCE 身份与完整 typed PREPARE 轨迹/审计；额外核实际 PREPARE phase、user/action/run、原 effect 与原捕获 context。使用原 `_claims` 构造器复核资源种类/键/金额完整分母、确定 UUID、owner、时间和状态，复用原收入账本读取与 `_reservation_matches`；收入 COMMITTED 对应资源 CONSUMED，缺收入原件仍未核实。审计采用原当前 epoch EXACT 核验。

独立关联查询涵盖全部 BankOperation、ActionReceipt、SimulatedBankPosting、SimulatedBankRedemption、ActionResourceReservation；关联查询不以 owner 条件筛掉异常行，posting 同时包含 operation_id/redemption_id 关联。任何银行行，包括 REJECTED，或任何回执/经济腿/旧赎回，都不能因应用 PLANNED 标签而释放原预留。SUBMITTED、UNKNOWN、异常状态及来源不全保持原 key、request/effect/hash 和 claims。

只有 scope/status/version/time 已变化、原 PLANNED/AUTHORIZED、完整零银行/回执/posting/redemption、原 claims 未消费且原 command 投影一致时，调用原 `_unsubmitted_command`、`_release_unsubmitted_claims`，真实转为 INVALIDATED，记录原 ACTION_STATE_CHANGED 和 NO_EFFECT exposure。资源释放后任何审计/投影错误向原调用者传播，既有生命周期事务必须回滚；不把异常吞成成功。无新的收入、银行 posting、回执或资金动作。

已 SETTLED 且应用 SUCCEEDED/RECONCILED 的终态还需完整已消费原 claims、唯一原 bank/receipt、同 owner 原 posting、无旧赎回，再调用原 `verify_execution_receipt` 核独立账本、完整原腿与原回执身份/金额/时间。未验真时保留在途。历史核验不要求已撤销的当前 Full 授权，不改原经济结果，不承诺多个银行操作整体回滚。

## 直接风险检查

新增 `apps/api/app/tests/test_full_asset_action_rechecks.py`，仅 SYNTHETIC_PROTOCOL_BOUNDARY/TOOL_ONLY。真实原冻结组合 reader、marker/command identity helper、原 claim 构造器及零银行 release helper运行；存储审计/trace 与终态银行 verifier 使用明确测试边界。它们不能证明 PG 提交、金融账本或经济效果。

已运行：

- `uv run --frozen pytest apps/api/app/tests/test_full_asset_action_rechecks.py -q -p no:cacheprovider --maxfail=1`：首轮夹具 `impact` 名称错误，1 ERROR / 5.83s，原源与失败记录保留；改为真实字段 `impact_analysis` 后，52 PASS / 8.19s / exit0。
- 补“缺持久 Batch 不缩原冻结动作分母”真实差量后，同模块 53 PASS / 8.99s / exit0。涵盖 revoke/新 Full UUID、未变 scope、SUBMITTED/UNKNOWN、REJECTED/ACCEPTED/SETTLED 行、跨 owner/孤立 posting/redemption、资源分母/金额/状态篡改、原 Parent/hash/marker/trace 缺口、严格 receipt 失败、孤立 Batch、缺收入与审计错误传播。
- 两文件 strict mypy、Ruff、format 检查通过。初始三长行/类型 `Base.user_id` 失败、测试谓词类型及 unused import 失败均保存于 `.runtime/FULL-605-asset-dependencies/`，未覆盖原失败。

本包检查只覆盖新增服务和直接风险夹具；没有全仓冻结或全量通过结论。

## 未覆盖与必要下一步骤

Root 尚需将精确接口接入 ASSET 生命周期新命令分支；保持原 replay 不执行重查及 `action_dependencies_supported=false` 的有限覆盖边界。建议唯一隔离实际 PG 验证：整组原 PLANNED/明确确认 AUTHORIZED 后撤销/变更，只失效未提交批；原 UNKNOWN/SETTLED 银行已付款而回执缺失时撤销，保原 key/claims 并按原固定批恢复；完整原 receipt 终态核验、真实审计 EXACT VALID、所有银行/Parent/Batch/Consent 原件逐行不变及原 command replay 零写。实际 Root 保留的资产 prepare500 失败与其他金融批失败不由本包重标或替代。

没有实际 PG 结果、全部生命周期并发/故障矩阵或集中全量验收，因此不关闭 FULL-605，也不声称所有 Full 模板依赖均已支持。
