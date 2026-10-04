import type { components } from '../../../../packages/contracts/schema';
import { object } from '../features/policy-form';
import { request } from './http';

export type TraceResponse = components['schemas']['DecisionTraceResponse'];
export type TraceList = components['schemas']['DecisionTraceList'];
export type ActionLink = components['schemas']['TraceActionLink'];
export type TraceReceipt = components['schemas']['ActionReceiptResponse'];
const originalResponses = new WeakMap<TraceResponse, string>();
export const getOriginalTraceResponse = (data: TraceResponse): string | null => originalResponses.get(data) ?? null;
export function hasUnsafeDeclarationNumbers(data: TraceResponse): boolean {
  function unsafe(value: unknown): boolean {
    if (typeof value === 'number') return !Number.isSafeInteger(value);
    if (Array.isArray(value)) return value.some(unsafe);
    return object(value) && Object.values(value).some(unsafe);
  }
  return unsafe(data.legacy_snapshot) || unsafe(data.legacy_result) || (data.trace?.sources ?? []).some((item) => unsafe(item.content)) || (data.trace?.policies ?? []).some((item) => unsafe(item.configuration));
}
export const isRunId = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const complete = ['COMPLETE', 'LEGACY_PARTIAL', 'UNSUPPORTED_VERSION'];
const phases = ['PREPARE', 'CONFIRM', 'RESERVE', 'BANK_ACCEPT', 'RECOVERY_PLAN', 'CONTRACT_SETTLEMENT', 'EVALUATION'];
function requireValue(condition: unknown): asserts condition { if (!condition) throw new Error('历史决策响应未通过完整性或精确金额校验'); }
const text = (value: unknown) => typeof value === 'string';
const nullable = (value: unknown, valid: (value: unknown) => boolean) => value == null || valid(value);
const timestamp = (value: unknown) => text(value) && /(?:Z|[+-]\d\d:\d\d)$/.test(value as string) && Number.isFinite(Date.parse(value as string));
const digest = (value: unknown) => text(value) && /^[0-9a-f]{64}$/.test(value as string);
const array = (value: unknown, max: number, valid: (value: unknown) => boolean) => Array.isArray(value) && value.length <= max && value.every(valid);
const optionalArray = (value: unknown, max: number, valid: (value: unknown) => boolean) => value === undefined || array(value, max, valid);
const strings = (value: unknown) => array(value, 10000, text);

