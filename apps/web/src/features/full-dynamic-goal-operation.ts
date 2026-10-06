import { useSyncExternalStore } from 'react';
import { dynamicCanonicalJson as canonical, dynamicRequestHash as hash, dynamicCheck, isFreshDynamicExecutionRead, parseDynamicIntent, parseDynamicLookup } from '../api/full-dynamic-goal-execution';
import type { DynamicAction, DynamicIntent, DynamicPrepare, DynamicLookup } from '../api/full-dynamic-goal-execution';
import { recoverDemoOperation } from './demo-operation';
import { recoverFullPolicyOperation } from './full-policy-operation';
import { recoverFullGoalOperation } from './full-goal-operation';
import { recoverOnboardingDraft } from './onboarding-draft';
import { recoverOneQuestionOperation } from './one-question-operation';
import { recoverSpendingEvidenceOperation } from './spending-evidence-operation';
import { recoverInterventionOperation } from './intervention-operation';
import { recoverGoalReleaseAuthorizationOperation } from './goal-release-authorization-operation';
import { recoverGoalCashReleaseOperation } from './goal-cash-release-operation';
import { recoverFullAssetExecutionOperation } from './full-asset-execution-operation';
import { recoverFixedPaymentOperation } from './fixed-payment-operation';
import { isWriteInFlight } from './write-flight';
import { object } from './policy-form';

