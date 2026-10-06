import { useSyncExternalStore } from 'react';
import { object } from './policy-form';
import { isWriteInFlight } from './write-flight';
import {
  interventionCanonicalJson as canonical, interventionCommand, interventionHash as hash,
  parseIntervention, parseInterventionBody, parseInterventionLookup, verifyInterventionHash,
} from '../api/interventions';
import type { InterventionIntent } from '../api/interventions';
import type { ObserveBody } from '../api/interventions';
import { spendingDigest as digest, spendingUUID as uuid } from '../api/spending-evidence';

type State = { pending: InterventionIntent | null; busy: boolean; storage_error: string | null };
const fields = ['protocol', 'kind', 'user_id', 'message_id', 'path', 'body', 'body_json', 'request_hash'];
const suffix = { DELIVER: 'deliveries', ACKNOWLEDGE: 'acknowledgements' };
export function validInterventionIntent(value: unknown): value is InterventionIntent {
  try {
    if (!object(value) || Object.keys(value).sort().join('|') !== [...fields].sort().join('|') || value.protocol !== 'intervention-browser-command-v1' || !['OBSERVE', 'DELIVER', 'ACKNOWLEDGE'].includes(value.kind as string) || !uuid(value.user_id) || !digest(value.request_hash) || typeof value.body_json !== 'string' || value.body_json.length > 10000) return false;
    const intent = value as InterventionIntent; parseInterventionBody(intent.kind, intent.body);
    return intent.body_json === JSON.stringify(intent.body) && (intent.kind === 'OBSERVE' ? intent.message_id === null && intent.path === '/interventions/observe' : uuid(intent.message_id) && intent.path === `/interventions/${intent.message_id}/${suffix[intent.kind]}`);
  } catch { return false; }
}
function freeze<T>(value: T): T { if (value !== null && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
export async function prepareInterventionIntent(input: Pick<InterventionIntent, 'kind' | 'user_id' | 'message_id' | 'body'>): Promise<InterventionIntent> {
  const body = structuredClone(input.body);
  if (input.kind === 'OBSERVE') (body as ObserveBody).intervention_policy_id ??= null;
  const intent: InterventionIntent = { ...structuredClone(input), body, protocol: 'intervention-browser-command-v1', path: input.kind === 'OBSERVE' ? '/interventions/observe' : `/interventions/${input.message_id}/${suffix[input.kind]}`, body_json: JSON.stringify(body), request_hash: '0'.repeat(64) };
  if (!validInterventionIntent(intent)) throw new Error('通知原命令不完整，未发送'); intent.request_hash = await hash(interventionCommand(intent)); return freeze(intent);
}
export const interventionIntentHashMatches = async (intent: InterventionIntent) => validInterventionIntent(intent) && await hash(interventionCommand(intent)) === intent.request_hash;
const listeners = new Set<() => void>(); const emit = () => listeners.forEach((listener) => listener());
let state: State = { pending: null, busy: false, storage_error: null }; let recovered = false;
const storageKey = () => `bounded-funds-intervention-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
export const getInterventionOperation = () => state;
export function recoverInterventionOperation(): State {
  if (recovered) return state; recovered = true;
  try { const raw = sessionStorage.getItem(storageKey()); if (raw !== null) { const value: unknown = JSON.parse(raw); if (!validInterventionIntent(value)) throw new Error('INVALID_ORIGINAL'); state = { ...state, pending: freeze(value), busy: false }; } }
  catch { state = { ...state, storage_error: '原通知请求无法完整读取，保留记录并禁止新写入。' }; } emit(); return state;
}
export async function beginInterventionOperation(intent: InterventionIntent, blocked = false): Promise<void> {
  recoverInterventionOperation(); const available = () => !blocked && !state.busy && !state.storage_error && !isWriteInFlight();
  if (!available() || !await interventionIntentHashMatches(intent) || !available()) throw new Error('其他请求尚待核对、原hash不一致或存储不可用，未发送');
  if (state.pending && canonical(state.pending) !== canonical(intent)) throw new Error('原通知结果尚不明，不能换键、消息或操作');
  const original = state.pending ?? freeze(structuredClone(intent));
  try { sessionStorage.setItem(storageKey(), JSON.stringify(original)); }
  catch { state = { ...state, pending: original, storage_error: '无法保存原通知body/key，未开始发送。' }; emit(); throw new Error(state.storage_error!); }
  state = { ...state, pending: original, busy: true }; emit();
}
/** All HTTP outcomes stay pending until separately read and verified originals. */
export function endInterventionAttempt(): void { state = { ...state, busy: false }; emit(); }
export async function clearInterventionAfterRead(intent: InterventionIntent, value: unknown): Promise<void> {
  const same = () => !state.busy && state.pending !== null && canonical(state.pending) === canonical(intent);
  if (!same() || !await interventionIntentHashMatches(intent)) throw new Error('原通知命令不同或仍在发送，不能解除');
  if (intent.kind === 'DELIVER') {
    const view = parseIntervention(value, intent.message_id!); await verifyInterventionHash(view); const original = view.original_message; const claim = view.original_inbox_claim;
    if (!claim || original.user_id !== intent.user_id || original.epoch_id !== intent.body.expected_epoch_id || !('reviewed_payload_hash' in intent.body) || view.payload_hash !== intent.body.reviewed_payload_hash || claim.message_id !== intent.message_id || claim.consumer_ref !== 'intervention-center-v1' || claim.actual_human_view_verified !== false) throw new Error('原固定收件记录未找到或不匹配，保留原请求；是否呈现仍未知。');
  } else {
    const result = parseInterventionLookup(value, intent);
    if (result.status !== 'RECORDED' || !result.original_receipt || !result.message || await hash(result.original_receipt.original_command) !== intent.request_hash) throw new Error('原键未找到不是终局，保留原请求。');
    await verifyInterventionHash(result.message);
  }
  if (!same()) throw new Error('核对期间原通知命令变化，未解除');
  try { sessionStorage.removeItem(storageKey()); state = { pending: null, busy: false, storage_error: null }; }
  catch { state = { ...state, storage_error: '原件已核对但存储无法删除，新写入仍禁止。' }; emit(); throw new Error(state.storage_error!); } emit();
}
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export const useInterventionOperation = () => useSyncExternalStore(subscribe, getInterventionOperation, getInterventionOperation);
