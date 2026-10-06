import type { components } from '../../../../packages/contracts/schema';
import type { Goal } from './goals';
import type { FullGoalModel, FullGoalPreview } from './full-goals';
import { parseFullGoalConfiguration, parseFullGoalModel, parseFullGoalPreview } from './full-goals';
import { parseFullGoalConflicts, validateGoalConflictGoals, type FullGoalConflicts } from './full-goal-conflicts';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { request } from './http';

// Exact new server DTO. Root will regenerate aliases after installing the router.
type Allocation = components['schemas']['MultiGoalAllocationResult'];
type Repair = components['schemas']['MinimalGoalRepair'];
type Candidate = components['schemas']['GoalRepairCandidate'];
export type GoalAdjustmentRange = { field: 'monthly_min_cents'; goal_id: string; expected_version_id: string; lower_cents: number; upper_cents: number } | { field: 'deadline'; goal_id: string; expected_version_id: string; lower_date: string; upper_date: string };
export interface GoalAdjustmentSelection { expected_epoch_id: string; reviewed_state_hash: string; adjustments: GoalAdjustmentRange[] }
export interface AdjustableGoal { goal_id: string; policy_id: string; current_version_id: string; monthly_min_cents: number; monthly_target_cents: number; monthly_max_cents: number; minimum_guarantee_cents: number; deadline: string; allow_partial: boolean; allow_deferral: boolean; current_owned_cents: number; current_month_contributed_cents: number; valid_until_exclusive: string | null; original_model: FullGoalModel }
export interface GoalAdjustmentRead { protocol: 'full-goal-adjustment-read-v1'; simulation: true; user_id: string; as_of: string; state: 'COMPUTED' | 'UNKNOWN'; original_conflicts: FullGoalConflicts; goals: AdjustableGoal[]; reasons: string[]; planning_only: true; grants_authority: false; writes_performed: false }
export interface GoalAdjustmentVersion { goal_id: string; current_version_id: string; field: 'monthly_min_cents' | 'deadline'; scope: 'SOFT_PREFERENCE_ONLY' | 'CURRENT_PERIOD_HARD_DEADLINE_REPAIR'; original_value: number | string; proposed_value: number | string; original_full_configuration: Record<string, unknown>; original_full_configuration_hash: string; actual_existing_preview: FullGoalPreview; preview_endpoint: string; preview_request: { expected_version_id: string; configuration: Record<string, unknown> }; confirmation_endpoint: string; confirmation_bindings: { expected_version_id: string; expected_epoch_id: string; configuration: Record<string, unknown>; reviewed_full_hash: string; reviewed_base_hash: string }; missing_explicit_user_fields: ['accepted', 'reason', 'idempotency_key']; ready_to_submit_confirmation: false; current_execution_permission_changed: false }
interface Outcome { goal_id: string; current_version_id: string; field: 'monthly_min_cents' | 'deadline'; original_value: number | string; proposed_value: number | string | null; reason: string; candidate: Candidate | null; hypothetical_allocation: Allocation | null; monthly_min_is_soft: true; existing_financial_permission: false }
type State = 'PROPOSAL' | 'SOFT_PREFERENCE_PREVIEW' | 'NOT_NEEDED' | 'NO_PERMITTED_REPAIR' | 'BASE_INFEASIBLE' | 'UNKNOWN';
export interface GoalAdjustmentPlan { protocol: 'full-goal-conditional-adjustments-v1'; original_input_hash: string; selection_hash: string; actual_selection: GoalAdjustmentSelection; original_baseline_repair: Repair; original_allocation: Allocation; hard_repair: Repair; outcomes: Outcome[]; soft_preference_candidates: Candidate[]; evaluated_subset_count: number; subset_limit: 256; state: State; minimality_scope: 'CURRENT_PERIOD_SELECTED_HARD_DEADLINE_CRITICAL_SUBSETS'; selection_source: 'USER_CURRENT_READONLY_PREVIEW_REQUEST'; identity_refs_are_financial_permissions: false; original_hard_protection_unchanged: true; minimum_guarantees_unchanged: true; original_ownership_and_income_unchanged: true; original_allow_deferral_unchanged: true; future_income_used_cents: 0; grants_authority: false; requires_new_version_confirmation: true }
export interface GoalAdjustmentPreview { protocol: 'full-goal-adjustment-preview-v1'; simulation: true; user_id: string; as_of: string; original: GoalAdjustmentRead; proposal: GoalAdjustmentPlan | null; version_previews: GoalAdjustmentVersion[]; state: State; reasons: string[]; planning_only: true; grants_authority: false; writes_performed: false; confirmation_is_separate: true; multi_version_atomic_confirmation_supported: false; limitations: string[] }
const originals = new WeakMap<GoalAdjustmentRead | GoalAdjustmentPreview, string>();
export const getOriginalGoalAdjustment = (value: GoalAdjustmentRead | GoalAdjustmentPreview) => originals.get(value) ?? null;
const states = ['PROPOSAL', 'SOFT_PREFERENCE_PREVIEW', 'NOT_NEEDED', 'NO_PERMITTED_REPAIR', 'BASE_INFEASIBLE', 'UNKNOWN'];
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(v);
const hash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const cents = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0;
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((row) => typeof row === 'string');
const date = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d\d-\d\d$/.test(v) && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v;
const stamp = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const exact = (v: Record<string, unknown>, keys: readonly string[]) => Object.keys(v).sort().join('|') === [...keys].sort().join('|');
function need(v: unknown): asserts v { if (!v) throw new Error('目标参数调整未通过原用户、版本、分母、条件规划或双hash绑定校验'); }
function same(left: unknown, right: unknown): boolean { if (left === right) return true; if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((v, i) => same(v, right[i])); if (object(left) && object(right)) return exact(left, Object.keys(right)) && Object.keys(left).every((key) => same(left[key], right[key])); return false; }
const binding = (goal: AdjustableGoal) => ({ id: goal.goal_id, policy_id: goal.policy_id, policy_version_id: goal.current_version_id });

