import type { components } from '../../../../packages/contracts/schema';
import type { Goal } from './goals';
import type { JointAllocation } from './joint-planning';
import { parseFullGoalConfiguration, parseFullGoalPreview } from './full-goals';
import { object } from '../features/policy-form';
import { request } from './http';

// Actual backend aliases. These intentionally need root's generated schema;
// there is no hand-written fallback that can silently become a financial DTO.
export type FullGoalConflicts = components['schemas']['FullGoalConflictResponse'];
export type FullGoalRepairs = components['schemas']['FullGoalRepairResponse'];
export type GoalRepairSelection = components['schemas']['GoalRepairPreviewBody'];
const originals = new WeakMap<FullGoalConflicts | FullGoalRepairs, string>();
export const getOriginalGoalConflictResponse = (value: FullGoalConflicts | FullGoalRepairs) => originals.get(value) ?? null;
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(v);
const hash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const integer = (v: unknown): v is number => Number.isSafeInteger(v);
const cents = (v: unknown): v is number => integer(v) && v >= 0;
const text = (v: unknown): v is string => typeof v === 'string';
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(text);
const ids = (v: unknown): v is string[] => Array.isArray(v) && v.every(uuid) && new Set(v).size === v.length;
const date = (v: unknown): v is string => text(v) && /^\d{4}-\d\d-\d\d$/.test(v) && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v;
const timestamp = (v: unknown): v is string => text(v) && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const exact = (v: Record<string, unknown>, keys: readonly string[]) => Object.keys(v).sort().join('|') === [...keys].sort().join('|');
function need(v: unknown): asserts v { if (!v) throw new Error('目标冲突/修复未通过原版本、完整分母、反事实范围或精确字段校验'); }
function same(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((child, i) => same(child, right[i]));
  if (object(left) && object(right)) return exact(left, Object.keys(right)) && Object.keys(left).every((key) => same(left[key], right[key]));
  return false;
}
function source(v: unknown, owner: string): void { need(object(v) && exact(v, ['user_id', 'evidence_id', 'content_hash']) && v.user_id === owner && uuid(v.evidence_id) && hash(v.content_hash)); }
const kinds = ['MINIMUM_GUARANTEE', 'DEADLINE_COMPLETION', 'MONTHLY_MAX'];
function constraintId(v: unknown, goals: readonly string[]): v is string { return text(v) && kinds.some((kind) => v.startsWith(`${kind}:`) && goals.includes(v.slice(kind.length + 1))); }

function allocation(v: unknown, goals: readonly string[], versions: Map<string, string>): asserts v is JointAllocation {
  need(object(v) && exact(v, ['algorithm_version', 'status', 'purpose', 'grants_authority', 'input_hash', 'objective_vector', 'budget_cents', 'visited_nodes', 'goals', 'income_uses', 'delay_scope', 'reasons']));
  need(v.algorithm_version === 'critical-flow-lexicographic-v1' && v.purpose === 'CURRENT_PERIOD_PLANNING_ONLY' && v.grants_authority === false && v.status === 'OPTIMAL' && hash(v.input_hash) && cents(v.budget_cents) && cents(v.visited_nodes) && v.delay_scope === 'ACTIVE_INCOMPLETE_GOALS_CURRENT_DECISION_LOWER_BOUND' && strings(v.reasons));
  need(Array.isArray(v.objective_vector) && v.objective_vector.length === 8 && v.objective_vector.every(cents) && Array.isArray(v.goals) && v.goals.length === goals.length && Array.isArray(v.income_uses));
  const found = new Set<string>(); const amounts = new Map<string, number>();
  for (const row of v.goals) {
    need(object(row) && exact(row, ['goal_id', 'effective_policy_version_id', 'amount_cents', 'minimum_shortfall_cents', 'projected_owned_cents', 'completion_date', 'delay_lower_bound_days', 'delay_censored', 'deferral_cost_lower_bound_cents']) && uuid(row.goal_id) && goals.includes(row.goal_id) && !found.has(row.goal_id) && uuid(row.effective_policy_version_id));
    if (versions.has(row.goal_id)) need(row.effective_policy_version_id === versions.get(row.goal_id));
    need(['amount_cents', 'minimum_shortfall_cents', 'projected_owned_cents', 'delay_lower_bound_days', 'deferral_cost_lower_bound_cents'].every((key) => cents(row[key])) && (row.completion_date === null || date(row.completion_date)) && typeof row.delay_censored === 'boolean' && (!row.delay_censored || row.completion_date === null));
    need(Number(row.projected_owned_cents) >= Number(row.amount_cents)); found.add(row.goal_id); amounts.set(row.goal_id, Number(row.amount_cents));
  }
  const used = new Map<string, bigint>();
  for (const row of v.income_uses) { need(object(row) && exact(row, ['fragment_id', 'origin_transaction_id', 'source_account_id', 'goal_id', 'amount_cents']) && ['fragment_id', 'origin_transaction_id', 'source_account_id', 'goal_id'].every((key) => uuid(row[key])) && found.has(String(row.goal_id)) && cents(row.amount_cents) && row.amount_cents > 0); used.set(String(row.goal_id), (used.get(String(row.goal_id)) ?? 0n) + BigInt(row.amount_cents)); }
  need([...used.values()].reduce((n, v) => n + v, 0n) <= BigInt(v.budget_cents));
  for (const [id, amount] of amounts) need((used.get(id) ?? 0n) === BigInt(amount));
}

