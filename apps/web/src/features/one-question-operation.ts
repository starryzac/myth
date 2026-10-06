import { useSyncExternalStore } from 'react';
import { object } from './policy-form';
import { isWriteInFlight } from './write-flight';
import { recoverDemoOperation } from './demo-operation';
import { recoverFullPolicyOperation } from './full-policy-operation';
import { recoverFullGoalOperation } from './full-goal-operation';
import { recoverOnboardingDraft } from './onboarding-draft';
import { parseQuestionBody, parseQuestionLookup, parseQuestionVariables, questionCanonicalJson, questionCommand, questionUUID } from '../api/question-workflow';
import type { QuestionIntent } from '../api/question-workflow';

type SessionReference = { protocol: 'one-question-read-reference-v1'; user_id: string; epoch_id: string; session_id: string };
type State = { pending: QuestionIntent | null; busy: boolean; storage_error: string | null; session: SessionReference | null };
const kinds = ['START', 'ANSWER', 'REFRESH', 'CLOSE'];
const suffix = { ANSWER: 'answers', REFRESH: 'refresh', CLOSE: 'close' };
export function validOneQuestionIntent(value: unknown): value is QuestionIntent {
  try {
    if (!object(value) || Object.keys(value).sort().join('|') !== ['protocol', 'kind', 'user_id', 'session_id', 'base_action_id', 'variables', 'previous_run_id', 'path', 'body', 'body_json', 'request_hash'].sort().join('|') || value.protocol !== 'one-question-browser-command-v1' || !kinds.includes(value.kind as string) || !questionUUID(value.user_id) || !questionUUID(value.base_action_id) || typeof value.body_json !== 'string' || value.body_json.length > 100000 || typeof value.request_hash !== 'string' || !/^[0-9a-f]{64}$/.test(value.request_hash)) return false;
    const intent = value as QuestionIntent; parseQuestionBody(intent.kind, intent.body); parseQuestionVariables(intent.variables);
    if (JSON.stringify(intent.body) !== intent.body_json) return false;
    if (intent.kind === 'START') return intent.session_id === null && intent.previous_run_id === null && intent.path === '/finite-planning/sessions' && 'variables' in intent.body && intent.body.base_action_id === intent.base_action_id && questionCanonicalJson(intent.body.variables) === questionCanonicalJson(intent.variables);
    return questionUUID(intent.session_id) && questionUUID(intent.previous_run_id) && intent.path === `/finite-planning/sessions/${intent.session_id}/${suffix[intent.kind]}`;
  } catch { return false; }
}
/** Python configuration_hash: sorted UTF-8 JSON, no ASCII escaping or whitespace. */
export async function oneQuestionRequestHash(intent: QuestionIntent): Promise<string> {
  if (!validOneQuestionIntent(intent) || !crypto.subtle) throw new Error('缺少原问答请求或安全摘要能力，未发送');
  const bytes = new TextEncoder().encode(questionCanonicalJson(questionCommand(intent))); const result = await crypto.subtle.digest('SHA-256', bytes); return [...new Uint8Array(result)].map((value) => value.toString(16).padStart(2, '0')).join('');
}
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export async function prepareOneQuestionIntent(input: Omit<QuestionIntent, 'protocol' | 'path' | 'body_json' | 'request_hash'>): Promise<QuestionIntent> {
  const intent: QuestionIntent = { ...structuredClone(input), protocol: 'one-question-browser-command-v1', path: input.kind === 'START' ? '/finite-planning/sessions' : `/finite-planning/sessions/${input.session_id}/${suffix[input.kind]}`, body_json: JSON.stringify(input.body), request_hash: '0'.repeat(64) };
  if (!validOneQuestionIntent(intent)) throw new Error('原问答完整body、账户/动作、周期或精确版本不完整'); intent.request_hash = await oneQuestionRequestHash(intent); return freeze(intent);
}
export async function oneQuestionIntentHashMatches(intent: QuestionIntent): Promise<boolean> { return validOneQuestionIntent(intent) && await oneQuestionRequestHash(intent) === intent.request_hash; }
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((listener) => listener());
let state: State = { pending: null, busy: false, storage_error: null, session: null }; let recovered = false;
const storageKey = () => `bounded-funds-one-question-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
const sessionKey = () => `${storageKey()}:read-reference`;
function validReference(value: unknown): value is SessionReference { return object(value) && Object.keys(value).sort().join('|') === ['protocol', 'user_id', 'epoch_id', 'session_id'].sort().join('|') && value.protocol === 'one-question-read-reference-v1' && questionUUID(value.user_id) && questionUUID(value.epoch_id) && questionUUID(value.session_id); }
export const getOneQuestionOperation = () => state;
export function recoverOneQuestionOperation(): State {
  if (recovered) return state; recovered = true;
  try { const raw = sessionStorage.getItem(storageKey()); if (raw !== null) { const parsed: unknown = JSON.parse(raw); if (!validOneQuestionIntent(parsed)) throw new Error('INVALID_ORIGINAL'); state = { ...state, pending: freeze(parsed), busy: false, storage_error: null }; } const reference = sessionStorage.getItem(sessionKey()); if (reference !== null) { const parsed: unknown = JSON.parse(reference); if (!validReference(parsed)) throw new Error('INVALID_READ_REFERENCE'); state = { ...state, session: freeze(parsed) }; } }
  catch { state = { ...state, storage_error: '原问答请求无法完整读取，禁止新写入；请保留原存储记录并只读核对。' }; } emit(); return state;
}
export async function beginOneQuestionOperation(intent: QuestionIntent, blocked = false): Promise<void> {
  recoverOneQuestionOperation();
  const available = () => { const demo = recoverDemoOperation(); const policy = recoverFullPolicyOperation(); const goal = recoverFullGoalOperation(); const onboarding = recoverOnboardingDraft(); return !blocked && !state.busy && !state.storage_error && !demo.busy && !demo.pending && !demo.storage_error && !policy.busy && !policy.pending && !policy.storage_error && !goal.busy && !goal.pending && !goal.storage_error && !onboarding.busy && !onboarding.draft.pending && !onboarding.storage_error && !isWriteInFlight(); };
  if (!available()) throw new Error('其他原请求待核对、存储不可用或正在发送，不能提交问答'); if (!await oneQuestionIntentHashMatches(intent)) throw new Error('原问答完整body/hash不一致'); if (!available()) throw new Error('核对期间有其他写入，未发送问答');
  if (state.pending && JSON.stringify(state.pending) !== JSON.stringify(intent)) throw new Error('原问答结果尚不明，禁止换键、答案或操作'); const original = state.pending ?? freeze(structuredClone(intent));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(original)); }
  catch { state = { ...state, pending: original, busy: false, storage_error: '无法保存原问答body/key，未开始发送。' }; emit(); throw new Error(state.storage_error!); }
  state = { ...state, pending: original, busy: true, storage_error: null }; emit();
}
/** HTTP success, network loss and 4xx all retain the original until separate GET. */
export function endOneQuestionAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function clearOneQuestionAfterLookup(intent: QuestionIntent, value: unknown): Promise<void> {
  const same = () => !state.busy && state.pending && JSON.stringify(state.pending) === JSON.stringify(intent);
  if (!same() || !await oneQuestionIntentHashMatches(intent)) throw new Error('不能解除不同或正在发送的原问答'); const lookup = parseQuestionLookup(value, intent); if (lookup.status !== 'RECORDED' || !same()) throw new Error('原键未找到不是终局，继续保留原请求');
  const reference: SessionReference = { protocol: 'one-question-read-reference-v1', user_id: intent.user_id, epoch_id: intent.body.expected_epoch_id, session_id: lookup.session_id! };
  try { sessionStorage.setItem(sessionKey(), JSON.stringify(reference)); sessionStorage.removeItem(storageKey()); state = { pending: null, busy: false, storage_error: null, session: freeze(reference) }; }
  catch { state = { ...state, storage_error: '原问答已核对，但记录无法删除；新写入仍禁止。' }; emit(); throw new Error(state.storage_error!); } emit();
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useOneQuestionOperation = () => useSyncExternalStore(subscribe, getOneQuestionOperation, getOneQuestionOperation);
