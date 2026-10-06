import type { components } from '../../../../packages/contracts/schema';
import { object } from '../features/policy-form';
import { releaseCanonicalJson as canonical, releaseDigest, releaseHash, releaseUUID, releaseUUID5 } from './goal-release-authorizations';
import { ApiError, request } from './http';
import { originalRecoveryComposedActionSet, parseRecoveryComposedActionSet } from './recovery-composed-action-set';
import type { RecoveryComposedActionSet } from './recovery-composed-action-set';

export type RecoveryObserveRequest = Required<components['schemas']['GlobalBoundaryObserveRequest']>;
export type RecoveryObservation = Omit<components['schemas']['RecoveryComposedGlobalObservation'], 'snapshot' | 'original_request'> & { snapshot: RecoveryComposedActionSet; original_request: RecoveryObserveRequest };
export type RecoveryObservationIntent = {
  protocol: 'recovery-composed-observe-browser-v1'; user_id: string; epoch_id: string;
  body: RecoveryObserveRequest; body_json: string; request_hash: string; expected_run_id: string;
  source_ref: { endpoint: '/boundary/recovery-composed-action-set/current'; user_id: string; epoch_id: string; as_of: string; snapshot_hash: string };
  bank_authority: false;
};
const base = '/boundary/recovery-composed-action-set';
const namespace = 'dfb9d38c-5698-532d-a90f-bcb2458d7d7f';
const rawOriginals = new WeakMap<RecoveryObservation, string>();
const freshReads = new WeakSet<RecoveryObservation>();
const checkedOriginals = new WeakSet<RecoveryObservation>();
const absentReads = new WeakSet<RecoveryObservationIntent>();
export const originalRecoveryObservation = (value: RecoveryObservation) => rawOriginals.get(value) ?? null;
export const isFreshRecoveryObservation = (value: RecoveryObservation) => freshReads.has(value);
export const isFreshRecoveryObservationAbsent = (value: RecoveryObservationIntent) => absentReads.has(value);
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b);
const exact = (value: Record<string, unknown>, fields: string[]) => same(Object.keys(value).sort(), [...fields].sort());
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const check: (condition: unknown) => asserts condition = condition => { if (!condition) throw new ApiError('完整原观察未通过身份、来源或比较校验；保留原请求', 200, 'INVALID_RESPONSE', null); };