/** Only explicit RawJsonObject copies bypass money validation; their fields are declarations, not calculated money. */
function checkJson(value: unknown, path = '', raw = false, depth = 0, owner?: string): void {
  requireValue(depth <= 64);
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return;
  if (typeof value === 'number') { requireValue(Number.isFinite(value)); return; }
  if (Array.isArray(value)) { value.forEach((item, i) => checkJson(item, `${path}[${i}]`, raw, depth + 1, owner)); return; }
  requireValue(object(value));
  for (const [key, child] of Object.entries(value)) {
    const childPath = path ? `${path}.${key}` : key;
    const declaration = raw || /^(?:legacy_snapshot|legacy_result)$/.test(childPath) ||
      /^trace\.(?:sources\[\d+\]\.content|policies\[\d+\]\.configuration)$/.test(childPath);
    if (!declaration && key === 'user_id' && owner !== undefined) requireValue(child === owner);
    if (!declaration && key === 'amount_options_cents') {
      requireValue(child === null || (array(child, 8, (amount) => Number.isSafeInteger(amount) && (amount as number) > 0) && (child as unknown[]).length >= 2));
    } else if (!declaration && key.endsWith('_cents')) requireValue(child === null || Number.isSafeInteger(child));
    if (!declaration && key.includes('_cents_by_')) {
      requireValue(child === null || (object(child) && Object.values(child).every((amount) => amount === null || Number.isSafeInteger(amount))));
    }
    checkJson(child, childPath, declaration, depth + 1, owner);
  }
}
export function parseTraceResponse(value: unknown, originalText?: string): TraceResponse {
  requireValue(object(value) && value.simulation === true && isRunId(value.user_id) && isRunId(value.run_id));
  requireValue(timestamp(value.as_of) && timestamp(value.read_at) && complete.includes(value.completeness as string));
  requireValue(['VALID', 'INTEGRITY_ERROR', 'UNSUPPORTED_VERSION', 'LEGACY_UNAUDITED', 'INCOMPLETE'].includes(value.audit_chain_status as string));
  requireValue(array(value.children, 10000, isRunId));
  requireValue(array(value.current_references, 11000, (item) => object(item) && ['EVIDENCE', 'POLICY'].includes(item.entity_type as string) &&
    isRunId(item.entity_id) && ['UNCHANGED', 'STATUS_CHANGED', 'MISSING'].includes(item.status as string) && text(item.original_status) && nullable(item.current_status, text)));
  requireValue(array(value.actions, 10000, (item) => object(item) && isRunId(item.action_id) && isRunId(item.decision_run_id) && text(item.status) && digest(item.request_hash) &&
    nullable(item.bank_operation_id, isRunId) && nullable(item.receipt_id, isRunId) && nullable(item.bank_status, text) && nullable(item.receipt_status, text)));
  for (const key of ['legacy_snapshot', 'legacy_result']) requireValue(nullable(value[key], object));
  const trace = value.trace;
  if (trace != null) {
    requireValue(object(trace) && trace.schema_version === 'decision-trace-v1' && trace.simulation === true && trace.run_id === value.run_id && trace.user_id === value.user_id && trace.as_of === value.as_of);
    requireValue(phases.includes(trace.phase as string) && nullable(trace.action_id, isRunId) && nullable(trace.parent_run_id, isRunId));
    if (trace.action_id != null) requireValue((value.actions as ActionLink[]).length === 1 && (value.actions as ActionLink[])[0]!.action_id === trace.action_id);
    requireValue(object(trace.algorithm_versions) && Object.values(trace.algorithm_versions).every(text) && object(trace.inputs) && object(trace.outcome) && digest(trace.input_hash) && digest(trace.trace_hash));
    requireValue(optionalArray(trace.sources, 10000, (item) => object(item) && isRunId(item.id) && item.user_id === value.user_id &&
      ['BANK_CONFIRMED', 'BANK_OBSERVED', 'USER_DECLARED', 'MODEL_INFERRED', 'USER_CONFIRMED_POLICY', 'USER_CONFIRMED_ACTION'].includes(item.evidence_level as string) &&
      text(item.source_type) && text(item.source_ref) && object(item.content) && digest(item.content_hash) && digest(item.captured_content_hash) &&
      ['VERIFIED', 'INVALID'].includes(item.content_integrity as string) && ['VALID', 'CONFLICTED', 'UNKNOWN', 'SUPERSEDED'].includes(item.status_at_decision as string) &&
      timestamp(item.observed_at) && timestamp(item.valid_from) && nullable(item.valid_to, timestamp) && nullable(item.supersedes_evidence_id, isRunId)));
    requireValue(optionalArray(trace.policies, 1000, (item) => object(item) && isRunId(item.id) && isRunId(item.policy_id) && item.user_id === value.user_id && Number.isSafeInteger(item.version_number) && (item.version_number as number) > 0 &&
      object(item.configuration) && digest(item.configuration_hash) && digest(item.captured_configuration_hash) && ['VERIFIED', 'INVALID'].includes(item.configuration_integrity as string) && text(item.status_at_decision) &&
      nullable(item.confirmed_at, timestamp) && nullable(item.valid_from, timestamp) && nullable(item.valid_to, timestamp)));
    requireValue(optionalArray(trace.constraints, 10000, (item) => object(item) && text(item.constraint_key) && nullable(item.policy_version_id, isRunId) && typeof item.is_hard === 'boolean' && nullable(item.satisfied, (v) => typeof v === 'boolean') &&
      nullable(item.required_cents, (v) => Number.isSafeInteger(v) && (v as number) >= 0) && nullable(item.available_cents, Number.isSafeInteger) &&
      nullable(item.due_date, (v) => text(v) && /^\d{4}-\d\d-\d\d$/.test(v as string)) && nullable(item.calculation, object) && text(item.reason_code)));
    requireValue(optionalArray(trace.candidates, 1000, (item) => object(item) && text(item.candidate_key) && text(item.kind) && text(item.status) &&
      (item.inputs === undefined || object(item.inputs)) && (item.result === undefined || object(item.result)) && (item.reasons === undefined || strings(item.reasons))));
  } else requireValue(value.completeness !== 'COMPLETE');
  const explanation = value.explanation;
  requireValue(value.completeness !== 'COMPLETE' || explanation != null);
  if (explanation != null) requireValue(object(explanation) && explanation.schema_version === 'decision-explanation-v1' && explanation.simulation === true &&
    explanation.run_id === value.run_id && explanation.user_id === value.user_id && nullable(explanation.level, text) && nullable(explanation.financial_evaluation, text) &&
    nullable(explanation.confirmation_required, (v) => typeof v === 'boolean') && nullable(explanation.confirmation_satisfied, (v) => typeof v === 'boolean') && strings(explanation.summary) &&
    array(explanation.reasons, 10000, (item) => object(item) && text(item.code) && text(item.text) && strings(item.references)) && explanation.audit_chain === 'NOT_IMPLEMENTED');
  checkJson(value, '', false, 0, value.user_id);
  const result = value as TraceResponse;
  if (originalText !== undefined) originalResponses.set(result, originalText);
  return result;
}
function parseList(value: unknown): TraceList {
  requireValue(object(value) && value.simulation === true && isRunId(value.user_id) && nullable(value.next_cursor, text));
  requireValue(array(value.items, 100, (item) => object(item) && isRunId(item.run_id) && timestamp(item.as_of) && text(item.trigger_type) && text(item.status) &&
    complete.includes(item.completeness as string) && nullable(item.phase, text) && nullable(item.parent_run_id, isRunId) && nullable(item.action_id, isRunId)));
  checkJson(value, '', false, 0, value.user_id); return value as TraceList;
}
export function getDecisions(cursor?: string): Promise<TraceList> {
  const params = new URLSearchParams({ limit: '20' }); if (cursor !== undefined) params.set('cursor', cursor);
  return request(`/decisions?${params}`, 'GET', undefined, parseList);
}
export async function getDecision(id: string): Promise<TraceResponse> {
  requireValue(isRunId(id)); const result = await request(`/decisions/${encodeURIComponent(id)}`, 'GET', undefined, parseTraceResponse);
  requireValue(result.run_id === id); return result;
}
export async function getTraceReceipt(link: ActionLink): Promise<TraceReceipt> {
  const receipt = await request<TraceReceipt>(`/actions/${encodeURIComponent(link.action_id)}/receipt`);
  requireValue(receipt.action_id === link.action_id && isRunId(receipt.receipt_id) && isRunId(receipt.bank_operation_id) && receipt.bank_operation_id === link.bank_operation_id &&
    (link.receipt_id == null || receipt.receipt_id === link.receipt_id) && text(receipt.status) &&
    [receipt.executed_cents, receipt.fee_cents, receipt.loss_cents].every((v) => Number.isSafeInteger(v) && v >= 0) && array(receipt.posting_ids, 10000, isRunId) &&
    timestamp(receipt.occurred_at) && nullable(receipt.reconciled_at, timestamp));
  return receipt;
}
