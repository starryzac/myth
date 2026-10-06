import { useSyncExternalStore } from 'react';
import { isRunId } from '../api/decisions';
import { object } from '../features/policy-form';
import { isWriteInFlight } from '../features/write-flight';
import type { State } from './api';

export type Kind = 'POLICY_CONFIRM' | 'POLICY_SUSPEND' | 'POLICY_REVOKE' | 'GOAL' | 'INCOME' | 'PREPARE' | 'CONFIRM' | 'EXECUTE';
export type Locator = { protocol: 'zhiyu-original-v1'; environment_id: string; epoch_id: string; kind: Kind; path: string; body: Record<string, unknown>; body_json: string; action_id: string | null; effect_hash: string | null };
type Operation = { locator: Locator | null; busy: boolean; error: string | null; ready: boolean };
let state: Operation = { locator: null, busy: false, error: null, ready: false };
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());
export const storageKey = (environment: string) => `zhiyu-original-v1:${environment}`;
const kinds: Kind[] = ['POLICY_CONFIRM', 'POLICY_SUSPEND', 'POLICY_REVOKE', 'GOAL', 'INCOME', 'PREPARE', 'CONFIRM', 'EXECUTE'];
export function parseLocator(value: unknown): Locator {
  if (!object(value) || Object.keys(value).sort().join('|') !== 'action_id|body|body_json|effect_hash|environment_id|epoch_id|kind|path|protocol' || value.protocol !== 'zhiyu-original-v1' || typeof value.environment_id !== 'string' || !value.environment_id || !isRunId(value.epoch_id) || !kinds.includes(value.kind as Kind) || typeof value.path !== 'string' || !object(value.body) || value.body_json !== JSON.stringify(value.body) || !(value.action_id === null || isRunId(value.action_id)) || !(value.effect_hash === null || typeof value.effect_hash === 'string' && /^[0-9a-f]{64}$/.test(value.effect_hash)) || (value.action_id === null) !== (value.effect_hash === null)) throw new Error('原操作记录损坏；保留原件并停止新提交。');
  const kind = value.kind as Kind, path = value.path;
  const uuidPath = '[0-9a-f-]{36}';
  const validPath = kind === 'PREPARE' ? path === '/zhiyu/actions/prepare' : kind === 'GOAL' ? path === '/zhiyu/goal' : kind === 'INCOME' ? path === '/zhiyu/income' : kind === 'EXECUTE' ? path === `/zhiyu/actions/${value.action_id}/execute` : kind === 'CONFIRM' ? path === `/actions/${value.action_id}/confirm` : new RegExp(kind === 'POLICY_CONFIRM' ? `^/policy-proposals/${uuidPath}/confirm$` : `^/policies/${uuidPath}/${kind === 'POLICY_SUSPEND' ? 'suspend' : 'revoke'}$`).test(path);
  if (!validPath) throw new Error('原请求路径与身份不一致；保留原件。');
  const body = value.body;
  const exact = (keys: string[]) => Object.keys(body).sort().join('|') === keys.sort().join('|');
  const digest = (hash: unknown) => typeof hash === 'string' && /^[0-9a-f]{64}$/.test(hash);
  const validBody = kind === 'EXECUTE' ? exact([]) : kind === 'CONFIRM' ? exact(['accepted', 'effect_hash']) && body.accepted === true && body.effect_hash === value.effect_hash
    : kind === 'POLICY_CONFIRM' ? exact(['accepted', 'reviewed_hash']) && body.accepted === true && digest(body.reviewed_hash)
      : kind === 'POLICY_SUSPEND' || kind === 'POLICY_REVOKE' ? exact(['expected_version_id']) && isRunId(body.expected_version_id)
        : kind === 'GOAL' ? exact(['policy_id', 'expected_version_id']) && isRunId(body.policy_id) && isRunId(body.expected_version_id)
          : kind === 'INCOME' ? exact(['expected_epoch_id']) && body.expected_epoch_id === value.epoch_id
            : exact(['expected_epoch_id', 'goal_id', 'scenario']) && body.expected_epoch_id === value.epoch_id && isRunId(body.goal_id) && ['SAFE', 'REVOKED', 'RESPONSE_LOSS'].includes(String(body.scenario));
  if (!validBody) throw new Error('原请求内容与身份不一致；不得从浏览器记录添加金额或银行事实。');
  return value as Locator;
}
export function legacyBlockers(): string[] {
  const result: string[] = [];
  for (const storage of [sessionStorage, localStorage]) for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i); if (!key || !key.startsWith('bounded-funds-') || (!/operation|goal-action|onboarding-draft|recovery-composed-observation|maturity-execution/.test(key)) || /:read-reference$/.test(key)) continue;
    const raw = storage.getItem(key); if (raw === null) continue;
    try {
      const value: unknown = JSON.parse(raw);
      if (!object(value)) { result.push(key); continue; }
      // A retained workspace requires its original environment's fresh read.
      // Only explicitly empty pending-only envelopes/drafts can be ignored.
      const isWrapper = 'pending' in value;
      const wrapperKeys = key.includes('onboarding-draft') ? 'candidates|declarations|discovery_proposal_ids|inputs|pending|protocol|step'
        : key.includes('fixed-payment') ? 'pending|workflow' : key.includes('future-income') ? 'pending|protocol|workspace'
          : key.includes('joint-goal') ? 'pending|protocol|workspace_reference' : key.includes('maturity-execution') ? 'original_json|pending|workspace'
            : key.includes('seasonal-adoption') ? 'original_receipt|pending' : key.includes('recovery-composed-observation') ? 'original_json|original_run_id|pending' : '';
      if (isWrapper && (!wrapperKeys || Object.keys(value).sort().join('|') !== wrapperKeys)) { result.push(key); continue; }
      const pending = isWrapper ? value.pending !== null : true;
      const retained = !!value.workspace || !!value.workspace_reference || object(value.workflow) && !!value.workflow.action;
      if (pending || retained || key.endsWith(':workspace')) result.push(key);
    } catch { result.push(key); }
  }
  return result;
}
export const getOperation = () => state;
export function recoverOperation(environment: State): void {
  if (state.busy) return;
  try {
    const blockers = legacyBlockers(); if (blockers.length) throw new Error(`发现旧环境原操作记录。请返回产生该记录的原环境地址核对后再进入知余；已保留 ${blockers.length} 项原件。`);
    let original: Locator | null = null;
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i); if (!key?.startsWith('zhiyu-original-v1:')) continue;
      const raw = localStorage.getItem(key); if (raw === null) continue;
      const locator = parseLocator(JSON.parse(raw));
      if (locator.environment_id !== environment.environment_id || locator.epoch_id !== environment.epoch_id) throw new Error('另一个演示环境仍有原操作待核对，请返回原环境恢复。原记录已保留。');
      if (original) throw new Error('存在多个原操作记录，请先核对原环境。');
      original = locator;
    }
    state = { locator: original, busy: false, error: null, ready: true };
  } catch (cause) { state = { ...state, busy: false, ready: true, error: cause instanceof Error ? cause.message : '无法读取原操作记录，禁止提交。' }; }
  emit();
}
export function makeLocator(environment: State, kind: Kind, path: string, body: Record<string, unknown>, action_id: string | null = null, effect_hash: string | null = null): Locator {
  return parseLocator({ protocol: 'zhiyu-original-v1', environment_id: environment.environment_id, epoch_id: environment.epoch_id, kind, path, body: structuredClone(body), body_json: JSON.stringify(body), action_id, effect_hash });
}
export function beginOperation(locator: Locator): void {
  parseLocator(locator);
  if (!state.ready || state.error || state.busy || isWriteInFlight() || legacyBlockers().length) throw new Error(state.error ?? '已有请求待处理，请先核对原操作。');
  if (state.locator && JSON.stringify(state.locator) !== JSON.stringify(locator)) throw new Error('原操作尚未解决，不能换金额、目标、场景或请求身份。');
  try { localStorage.setItem(storageKey(locator.environment_id), JSON.stringify(locator)); }
  catch { state = { ...state, error: '无法保存原操作身份；本次请求未发送。' }; emit(); throw new Error(state.error!); }
  state = { ...state, locator: structuredClone(locator), busy: true }; emit();
}
export function endAttempt(): void { state = { ...state, busy: false }; emit(); }
export function bindAction(action_id: string, effect_hash: string): void {
  if (!state.locator) throw new Error('缺少原操作身份');
  const next = parseLocator({ ...state.locator, action_id, effect_hash });
  try { localStorage.setItem(storageKey(next.environment_id), JSON.stringify(next)); state = { ...state, locator: next }; }
  catch { state = { ...state, error: '服务已返回原动作，但浏览器无法保存其身份。保留原请求并先核对。' }; emit(); throw new Error(state.error!); }
  emit();
}
/** Call only after independently reading the original outcome from the server. */
export function finishOperation(original: Locator): void {
  if (state.busy || !state.locator || JSON.stringify(state.locator) !== JSON.stringify(original)) throw new Error('原操作正在处理或身份已变化');
  try { localStorage.removeItem(storageKey(original.environment_id)); state = { ...state, locator: null, error: null }; }
  catch { state = { ...state, error: '原结果已读取，但浏览器原记录无法关闭，暂不提交新操作。' }; }
  emit();
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useOperation = () => useSyncExternalStore(subscribe, getOperation, getOperation);