type State = { pending: DynamicIntent | null; workspace: DynamicLookup | null; busy: boolean; recovering: boolean; storage_error: string | null };
let state: State = { pending: null, workspace: null, busy: false, recovering: false, storage_error: null }; let recovery: Promise<State> | null = null;
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((listener) => listener());
const storageKey = () => `bounded-funds-full-dynamic-goal-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
const workspaceKey = () => `${storageKey()}:workspace`;
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export const getDynamicGoalOperation = () => state;
export function recoverDynamicGoalOperation(): Promise<State> {
  if (recovery) return recovery; state = { ...state, recovering: true }; emit();
  recovery = (async () => { try { const raw = sessionStorage.getItem(storageKey()), saved = sessionStorage.getItem(workspaceKey()); let workspace: DynamicLookup | null = null;
      if (saved !== null) { const parsed: unknown = JSON.parse(saved); dynamicCheck(object(parsed) && parsed.status === 'RECORDED' && typeof parsed.user_id === 'string' && object(parsed.original_request)); const intent = await prepareDynamicGoalIntent('PREPARE', parsed.user_id, parsed.original_request as DynamicPrepare); workspace = freeze(await parseDynamicLookup(parsed, intent)); }
      state = { ...state, workspace, recovering: false }; if (raw !== null) state = { ...state, pending: freeze(await parseDynamicIntent(JSON.parse(raw))), busy: false, recovering: false, storage_error: null }; }
    catch { state = { ...state, recovering: false, storage_error: '动态目标原body/key/hash无法完整读取，保留存储原件并禁止新动作。' }; }
    emit(); return state;
  })(); return recovery;
}
export async function prepareDynamicGoalIntent(kind: DynamicIntent['kind'], user: string, prepare: DynamicPrepare, action: DynamicAction | null = null): Promise<DynamicIntent> {
  const body: DynamicIntent['body'] = kind === 'PREPARE' ? structuredClone(prepare) : kind === 'CONFIRM' ? { accepted: true as const, effect_hash: action?.effect_hash ?? '' } : {};
  const intent: DynamicIntent = { protocol: 'full-dynamic-goal-browser-v1', kind, user_id: user, prepare_request: structuredClone(prepare), action: action ? structuredClone(action) : null, path: kind === 'PREPARE' ? '/dynamic-goal-actions/prepare' : `/actions/${action?.action_id}/${kind === 'CONFIRM' ? 'confirm' : 'execute'}`, body, body_json: JSON.stringify(body), request_hash: await hash(body) };
  await parseDynamicIntent(intent); return freeze(intent);
}
async function otherBlocked(): Promise<boolean> {
  const asyncStates = await Promise.all([recoverGoalCashReleaseOperation(), recoverFullAssetExecutionOperation(), recoverFixedPaymentOperation()]);
  const values = [recoverDemoOperation(), recoverFullPolicyOperation(), recoverFullGoalOperation(), recoverOneQuestionOperation(), recoverSpendingEvidenceOperation(), recoverInterventionOperation(), recoverGoalReleaseAuthorizationOperation(), ...asyncStates];
  const onboarding = recoverOnboardingDraft(); return values.some((value) => !!(value.pending || value.busy || value.storage_error)) || asyncStates.some((value) => value.recovering) || !!(onboarding.busy || onboarding.draft.pending || onboarding.storage_error);
}
export async function beginDynamicGoalOperation(intent: DynamicIntent, externallyBlocked = false): Promise<void> {
  await recoverDynamicGoalOperation(); await parseDynamicIntent(intent);
  const available = () => !externallyBlocked && !state.busy && !state.recovering && !state.storage_error && !isWriteInFlight();
  if (!available() || await otherBlocked() || !available()) throw new Error('原动态目标或其它族请求尚待核对，禁止新动作。');
  if (state.pending && canonical(state.pending) !== canonical(intent)) throw new Error('动态目标原请求未决，不能换键、目标、周期、金额或动作。');
  if (state.workspace && isDynamicGoalWorkspaceUnresolved(state.workspace)) {
    if (intent.kind === 'PREPARE' || canonical(intent.prepare_request) !== canonical(state.workspace.original_request) || intent.action?.action_id !== state.workspace.action?.action_id) throw new Error('尚有固定原目标动作未终局，不能用另一键或动作替换工作区。');
  }
  const original = state.pending ?? freeze(structuredClone(intent));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(original)); }
  catch { state = { ...state, pending: original, busy: false, storage_error: '完整动态目标原请求无法保存，未发送；禁止新动作。' }; emit(); throw new Error(state.storage_error!); }
  state = { ...state, pending: original, busy: true, recovering: false, storage_error: null }; emit();
}
/** HTTP success, 4xx, invalid JSON and network loss all leave the same original pending. */
export function endDynamicGoalAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function acceptDynamicGoalRead(intent: DynamicIntent, value: unknown): Promise<{ complete: boolean; lookup: DynamicLookup }> {
  const same = () => !state.busy && state.pending !== null && canonical(state.pending) === canonical(intent);
  dynamicCheck(same() && typeof value === 'object' && value !== null && isFreshDynamicExecutionRead(value));
  const lookup = await parseDynamicLookup(value, intent); let complete = lookup.status === 'RECORDED';
  if (complete && intent.kind === 'CONFIRM') complete = lookup.confirmation_status === 'VERIFIED_AT_CONFIRMATION' && lookup.confirmation !== null;
  if (complete && intent.kind === 'EXECUTE') complete = lookup.action !== null && ['SUCCEEDED', 'RECONCILED'].includes(lookup.action.status) && lookup.action.bank_status === 'SETTLED' && lookup.action.receipt !== null;
  if (!complete) return { complete: false, lookup }; dynamicCheck(same());
  try { sessionStorage.setItem(workspaceKey(), JSON.stringify(lookup)); sessionStorage.removeItem(storageKey()); state = { pending: null, workspace: freeze(lookup), busy: false, recovering: false, storage_error: null }; }
  catch { state = { ...state, storage_error: '原动态结果已核对但记录无法删除，新动作仍禁止。' }; emit(); throw new Error(state.storage_error!); }
  emit(); return { complete: true, lookup };
}
export function isDynamicGoalWorkspaceUnresolved(value: DynamicLookup): boolean {
  const a = value.action; if (!a) return true;
  if (['SUCCEEDED', 'RECONCILED'].includes(a.status) && a.receipt && a.bank_status === 'SETTLED') return false;
  return !(['FAILED', 'INVALIDATED', 'CANCELLED'].includes(a.status) && [null, 'REJECTED'].includes(a.bank_status ?? null));
}
export async function dynamicGoalWorkspaceIntent(): Promise<DynamicIntent> { dynamicCheck(state.workspace?.original_request); return prepareDynamicGoalIntent('PREPARE', state.workspace.user_id, state.workspace.original_request); }
export async function acceptDynamicGoalWorkspaceRead(intent: DynamicIntent, value: unknown): Promise<DynamicLookup> {
  dynamicCheck(!state.pending && !state.busy && state.workspace?.action && typeof value === 'object' && value !== null && isFreshDynamicExecutionRead(value)); const before = state.workspace;
  const lookup = await parseDynamicLookup(value, intent); dynamicCheck(lookup.status === 'RECORDED' && lookup.action?.action_id === before.action?.action_id && lookup.action?.effect_hash === before.action?.effect_hash && canonical(lookup.original_request) === canonical(before.original_request) && state.workspace === before && !state.pending && !state.busy);
  try { sessionStorage.setItem(workspaceKey(), JSON.stringify(lookup)); state = { ...state, workspace: freeze(lookup) }; }
  catch { state = { ...state, storage_error: '原目标动作只读更新无法保存，禁止新动作。' }; emit(); throw new Error(state.storage_error!); }
  emit(); return lookup;
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useDynamicGoalOperation = () => useSyncExternalStore(subscribe, getDynamicGoalOperation, getDynamicGoalOperation);
