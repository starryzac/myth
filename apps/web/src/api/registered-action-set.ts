import type { components } from '../../../../packages/contracts/schema';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';
import { ApiError, request } from './http';
import { releaseCanonicalJson, releaseDigest, releaseHash, releaseUUID } from './goal-release-authorizations';
import { parseRecoveryComposedActionSet } from './recovery-composed-action-set';

export type RegisteredActionSet = components['schemas']['RegisteredActionSetSnapshot'];
type View = components['schemas']['CandidateView'];
type Family = RegisteredActionSet['release_family'] | RegisteredActionSet['joint_family'];
const originals = new WeakMap<RegisteredActionSet, string>();
export const originalRegisteredActionSet = (value: RegisteredActionSet) => originals.get(value) ?? null;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(row => typeof row === 'string');
const unique = (value: unknown): value is string[] => strings(value) && new Set(value).size === value.length;
const ids = (value: unknown): value is string[] => unique(value) && value.every(releaseUUID);
const same = (a: unknown, b: unknown) => releaseCanonicalJson(a) === releaseCanonicalJson(b);
const check: (condition: unknown) => asserts condition = condition => { if (!condition) throw new ApiError('登记动作集合缺少一致原件或完整分母，请重新读取', 200, 'INVALID_RESPONSE', null); };
function view(value: unknown): value is View {
  if (!object(value) || typeof value.candidate_key !== 'string' || !value.candidate_key || !['INCLUDED', 'EXCLUDED', 'UNKNOWN'].includes(String(value.state)) || !strings(value.reasons)) return false;
  if (!(value.action_type === null || typeof value.action_type === 'string') || !(value.autonomy_level === null || typeof value.autonomy_level === 'string') || !(value.signature === null || releaseDigest(value.signature)) || !(value.amount_cents === null || Number.isSafeInteger(value.amount_cents) && (value.amount_cents as number) >= 0)) return false;
  return value.state !== 'INCLUDED' || releaseDigest(value.signature) && value.action_type !== null && value.amount_cents !== null;
}
function familyShape(value: unknown, kind: 'release'): value is RegisteredActionSet['release_family'];
function familyShape(value: unknown, kind: 'joint'): value is RegisteredActionSet['joint_family'];
function familyShape(value: unknown, kind: 'release' | 'joint'): value is Family {
  if (!object(value) || value.algorithm_version !== `full-policy-${kind}-action-producers-v1` || value.simulation !== true || value.bank_authority !== false || value.grants_authority !== false || value.financial_write !== false || value.full_global_adapter_installed !== false || !releaseUUID(value.user_id) || !releaseUUID(value.epoch_id) || typeof value.as_of !== 'string') return false;
  return ['original_actual_input_hash', 'input_hash', 'result_hash'].every(key => releaseDigest(value[key])) && ids(value.original_action_ids) && ids(value.unresolved_original_action_ids) && unique(value.expected_candidate_keys) && unique(value.handled_unsupported_codes) && strings(value.original_actual_reasons) && unique(value.remaining_unsupported_producers) && unique(value.reasons) && Array.isArray(value.results);
}
export async function parseRegisteredActionSet(value: unknown, raw: string): Promise<RegisteredActionSet> {
  assertMoneyFields(value);
  check(object(value) && value.algorithm_version === 'full-policy-registered-action-set-boundary-v5' && value.scope === 'POLICY_BACKED_ACTUAL_REGISTERED_SERVER_PRODUCERS_V5' && value.simulation === true && value.bank_authority === false && value.grants_authority === false && value.financial_write === false && value.arbitrary_manual_intents_covered === false && value.notification_support === 'NOT_IMPLEMENTED_FOR_REGISTERED_V5');
  check(same(JSON.parse(raw), value));
  const original = await parseRecoveryComposedActionSet(value.original_recovery_composed_snapshot, JSON.stringify(value.original_recovery_composed_snapshot));
  const actual = original.original_composed_snapshot.original_actual_snapshot;
  check(value.user_id === original.user_id && value.epoch_id === original.epoch_id && value.as_of === original.as_of && value.original_inventory_hash === original.original_inventory_hash && value.financial_input_hash === original.financial_input_hash);
  check(releaseDigest(value.input_hash) && releaseDigest(value.snapshot_hash) && (value.action_set_signature === null || releaseDigest(value.action_set_signature)) && unique(value.expected_candidate_keys) && unique(value.replaced_original_candidate_keys) && unique(value.unsupported_producers) && unique(value.reasons) && Array.isArray(value.candidates) && value.candidates.every(view));
  const r = value.release_family; const j = value.joint_family;
  check(familyShape(r, 'release') && familyShape(j, 'joint'));
  check(object(r) && ids(r.expected_full_policy_ids) && ids(r.original_authorization_source_ids) && typeof r.release_family_complete === 'boolean' && r.status === (r.release_family_complete ? 'COMPLETE_REGISTERED_RELEASE_FAMILY' : 'UNKNOWN'));
  check(object(j) && ids(j.expected_goal_ids) && typeof j.joint_family_complete === 'boolean' && j.status === (j.joint_family_complete ? 'COMPLETE_REGISTERED_JOINT_FAMILY' : 'UNKNOWN') && j.current_joint_execution === 'EXACT_EXISTING_EFFECT_ONLY' && j.different_allocation_execution === 'NOT_IMPLEMENTED_FOR_DIFFERENT_ALLOCATION');
  for (const f of [r, j]) {
    check(f.user_id === original.user_id && f.epoch_id === original.epoch_id && f.as_of === original.as_of && f.unresolved_original_action_ids.every(id => f.original_action_ids.includes(id)));
  }
  const releaseKeys = new Set<string>();
  for (const row of r.results) {
    check(object(row) && releaseUUID(row.full_policy_id) && releaseUUID(row.source_goal_id) && releaseUUID(row.destination_account_id) && row.candidate_key === `full-release:${row.full_policy_id}:${row.source_goal_id}:${row.destination_account_id}` && typeof row.candidate_key === 'string' && !releaseKeys.has(row.candidate_key) && r.expected_full_policy_ids.includes(row.full_policy_id) && view(row.view) && row.view.candidate_key === row.candidate_key && row.shadow_original_candidate_key === null && row.requires_new_exact_user_confirmation === true && ids(row.unresolved_original_action_ids) && row.unresolved_original_action_ids.every(id => r.original_action_ids.includes(id)));
    check(row.view.state !== 'INCLUDED' || row.view.action_type === 'RELEASE_GOAL' && row.view.autonomy_level === 'ASK_ONCE');
    releaseKeys.add(row.candidate_key);
  }
  for (const key of r.expected_candidate_keys) {
    const parts = key.split(':');
    check(parts.length === 4 && parts[0] === 'full-release' && parts.slice(1).every(releaseUUID) && r.expected_full_policy_ids.includes(parts[1]!));
  }
  check([...releaseKeys].every(key => r.expected_candidate_keys.includes(key)));
  if (r.release_family_complete) check(r.reasons.length === 0 && r.unresolved_original_action_ids.length === 0 && same([...releaseKeys].sort(), [...r.expected_candidate_keys].sort()) && r.results.every(row => object(row) && object(row.view) && row.view.state !== 'UNKNOWN') && same(r.handled_unsupported_codes, r.expected_full_policy_ids.map(id => `FULL_PRODUCER_ADAPTER_MISSING:CrossGoalReallocationPolicy:${id}`)));
  else check(r.reasons.length > 0 && r.handled_unsupported_codes.length === 0);
  const jointKeys = new Set<string>(); const shadows: string[] = [];
  check(same([...j.expected_goal_ids].sort(), actual.dynamic_candidate_keys.map(key => { check(key.startsWith('goal:')); return key.slice(5); }).sort()));
  check(same(j.expected_candidate_keys, j.expected_goal_ids.map(id => `full-joint:goal:${id}`)) && j.handled_unsupported_codes.length === 0);
  for (const row of j.results) {
    check(object(row) && releaseUUID(row.goal_id) && j.expected_goal_ids.includes(row.goal_id) && row.candidate_key === `full-joint:goal:${row.goal_id}` && typeof row.candidate_key === 'string' && !jointKeys.has(row.candidate_key) && view(row.view) && row.view.candidate_key === row.candidate_key && (row.shadow_original_candidate_key === null || typeof row.shadow_original_candidate_key === 'string'));
    check(row.view.state !== 'INCLUDED' || row.view.action_type === 'ALLOCATE_GOAL');
    jointKeys.add(row.candidate_key); if (row.shadow_original_candidate_key !== null) shadows.push(row.shadow_original_candidate_key);
  }
  if (j.joint_family_complete) check(j.reasons.length === 0 && j.unresolved_original_action_ids.length === 0 && same([...jointKeys].sort(), [...j.expected_candidate_keys].sort()) && j.results.every(row => object(row) && object(row.view) && row.view.state !== 'UNKNOWN'));
  else check(j.reasons.length > 0);
  const data = value as RegisteredActionSet;
  const release = data.release_family; const joint = data.joint_family;
  const views = new Map(original.candidates.map(row => [row.candidate_key, row]));
  const expected = new Set(original.expected_candidate_keys); const unsupported = new Set(original.unsupported_producers); const replaced = [...original.replaced_original_candidate_keys];
  const reasons = new Set([...original.reasons.filter(reason => !['RECOVERY_COMPOSED_UNSUPPORTED_CURRENT_PRODUCERS', 'RECOVERY_COMPOSED_CURRENT_PRODUCER_UNKNOWN'].includes(reason)), ...release.reasons, ...joint.reasons]);
  const releaseBinding = release.original_actual_input_hash === actual.input_hash;
  const jointBinding = joint.original_actual_input_hash === actual.input_hash;
  for (const f of [release, joint]) if (f.original_actual_input_hash === actual.input_hash) check(same(f.original_actual_reasons, actual.reasons) && same(f.remaining_unsupported_producers, actual.unsupported_producers.filter(code => !f.handled_unsupported_codes.includes(code)).sort()));
  const releaseExact = releaseBinding && release.release_family_complete && [...releaseKeys].every(key => !views.has(key));
  if (releaseExact) { for (const code of release.handled_unsupported_codes) unsupported.delete(code); for (const row of release.results) { views.set(row.candidate_key, row.view); expected.add(row.candidate_key); } }
  else { reasons.add('REGISTERED_RELEASE_COMPLETE_EXACT_BINDING_NOT_PROVEN'); for (const row of release.results) if (!views.has(row.candidate_key)) views.set(row.candidate_key, { ...row.view, state: 'UNKNOWN', amount_cents: null, autonomy_level: null, signature: null, reasons: [...row.view.reasons, 'REGISTERED_RELEASE_BINDING_NOT_PROVEN'] }); for (const key of release.expected_candidate_keys) expected.add(key); }
  const jointExact = jointBinding && joint.joint_family_complete && new Set(shadows).size === shadows.length && shadows.every(key => views.has(key)) && [...jointKeys].every(key => !views.has(key));
  if (jointExact) { for (const code of joint.handled_unsupported_codes) unsupported.delete(code); for (const row of joint.results) { if (row.shadow_original_candidate_key !== null) { check(typeof row.shadow_original_candidate_key === 'string'); views.delete(row.shadow_original_candidate_key); expected.delete(row.shadow_original_candidate_key); replaced.push(row.shadow_original_candidate_key); } views.set(row.candidate_key, row.view); expected.add(row.candidate_key); } }
  else { reasons.add('REGISTERED_JOINT_COMPLETE_EXACT_BINDING_NOT_PROVEN'); for (const row of joint.results) if (!views.has(row.candidate_key)) views.set(row.candidate_key, { ...row.view, state: 'UNKNOWN', amount_cents: null, autonomy_level: null, signature: null, reasons: [...row.view.reasons, 'REGISTERED_JOINT_BINDING_NOT_PROVEN'] }); for (const key of joint.expected_candidate_keys) expected.add(key); }
  if (unsupported.size) reasons.add('REGISTERED_UNSUPPORTED_CURRENT_PRODUCERS');
  if ([...views.values()].some(row => row.state === 'UNKNOWN')) reasons.add('REGISTERED_CURRENT_PRODUCER_UNKNOWN');
  if (!same([...views.keys()].sort(), [...expected].sort())) reasons.add('REGISTERED_COMPLETE_PRODUCER_DENOMINATOR_DIFFERS');
  if (views.size > 64) reasons.add('REGISTERED_CAPTURE_CAPACITY_EXCEEDED');
  check(same(data.expected_candidate_keys, [...expected].sort()) && same(data.candidates, [...views.values()].sort((a, b) => a.candidate_key < b.candidate_key ? -1 : a.candidate_key > b.candidate_key ? 1 : 0)) && same(data.replaced_original_candidate_keys, replaced.sort()) && same(data.unsupported_producers, [...unsupported].sort()));
  check([...reasons].every(reason => data.reasons.includes(reason)));
  const complete = data.status === 'COMPLETE';
  check(['COMPLETE', 'UNKNOWN'].includes(data.status) && data.global_action_set_complete === complete && (complete ? reasons.size === 0 && data.reasons.length === 0 && releaseExact && jointExact : data.reasons.length > 0 && data.action_set_signature === null));
  if (complete) check(data.action_set_signature === await releaseHash({ scope: data.scope, members: [...new Set(data.candidates.flatMap(row => row.signature === null ? [] : [row.signature]))].sort() }));
  for (const f of [release, joint]) { const { result_hash: hash, ...body } = f; check(hash === await releaseHash(body)); }
  const { snapshot_hash: hash, ...body } = data; check(hash === await releaseHash(body));
  originals.set(data, raw); return data;
}
export async function getRegisteredActionSet(): Promise<RegisteredActionSet> { return await request('/boundary/registered-action-set/current', 'GET', undefined, parseRegisteredActionSet); }
