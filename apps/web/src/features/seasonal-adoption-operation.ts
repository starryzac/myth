import { useSyncExternalStore } from 'react';
import { seasonalCanonicalJson, isFreshSeasonalRead, parseSeasonalIntent, parseSeasonalLookup, parseSeasonalReceipt } from '../api/seasonal-reserve-adoptions';
import type { SeasonalIntent, SeasonalLookup, SeasonalReceipt } from '../api/seasonal-reserve-adoptions';
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
import { recoverDynamicGoalOperation } from './full-dynamic-goal-operation';
import { recoverFullRecoveryOperation } from './full-recovery-execution-operation';
import { recoverFutureIncomeOperation } from './future-income-operation';

type State = { pending: SeasonalIntent | null; original_receipt: SeasonalReceipt | null; busy: boolean; recovering: boolean; storage_error: string | null };
let state: State = { pending: null, original_receipt: null, busy: false, recovering: false, storage_error: null };
let recovery: Promise<State> | null = null;
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((fn) => fn());
const storageKey = () => `bounded-funds-seasonal-adoption-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
const same = (a: unknown, b: unknown) => seasonalCanonicalJson(a) === seasonalCanonicalJson(b);
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export const getSeasonalAdoptionOperation = () => state;
export function recoverSeasonalAdoptionOperation(): Promise<State> {
  if (recovery) return recovery; state = { ...state, recovering: true }; emit();
  recovery = (async () => {
    try { const raw = sessionStorage.getItem(storageKey()); if (raw !== null) { const value: unknown = JSON.parse(raw); if (!object(value) || Object.keys(value).sort().join('|') !== 'original_receipt|pending') throw new Error('原件形状不完整'); const pending = value.pending === null ? null : await parseSeasonalIntent(value.pending), receipt = value.original_receipt === null ? null : await parseSeasonalReceipt(value.original_receipt); state = { pending: pending ? freeze(pending) : null, original_receipt: receipt ? freeze(receipt) : null, busy: false, recovering: false, storage_error: null }; } else state = { ...state, recovering: false }; }
    catch { state = { ...state, recovering: false, storage_error: '季节采纳完整原请求或历史回执无法恢复；保留原存储，禁止新提交。' }; }
    emit(); return state;
  })(); return recovery;
}
async function otherBlocked(): Promise<boolean> {
  const values = await Promise.all([recoverDemoOperation(), recoverFullPolicyOperation(), recoverFullGoalOperation(), recoverOneQuestionOperation(), recoverSpendingEvidenceOperation(), recoverInterventionOperation(), recoverGoalReleaseAuthorizationOperation(), recoverGoalCashReleaseOperation(), recoverFullAssetExecutionOperation(), recoverFixedPaymentOperation(), recoverDynamicGoalOperation(), recoverFullRecoveryOperation(), recoverFutureIncomeOperation()]);
  const onboarding = recoverOnboardingDraft(); return values.some((v) => v.busy || v.pending || v.storage_error || 'recovering' in v && v.recovering) || !!(onboarding.busy || onboarding.draft.pending || onboarding.storage_error);
}
function persist(pending: SeasonalIntent | null, receipt: SeasonalReceipt | null): void {
  const copy = freeze(structuredClone({ pending, original_receipt: receipt }));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(copy)); state = { ...state, ...copy }; }
  catch { state = { ...state, busy: false, storage_error: '无法保存季节采纳完整原件，禁止发送或解除原请求。' }; if (pending) state = { ...state, pending: copy.pending }; emit(); throw new Error(state.storage_error!); }
}
export async function beginSeasonalAdoptionOperation(intent: SeasonalIntent, externallyBlocked = false): Promise<void> {
  await recoverSeasonalAdoptionOperation(); await parseSeasonalIntent(intent);
  const blocked = () => externallyBlocked || !!state.pending || state.busy || state.recovering || !!state.storage_error || isWriteInFlight();
  if (blocked() || await otherBlocked() || blocked()) throw new Error('原请求或其他族尚待核对，禁止新采纳或换键');
  persist(intent, state.original_receipt); state = { ...state, busy: true }; emit();
}
/** POST success, rejection and lost response all keep the exact original command. */
export function endSeasonalAdoptionAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function acceptSeasonalAdoptionRead(intent: SeasonalIntent, lookup: SeasonalLookup): Promise<boolean> {
  const original = state.pending;
  if (!original || state.busy || !same(original, intent) || !isFreshSeasonalRead(lookup)) throw new Error('必须独立GET核对完整原请求，POST或存储不能清门');
  const verified = await parseSeasonalLookup(lookup, intent);
  if (state.pending !== original || state.busy) throw new Error('核对期间原请求变化');
  if (verified.status !== 'RECORDED' || !verified.original_receipt) return false;
  persist(null, verified.original_receipt); emit(); return true;
}
function subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; }
export const useSeasonalAdoptionOperation = () => useSyncExternalStore(subscribe, getSeasonalAdoptionOperation, getSeasonalAdoptionOperation);