export function parseRecoveryObserveRequest(value: unknown): RecoveryObserveRequest {
  check(object(value) && exact(value, ['expected_epoch_id', 'previous_observation_run_id', 'idempotency_key']) && releaseUUID(value.expected_epoch_id) && (value.previous_observation_run_id === null || releaseUUID(value.previous_observation_run_id)) && typeof value.idempotency_key === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(value.idempotency_key));
  return value as RecoveryObserveRequest;
}
export async function parseRecoveryObservationIntent(value: unknown): Promise<RecoveryObservationIntent> {
  check(object(value) && exact(value, ['protocol', 'user_id', 'epoch_id', 'body', 'body_json', 'request_hash', 'expected_run_id', 'source_ref', 'bank_authority']) && value.protocol === 'recovery-composed-observe-browser-v1' && value.bank_authority === false && releaseUUID(value.user_id) && releaseUUID(value.epoch_id) && releaseUUID(value.expected_run_id) && releaseDigest(value.request_hash) && typeof value.body_json === 'string');
  const body = parseRecoveryObserveRequest(value.body), ref = value.source_ref;
  check(object(ref) && exact(ref, ['endpoint', 'user_id', 'epoch_id', 'as_of', 'snapshot_hash']) && ref.endpoint === `${base}/current` && ref.user_id === value.user_id && ref.epoch_id === value.epoch_id && time(ref.as_of) && releaseDigest(ref.snapshot_hash));
  check(body.expected_epoch_id === value.epoch_id && value.body_json === JSON.stringify(body) && value.request_hash === await releaseHash(body) && value.expected_run_id === await releaseUUID5(namespace, `${value.user_id}:${value.epoch_id}:${body.idempotency_key}`));
  return value as RecoveryObservationIntent;
}
export async function createRecoveryObservationIntent(current: RecoveryComposedActionSet, previous: RecoveryObservation | null = null): Promise<RecoveryObservationIntent> {
  check(originalRecoveryComposedActionSet(current) !== null && releaseUUID(current.user_id) && releaseUUID(current.epoch_id) && time(current.as_of));
  if (previous) check(checkedOriginals.has(previous) && previous.user_id === current.user_id && previous.epoch_id === current.epoch_id && Date.parse(previous.snapshot.as_of) <= Date.parse(current.as_of));
  const body: RecoveryObserveRequest = { expected_epoch_id: current.epoch_id, previous_observation_run_id: previous?.observation_run_id ?? null, idempotency_key: `recovery-observe:${crypto.randomUUID()}` };
  const result: RecoveryObservationIntent = {
    protocol: 'recovery-composed-observe-browser-v1', user_id: current.user_id, epoch_id: current.epoch_id, body, body_json: JSON.stringify(body), request_hash: await releaseHash(body),
    expected_run_id: await releaseUUID5(namespace, `${current.user_id}:${current.epoch_id}:${body.idempotency_key}`),
    source_ref: { endpoint: '/boundary/recovery-composed-action-set/current', user_id: current.user_id, epoch_id: current.epoch_id, as_of: current.as_of, snapshot_hash: current.snapshot_hash }, bank_authority: false,
  };
  return await parseRecoveryObservationIntent(result);
}
export async function parseRecoveryObservation(value: unknown, raw: string, previous: RecoveryObservation | null = null, intent?: RecoveryObservationIntent): Promise<RecoveryObservation> {
  check(object(value) && exact(value, ['simulation', 'bank_authority', 'grants_authority', 'financial_write', 'notification_support', 'user_id', 'epoch_id', 'observation_run_id', 'previous_observation_run_id', 'original_request', 'request_hash', 'snapshot', 'kind', 'semantic_key', 'requires_user_attention', 'previous_snapshot_hash', 'previous_action_set_signature', 'global_action_set_complete', 'idempotent_replay']) && value.simulation === true && value.bank_authority === false && value.grants_authority === false && value.financial_write === false && value.notification_support === 'NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4' && releaseUUID(value.user_id) && releaseUUID(value.epoch_id) && releaseUUID(value.observation_run_id) && (value.previous_observation_run_id === null || releaseUUID(value.previous_observation_run_id)) && releaseDigest(value.request_hash) && typeof value.requires_user_attention === 'boolean' && typeof value.global_action_set_complete === 'boolean' && typeof value.idempotent_replay === 'boolean');
  check(same(JSON.parse(raw), value));
  const body = parseRecoveryObserveRequest(value.original_request);
  const current = await parseRecoveryComposedActionSet(value.snapshot, JSON.stringify(value.snapshot));
  check(time(current.as_of) && current.user_id === value.user_id && current.epoch_id === value.epoch_id && body.expected_epoch_id === value.epoch_id && body.previous_observation_run_id === value.previous_observation_run_id && value.request_hash === await releaseHash(body) && value.observation_run_id === await releaseUUID5(namespace, `${value.user_id}:${value.epoch_id}:${body.idempotency_key}`));
  check((previous === null) === (value.previous_observation_run_id === null));
  if (previous) check(checkedOriginals.has(previous) && previous.observation_run_id === value.previous_observation_run_id);
  check(value.previous_snapshot_hash === (previous?.snapshot.snapshot_hash ?? null) && value.previous_action_set_signature === (previous?.snapshot.action_set_signature ?? null));
  const before = previous?.snapshot;
  const comparable = !before || before.global_action_set_complete && before.action_set_signature !== null && before.user_id === current.user_id && before.epoch_id === current.epoch_id && before.scope === current.scope && before.algorithm_version === current.algorithm_version && time(before.as_of) && Date.parse(before.as_of) <= Date.parse(current.as_of);
  let kind: RecoveryObservation['kind'] = null; let semantic: string | null = null;
  if (current.global_action_set_complete && current.action_set_signature !== null && comparable) {
    kind = !before || before.action_set_signature === current.action_set_signature ? 'BoundaryObserved' : 'BoundaryCrossed';
    if (before) semantic = await releaseHash({ user_id: current.user_id, epoch_id: current.epoch_id, scope: current.scope, before: before.action_set_signature, after: current.action_set_signature });
  }
  check(value.kind === kind && value.semantic_key === semantic && value.requires_user_attention === (kind === 'BoundaryCrossed') && value.global_action_set_complete === (current.global_action_set_complete && comparable));
  if (intent) { await parseRecoveryObservationIntent(intent); check(value.user_id === intent.user_id && value.epoch_id === intent.epoch_id && value.observation_run_id === intent.expected_run_id && value.request_hash === intent.request_hash && same(body, intent.body)); }
  const result = { ...value, snapshot: current, original_request: body } as RecoveryObservation;
  rawOriginals.set(result, raw); checkedOriginals.add(result); return result;
}
async function readOriginal(runId: string, intent: RecoveryObservationIntent | undefined, visited: Set<string>): Promise<RecoveryObservation> {
  check(releaseUUID(runId) && !visited.has(runId) && visited.size < 64); visited.add(runId);
  const result = await request(`${base}/observations/${runId}`, 'GET', undefined, async (value, raw) => {
    check(object(value) && value.observation_run_id === runId && (value.previous_observation_run_id === null || releaseUUID(value.previous_observation_run_id)));
    let before: RecoveryObservation | null = null;
    if (value.previous_observation_run_id !== null) {
      try { before = await readOriginal(value.previous_observation_run_id as string, undefined, visited); }
      catch { throw new ApiError('先前原观察无法完整核对；当前记录不可视为不存在', 409, 'PARENT_ORIGINAL_UNVERIFIED', null); }
    }
    return await parseRecoveryObservation(value, raw, before, intent);
  });
  check(result.idempotent_replay === false); freshReads.add(result); return result;
}
export async function getRecoveryObservation(runId: string, intent?: RecoveryObservationIntent): Promise<RecoveryObservation> {
  if (intent) { await parseRecoveryObservationIntent(intent); check(runId === intent.expected_run_id); absentReads.delete(intent); }
  try { return await readOriginal(runId, intent, new Set()); }
  catch (error) { if (intent && error instanceof ApiError && error.status === 404) absentReads.add(intent); throw error; }
}
export async function postRecoveryObservation(intent: RecoveryObservationIntent): Promise<RecoveryObservation> {
  await parseRecoveryObservationIntent(intent); absentReads.delete(intent);
  return await request(`${base}/observe`, 'POST', intent.body, async (value, raw) => {
    const previous = intent.body.previous_observation_run_id === null ? null : await getRecoveryObservation(intent.body.previous_observation_run_id);
    return await parseRecoveryObservation(value, raw, previous, intent);
  });
}
