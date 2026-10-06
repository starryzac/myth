import { useSyncExternalStore } from 'react';
import { recoveryCheck as check, recoveryCanonicalJson as canonical, recoveryRequestHash as hash } from '../api/full-recovery-execution';
import { isFreshMaturityRead, originalMaturityResponse, parseMaturityIntent, parseMaturityLookup } from '../api/full-maturity-execution';
import type { MaturityAction, MaturityIntent, MaturityLookup, MaturityPrepare } from '../api/full-maturity-execution';
import { isWriteInFlight } from './write-flight';
import { object } from './policy-form';

type State = { pending: MaturityIntent | null; workspace: MaturityLookup | null; original_json: string | null; busy: boolean; recovering: boolean; storage_error: string | null };
let state: State = { pending: null, workspace: null, original_json: null, busy: false, recovering: false, storage_error: null }, recovery: Promise<State> | null = null;
const listeners = new Set<() => void>(); const emit = () => listeners.forEach(fn => fn());
const storageKey = () => `bounded-funds-full-maturity-execution-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export const getFullMaturityOperation = () => state;
export async function maturityIntent(kind: MaturityIntent['kind'], user: string, prepare: MaturityPrepare, action: MaturityAction | null = null): Promise<MaturityIntent> {
  const body = kind === 'PREPARE' ? structuredClone(prepare) : { expected_epoch_id: prepare.expected_epoch_id, reviewed_command_hash: action?.reviewed_command_hash ?? '', ...(kind === 'CONFIRM' ? { accepted: true as const } : {}) };
  return freeze(await parseMaturityIntent({ protocol: 'full-maturity-browser-v1', kind, user_id: user, prepare_request: structuredClone(prepare), action: action ? structuredClone(action) : null, path: kind === 'PREPARE' ? '/full-maturity-actions/prepare' : `/full-maturity-actions/actions/${action?.action_id}/${kind === 'CONFIRM' ? 'confirm' : 'execute'}`, body, body_json: JSON.stringify(body), request_hash: await hash(body) }));
}
export function recoverFullMaturityOperation(): Promise<State> {
  if (recovery) return recovery; state = { ...state, recovering: true }; emit();
  recovery = (async () => {
    try { const raw = sessionStorage.getItem(storageKey()); if (raw !== null) {
      const v: unknown = JSON.parse(raw); check(object(v) && Object.keys(v).sort().join('|') === 'original_json|pending|workspace' && (v.original_json === null || typeof v.original_json === 'string'));
      let workspace: MaturityLookup | null = null;
      if (v.workspace !== null) { check(object(v.workspace) && object(v.workspace.action) && typeof v.workspace.user_id === 'string'); const intent = await maturityIntent('PREPARE', v.workspace.user_id, v.workspace.action.original_request as MaturityPrepare); workspace = freeze(await parseMaturityLookup(v.workspace, intent)); check(typeof v.original_json === 'string' && canonical(JSON.parse(v.original_json)) === canonical(workspace)); } else check(v.original_json === null);
      state = { pending: v.pending === null ? null : freeze(await parseMaturityIntent(v.pending)), workspace, original_json: v.original_json as string | null, busy: false, recovering: false, storage_error: null };
    } else state = { ...state, recovering: false }; }
    catch { state = { ...state, busy: false, recovering: false, storage_error: '到期原body/key/commandHash无法完整恢复，保留原存储并禁止新动作。' }; } emit(); return state;
  })(); return recovery;
}
function persist(pending: MaturityIntent | null, workspace = state.workspace, raw = state.original_json) {
  try { sessionStorage.setItem(storageKey(), JSON.stringify({ pending, workspace, original_json: raw })); state = { ...state, pending, workspace, original_json: raw, storage_error: null }; }
  catch { state = { ...state, pending: pending ?? state.pending, storage_error: '完整到期原请求/原件无法持久保存，未解除门；禁止新键。' }; emit(); throw new Error(state.storage_error!); }
}
export function isFullMaturityWorkspaceUnresolved(value: MaturityLookup): boolean { return !value.action?.service_receipt_verified; }
export async function beginFullMaturityOperation(intent: MaturityIntent, otherBlocked: boolean | (() => boolean) = false): Promise<void> {
  await recoverFullMaturityOperation(); await parseMaturityIntent(intent);
  if ((typeof otherBlocked === 'function' ? otherBlocked() : otherBlocked) || state.busy || state.recovering || state.storage_error || isWriteInFlight()) throw new Error('到期或其它族写请求未核对，禁止新POST。');
  if (state.pending && canonical(state.pending) !== canonical(intent)) throw new Error('原到期请求未决，不能换key/仓位/轮次/完整命令。');
  if (state.workspace && isFullMaturityWorkspaceUnresolved(state.workspace) && (intent.kind === 'PREPARE' || canonical(intent.prepare_request) !== canonical(state.workspace.action?.original_request) || intent.action?.action_id !== state.workspace.action?.action_id)) throw new Error('固定到期原动作未终局，不能另一键替代。');
  const original = state.pending ?? freeze(structuredClone(intent)); persist(original); state = { ...state, busy: true }; emit();
}
export function endFullMaturityAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function acceptFullMaturityRead(intent: MaturityIntent, value: MaturityLookup): Promise<{ complete: boolean; lookup: MaturityLookup }> {
  const before = state.pending; check(before && !state.busy && canonical(before) === canonical(intent) && isFreshMaturityRead(value)); const lookup = await parseMaturityLookup(value, intent);
  let complete = lookup.status === 'RECORDED'; if (intent.kind === 'CONFIRM') complete &&= lookup.action?.original_consent !== null; if (intent.kind === 'EXECUTE') complete &&= lookup.action?.original_consent !== null && lookup.action?.service_receipt_verified === true;
  if (complete) { check(state.pending === before && !state.busy); const raw = originalMaturityResponse(value); check(raw !== null); persist(null, freeze(lookup), raw); emit(); }
  return { complete, lookup };
}
export async function fullMaturityWorkspaceIntent(): Promise<MaturityIntent> { check(state.workspace?.action); return maturityIntent('PREPARE', state.workspace.user_id, state.workspace.action.original_request); }
export async function acceptFullMaturityWorkspaceRead(intent: MaturityIntent, value: MaturityLookup): Promise<MaturityLookup> {
  const before = state.workspace; check(before?.action && !state.pending && !state.busy && isFreshMaturityRead(value)); const lookup = await parseMaturityLookup(value, intent);
  check(lookup.action && lookup.action.action_id === before.action.action_id && lookup.action.server_request_hash === before.action.server_request_hash && state.workspace === before && !state.pending && !state.busy); const raw = originalMaturityResponse(value); check(raw !== null); persist(null, freeze(lookup), raw); emit(); return lookup;
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useFullMaturityOperation = () => useSyncExternalStore(subscribe, getFullMaturityOperation, getFullMaturityOperation);
