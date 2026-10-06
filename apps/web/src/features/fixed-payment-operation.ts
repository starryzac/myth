import { useSyncExternalStore } from 'react';
import { isFreshPaymentRead, parsePaymentAction, parsePaymentIntent, parsePaymentRead, parsePaymentReceipt, preparePaymentIntent } from '../api/full-payment-relations';
import type { PaymentAction, PaymentConsent, PaymentIntent, PaymentLookup, PaymentPrepared, PaymentRead, PaymentReceipt } from '../api/full-payment-relations';
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
import { object } from './policy-form';

export type PaymentWorkflow = { start: PaymentReceipt | null; authorization: PaymentReceipt | null; prepared: PaymentPrepared | null; action: PaymentAction | null; consent: PaymentConsent | null };
type State = { pending: PaymentIntent | null; workflow: PaymentWorkflow; busy: boolean; recovering: boolean; storage_error: string | null };
const empty = (): PaymentWorkflow => ({ start: null, authorization: null, prepared: null, action: null, consent: null });
let state: State = { pending: null, workflow: empty(), busy: false, recovering: false, storage_error: null }; let recovered = false;
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((fn) => fn());
const freshAction = new WeakSet<object>();
const storageKey = () => `bounded-funds-fixed-payment-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
export const getFixedPaymentOperation = () => state;
async function validateWorkflow(value: unknown): Promise<PaymentWorkflow> {
  if (!object(value) || Object.keys(value).sort().join('|') !== 'action|authorization|consent|prepared|start') throw new Error('恢复工作区字段不完整');
  if (value.start !== null) { const r = await parsePaymentReceipt(value.start); if (r.original.kind !== 'START') throw new Error('原发起缺失'); }
  if (value.authorization !== null) { const r = await parsePaymentReceipt(value.authorization); if (!object(value.start) || !object(value.start.original) || r.original.kind !== 'CONFIRM' || r.original.start_command_id !== value.start.original.command_id || !same(r.original.scope, value.start.original.scope)) throw new Error('原确认不是此发起'); }
  if (value.prepared !== null) {
    if (!object(value.authorization) || !object(value.authorization.original) || !object(value.prepared) || !object(value.prepared.original_binding)) throw new Error('准备缺原关系');
    const b = value.prepared.original_binding, authorization = await parsePaymentReceipt(value.authorization); const intent = await preparePaymentIntent('PREPARE', authorization.original.scope, b.original_prepare_request as PaymentIntent['body'], { authorization_id: authorization.original.command_id });
    await parsePaymentRead(value.prepared, intent);
    if (b.authorization_evidence_id !== authorization.evidence_id || b.authorization_evidence_hash !== authorization.evidence_hash) throw new Error('原准备不能更换关系证据');
  }
  if (value.action !== null) {
    if (!object(value.prepared) || !object(value.prepared.original_action) || !object(value.authorization) || !object(value.authorization.original)) throw new Error('动作缺原准备');
    await parsePaymentAction(value.action, value.authorization.original.scope as PaymentReceipt['original']['scope'], value.prepared.original_action as PaymentAction);
  }
  if (value.consent !== null) {
    if (!object(value.action) || !object(value.authorization) || !object(value.authorization.original) || !object(value.consent) || !object(value.consent.original_request)) throw new Error('同意缺原动作');
    const intent = await preparePaymentIntent('ACTION_CONFIRM', value.authorization.original.scope as PaymentReceipt['original']['scope'], value.consent.original_request as PaymentIntent['body'], { authorization_id: value.authorization.original.command_id as string, action: value.action as PaymentAction }); await parsePaymentRead(value.consent, intent);
  }
  return value as PaymentWorkflow;
}
export async function recoverFixedPaymentOperation(): Promise<State> {
  if (recovered) return state; recovered = true; state = { ...state, recovering: true }; emit();
  try { const raw = sessionStorage.getItem(storageKey()); if (raw !== null) { const value: unknown = JSON.parse(raw); if (!object(value) || Object.keys(value).sort().join('|') !== 'pending|workflow') throw new Error('恢复原件形状不完整'); const pending = value.pending === null ? null : await parsePaymentIntent(value.pending), workflow = await validateWorkflow(value.workflow); state = { pending: pending ? freeze(pending) : null, workflow: freeze(workflow), busy: false, recovering: false, storage_error: null }; } else state = { ...state, recovering: false }; }
  catch { state = { ...state, recovering: false, storage_error: '固定付款完整原请求/关系/动作无法恢复；保留原存储并禁止新写。' }; }
  emit(); return state;
}
async function otherBlocked(): Promise<boolean> {
  const values = [recoverDemoOperation(), recoverFullPolicyOperation(), recoverFullGoalOperation(), recoverOneQuestionOperation(), recoverSpendingEvidenceOperation(), recoverInterventionOperation(), recoverGoalReleaseAuthorizationOperation(), await recoverGoalCashReleaseOperation()];
  const onboarding = recoverOnboardingDraft(); return values.some((v) => v.busy || v.pending || v.storage_error || ('recovering' in v && v.recovering)) || !!(onboarding.busy || onboarding.draft.pending || onboarding.storage_error);
}
function persist(pending: PaymentIntent | null, workflow: PaymentWorkflow): void {
  const copy = freeze(structuredClone({ pending, workflow }));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(copy)); state = { ...state, ...copy }; }
  catch { state = { ...state, ...copy, busy: false, storage_error: '无法保存完整固定付款原件；禁止发送新请求，保留原身份。' }; emit(); throw new Error(state.storage_error!); }
}
export async function beginFixedPaymentOperation(intent: PaymentIntent, blocked = false): Promise<void> {
  await recoverFixedPaymentOperation(); await parsePaymentIntent(intent);
  if (blocked || state.pending || state.busy || state.recovering || state.storage_error || isWriteInFlight() || await otherBlocked()) throw new Error('原请求或其他族待核对，禁止新付款/换键');
  if (state.pending || state.busy || state.storage_error || isWriteInFlight()) throw new Error('复核期间已有写入');
  const w = state.workflow;
  if (intent.kind === 'START' && w.action && !['SUCCEEDED', 'RECONCILED'].includes(w.action.status)) throw new Error('原付款尚未终局，不能用新关系替换工作区');
  if (intent.kind === 'CONFIRM' && (!w.start || w.start.original.command_id !== intent.start_command_id || !same(w.start.original.scope, intent.scope))) throw new Error('缺原发起');
  if (['PREPARE', 'ACTION_CONFIRM', 'EXECUTE'].includes(intent.kind) && (!w.authorization || w.authorization.original.command_id !== intent.authorization_id || !same(w.authorization.original.scope, intent.scope))) throw new Error('缺原用户确认关系');
  if (intent.kind === 'PREPARE' && w.action && !['SUCCEEDED', 'RECONCILED'].includes(w.action.status)) throw new Error('原付款动作尚未终局，不能换键准备');
  if (intent.kind === 'ACTION_CONFIRM' || intent.kind === 'EXECUTE') {
    if (!w.action || !intent.action || !freshAction.has(w.action) || w.action.action_id !== intent.action.action_id || w.action.effect_hash !== intent.action.effect_hash) throw new Error('须先独立GET原动作再复核，历史存储不是当前读');
    if (!['PLANNED', 'AUTHORIZED'].includes(w.action.status) || ['SETTLED', 'UNKNOWN'].includes(w.action.bank_status ?? '')) throw new Error('在途或未知原动作只能恢复原件');
    if (!['AUTO_EXECUTE', 'ASK_ONCE'].includes(w.action.autonomy_level) || intent.kind === 'ACTION_CONFIRM' && w.action.autonomy_level !== 'ASK_ONCE') throw new Error('建议或阻挡等级不能用用户同意升级执行');
    if (intent.kind === 'EXECUTE' && w.action.autonomy_level !== 'AUTO_EXECUTE' && (w.consent?.status !== 'RECORDED' || w.consent.original?.original_effect_hash !== w.action.effect_hash)) throw new Error('ASK缺原签名用户同意');
  }
  persist(intent, intent.kind === 'START' ? empty() : w); state = { ...state, busy: true }; emit();
}
/** Every HTTP POST outcome, including successful bodies and 4xx, keeps this original. */
export function endFixedPaymentAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function acceptFixedPaymentRead(intent: PaymentIntent, value: PaymentRead): Promise<boolean> {
  const before = state.pending; if (!before || state.busy || !same(before, intent) || !isFreshPaymentRead(value)) throw new Error('必须独立GET核原请求，POST不能清门');
  const result = await parsePaymentRead(value, intent); if (state.pending !== before || state.busy) throw new Error('核对期间原请求已变化');
  if (intent.kind !== 'EXECUTE' && result.status !== 'RECORDED') return false;
  let workflow = { ...state.workflow };
  if (intent.kind === 'START') workflow = { ...empty(), start: (result as PaymentLookup).original! };
  if (intent.kind === 'CONFIRM') workflow = { ...workflow, authorization: (result as PaymentLookup).original!, prepared: null, action: null, consent: null };
  if (intent.kind === 'PREPARE') { const prepared = result as PaymentPrepared; if (!workflow.authorization || prepared.original_binding?.authorization_evidence_id !== workflow.authorization.evidence_id || prepared.original_binding.authorization_evidence_hash !== workflow.authorization.evidence_hash) throw new Error('原准备不能更换关系证据'); workflow = { ...workflow, prepared, action: prepared.original_action!, consent: null }; }
  if (intent.kind === 'ACTION_CONFIRM') workflow = { ...workflow, consent: result as PaymentConsent };
  if (intent.kind === 'EXECUTE') {
    const action = result as PaymentAction; workflow = { ...workflow, action };
    if (!['SUCCEEDED', 'RECONCILED'].includes(action.status) || action.bank_status !== 'SETTLED' || !action.receipt) { persist(before, workflow); freshAction.add(state.workflow.action!); emit(); return false; }
  }
  persist(null, workflow); if (state.workflow.action) freshAction.add(state.workflow.action); emit(); return true;
}
export async function retainFixedPaymentActionRead(action: PaymentAction): Promise<void> {
  const w = state.workflow; if (!w.authorization || !w.action || !isFreshPaymentRead(action) || state.busy) throw new Error('需要当前独立GET原动作');
  await parsePaymentAction(action, w.authorization.original.scope, w.action); if (state.workflow !== w || state.busy) throw new Error('原动作改变'); persist(state.pending, { ...w, action }); freshAction.add(state.workflow.action!); emit();
}
/** Explicitly resume only the same already-settled bank identity; never retry a new effect. */
export async function beginFixedPaymentSettlementResume(blocked = false): Promise<PaymentIntent> {
  const p = state.pending, a = state.workflow.action;
  if (blocked || !p || p.kind !== 'EXECUTE' || !a || !freshAction.has(a) || a.action_id !== p.action?.action_id || a.effect_hash !== p.action.effect_hash || a.bank_status !== 'SETTLED' || a.receipt || state.busy || state.recovering || state.storage_error || isWriteInFlight() || await otherBlocked()) throw new Error('只可显式恢复独立GET已验真的原SETTLED身份，其他写门仍须解除');
  await parsePaymentIntent(p); if (state.pending !== p || state.busy || state.storage_error || isWriteInFlight()) throw new Error('原身份改变'); state = { ...state, busy: true }; emit(); return p;
}
function subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; }
export const useFixedPaymentOperation = () => useSyncExternalStore(subscribe, getFixedPaymentOperation, getFixedPaymentOperation);
