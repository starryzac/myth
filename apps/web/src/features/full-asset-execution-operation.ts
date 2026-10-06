import { useSyncExternalStore } from 'react';
import { assetCanonicalJson as canonical, assetRequestHash, isFreshAssetExecutionRead, parseAssetExecute, parseAssetIntent, parseAssetLookup, parseAssetResponse, verifyAssetIntent } from '../api/full-asset-execution';
import type { AssetIntent, AssetPortfolio, AssetResponse } from '../api/full-asset-execution';
import { recoverDemoOperation } from './demo-operation';
import { recoverFullPolicyOperation } from './full-policy-operation';
import { recoverFullGoalOperation } from './full-goal-operation';
import { recoverOnboardingDraft } from './onboarding-draft';
import { recoverOneQuestionOperation } from './one-question-operation';
import { recoverSpendingEvidenceOperation } from './spending-evidence-operation';
import { recoverInterventionOperation } from './intervention-operation';
import { recoverGoalReleaseAuthorizationOperation } from './goal-release-authorization-operation';
import { recoverGoalCashReleaseOperation } from './goal-cash-release-operation';
import { isWriteInFlight } from './write-flight';

type State = { pending: AssetIntent | null; busy: boolean; recovering: boolean; storage_error: string | null };
let state: State = { pending: null, busy: false, recovering: false, storage_error: null }; let recovery: Promise<State> | null = null;
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((listener) => listener());
const storageKey = () => `bounded-funds-full-asset-execution-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export const getFullAssetExecutionOperation = () => state;
export function recoverFullAssetExecutionOperation(): Promise<State> {
  if (recovery) return recovery; state = { ...state, recovering: true }; emit();
  recovery = (async () => {
    try { const raw = sessionStorage.getItem(storageKey()); if (raw !== null) { const original = parseAssetIntent(JSON.parse(raw)); await verifyAssetIntent(original); state = { pending: freeze(original), busy: false, recovering: false, storage_error: null }; } else state = { ...state, recovering: false }; }
    catch { state = { ...state, recovering: false, storage_error: '资产组合完整原请求、键或hash无法读取；保留存储原件，禁止新提交。' }; }
    emit(); return state;
  })(); return recovery;
}
export async function prepareAssetIntent(kind: AssetIntent['kind'], userId: string, body: AssetIntent['body'], portfolio: AssetPortfolio | null = null): Promise<AssetIntent> {
  const original: AssetIntent = { protocol: 'full-asset-browser-command-v1', kind, user_id: userId, portfolio_id: portfolio?.portfolio_id ?? null, path: kind === 'PREPARE' ? '/full-asset-executions/prepare' : `/full-asset-executions/portfolios/${portfolio?.portfolio_id}/${kind === 'CONFIRM' ? 'confirm' : 'execute-next'}`, body: structuredClone(body), body_json: JSON.stringify(body), request_hash: await assetRequestHash(body), reviewed_portfolio: portfolio ? structuredClone(portfolio) : null };
  await verifyAssetIntent(original); return freeze(original);
}
async function otherFamilyBlocked(): Promise<boolean> {
  const cash = await recoverGoalCashReleaseOperation(); const values = [recoverDemoOperation(), recoverFullPolicyOperation(), recoverFullGoalOperation(), recoverOneQuestionOperation(), recoverSpendingEvidenceOperation(), recoverInterventionOperation(), recoverGoalReleaseAuthorizationOperation(), cash];
  const onboarding = recoverOnboardingDraft(); return values.some((value) => value.busy || value.pending || value.storage_error) || cash.recovering || !!(onboarding.busy || onboarding.draft.pending || onboarding.storage_error);
}
export async function beginFullAssetExecutionOperation(intent: AssetIntent, externallyBlocked = false): Promise<void> {
  await recoverFullAssetExecutionOperation(); await verifyAssetIntent(intent);
  const available = () => !externallyBlocked && !state.busy && !state.recovering && !state.storage_error && !isWriteInFlight();
  if (!available() || await otherFamilyBlocked() || !available()) throw new Error('原资产请求或其它族尚待核对，禁止新提交或换键。');
  if (state.pending && canonical(state.pending) !== canonical(intent)) throw new Error('原资产结果未知，不能换组合、批次、body或原键。');
  const original = state.pending ?? freeze(structuredClone(intent));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(original)); }
  catch { state = { ...state, pending: original, busy: false, storage_error: '完整资产body/key/批次无法持久保存，未发送；请保留原请求。' }; emit(); throw new Error(state.storage_error!); }
  state = { pending: original, busy: true, recovering: false, storage_error: null }; emit();
}
/** POST success, parsing errors, 4xx and network loss all retain the original. */
export function endFullAssetExecutionAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function acceptFullAssetExecutionRead(intent: AssetIntent, value: unknown): Promise<{ complete: boolean; original: AssetResponse | null }> {
  const same = () => !state.busy && state.pending !== null && canonical(state.pending) === canonical(intent);
  if (!same() || typeof value !== 'object' || value === null || !isFreshAssetExecutionRead(value)) throw new Error('仅独立原GET可核对恢复；POST/latest或另一请求不能清门。');
  await verifyAssetIntent(intent); let original: AssetResponse | null;
  if (intent.kind === 'EXECUTE') {
    original = await parseAssetResponse(value, intent.user_id, intent.reviewed_portfolio!);
    const body = parseAssetExecute(intent.body);
    const batch = original.batches.find((row) => row.batch_number === body.expected_batch_number && row.action_id === body.expected_action_id);
    if (!batch?.original_action || !['SUCCEEDED', 'RECONCILED'].includes(batch.original_action.status) || batch.original_action.bank_status !== 'SETTLED' || !batch.original_action.receipt || !batch.original_trace_verified) return { complete: false, original };
  } else {
    const lookup = await parseAssetLookup(value, intent); original = lookup.original;
    if (lookup.status !== 'RECORDED') return { complete: false, original };
  }
  if (!same()) throw new Error('只读核验期间原资产请求改变，未解除。');
  try { sessionStorage.removeItem(storageKey()); state = { pending: null, busy: false, recovering: false, storage_error: null }; }
  catch { state = { ...state, storage_error: '原资产已核对但记录无法删除，新提交仍禁止。' }; emit(); throw new Error(state.storage_error!); }
  emit(); return { complete: true, original };
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useFullAssetExecutionOperation = () => useSyncExternalStore(subscribe, getFullAssetExecutionOperation, getFullAssetExecutionOperation);
