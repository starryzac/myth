# FULL-605 整组资产权限撤销：独立真实候选

状态 PENDING / ACTUAL_PG_NOT_RUN；只交付可收集的真实原接口测试，不是金融效果或完整版验收。

新节点：apps/api/app/tests/test_actual_full_asset_revoke_integration.py::test_actual_full_asset_revoke_invalidates_unsubmitted_originals_without_money_effects。

复用原 annual_client 的 owned bf_test_UUID / 真实 Alembic seed、原实际银行 INCOME 摄入与目录登记、真实 FULL 资产声明和原 MVP 单独首次确认。仅此新节点固定 seed 后可信服务端当前 UTC，并检查唯一实际 owner OPEN epoch，旧 fixture/global NOW 不变。原47min UNKNOWN/同键恢复测试不修改、不并入此候选。

通过实际 /full-asset-executions/prepare 创建原整组 PLANNED 未提交子动作，原金额/hash/action/bank key 不由测试伪造。确认 bank_operations/action_receipts 为空、准备前后全部实际 accounts/transactions/bills/positions/goals/bank operations/receipts/postings/redemptions/external facts/resource reservations 及原 income/goal Evidence 完整字节口径相同。已摄入工资有真实旧 postings，因此比较全原表不新增；绝不把全局 postings 虚称零。

通过原 /full-policies/{id}/revoke，严格原 FullStateRequest 仅 expected_version_id/reason/idempotency_key（没有虚构 accepted/reviewed_hash 字段），要求 invalidated_action_ids 精确等于所有原 frozen child IDs、inflight 空、实际全部原 ActionPlan.status=INVALIDATED、原 bank keys 不变、最终 EXACT audit VALID。资金/收入/目标原件必须完全不变。随后原 revoke request/key 重放要求全35表物理快照零写；原 wholeHash/key/action 的 confirm/固定 execute-next 必须409且全表不变。

static/strict/format exit0；仅 collection 1 node/4.22s。真实金融未运行；此候选未证明 UNKNOWN 在途撤销后继续原键恢复（非低成本路径），原47min节点只证明未撤销的 UNKNOWN 恢复，不能拼成新撤销证明。当前scope单请求性能改进未实测。Root统一纳入 lifecycle605、307、两FullExecutionProtection及原审计篡改风险的最终冻结唯一链；本agent不启动PG。
