/** TOOL_ONLY protocol fixtures. No DB, bank, browser or delivery measurement. */
import { createHash } from 'node:crypto';
import type { components } from '../../../../packages/contracts/schema';
import { interventionCanonicalJson, interventionCommand } from '../api/interventions';
import type { Intervention, InterventionDelivery, InterventionIntent, InterventionList, InterventionLookup, InterventionResult, ObserveBody } from '../api/interventions';
import { questionEpoch, questionResponseFixture, questionRevisionFixture, questionRun, questionSession, questionUser } from './question-fixture';
import { traceFixture } from './trace-fixture';

export const interventionUser = questionUser;
export const interventionEpoch = questionEpoch;
export const interventionId = '20000000-0000-4000-8000-000000000001';
export const interventionInboxId = '20000000-0000-4000-8000-000000000002';
export const interventionAsOf = '2026-10-05T12:00:00Z';
export const interventionHashFixture = (value: unknown) => createHash('sha256').update(interventionCanonicalJson(value), 'utf8').digest('hex');
export function observeBodyFixture(key = 'TOOL_ONLY_OBSERVE_001'): ObserveBody { return { kind: 'QUESTION', session_id: questionSession, expected_revision: 1, expected_run_id: questionRun, reviewed_source_trace_hash: 'a'.repeat(64), expected_epoch_id: interventionEpoch, intervention_policy_id: null, idempotency_key: key }; }
export function interventionFixture(claimed = false): Intervention {
  const message: Intervention['original_message'] = { protocol: 'full-intervention-message-v1', message_id: interventionId, user_id: interventionUser, epoch_id: interventionEpoch, source_kind: 'QUESTION', source_run_id: questionRun, source_trace_hash: 'a'.repeat(64), semantic_key: 'b'.repeat(64), creation_command_run_id: '20000000-0000-4000-8000-000000000003', session_id: questionSession, question_revision: 1, question: questionRevisionFixture().pending_question as Intervention['original_message']['question'], boundary_observation: null, intervention_policy_binding: null, requires_user_attention: true, created_at: interventionAsOf, bank_authority: false, answers_question: false, execution_eligible: false, global_action_set_complete: false };
  const payload_hash = interventionHashFixture(message);
  return { simulation: true, original_message: message, payload_hash, stored_state: 'PENDING', effective_state: 'PENDING', source_status: 'CURRENT', current_source_binding: 'ORIGINAL_MESSAGE', current_question_observation: null, available_at: interventionAsOf, pending: true, previously_claimed: claimed, original_inbox_claim: claimed ? { inbox_id: interventionInboxId, user_id: interventionUser, message_id: interventionId, epoch_id: interventionEpoch, consumer_ref: 'intervention-center-v1', payload_hash, state: 'RECEIVED', created_at: interventionAsOf, received_at: interventionAsOf, actual_human_view_verified: false } : null, current_question: structuredClone(message.question), original_acknowledgment: null, authority_granted: false, answers_question: false, execution_eligible: false, dedicated_audit_event: false, global_boundary_subscription: 'NOT_IMPLEMENTED' };
}
/** A new original OBSERVE binds an equivalent current question to the immutable
 * historical message. All UUID/hash values here remain synthetic TOOL_ONLY. */
