import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';

/** Exact public DTOs; never a candidate-to-bank authorization bridge. */
export type QuestionChoice = { key: string; value: { kind: 'money'; amount_cents: number } | { kind: 'account'; account_id: string } | { kind: 'intent'; intent: Record<string, unknown> } };
export type QuestionVariable = { variable_id: string; field: 'ACTION_INTENT' | 'TRANSFER_AMOUNT' | 'TRANSFER_SOURCE' | 'TRANSFER_DESTINATION'; choices: QuestionChoice[]; completeness: 'COMPLETE' | 'INCOMPLETE'; source: 'USER_REQUEST' | 'REGISTERED_EVIDENCE'; evidence_id: string | null; impact_scope: 'ORIGINAL_MVP_PLANNING_REQUEST' };
export type QuestionStart = Omit<components['schemas']['QuestionStartRequest'], 'variables'> & { variables: QuestionVariable[] };
export type QuestionRefresh = components['schemas']['QuestionRefreshRequest'];
export type QuestionAnswer = components['schemas']['QuestionAnswerRequest'];
export type QuestionKind = 'START' | 'ANSWER' | 'REFRESH' | 'CLOSE';
export type QuestionBody = QuestionStart | QuestionAnswer | QuestionRefresh;
export type PendingQuestion = { question_id: string; variable_id: string; choices: QuestionChoice[]; worst_residual_signature_count: number; affected_action_types: string[]; bank_authority: false };
export type QuestionWorld = { world_key: string; assignments: Record<string, string>; intent: Record<string, unknown> | null; outcome: Record<string, unknown>; planning_only: true; execution_eligible: false };
export type QuestionEvaluation = { algorithm_version: 'full-finite-planning-minimax-v1'; status: 'STABLE' | 'DIVERGENT' | 'ALL_WORLDS_BLOCKED' | 'UNKNOWN' | 'CAPACITY_EXCEEDED'; complete_within_declared_domain: boolean; expected_world_count: number; evaluated_world_count: number; known_world_count: number; unknown_or_unsupported_world_count: number; stable: boolean | null; should_ask: boolean | null; worlds: QuestionWorld[]; distinct_signatures: string[]; question: Record<string, unknown> | null; minimax_candidates: Record<string, unknown>[]; full_confirmation_baseline_question_count: number; affected_action_types: string[]; explanation: string; reasons: string[] };
export type QuestionRevision = Omit<components['schemas']['QuestionRevision'], 'variables' | 'evaluation' | 'pending_question'> & { variables: QuestionVariable[]; evaluation: QuestionEvaluation; pending_question: PendingQuestion | null };
export type QuestionResponse = Omit<components['schemas']['QuestionWorkflowResponse'], 'original_receipt' | 'current_revision' | 'pending_question'> & { original_receipt: QuestionRevision; current_revision: QuestionRevision; pending_question: PendingQuestion | null };
export type QuestionLookup = Omit<components['schemas']['QuestionCommandLookupResponse'], 'original_receipt' | 'current_revision' | 'original_start_request'> & { original_receipt: QuestionRevision | null; current_revision: QuestionRevision | null; original_start_request: QuestionStart | null };
export type QuestionIntent = { protocol: 'one-question-browser-command-v1'; kind: QuestionKind; user_id: string; session_id: string | null; base_action_id: string; variables: QuestionVariable[]; previous_run_id: string | null; path: string; body: QuestionBody; body_json: string; request_hash: string };
export const questionUUID = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value);
const digest = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const key = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(value);
const choiceKey = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_.-]{1,80}$/.test(value);
const timestamp = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const integer = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && (value as number) >= min && (value as number) <= max;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === 'string');
const exact = (value: Record<string, unknown>, fields: string[]) => Object.keys(value).sort().join('|') === [...fields].sort().join('|');
function check(value: unknown): asserts value { if (!value) throw new Error('一次一问响应的原身份、完整分母或无授权边界不一致'); }
export function questionCanonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(questionCanonicalJson).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().map((field) => `${JSON.stringify(field)}:${questionCanonicalJson(value[field])}`).join(',')}}`;
  if (value === null || typeof value === 'string' || typeof value === 'boolean' || Number.isSafeInteger(value)) return JSON.stringify(value);
  throw new Error('原问答请求不能含不精确数字或非JSON值');
}
export const sameQuestionJson = (left: unknown, right: unknown) => questionCanonicalJson(left) === questionCanonicalJson(right);
function parseIntent(value: unknown): void {
  check(object(value));
  if (value.kind === 'transfer_internal') check(exact(value, ['kind', 'source_account_id', 'destination_account_id', 'amount_cents']) && questionUUID(value.source_account_id) && questionUUID(value.destination_account_id) && integer(value.amount_cents, 1));
  else if (value.kind === 'pay_recurring') check(exact(value, ['kind', 'policy_id', 'period', 'bill_id']) && questionUUID(value.policy_id) && (value.period === null || typeof value.period === 'string' && /^\d{4}-(?:0[1-9]|1[0-2])$/.test(value.period)) && (value.bill_id === null || questionUUID(value.bill_id)));
  else { const field = value.kind === 'allocate_goal' ? 'goal_id' : value.kind === 'purchase_asset' ? 'policy_id' : value.kind === 'redeem_asset' ? 'position_id' : null; check(field && exact(value, ['kind', field]) && questionUUID(value[field])); }
}
export function parseQuestionVariables(value: unknown): QuestionVariable[] {
  check(Array.isArray(value) && value.length >= 1 && value.length <= 3);
  for (const row of value) {
    check(object(row) && exact(row, ['variable_id', 'field', 'choices', 'completeness', 'source', 'evidence_id', 'impact_scope']) && choiceKey(row.variable_id) && ['ACTION_INTENT', 'TRANSFER_AMOUNT', 'TRANSFER_SOURCE', 'TRANSFER_DESTINATION'].includes(row.field as string) && ['COMPLETE', 'INCOMPLETE'].includes(row.completeness as string) && ['USER_REQUEST', 'REGISTERED_EVIDENCE'].includes(row.source as string) && row.impact_scope === 'ORIGINAL_MVP_PLANNING_REQUEST');
    check(row.source === 'REGISTERED_EVIDENCE' ? questionUUID(row.evidence_id) : row.evidence_id === null);
    check(Array.isArray(row.choices) && row.choices.length >= 2 && row.choices.length <= 8);
    for (const choice of row.choices) {
      check(object(choice) && exact(choice, ['key', 'value']) && choiceKey(choice.key) && object(choice.value)); const selected = choice.value;
      if (row.field === 'ACTION_INTENT') { check(exact(selected, ['kind', 'intent']) && selected.kind === 'intent'); parseIntent(selected.intent); }
      else if (row.field === 'TRANSFER_AMOUNT') check(exact(selected, ['kind', 'amount_cents']) && selected.kind === 'money' && integer(selected.amount_cents, 1));
      else check(exact(selected, ['kind', 'account_id']) && selected.kind === 'account' && questionUUID(selected.account_id));
    }
    check(new Set(row.choices.map((choice) => choice.key)).size === row.choices.length && new Set(row.choices.map((choice) => questionCanonicalJson(choice.value))).size === row.choices.length);
  }
  check(new Set(value.map((row) => row.variable_id)).size === value.length && new Set(value.map((row) => row.field)).size === value.length); return value as QuestionVariable[];
}
export function parseQuestionBody(kind: QuestionKind, value: unknown): QuestionBody {
  check(object(value) && questionUUID(value.expected_epoch_id) && key(value.idempotency_key));
  if (kind === 'START') { check(exact(value, ['base_action_id', 'variables', 'expected_epoch_id', 'idempotency_key']) && questionUUID(value.base_action_id)); parseQuestionVariables(value.variables); }
  else { const fields = ['expected_epoch_id', 'expected_revision', 'idempotency_key']; if (kind === 'ANSWER') fields.push('question_id', 'choice_key'); check(exact(value, fields) && integer(value.expected_revision, 1, 16)); if (kind === 'ANSWER') check(questionUUID(value.question_id) && choiceKey(value.choice_key)); }
  return value as QuestionBody;
}
function parsePending(value: unknown, variables: QuestionVariable[], answers: Record<string, unknown>, evaluation: Record<string, unknown>): void {
  check(object(value) && questionUUID(value.question_id) && choiceKey(value.variable_id) && value.bank_authority === false && integer(value.worst_residual_signature_count, 1) && strings(value.affected_action_types));
  const variable = variables.find((row) => row.variable_id === value.variable_id); check(variable && !Object.hasOwn(answers, variable.variable_id) && sameQuestionJson(value.choices, [...variable.choices].sort((a, b) => a.key < b.key ? -1 : a.key > b.key ? 1 : 0)));
  check(object(evaluation.question) && evaluation.question.variable_id === value.variable_id && evaluation.question.worst_residual_signature_count === value.worst_residual_signature_count && sameQuestionJson(value.affected_action_types, evaluation.affected_action_types));
}
export function parseQuestionRevision(value: unknown): QuestionRevision {
  check(object(value) && value.protocol === 'full-one-question-v1' && ['user_id', 'session_id', 'epoch_id', 'run_id', 'base_action_id'].every((field) => questionUUID(value[field])) && (value.previous_run_id === null || questionUUID(value.previous_run_id)) && timestamp(value.as_of) && integer(value.revision, 1, 17));
  check(['START', 'ANSWER', 'REBASE', 'REFRESH', 'CLOSE'].includes(value.command_kind as string) && key(value.command_key) && digest(value.command_hash) && digest(value.source_fingerprint) && typeof value.answer_applied === 'boolean' && ['old_candidates_execution_eligible', 'inherited_confirmation', 'authority_granted', 'execution_eligible'].every((field) => value[field] === false));
  check(value.revision === 1 ? value.command_kind === 'START' && value.previous_run_id === null && value.answer_applied === false : value.command_kind !== 'START' && questionUUID(value.previous_run_id));
  const variables = parseQuestionVariables(value.variables); check(object(value.answers)); const answers = value.answers;
  for (const [id, answer] of Object.entries(answers)) check(variables.some((row) => row.variable_id === id && row.choices.some((choice) => choice.key === answer)));
  const e = value.evaluation; check(object(e) && e.algorithm_version === 'full-finite-planning-minimax-v1' && e.planning_only === true && e.authority_granted === false && e.execution_eligible === false && e.probability_model === 'NONE' && e.answer_state_machine === 'NOT_IMPLEMENTED' && e.restart_deduplication === 'NOT_IMPLEMENTED');
  check(['STABLE', 'DIVERGENT', 'ALL_WORLDS_BLOCKED', 'UNKNOWN', 'CAPACITY_EXCEEDED'].includes(e.status as string) && typeof e.complete_within_declared_domain === 'boolean' && ['expected_world_count', 'evaluated_world_count', 'known_world_count', 'unknown_or_unsupported_world_count', 'full_confirmation_baseline_question_count'].every((field) => integer(e[field], 0, 512)) );
  const denominator = variables.reduce((n, row) => n * (Object.hasOwn(answers, row.variable_id) ? 1 : row.choices.length), 1); check(Array.isArray(e.worlds) && e.worlds.length === e.evaluated_world_count); const absent = e.worlds.length === 0; const fullDenominator = variables.reduce((n, row) => n * row.choices.length, 1); check(e.expected_world_count === (absent ? fullDenominator : denominator) && (e.evaluated_world_count as number) <= (e.expected_world_count as number) && (e.known_world_count as number) + (e.unknown_or_unsupported_world_count as number) === (absent ? e.expected_world_count : e.evaluated_world_count) && e.full_confirmation_baseline_question_count === (absent ? variables.length : variables.length - Object.keys(answers).length));
  const worlds = e.worlds; for (const world of worlds) {
    check(object(world) && digest(world.world_key) && object(world.assignments) && exact(world.assignments, variables.map((row) => row.variable_id)) && world.planning_only === true && world.execution_eligible === false && object(world.outcome));
    for (const row of variables) check(row.choices.some((choice) => choice.key === (world.assignments as Record<string, unknown>)[row.variable_id]) && (!Object.hasOwn(answers, row.variable_id) || world.assignments[row.variable_id] === answers[row.variable_id]));
    if (world.intent !== null) parseIntent(world.intent); const outcome = world.outcome; check(['KNOWN', 'UNKNOWN', 'UNSUPPORTED'].includes(outcome.status as string) && strings(outcome.reasons) && Array.isArray(outcome.source_evidence_ids) && outcome.source_evidence_ids.every(questionUUID));
    if (outcome.status === 'KNOWN') check(digest(outcome.signature) && digest(outcome.source_context_hash) && outcome.source_evidence_ids.length > 0 && object(outcome.decision) && outcome.decision.simulation === true && outcome.decision.evaluation_only === true && ['AUTO_EXECUTE', 'ASK_ONCE', 'ADVISE_ONLY', 'BLOCKED'].includes(outcome.decision.level as string) && object(outcome.effect) && outcome.effect.simulation === true && questionUUID(outcome.effect.user_id) && questionUUID(outcome.effect.operation_id) && integer(outcome.effect.amount_cents, 1) && object(outcome.validation) && outcome.validation.simulation === true && outcome.validation.financial_only === true && digest(outcome.validation.effect_hash));
  }
  check(new Set(worlds.map((world) => questionCanonicalJson(world.assignments))).size === worlds.length && new Set(worlds.map((world) => world.world_key)).size === worlds.length);
  const known = worlds.filter((world) => world.outcome.status === 'KNOWN'); check(known.length === e.known_world_count && strings(e.distinct_signatures) && sameQuestionJson([...new Set(known.map((world) => world.outcome.signature))].sort(), e.distinct_signatures));
  check(strings(e.reasons) && strings(e.affected_action_types) && typeof e.explanation === 'string' && Array.isArray(e.minimax_candidates) && (e.question === null || object(e.question)));
  const unknown = ['UNKNOWN', 'CAPACITY_EXCEEDED'].includes(e.status as string); check(unknown ? e.complete_within_declared_domain === false && e.stable === null && e.should_ask === null && e.question === null : e.complete_within_declared_domain === true && worlds.length === denominator && e.unknown_or_unsupported_world_count === 0 && e.stable === (e.distinct_signatures.length === 1) && e.should_ask === (e.status === 'DIVERGENT'));
  if (unknown) check(e.minimax_candidates.length === 0);
  else {
    const remaining = variables.filter((row) => !Object.hasOwn(answers, row.variable_id)); check(e.minimax_candidates.length === remaining.length);
    const candidates = e.minimax_candidates as Record<string, unknown>[];
    for (const row of remaining) {
      const matches = candidates.filter((candidate) => object(candidate) && candidate.variable_id === row.variable_id); check(matches.length === 1); const candidate = matches[0]!;
      check(candidate.field === row.field && Array.isArray(candidate.partitions) && candidate.partitions.length === row.choices.length); const counts: number[] = [];
      for (const choice of row.choices) {
        const parts = candidate.partitions.filter((part) => object(part) && part.choice_key === choice.key); check(parts.length === 1); const part = parts[0]!; const members = worlds.filter((world) => world.assignments[row.variable_id] === choice.key); const signatures = [...new Set(members.map((world) => world.outcome.signature))].sort();
        check(strings(part.world_keys) && sameQuestionJson([...part.world_keys].sort(), members.map((world) => world.world_key).sort()) && sameQuestionJson(part.signatures, signatures) && part.residual_signature_count === signatures.length); counts.push(signatures.length);
      }
      check(candidate.worst_residual_signature_count === Math.max(...counts));
    }
    const blocked = known.every((world) => object(world.outcome.decision) && world.outcome.decision.level === 'BLOCKED'); check(e.status === (blocked ? 'ALL_WORLDS_BLOCKED' : e.distinct_signatures.length === 1 ? 'STABLE' : 'DIVERGENT'));
    const minimum = [...candidates].sort((a, b) => (a.worst_residual_signature_count as number) - (b.worst_residual_signature_count as number) || ((a.variable_id as string) < (b.variable_id as string) ? -1 : 1))[0]; check(e.status === 'DIVERGENT' ? minimum && sameQuestionJson(e.question, minimum) : e.question === null);
  }
  check(['PENDING_ANSWER', 'READY_FOR_REVIEW', 'ALL_WORLDS_BLOCKED', 'UNKNOWN', 'CLOSED'].includes(value.state as string));
  if (value.state === 'PENDING_ANSWER') { check(e.status === 'DIVERGENT'); parsePending(value.pending_question, variables, answers, e); }
  else { check(value.pending_question === null); if (value.state === 'CLOSED') check(value.command_kind === 'CLOSE' && value.answer_applied === false); else check(value.state === (unknown ? 'UNKNOWN' : e.status === 'ALL_WORLDS_BLOCKED' ? 'ALL_WORLDS_BLOCKED' : 'READY_FOR_REVIEW')); }
  check((value.state === 'CLOSED') === (value.command_kind === 'CLOSE') && (value.state === 'CLOSED' || (value.revision as number) <= 16)); if (value.command_kind === 'REBASE') check(Object.keys(answers).length === 0 && value.answer_applied === false); if (value.command_kind === 'ANSWER') check(value.answer_applied === true);
  assertMoneyFields(value); return value as QuestionRevision;
}
const originals = new WeakMap<object, string>();
export const getOriginalQuestionResponse = (value: object) => originals.get(value) ?? null;
function sameSession(a: QuestionRevision, b: QuestionRevision) { check(['user_id', 'session_id', 'epoch_id', 'base_action_id'].every((field) => a[field as keyof QuestionRevision] === b[field as keyof QuestionRevision]) && sameQuestionJson(a.variables, b.variables) && a.revision <= b.revision); }
export function parseQuestionResponse(value: unknown, sessionId?: string, raw?: string): QuestionResponse {
  check(object(value) && value.protocol === 'full-one-question-v1' && value.simulation === true && value.planning_only === true && value.persisted_workflow === true && value.legacy_analyzer_flags_apply_only_to_stateless_analysis === true && value.bank_submission_support === 'NOT_IMPLEMENTED' && ['authority_granted', 'execution_eligible', 'current_old_candidates_eligible', 'old_confirmation_inherited'].every((field) => value[field] === false) && typeof value.replayed_original_receipt === 'boolean');
  const receipt = parseQuestionRevision(value.original_receipt); const current = parseQuestionRevision(value.current_revision); sameSession(receipt, current); if (sessionId) check(current.session_id === sessionId);
  check(['PENDING_ANSWER', 'READY_FOR_REVIEW', 'ALL_WORLDS_BLOCKED', 'UNKNOWN', 'STALE_RECOMPUTATION_REQUIRED', 'ARCHIVED', 'CLOSED'].includes(value.effective_state as string) && (value.current_source_fingerprint === null || digest(value.current_source_fingerprint)) && (value.fresh_evaluation_at === null || timestamp(value.fresh_evaluation_at)));
  if (value.effective_state === 'PENDING_ANSWER') check(current.state === 'PENDING_ANSWER' && sameQuestionJson(value.pending_question, current.pending_question)); else check(value.pending_question === null);
  if (value.effective_state === 'CLOSED') check(current.state === 'CLOSED' && value.current_source_fingerprint === null && value.fresh_evaluation_at === null); else if (current.state === 'CLOSED') check(value.effective_state === 'ARCHIVED');
  if (!['UNKNOWN', 'ARCHIVED', 'CLOSED', 'STALE_RECOMPUTATION_REQUIRED'].includes(value.effective_state as string)) check(value.effective_state === current.state && value.current_source_fingerprint === current.source_fingerprint && timestamp(value.fresh_evaluation_at));
  if (value.effective_state === 'STALE_RECOMPUTATION_REQUIRED') check(digest(value.current_source_fingerprint) && value.current_source_fingerprint !== current.source_fingerprint && timestamp(value.fresh_evaluation_at));
  const result = value as QuestionResponse; if (raw !== undefined) originals.set(result, raw); return result;
}
export function questionCommand(intent: QuestionIntent): Record<string, unknown> { return { kind: intent.kind, user_id: intent.user_id, ...(intent.kind === 'START' ? {} : { session_id: intent.session_id }), request: intent.body }; }
function receiptMatches(intent: QuestionIntent, receipt: QuestionRevision): void {
  check(receipt.user_id === intent.user_id && receipt.epoch_id === intent.body.expected_epoch_id && receipt.base_action_id === intent.base_action_id && sameQuestionJson(receipt.variables, intent.variables) && receipt.command_key === intent.body.idempotency_key && receipt.command_hash === intent.request_hash);
  if (intent.kind === 'START') check(receipt.command_kind === 'START' && receipt.revision === 1 && receipt.previous_run_id === null);
  else { check(receipt.session_id === intent.session_id && receipt.revision === (intent.body as QuestionRefresh).expected_revision + 1 && receipt.previous_run_id === intent.previous_run_id && [intent.kind, ...(intent.kind === 'CLOSE' ? [] : ['REBASE'])].includes(receipt.command_kind)); if (intent.kind === 'ANSWER' && receipt.answer_applied) check(Object.values(receipt.answers).includes((intent.body as QuestionAnswer).choice_key)); }
}
export function parseQuestionLookup(value: unknown, intent: QuestionIntent, raw?: string): QuestionLookup {
  check(object(value) && value.protocol === 'full-question-command-lookup-v1' && value.simulation === true && ['RECORDED', 'NOT_FOUND_NOT_FINAL'].includes(value.status as string) && value.epoch_id === intent.body.expected_epoch_id && value.idempotency_key === intent.body.idempotency_key && value.authority_granted === false && value.execution_eligible === false && value.replacement_allowed === false && value.client_match_required === true);
  if (value.status === 'NOT_FOUND_NOT_FINAL') check(['session_id', 'original_command', 'request_hash', 'original_receipt', 'current_revision', 'original_start_request'].every((field) => value[field] === null));
  else { check(value.request_hash === intent.request_hash && sameQuestionJson(value.original_command, questionCommand(intent))); const receipt = parseQuestionRevision(value.original_receipt); const current = parseQuestionRevision(value.current_revision); sameSession(receipt, current); receiptMatches(intent, receipt); check(value.session_id === current.session_id && (intent.kind === 'START' ? sameQuestionJson(value.original_start_request, intent.body) : value.original_start_request === null)); }
  const result = value as QuestionLookup; if (raw !== undefined) originals.set(result, raw); return result;
}
export function getQuestionSession(id: string): Promise<QuestionResponse> { check(questionUUID(id)); return request(`/finite-planning/sessions/${id}`, 'GET', undefined, (value, raw) => parseQuestionResponse(value, id, raw)); }
export function postQuestionCommand(intent: QuestionIntent): Promise<QuestionResponse> { parseQuestionBody(intent.kind, intent.body); const suffix = { ANSWER: 'answers', REFRESH: 'refresh', CLOSE: 'close' }; check(intent.protocol === 'one-question-browser-command-v1' && questionUUID(intent.user_id) && questionUUID(intent.base_action_id) && digest(intent.request_hash) && intent.body_json === JSON.stringify(intent.body) && intent.path === (intent.kind === 'START' ? '/finite-planning/sessions' : `/finite-planning/sessions/${intent.session_id}/${suffix[intent.kind]}`)); return request(intent.path, 'POST', intent.body, (value, raw) => { const response = parseQuestionResponse(value, intent.session_id ?? undefined, raw); receiptMatches(intent, response.original_receipt); return response; }); }
export function lookupQuestionCommand(intent: QuestionIntent): Promise<QuestionLookup> { const path = intent.kind === 'START' ? `/finite-planning/sessions/commands/${intent.body.expected_epoch_id}/by-start-key/${encodeURIComponent(intent.body.idempotency_key)}` : `/finite-planning/sessions/${intent.session_id}/commands/by-key/${encodeURIComponent(intent.body.idempotency_key)}`; return request(path, 'GET', undefined, (value, raw) => parseQuestionLookup(value, intent, raw)); }
