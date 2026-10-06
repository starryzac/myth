import { useSyncExternalStore } from 'react';
import { futureCanonical, futureCheck, futureRequestHash, isFreshFutureIncomeLookup, parseFutureIncomeBody, parseFutureIncomeCandidate, parseFutureIncomeLookup } from '../api/future-income-planning';
import type { FutureIncomeCandidate, FutureIncomeCandidateBody, FutureIncomeConfirmBody, FutureIncomeLookup } from '../api/future-income-planning';
import { object } from './policy-form';
import { isWriteInFlight } from './write-flight';

export type FutureIncomeIntent = {
  protocol: 'future-income-browser-intent-v1'; kind: 'CANDIDATE' | 'CONFIRM'; user_id: string; epoch_id: string;
  body: FutureIncomeCandidateBody | FutureIncomeConfirmBody; body_json: string; request_hash: string;
  candidate: FutureIncomeCandidate | null;
};
type State = { pending: FutureIncomeIntent | null; workspace: FutureIncomeLookup | null; busy: boolean; recovering: boolean; storage_error: string | null };
let state: State = { pending: null, workspace: null, busy: false, recovering: false, storage_error: null };
let recovery: Promise<State> | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach(listener => listener());
const storageKey = () => `bounded-funds-future-income-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
function saved(pending: FutureIncomeIntent | null, workspace: FutureIncomeLookup | null) { return JSON.stringify({ protocol: 'future-income-browser-state-v1', pending, workspace }); }
export const getFutureIncomeOperation = () => state;

export async function parseFutureIncomeIntent(value: unknown): Promise<FutureIncomeIntent> {
  futureCheck(object(value) && value.protocol === 'future-income-browser-intent-v1' && (value.kind === 'CANDIDATE' || value.kind === 'CONFIRM') && typeof value.user_id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.user_id) && typeof value.epoch_id === 'string' && typeof value.body_json === 'string');
  futureCheck(Object.keys(value).length === 8 && Object.keys(value).every(k => ['protocol', 'kind', 'user_id', 'epoch_id', 'body', 'body_json', 'request_hash', 'candidate'].includes(k)));
  const body = parseFutureIncomeBody(value.body, value.kind);
  futureCheck(body.expected_epoch_id === value.epoch_id && value.body_json === JSON.stringify(body) && value.request_hash === await futureRequestHash(body));
  if (value.kind === 'CANDIDATE') futureCheck(value.candidate === null);
  else {
    const candidate = await parseFutureIncomeCandidate(value.candidate, value.user_id, value.epoch_id);
    const confirmation = body as FutureIncomeConfirmBody;
    futureCheck(confirmation.candidate_id === candidate.candidate_id && confirmation.reviewed_candidate_hash === candidate.candidate_hash);
  }
  return value as FutureIncomeIntent;
}
export async function prepareFutureIncomeIntent(kind: FutureIncomeIntent['kind'], user: string, body: FutureIncomeIntent['body'], candidate: FutureIncomeCandidate | null = null): Promise<FutureIncomeIntent> {
  const original = structuredClone(body);
  return freeze(await parseFutureIncomeIntent({ protocol: 'future-income-browser-intent-v1', kind, user_id: user, epoch_id: original.expected_epoch_id, body: original, body_json: JSON.stringify(original), request_hash: await futureRequestHash(original), candidate: candidate ? structuredClone(candidate) : null }));
}
export function recoverFutureIncomeOperation(): Promise<State> {
  if (recovery) return recovery;
  state = { ...state, recovering: true }; emit();
  recovery = (async () => {
    try {
      const raw = sessionStorage.getItem(storageKey());
      if (raw !== null) {
        const value: unknown = JSON.parse(raw);
        futureCheck(object(value) && value.protocol === 'future-income-browser-state-v1' && Object.keys(value).length === 3);
        const pending = value.pending === null ? null : await parseFutureIncomeIntent(value.pending);
        let workspace: FutureIncomeLookup | null = null;
        if (value.workspace !== null) { const w = value.workspace; futureCheck(object(w) && typeof w.user_id === 'string' && typeof w.epoch_id === 'string' && typeof w.idempotency_key === 'string'); workspace = await parseFutureIncomeLookup(w, w.user_id, w.epoch_id, w.idempotency_key); futureCheck(workspace.status === 'RECORDED'); }
        if (pending?.kind === 'CONFIRM') futureCheck(workspace !== null && workspace.candidate !== null && pending.candidate !== null && workspace.candidate.candidate_id === pending.candidate.candidate_id && workspace.candidate.candidate_hash === pending.candidate.candidate_hash);
        state = { ...state, pending: freeze(pending), workspace: freeze(workspace) };
      }
      state = { ...state, busy: false, recovering: false };
    } catch { state = { ...state, recovering: false, storage_error: '条件规划原请求或工作区无法完整读取，保留原存储并禁止新写入。' }; }
    emit(); return state;
  })();
  return recovery;
}
export function isFutureIncomeWorkspaceUnresolved(value: FutureIncomeLookup): boolean { return value.command_kind === 'CANDIDATE'; }
export async function beginFutureIncomeOperation(intent: FutureIncomeIntent, externallyBlocked = false): Promise<void> {
  await recoverFutureIncomeOperation(); await parseFutureIncomeIntent(intent);
  futureCheck(!externallyBlocked && !state.busy && !state.recovering && !state.storage_error && !isWriteInFlight());
  if (state.pending) futureCheck(futureCanonical(state.pending) === futureCanonical(intent));
  else if (intent.kind === 'CANDIDATE') futureCheck(!state.workspace || !isFutureIncomeWorkspaceUnresolved(state.workspace));
  else {
    futureCheck(state.workspace !== null && isFreshFutureIncomeLookup(state.workspace) && state.workspace.command_kind === 'CANDIDATE' && state.workspace.candidate?.state === 'REQUIRES_EXPLICIT_CONFIRMATION');
    futureCheck(state.workspace.user_id === intent.user_id && state.workspace.epoch_id === intent.epoch_id && state.workspace.candidate.candidate_id === intent.candidate?.candidate_id && state.workspace.candidate.candidate_hash === intent.candidate?.candidate_hash && futureCanonical(state.workspace.candidate.original_evidence) === futureCanonical(intent.candidate.original_evidence));
  }
  const original = state.pending ?? freeze(structuredClone(intent));
  try { sessionStorage.setItem(storageKey(), saved(original, state.workspace)); }
  catch { state = { ...state, pending: original, storage_error: '完整条件规划原body/key/hash无法保存，未发送；禁止新写入。' }; emit(); throw new Error(state.storage_error!); }
  state = { ...state, pending: original, busy: true }; emit();
}
/** Every POST outcome retains the exact pending; no HTTP status releases it. */
export function endFutureIncomeAttempt(): void { state = { ...state, busy: false }; emit(); }
function match(intent: FutureIncomeIntent, lookup: FutureIncomeLookup) {
  futureCheck(lookup.user_id === intent.user_id && lookup.epoch_id === intent.epoch_id && lookup.idempotency_key === intent.body.idempotency_key);
  if (lookup.status === 'RECORDED') futureCheck(lookup.command_kind === intent.kind && lookup.request_hash === intent.request_hash && futureCanonical(lookup.original_request) === futureCanonical(intent.body));
  if (intent.kind === 'CONFIRM' && lookup.status === 'RECORDED') futureCheck(lookup.candidate?.candidate_id === intent.candidate?.candidate_id && lookup.candidate?.candidate_hash === intent.candidate?.candidate_hash && futureCanonical(lookup.candidate?.original_evidence) === futureCanonical(intent.candidate?.original_evidence) && lookup.confirmation !== null);
}
export async function acceptFutureIncomeRead(intent: FutureIncomeIntent, value: unknown): Promise<{ complete: boolean; lookup: FutureIncomeLookup }> {
  const same = () => !state.busy && state.pending !== null && futureCanonical(state.pending) === futureCanonical(intent);
  futureCheck(same() && object(value) && isFreshFutureIncomeLookup(value as FutureIncomeLookup));
  const lookup = await parseFutureIncomeLookup(value, intent.user_id, intent.epoch_id, intent.body.idempotency_key);
  match(intent, lookup);
  if (lookup.status !== 'RECORDED') return { complete: false, lookup };
  futureCheck(same());
  try { sessionStorage.setItem(storageKey(), saved(null, lookup)); }
  catch { state = { ...state, storage_error: '原GET已匹配，但核对结果无法持久保存，原请求继续锁住新写入。' }; emit(); throw new Error(state.storage_error!); }
  state = { pending: null, workspace: freeze(lookup), busy: false, recovering: false, storage_error: null }; emit();
  return { complete: true, lookup };
}
export async function acceptFutureIncomeWorkspaceRead(value: FutureIncomeLookup): Promise<FutureIncomeLookup> {
  const before = state.workspace;
  futureCheck(before !== null && !state.pending && !state.busy && !state.recovering && isFreshFutureIncomeLookup(value));
  const result = await parseFutureIncomeLookup(value, before.user_id, before.epoch_id, before.idempotency_key);
  futureCheck(result.status === 'RECORDED' && result.command_kind === before.command_kind && result.request_hash === before.request_hash && futureCanonical(result.original_request) === futureCanonical(before.original_request) && state.workspace === before && !state.pending && !state.busy);
  try { sessionStorage.setItem(storageKey(), saved(null, result)); }
  catch { state = { ...state, storage_error: '原候选只读核对无法保存，禁止新写入。' }; emit(); throw new Error(state.storage_error!); }
  state = { ...state, workspace: freeze(result) }; emit(); return result;
}
/** Only discards local review; the server metadata originals are never deleted. */
export function discardFutureIncomeReview(): void {
  futureCheck(!state.pending && !state.busy && !state.recovering && !state.storage_error);
  try { sessionStorage.setItem(storageKey(), saved(null, null)); }
  catch { state = { ...state, storage_error: '本地复核工作区无法关闭，保留原件并禁止新写入。' }; emit(); throw new Error(state.storage_error!); }
  state = { ...state, workspace: null }; emit();
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useFutureIncomeOperation = () => useSyncExternalStore(subscribe, getFutureIncomeOperation, getFutureIncomeOperation);
