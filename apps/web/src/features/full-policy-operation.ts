import { useSyncExternalStore } from 'react';
import type { components } from '../../../../packages/contracts/schema';
import { object } from './policy-form';
import { assertMoneyFields } from './money';
import { recoverDemoOperation } from './demo-operation';
import { isWriteInFlight } from './write-flight';

type Body = components['schemas']['FullCreateRequest'] | components['schemas']['FullChangeRequest'] | components['schemas']['FullResumeRequest'] | components['schemas']['FullStateRequest'];
export type FullPolicyIntent = {
  protocol: 'full-policy-browser-command-v1'; kind: 'CREATE' | 'CHANGE' | 'SUSPEND' | 'REVOKE' | 'RESUME';
  policy_id: string | null; original_epoch_id: string | null; original_configuration_hash: string; path: string; body: Body; body_json: string;
};
export function sameFullPolicyJson(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((value, index) => sameFullPolicyJson(value, right[index]));
  if (object(left) && object(right)) return Object.keys(left).length === Object.keys(right).length && Object.keys(left).every((key) => Object.hasOwn(right, key) && sameFullPolicyJson(left[key], right[key]));
  return false;
}
/** Recovery metadata has no authority. Only the complete matching original lookup can release this UI gate. */
export function recordedFullPolicyLookupMatches(intent: FullPolicyIntent, value: unknown): boolean {
  if (!validFullPolicyIntent(intent) || !object(value) || value.simulation !== true || value.bank_authority !== false || value.dedicated_audit_event !== false || value.receipt_is_current_authority !== false || value.not_found_is_final !== false || value.status !== 'RECORDED' || value.idempotency_key !== intent.body.idempotency_key || !digest(value.request_hash)) return false;
  const original = value.original_request; const command = value.command;
  if (!object(original) || !object(command) || original.protocol !== 'full-policy-command-v1' || original.kind !== intent.kind || !uuid(original.user_id) || original.policy_id !== intent.policy_id || !sameFullPolicyJson(original.body, intent.body)) return false;
  if (Object.keys(original).sort().join('|') !== ['protocol', 'kind', 'user_id', 'policy_id', 'body'].sort().join('|')) return false;
  if (command.simulation !== true || command.bank_authority !== false || command.dedicated_audit_event !== false || command.kind !== intent.kind || command.idempotency_key !== intent.body.idempotency_key || command.request_hash !== value.request_hash || !uuid(command.command_id) || !uuid(command.policy_id) || !uuid(command.version_id) || !digest(command.result_hash) || !Number.isSafeInteger(command.command_number) || (command.command_number as number) < 1) return false;
  if (intent.policy_id !== null && command.policy_id !== intent.policy_id) return false;
  if (typeof command.created_at !== 'string' || !/(?:Z|[+-]\d\d:\d\d)$/.test(command.created_at) || !Number.isFinite(Date.parse(command.created_at))) return false;
  const result = command.result;
  if (!object(result) || result.simulation !== true || result.bank_authority !== false || result.dedicated_audit_event !== false || result.receipt_is_current_authority !== false || result.action_dependencies_supported !== false || result.requires_recompute !== true || !uuid(result.epoch_id) || result.policy_id !== command.policy_id || result.version_id !== command.version_id || result.command_id !== command.command_id || result.command_number !== command.command_number || result.previous_command_hash !== command.previous_hash || result.status !== command.resulting_status || result.configuration_hash !== intent.original_configuration_hash) return false;
  if (intent.original_epoch_id !== null && result.epoch_id !== intent.original_epoch_id) return false;
  if (['SUSPEND', 'REVOKE'].includes(intent.kind) && result.version_id !== (intent.body as components['schemas']['FullStateRequest']).expected_version_id) return false;
  if (intent.kind === 'CREATE' && command.command_number !== 1) return false;
  if (command.command_number === 1 ? command.previous_hash !== null || command.previous_status !== null : !digest(command.previous_hash) || !['ACTIVE', 'CONFIRMED', 'EXPIRED', 'SUSPENDED', 'REVOKED'].includes(command.previous_status as string)) return false;
  if (!Array.isArray(result.invalidated_action_ids) || !Array.isArray(result.inflight_action_ids) || [result.invalidated_action_ids, result.inflight_action_ids].some((ids) => !ids.every(uuid) || new Set(ids).size !== ids.length)) return false;
  if (['CHANGE', 'RESUME'].includes(intent.kind) && result.version_id === (intent.body as components['schemas']['FullResumeRequest']).expected_version_id) return false;
  return intent.kind === 'SUSPEND' ? result.status === 'SUSPENDED' : intent.kind === 'REVOKE' ? result.status === 'REVOKED' : intent.kind === 'CREATE' ? ['ACTIVE', 'CONFIRMED', 'EXPIRED'].includes(result.status as string) : intent.kind === 'RESUME' ? ['ACTIVE', 'CONFIRMED'].includes(result.status as string) : ['ACTIVE', 'CONFIRMED', 'EXPIRED', 'SUSPENDED'].includes(result.status as string);
}
type OperationState = { pending: FullPolicyIntent | null; busy: boolean; storage_error: string | null };
const listeners = new Set<() => void>();
let state: OperationState = { pending: null, busy: false, storage_error: null };
const storageKey = () => `bounded-funds-full-policy-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const nonblank = (value: unknown, max: number) => typeof value === 'string' && value.trim().length > 0 && value.length <= max;
const templates = ['DatedExpensePolicy', 'PeriodicTransferPolicy', 'AssetAuthorizationPolicy', 'RecoveryPolicy', 'GoalAllocationPolicy', 'CrossGoalReallocationPolicy', 'SeasonalReservePolicy', 'InterventionPolicy'];
function safeJson(value: unknown, depth = 0): boolean {
  if (depth > 64) return false;
  if (typeof value === 'number') return Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value));
  if (Array.isArray(value)) return value.every((child) => safeJson(child, depth + 1));
  if (object(value)) return Object.values(value).every((child) => safeJson(child, depth + 1));
  return value === null || typeof value === 'string' || typeof value === 'boolean';
}
export function validFullPolicyIntent(value: unknown): value is FullPolicyIntent {
  if (!object(value) || value.protocol !== 'full-policy-browser-command-v1' || !['CREATE', 'CHANGE', 'SUSPEND', 'REVOKE', 'RESUME'].includes(value.kind as string) || !digest(value.original_configuration_hash) || !object(value.body) || typeof value.body_json !== 'string') return false;
  if (Object.keys(value).sort().join('|') !== ['protocol', 'kind', 'policy_id', 'original_epoch_id', 'original_configuration_hash', 'path', 'body', 'body_json'].sort().join('|')) return false;
  const kind = value.kind as FullPolicyIntent['kind']; const body = value.body;
  if (kind === 'CREATE' ? value.policy_id !== null || value.original_epoch_id !== null || value.path !== '/full-policies/confirm' : !uuid(value.policy_id) || !uuid(value.original_epoch_id) || value.path !== `/full-policies/${value.policy_id}/${kind.toLowerCase()}`) return false;
  const keys = ['reason', 'idempotency_key'];
  if (!nonblank(body.reason, 1000) || !nonblank(body.idempotency_key, 160)) return false;
  if (kind !== 'CREATE') { keys.push('expected_version_id'); if (!uuid(body.expected_version_id)) return false; }
  if (['CREATE', 'CHANGE', 'RESUME'].includes(kind)) { keys.push('accepted', 'reviewed_hash'); if (body.accepted !== true || !digest(body.reviewed_hash) || body.reviewed_hash !== value.original_configuration_hash) return false; }
  if (kind === 'CREATE') { keys.push('template_name'); if (!templates.includes(body.template_name as string)) return false; }
  if (['CREATE', 'CHANGE'].includes(kind)) { keys.push('configuration'); if (!object(body.configuration) || !safeJson(body.configuration)) return false; }
  if (Object.keys(body).sort().join('|') !== keys.sort().join('|')) return false;
  try { assertMoneyFields(body); return JSON.stringify(body) === value.body_json; } catch { return false; }
}
function freeze<T>(value: T): T {
  if (typeof value === 'object' && value !== null) { Object.values(value).forEach(freeze); Object.freeze(value); } return value;
}
function emit() { listeners.forEach((listener) => listener()); }
export const getFullPolicyOperation = () => state;
export function recoverFullPolicyOperation(): OperationState {
  if (state.pending || state.busy || state.storage_error) return state;
  try {
    const stored = sessionStorage.getItem(storageKey());
    if (stored !== null) { const parsed: unknown = JSON.parse(stored); if (!validFullPolicyIntent(parsed)) throw new Error('INVALID_ORIGINAL'); state = { pending: freeze(parsed), busy: false, storage_error: null }; }
  } catch { state = { ...state, storage_error: '完整版策略原请求记录无法完整读取，禁止新写入；请保留记录并核对原命令。' }; }
  emit(); return state;
}
export function prepareFullPolicyIntent(input: Omit<FullPolicyIntent, 'protocol' | 'body_json'>): FullPolicyIntent {
  const candidate: unknown = { protocol: 'full-policy-browser-command-v1', ...JSON.parse(JSON.stringify(input)), body_json: JSON.stringify(input.body) };
  if (!validFullPolicyIntent(candidate)) throw new Error('完整策略原请求身份、body或键无效'); return freeze(candidate);
}
export function beginFullPolicyOperation(intent: FullPolicyIntent, externallyBlocked = false): void {
  recoverFullPolicyOperation(); const demo = recoverDemoOperation();
  if (externallyBlocked || demo.busy || demo.pending || demo.storage_error || isWriteInFlight() || state.busy) throw new Error('其他原请求正在处理或待核对，不能提交完整策略命令');
  if (state.storage_error) throw new Error(state.storage_error);
  if (!validFullPolicyIntent(intent)) throw new Error('完整策略原请求未通过校验');
  if (state.pending && JSON.stringify(state.pending) !== JSON.stringify(intent)) throw new Error('原完整策略命令尚待核对，禁止换键、配置或操作');
  const original = state.pending ?? freeze(JSON.parse(JSON.stringify(intent)) as FullPolicyIntent);
  try { sessionStorage.setItem(storageKey(), JSON.stringify(original)); }
  catch { state = { pending: original, busy: false, storage_error: '浏览器无法保存完整策略原body和键，未发送写入；请保留原请求。' }; emit(); throw new Error(state.storage_error!); }
  state = { pending: original, busy: true, storage_error: null }; emit();
}
/** An HTTP reply alone never resolves an uncertain original write; lookup proof is separate. */
export function endFullPolicyAttempt(): void { state = { ...state, busy: false }; emit(); }
export function clearFullPolicyOperationAfterLookup(intent: FullPolicyIntent, lookup: unknown): void {
  if (state.busy || !state.pending || JSON.stringify(state.pending) !== JSON.stringify(intent)) throw new Error('不能清除另一原命令或正在执行的请求');
  if (!recordedFullPolicyLookupMatches(intent, lookup)) throw new Error('只读原命令未完整匹配，不能清除待核对请求');
  try { sessionStorage.removeItem(storageKey()); state = { pending: null, busy: false, storage_error: null }; }
  catch { state = { ...state, storage_error: '原命令已核对，但浏览器无法删除恢复记录；新写入仍被阻止。' }; emit(); throw new Error(state.storage_error!); }
  emit();
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useFullPolicyOperation = () => useSyncExternalStore(subscribe, getFullPolicyOperation, getFullPolicyOperation);
