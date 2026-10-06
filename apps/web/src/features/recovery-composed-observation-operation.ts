import { useSyncExternalStore } from 'react';
import { isFreshRecoveryObservation, isFreshRecoveryObservationAbsent, originalRecoveryObservation, parseRecoveryObservationIntent } from '../api/recovery-composed-observations';
import type { RecoveryObservation, RecoveryObservationIntent } from '../api/recovery-composed-observations';
import { releaseUUID } from '../api/goal-release-authorizations';
import { object } from './policy-form';
import { isWriteInFlight } from './write-flight';

type State = { pending: RecoveryObservationIntent | null; original_run_id: string | null; original_json: string | null; busy: boolean; recovering: boolean; storage_error: string | null };
let state: State = { pending: null, original_run_id: null, original_json: null, busy: false, recovering: false, storage_error: null };
let recovery: Promise<State> | null = null;
const listeners = new Set<() => void>(); const emit = () => listeners.forEach(fn => fn());
const key = () => `bounded-funds-recovery-composed-observation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
const readAbsent = new WeakSet<RecoveryObservationIntent>();
type WriteBlocked = boolean | (() => boolean);
const blocked = (value: WriteBlocked) => typeof value === 'function' ? value() : value;
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export const getRecoveryComposedObservationOperation = () => state;
export function recoverRecoveryComposedObservationOperation(): Promise<State> {
  if (recovery) return recovery; state = { ...state, recovering: true }; emit();
  recovery = (async () => {
    try {
      const raw = localStorage.getItem(key());
      if (raw !== null) {
        const v: unknown = JSON.parse(raw);
        if (!object(v) || Object.keys(v).sort().join('|') !== 'original_json|original_run_id|pending' || !(v.original_json === null || typeof v.original_json === 'string') || !(v.original_run_id === null || releaseUUID(v.original_run_id)) || (v.original_json === null) !== (v.original_run_id === null)) throw new Error('恢复原件不完整');
        if (typeof v.original_json === 'string') { const retained: unknown = JSON.parse(v.original_json); if (!object(retained) || retained.observation_run_id !== v.original_run_id || retained.simulation !== true) throw new Error('存储原观察身份不同'); }
        const pending = v.pending === null ? null : await parseRecoveryObservationIntent(v.pending);
        // Stored original is retained text only. It never becomes a fresh GET or authority.
        state = { pending: pending ? freeze(pending) : null, original_json: v.original_json as string | null, original_run_id: v.original_run_id as string | null, busy: false, recovering: false, storage_error: null };
      } else state = { ...state, recovering: false };
    } catch { state = { ...state, recovering: false, storage_error: '完整原观察请求无法恢复；保留原存储并禁止新POST。' }; }
    emit(); return state;
  })(); return recovery;
}
function persist(pending: RecoveryObservationIntent | null, value: RecoveryObservation | null = null): void {
  const next = freeze(structuredClone({ pending, original_run_id: value?.observation_run_id ?? state.original_run_id, original_json: value ? originalRecoveryObservation(value) : state.original_json }));
  try { localStorage.setItem(key(), JSON.stringify(next)); state = { ...state, ...next, storage_error: null }; }
  catch { state = { ...state, pending: next.pending ?? state.pending, busy: false, storage_error: '原观察无法完整保存；未解除原请求，禁止新POST。' }; emit(); throw new Error(state.storage_error!); }
}
export async function beginRecoveryComposedObservation(intent: RecoveryObservationIntent, externallyBlocked: WriteBlocked): Promise<void> {
  await recoverRecoveryComposedObservationOperation(); await parseRecoveryObservationIntent(intent);
  if (blocked(externallyBlocked) || state.pending || state.busy || state.recovering || state.storage_error || isWriteInFlight()) throw new Error('原观察或其他写请求未核对，禁止新观察/换键');
  persist(intent); state = { ...state, busy: true }; emit();
}
export function endRecoveryComposedObservationAttempt(): void { state = { ...state, busy: false }; emit(); }
export function noteRecoveryObservationAbsent(intent: RecoveryObservationIntent): void {
  if (!state.pending || state.busy || !isFreshRecoveryObservationAbsent(intent) || state.pending.expected_run_id !== intent.expected_run_id || state.pending.body_json !== intent.body_json || state.pending.request_hash !== intent.request_hash) throw new Error('只能记录当前原GET的404');
  readAbsent.add(state.pending);
}
export async function beginRecoveryObservationSameKeyRetry(intent: RecoveryObservationIntent, externallyBlocked: WriteBlocked): Promise<void> {
  await recoverRecoveryComposedObservationOperation(); await parseRecoveryObservationIntent(intent);
  const before = state.pending;
  if (!before || blocked(externallyBlocked) || state.busy || state.recovering || state.storage_error || isWriteInFlight() || !readAbsent.has(before) || !isFreshRecoveryObservationAbsent(intent) || before.body_json !== intent.body_json || before.expected_run_id !== intent.expected_run_id || before.request_hash !== intent.request_hash) throw new Error('需要此次原GET的404及同body/key，不能替换原请求');
  readAbsent.delete(before); state = { ...state, busy: true }; emit();
}
export async function acceptRecoveryObservationRead(intent: RecoveryObservationIntent, value: RecoveryObservation): Promise<boolean> {
  const before = state.pending;
  if (!before || state.busy || !isFreshRecoveryObservation(value) || before.body_json !== intent.body_json || before.request_hash !== intent.request_hash || value.observation_run_id !== intent.expected_run_id || value.user_id !== intent.user_id || value.epoch_id !== intent.epoch_id || value.request_hash !== intent.request_hash || JSON.stringify(value.original_request) !== intent.body_json) throw new Error('必须独立GET精确原body/hash/run，POST或存储不能清门');
  await parseRecoveryObservationIntent(intent);
  if (state.pending !== before || state.busy) throw new Error('核对期间原请求变化');
  persist(null, value); emit(); return true;
}
function subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; }
export const useRecoveryComposedObservationOperation = () => useSyncExternalStore(subscribe, getRecoveryComposedObservationOperation, getRecoveryComposedObservationOperation);