export function parseGoalAdjustmentRead(value: unknown, owner: string, goals?: readonly Goal[], raw?: string): GoalAdjustmentRead {
  need(uuid(owner) && object(value) && exact(value, ['protocol', 'simulation', 'user_id', 'as_of', 'state', 'original_conflicts', 'goals', 'reasons', 'planning_only', 'grants_authority', 'writes_performed']));
  need(value.protocol === 'full-goal-adjustment-read-v1' && value.simulation === true && value.user_id === owner && stamp(value.as_of) && ['COMPUTED', 'UNKNOWN'].includes(String(value.state)) && strings(value.reasons) && value.planning_only === true && value.grants_authority === false && value.writes_performed === false && Array.isArray(value.goals));
  const original = parseFullGoalConflicts(value.original_conflicts); validateGoalConflictGoals(original, goals);
  need(original.user_id === owner && original.as_of === value.as_of && original.state === value.state);
  const seen = new Set<string>();
  for (const v of value.goals) {
    need(object(v) && exact(v, ['goal_id', 'policy_id', 'current_version_id', 'monthly_min_cents', 'monthly_target_cents', 'monthly_max_cents', 'minimum_guarantee_cents', 'deadline', 'allow_partial', 'allow_deferral', 'current_owned_cents', 'current_month_contributed_cents', 'valid_until_exclusive', 'original_model']) && uuid(v.goal_id) && uuid(v.policy_id) && uuid(v.current_version_id) && !seen.has(v.goal_id) && original.included_goal_ids.includes(v.goal_id));
    need(['monthly_min_cents', 'monthly_target_cents', 'monthly_max_cents', 'minimum_guarantee_cents', 'current_owned_cents', 'current_month_contributed_cents'].every((key) => cents(v[key])) && date(v.deadline) && typeof v.allow_partial === 'boolean' && typeof v.allow_deferral === 'boolean' && (v.valid_until_exclusive === null || stamp(v.valid_until_exclusive)));
    const row = v as unknown as AdjustableGoal; const model = parseFullGoalModel(v.original_model, binding(row)); const config = parseFullGoalConfiguration(model.full_configuration);
    need(model.status === 'VERIFIED' && model.epoch_id === original.epoch_id && original.source_evidence_ids.includes(model.evidence_id!) && config.deadline === row.deadline && config.monthly_contribution.min_cents === row.monthly_min_cents && config.monthly_contribution.target_cents === row.monthly_target_cents && config.monthly_contribution.max_cents === row.monthly_max_cents && config.minimum_guarantee_cents === row.minimum_guarantee_cents && config.allow_partial === row.allow_partial && config.allow_deferral === row.allow_deferral);
    if (goals) need(goals.some((g) => g.id === row.goal_id && g.policy_id === row.policy_id && g.policy_version_id === row.current_version_id));
    seen.add(row.goal_id);
  }
  need(value.state === 'COMPUTED' ? seen.size === original.registered_goal_count : seen.size === 0 && value.reasons.length > 0);
  // The original conflict parser verifies the witness_amounts_cents map by
  // entry. A generic suffix check would mistake that original map for one cent.
  const result = value as unknown as GoalAdjustmentRead; if (raw !== undefined) originals.set(result, raw); return result;
}