export function interventionCurrentObservationFixture(claimed = false): Intervention {
  const view = interventionFixture(claimed);
  const sessionId = '20000000-0000-4000-8000-000000000010';
  const sourceId = '20000000-0000-4000-8000-000000000011';
  const intent = interventionIntentFixture('OBSERVE', 'TOOL_ONLY_CURRENT_OBSERVATION');
  checkQuestion(intent.body);
  intent.body.session_id = sessionId; intent.body.expected_revision = 2; intent.body.expected_run_id = sourceId; intent.body.reviewed_source_trace_hash = 'c'.repeat(64);
  intent.body_json = JSON.stringify(intent.body); intent.request_hash = interventionHashFixture(interventionCommand(intent));
  const originalReceipt = interventionReceiptFixture(intent); originalReceipt.duplicate_semantics = true;
  const currentQuestion = structuredClone(view.original_message.question)!; currentQuestion.question_id = '20000000-0000-4000-8000-000000000012';
  view.current_question = currentQuestion; view.current_source_binding = 'CURRENT_OBSERVATION';
  view.current_question_observation = { observation_run_id: '20000000-0000-4000-8000-000000000013', observation_trace_hash: 'd'.repeat(64), original_receipt: originalReceipt, session_id: sessionId, revision: 2, source_run_id: sourceId, source_trace_hash: 'c'.repeat(64), semantic_key: view.original_message.semantic_key, current_question: structuredClone(currentQuestion), authority_granted: false, execution_eligible: false };
  return view;
}
function checkQuestion(body: InterventionIntent['body']): asserts body is components['schemas']['QuestionObservationRequest'] {
  if (!('kind' in body) || body.kind !== 'QUESTION') throw new Error('TOOL_ONLY_NOT_QUESTION');
}
export function interventionIntentFixture(kind: InterventionIntent['kind'] = 'OBSERVE', key = `TOOL_ONLY_${kind}_001`): InterventionIntent {
  const view = interventionFixture(); const body = kind === 'OBSERVE' ? observeBodyFixture(key) : { expected_epoch_id: interventionEpoch, reviewed_payload_hash: view.payload_hash, ...(kind === 'ACKNOWLEDGE' ? { idempotency_key: key, acknowledged: true as const } : {}) };
  const partial = { kind, user_id: interventionUser, message_id: kind === 'OBSERVE' ? null : interventionId, body };
  return { ...partial, protocol: 'intervention-browser-command-v1', path: kind === 'OBSERVE' ? '/interventions/observe' : `/interventions/${interventionId}/${kind === 'DELIVER' ? 'deliveries' : 'acknowledgements'}`, body_json: JSON.stringify(body), request_hash: interventionHashFixture(interventionCommand(partial)) };
}
export function interventionReceiptFixture(intent: InterventionIntent): components['schemas']['InterventionReceipt'] {
  if (intent.kind === 'DELIVER' || !('idempotency_key' in intent.body)) throw new Error('TOOL_ONLY_DELIVER_HAS_NO_COMMAND_RECEIPT');
  return { protocol: 'full-intervention-command-v1', kind: intent.kind, user_id: interventionUser, epoch_id: interventionEpoch, idempotency_key: intent.body.idempotency_key, request_hash: intent.request_hash, original_command: structuredClone(interventionCommand(intent)), message_id: interventionId, payload_hash: interventionFixture().payload_hash, recorded_at: interventionAsOf, duplicate_semantics: false, authority_granted: false, execution_eligible: false };
}
export function interventionResultFixture(intent: InterventionIntent, view = interventionFixture()): InterventionResult {
  if (intent.kind === 'ACKNOWLEDGE') { view = interventionFixture(true); view.stored_state = 'ACKNOWLEDGED'; view.effective_state = 'ACKNOWLEDGED'; view.pending = false; view.original_inbox_claim!.state = 'ACKNOWLEDGED'; view.original_acknowledgment = interventionReceiptFixture(intent); }
  return { simulation: true, original_receipt: interventionReceiptFixture(intent), message: view, replayed_original_receipt: false, authority_granted: false, execution_eligible: false };
}
export function interventionLookupFixture(intent: InterventionIntent, found = true, view?: Intervention): InterventionLookup {
  if (!('idempotency_key' in intent.body)) throw new Error('TOOL_ONLY_DELIVER_HAS_NO_KEY');
  const result = found ? interventionResultFixture(intent, view) : null;
  return { simulation: true, status: found ? 'RECORDED' : 'NOT_FOUND_NOT_FINAL', epoch_id: interventionEpoch, idempotency_key: intent.body.idempotency_key, original_receipt: result?.original_receipt ?? null, message: result?.message ?? null, authority_granted: false, replacement_allowed: false };
}
export function interventionDeliveryFixture(): InterventionDelivery { return { simulation: true, message: interventionFixture(true), inbox_id: interventionInboxId, original_received_at: interventionAsOf, present_once: true, actual_human_view_verified: false, authority_granted: false, answers_question: false }; }
export function interventionListFixture(items = [interventionFixture()]): InterventionList { return { simulation: true, items, actual_message_count: items.length, complete_inventory: true, presentation_truncated: false, authority_granted: false }; }
export function interventionQuestionFixture() { return questionResponseFixture(); }
export function interventionTraceFixture() {
  const value = traceFixture(); const current = questionRevisionFixture(); value.user_id = interventionUser; value.run_id = questionRun; value.as_of = current.as_of; value.read_at = current.as_of; value.current_references = []; value.actions = [];
  value.explanation!.user_id = interventionUser; value.explanation!.run_id = questionRun;
  const trace = value.trace!; trace.user_id = interventionUser; trace.run_id = questionRun; trace.as_of = current.as_of; trace.sources = []; trace.policies = []; trace.constraints = []; trace.candidates = []; trace.inputs = {}; trace.outcome = { question_revision: current }; trace.trace_hash = 'a'.repeat(64); return value;
}