function candidate(v: unknown, owner: string, goals: readonly string[]): void {
  need(object(v) && exact(v, ['candidate_id', 'goal_id', 'source_policy_version_id', 'monthly_min_cents', 'monthly_max_cents', 'deadline', 'permission_ref']) && uuid(v.candidate_id) && uuid(v.goal_id) && goals.includes(v.goal_id) && uuid(v.source_policy_version_id) && v.monthly_min_cents === null && v.deadline === null && cents(v.monthly_max_cents)); source(v.permission_ref, owner);
}
function repair(v: unknown, owner: string, goals: readonly string[], inputHash: string): void {
  need(object(v) && exact(v, ['status', 'original_input_hash', 'grants_authority', 'requires_new_version_confirmation', 'candidates', 'unaffected_goal_ids', 'changed_policy_count', 'parameter_deviation_numerator', 'parameter_deviation_denominator', 'priority_loss_cents', 'hypothetical_allocation', 'reasons']) && ['PROPOSAL', 'NOT_NEEDED', 'NO_PERMITTED_REPAIR', 'BASE_INFEASIBLE', 'UNKNOWN'].includes(String(v.status)) && v.original_input_hash === inputHash && v.grants_authority === false && v.requires_new_version_confirmation === true && strings(v.reasons) && ids(v.unaffected_goal_ids) && Array.isArray(v.candidates) && v.candidates.length <= 8);
  const changed = new Map<string, string>(); const candidateIds = new Set<string>();
  for (const row of v.candidates) { candidate(row, owner, goals); need(object(row) && !changed.has(String(row.goal_id)) && !candidateIds.has(String(row.candidate_id))); changed.set(String(row.goal_id), String(row.source_policy_version_id)); candidateIds.add(String(row.candidate_id)); }
  need(v.unaffected_goal_ids.every((id) => goals.includes(id) && !changed.has(id)) && changed.size + v.unaffected_goal_ids.length === goals.length);
  if (v.status === 'PROPOSAL' || v.status === 'NOT_NEEDED') {
    need(v.changed_policy_count === changed.size && cents(v.parameter_deviation_numerator) && integer(v.parameter_deviation_denominator) && v.parameter_deviation_denominator > 0 && cents(v.priority_loss_cents));
    if (v.status === 'PROPOSAL') { need(changed.size > 0); allocation(v.hypothetical_allocation, goals, changed); }
    else need(changed.size === 0 && v.parameter_deviation_numerator === 0 && v.priority_loss_cents === 0 && v.hypothetical_allocation === null);
  } else need(changed.size === 0 && ['changed_policy_count', 'parameter_deviation_numerator', 'parameter_deviation_denominator', 'priority_loss_cents', 'hypothetical_allocation'].every((key) => v[key] === null));
}