export function parseGoalAdjustmentSelection(value: unknown, original: GoalAdjustmentRead): GoalAdjustmentSelection {
  need(object(value) && exact(value, ['expected_epoch_id', 'reviewed_state_hash', 'adjustments']) && original.state === 'COMPUTED' && value.expected_epoch_id === original.original_conflicts.epoch_id && value.reviewed_state_hash === original.original_conflicts.review_state_hash && Array.isArray(value.adjustments) && value.adjustments.length > 0 && value.adjustments.length <= 8);
  const seen = new Set<string>();
  for (const v of value.adjustments) {
    need(object(v) && uuid(v.goal_id) && !seen.has(v.goal_id)); const row = original.goals.find((goal) => goal.goal_id === v.goal_id);
    need(row && v.expected_version_id === row.current_version_id);
    if (v.field === 'monthly_min_cents') need(exact(v, ['field', 'goal_id', 'expected_version_id', 'lower_cents', 'upper_cents']) && cents(v.lower_cents) && cents(v.upper_cents) && v.lower_cents <= v.upper_cents && v.upper_cents <= row.monthly_min_cents);
    else need(v.field === 'deadline' && exact(v, ['field', 'goal_id', 'expected_version_id', 'lower_date', 'upper_date']) && date(v.lower_date) && date(v.upper_date) && v.lower_date <= v.upper_date && v.upper_date !== '9999-12-31');
    seen.add(v.goal_id);
  }
  return value as unknown as GoalAdjustmentSelection;
}

