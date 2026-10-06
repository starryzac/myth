/** TOOL_ONLY synthetic HTTP tests; the pure domain fixture is not PG/bank proof. */
import { createHash } from 'node:crypto';
import original from './full-joint-goal-execution-fixture.json';
import { actionFixture } from './demo-fixture';
import { spendingCanonicalJson } from '../api/spending-evidence';
import type { JointConfirm, JointIntent, JointLookup, JointPlan, JointPreview, JointResponse } from '../api/full-joint-goal-execution';
import type { LocalActorSession } from '../api/local-actor';
export const jointFixtureHash = (value: unknown) => createHash('sha256').update(spendingCanonicalJson(value), 'utf8').digest('hex');
export const jointFixtureId = (n: number) => `94000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
export function jointPreviewFixture(): JointPreview { return structuredClone(original.preview) as unknown as JointPreview; }
export function jointPlanFixture(): JointPlan { return jointPreviewFixture().plan!; }
export function jointActorFixture(): LocalActorSession { return structuredClone(original.actor_session) as LocalActorSession; }
export function jointConfirmFixture(): JointConfirm { return structuredClone(original.confirmation_content.original_request); }
export function jointResponseFixture(state: 'PLANNED' | 'CONFIRMED' | 'UNKNOWN' | 'FIRST_SETTLED' | 'SETTLED' | 'MISSING' | 'HISTORY' = 'PLANNED'): JointResponse {
  const plan = jointPlanFixture();
  const children = plan.children.map((child, index) => {
    const settled = state === 'SETTLED' || state === 'FIRST_SETTLED' && index === 0;
    const action = actionFixture(); action.user_id = plan.user_id; action.action_id = child.action_id; action.decision_run_id = jointFixtureId(100 + index); action.effect = structuredClone(child.command.effect); action.effect_hash = child.command.effect_hash; action.prepared_at = plan.prepared_at; action.as_of = plan.prepared_at; action.autonomy_level = 'ASK_ONCE'; action.status = settled ? 'SUCCEEDED' : state === 'UNKNOWN' && index === 0 ? 'UNKNOWN' : 'PLANNED'; action.bank_status = settled ? 'SETTLED' : state === 'UNKNOWN' && index === 0 ? 'UNKNOWN' : null; action.prepared_validation.effect_hash = child.command.effect_hash;
    action.receipt = settled ? { simulation: true, receipt_id: jointFixtureId(200 + index), action_id: child.action_id, bank_operation_id: jointFixtureId(300 + index), status: 'SUCCEEDED', executed_cents: child.command.effect.amount_cents, fee_cents: 0, loss_cents: 0, posting_ids: [jointFixtureId(400 + index), jointFixtureId(500 + index)], occurred_at: plan.prepared_at, reconciled_at: null } : null;
    return { child_number: child.child_number, action_id: child.action_id, bank_idempotency_key: child.bank_idempotency_key, state: settled ? 'ORIGINAL_RECEIPT_VERIFIED' : state === 'UNKNOWN' && index === 0 ? 'UNKNOWN' : state === 'MISSING' && index === 1 ? 'MISSING' : 'PLANNED_UNRESERVED', original_action: state === 'MISSING' && index === 1 ? null : action, original_request_hash: state === 'MISSING' && index === 1 ? null : 'd'.repeat(64) };
  });
  const content = structuredClone(original.confirmation_content), body = jointConfirmFixture(), evidenceId = jointFixtureId(20), evidenceHash = jointFixtureHash(content);
  const consent = { consent_id: jointFixtureId(21), user_id: plan.user_id, plan_id: plan.plan_id, epoch_id: plan.epoch_id, original_request: body, request_hash: jointFixtureHash(body), original_evidence: { id: evidenceId, user_id: plan.user_id, source_type: 'USER_JOINT_GOAL_CONFIRMATION', source_ref: plan.plan_id, evidence_level: 'USER_CONFIRMED_ACTION', content_hash: evidenceHash, content, status: 'VALID', observed_at: plan.prepared_at, valid_from: plan.prepared_at, valid_to: plan.expires_at }, evidence_id: evidenceId, evidence_hash: evidenceHash, current_evidence_verified: true, current_evidence_status: 'CURRENT_EVIDENCE_MATCHED', receipt_is_current_authority: false };
  return { simulation: true, bank_authority: false, current_authority_assessed: false, receipt_is_current_authority: false, economic_experiment_verified: false, funds_reserved: false, reservation_scope: 'WHOLE_UNRESERVED_CHILDREN_USE_ORIGINAL_CLAIMS', user_id: plan.user_id, epoch_id: plan.epoch_id, as_of: plan.prepared_at, original_plan: plan, original_request_hash: jointFixtureHash(plan.inputs.request), original_consent: ['CONFIRMED', 'UNKNOWN', 'FIRST_SETTLED', 'SETTLED'].includes(state) ? consent : null, children, state: state === 'HISTORY' ? 'RETAINED_HISTORY' : state === 'SETTLED' ? 'ORIGINAL_SERVICE_RECEIPTS_VERIFIED' : state === 'FIRST_SETTLED' ? 'PARTIALLY_SETTLED' : state === 'UNKNOWN' ? 'UNRESOLVED' : state === 'MISSING' ? 'PARTIALLY_PREPARED' : state === 'CONFIRMED' ? 'CONFIRMED_UNRESERVED' : 'PREPARED_UNRESERVED' } as unknown as JointResponse;
}
export function jointLookupFixture(intent: JointIntent, response = jointResponseFixture()): JointLookup {
  if (!('idempotency_key' in intent.body) || intent.kind === 'EXECUTE') throw new Error('TOOL_ONLY lookup expects a keyed original command');
  return { simulation: true, user_id: intent.user_id, idempotency_key: intent.body.idempotency_key, status: 'RECORDED', command_kind: intent.kind, original_request: structuredClone(intent.body), original_request_hash: intent.request_hash, original: response, not_found_is_final: false, bank_authority: false };
}
