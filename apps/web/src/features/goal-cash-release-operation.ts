import { useSyncExternalStore } from 'react';
import { isFreshCashRead, parseCashAction, parseCashExecute, parseCashIntent, parseCashLookup } from '../api/goal-cash-releases';
import type { CashAction, CashExecute, CashIntent, CashLookup } from '../api/goal-cash-releases';
import { recoverDemoOperation } from './demo-operation';
import { recoverFullPolicyOperation } from './full-policy-operation';
import { recoverFullGoalOperation } from './full-goal-operation';
import { recoverOnboardingDraft } from './onboarding-draft';
import { recoverOneQuestionOperation } from './one-question-operation';
import { recoverSpendingEvidenceOperation } from './spending-evidence-operation';
import { recoverInterventionOperation } from './intervention-operation';
import { recoverGoalReleaseAuthorizationOperation } from './goal-release-authorization-operation';
import { isWriteInFlight } from './write-flight';
import { object } from './policy-form';

export type CashPending = { intent: CashIntent; original_action: CashAction | null; execute_body: CashExecute | null; execute_json: string | null };
type State = { pending: CashPending | null; busy: boolean; storage_error: string | null; recovering: boolean };
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((fn) => fn());
let state: State = { pending: null, busy: false, storage_error: null, recovering: false }; let recovered = false;
// An original GET records only historical settlement identity, never current permission.
// Replacing or restoring pending bytes invalidates this invocation-local read marker.
const readSettlement = new WeakSet<CashPending>();
const storageKey = () => `bounded-funds-goal-cash-release-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export const getGoalCashReleaseOperation = () => state;
export async function recoverGoalCashReleaseOperation(): Promise<State> {
  if (recovered) return state; recovered = true; state = { ...state, recovering: true }; emit();
  try {
    const raw = sessionStorage.getItem(storageKey());
    if (raw !== null) {
      const value: unknown = JSON.parse(raw); if (!object(value) || Object.keys(value).sort().join('|') !== 'execute_body|execute_json|intent|original_action') throw new Error('INVALID_ORIGINAL');
      const intent = await parseCashIntent(value.intent), original = value.original_action === null ? null : await parseCashAction(value.original_action, intent);
      const body = value.execute_body === null ? null : parseCashExecute(value.execute_body);
      if (body && (!original || body.reviewed_effect_hash !== original.original_command.effect_hash || body.expected_epoch_id !== intent.body.expected_epoch_id || value.execute_json !== JSON.stringify(body)) || !body && value.execute_json !== null) throw new Error('INVALID_ORIGINAL');
      state = { pending: freeze({ intent, original_action: original, execute_body: body, execute_json: value.execute_json as string | null }), busy: false, storage_error: null, recovering: false };
    } else state = { ...state, recovering: false };
  } catch { state = { ...state, recovering: false, storage_error: '完整回拨原body/action/effect/确认记录无法读取；保留存储原件并禁止新POST。' }; }
  emit(); return state;
}
function blockedByFamily(): boolean {
  const values = [recoverDemoOperation(), recoverFullPolicyOperation(), recoverFullGoalOperation(), recoverOneQuestionOperation(), recoverSpendingEvidenceOperation(), recoverInterventionOperation(), recoverGoalReleaseAuthorizationOperation()];
  const onboarding = recoverOnboardingDraft(); return values.some((v) => v.busy || v.pending || v.storage_error) || !!(onboarding.busy || onboarding.draft.pending || onboarding.storage_error);
}
function persist(pending: CashPending): void {
  const original = freeze(structuredClone(pending));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(original)); state = { ...state, pending: original }; }
  catch { state = { ...state, pending: original, busy: false, storage_error: '无法保存完整回拨原请求；未开始POST，原身份需保留核对。' }; emit(); throw new Error(state.storage_error!); }
}
export async function beginCashPrepare(intent: CashIntent, externallyBlocked = false): Promise<void> {
  await recoverGoalCashReleaseOperation(); await parseCashIntent(intent);
  if (externallyBlocked || state.pending || state.busy || state.recovering || state.storage_error || isWriteInFlight() || blockedByFamily()) throw new Error('其他族或原回拨待核对，禁止新prepare/换键');
  persist({ intent, original_action: null, execute_body: null, execute_json: null }); state = { ...state, busy: true }; emit();
}
export async function retainCashPostResponse(intent: CashIntent, value: unknown): Promise<CashAction> {
  const before = state.pending;
  if (!before || JSON.stringify(before.intent) !== JSON.stringify(intent)) throw new Error('不能用另一原请求覆盖');
  const action = await parseCashAction(value, intent, before.original_action);
  if (state.pending !== before) throw new Error('核验期间原请求发生变化'); persist({ ...before, original_action: action }); emit(); return action;
}
export function endCashAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function acceptCashRead(intent: CashIntent, result: CashLookup): Promise<boolean> {
  const before = state.pending; if (!before || state.busy || !isFreshCashRead(result) || JSON.stringify(before.intent) !== JSON.stringify(intent)) throw new Error('需要当前原请求的独立GET，POST不能清门');
  const checked = await parseCashLookup(result, intent, before.original_action);
  if (state.pending !== before || state.busy) throw new Error('另一原请求正在处理');
  if (checked.status !== 'RECORDED') return false;
  const original = checked.original!;
  if (!original.service_receipt_verified || !['SUCCEEDED', 'RECONCILED'].includes(original.original_action_status)) {
    persist({ ...before, original_action: original });
    if (original.original_bank_status === 'SETTLED' && original.bank_settlement_legs_verified) readSettlement.add(state.pending!);
    emit(); return false;
  }
  try { sessionStorage.removeItem(storageKey()); state = { pending: null, busy: false, storage_error: null, recovering: false }; }
  catch { state = { ...state, storage_error: '原回执已核对但无法删除恢复记录；新prepare仍禁止。' }; emit(); throw new Error(state.storage_error!); }
  emit(); return true;
}
export async function beginCashExecute(body: CashExecute, externallyBlocked = false): Promise<CashPending> {
  await recoverGoalCashReleaseOperation(); const before = state.pending; parseCashExecute(body);
  if (!before?.original_action || state.busy || state.recovering || state.storage_error || isWriteInFlight()) throw new Error('缺原行动或写入在途，禁止execute');
  const action = await parseCashAction(before.original_action, before.intent);
  const historical = action.original_bank_status === 'SETTLED' && action.bank_settlement_legs_verified && !action.service_receipt_verified;
  if (historical && !readSettlement.has(before)) throw new Error('已结算恢复也必须先独立GET原银行行动；旧POST或reload不是当前读取');
  if ((!historical && (externallyBlocked || blockedByFamily())) || body.reviewed_effect_hash !== action.original_command.effect_hash || body.expected_epoch_id !== before.intent.body.expected_epoch_id || before.execute_body && JSON.stringify(before.execute_body) !== JSON.stringify(body)) throw new Error('门被阻挡或不是原经济后果，不能execute');
  if (!historical && !['PLANNED', 'AUTHORIZED', 'SUBMITTED'].includes(action.original_action_status)) throw new Error('未知银行结果只可GET，不生成新的银行效果');
  if (state.pending !== before || state.busy || isWriteInFlight()) throw new Error('复核期间另一请求开始');
  persist({ ...before, execute_body: body, execute_json: JSON.stringify(body) }); state = { ...state, busy: true }; emit(); return state.pending!;
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useGoalCashReleaseOperation = () => useSyncExternalStore(subscribe, getGoalCashReleaseOperation, getGoalCashReleaseOperation);