export function parseFullGoalConflicts(v: unknown, raw?: string): FullGoalConflicts {
  need(object(v) && exact(v, ['protocol', 'simulation', 'user_id', 'epoch_id', 'as_of', 'state', 'registered_goal_count', 'included_goal_ids', 'uncovered_goal_ids', 'current_input_hash', 'review_state_hash', 'planning_source_digest', 'full_binding_hash', 'explanation', 'current_permission_repair', 'source_evidence_ids', 'reasons', 'planning_only', 'grants_authority', 'execution_support', 'limits']));
  need(v.protocol === 'full-goal-conflict-read-v1' && v.simulation === true && uuid(v.user_id) && (v.epoch_id === null || uuid(v.epoch_id)) && timestamp(v.as_of) && ['COMPUTED', 'UNKNOWN'].includes(String(v.state)) && cents(v.registered_goal_count) && ids(v.included_goal_ids) && ids(v.uncovered_goal_ids) && ids(v.source_evidence_ids) && strings(v.reasons) && strings(v.limits));
  need(new Set([...v.included_goal_ids, ...v.uncovered_goal_ids]).size === v.registered_goal_count && (v.current_input_hash === null || hash(v.current_input_hash)) && (v.review_state_hash === null || hash(v.review_state_hash)) && hash(v.planning_source_digest) && (v.full_binding_hash === null || hash(v.full_binding_hash)) && v.planning_only === true && v.grants_authority === false && v.execution_support === 'NOT_IMPLEMENTED');
  if (v.state === 'UNKNOWN') need(v.explanation === null && v.current_permission_repair === null && v.review_state_hash === null && v.reasons.length > 0);
  else {
    need(uuid(v.epoch_id) && hash(v.current_input_hash) && hash(v.review_state_hash) && hash(v.full_binding_hash) && v.uncovered_goal_ids.length === 0 && v.included_goal_ids.length <= 8);
    const e = v.explanation;
    need(object(e) && exact(e, ['protocol', 'original_input_hash', 'registered_input_goal_ids', 'conflict', 'constraints', 'goals_outside_this_minimal_set', 'all_other_goal_constraints_proven_compatible', 'minimality', 'removal_witness_scope', 'immutable_financial_point_count', 'immutable_base_blocks', 'grants_authority']) && e.protocol === 'full-goal-minimal-conflict-v1' && e.original_input_hash === v.current_input_hash && ids(e.registered_input_goal_ids) && same([...e.registered_input_goal_ids].sort(), [...v.included_goal_ids].sort()) && ids(e.goals_outside_this_minimal_set) && e.all_other_goal_constraints_proven_compatible === false && e.minimality === 'DELETION_MINIMAL_NOT_MINIMUM_CARDINALITY' && e.removal_witness_scope === 'THIS_MINIMAL_SET_MINUS_ONE' && integer(e.immutable_financial_point_count) && e.immutable_financial_point_count > 0 && e.immutable_financial_point_count <= 1098 && e.grants_authority === false);
    const c = e.conflict;
    need(object(c) && exact(c, ['status', 'scope', 'grants_authority', 'input_hash', 'constraint_ids', 'deletion_checks', 'reasons']) && ['MINIMAL_CONFLICT', 'NO_CONFLICT', 'BASE_INFEASIBLE'].includes(String(c.status)) && c.scope === 'GOAL_POLICIES_WITH_IMMUTABLE_FINANCIAL_BASE' && c.grants_authority === false && c.input_hash === v.current_input_hash && strings(c.constraint_ids) && new Set(c.constraint_ids).size === c.constraint_ids.length && c.constraint_ids.every((id) => constraintId(id, v.included_goal_ids as string[])) && Array.isArray(c.deletion_checks) && strings(c.reasons) && Array.isArray(e.constraints) && e.constraints.length === c.constraint_ids.length && Array.isArray(e.immutable_base_blocks));
    const details = new Set<string>(); const conflictGoals = new Set<string>();
    for (const row of e.constraints) {
      need(object(row) && exact(row, ['constraint_id', 'kind', 'goal_id', 'policy_id', 'current_version_id', 'original_parameter_cents', 'current_owned_cents', 'current_month_contributed_cents', 'required_new_cents', 'allowed_new_cents', 'deadline', 'source_refs']) && kinds.includes(String(row.kind)) && uuid(row.goal_id) && uuid(row.policy_id) && uuid(row.current_version_id) && row.constraint_id === `${row.kind}:${row.goal_id}` && c.constraint_ids.includes(String(row.constraint_id)) && !details.has(String(row.constraint_id)) && cents(row.original_parameter_cents) && cents(row.current_owned_cents) && cents(row.current_month_contributed_cents) && Array.isArray(row.source_refs) && row.source_refs.length > 0);
      if (row.kind === 'MONTHLY_MAX') need(row.required_new_cents === null && cents(row.allowed_new_cents) && row.deadline === null);
      else need(cents(row.required_new_cents) && row.allowed_new_cents === null && (row.kind === 'DEADLINE_COMPLETION' ? date(row.deadline) : row.deadline === null));
      for (const ref of row.source_refs) { source(ref, v.user_id); need(object(ref) && v.source_evidence_ids.includes(String(ref.evidence_id))); }
      details.add(String(row.constraint_id)); conflictGoals.add(row.goal_id);
    }
    need(same([...e.goals_outside_this_minimal_set].sort(), v.included_goal_ids.filter((id) => !conflictGoals.has(id)).sort()));
    const removed = new Set<string>();
    for (const check of c.deletion_checks) {
      need(object(check) && exact(check, ['removed_constraint_id', 'remaining_feasible', 'counterfactual_only', 'witness_amounts_cents']) && c.constraint_ids.includes(String(check.removed_constraint_id)) && !removed.has(String(check.removed_constraint_id)) && check.remaining_feasible === true && check.counterfactual_only === true && object(check.witness_amounts_cents));
      need(exact(check.witness_amounts_cents, v.included_goal_ids) && Object.values(check.witness_amounts_cents).every(cents)); removed.add(String(check.removed_constraint_id));
    }
    if (c.status === 'MINIMAL_CONFLICT') need(removed.size > 0 && removed.size === c.constraint_ids.length && e.immutable_base_blocks.length === 0);
    else need(c.constraint_ids.length === 0 && removed.size === 0);
    const points = new Set<number>();
    for (const block of e.immutable_base_blocks) { need(object(block) && exact(block, ['point_index', 'date', 'cash_cents', 'protected_cents', 'shortfall_cents', 'source_refs', 'adjustable']) && cents(block.point_index) && block.point_index < e.immutable_financial_point_count && !points.has(block.point_index) && date(block.date) && integer(block.cash_cents) && cents(block.protected_cents) && cents(block.shortfall_cents) && block.shortfall_cents > 0 && BigInt(block.protected_cents) - BigInt(block.cash_cents) === BigInt(block.shortfall_cents) && block.adjustable === false && Array.isArray(block.source_refs) && block.source_refs.length > 0); for (const ref of block.source_refs) source(ref, v.user_id); points.add(block.point_index); }
    need(c.status === 'BASE_INFEASIBLE' ? points.size > 0 : points.size === 0);
    repair(v.current_permission_repair, v.user_id, v.included_goal_ids, v.current_input_hash);
    need(object(v.current_permission_repair) && v.current_permission_repair.status !== 'PROPOSAL');
  }
  const result = v as FullGoalConflicts; if (raw !== undefined) originals.set(result, raw); return result;
}

