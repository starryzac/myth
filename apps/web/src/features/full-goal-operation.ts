import { useSyncExternalStore } from 'react';
import type { components } from '../../../../packages/contracts/schema';
import { parseFullGoalCommandLookup, parseFullGoalConfiguration } from '../api/full-goals';
import { object } from './policy-form';
import { recoverDemoOperation } from './demo-operation';
import { recoverFullPolicyOperation } from './full-policy-operation';
import { recoverOnboardingDraft } from './onboarding-draft';
import { isWriteInFlight } from './write-flight';

type Body = components['schemas']['FullGoalConfirmationRequest'] & { accepted: true };
export type FullGoalIntent = { protocol: 'full-goal-browser-command-v1'; user_id: string; goal_id: string; policy_id: string; path: string; body: Body; body_json: string; request_hash: string };
type State = { pending: FullGoalIntent | null; busy: boolean; storage_error: string | null };
const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const digest = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const nonblank = (value: unknown, max: number) => typeof value === 'string' && value.trim().length > 0 && value.length <= max;
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join('|') === [...keys].sort().join('|');
export function validFullGoalIntent(value: unknown): value is FullGoalIntent {
  if (!object(value) || !exact(value, ['protocol', 'user_id', 'goal_id', 'policy_id', 'path', 'body', 'body_json', 'request_hash']) || value.protocol !== 'full-goal-browser-command-v1' || !uuid(value.user_id) || !uuid(value.goal_id) || !uuid(value.policy_id) || value.path !== `/goals/${value.goal_id}/full-model/confirm` || !digest(value.request_hash) || typeof value.body_json !== 'string' || value.body_json.length > 20000 || !object(value.body)) return false;
  const body = value.body;
  if (!exact(body, ['expected_version_id', 'expected_epoch_id', 'configuration', 'reviewed_full_hash', 'reviewed_base_hash', 'accepted', 'reason', 'idempotency_key']) || !uuid(body.expected_version_id) || !uuid(body.expected_epoch_id) || !digest(body.reviewed_full_hash) || !digest(body.reviewed_base_hash) || body.accepted !== true || !nonblank(body.reason, 1000) || !nonblank(body.idempotency_key, 150)) return false;
  try { parseFullGoalConfiguration(body.configuration); return JSON.stringify(body) === value.body_json; } catch { return false; }
}
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  if (value === null || typeof value === 'string' || typeof value === 'boolean' || Number.isSafeInteger(value)) return JSON.stringify(value);
  throw new Error('原确认请求不能含不精确或非JSON字段');
}
/** Mirrors full_goals._request_hash, solely to bind the original browser request. */
export async function fullGoalRequestHash(userId: string, goalId: string, body: Body): Promise<string> {
  if (!uuid(userId) || !uuid(goalId) || !crypto.subtle) throw new Error('原用户/目标或安全摘要能力缺失，未开始确认');
  const original = { protocol: 'full-goal-model-v1', user_id: userId, epoch_id: body.expected_epoch_id, goal_id: goalId, expected_version_id: body.expected_version_id, full_configuration: body.configuration, reason: body.reason, idempotency_key: body.idempotency_key, accepted: true };
  const result = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonicalJson(original))); return [...new Uint8Array(result)].map((value) => value.toString(16).padStart(2, '0')).join('');
}
function frozen<T>(value: T): T { if (typeof value === 'object' && value !== null) { Object.values(value).forEach(frozen); Object.freeze(value); } return value; }
export async function prepareFullGoalIntent(input: Omit<FullGoalIntent, 'protocol' | 'path' | 'body_json' | 'request_hash'>): Promise<FullGoalIntent> {
  const original = { protocol: 'full-goal-browser-command-v1' as const, ...structuredClone(input), path: `/goals/${input.goal_id}/full-model/confirm`, body_json: JSON.stringify(input.body), request_hash: '0'.repeat(64) };
  if (!validFullGoalIntent(original)) throw new Error('完整目标原配置、双hash、版本/周期或明确接受不完整');
  original.request_hash = await fullGoalRequestHash(original.user_id, original.goal_id, original.body); return frozen(original);
}
export async function fullGoalIntentHashMatches(intent: FullGoalIntent): Promise<boolean> { return validFullGoalIntent(intent) && await fullGoalRequestHash(intent.user_id, intent.goal_id, intent.body) === intent.request_hash; }
export async function recordedFullGoalLookupMatches(intent: FullGoalIntent, value: unknown): Promise<boolean> {
  try { if (!await fullGoalIntentHashMatches(intent)) return false; return parseFullGoalCommandLookup(value, intent).status === 'RECORDED'; } catch { return false; }
}
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((listener) => listener());
let state: State = { pending: null, busy: false, storage_error: null }; let recovered = false;
const storageKey = () => `bounded-funds-full-goal-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
export const getFullGoalOperation = () => state;
export function recoverFullGoalOperation(): State {
  if (recovered) return state; recovered = true;
  try { const raw = sessionStorage.getItem(storageKey()); if (raw !== null) { const parsed: unknown = JSON.parse(raw); if (!validFullGoalIntent(parsed)) throw new Error('INVALID_ORIGINAL'); state = { pending: frozen(parsed), busy: false, storage_error: null }; } }
  catch { state = { ...state, storage_error: '完整目标原请求记录无法完整读取，禁止新确认；请保留原记录并只读核对。' }; }
  emit(); return state;
}
export function beginFullGoalOperation(intent: FullGoalIntent, blocked = false): void {
  recoverFullGoalOperation(); const demo = recoverDemoOperation(); const policy = recoverFullPolicyOperation(); const onboarding = recoverOnboardingDraft();
  if (blocked || state.busy || demo.busy || demo.pending || demo.storage_error || policy.busy || policy.pending || policy.storage_error || onboarding.busy || onboarding.draft.pending || onboarding.storage_error || isWriteInFlight()) throw new Error('其他原请求正在处理或待核对，不能开始完整目标确认');
  if (state.storage_error) throw new Error(state.storage_error); if (!validFullGoalIntent(intent)) throw new Error('完整目标原请求校验失败');
  if (state.pending && JSON.stringify(state.pending) !== JSON.stringify(intent)) throw new Error('完整目标原请求待核对，禁止换目标、版本、配置或键');
  const original = state.pending ?? frozen(structuredClone(intent));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(original)); }
  catch { state = { pending: original, busy: false, storage_error: '无法保存完整目标原body/key，未开始发送；请保留原请求。' }; emit(); throw new Error(state.storage_error!); }
  state = { pending: original, busy: true, storage_error: null }; emit();
}
/** Every HTTP outcome remains pending until a separate matching original lookup. */
export function endFullGoalAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function clearFullGoalOperationAfterLookup(intent: FullGoalIntent, value: unknown): Promise<void> {
  const same = () => !state.busy && state.pending && JSON.stringify(state.pending) === JSON.stringify(intent);
  if (!same()) throw new Error('不能清除另一原目标确认或正在发送的请求');
  if (!await recordedFullGoalLookupMatches(intent, value)) throw new Error('原请求、双hash或原回执未完整匹配，继续保留');
  if (!same()) throw new Error('核对期间原请求状态已变化，未清除记录');
  try { sessionStorage.removeItem(storageKey()); state = { pending: null, busy: false, storage_error: null }; }
  catch { state = { ...state, storage_error: '原目标确认已核对，但恢复记录无法删除；新写入仍禁止。' }; emit(); throw new Error(state.storage_error!); }
  emit();
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useFullGoalOperation = () => useSyncExternalStore(subscribe, getFullGoalOperation, getFullGoalOperation);
