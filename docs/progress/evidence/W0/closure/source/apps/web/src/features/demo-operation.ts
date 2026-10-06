import { useSyncExternalStore } from 'react';
import { object } from './policy-form';
import { isRunId } from '../api/decisions';
import type { DemoReset } from '../api/demo';
import { isWriteInFlight } from './write-flight';

/** Browser recovery metadata only; authority, money and results always come from the API. */
export type DemoOperationIdentity = {
  kind: 'event' | 'reset' | 'template' | 'policy-confirm' | 'action-confirm' | 'action-execute' | 'rent-change';
  epoch_id: string | null;
  event_kind?: string;
  resource_id?: string;
  reset_key?: string;
  effect_hash?: string;
  idempotency_key?: string;
  reviewed_hash?: string;
  expected_version_id?: string;
};
type OperationState = { busy: boolean; pending: DemoOperationIdentity | null; storage_error: string | null; read_generation: number; reset_receipt: DemoReset | null };
const listeners = new Set<() => void>();
let state: OperationState = { busy: false, pending: null, storage_error: null, read_generation: 0, reset_receipt: null };
const storageKey = () => `bounded-funds-demo-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
function valid(value: unknown): value is DemoOperationIdentity {
  if (!object(value)) return false;
  const event = ['SALARY_RECEIVED', 'CREATE_CAR_GOAL', 'LARGE_CONSUMPTION', 'AUTO_REDEEM', 'FIXED_EARLY_WITHDRAWAL', 'CHANGE_RENT'].includes(value.event_kind as string);
  const template = ['CAR_GOAL', 'LIQUID_ASSET', 'FIXED_ASSET', 'RENT'].includes(value.event_kind as string);
  const hash = (item: unknown) => typeof item === 'string' && /^[0-9a-f]{64}$/.test(item);
  const required = value.kind === 'reset' ? typeof value.reset_key === 'string' && value.reset_key.trim().length > 0
    : value.kind === 'event' ? event : value.kind === 'template' ? template
      : value.kind === 'policy-confirm' ? isRunId(value.resource_id) && hash(value.reviewed_hash)
        : ['action-confirm', 'action-execute'].includes(value.kind as string) ? event && isRunId(value.resource_id) && hash(value.effect_hash)
          : value.kind === 'rent-change' ? isRunId(value.resource_id) && isRunId(value.expected_version_id) && hash(value.reviewed_hash) && typeof value.idempotency_key === 'string' && value.idempotency_key.trim().length > 0 : false;
  return required && (isRunId(value.epoch_id) || (value.kind === 'reset' && value.epoch_id === null)) && ['event', 'reset', 'template', 'policy-confirm', 'action-confirm', 'action-execute', 'rent-change'].includes(value.kind as string) &&
    Object.entries(value).every(([key, item]) => ['kind', 'epoch_id', 'event_kind', 'resource_id', 'reset_key', 'effect_hash', 'idempotency_key', 'reviewed_hash', 'expected_version_id'].includes(key) && ((key === 'epoch_id' && item === null) || (typeof item === 'string' && item.length <= 160))) &&
    (value.resource_id === undefined || isRunId(value.resource_id)) && (value.expected_version_id === undefined || isRunId(value.expected_version_id)) &&
    ['effect_hash', 'reviewed_hash'].every((key) => value[key] === undefined || /^[0-9a-f]{64}$/.test(value[key] as string));
}
function emit() { listeners.forEach((callback) => callback()); }
function persist(): boolean {
  try { if (state.pending) sessionStorage.setItem(storageKey(), JSON.stringify(state.pending)); else sessionStorage.removeItem(storageKey()); return true; }
  catch { state = { ...state, storage_error: '浏览器无法保存原命令身份，请保留本页并先核对服务端原命令。' }; return false; }
}
export function getDemoOperation(): OperationState { return state; }
export function canStartDemoOperation(identity: DemoOperationIdentity): boolean {
  return !state.busy && !isWriteInFlight() && !state.storage_error && (!state.pending || (Object.keys(state.pending).length === Object.keys(identity).length && Object.entries(identity).every(([key, value]) => state.pending![key as keyof DemoOperationIdentity] === value)));
}
export function recoverDemoOperation(): OperationState {
  if (state.busy || state.pending) return state;
  try {
    const stored = sessionStorage.getItem(storageKey());
    if (stored !== null) {
      const identity: unknown = JSON.parse(stored);
      if (!valid(identity)) state = { ...state, storage_error: '浏览器原命令记录未通过校验，请读取服务端演示状态后再处理。' };
      else state = { ...state, busy: false, pending: identity, storage_error: null };
    }
  } catch { state = { ...state, storage_error: '浏览器原命令记录无法读取，请先核对服务端演示状态。' }; }
  emit(); return state;
}
export function beginDemoOperation(identity: DemoOperationIdentity): void {
  if (!valid(identity)) throw new Error('演示命令身份无效');
  recoverDemoOperation();
  if (state.busy) throw new Error('已有演示命令正在处理，请等待原请求');
  if (isWriteInFlight()) throw new Error('已有资金或策略请求正在处理，请等待原请求完成');
  if (state.storage_error) throw new Error(state.storage_error);
  if (state.pending && (Object.keys(state.pending).length !== Object.keys(identity).length || Object.entries(identity).some(([key, value]) => state.pending![key as keyof DemoOperationIdentity] !== value))) throw new Error('原命令结果尚待核对，不能用新事件覆盖');
  state = { ...state, busy: true, pending: identity, storage_error: null };
  if (!persist()) { state = { ...state, busy: false }; emit(); throw new Error(state.storage_error!); }
  emit();
}
/** Clear only after an actual API response/reconciliation establishes the original outcome. */
export function endDemoOperation(resolved: boolean): void {
  state = { ...state, busy: false, pending: resolved ? null : state.pending, storage_error: resolved ? null : state.storage_error }; persist(); emit();
}
/** Only a successfully validated server read may establish an unresolved original command. */
export function retainServerDemoCommand(command: { epoch_id: string; event_kind: string; actions?: { action_id: string; effect_hash: string; status: string; autonomy_level: string }[] }): void {
  if (state.busy || state.pending || state.storage_error) return;
  const originalAction = command.actions?.find((action) => action.autonomy_level === 'ASK_ONCE' && ['UNKNOWN', 'SUBMITTED'].includes(action.status));
  const identity: DemoOperationIdentity = originalAction ? { kind: 'action-execute', epoch_id: command.epoch_id, event_kind: command.event_kind, resource_id: originalAction.action_id, effect_hash: originalAction.effect_hash }
    : { kind: 'event', epoch_id: command.epoch_id, event_kind: command.event_kind };
  if (!valid(identity)) return;
  state = { ...state, pending: identity }; persist(); emit();
}
/** Transition only from a read of the same server command to its bound original action. */
export function recoverServerDemoAction(command: { epoch_id: string; event_kind: string; actions?: { action_id: string; effect_hash: string; status: string; autonomy_level: string }[] }): void {
  if (state.busy || state.pending?.kind !== 'event' || state.pending.epoch_id !== command.epoch_id || state.pending.event_kind !== command.event_kind) return;
  const action = command.actions?.find((item) => item.autonomy_level === 'ASK_ONCE' && ['UNKNOWN', 'SUBMITTED'].includes(item.status));
  if (!action) return;
  const identity: DemoOperationIdentity = { kind: 'action-execute', epoch_id: command.epoch_id, event_kind: command.event_kind, resource_id: action.action_id, effect_hash: action.effect_hash };
  if (!valid(identity)) return;
  state = { ...state, pending: identity }; persist(); emit();
}
export function markDemoExecutionStage(identity: DemoOperationIdentity): void {
  if (!valid(identity) || !state.busy || state.pending?.kind !== 'action-confirm' || identity.kind !== 'action-execute' ||
    identity.resource_id !== state.pending.resource_id || identity.epoch_id !== state.pending.epoch_id || identity.effect_hash !== state.pending.effect_hash) throw new Error('不能改变原动作确认链身份');
  state = { ...state, pending: identity };
  if (!persist()) { emit(); throw new Error(state.storage_error!); }
  emit();
}
export function renewDemoReadContext(receipt: DemoReset): void {
  state = { busy: false, pending: null, storage_error: null, read_generation: state.read_generation + 1, reset_receipt: receipt }; persist(); emit();
}
function subscribe(callback: () => void) { listeners.add(callback); return () => { listeners.delete(callback); }; }
export function useDemoOperation() { return useSyncExternalStore(subscribe, getDemoOperation, getDemoOperation); }