export function validateGoalConflictGoals(data: FullGoalConflicts, goals?: readonly Goal[]): void {
  if (goals === undefined) return;
  need(new Set(goals.map((row) => row.id)).size === goals.length && goals.length === data.registered_goal_count && same(goals.map((row) => row.id).sort(), [...new Set([...data.included_goal_ids, ...data.uncovered_goal_ids])].sort()));
  for (const c of data.explanation?.constraints ?? []) need(goals.some((row) => row.id === c.goal_id && row.policy_id === c.policy_id && row.policy_version_id === c.current_version_id));
}

export function parseGoalRepairSelection(v: unknown, original: FullGoalConflicts): GoalRepairSelection {
  need(object(v) && exact(v, ['expected_epoch_id', 'reviewed_state_hash', 'adjustments']) && original.state === 'COMPUTED' && v.expected_epoch_id === original.epoch_id && v.reviewed_state_hash === original.review_state_hash && Array.isArray(v.adjustments) && v.adjustments.length > 0 && v.adjustments.length <= 8);
  const chosen = new Set<string>();
  for (const row of v.adjustments) { need(object(row) && exact(row, ['goal_id', 'expected_version_id', 'minimum_new_monthly_max_cents', 'maximum_new_monthly_max_cents']) && uuid(row.goal_id) && original.included_goal_ids.includes(row.goal_id) && !chosen.has(row.goal_id) && uuid(row.expected_version_id) && cents(row.minimum_new_monthly_max_cents) && cents(row.maximum_new_monthly_max_cents) && row.minimum_new_monthly_max_cents <= row.maximum_new_monthly_max_cents); need((original.explanation?.constraints ?? []).filter((c) => c.goal_id === row.goal_id).every((c) => c.current_version_id === row.expected_version_id)); chosen.add(row.goal_id); }
  return v as GoalRepairSelection;
}