function allocation(value: unknown, original: GoalAdjustmentRead): void {
  need(object(value) && value.algorithm_version === 'critical-flow-lexicographic-v1' && ['OPTIMAL', 'INFEASIBLE', 'UNKNOWN'].includes(String(value.status)) && value.purpose === 'CURRENT_PERIOD_PLANNING_ONLY' && value.grants_authority === false && hash(value.input_hash) && cents(value.budget_cents) && cents(value.visited_nodes) && strings(value.reasons) && value.delay_scope === 'ACTIVE_INCOMPLETE_GOALS_CURRENT_DECISION_LOWER_BOUND' && Array.isArray(value.goals) && value.goals.length === original.goals.length && Array.isArray(value.income_uses));
  need(value.status === 'OPTIMAL' ? Array.isArray(value.objective_vector) && value.objective_vector.length === 8 && value.objective_vector.every(Number.isSafeInteger) : value.objective_vector === null);
  const amounts = new Map<string, number>();
  for (const v of value.goals) { need(object(v) && uuid(v.goal_id) && !amounts.has(v.goal_id) && original.goals.some((g) => g.goal_id === v.goal_id && g.current_version_id === v.effective_policy_version_id)); need(['amount_cents', 'minimum_shortfall_cents', 'projected_owned_cents', 'delay_lower_bound_days', 'deferral_cost_lower_bound_cents'].every((key) => value.status === 'OPTIMAL' ? cents(v[key]) : v[key] === null)); need(v.completion_date === null || date(v.completion_date)); need(typeof v.delay_censored === 'boolean'); amounts.set(v.goal_id, value.status === 'OPTIMAL' ? Number(v.amount_cents) : 0); }
  const used = new Map<string, bigint>();
  for (const v of value.income_uses) { need(value.status === 'OPTIMAL' && object(v) && ['fragment_id', 'origin_transaction_id', 'source_account_id', 'goal_id'].every((key) => uuid(v[key])) && amounts.has(String(v.goal_id)) && cents(v.amount_cents) && v.amount_cents > 0); used.set(String(v.goal_id), (used.get(String(v.goal_id)) ?? 0n) + BigInt(v.amount_cents)); }
  for (const [id, amount] of amounts) need((used.get(id) ?? 0n) === BigInt(amount));
  need([...used.values()].reduce((n, v) => n + v, 0n) <= BigInt(value.budget_cents));
}
function candidate(value: unknown, original: GoalAdjustmentRead): Candidate {
  need(object(value) && uuid(value.candidate_id) && uuid(value.goal_id)); const row = original.goals.find((goal) => goal.goal_id === value.goal_id);
  need(row && value.source_policy_version_id === row.current_version_id && value.monthly_max_cents === null && object(value.permission_ref) && value.permission_ref.user_id === original.user_id && value.permission_ref.evidence_id === row.original_model.evidence_id && value.permission_ref.content_hash === row.original_model.evidence_hash);
  need(cents(value.monthly_min_cents) ? value.deadline === null && value.monthly_min_cents < row.monthly_min_cents : value.monthly_min_cents === null && date(value.deadline) && value.deadline > row.deadline && !row.allow_partial && !row.allow_deferral);
  return value as unknown as Candidate;
}
function repair(value: unknown, original: GoalAdjustmentRead): Repair {
  need(object(value) && states.includes(String(value.status)) && value.status !== 'SOFT_PREFERENCE_PREVIEW' && value.original_input_hash === original.original_conflicts.current_input_hash && value.grants_authority === false && value.requires_new_version_confirmation === true && Array.isArray(value.candidates) && Array.isArray(value.unaffected_goal_ids) && strings(value.reasons));
  const changed = new Set<string>(); for (const v of value.candidates) { const c = candidate(v, original); need(c.deadline !== null && !changed.has(c.goal_id)); changed.add(c.goal_id); }
  need(new Set(value.unaffected_goal_ids).size === value.unaffected_goal_ids.length && value.unaffected_goal_ids.every((v) => original.goals.some((goal) => goal.goal_id === v) && !changed.has(String(v))) && changed.size + value.unaffected_goal_ids.length === original.goals.length);
  if (value.status === 'PROPOSAL' || value.status === 'NOT_NEEDED') { need(value.changed_policy_count === changed.size && cents(value.parameter_deviation_numerator) && cents(value.parameter_deviation_denominator) && value.parameter_deviation_denominator > 0 && value.priority_loss_cents === 0); if (value.status === 'PROPOSAL') { need(changed.size > 0); allocation(value.hypothetical_allocation, original); need(object(value.hypothetical_allocation) && value.hypothetical_allocation.status === 'OPTIMAL'); } else need(changed.size === 0 && value.hypothetical_allocation === null && value.parameter_deviation_numerator === 0); }
  else need(changed.size === 0 && ['changed_policy_count', 'parameter_deviation_numerator', 'parameter_deviation_denominator', 'priority_loss_cents', 'hypothetical_allocation'].every((key) => value[key] === null));
  return value as unknown as Repair;
}

