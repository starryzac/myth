import type { components } from '../../../../packages/contracts/schema';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { request } from './http';
import { spendingCanonicalJson as canonical, spendingDigest as digest, spendingHash as hash, spendingUUID as uuid } from './spending-evidence';
import { getQuestionSession } from './question-workflow';
import { getDecision } from './decisions';

export type Intervention = components['schemas']['InterventionView'];
export type InterventionList = components['schemas']['InterventionList'];
export type InterventionLookup = components['schemas']['InterventionCommandLookup'];
export type InterventionResult = components['schemas']['InterventionCommandResponse'];
export type InterventionDelivery = components['schemas']['InterventionDeliveryResponse'];
export type ObserveBody = components['schemas']['QuestionObservationRequest'] | components['schemas']['app__domain__full_intervention__BoundaryObservationRequest'];
export type DeliverBody = components['schemas']['DeliveryRequest'];
export type AckBody = components['schemas']['AcknowledgmentRequest'];
export type InterventionIntent = {
  protocol: 'intervention-browser-command-v1'; kind: 'OBSERVE' | 'DELIVER' | 'ACKNOWLEDGE';
  user_id: string; message_id: string | null; path: string;
  body: ObserveBody | DeliverBody | AckBody; body_json: string; request_hash: string;
};
const keys = (value: Record<string, unknown>, fields: string[]) => Object.keys(value).sort().join('|') === [...fields].sort().join('|');
const clock = (value: unknown) => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const states = ['PENDING', 'ACKNOWLEDGED', 'INVALIDATED', 'RECORDED_ONLY', 'DEFERRED'];
function check(value: unknown): asserts value { if (!value) throw new Error('通知原件、用户、周期或原命令不一致，保留原请求。'); }
const originals = new WeakMap<object, string>();
export const getOriginalInterventionResponse = (value: object) => originals.get(value) ?? null;
const retain = <T extends object>(value: T, raw?: string): T => { if (raw !== undefined) originals.set(value, raw); return value; };
export const interventionCanonicalJson = canonical;
export const interventionHash = hash;

export function parseInterventionBody(kind: InterventionIntent['kind'], value: unknown): InterventionIntent['body'] {
  check(object(value) && uuid(value.expected_epoch_id));
  if (kind === 'DELIVER') check(keys(value, ['expected_epoch_id', 'reviewed_payload_hash']) && digest(value.reviewed_payload_hash));
  else if (kind === 'ACKNOWLEDGE') check(keys(value, ['expected_epoch_id', 'reviewed_payload_hash', 'idempotency_key', 'acknowledged']) && digest(value.reviewed_payload_hash) && value.acknowledged === true);
  else {
    check(typeof value.idempotency_key === 'string' && (value.intervention_policy_id === null || uuid(value.intervention_policy_id)) && digest(value.reviewed_source_trace_hash));
    if (value.kind === 'QUESTION') check(keys(value, ['kind', 'session_id', 'expected_revision', 'expected_run_id', 'reviewed_source_trace_hash', 'expected_epoch_id', 'intervention_policy_id', 'idempotency_key']) && uuid(value.session_id) && uuid(value.expected_run_id) && Number.isSafeInteger(value.expected_revision) && Number(value.expected_revision) >= 1 && Number(value.expected_revision) <= 16);
    else check(value.kind === 'SINGLE_ACTION_BOUNDARY' && keys(value, ['kind', 'observation_run_id', 'reviewed_source_trace_hash', 'expected_epoch_id', 'intervention_policy_id', 'idempotency_key']) && uuid(value.observation_run_id));
  }
  if (kind !== 'DELIVER') check(typeof value.idempotency_key === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(value.idempotency_key));
  return value as InterventionIntent['body'];
}