export function parseFullGoalRepairs(v: unknown, original: FullGoalConflicts, body: GoalRepairSelection, raw?: string): FullGoalRepairs {
  parseGoalRepairSelection(body, original);
  need(object(v) && exact(v, ['protocol', 'simulation', 'user_id', 'as_of', 'original_conflicts', 'proposal', 'version_previews', 'state', 'reasons', 'planning_only', 'grants_authority', 'writes_performed', 'confirmation_is_separate', 'multi_version_atomic_confirmation_supported', 'limitations']) && v.protocol === 'full-goal-repair-preview-v1' && v.simulation === true && v.user_id === original.user_id && timestamp(v.as_of) && strings(v.reasons) && strings(v.limitations) && v.planning_only === true && v.grants_authority === false && v.writes_performed === false && v.confirmation_is_separate === true && v.multi_version_atomic_confirmation_supported === false && Array.isArray(v.version_previews));
  const current = parseFullGoalConflicts(v.original_conflicts); need(current.user_id === original.user_id && current.epoch_id === body.expected_epoch_id && Date.parse(current.as_of) === Date.parse(v.as_of));
  if (current.state === 'UNKNOWN') need(v.state === 'UNKNOWN' && v.proposal === null && v.version_previews.length === 0);
  else {
    need(current.review_state_hash === body.reviewed_state_hash && object(v.proposal)); const p = v.proposal;
    need(exact(p, ['protocol', 'original_input_hash', 'search_input_hash', 'selection_hash', 'actual_preview_selection', 'selection_source', 'selected_ranges_are_existing_financial_permissions', 'source_refs_are_current_version_identity_only', 'candidates', 'range_outcomes', 'repair', 'minimality_scope', 'original_execution_caps_unchanged', 'original_hard_protection_unchanged', 'future_income_used_cents', 'grants_authority', 'requires_new_version_confirmation']) && p.protocol === 'full-goal-user-selected-repair-v1' && p.original_input_hash === current.current_input_hash && hash(p.search_input_hash) && hash(p.selection_hash) && same(p.actual_preview_selection, body) && p.selection_source === 'USER_CURRENT_READONLY_PREVIEW_REQUEST' && p.selected_ranges_are_existing_financial_permissions === false && p.source_refs_are_current_version_identity_only === true && p.minimality_scope === 'EXACT_INTEGER_CURRENT_PERIOD_WITH_SELECTED_MAX_RANGES' && p.original_execution_caps_unchanged === true && p.original_hard_protection_unchanged === true && p.future_income_used_cents === 0 && p.grants_authority === false && p.requires_new_version_confirmation === true && Array.isArray(p.candidates) && p.candidates.length <= body.adjustments.length && Array.isArray(p.range_outcomes));
    const generated = new Map<string, Record<string, unknown>>();
    for (const row of p.candidates) { candidate(row, v.user_id as string, current.included_goal_ids); need(object(row) && !generated.has(String(row.goal_id))); const option = body.adjustments.find((option) => option.goal_id === row.goal_id); need(option && option.expected_version_id === row.source_policy_version_id && cents(row.monthly_max_cents) && row.monthly_max_cents >= option.minimum_new_monthly_max_cents && row.monthly_max_cents <= option.maximum_new_monthly_max_cents); generated.set(String(row.goal_id), row); }
    const ranges = new Set<string>();
    for (const row of p.range_outcomes) { need(object(row) && exact(row, ['goal_id', 'current_version_id', 'original_monthly_max_cents', 'proposed_monthly_max_cents', 'reason']) && uuid(row.goal_id) && !ranges.has(row.goal_id) && cents(row.original_monthly_max_cents)); const option = body.adjustments.find((option) => option.goal_id === row.goal_id); need(option && row.current_version_id === option.expected_version_id && ['MINIMUM_CRITICAL_CAP_WITHIN_SELECTED_RANGE', 'CURRENT_CAP_IS_NOT_A_HARD_CONFLICT', 'SELECTED_RANGE_CANNOT_RESTORE_CAP_FEASIBILITY', 'GOAL_NOT_CURRENTLY_AUTHORIZED'].includes(String(row.reason))); if (row.reason === 'MINIMUM_CRITICAL_CAP_WITHIN_SELECTED_RANGE') need(row.proposed_monthly_max_cents === generated.get(row.goal_id)?.monthly_max_cents && cents(row.proposed_monthly_max_cents) && row.proposed_monthly_max_cents > row.original_monthly_max_cents); else need(row.proposed_monthly_max_cents === null && !generated.has(row.goal_id)); ranges.add(row.goal_id); }
    need(ranges.size === body.adjustments.length || ranges.size === 0 && generated.size === 0 && ['UNKNOWN', 'BASE_INFEASIBLE'].includes(String(v.state)));
    repair(p.repair, v.user_id as string, current.included_goal_ids, p.search_input_hash); need(object(p.repair) && v.state === p.repair.status && Array.isArray(p.repair.candidates) && p.repair.candidates.every((row) => object(row) && same(row, generated.get(String(row.goal_id)))) && v.version_previews.length === p.repair.candidates.length);
    const selected = new Map(p.repair.candidates.map((row: Record<string, unknown>) => [String(row.goal_id), row])); const seen = new Set<string>();
    for (const row of v.version_previews) {
      need(object(row) && exact(row, ['goal_id', 'current_version_id', 'original_full_configuration', 'original_full_configuration_hash', 'original_monthly_max_cents', 'proposed_monthly_max_cents', 'preview_endpoint', 'preview_request', 'actual_existing_preview', 'confirmation_endpoint', 'confirmation_bindings', 'missing_explicit_user_fields', 'ready_to_submit_confirmation', 'current_execution_permission_changed']) && uuid(row.goal_id) && !seen.has(row.goal_id) && selected.has(row.goal_id) && row.current_version_id === selected.get(row.goal_id)?.source_policy_version_id && hash(row.original_full_configuration_hash) && cents(row.original_monthly_max_cents) && row.proposed_monthly_max_cents === selected.get(row.goal_id)?.monthly_max_cents && row.ready_to_submit_confirmation === false && row.current_execution_permission_changed === false);
      const before = parseFullGoalConfiguration(row.original_full_configuration); need(before.monthly_contribution.max_cents === row.original_monthly_max_cents && object(row.actual_existing_preview) && object(row.actual_existing_preview.base_policy_impact) && uuid(row.actual_existing_preview.base_policy_impact.policy_id));
      const binding = { id: row.goal_id, policy_id: row.actual_existing_preview.base_policy_impact.policy_id, policy_version_id: String(row.current_version_id) }; const actual = parseFullGoalPreview(row.actual_existing_preview, binding);
      const after = parseFullGoalConfiguration(actual.full_configuration); need(actual.epoch_id === body.expected_epoch_id && actual.base_policy_impact.user_id === original.user_id && same(after, { ...before, monthly_contribution: { ...before.monthly_contribution, max_cents: row.proposed_monthly_max_cents } }));
      need(row.preview_endpoint === `/api/v1/goals/${row.goal_id}/full-model/preview` && same(row.preview_request, { expected_version_id: row.current_version_id, configuration: actual.full_configuration }) && row.confirmation_endpoint === `/api/v1/goals/${row.goal_id}/full-model/confirm` && same(row.confirmation_bindings, { expected_version_id: row.current_version_id, expected_epoch_id: body.expected_epoch_id, configuration: actual.full_configuration, reviewed_full_hash: actual.full_configuration_hash, reviewed_base_hash: actual.base_configuration_hash }) && same(row.missing_explicit_user_fields, ['accepted', 'reason', 'idempotency_key'])); seen.add(row.goal_id);
    }
  }
  const result = v as FullGoalRepairs; if (raw !== undefined) originals.set(result, raw); return result;
}
export function getFullGoalConflicts(): Promise<FullGoalConflicts> { return request('/planning/full-goal-conflicts', 'GET', undefined, parseFullGoalConflicts); }
export function previewFullGoalRepairs(original: FullGoalConflicts, value: unknown): Promise<FullGoalRepairs> { const body = parseGoalRepairSelection(value, original); return request('/planning/full-goal-repairs/preview', 'POST', body, (v, raw) => parseFullGoalRepairs(v, original, body, raw)); }
