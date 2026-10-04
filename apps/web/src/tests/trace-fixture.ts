import type { components } from '../../../../packages/contracts/schema';
import { dashboardFixture } from './dashboard-fixture';
export const runId = '10000000-0000-0000-0000-000000000080';
export const actionId = '10000000-0000-0000-0000-000000000081';
export const evidenceId = '10000000-0000-0000-0000-000000000082';
export function traceFixture(): components['schemas']['DecisionTraceResponse'] {
  const user = dashboardFixture().user_id; const time = '2026-10-04T00:00:00Z'; const hash = 'a'.repeat(64);
  return { simulation: true, user_id: user, run_id: runId, as_of: time, read_at: time, completeness: 'COMPLETE', current_references: [
    { entity_type: 'EVIDENCE', entity_id: evidenceId, status: 'STATUS_CHANGED', original_status: 'VALID', current_status: 'SUPERSEDED' }],
    actions: [{ action_id: actionId, decision_run_id: runId, status: 'UNKNOWN', request_hash: hash, bank_operation_id: actionId, bank_status: 'UNKNOWN', receipt_id: null, receipt_status: null }],
    children: [], legacy_snapshot: null, legacy_result: null, audit_chain_status: 'VALID',
    explanation: { schema_version: 'decision-explanation-v1', simulation: true, run_id: runId, user_id: user, level: 'BLOCKED', financial_evaluation: 'REJECTED', confirmation_required: false, confirmation_satisfied: false,
      summary: ['单元HTTP夹具：资金约束未满足。'], reasons: [{ code: 'UNIT_UNKNOWN_REASON', text: '该原因尚无解释模板，保留原代码。', references: ['constraints[0].required_cents', 'candidates[0].reasons[0]'] }], audit_chain: 'NOT_IMPLEMENTED' },
    trace: { schema_version: 'decision-trace-v1', simulation: true, run_id: runId, user_id: user, phase: 'EVALUATION', as_of: time, action_id: null, parent_run_id: null,
      algorithm_versions: { autonomy: 'bounded-autonomy-v1' }, inputs: { amount_options_cents: [100, 200], autonomy_boundary: { snapshot: { cash_cents: 100000 } } },
      sources: [{ id: evidenceId, user_id: user, evidence_level: 'USER_DECLARED', source_type: 'UNIT_FALSE_CLAIM', source_ref: 'unit-source',
        content: { amount_cents: '错误金额声明', note: '<script>原文</script>' }, content_hash: hash, captured_content_hash: hash, content_integrity: 'VERIFIED', status_at_decision: 'VALID', observed_at: time, valid_from: time, valid_to: null, supersedes_evidence_id: null }],
      policies: [{ id: evidenceId, user_id: user, policy_id: evidenceId, version_number: 1, configuration: { type: 'emergency_buffer', name: '当时应急金', amount_cents: '坏声明' },
        configuration_hash: hash, captured_configuration_hash: 'b'.repeat(64), configuration_integrity: 'INVALID', status_at_decision: 'ACTIVE', confirmed_at: time, valid_from: time, valid_to: null }],
      constraints: Array.from({ length: 30 }, (_, index) => ({ constraint_key: `baseline:${index}:BEFORE_PAYMENT`, policy_version_id: null, is_hard: true,
        satisfied: index !== 0, required_cents: 100, available_cents: 90, due_date: '2026-10-05', calculation: { margin_cents: -10, protected_cents_by_reason: { emergency: 100 } }, reason_code: 'UNIT_CONSTRAINT' })),
      candidates: [{ candidate_key: 'unit-asset', kind: 'ASSET_ALLOCATION', status: 'REJECTED', inputs: {}, result: { max_allocatable_cents: 0 }, reasons: ['UNIT_UNKNOWN_REASON'] }],
      outcome: { decision: { level: 'BLOCKED', financial_evaluation: 'REJECTED' } }, input_hash: hash, trace_hash: hash }
  };
}