export function interventionCommand(intent: Pick<InterventionIntent, 'kind' | 'user_id' | 'message_id' | 'body'>) {
  return { kind: intent.kind, user_id: intent.user_id, ...(intent.kind === 'OBSERVE' ? {} : { message_id: intent.message_id }), request: intent.body };
}
function question(value: unknown) {
  check(object(value) && keys(value, ['question_id', 'variable_id', 'bank_authority', 'choices', 'worst_residual_signature_count', 'affected_action_types']) && Array.isArray(value.affected_action_types) && value.affected_action_types.every((item) => typeof item === 'string'));
  check(object(value) && uuid(value.question_id) && typeof value.variable_id === 'string' && value.bank_authority === false && Array.isArray(value.choices) && value.choices.length >= 2 && value.choices.length <= 8 && Number.isSafeInteger(value.worst_residual_signature_count) && Number(value.worst_residual_signature_count) >= 1 && Array.isArray(value.affected_action_types));
  for (const choice of value.choices) {
    check(object(choice) && typeof choice.key === 'string' && object(choice.value));
    if (choice.value.kind === 'money') check(Number.isSafeInteger(choice.value.amount_cents) && Number(choice.value.amount_cents) > 0);
    else if (choice.value.kind === 'account') check(uuid(choice.value.account_id));
    else check(choice.value.kind === 'intent' && object(choice.value.intent));
  }
  check(new Set(value.choices.map((row) => (row as { key: string }).key)).size === value.choices.length);
}
function receipt(value: unknown) {
  check(object(value) && keys(value, ['protocol', 'kind', 'user_id', 'epoch_id', 'idempotency_key', 'request_hash', 'original_command', 'message_id', 'payload_hash', 'recorded_at', 'duplicate_semantics', 'authority_granted', 'execution_eligible']));
  check(object(value) && value.protocol === 'full-intervention-command-v1' && ['OBSERVE', 'ACKNOWLEDGE'].includes(value.kind as string) && uuid(value.user_id) && uuid(value.epoch_id) && uuid(value.message_id) && digest(value.payload_hash) && digest(value.request_hash) && clock(value.recorded_at) && typeof value.duplicate_semantics === 'boolean' && value.authority_granted === false && value.execution_eligible === false && object(value.original_command));
  const command = value.original_command;
  check(command.kind === value.kind && command.user_id === value.user_id);
  const body = parseInterventionBody(value.kind as 'OBSERVE' | 'ACKNOWLEDGE', command.request);
  check(body.expected_epoch_id === value.epoch_id && 'idempotency_key' in body && body.idempotency_key === value.idempotency_key);
  if (value.kind === 'OBSERVE') check(keys(command, ['kind', 'user_id', 'request']));
  else check(keys(command, ['kind', 'user_id', 'message_id', 'request']) && command.message_id === value.message_id && 'reviewed_payload_hash' in body && body.reviewed_payload_hash === value.payload_hash);
}

/** The server verifies the complete worlds and audit chain. This consumer checks
 * exact DTO bindings and immutable command bytes, without re-running that engine. */
function currentObservation(value: unknown, original: Record<string, unknown>, payloadHash: unknown): void {
  check(object(value) && keys(value, ['observation_run_id', 'observation_trace_hash', 'original_receipt', 'session_id', 'revision', 'source_run_id', 'source_trace_hash', 'semantic_key', 'current_question', 'authority_granted', 'execution_eligible']));
  check(uuid(value.observation_run_id) && digest(value.observation_trace_hash) && uuid(value.session_id) && Number.isSafeInteger(value.revision) && Number(value.revision) >= 1 && Number(value.revision) <= 16 && uuid(value.source_run_id) && digest(value.source_trace_hash) && value.semantic_key === original.semantic_key && value.authority_granted === false && value.execution_eligible === false);
  question(value.current_question);
  receipt(value.original_receipt); check(object(value.original_receipt));
  const recorded = value.original_receipt;
  check(recorded.kind === 'OBSERVE' && recorded.user_id === original.user_id && recorded.epoch_id === original.epoch_id && recorded.message_id === original.message_id && recorded.payload_hash === payloadHash && Date.parse(String(recorded.recorded_at)) >= Date.parse(String(original.created_at)) && object(recorded.original_command));
  const body = recorded.original_command.request;
  check(object(body) && body.kind === 'QUESTION' && body.session_id === value.session_id && body.expected_revision === value.revision && body.expected_run_id === value.source_run_id && body.reviewed_source_trace_hash === value.source_trace_hash);
  // Equivalent current questions may have new UUIDs. These finite question
  // fields must still match; semantic_key is the server's full-world report.
  check(object(original.question) && object(value.current_question));
  const { question_id: originalId, ...originalFields } = original.question;
  const { question_id: currentId, ...currentFields } = value.current_question;
  check(uuid(originalId) && uuid(currentId) && canonical(originalFields) === canonical(currentFields));
}

