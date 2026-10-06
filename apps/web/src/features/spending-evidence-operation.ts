import { useSyncExternalStore } from 'react';
import { object } from './policy-form';
import { isWriteInFlight } from './write-flight';
import { parseCategoryBody, parseCategoryResult, parseCategoryReview, spendingCommand, spendingDigest, spendingHash, spendingUUID } from '../api/spending-evidence';
import type { CategoryBody, CategoryReview, SpendingIntent } from '../api/spending-evidence';

type ReadReference = { protocol: 'spending-transaction-read-reference-v1'; user_id: string; transaction_id: string; epoch_id: string };
type State = { pending: SpendingIntent | null; busy: boolean; storage_error: string | null; transaction: ReadReference | null };
const keys = ['protocol', 'user_id', 'transaction_id', 'path', 'body', 'body_json', 'request_hash', 'bank_evidence_id', 'bank_evidence_hash', 'before_category'];
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export function validSpendingIntent(value: unknown): value is SpendingIntent {
  try { if (!object(value) || Object.keys(value).sort().join('|') !== keys.sort().join('|') || value.protocol !== 'spending-category-browser-command-v1' || !spendingUUID(value.user_id) || !spendingUUID(value.transaction_id) || !spendingUUID(value.bank_evidence_id) || !spendingDigest(value.bank_evidence_hash) || !spendingDigest(value.request_hash) || typeof value.before_category !== 'string' || typeof value.body_json !== 'string' || value.body_json.length > 10000 || value.path !== `/transactions/${value.transaction_id}/category-confirmation`) return false;
    parseCategoryBody(value.body); return JSON.stringify(value.body) === value.body_json;
  } catch { return false; }
}
export async function prepareSpendingIntent(review: CategoryReview, body: CategoryBody): Promise<SpendingIntent> {
  parseCategoryReview(review, review.user_id, review.transaction_id); parseCategoryBody(body);
  if (!review.first_confirmation_supported || body.expected_epoch_id !== review.epoch_id || body.reviewed_transaction_hash !== review.reviewed_transaction_hash || await spendingHash({ protocol: 'transaction-category-review-v1', epoch_id: review.epoch_id, transaction: review.transaction, bank_fact: review.bank_fact }) !== review.reviewed_transaction_hash) throw new Error('原银行交易复核hash、首次确认或模拟期不一致');
  const intent: SpendingIntent = { protocol: 'spending-category-browser-command-v1', user_id: review.user_id, transaction_id: review.transaction_id, path: `/transactions/${review.transaction_id}/category-confirmation`, body: structuredClone(body), body_json: JSON.stringify(body), request_hash: '', bank_evidence_id: String(review.bank_fact.evidence_id), bank_evidence_hash: String(review.bank_fact.evidence_hash), before_category: String(review.transaction.category) };
  intent.request_hash = await spendingHash(spendingCommand(intent)); if (!validSpendingIntent(intent)) throw new Error('原分类命令不完整，未发送'); return freeze(intent);
}
export const spendingIntentHashMatches = async (intent: SpendingIntent) => validSpendingIntent(intent) && await spendingHash(spendingCommand(intent)) === intent.request_hash;
let state: State = { pending: null, busy: false, storage_error: null, transaction: null }; let recovered = false;
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((listener) => listener());
const storageKey = () => `bounded-funds-spending-category-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
const referenceKey = () => `${storageKey()}:read-reference`;
function validReference(value: unknown): value is ReadReference { return object(value) && Object.keys(value).sort().join('|') === ['protocol', 'user_id', 'transaction_id', 'epoch_id'].sort().join('|') && value.protocol === 'spending-transaction-read-reference-v1' && spendingUUID(value.user_id) && spendingUUID(value.transaction_id) && spendingUUID(value.epoch_id); }
export const getSpendingEvidenceOperation = () => state;
/** Only an original read locator, never cached authority or a current transaction. */
export const getSessionTransactionReadonlyReference = () => recoverSpendingEvidenceOperation().transaction;
export function recoverSpendingEvidenceOperation(): State {
  if (recovered) return state; recovered = true;
  try { const raw = sessionStorage.getItem(storageKey()); if (raw !== null) { const original: unknown = JSON.parse(raw); if (!validSpendingIntent(original)) throw new Error('INVALID_ORIGINAL'); state = { ...state, pending: freeze(original), busy: false }; }
    const rawReference = sessionStorage.getItem(referenceKey()); if (rawReference !== null) { const reference: unknown = JSON.parse(rawReference); if (!validReference(reference)) throw new Error('INVALID_REFERENCE'); state = { ...state, transaction: freeze(reference) }; }
  } catch { state = { ...state, storage_error: '原分类请求无法完整读取，保留存储记录并禁止新写入。' }; } emit(); return state;
}
export async function beginSpendingEvidenceOperation(intent: SpendingIntent, blocked = false): Promise<void> {
  recoverSpendingEvidenceOperation(); const available = () => !blocked && !state.busy && !state.storage_error && !isWriteInFlight();
  if (!available() || !await spendingIntentHashMatches(intent) || !available()) throw new Error('其他请求正在处理、原hash不一致或存储不可用，未发送分类确认');
  if (state.pending && JSON.stringify(state.pending) !== JSON.stringify(intent)) throw new Error('原分类结果尚不明，不能换键或分类'); const original = state.pending ?? freeze(structuredClone(intent));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(original)); }
  catch { state = { ...state, pending: original, storage_error: '无法保存原分类body/key，未开始发送。' }; emit(); throw new Error(state.storage_error!); }
  state = { ...state, pending: original, busy: true }; emit();
}
/** Every POST outcome leaves pending; only separately verified by-key GET closes it. */
export function endSpendingEvidenceAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function clearSpendingEvidenceAfterLookup(intent: SpendingIntent, value: unknown): Promise<void> {
  const same = () => !state.busy && state.pending && JSON.stringify(state.pending) === JSON.stringify(intent);
  if (!same() || !await spendingIntentHashMatches(intent)) throw new Error('原分类命令不同或仍在发送，不能解除'); const result = parseCategoryResult(value, intent);
  if (result.status !== 'RECORDED' || !same()) throw new Error('原键未找到不是终局，保留原请求');
  const reference: ReadReference = { protocol: 'spending-transaction-read-reference-v1', user_id: intent.user_id, transaction_id: intent.transaction_id, epoch_id: intent.body.expected_epoch_id };
  try { sessionStorage.setItem(referenceKey(), JSON.stringify(reference)); sessionStorage.removeItem(storageKey()); state = { pending: null, busy: false, storage_error: null, transaction: freeze(reference) }; }
  catch { state = { ...state, storage_error: '原分类回执已核对，但存储不能删除，新写入仍禁止。' }; emit(); throw new Error(state.storage_error!); } emit();
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useSpendingEvidenceOperation = () => useSyncExternalStore(subscribe, getSpendingEvidenceOperation, getSpendingEvidenceOperation);