export function parseGoalAdjustmentPreview(value: unknown, expected: GoalAdjustmentSelection, owner: string, goals?: readonly Goal[], raw?: string): GoalAdjustmentPreview {
  need(object(value) && value.protocol === 'full-goal-adjustment-preview-v1' && value.simulation === true && value.user_id === owner && stamp(value.as_of) && states.includes(String(value.state)) && value.planning_only === true && value.grants_authority === false && value.writes_performed === false && value.confirmation_is_separate === true && value.multi_version_atomic_confirmation_supported === false && strings(value.reasons) && strings(value.limitations) && Array.isArray(value.version_previews));
  const original = parseGoalAdjustmentRead(value.original, owner, goals); need(value.as_of === original.as_of && expected.expected_epoch_id === original.original_conflicts.epoch_id);
  const plan = value.proposal; const selected = new Map<string, Candidate>();
  if (plan === null) need(value.state === 'UNKNOWN' && original.state === 'UNKNOWN' && value.version_previews.length === 0);
  else {
    need(object(plan) && original.state === 'COMPUTED' && plan.protocol === 'full-goal-conditional-adjustments-v1' && plan.state === value.state && plan.original_input_hash === original.original_conflicts.current_input_hash && hash(plan.selection_hash) && same(plan.actual_selection, expected)); parseGoalAdjustmentSelection(plan.actual_selection, original);
    need(plan.subset_limit === 256 && cents(plan.evaluated_subset_count) && plan.evaluated_subset_count <= 255 && plan.minimality_scope === 'CURRENT_PERIOD_SELECTED_HARD_DEADLINE_CRITICAL_SUBSETS' && plan.selection_source === 'USER_CURRENT_READONLY_PREVIEW_REQUEST' && plan.identity_refs_are_financial_permissions === false && plan.original_hard_protection_unchanged === true && plan.minimum_guarantees_unchanged === true && plan.original_ownership_and_income_unchanged === true && plan.original_allow_deferral_unchanged === true && plan.future_income_used_cents === 0 && plan.grants_authority === false && plan.requires_new_version_confirmation === true && Array.isArray(plan.outcomes) && Array.isArray(plan.soft_preference_candidates));
    allocation(plan.original_allocation, original); const baseline = repair(plan.original_baseline_repair, original); const hard = repair(plan.hard_repair, original); need(baseline.status !== 'PROPOSAL'); need(hard.status !== 'NOT_NEEDED' || (object(plan.original_allocation) && plan.original_allocation.status === 'OPTIMAL'));
    need(value.state === 'SOFT_PREFERENCE_PREVIEW' ? hard.status === 'NOT_NEEDED' && plan.soft_preference_candidates.length > 0 : hard.status === value.state);
    for (const c of hard.candidates) selected.set(c.goal_id, c);
    for (const v of plan.soft_preference_candidates) { const c = candidate(v, original); need(c.monthly_min_cents !== null && !selected.has(c.goal_id)); selected.set(c.goal_id, c); }
    const outcomes = new Set<string>();
    for (const v of plan.outcomes) { need(object(v) && uuid(v.goal_id) && !outcomes.has(v.goal_id) && v.monthly_min_is_soft === true && v.existing_financial_permission === false && typeof v.reason === 'string'); const g = original.goals.find((row) => row.goal_id === v.goal_id); const option = expected.adjustments.find((row) => row.goal_id === v.goal_id); need(g && option && v.current_version_id === g.current_version_id && v.field === option.field && v.original_value === (v.field === 'deadline' ? g.deadline : g.monthly_min_cents)); if (v.candidate !== null) { const c = candidate(v.candidate, original); need(v.proposed_value === (c.deadline ?? c.monthly_min_cents)); } else need(v.proposed_value === null); if (v.hypothetical_allocation !== null) allocation(v.hypothetical_allocation, original); outcomes.add(v.goal_id); }
    need(plan.outcomes.length === 0 ? ['UNKNOWN', 'BASE_INFEASIBLE'].includes(String(value.state)) : outcomes.size === expected.adjustments.length);
  }
  const seen = new Set<string>();
  for (const v of value.version_previews) {
    need(object(v) && uuid(v.goal_id) && !seen.has(v.goal_id)); const g = original.goals.find((row) => row.goal_id === v.goal_id); const c = selected.get(v.goal_id); need(g && c && v.current_version_id === g.current_version_id && v.ready_to_submit_confirmation === false && v.current_execution_permission_changed === false && same(v.missing_explicit_user_fields, ['accepted', 'reason', 'idempotency_key']) && same(v.original_full_configuration, g.original_model.full_configuration) && v.original_full_configuration_hash === g.original_model.full_configuration_hash);
    const preview = parseFullGoalPreview(v.actual_existing_preview, binding(g)); need(preview.epoch_id === expected.expected_epoch_id && preview.base_policy_impact.user_id === owner && preview.base_policy_impact.as_of === value.as_of);
    const config = structuredClone(g.original_model.full_configuration!);
    if (v.field === 'monthly_min_cents') { need(c.monthly_min_cents !== null && v.scope === 'SOFT_PREFERENCE_ONLY' && v.original_value === g.monthly_min_cents && v.proposed_value === c.monthly_min_cents); need(object(config.monthly_contribution)); config.monthly_contribution.min_cents = c.monthly_min_cents; }
    else { need(v.field === 'deadline' && c.deadline !== null && v.scope === 'CURRENT_PERIOD_HARD_DEADLINE_REPAIR' && v.original_value === g.deadline && v.proposed_value === c.deadline); config.deadline = c.deadline; }
    need(same(config, preview.full_configuration) && v.preview_endpoint === `/api/v1/goals/${g.goal_id}/full-model/preview` && v.confirmation_endpoint === `/api/v1/goals/${g.goal_id}/full-model/confirm` && same(v.preview_request, { expected_version_id: g.current_version_id, configuration: config }) && same(v.confirmation_bindings, { expected_version_id: g.current_version_id, expected_epoch_id: expected.expected_epoch_id, configuration: config, reviewed_full_hash: preview.full_configuration_hash, reviewed_base_hash: preview.base_configuration_hash }));
    seen.add(v.goal_id);
  }
  need(['UNKNOWN', 'BASE_INFEASIBLE'].includes(String(value.state)) ? seen.size === 0 : seen.size === selected.size);
  // Full original conflicts retain their independently parsed cent maps.
  // New solver/selection scalars and original preview fields remain exact.
  assertMoneyFields(plan); const result = value as unknown as GoalAdjustmentPreview; if (raw !== undefined) originals.set(result, raw); return result;
}

export function getGoalAdjustmentOriginals(owner: string, goals?: readonly Goal[]): Promise<GoalAdjustmentRead> { need(uuid(owner)); return request('/planning/full-goal-adjustments', 'GET', undefined, (v, raw) => parseGoalAdjustmentRead(v, owner, goals, raw)); }
export function previewGoalAdjustments(selection: GoalAdjustmentSelection, original: GoalAdjustmentRead, goals?: readonly Goal[]): Promise<GoalAdjustmentPreview> { const exactSelection = parseGoalAdjustmentSelection(selection, original); return request('/planning/full-goal-adjustments/preview', 'POST', exactSelection, (v, raw) => parseGoalAdjustmentPreview(v, exactSelection, original.user_id, goals, raw)); }
