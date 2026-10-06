import { useSyncExternalStore } from 'react';
import { parseReleaseIntent, parseReleaseLookup, releaseHash, releaseRequestEnvelope } from '../api/goal-release-authorizations';
import type { ReleaseConfirmation, ReleaseIntent, ReleaseScope } from '../api/goal-release-authorizations';
import { recoverDemoOperation } from './demo-operation';
import { recoverFullPolicyOperation } from './full-policy-operation';
import { recoverFullGoalOperation } from './full-goal-operation';
import { recoverOnboardingDraft } from './onboarding-draft';
import { recoverOneQuestionOperation } from './one-question-operation';
import { recoverSpendingEvidenceOperation } from './spending-evidence-operation';
import { recoverInterventionOperation } from './intervention-operation';
import { isWriteInFlight } from './write-flight';

type State = { pending: ReleaseIntent | null; busy: boolean; storage_error: string | null };
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export function validGoalReleaseIntent(value: unknown): value is ReleaseIntent { try { parseReleaseIntent(value); return true; } catch { return false; } }
export async function goalReleaseIntentHashMatches(intent: ReleaseIntent): Promise<boolean> {
  return validGoalReleaseIntent(intent) && intent.body.reviewed_scope_hash === await releaseHash(intent.reviewed_scope) && intent.request_hash === await releaseHash(releaseRequestEnvelope(intent.user_id, intent.policy_id, intent.body));
}
export async function prepareGoalReleaseIntent(userId: string, ownerGoalId: string, scope: ReleaseScope, body: ReleaseConfirmation): Promise<ReleaseIntent> {
  const intent = { protocol: 'goal-release-browser-command-v1' as const, user_id: userId, owner_goal_id: ownerGoalId, policy_id: scope.policy_id, path: `/goal-release-authorizations/policies/${scope.policy_id}/confirm`, body: structuredClone(body), body_json: JSON.stringify(body), reviewed_scope: structuredClone(scope), request_hash: '0'.repeat(64) };
  parseReleaseIntent(intent); if (body.reviewed_scope_hash !== await releaseHash(scope)) throw new Error('完整复核范围hash不一致，未准备确认'); intent.request_hash = await releaseHash(releaseRequestEnvelope(userId, intent.policy_id, body)); return freeze(intent);
}
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((fn) => fn());
let state: State = { pending: null, busy: false, storage_error: null }; let recovered = false;
const storageKey = () => `bounded-funds-goal-release-authorization-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
export const getGoalReleaseAuthorizationOperation = () => state;
export function recoverGoalReleaseAuthorizationOperation(): State {
  if (recovered) return state; recovered = true;
  try { const raw = sessionStorage.getItem(storageKey()); if (raw !== null) { const value: unknown = JSON.parse(raw); if (!validGoalReleaseIntent(value)) throw new Error('INVALID_ORIGINAL'); state = { pending: freeze(value), busy: false, storage_error: null }; } }
  catch { state = { ...state, storage_error: '专用回拨授权原请求无法完整读取；保留存储原件并禁止新确认。' }; } emit(); return state;
}
function otherFamilyBlocked(): boolean {
  const demo = recoverDemoOperation(), policy = recoverFullPolicyOperation(), goal = recoverFullGoalOperation(), onboarding = recoverOnboardingDraft(), question = recoverOneQuestionOperation(), spending = recoverSpendingEvidenceOperation(), intervention = recoverInterventionOperation();
  return !!(demo.busy || demo.pending || demo.storage_error || policy.busy || policy.pending || policy.storage_error || goal.busy || goal.pending || goal.storage_error || onboarding.busy || onboarding.draft.pending || onboarding.storage_error || question.busy || question.pending || question.storage_error || spending.busy || spending.pending || spending.storage_error || intervention.busy || intervention.pending || intervention.storage_error);
}
export async function beginGoalReleaseAuthorizationOperation(intent: ReleaseIntent, blocked = false): Promise<void> {
  recoverGoalReleaseAuthorizationOperation(); const available = () => !blocked && !state.busy && !state.storage_error && !isWriteInFlight() && !otherFamilyBlocked();
  if (!available() || !await goalReleaseIntentHashMatches(intent) || !available()) throw new Error('原请求/摘要不一致，其他族待核对或存储不可用，未开始专用授权');
  if (state.pending && JSON.stringify(state.pending) !== JSON.stringify(intent)) throw new Error('原专用授权结果不明，禁止换键、范围、策略或目标'); const original = state.pending ?? freeze(structuredClone(intent));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(original)); }
  catch { state = { pending: original, busy: false, storage_error: '无法保存完整授权scope/body/key，未开始发送；请保留原请求。' }; emit(); throw new Error(state.storage_error!); }
  state = { pending: original, busy: true, storage_error: null }; emit();
}
/** Every POST outcome retains the original. A fresh independent by-key GET is required. */
export function endGoalReleaseAuthorizationAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function clearGoalReleaseAuthorizationAfterLookup(intent: ReleaseIntent, value: unknown): Promise<void> {
  const same = () => !state.busy && state.pending && JSON.stringify(state.pending) === JSON.stringify(intent);
  if (!same() || !await goalReleaseIntentHashMatches(intent)) throw new Error('另一原授权正在处理，或原请求摘要未匹配，不能解除'); const lookup = await parseReleaseLookup(value, intent);
  if (lookup.status !== 'RECORDED' || !same()) throw new Error('原键未找到不是最终未提交证明；继续保留完整请求');
  try { sessionStorage.removeItem(storageKey()); state = { pending: null, busy: false, storage_error: null }; }
  catch { state = { ...state, storage_error: '原授权已核对，但恢复记录无法删除；新确认仍被禁止。' }; emit(); throw new Error(state.storage_error!); } emit();
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useGoalReleaseAuthorizationOperation = () => useSyncExternalStore(subscribe, getGoalReleaseAuthorizationOperation, getGoalReleaseAuthorizationOperation);
