import type { components } from '../../../../packages/contracts/schema';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { ApiError, request } from './http';
import { releaseCanonicalJson, releaseDigest, releaseHash, releaseUUID } from './goal-release-authorizations';

export type FullPolicyDependencyReview = components['schemas']['FullPolicyDependencyReview'];
export type DependencyReviewBinding = { policyId: string; currentVersionId?: string };
export type CurrentDependencyReview = { policyId: string; versionId: string; reviewHash: string };
const originals = new WeakMap<FullPolicyDependencyReview, string>();
export const originalFullPolicyDependencies = (value: FullPolicyDependencyReview) => originals.get(value) ?? null;
const check: (value: unknown) => asserts value = value => { if (!value) throw new ApiError('策略依赖缺少一致原件或完整分母，请重新读取当前版本', 200, 'INVALID_RESPONSE', null); };
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(row => typeof row === 'string');
const unique = (value: unknown): value is string[] => strings(value) && new Set(value).size === value.length;
const ids = (value: unknown): value is string[] => unique(value) && value.every(releaseUUID);
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const same = (a: unknown, b: unknown) => releaseCanonicalJson(a) === releaseCanonicalJson(b);
const templates = ['RecurringObligationPolicy', 'LivingReservePolicy', 'EmergencyBufferPolicy', 'DatedExpensePolicy', 'LongTermGoalPolicy', 'PeriodicTransferPolicy', 'AssetAuthorizationPolicy', 'RecoveryPolicy', 'GoalAllocationPolicy', 'CrossGoalReallocationPolicy', 'SeasonalReservePolicy', 'InterventionPolicy'];
const roles: Record<string, readonly string[]> = { goal: ['GOAL'], source_account: ['ACCOUNT'], payee_source: ['EVIDENCE'], protected_policy: ['MVP_POLICY', 'FULL_POLICY'], asset_policy: ['MVP_POLICY', 'FULL_POLICY'] };
type Ref = { role: string; kind: string; id: string; binding_hash: string; snapshot: Record<string, unknown> } & Record<string, unknown>;
function refs(value: unknown, owner: string): Map<string, Ref> {
  check(Array.isArray(value)); const result = new Map<string, Ref>();
  for (const row of value) {
    check(object(row) && typeof row.role === 'string' && typeof row.kind === 'string' && roles[row.role]?.includes(row.kind) && releaseUUID(row.id) && releaseDigest(row.binding_hash) && object(row.snapshot) && row.snapshot.id === row.id && row.snapshot.user_id === owner);
    const key = `${row.role}:${row.kind}:${row.id}`; check(!result.has(key)); result.set(key, row as Ref);
  }
  return result;
}
function denominator(config: Record<string, unknown>, actual: Map<string, Ref>) {
  const expected = new Set<string>(); const selectedIds = (value: unknown) => { check(ids(value)); return value; };
  for (const field of ['goal_ids', 'source_goal_ids']) if (config[field] !== undefined) for (const id of selectedIds(config[field])) expected.add(`goal:${id}`);
  for (const [field, role] of [['goal_id', 'goal'], ['source_account_id', 'source_account'], ['asset_policy_id', 'asset_policy']] as const) if (config[field] !== undefined && config[field] !== null) { check(releaseUUID(config[field])); expected.add(`${role}:${config[field]}`); }
  if (config.must_not_reduce_policy_ids !== undefined) for (const id of selectedIds(config.must_not_reduce_policy_ids)) expected.add(`protected_policy:${id}`);
  const actualKeys = [...actual.values()].filter(row => row.role !== 'payee_source').map(row => `${row.role}:${row.id}`);
  check(new Set(actualKeys).size === actualKeys.length && same(actualKeys.sort(), [...expected].sort()) && [...actual.values()].filter(row => row.role === 'payee_source').length === (config.source_account_id ? 1 : 0));
}
function cyclicComponents(graph: Map<string, Set<string>>) {
  const reach = (start: string) => { const seen = new Set<string>(); const todo = [...graph.get(start) ?? []]; while (todo.length) { const node = todo.pop()!; if (!seen.has(node)) { seen.add(node); todo.push(...graph.get(node) ?? []); } } return seen; };
  const reachability = new Map([...graph.keys()].map(id => [id, reach(id)])); const used = new Set<string>(); const groups: string[][] = [];
  for (const id of [...graph.keys()].sort()) {
    if (used.has(id)) continue;
    const group = [...graph.keys()].filter(other => other === id || reachability.get(id)!.has(other) && reachability.get(other)!.has(id)).sort();
    for (const member of group) used.add(member);
    if (group.length > 1 || graph.get(id)!.has(id)) groups.push(group);
  }
  return groups;
}
export async function parseFullPolicyDependencies(value: unknown, binding: DependencyReviewBinding, raw: string): Promise<FullPolicyDependencyReview> {
  assertMoneyFields(value);
  check(releaseUUID(binding.policyId) && (binding.currentVersionId === undefined || releaseUUID(binding.currentVersionId)));
  check(object(value) && value.protocol === 'full-policy-dependency-review-v1' && value.simulation === true && value.selected_policy_id === binding.policyId && releaseUUID(value.user_id) && releaseUUID(value.epoch_id) && time(value.as_of) && same(value, JSON.parse(raw)));
  check(['bank_authority', 'writes_policy_or_finance', 'all_template_action_rechecks_supported', 'position_and_boundary_recovery_verified', 'financial_conflict_solver_applied'].every(key => value[key] === false));
  check(['COMPLETE_CURRENT_DECLARATION_GRAPH', 'UNKNOWN'].includes(String(value.status)) && typeof value.review_required === 'boolean' && count(value.current_policy_count) && count(value.captured_policy_count) && count(value.archived_policy_count) && ids(value.current_policy_ids) && Array.isArray(value.policies) && Array.isArray(value.edges) && Array.isArray(value.cyclic_components) && unique(value.reasons) && strings(value.limitations) && releaseDigest(value.input_hash) && releaseDigest(value.review_hash));
  const policyIds = value.current_policy_ids; const reasons = value.reasons;
  const items = new Map<string, Record<string, unknown>>();
  for (const row of value.policies) {
    check(object(row) && releaseUUID(row.policy_id) && !items.has(row.policy_id) && row.epoch_id === value.epoch_id && releaseUUID(row.version_id) && releaseDigest(row.configuration_hash) && object(row.configuration) && row.configuration_hash === await releaseHash(row.configuration) && typeof row.name === 'string' && templates.includes(String(row.template_name)) && typeof row.effective_status === 'string' && typeof row.reference_validation === 'string' && typeof row.planning_confirmation_valid === 'boolean' && strings(row.source_issues) && object(row.reference_effective_statuses));
    check(row.source_issues.every(issue => reasons.includes(issue)));
    items.set(row.policy_id, row);
  }
  check(items.size === value.captured_policy_count && [...items.keys()].every(id => policyIds.includes(id)));
  const complete = value.status === 'COMPLETE_CURRENT_DECLARATION_GRAPH';
  check(complete ? value.reasons.length === 0 && value.current_policy_count === items.size && same([...items.keys()].sort(), [...value.current_policy_ids].sort()) && items.has(binding.policyId) : value.reasons.length > 0);
  const selected = items.get(binding.policyId); if (selected && binding.currentVersionId !== undefined) check(selected.version_id === binding.currentVersionId);
  const referenceCount = [...items.values()].reduce<number>((sum, row) => { check(Array.isArray(row.recorded_references) && (row.current_references === null || Array.isArray(row.current_references))); return sum + row.recorded_references.length + (row.current_references?.length ?? 0); }, 0);
  const capacity = items.size <= 64 && referenceCount <= 512;
  const expectedEdges: Record<string, unknown>[] = []; const graph = new Map([...items.keys()].map(id => [id, new Set<string>()]));
  let reviewRequired = value.reasons.length > 0;
  for (const [policyId, row] of items) {
    if (!capacity) continue;
    check(object(row.configuration) && object(row.reference_effective_statuses));
    const old = refs(row.recorded_references, value.user_id); denominator(row.configuration, old);
    const current = row.current_references === null ? new Map<string, Ref>() : refs(row.current_references, value.user_id);
    if (row.current_references !== null) denominator(row.configuration, current);
    else check(!complete);
    check(same(Object.keys(row.reference_effective_statuses).sort(), [...new Set([...current.values()].filter(ref => ref.kind === 'MVP_POLICY').map(ref => ref.id))].sort()));
    for (const key of [...new Set([...old.keys(), ...current.keys()])].sort()) {
      const original = old.get(key) ?? null; const fresh = current.get(key) ?? null; const ref = fresh ?? original!;
      const status = fresh === null ? 'UNAVAILABLE' : original === null ? 'ADDED' : fresh.binding_hash === original.binding_hash ? 'UNCHANGED' : 'CHANGED';
      let targetStatus: unknown = fresh?.snapshot.status ?? null;
      if (fresh?.kind === 'FULL_POLICY' && items.has(fresh.id)) targetStatus = items.get(fresh.id)!.effective_status;
      if (fresh?.kind === 'MVP_POLICY') targetStatus = row.reference_effective_statuses[fresh.id];
      check(targetStatus === null || typeof targetStatus === 'string');
      expectedEdges.push({ source_policy_id: policyId, role: ref.role, kind: ref.kind, target_id: ref.id, status, original_binding_hash: original?.binding_hash ?? null, current_binding_hash: fresh?.binding_hash ?? null, original_reference: original, current_reference: fresh, current_target_status: targetStatus, bank_authority: false });
      if (fresh?.kind === 'FULL_POLICY' && ['protected_policy', 'asset_policy'].includes(fresh.role) && items.has(fresh.id)) graph.get(policyId)!.add(fresh.id);
      reviewRequired ||= status !== 'UNCHANGED' || ['SUSPENDED', 'EXPIRED', 'REVOKED', 'CONFLICTED', 'ARCHIVED', 'PROPOSED', 'DISCOVERED', 'MODIFIED'].includes(String(targetStatus));
    }
    reviewRequired ||= row.reference_validation !== 'CURRENT' || !row.planning_confirmation_valid;
  }
  check(same(value.edges, expectedEdges));
  if (!capacity) check(!complete && value.edges.length === 0 && value.cyclic_components.length === 0);
  const groups = capacity ? cyclicComponents(graph) : [];
  check(groups.length === value.cyclic_components.length);
  for (const [index, cycle] of value.cyclic_components.entries()) {
    check(object(cycle) && cycle.meaning === 'DECLARED_DEPENDENCY_CYCLE_REQUIRES_REVIEW' && cycle.financial_infeasibility_proven === false && ids(cycle.policy_ids) && same(cycle.policy_ids, groups[index]) && strings(cycle.example_path));
    const members = cycle.policy_ids; const path = cycle.example_path;
    check(path.length >= 2 && path[0] === path.at(-1) && path.slice(0, -1).every(id => members.includes(id)));
    check(new Set(path.slice(0, -1)).size === path.length - 1 && path.slice(0, -1).every((id, i) => graph.get(id)?.has(path[i + 1]!)));
  }
  reviewRequired ||= groups.length > 0;
  check(value.review_required === reviewRequired);
  const { review_hash: digest, ...body } = value; check(digest === await releaseHash(body));
  const result = value as FullPolicyDependencyReview; originals.set(result, raw); return result;
}
export async function getFullPolicyDependencies(binding: DependencyReviewBinding): Promise<FullPolicyDependencyReview> {
  check(releaseUUID(binding.policyId));
  return await request(`/full-policy-dependencies/${binding.policyId}`, 'GET', undefined, (value, raw) => parseFullPolicyDependencies(value, binding, raw));
}
