import type { components } from '../../../../packages/contracts/schema';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';
import { parseComposedActionSet } from './composed-action-set';
import { releaseCanonicalJson, releaseDigest, releaseHash, releaseUUID } from './goal-release-authorizations';
import { ApiError, request } from './http';

export type RecoveryComposedActionSet = Omit<components['schemas']['RecoveryComposedActionSetSnapshot'], 'recovery_family'> & { recovery_family: Omit<components['schemas']['RecoveryActionSetResult'], 'results'> & { results: Required<components['schemas']['RecoveryProducerResult']>[] } };
type View = components['schemas']['CandidateView'];
const originals = new WeakMap<RecoveryComposedActionSet, string>();
export const originalRecoveryComposedActionSet = (value: RecoveryComposedActionSet) => originals.get(value) ?? null;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string');
const unique = (value: unknown): value is string[] => strings(value) && new Set(value).size === value.length;
const ids = (value: unknown): value is string[] => unique(value) && value.every(releaseUUID);
const same = (a: unknown, b: unknown) => releaseCanonicalJson(a) === releaseCanonicalJson(b);
const check: (condition: unknown) => asserts condition = condition => { if (!condition) throw new ApiError('恢复动作组合缺少一致原件或完整来源，请重新读取', 200, 'INVALID_RESPONSE', null); };
function view(value: unknown): value is View {
  if (!object(value) || typeof value.candidate_key !== 'string' || !value.candidate_key || !['INCLUDED', 'EXCLUDED', 'UNKNOWN'].includes(String(value.state)) || !strings(value.reasons)) return false;
  if (!(value.action_type === null || typeof value.action_type === 'string') || !(value.autonomy_level === null || typeof value.autonomy_level === 'string') || !(value.signature === null || releaseDigest(value.signature)) || !(value.amount_cents === null || Number.isSafeInteger(value.amount_cents) && (value.amount_cents as number) >= 0)) return false;
  return value.state !== 'INCLUDED' || releaseDigest(value.signature) && value.action_type !== null && value.amount_cents !== null;
}
export async function parseRecoveryComposedActionSet(value: unknown, raw: string): Promise<RecoveryComposedActionSet> {
  assertMoneyFields(value);
  check(object(value) && value.algorithm_version === 'full-policy-action-set-boundary-recovery-composed-v4' && value.scope === 'POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_RECOVERY_COMPOSED_V4' && value.simulation === true && value.bank_authority === false && value.grants_authority === false && value.financial_write === false && value.arbitrary_manual_intents_covered === false && value.notification_support === 'NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4');
  const original = await parseComposedActionSet(value.original_composed_snapshot, JSON.stringify(value.original_composed_snapshot));
  check(value.user_id === original.user_id && value.epoch_id === original.epoch_id && value.as_of === original.as_of && value.original_inventory_hash === original.original_inventory_hash && value.financial_input_hash === original.financial_input_hash);
  check(releaseDigest(value.input_hash) && releaseDigest(value.snapshot_hash) && (value.action_set_signature === null || releaseDigest(value.action_set_signature)) && unique(value.expected_candidate_keys) && unique(value.replaced_original_candidate_keys) && unique(value.unsupported_producers) && unique(value.reasons) && Array.isArray(value.candidates) && value.candidates.every(view));
  const f = value.recovery_family;
  check(object(f) && f.algorithm_version === 'full-policy-recovery-action-producers-v1' && f.simulation === true && f.bank_authority === false && f.grants_authority === false && f.financial_write === false && f.full_global_adapter_installed === false && releaseUUID(f.user_id) && releaseUUID(f.epoch_id) && typeof f.as_of === 'string' && ['input_hash', 'result_hash', 'original_actual_input_hash'].every(field => releaseDigest(f[field])));
  check(['expected_full_policy_ids', 'original_position_ids', 'original_action_ids', 'unresolved_original_action_ids'].every(field => ids(f[field])) && unique(f.handled_unsupported_codes) && unique(f.remaining_unsupported_producers) && strings(f.original_actual_reasons) && unique(f.reasons) && Array.isArray(f.results) && typeof f.recovery_family_complete === 'boolean');
  const keys = new Set<string>(); const shadows: string[] = [];
  for (const row of f.results) {
    check(object(row) && releaseUUID(row.full_policy_id) && (row.full_policy_version_id === null || releaseUUID(row.full_policy_version_id)) && row.candidate_key === `full-recovery:${row.full_policy_id}` && typeof row.candidate_key === 'string' && !keys.has(row.candidate_key) && view(row.view) && row.view.candidate_key === row.candidate_key && row.requires_new_exact_user_confirmation === true && (row.shadow_original_candidate_key === null || typeof row.shadow_original_candidate_key === 'string') && ['selected_position_ids', 'unsupported_position_ids', 'authority_excluded_position_ids', 'unresolved_original_action_ids'].every(field => ids(row[field])));
    check(row.view.state !== 'INCLUDED' || row.view.action_type === 'REDEEM_ASSET' && row.view.autonomy_level === 'ASK_ONCE');
    keys.add(row.candidate_key); if (row.shadow_original_candidate_key !== null) shadows.push(row.shadow_original_candidate_key);
  }
  const data = value as RecoveryComposedActionSet; const family = data.recovery_family;
  const completeFamily = family.recovery_family_complete;
  check(family.status === (completeFamily ? 'COMPLETE_REGISTERED_RECOVERY_FAMILY' : 'UNKNOWN'));
  check(family.unresolved_original_action_ids.every(id => family.original_action_ids.includes(id)));
  for (const row of family.results) {
    check([...row.selected_position_ids, ...row.unsupported_position_ids, ...row.authority_excluded_position_ids].every(id => family.original_position_ids.includes(id)) && row.unresolved_original_action_ids.every(id => family.original_action_ids.includes(id)));
  }
  if (completeFamily) check(family.reasons.length === 0 && family.unresolved_original_action_ids.length === 0 && keys.size === family.expected_full_policy_ids.length && family.expected_full_policy_ids.every(id => keys.has(`full-recovery:${id}`)) && family.results.every(row => row.view.state !== 'UNKNOWN') && shadows.length === new Set(shadows).size && same(family.handled_unsupported_codes, family.expected_full_policy_ids.map(id => `FULL_PRODUCER_ADAPTER_MISSING:RecoveryPolicy:${id}`).sort()));
  const binding = family.user_id === original.user_id && family.epoch_id === original.epoch_id && family.as_of === original.as_of && family.original_actual_input_hash === original.original_actual_snapshot.input_hash;
  if (binding) check(same(family.original_actual_reasons, original.original_actual_snapshot.reasons));
  const oldViews = new Map(original.candidates.map(row => [row.candidate_key, row]));
  const exact = binding && completeFamily && shadows.every(key => oldViews.has(key)) && [...keys].every(key => !oldViews.has(key));
  const replaced = [...original.replaced_original_candidate_keys, ...(exact ? shadows : [])].sort();
  check(same(data.replaced_original_candidate_keys, replaced) && same(data.unsupported_producers, original.unsupported_producers.filter(code => !(exact && family.handled_unsupported_codes.includes(code))).sort()));
  if (binding) check(same(family.remaining_unsupported_producers, original.original_actual_snapshot.unsupported_producers.filter(code => !family.handled_unsupported_codes.includes(code)).sort()));
  const expectedViews = new Map(oldViews); const expectedKeys = new Set(original.expected_candidate_keys);
  if (exact) for (const row of family.results) {
    if (row.shadow_original_candidate_key !== null) { expectedViews.delete(row.shadow_original_candidate_key); expectedKeys.delete(row.shadow_original_candidate_key); }
    expectedViews.set(row.candidate_key, row.view); expectedKeys.add(row.candidate_key);
  }
  else { for (const row of family.results) if (!expectedViews.has(row.candidate_key)) expectedViews.set(row.candidate_key, { ...row.view, state: 'UNKNOWN', amount_cents: null, autonomy_level: null, signature: null, reasons: [...row.view.reasons, 'RECOVERY_COMPOSED_FAMILY_BINDING_NOT_COMPLETE'] }); for (const id of family.expected_full_policy_ids) expectedKeys.add(`full-recovery:${id}`); }
  check(same(data.expected_candidate_keys, [...expectedKeys].sort()) && same(data.candidates, [...expectedViews.values()].sort((a, b) => a.candidate_key < b.candidate_key ? -1 : a.candidate_key > b.candidate_key ? 1 : 0)));
  const expectedReasons = new Set([...original.reasons.filter(reason => !['COMPOSED_UNSUPPORTED_CURRENT_PRODUCERS', 'COMPOSED_CURRENT_PRODUCER_UNKNOWN'].includes(reason)), ...family.reasons]);
  if (!binding) expectedReasons.add('RECOVERY_COMPOSED_EXACT_ORIGINAL_SNAPSHOT_BINDING_DIFFERS');
  if (!exact) expectedReasons.add('RECOVERY_COMPOSED_COMPLETE_UNIQUE_MAPPING_NOT_PROVEN');
  if (!same([...expectedViews.keys()].sort(), [...expectedKeys].sort())) expectedReasons.add('RECOVERY_COMPOSED_COMPLETE_PRODUCER_DENOMINATOR_DIFFERS');
  if (data.unsupported_producers.length) expectedReasons.add('RECOVERY_COMPOSED_UNSUPPORTED_CURRENT_PRODUCERS');
  if (data.candidates.some(row => row.state === 'UNKNOWN')) expectedReasons.add('RECOVERY_COMPOSED_CURRENT_PRODUCER_UNKNOWN');
  if (data.candidates.length > 64) expectedReasons.add('RECOVERY_COMPOSED_CAPTURE_CAPACITY_EXCEEDED');
  check([...expectedReasons].every(reason => data.reasons.includes(reason)));
  const complete = data.status === 'COMPLETE';
  check(['COMPLETE', 'UNKNOWN'].includes(data.status) && data.global_action_set_complete === complete && (complete ? exact && expectedReasons.size === 0 && data.reasons.length === 0 && data.candidates.length <= 64 : data.reasons.length > 0 && data.action_set_signature === null));
  if (complete) check(data.action_set_signature === await releaseHash({ scope: data.scope, members: [...new Set(data.candidates.flatMap(row => row.signature === null ? [] : [row.signature]))].sort() }));
  const { result_hash: familyHash, ...familyBody } = family; check(familyHash === await releaseHash(familyBody));
  const { snapshot_hash: currentHash, ...currentBody } = data; check(currentHash === await releaseHash(currentBody));
  originals.set(data, raw); return data;
}
export async function getRecoveryComposedActionSet(): Promise<RecoveryComposedActionSet> { return await request('/boundary/recovery-composed-action-set/current', 'GET', undefined, parseRecoveryComposedActionSet); }
