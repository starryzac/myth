import { useSyncExternalStore } from 'react';
import { recoveryCanonicalJson as canonical, recoveryRequestHash as hash, recoveryCheck, isFreshRecoveryRead, parseRecoveryIntent, parseRecoveryLookup } from '../api/full-recovery-execution';
import type { RecoveryAction, RecoveryIntent, RecoveryLookup, RecoveryPrepare } from '../api/full-recovery-execution';
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
import { recoverDynamicGoalOperation, isDynamicGoalWorkspaceUnresolved } from './full-dynamic-goal-operation';
import { isWriteInFlight } from './write-flight';
import { object } from './policy-form';

type State = { pending: RecoveryIntent | null; workspace: RecoveryLookup | null; busy: boolean; recovering: boolean; storage_error: string | null };
let state: State = { pending: null, workspace: null, busy: false, recovering: false, storage_error: null }; let recovery: Promise<State> | null = null;
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((listener) => listener());
const storageKey = () => `bounded-funds-full-recovery-execution-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
const workspaceKey = () => `${storageKey()}:workspace`;
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export const getFullRecoveryOperation = () => state;
export function recoverFullRecoveryOperation(): Promise<State> {
  if (recovery) return recovery; state = { ...state, recovering: true }; emit();
  recovery = (async () => {
    try {
      const raw = sessionStorage.getItem(storageKey()), saved = sessionStorage.getItem(workspaceKey()); let workspace: RecoveryLookup | null = null;
      if (saved !== null) { const value: unknown = JSON.parse(saved); recoveryCheck(object(value) && value.status === 'RECORDED' && typeof value.user_id === 'string' && object(value.original_request)); const intent = await prepareRecoveryIntent('PREPARE', value.user_id, value.original_request as RecoveryPrepare); workspace = freeze(await parseRecoveryLookup(value, intent)); }
      state = { ...state, workspace, pending: raw === null ? null : freeze(await parseRecoveryIntent(JSON.parse(raw))), busy: false, recovering: false, storage_error: null };
    } catch { state = { ...state, busy: false, recovering: false, storage_error: '恢复原body/key/hash/USER同意无法完整读取，保留原存储并禁止新动作。' }; }
    emit(); return state;
  })(); return recovery;
}
export async function prepareRecoveryIntent(kind: RecoveryIntent['kind'], user: string, prepare: RecoveryPrepare, action: RecoveryAction | null = null): Promise<RecoveryIntent> {
  const body: RecoveryIntent['body'] = kind === 'PREPARE' ? structuredClone(prepare) : kind === 'CONFIRM' ? { expected_epoch_id: prepare.expected_epoch_id, reviewed_effect_hash: action?.effect_hash ?? '', accepted: true } : { expected_epoch_id: prepare.expected_epoch_id, reviewed_effect_hash: action?.effect_hash ?? '' };
  const intent: RecoveryIntent = { protocol: 'full-recovery-browser-v1', kind, user_id: user, prepare_request: structuredClone(prepare), action: action ? structuredClone(action) : null, path: kind === 'PREPARE' ? '/full-recovery-actions/prepare' : `/full-recovery-actions/actions/${action?.action_id}/${kind === 'CONFIRM' ? 'confirm' : 'execute'}`, body, body_json: JSON.stringify(body), request_hash: await hash(body) };
  await parseRecoveryIntent(intent); return freeze(intent);
}
async function otherBlocked(): Promise<boolean> {
  const dynamic = await recoverDynamicGoalOperation();
  const asyncStates = await Promise.all([recoverGoalCashReleaseOperation(), recoverFullAssetExecutionOperation(), recoverFixedPaymentOperation()]);
  const values = [recoverDemoOperation(), recoverFullPolicyOperation(), recoverFullGoalOperation(), recoverOneQuestionOperation(), recoverSpendingEvidenceOperation(), recoverInterventionOperation(), recoverGoalReleaseAuthorizationOperation(), dynamic, ...asyncStates];
  const onboarding = recoverOnboardingDraft(); return values.some((value) => !!(value.pending || value.busy || value.storage_error)) || [dynamic, ...asyncStates].some((value) => value.recovering) || !!(dynamic.workspace && isDynamicGoalWorkspaceUnresolved(dynamic.workspace)) || !!(onboarding.busy || onboarding.draft.pending || onboarding.storage_error);
}
export async function beginFullRecoveryOperation(intent: RecoveryIntent, externallyBlocked = false): Promise<void> {
  await recoverFullRecoveryOperation(); await parseRecoveryIntent(intent);
  const available = () => !externallyBlocked && !state.busy && !state.recovering && !state.storage_error && !isWriteInFlight();
  if (!available() || await otherBlocked() || !available()) throw new Error('原恢复动作或其它族请求尚待核对，禁止新动作。');
  if (state.pending && canonical(state.pending) !== canonical(intent)) throw new Error('恢复原请求未决，不能换键、仓位、轮次、金额或动作。');
  if (state.workspace && isFullRecoveryWorkspaceUnresolved(state.workspace) && (intent.kind === 'PREPARE' || canonical(intent.prepare_request) !== canonical(state.workspace.original_request) || intent.action?.action_id !== state.workspace.action?.action_id)) throw new Error('固定原恢复动作未终局，不能换键创建另一动作。');
  const original = state.pending ?? freeze(structuredClone(intent));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(original)); }
  catch { state = { ...state, pending: original, busy: false, storage_error: '完整原恢复请求无法保存，未发送；禁止新动作。' }; emit(); throw new Error(state.storage_error!); }
  state = { ...state, pending: original, busy: true, recovering: false, storage_error: null }; emit();
}
/** POST success/4xx/network loss never releases the original pending. */
export function endFullRecoveryAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function acceptFullRecoveryRead(intent: RecoveryIntent, value: unknown): Promise<{ complete: boolean; lookup: RecoveryLookup }> {
  const same = () => !state.busy && state.pending !== null && canonical(state.pending) === canonical(intent);
  recoveryCheck(same() && typeof value === 'object' && value !== null && isFreshRecoveryRead(value)); const lookup = await parseRecoveryLookup(value, intent);
  let complete = lookup.status === 'RECORDED';
  if (complete && intent.kind === 'CONFIRM') complete = lookup.original_consent !== null;
  if (complete && intent.kind === 'EXECUTE') complete = lookup.original_consent !== null && lookup.action !== null && ['SUCCEEDED', 'RECONCILED'].includes(lookup.action.status) && lookup.action.bank_status === 'SETTLED' && lookup.action.receipt !== null;
  if (!complete) return { complete: false, lookup }; recoveryCheck(same());
  try { sessionStorage.setItem(workspaceKey(), JSON.stringify(lookup)); sessionStorage.removeItem(storageKey()); state = { pending: null, workspace: freeze(lookup), busy: false, recovering: false, storage_error: null }; }
  catch { state = { ...state, storage_error: '原恢复结果已核对但存储无法关闭，新动作仍禁止。' }; emit(); throw new Error(state.storage_error!); }
  emit(); return { complete: true, lookup };
}
export function isFullRecoveryWorkspaceUnresolved(value: RecoveryLookup): boolean { const a = value.action; if (!a) return true; if (['SUCCEEDED', 'RECONCILED'].includes(a.status) && a.bank_status === 'SETTLED' && a.receipt) return false; return !(['FAILED', 'INVALIDATED', 'CANCELLED'].includes(a.status) && [null, 'REJECTED'].includes(a.bank_status ?? null)); }
export async function fullRecoveryWorkspaceIntent(): Promise<RecoveryIntent> { recoveryCheck(state.workspace?.original_request); return prepareRecoveryIntent('PREPARE', state.workspace.user_id, state.workspace.original_request); }
export async function acceptFullRecoveryWorkspaceRead(intent: RecoveryIntent, value: unknown): Promise<RecoveryLookup> {
  recoveryCheck(!state.pending && !state.busy && state.workspace?.action && typeof value === 'object' && value !== null && isFreshRecoveryRead(value)); const before = state.workspace;
  const lookup = await parseRecoveryLookup(value, intent); recoveryCheck(lookup.status === 'RECORDED' && lookup.action?.action_id === before.action?.action_id && lookup.action?.effect_hash === before.action?.effect_hash && canonical(lookup.original_request) === canonical(before.original_request) && state.workspace === before && !state.pending && !state.busy);
  try { sessionStorage.setItem(workspaceKey(), JSON.stringify(lookup)); state = { ...state, workspace: freeze(lookup) }; }
  catch { state = { ...state, storage_error: '原恢复工作区只读更新无法保存，禁止新动作。' }; emit(); throw new Error(state.storage_error!); }
  emit(); return lookup;
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useFullRecoveryOperation = () => useSyncExternalStore(subscribe, getFullRecoveryOperation, getFullRecoveryOperation);
