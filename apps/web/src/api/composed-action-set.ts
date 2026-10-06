import type { components } from '../../../../packages/contracts/schema';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';
import { parseActualActionSet } from './actual-action-set';
import { releaseCanonicalJson, releaseDigest, releaseHash, releaseUUID } from './goal-release-authorizations';
import { ApiError, request } from './http';

export type ComposedActionSet = components['schemas']['ComposedActionSetSnapshot'];
type View = components['schemas']['CandidateView'];
const originals = new WeakMap<ComposedActionSet, string>();
export const getOriginalComposedActionSet = (value: ComposedActionSet) => originals.get(value) ?? null;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === 'string');
const unique = (value: unknown): value is string[] => strings(value) && new Set(value).size === value.length;
const ids = (value: unknown): value is string[] => unique(value) && value.every(releaseUUID);
const same = (a: unknown, b: unknown) => releaseCanonicalJson(a) === releaseCanonicalJson(b);
const check: (condition: unknown) => asserts condition = (condition) => { if (!condition) throw new ApiError('周期动作组合缺少一致的原件或完整分母，请重新读取', 200, 'INVALID_RESPONSE', null); };
function view(value: unknown): value is View {
  if (!object(value) || typeof value.candidate_key !== 'string' || !value.candidate_key || !['INCLUDED', 'EXCLUDED', 'UNKNOWN'].includes(String(value.state)) || !strings(value.reasons)) return false;
  if (!(value.action_type === null || typeof value.action_type === 'string') || !(value.autonomy_level === null || typeof value.autonomy_level === 'string') || !(value.signature === null || releaseDigest(value.signature)) || !(value.amount_cents === null || Number.isSafeInteger(value.amount_cents) && (value.amount_cents as number) >= 0)) return false;
  return value.state !== 'INCLUDED' || releaseDigest(value.signature) && value.action_type !== null && value.amount_cents !== null;
}
export async function parseComposedActionSet(value: unknown, raw: string): Promise<ComposedActionSet> {
  assertMoneyFields(value);
  check(object(value) && value.algorithm_version === 'full-policy-action-set-boundary-composed-v3' && value.scope === 'POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_COMPOSED_V3' && value.simulation === true && value.bank_authority === false && value.grants_authority === false && value.financial_write === false && value.arbitrary_manual_intents_covered === false && value.notification_support === 'NOT_IMPLEMENTED_FOR_COMPOSED_V3');
  const original = parseActualActionSet(value.original_actual_snapshot, JSON.stringify(value.original_actual_snapshot));
  check(value.user_id === original.user_id && value.epoch_id === original.epoch_id && value.as_of === original.as_of && value.original_inventory_hash === original.original_inventory_hash && value.financial_input_hash === original.financial_input_hash);
  check(['input_hash', 'snapshot_hash'].every((field) => releaseDigest(value[field])) && (value.action_set_signature === null || releaseDigest(value.action_set_signature)) && unique(value.expected_candidate_keys) && unique(value.replaced_original_candidate_keys) && unique(value.unsupported_producers) && unique(value.reasons) && Array.isArray(value.candidates) && value.candidates.every(view));
  const f = value.periodic_family;
  check(object(f) && f.algorithm_version === 'full-policy-periodic-action-producers-v1' && f.simulation === true && f.bank_authority === false && f.grants_authority === false && f.financial_write === false && f.full_global_adapter_installed === false && f.user_id === original.user_id && f.epoch_id === original.epoch_id && f.as_of === original.as_of && f.original_actual_input_hash === original.input_hash);
  check(['input_hash', 'result_hash'].every((field) => releaseDigest(f[field])) && unique(f.expected_full_policy_ids) && f.expected_full_policy_ids.every(releaseUUID) && unique(f.relation_source_ids) && f.relation_source_ids.every(releaseUUID) && Number.isSafeInteger(f.relation_source_count) && f.relation_source_count === f.relation_source_ids.length && unique(f.handled_unsupported_codes) && strings(f.original_actual_reasons) && same(f.original_actual_reasons, original.reasons) && unique(f.remaining_unsupported_producers) && unique(f.reasons) && Array.isArray(f.results));
  const keys = new Set<string>(); const shadows: string[] = [];
  for (const row of f.results) {
    check(object(row) && releaseUUID(row.full_policy_id) && typeof row.candidate_key === 'string' && row.candidate_key === `full-periodic:${row.full_policy_id}` && !keys.has(row.candidate_key) && view(row.view) && row.view.candidate_key === row.candidate_key && (row.shadow_original_candidate_key === null || typeof row.shadow_original_candidate_key === 'string') && ['current_confirmation_evidence_ids', 'original_command_ids', 'unresolved_original_action_ids'].every((field) => ids(row[field])));
    keys.add(row.candidate_key); if (row.shadow_original_candidate_key !== null) shadows.push(row.shadow_original_candidate_key);
  }
  const completeFamily = f.periodic_family_complete === true;
  check(typeof f.periodic_family_complete === 'boolean' && f.status === (completeFamily ? 'COMPLETE_REGISTERED_PERIODIC_FAMILY' : 'UNKNOWN'));
  if (completeFamily) check(f.reasons.length === 0 && keys.size === f.expected_full_policy_ids.length && f.expected_full_policy_ids.every((id) => keys.has(`full-periodic:${id}`)) && f.results.every((row) => object(row) && object(row.view) && row.view.state !== 'UNKNOWN') && shadows.length === new Set(shadows).size);
  const data = value as ComposedActionSet;
  const family = data.periodic_family;
  const originalViews = new Map(original.candidates.map((row) => [row.candidate_key, row]));
  const canCompose = completeFamily && shadows.every((key) => originalViews.has(key)) && [...keys].every((key) => !originalViews.has(key));
  check(same(data.replaced_original_candidate_keys, canCompose ? [...shadows].sort() : []));
  const expectedUnsupported = original.unsupported_producers.filter((code) => !(canCompose && family.handled_unsupported_codes.includes(code))).sort();
  check(same(data.unsupported_producers, expectedUnsupported) && same(family.remaining_unsupported_producers, original.unsupported_producers.filter((code) => !family.handled_unsupported_codes.includes(code)).sort()));
  const expectedViews = new Map(originalViews);
  if (canCompose) for (const row of family.results) { if (row.shadow_original_candidate_key !== null) expectedViews.delete(row.shadow_original_candidate_key); expectedViews.set(row.candidate_key, row.view); }
  else for (const row of family.results) if (!expectedViews.has(row.candidate_key)) expectedViews.set(row.candidate_key, { ...row.view, state: 'UNKNOWN', amount_cents: null, autonomy_level: null, signature: null, reasons: [...row.view.reasons, 'COMPOSED_FAMILY_BINDING_NOT_COMPLETE'] });
  const sortedViews = [...expectedViews.values()].sort((a, b) => a.candidate_key < b.candidate_key ? -1 : a.candidate_key > b.candidate_key ? 1 : 0);
  check(same(data.candidates, sortedViews) && same(data.expected_candidate_keys, [...new Set([...original.expected_candidate_keys.filter((key) => !data.replaced_original_candidate_keys.includes(key)), ...family.expected_full_policy_ids.map((id) => `full-periodic:${id}`)])].sort()));
  const complete = data.status === 'COMPLETE';
  check(['COMPLETE', 'UNKNOWN'].includes(data.status) && data.global_action_set_complete === complete && (!complete || canCompose && data.reasons.length === 0 && data.unsupported_producers.length === 0 && data.candidates.length <= 64 && data.candidates.every((row) => row.state !== 'UNKNOWN')) && (complete || data.action_set_signature === null));
  if (complete) check(data.action_set_signature === await releaseHash({ scope: data.scope, members: [...new Set(data.candidates.flatMap((row) => row.signature === null ? [] : [row.signature]))].sort() }));
  const { result_hash: familyHash, ...familyBody } = family; check(familyHash === await releaseHash(familyBody));
  const { snapshot_hash: originalHash, ...originalBody } = original; check(originalHash === await releaseHash(originalBody));
  const { snapshot_hash: currentHash, ...currentBody } = data; check(currentHash === await releaseHash(currentBody));
  originals.set(data, raw); return data;
}
export async function getComposedActionSet(): Promise<ComposedActionSet> { return await request('/boundary/composed-action-set/current', 'GET', undefined, parseComposedActionSet); }
