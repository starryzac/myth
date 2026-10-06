import { useSyncExternalStore } from 'react';
import { jointCanonical, jointCheck, jointRequestHash, jointReviewBinding, isFreshJointExecutionRead, parseJointBinding, parseJointIntent, parseJointLookup, parseJointResponse } from '../api/full-joint-goal-execution';
import type { JointIntent, JointPlan, JointResponse, JointReviewBinding } from '../api/full-joint-goal-execution';
import { object } from './policy-form';
import { isWriteInFlight } from './write-flight';
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
import { recoverFullRecoveryOperation, isFullRecoveryWorkspaceUnresolved } from './full-recovery-execution-operation';
import { recoverFullMaturityOperation, isFullMaturityWorkspaceUnresolved } from './full-maturity-execution-operation';
import { recoverFutureIncomeOperation } from './future-income-operation';
import { recoverSeasonalAdoptionOperation } from './seasonal-adoption-operation';
import { recoverRecoveryComposedObservationOperation } from './recovery-composed-observation-operation';

type State = { pending: JointIntent | null; workspace_reference: JointReviewBinding | null; original: JointResponse | null; pending_read_verified: boolean; busy: boolean; recovering: boolean; storage_error: string | null };
let state: State = { pending: null, workspace_reference: null, original: null, pending_read_verified: false, busy: false, recovering: false, storage_error: null }, recovery: Promise<State> | null = null;
const listeners = new Set<() => void>(), emit = () => listeners.forEach((listener) => listener());
const storageKey = () => `bounded-funds-joint-goal-operation-v2:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
const serialized = (pending: JointIntent | null, workspace_reference: JointReviewBinding | null) => JSON.stringify({ protocol: 'joint-goal-workspace-v2', pending, workspace_reference });
export const getFullJointGoalOperation = () => state;
export function recoverFullJointGoalOperation(): Promise<State> {
  if (recovery) return recovery; state = { ...state, recovering: true }; emit();
  recovery = (async () => {
    try {
      const raw = sessionStorage.getItem(storageKey()); let pending: JointIntent | null = null, reference: JointReviewBinding | null = null;
      if (raw !== null) { const saved: unknown = JSON.parse(raw); jointCheck(object(saved) && Object.keys(saved).sort().join('|') === 'pending|protocol|workspace_reference' && saved.protocol === 'joint-goal-workspace-v2'); pending = saved.pending === null ? null : freeze(await parseJointIntent(saved.pending)); reference = saved.workspace_reference === null ? null : freeze(await parseJointBinding(saved.workspace_reference)); if (pending && reference) jointCheck(pending.user_id === reference.user_id && pending.body.expected_epoch_id === reference.epoch_id && (pending.kind === 'PREPARE' ? jointCanonical(pending.body) === jointCanonical(reference.original_request) : jointCanonical(pending.reviewed_plan) === jointCanonical(reference))); }
      state = { pending, workspace_reference: reference, original: null, pending_read_verified: false, busy: false, recovering: false, storage_error: null };
    } catch { state = { ...state, busy: false, recovering: false, storage_error: '联合原body/key/hash/固定子工作区无法读取，保留原存储并禁止新提交。' }; }
    emit(); return state;
  })(); return recovery;
}
export async function prepareJointIntent(kind: JointIntent['kind'], user: string, body: JointIntent['body'], plan: JointPlan | null = null): Promise<JointIntent> {
  const intent: JointIntent = { protocol: 'joint-goal-browser-command-v2', kind, user_id: user, plan_id: plan?.plan_id ?? null, path: kind === 'PREPARE' ? '/joint-goal-actions/prepare' : `/joint-goal-actions/${plan?.plan_id}/${kind === 'CONFIRM' ? 'confirm' : 'execute-child'}`, body: structuredClone(body), body_json: JSON.stringify(body), request_hash: await jointRequestHash(body), reviewed_plan: plan ? jointReviewBinding(plan) : null }; return freeze(await parseJointIntent(intent));
}
async function otherBlocked(): Promise<boolean> {
  const [cash, asset, payment, dynamic, recoveryState, maturity, future, seasonal, composed] = await Promise.all([recoverGoalCashReleaseOperation(), recoverFullAssetExecutionOperation(), recoverFixedPaymentOperation(), recoverDynamicGoalOperation(), recoverFullRecoveryOperation(), recoverFullMaturityOperation(), recoverFutureIncomeOperation(), recoverSeasonalAdoptionOperation(), recoverRecoveryComposedObservationOperation()]);
  const all = [cash, asset, payment, dynamic, recoveryState, maturity, future, seasonal, composed, recoverDemoOperation(), recoverFullPolicyOperation(), recoverFullGoalOperation(), recoverOneQuestionOperation(), recoverSpendingEvidenceOperation(), recoverInterventionOperation(), recoverGoalReleaseAuthorizationOperation()];
  const onboarding = recoverOnboardingDraft();
  return all.some((s) => !!(s.pending || s.busy || s.storage_error)) || [cash, asset, payment, dynamic, recoveryState, maturity, future, seasonal, composed].some((s) => s.recovering) || !!(dynamic.workspace && isDynamicGoalWorkspaceUnresolved(dynamic.workspace)) || !!(recoveryState.workspace && isFullRecoveryWorkspaceUnresolved(recoveryState.workspace)) || !!(maturity.workspace && isFullMaturityWorkspaceUnresolved(maturity.workspace)) || !!(onboarding.busy || onboarding.draft.pending || onboarding.storage_error);
}
/** A stored/historical stop or a parent confirmation does not prove settlement. */
export function isJointWorkspaceUnresolved(value: JointResponse): boolean { return !value.children.every((row) => row.state === 'ORIGINAL_RECEIPT_VERIFIED' && row.original_action?.bank_status === 'SETTLED' && row.original_action.receipt !== null && ['SUCCEEDED', 'RECONCILED'].includes(row.original_action.status)); }
/** Includes restored locators even when no full server read exists this invocation. */
export function isFullJointGoalWorkspaceUnresolved(): boolean { return state.workspace_reference !== null; }
export async function beginFullJointGoalOperation(intent: JointIntent, externallyBlocked = false): Promise<void> {
  await recoverFullJointGoalOperation(); await parseJointIntent(intent);
  const available = () => !externallyBlocked && !state.busy && !state.recovering && !state.storage_error && !isWriteInFlight();
  if (!available() || await otherBlocked() || !available()) throw new Error('联合原请求或其它族尚待核对，禁止新动作。');
  if (state.pending && jointCanonical(state.pending) !== jointCanonical(intent)) throw new Error('原联合请求未决，不能换父计划、子动作、body或原键。');
  const reference = state.workspace_reference;
  if (reference && (intent.user_id !== reference.user_id || intent.body.expected_epoch_id !== reference.epoch_id || (intent.kind === 'PREPARE' ? jointCanonical(intent.body) !== jointCanonical(reference.original_request) : jointCanonical(intent.reviewed_plan) !== jointCanonical(reference)))) throw new Error('固定联合计划尚未全部终局，不能创建第二计划或替换原子身份。');
  if (!state.pending && (intent.kind !== 'PREPARE' || reference)) jointCheck(state.original && isFreshJointExecutionRead(state.original) && jointCanonical(jointReviewBinding(state.original.original_plan)) === jointCanonical(intent.kind === 'PREPARE' ? reference : intent.reviewed_plan));
  if (!state.pending && intent.kind === 'EXECUTE') { jointCheck(state.original && 'expected_child_number' in intent.body); const number = intent.body.expected_child_number, child = state.original.children[number - 1]; jointCheck(child && child.action_id === intent.body.expected_action_id && ['PLANNED_UNRESERVED', 'AUTHORIZED', 'SUBMITTED', 'UNKNOWN', 'ORIGINAL_RECEIPT_VERIFIED'].includes(child.state) && state.original.children.slice(0, number - 1).every((row) => row.state === 'ORIGINAL_RECEIPT_VERIFIED')); }
  const original = state.pending ?? freeze(structuredClone(intent));
  try { sessionStorage.setItem(storageKey(), serialized(original, state.workspace_reference)); }
  catch { state = { ...state, pending: original, storage_error: '无法持久保存完整原联合请求，未发送；禁止新请求。' }; emit(); throw new Error(state.storage_error!); }
  state = { ...state, pending: original, pending_read_verified: false, busy: true, recovering: false, storage_error: null }; emit();
}
/** Every POST outcome, including success and 4xx, retains the exact original. */
export function endFullJointGoalAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function acceptFullJointGoalRead(intent: JointIntent, value: unknown): Promise<{ complete: boolean; original: JointResponse | null }> {
  const same = () => !state.busy && state.pending !== null && jointCanonical(state.pending) === jointCanonical(intent);
  jointCheck(same() && typeof value === 'object' && value !== null && isFreshJointExecutionRead(value)); await parseJointIntent(intent);
  let original: JointResponse | null, complete = false;
  if (intent.kind === 'EXECUTE') { original = await parseJointResponse(value, intent.user_id, intent.reviewed_plan); const body = intent.body; jointCheck('expected_child_number' in body); const child = original.children.find((row) => row.child_number === body.expected_child_number && row.action_id === body.expected_action_id); complete = child?.state === 'ORIGINAL_RECEIPT_VERIFIED' && child.original_action?.bank_status === 'SETTLED' && child.original_action.receipt !== null && ['SUCCEEDED', 'RECONCILED'].includes(child.original_action.status); }
  else { const lookup = await parseJointLookup(value, intent); original = lookup.original; complete = lookup.status === 'RECORDED'; }
  jointCheck(same()); if (!complete) { state = { ...state, pending_read_verified: true, original: original ? freeze(original) : state.original }; emit(); return { complete: false, original }; }
  jointCheck(original !== null);
  const reference = isJointWorkspaceUnresolved(original) ? jointReviewBinding(original.original_plan) : null;
  try { sessionStorage.setItem(storageKey(), serialized(null, reference)); }
  catch { state = { ...state, storage_error: '原联合结果已核对但无法保存，新动作仍禁止。' }; emit(); throw new Error(state.storage_error!); }
  state = { pending: null, workspace_reference: reference ? freeze(reference) : null, original: freeze(original), pending_read_verified: false, busy: false, recovering: false, storage_error: null }; emit(); return { complete: true, original };
}
export async function acceptFullJointGoalWorkspaceRead(value: JointResponse): Promise<JointResponse> {
  const before = state.workspace_reference; jointCheck(!state.pending && !state.busy && !state.recovering && isFreshJointExecutionRead(value));
  const original = await parseJointResponse(value, before?.user_id, before); jointCheck(state.workspace_reference === before && !state.pending && !state.busy);
  const reference = isJointWorkspaceUnresolved(original) ? jointReviewBinding(original.original_plan) : null;
  try { sessionStorage.setItem(storageKey(), serialized(null, reference)); } catch { state = { ...state, storage_error: '原联合工作区只读更新无法保存，禁止新动作。' }; emit(); throw new Error(state.storage_error!); }
  state = { ...state, workspace_reference: reference ? freeze(reference) : null, original: freeze(original) }; emit(); return original;
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useFullJointGoalOperation = () => useSyncExternalStore(subscribe, getFullJointGoalOperation, getFullJointGoalOperation);
