# FULL-604 已到期整仓的 USER 单次确认执行增量

2026-10-06：独立生产源已实现，原任务 **PENDING**。本包是当前已到期固定本金产品的单仓整仓候选，必须实际签名 Local USER 明确确认。旧批量 AUTO 到期流程、历史哈希、原报价、失败记录均保留。未启动 PostgreSQL 集成或真实浏览器。

## 能力和来源边界

新请求只接受 `policy_id / expected_version_id / expected_epoch_id / position_id / idempotency_key`；没有调用方金额、当前时钟、角色、回执、结果或权限字段。服务从完整当前 RR/RO 财务快照、全持仓分母、独立 BANK 本金链、不可变目录、原固定本金合同、当前已确认 FULL Recovery / 关联资产 / MVP 权限、当前目标及 365×3 保护曲线推导唯一整仓 `MATURE` 命令。原合同必须本金返还 10000bps、无续期、无费用、无损失；当前实现只覆盖结算延迟 0 的到期合同，不能把提前赎回更名为到期。

唯一允许的未对账例外是原 `UNRECONCILED_POSITION_AVAILABILITY:<本仓 UUID>`，并要求独立 BANK 完整链证明该仓原本金。该原 issue 和完整原快照仍冻结在输入。条件保护基线保守扣留该本金，条件结果仅向原现金账户/原 Goal 归属返还同一本金；完整 1098 检查点不得变差，剩余负点仍报告，预计返还不能称当前已到账现金。其它未知、完整保护不证明或分母缺项均拒绝。

`prepare` 在原用户锁/审计事务下建立 `ASSET_MATURITY / ASK_ONCE / PLANNED`、完整 PREPARE Trace 与明确 pending 曝光；原请求键推导唯一 BANK key 和 Action UUID。相同请求键只能回原 Action，异 body 冲突；没有自动新仓或新 key。`confirm` 写独立 `USER_CONFIRMED_ACTION / FULL_MATURITY_USER_CONSENT` 原 Evidence 和 CONFIRM Trace，原 Action 请求/hash 保持。首次 `execute` 重新核当前全部来源、原 USER 确认及完整合同，必须走原独立 simulated bank，再走原 `project_request / finalize_projections` 两腿及应用回执。银行已受理但回执丢失保原 `UNKNOWN`；原银行同 key 回读允许在后续策略撤销后恢复已受理原结果，不能成为新的受理授权。只有原实际服务回执验真才显示 `service_receipt_verified`，`economic_verified` 始终 false。

## Root 必须安装的原执行接缝

常量：`MARKER=full_maturity_execution`，`ALGORITHM=full-maturity-user-execution-v1`，`GUARDS_VERSION=full-maturity-user-guards-v1`，`VALIDATION_INFO_KEY=full_maturity_current_validation`。

1. 原 simulated bank **首次受理**的新 marker 分支必须 `ASK_ONCE` 且 `MATURE`，调用 `validate_current_maturity_bank_request(session, action, command, now)`；旧无 marker AUTO 逻辑不变，已有原银行请求同 key 回读路径不变。
2. 原 asset exposure importer 对 marker 识别完整原请求，再调用 `validate_maturity_exposure_original(...)`。只在完整无银行请求/回执/操作/本金变动/预留冲突下返回 true，并保持全部 Actions 分母。存在原银行记录返回 false，继续原两腿/回执核验；未知来源不能当 no effect。
3. 原 bank/importer 均须发布同 `FULL_MATURITY_GUARDS_VERSION`，否则新 prepare 明确 `FULL_MATURITY_NOT_IMPLEMENTED`。
4. 原历史算法分支调用纯 `verify_frozen_maturity_trace(trace)`，覆盖 PREPARE / CONFIRM / BANK_ACCEPT；不调用服务 `_frozen`、DB、当前来源或递归审计。
5. 原 Main 注册新 router，并为 preview / GET 保留 clean `REPEATABLE READ / READ ONLY`。当前新文件尚未代替 Root 注册和实际 schema 生成。

银行 guard 每次先 pop 旧 info，再 fresh 全部实际来源核验，向同一写事务 info 写 **一次性证据传递**，不是授权缓存。原 recorder 在 guard 之后必须 pop，严格核 action.id / 当前 as_of；缺失或身份不匹配拒绝。

```text
validation_inputs.full_maturity_validation = {
  action_id: UUID string,
  inputs: FullMaturityInput 原完整 JSON,
  proof: derive_maturity_proof(inputs, 原 BankRequest) 原 JSON,
  original_consent: FullMaturityConsent 原 JSON,
  consent_source: 原完整 TraceEvidence JSON
}
```

闭合对象不可增减字段。BANK_ACCEPT 原 inputs 保留 `action_request`、`bank_request`、旧 `validation_inputs` 和旧 context 原件；sources 按 id union 旧实际来源、`inputs.source_originals`、`consent_source`，同 id 的原副本必须相同。新算法版本绑定 marker；run `UUID5(action, legacy-bank-accept-decision)`、parent `UUID5(action, maturity-user-prepare)`。新 outcome 必须 `autonomy_level=ASK_ONCE / new_authority=false / settlement_kind=ORIGINAL_CONTRACT / bank_validation_status=READY`。独立当前证明重算完整 inputs/proof，不能沿用旧 AUTO 权限语义。

## 实际直接检查

- `maturity-user-history-and-json-risk-20261006T040611Z-e29664fe`：40 个 pure / actual FastAPI JSON 解析直接风险 PASS，11.14s，0 skip。它们使用明确 synthetic 输入/读源 doubles，不是金融成功证明；owned scope 稳定，global 因独立 action-set 新测试变化为 false。
- `maturity-user-six-final-strict-20261006T040611Z-8041bffd`：六个 owned 源 `mypy --strict --follow-imports=silent` PASS；此范围不会伪装为整个后台类型验收。
- `maturity-user-six-final-static-20261006T040611Z-3e973d28`：六源 Ruff PASS。
- 早期 19 / 26 pure 通过和 types / format 的全部失败日志与修改前源保留于 `.runtime/FULL-604-maturity/` 与各原 evidence 目录；后续通过不改原件。

原实际集成候选：`app/tests/test_full_maturity_execution_integration.py::test_actual_mature_whole_user_ask_unknown_key_revoke_recovery_and_tamper`。只收集，**NOT_RUN**。候选使用隔离 owned bf_test 原 seed/收入/固定存款购买/真实 USER cookie，完整物理表证明 preview 零写、同 key 重放零写、无确认拒绝、实际银行提交后响应丢失 UNKNOWN、两腿守恒、撤销后原 key 恢复、重复恢复零写和确认原件篡改拒绝。没有运行金融来替该候选作成功标签。

## 具体未覆盖

共享执行接缝/历史验真注册/Main/schema 仍待 Root 安装后实证；原真正银行及全流程 PostgreSQL 节点未运行。固定合同 settlement delay >0、部分本金、有损/费用、收益支付、原子多仓、自动推进下一仓均未支持。SEALED epoch 的 archived audit 回读适配尚未证明，当前原 `get_decision_trace` 依赖审计能力；不能宣称跨封存历史成功。接口消费者/原请求持久 UI、实际浏览器、最终全量、性能及 FULL604 原全部验收仍待完成。