export function parseIntervention(value: unknown, expectedId?: string, raw?: string): Intervention {
  check(object(value) && value.simulation === true && value.authority_granted === false && value.answers_question === false && value.execution_eligible === false && value.dedicated_audit_event === false && value.global_boundary_subscription === 'NOT_IMPLEMENTED');
  assertMoneyFields(value);
  const original = value.original_message;
  check(object(original) && original.protocol === 'full-intervention-message-v1' && uuid(original.message_id) && (!expectedId || original.message_id === expectedId) && uuid(original.user_id) && uuid(original.epoch_id) && uuid(original.source_run_id) && uuid(original.creation_command_run_id) && digest(original.source_trace_hash) && digest(original.semantic_key) && digest(value.payload_hash) && clock(original.created_at) && original.bank_authority === false && original.answers_question === false && original.execution_eligible === false && original.global_action_set_complete === false);
  if (original.source_kind === 'QUESTION') { check(uuid(original.session_id) && Number.isSafeInteger(original.question_revision) && Number(original.question_revision) >= 1 && original.requires_user_attention === true && original.boundary_observation === null); question(original.question); }
  else check(original.source_kind === 'SINGLE_ACTION_BOUNDARY' && original.session_id === null && original.question_revision === null && original.question === null && object(original.boundary_observation) && typeof original.requires_user_attention === 'boolean');
  check(states.includes(value.stored_state as string) && [...states, 'UNKNOWN', 'ARCHIVED'].includes(value.effective_state as string) && ['CURRENT', 'STALE', 'UNKNOWN', 'ARCHIVED'].includes(value.source_status as string) && clock(value.available_at) && typeof value.pending === 'boolean' && typeof value.previously_claimed === 'boolean');
  check(!value.pending || value.effective_state === 'PENDING' && value.source_status === 'CURRENT' && original.requires_user_attention === true);
  if (value.current_question !== null) question(value.current_question);
  check(['ORIGINAL_MESSAGE', 'CURRENT_OBSERVATION', 'LEGACY_TERMINAL_SOURCE', 'UNVERIFIED'].includes(value.current_source_binding as string) && (value.current_question_observation === null || object(value.current_question_observation)));
  if (value.current_question_observation !== null) {
    check(original.source_kind === 'QUESTION' && value.source_status === 'CURRENT' && ['CURRENT_OBSERVATION', 'LEGACY_TERMINAL_SOURCE'].includes(value.current_source_binding as string));
    currentObservation(value.current_question_observation, original, value.payload_hash);
    check(canonical(value.current_question) === canonical(value.current_question_observation.current_question));
  }
  if (value.current_source_binding === 'CURRENT_OBSERVATION') check(value.current_question_observation !== null && value.source_status === 'CURRENT');
  else if (value.current_source_binding === 'ORIGINAL_MESSAGE') {
    check(value.current_question_observation === null && value.source_status === 'CURRENT');
    if (original.source_kind === 'QUESTION') check(value.current_question !== null && canonical(value.current_question) === canonical(original.question));
  } else if (value.current_source_binding === 'LEGACY_TERMINAL_SOURCE') check(original.source_kind === 'QUESTION' && value.stored_state === 'INVALIDATED' && value.effective_state === 'INVALIDATED' && value.pending === false);
  else check(value.current_question_observation === null && value.source_status !== 'CURRENT');
  if (original.source_kind === 'SINGLE_ACTION_BOUNDARY') check(value.current_question === null);
  const claim = value.original_inbox_claim;
  check(value.previously_claimed === (claim !== null));
  if (claim !== null) check(object(claim) && uuid(claim.inbox_id) && claim.user_id === original.user_id && claim.message_id === original.message_id && claim.epoch_id === original.epoch_id && claim.consumer_ref === 'intervention-center-v1' && claim.payload_hash === value.payload_hash && ['RECEIVED', 'ACKNOWLEDGED', 'INVALIDATED'].includes(claim.state as string) && clock(claim.created_at) && clock(claim.received_at) && Date.parse(String(claim.received_at)) >= Date.parse(String(claim.created_at)) && claim.actual_human_view_verified === false);
  if (value.original_acknowledgment !== null) {
    receipt(value.original_acknowledgment); const ack = value.original_acknowledgment as Record<string, unknown>;
    check(ack.kind === 'ACKNOWLEDGE' && ack.user_id === original.user_id && ack.message_id === original.message_id && ack.epoch_id === original.epoch_id && ack.payload_hash === value.payload_hash && value.stored_state === 'ACKNOWLEDGED' && object(claim) && claim.state === 'ACKNOWLEDGED');
  } else check(value.stored_state !== 'ACKNOWLEDGED');
  return retain(value as Intervention, raw);
}
export async function verifyInterventionHash(view: Intervention): Promise<void> {
  check(await hash(view.original_message) === view.payload_hash);
  if (view.original_acknowledgment) check(await hash(view.original_acknowledgment.original_command) === view.original_acknowledgment.request_hash);
  if (view.current_question_observation) check(await hash(view.current_question_observation.original_receipt.original_command) === view.current_question_observation.original_receipt.request_hash);
}
export function parseInterventionList(value: unknown, raw?: string): InterventionList {
  check(object(value) && value.simulation === true && value.authority_granted === false && value.complete_inventory === true && Number.isSafeInteger(value.actual_message_count) && Number(value.actual_message_count) >= 0 && Number(value.actual_message_count) <= 512 && Array.isArray(value.items) && value.items.length <= Number(value.actual_message_count) && typeof value.presentation_truncated === 'boolean' && value.presentation_truncated === (value.items.length < Number(value.actual_message_count)));
  const views = value.items.map((row) => parseIntervention(row));
  check(new Set(views.map((row) => row.original_message.message_id)).size === views.length && new Set(views.map((row) => row.original_message.user_id)).size <= 1);
  return retain(value as InterventionList, raw);
}
export function parseInterventionLookup(value: unknown, intent: InterventionIntent, raw?: string): InterventionLookup {
  check(intent.kind !== 'DELIVER' && object(value) && value.simulation === true && ['RECORDED', 'NOT_FOUND_NOT_FINAL'].includes(value.status as string) && value.authority_granted === false && value.replacement_allowed === false && value.epoch_id === intent.body.expected_epoch_id && 'idempotency_key' in intent.body && value.idempotency_key === intent.body.idempotency_key);
  if (value.status === 'NOT_FOUND_NOT_FINAL') check(value.original_receipt === null && value.message === null);
  else {
    receipt(value.original_receipt); check(object(value.original_receipt)); const original = value.original_receipt;
    check(original.kind === intent.kind && original.user_id === intent.user_id && original.request_hash === intent.request_hash && canonical(original.original_command) === canonical(interventionCommand(intent)));
    const view = parseIntervention(value.message);
    check(view.original_message.user_id === intent.user_id && view.original_message.epoch_id === intent.body.expected_epoch_id && original.message_id === view.original_message.message_id && original.payload_hash === view.payload_hash);
    if (intent.kind === 'ACKNOWLEDGE') check(original.message_id === intent.message_id);
  }
  return retain(value as InterventionLookup, raw);
}
export async function listInterventions(): Promise<InterventionList> {
  const value = await request('/interventions?limit=512', 'GET', undefined, parseInterventionList);
  await Promise.all(value.items.map(verifyInterventionHash)); return value;
}
export async function getIntervention(id: string): Promise<Intervention> {
  check(uuid(id)); const value = await request(`/interventions/${id}`, 'GET', undefined, (input, raw) => parseIntervention(input, id, raw));
  await verifyInterventionHash(value); return value;
}
export async function lookupIntervention(intent: InterventionIntent): Promise<InterventionLookup> {
  check(intent.kind !== 'DELIVER' && 'idempotency_key' in intent.body);
  const value = await request(`/interventions/commands/${intent.body.expected_epoch_id}/by-key/${encodeURIComponent(intent.body.idempotency_key)}`, 'GET', undefined, (input, raw) => parseInterventionLookup(input, intent, raw));
  if (value.original_receipt) { check(await hash(value.original_receipt.original_command) === intent.request_hash); check(value.message); await verifyInterventionHash(value.message); }
  return value;
}
export async function postIntervention(intent: InterventionIntent): Promise<InterventionResult | InterventionDelivery> {
  parseInterventionBody(intent.kind, intent.body);
  check(uuid(intent.user_id) && intent.body_json === JSON.stringify(intent.body) && await hash(interventionCommand(intent)) === intent.request_hash);
  check(intent.kind === 'OBSERVE' ? intent.message_id === null && intent.path === '/interventions/observe' : uuid(intent.message_id) && intent.path === `/interventions/${intent.message_id}/${intent.kind === 'DELIVER' ? 'deliveries' : 'acknowledgements'}`);
  const value = await request(intent.path, 'POST', intent.body, (input, raw) => {
    check(object(input) && input.simulation === true && input.authority_granted === false);
    const view = parseIntervention(input.message);
    check(view.original_message.user_id === intent.user_id && view.original_message.epoch_id === intent.body.expected_epoch_id);
    if (intent.kind === 'DELIVER') check(view.original_message.message_id === intent.message_id && 'reviewed_payload_hash' in intent.body && view.payload_hash === intent.body.reviewed_payload_hash && object(view.original_inbox_claim) && input.inbox_id === view.original_inbox_claim.inbox_id && input.original_received_at === view.original_inbox_claim.received_at && typeof input.present_once === 'boolean' && input.actual_human_view_verified === false && input.answers_question === false);
    else { receipt(input.original_receipt); check(object(input.original_receipt) && input.execution_eligible === false && typeof input.replayed_original_receipt === 'boolean' && input.original_receipt.request_hash === intent.request_hash && canonical(input.original_receipt.original_command) === canonical(interventionCommand(intent)) && input.original_receipt.message_id === view.original_message.message_id && input.original_receipt.user_id === intent.user_id && input.original_receipt.epoch_id === intent.body.expected_epoch_id && input.original_receipt.payload_hash === view.payload_hash); }
    return retain(input as InterventionResult | InterventionDelivery, raw);
  });
  await verifyInterventionHash(value.message); return value;
}

/** Current actual GETs supply identity and trace hash; no caller result or amount is accepted. */
export async function readQuestionObservation(sessionId: string, key: string): Promise<{ user_id: string; body: ObserveBody }> {
  const workflow = await getQuestionSession(sessionId); const current = workflow.current_revision;
  check(workflow.effective_state === 'PENDING_ANSWER' && workflow.pending_question !== null);
  const original = await getDecision(current.run_id);
  check(original.user_id === current.user_id && original.run_id === current.run_id && original.completeness === 'COMPLETE' && original.audit_chain_status === 'VALID' && original.trace && canonical(original.trace.outcome.question_revision) === canonical(current));
  const body: ObserveBody = { kind: 'QUESTION', session_id: current.session_id, expected_revision: current.revision, expected_run_id: current.run_id, reviewed_source_trace_hash: original.trace.trace_hash, expected_epoch_id: current.epoch_id, intervention_policy_id: null, idempotency_key: key };
  parseInterventionBody('OBSERVE', body); return { user_id: current.user_id, body };
}
