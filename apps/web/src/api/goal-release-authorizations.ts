import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import type { Goal } from './goals';
import { getFullGoalModel, getOriginalFullGoalResponse, parseFullGoalModel } from './full-goals';
import type { FullGoalModel, GoalModelBinding } from './full-goals';
import { getFullPolicy, getOriginalFullPolicyResponse, parseFullPolicy } from './full-policies';
import type { FullPolicy } from './full-policies';
import { parseCrossGoalConfiguration, repairConditions } from './goal-reallocation';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { sameFullPolicyJson } from '../features/full-policy-operation';

export type ReleaseScope = components['schemas']['GoalReleaseScope'];
export type ReleasePreview = components['schemas']['ReleaseAuthorizationPreview'];
export type ReleaseConfirmation = components['schemas']['ReleaseAuthorizationConfirmation'] & { accepted: true };
export type ReleaseResponse = components['schemas']['ReleaseAuthorizationResponse'];
export type ReleaseLookup = components['schemas']['ReleaseAuthorizationLookup'];
export type ReleaseBinding = { goal: GoalModelBinding; userId: string; epochId: string };
export type ReleaseSource = { goal: Goal; model: FullGoalModel };
export type ReleaseReview = { preview: ReleasePreview; policy: FullPolicy; sources: ReleaseSource[]; originals: { policy: string | null; goals: string; models: (string | null)[] } };
export type ReleaseIntent = {
  protocol: 'goal-release-browser-command-v1'; user_id: string; owner_goal_id: string;
  policy_id: string; path: string; body: ReleaseConfirmation; body_json: string;
  reviewed_scope: ReleaseScope; request_hash: string;
};
export const releaseUUID = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value);
export const releaseDigest = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const cents = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const exact = (value: Record<string, unknown>, keys: readonly string[]) => Object.keys(value).sort().join('|') === [...keys].sort().join('|');
function check(value: unknown): asserts value { if (!value) throw new Error('专用回拨授权的原目标、范围、确认或恢复摘要不一致；未证明当前资金金额'); }
export function releaseCanonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(releaseCanonicalJson).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${releaseCanonicalJson(value[key])}`).join(',')}}`;
  if (typeof value === 'string') { check(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)); return JSON.stringify(value); }
  if (value === null || typeof value === 'boolean' || Number.isSafeInteger(value)) return JSON.stringify(value);
  throw new Error('摘要只接受精确整数、完整Unicode和原JSON字段');
}
async function digestBytes(algorithm: 'SHA-256' | 'SHA-1', bytes: Uint8Array): Promise<Uint8Array> {
  if (!globalThis.crypto?.subtle) throw new Error('安全摘要能力缺失，未发送或解除原请求');
  return new Uint8Array(await crypto.subtle.digest(algorithm, Uint8Array.from(bytes).buffer));
}
export async function releaseHash(value: unknown): Promise<string> { return [...await digestBytes('SHA-256', new TextEncoder().encode(releaseCanonicalJson(value)))].map((v) => v.toString(16).padStart(2, '0')).join(''); }
/** Exact UUID5 used by the original Python consent/evidence identity, never a bank ID. */
export async function releaseUUID5(namespace: string, name: string): Promise<string> {
  check(releaseUUID(namespace)); const bytes = Uint8Array.from(namespace.replaceAll('-', '').match(/../g)!.map((v) => parseInt(v, 16))); const label = new TextEncoder().encode(name);
  const all = new Uint8Array(bytes.length + label.length); all.set(bytes); all.set(label, bytes.length); const digest = (await digestBytes('SHA-1', all)).slice(0, 16); digest[6] = (digest[6]! & 15) | 80; digest[8] = (digest[8]! & 63) | 128;
  const hex = [...digest].map((v) => v.toString(16).padStart(2, '0')).join(''); return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
export function parseReleaseScope(value: unknown): ReleaseScope {
  assertMoneyFields(value);
  check(object(value) && exact(value, ['protocol', 'simulation', 'user_id', 'epoch_id', 'policy_id', 'policy_version_id', 'policy_configuration_hash', 'source_goals', 'emergency_conditions', 'destination_scope', 'single_action_cap_cents', 'total_cap_cents', 'valid_from', 'valid_until', 'principal_release_allowed', 'ordinary_goal_redistribution_allowed', 'creates_new_income', 'changes_original_assigned_income', 'cumulative_scope', 'overrides_default_lock_only_for_listed_emergencies']) && value.protocol === 'full-goal-release-authorization-v1' && value.simulation === true && ['user_id', 'epoch_id', 'policy_id', 'policy_version_id'].every((key) => releaseUUID(value[key])) && releaseDigest(value.policy_configuration_hash));
  check(value.destination_scope === 'PROTECTED_CASH' && value.cumulative_scope === 'POLICY_ID_ALL_VERSIONS' && value.overrides_default_lock_only_for_listed_emergencies === true && ['principal_release_allowed', 'ordinary_goal_redistribution_allowed', 'creates_new_income', 'changes_original_assigned_income'].every((key) => value[key] === false));
  check(cents(value.single_action_cap_cents) && cents(value.total_cap_cents) && value.single_action_cap_cents > 0 && value.single_action_cap_cents <= value.total_cap_cents && time(value.valid_from) && time(value.valid_until) && Date.parse(value.valid_from) < Date.parse(value.valid_until));
  check(Array.isArray(value.source_goals) && value.source_goals.length > 0 && value.source_goals.length <= 32);
  const goalIds: string[] = [];
  for (const goal of value.source_goals) { check(object(goal) && exact(goal, ['goal_id', 'original_policy_id', 'original_policy_version_id', 'full_model_evidence_id', 'full_model_evidence_hash', 'full_configuration_hash', 'minimum_guarantee_cents']) && ['goal_id', 'original_policy_id', 'original_policy_version_id', 'full_model_evidence_id'].every((key) => releaseUUID(goal[key])) && releaseDigest(goal.full_model_evidence_hash) && releaseDigest(goal.full_configuration_hash) && cents(goal.minimum_guarantee_cents)); goalIds.push(goal.goal_id as string); }
  check(new Set(goalIds).size === goalIds.length && sameFullPolicyJson(goalIds, [...goalIds].sort()));
  check(Array.isArray(value.emergency_conditions) && value.emergency_conditions.length > 0 && value.emergency_conditions.length <= 3 && value.emergency_conditions.every((v) => repairConditions.includes(v)) && new Set(value.emergency_conditions).size === value.emergency_conditions.length && sameFullPolicyJson(value.emergency_conditions, [...value.emergency_conditions].sort()));
  return value as ReleaseScope;
}
export function parseReleaseConfirmation(value: unknown): ReleaseConfirmation {
  check(object(value) && exact(value, ['expected_epoch_id', 'expected_policy_version_id', 'reviewed_scope_hash', 'accepted', 'idempotency_key']) && releaseUUID(value.expected_epoch_id) && releaseUUID(value.expected_policy_version_id) && releaseDigest(value.reviewed_scope_hash) && value.accepted === true && typeof value.idempotency_key === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,149}$/.test(value.idempotency_key)); return value as ReleaseConfirmation;
}
export function parseReleaseIntent(value: unknown): ReleaseIntent {
  check(object(value) && exact(value, ['protocol', 'user_id', 'owner_goal_id', 'policy_id', 'path', 'body', 'body_json', 'reviewed_scope', 'request_hash']) && value.protocol === 'goal-release-browser-command-v1' && releaseUUID(value.user_id) && releaseUUID(value.owner_goal_id) && releaseUUID(value.policy_id) && value.path === `/goal-release-authorizations/policies/${value.policy_id}/confirm` && releaseDigest(value.request_hash) && typeof value.body_json === 'string' && value.body_json.length <= 10000);
  const body = parseReleaseConfirmation(value.body), scope = parseReleaseScope(value.reviewed_scope);
  check(JSON.stringify(body) === value.body_json && scope.user_id === value.user_id && scope.policy_id === value.policy_id && scope.epoch_id === body.expected_epoch_id && scope.policy_version_id === body.expected_policy_version_id && scope.source_goals.some((g) => g.goal_id === value.owner_goal_id));
  return value as ReleaseIntent;
}
export const releaseRequestEnvelope = (userId: string, policyId: string, body: ReleaseConfirmation) => ({ kind: 'CONFIRM_EMERGENCY_GOAL_RELEASE_PERMISSION', user_id: userId, policy_id: policyId, request: body });
const originals = new WeakMap<object, string>();
export const getOriginalReleaseResponse = (value: object) => originals.get(value) ?? null;
export async function parseReleasePreview(value: unknown, binding: ReleaseBinding, policy: FullPolicy, sources: ReleaseSource[], raw?: string): Promise<ReleasePreview> {
  check(object(value) && exact(value, ['simulation', 'scope', 'scope_hash', 'financial_permission_recorded', 'current_financial_amount_verified', 'execution_support']) && value.simulation === true && value.financial_permission_recorded === false && value.current_financial_amount_verified === false && value.execution_support === 'NOT_IMPLEMENTED' && releaseDigest(value.scope_hash));
  const scope = parseReleaseScope(value.scope), current = parseFullPolicy(policy), config = parseCrossGoalConfiguration(current.current_version.configuration);
  check(releaseUUID(binding.userId) && releaseUUID(binding.epochId) && current.template_name === 'CrossGoalReallocationPolicy' && current.epoch_id === binding.epochId && current.current_version.confirmation.user_id === binding.userId && current.planning_confirmation_valid && current.reference_validation === 'CURRENT' && ['ACTIVE', 'CONFIRMED'].includes(current.effective_status) && config.enabled === true);
  check(scope.user_id === binding.userId && scope.epoch_id === binding.epochId && scope.policy_id === current.policy_id && scope.policy_version_id === current.current_version.version_id && scope.policy_configuration_hash === current.current_version.content_hash && scope.policy_configuration_hash === await releaseHash(current.current_version.configuration) && value.scope_hash === await releaseHash(scope));
  check(scope.single_action_cap_cents === config.single_action_cap_cents && scope.total_cap_cents === config.total_cap_cents && sameFullPolicyJson(scope.emergency_conditions, [...config.emergency_conditions as string[]].sort()) && sameFullPolicyJson(scope.source_goals.map((g) => g.goal_id), [...config.source_goal_ids as string[]].sort()));
  check(scope.source_goals.some((g) => g.goal_id === binding.goal.id && g.original_policy_id === binding.goal.policy_id && g.original_policy_version_id === binding.goal.policy_version_id) && sources.length === scope.source_goals.length && new Set(sources.map((s) => s.goal.id)).size === sources.length);
  let earliest = Math.max(Date.parse(current.current_version.valid_from), Date.parse(current.current_version.confirmed_at));
  for (const g of scope.source_goals) { const source = sources.find((s) => s.goal.id === g.goal_id); check(source); const model = parseFullGoalModel(source.model, source.goal);
    check(model.status === 'VERIFIED' && model.epoch_id === binding.epochId && model.policy_id === g.original_policy_id && model.base_policy_version_id === g.original_policy_version_id && model.evidence_id === g.full_model_evidence_id && model.evidence_hash === g.full_model_evidence_hash && model.full_configuration_hash === g.full_configuration_hash && object(model.full_configuration) && model.full_configuration.minimum_guarantee_cents === g.minimum_guarantee_cents && source.goal.minimum_protection_cents === g.minimum_guarantee_cents && model.full_configuration_hash === await releaseHash(model.full_configuration) && time(model.confirmed_at)); earliest = Math.max(earliest, Date.parse(model.confirmed_at)); }
  check(Date.parse(scope.valid_from) === earliest && time(current.current_version.valid_until) && Date.parse(scope.valid_until) === Date.parse(current.current_version.valid_until));
  const result = value as ReleasePreview; if (raw !== undefined) originals.set(result, raw); return result;
}
export async function loadReleaseReview(binding: ReleaseBinding, policy: FullPolicy): Promise<ReleaseReview> {
  check(releaseUUID(binding.userId) && releaseUUID(binding.epochId)); const current = await getFullPolicy(policy.policy_id); check(current.current_version.version_id === policy.current_version.version_id); const config = parseCrossGoalConfiguration(current.current_version.configuration); const ids = config.source_goal_ids as string[];
  check(config.enabled === true && ids.includes(binding.goal.id)); let goalsRaw = '';
  const response = await request<components['schemas']['GoalList']>('/goals', 'GET', undefined, (value, raw) => { assertMoneyFields(value); check(object(value) && value.simulation === true && Array.isArray(value.items) && value.items.length <= 10000 && value.items.every((g) => object(g) && releaseUUID(g.id) && releaseUUID(g.policy_id) && releaseUUID(g.policy_version_id) && typeof g.name === 'string' && cents(g.minimum_protection_cents)) && new Set(value.items.map((g) => g.id)).size === value.items.length); goalsRaw = raw; return value as components['schemas']['GoalList']; });
  const goals = ids.map((id) => { const g = response.items.find((goal) => goal.id === id); check(g); return g; });
  const sources = await Promise.all(goals.map(async (goal) => ({ goal, model: await getFullGoalModel(goal) })));
  const body: components['schemas']['ReleaseAuthorizationPreviewRequest'] = { expected_epoch_id: binding.epochId, expected_policy_version_id: current.current_version.version_id };
  const preview = await request(`/goal-release-authorizations/policies/${current.policy_id}/preview`, 'POST', body, (value, raw) => parseReleasePreview(value, binding, current, sources, raw));
  return { preview, policy: current, sources, originals: { policy: getOriginalFullPolicyResponse(current), goals: goalsRaw, models: sources.map((s) => getOriginalFullGoalResponse(s.model)) } };
}
export async function parseReleaseResponse(value: unknown, intent: ReleaseIntent, raw?: string): Promise<ReleaseResponse> {
  parseReleaseIntent(intent);
  assertMoneyFields(value); check(object(value) && exact(value, ['simulation', 'original_authorization', 'evidence_id', 'evidence_hash', 'original_trace_hash', 'idempotent_replay', 'current_scope_status', 'current_financial_amount_verified', 'execution_support', 'submits_bank_operation']) && value.simulation === true && releaseUUID(value.evidence_id) && releaseDigest(value.evidence_hash) && releaseDigest(value.original_trace_hash) && typeof value.idempotent_replay === 'boolean' && ['CURRENT', 'STALE', 'UNKNOWN', 'ARCHIVED'].includes(String(value.current_scope_status)) && value.current_financial_amount_verified === false && value.execution_support === 'NOT_IMPLEMENTED' && value.submits_bank_operation === false);
  const a = value.original_authorization; check(object(a) && exact(a, ['protocol', 'authorization_id', 'user_id', 'epoch_id', 'policy_id', 'policy_version_id', 'scope', 'scope_hash', 'accepted', 'idempotency_key', 'original_request', 'request_hash', 'confirmed_at', 'valid_until', 'permission_kind']) && a.protocol === 'full-goal-release-authorization-v1' && a.permission_kind === 'EMERGENCY_GOAL_CASH_RELEASE' && a.user_id === intent.user_id && a.epoch_id === intent.body.expected_epoch_id && a.policy_id === intent.policy_id && a.policy_version_id === intent.body.expected_policy_version_id && a.accepted === true && a.idempotency_key === intent.body.idempotency_key && a.scope_hash === intent.body.reviewed_scope_hash && a.request_hash === intent.request_hash && time(a.confirmed_at) && time(a.valid_until));
  parseReleaseConfirmation(a.original_request); const scope = parseReleaseScope(a.scope); check(sameFullPolicyJson(a.original_request, intent.body) && sameFullPolicyJson(scope, intent.reviewed_scope) && a.scope_hash === await releaseHash(scope) && a.request_hash === await releaseHash(releaseRequestEnvelope(intent.user_id, intent.policy_id, intent.body)) && a.valid_until === scope.valid_until && Date.parse(a.confirmed_at) >= Date.parse(scope.valid_from) && Date.parse(a.confirmed_at) < Date.parse(scope.valid_until));
  check(a.authorization_id === await releaseUUID5('c341ff79-6c74-5fc9-a892-b9f5a53ac303', `${intent.user_id}:${intent.body.expected_epoch_id}:${intent.body.idempotency_key}`) && value.evidence_id === await releaseUUID5(a.authorization_id as string, 'authorization-evidence') && value.evidence_hash === await releaseHash(a));
  const result = value as ReleaseResponse; if (raw !== undefined) originals.set(result, raw); return result;
}
export async function parseReleaseLookup(value: unknown, intent: ReleaseIntent, raw?: string): Promise<ReleaseLookup> {
  parseReleaseIntent(intent);
  check(object(value) && exact(value, ['simulation', 'user_id', 'epoch_id', 'idempotency_key', 'status', 'original', 'replacement_allowed']) && value.simulation === true && value.user_id === intent.user_id && value.epoch_id === intent.body.expected_epoch_id && value.idempotency_key === intent.body.idempotency_key && value.replacement_allowed === false && ['RECORDED', 'NOT_FOUND_NOT_FINAL'].includes(String(value.status)));
  if (value.status === 'NOT_FOUND_NOT_FINAL') check(value.original === null); else { const response = await parseReleaseResponse(value.original, intent, raw); check(response.idempotent_replay === true); }
  const result = value as ReleaseLookup; if (raw !== undefined) originals.set(result, raw); return result;
}
export async function confirmReleaseIntent(intent: ReleaseIntent): Promise<ReleaseResponse> {
  parseReleaseIntent(intent); check(intent.request_hash === await releaseHash(releaseRequestEnvelope(intent.user_id, intent.policy_id, intent.body)) && intent.body.reviewed_scope_hash === await releaseHash(intent.reviewed_scope));
  return request(intent.path, 'POST', intent.body, (value, raw) => parseReleaseResponse(value, intent, raw));
}
export async function lookupReleaseIntent(intent: ReleaseIntent): Promise<ReleaseLookup> {
  parseReleaseIntent(intent);
  return request(`/goal-release-authorizations/commands/${intent.body.expected_epoch_id}/by-key/${encodeURIComponent(intent.body.idempotency_key)}`, 'GET', undefined, (value, raw) => parseReleaseLookup(value, intent, raw));
}
