import type { components } from '../../../../packages/contracts/schema';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';
import { request } from './http';

export type JointPlanning = components['schemas']['JointPlanningResponse'];
export type JointAllocation = components['schemas']['MultiGoalAllocationResult'];
export type JointGoal = components['schemas']['GoalAllocation'];
const originalResponses = new WeakMap<JointPlanning, string>();
export const getOriginalJointPlanning = (value: JointPlanning): string | null => originalResponses.get(value) ?? null;
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const text = (value: unknown): value is string => typeof value === 'string';
const digest = (value: unknown) => text(value) && /^[0-9a-f]{64}$/.test(value);
const date = (value: unknown) => text(value) && /^\d{4}-\d\d-\d\d$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const timestamp = (value: unknown) => text(value) && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const cents = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;
const nullable = (value: unknown, check: (value: unknown) => boolean) => value === null || check(value);
const uniqueIds = (value: unknown): value is string[] => Array.isArray(value) && value.every(uuid) && new Set(value.map((id) => id.toLowerCase())).size === value.length;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text);
function requireValue(condition: unknown): asserts condition { if (!condition) throw new Error('联合目标规划未通过原分母、只读范围或精确金额校验'); }
function safeSum(values: number[]): bigint { return values.reduce((sum, value) => sum + BigInt(value), 0n); }

export function parseJointPlanning(value: unknown, originalText?: string): JointPlanning {
  requireValue(object(value) && value.schema_version === 'verified-joint-goal-planning-v1' && value.simulation === true && value.grants_authority === false);
  requireValue(uuid(value.user_id) && timestamp(value.as_of) && digest(value.input_hash) && value.funds_scope === 'ACTUAL_CURRENT_UNASSIGNED_INCOME_ONLY' && value.protection_scope === 'ALL_ORIGINAL_365_DAY_RESERVES_RETAINED' && value.full_model_rules_have_dedicated_audit_event === false && typeof value.independent_bank_projection_matched === 'boolean');
  requireValue(Number.isSafeInteger(value.registered_goal_count) && (value.registered_goal_count as number) >= 0 && uniqueIds(value.included_goal_ids) && uniqueIds(value.uncovered_goal_ids));
  const included = value.included_goal_ids; const uncovered = value.uncovered_goal_ids;
  requireValue(new Set([...included, ...uncovered].map((id) => id.toLowerCase())).size === value.registered_goal_count);
  requireValue(uniqueIds(value.source_evidence_ids) && strings(value.limitations) && Array.isArray(value.source_issues) && value.source_issues.every((issue) => object(issue) && text(issue.code) && text(issue.source_ref) && text(issue.message)));
  requireValue(['COMPUTED', 'UNKNOWN'].includes(value.state as string));
  const allocation = value.allocation;
  if (allocation === null) requireValue(value.state === 'UNKNOWN' && value.conflict === null);
  else {
    requireValue(object(allocation) && allocation.algorithm_version === 'critical-flow-lexicographic-v1' && allocation.purpose === 'CURRENT_PERIOD_PLANNING_ONLY' && allocation.grants_authority === false && allocation.delay_scope === 'ACTIVE_INCOMPLETE_GOALS_CURRENT_DECISION_LOWER_BOUND' && digest(allocation.input_hash));
    requireValue(['OPTIMAL', 'INFEASIBLE', 'UNKNOWN'].includes(allocation.status as string) && cents(allocation.budget_cents) && Number.isSafeInteger(allocation.visited_nodes) && (allocation.visited_nodes as number) >= 0 && strings(allocation.reasons) && Array.isArray(allocation.goals) && allocation.goals.length <= 8 && Array.isArray(allocation.income_uses));
    const optimal = allocation.status === 'OPTIMAL'; requireValue(value.state === (allocation.status === 'UNKNOWN' ? 'UNKNOWN' : 'COMPUTED'));
    requireValue(optimal ? Array.isArray(allocation.objective_vector) && allocation.objective_vector.length === 8 && allocation.objective_vector.every(cents) : allocation.objective_vector === null);
    const ids = new Set<string>();
    for (const goal of allocation.goals) {
      requireValue(object(goal) && uuid(goal.goal_id) && uuid(goal.effective_policy_version_id) && typeof goal.delay_censored === 'boolean'); requireValue(!ids.has(goal.goal_id)); ids.add(goal.goal_id);
      for (const key of ['amount_cents', 'minimum_shortfall_cents', 'projected_owned_cents', 'delay_lower_bound_days', 'deferral_cost_lower_bound_cents']) requireValue(optimal ? cents(goal[key]) : goal[key] === null);
      requireValue(nullable(goal.completion_date, date));
      if (!optimal) requireValue(goal.completion_date === null && goal.delay_censored === true);
      if (goal.delay_censored) requireValue(goal.completion_date === null);
      if (optimal) requireValue((goal.projected_owned_cents as number) >= (goal.amount_cents as number));
    }
    requireValue(ids.size === included.length && included.every((id) => ids.has(id)));
    if (optimal) requireValue(uncovered.length === 0 && value.source_issues.length === 0 && value.independent_bank_projection_matched === true);
    const uses: { goal: string; amount: number }[] = [];
    for (const use of allocation.income_uses) {
      requireValue(object(use) && uuid(use.fragment_id) && uuid(use.origin_transaction_id) && uuid(use.source_account_id) && uuid(use.goal_id) && ids.has(use.goal_id) && cents(use.amount_cents) && (use.amount_cents as number) > 0);
      uses.push({ goal: use.goal_id, amount: use.amount_cents as number });
    }
    requireValue(optimal || uses.length === 0);
    if (optimal) {
      requireValue(safeSum(uses.map((use) => use.amount)) <= BigInt(allocation.budget_cents as number));
      for (const goal of allocation.goals) requireValue(safeSum(uses.filter((use) => use.goal === goal.goal_id).map((use) => use.amount)) === BigInt(goal.amount_cents));
    }
  }
  const conflict = value.conflict;
  if (conflict !== null) {
    requireValue(object(conflict) && object(allocation) && allocation.status === 'INFEASIBLE' && conflict.input_hash === allocation.input_hash && conflict.scope === 'GOAL_POLICIES_WITH_IMMUTABLE_FINANCIAL_BASE' && conflict.grants_authority === false);
    requireValue(['MINIMAL_CONFLICT', 'NO_CONFLICT', 'BASE_INFEASIBLE', 'UNKNOWN'].includes(conflict.status as string) && strings(conflict.constraint_ids) && new Set(conflict.constraint_ids).size === conflict.constraint_ids.length && Array.isArray(conflict.deletion_checks) && strings(conflict.reasons));
    const removed = new Set<string>();
    for (const check of conflict.deletion_checks) {
      requireValue(object(check) && text(check.removed_constraint_id) && conflict.constraint_ids.includes(check.removed_constraint_id) && !removed.has(check.removed_constraint_id) && check.remaining_feasible === true && check.counterfactual_only === true && object(check.witness_amounts_cents)); removed.add(check.removed_constraint_id);
      requireValue(Object.keys(check.witness_amounts_cents).length === included.length && Object.entries(check.witness_amounts_cents).every(([id, amount]) => idsIn(included, id) && cents(amount)));
    }
    if (conflict.status === 'MINIMAL_CONFLICT') requireValue(removed.size === conflict.constraint_ids.length && removed.size > 0);
  }
  // Conflict witness_amounts_cents is a UUID-keyed dictionary, independently checked above.
  // The common scalar-money validator must not mistake that complete dictionary for a number.
  assertMoneyFields(allocation); const result = value as JointPlanning;
  if (originalText !== undefined) originalResponses.set(result, originalText); return result;
}
function idsIn(ids: string[], id: string): boolean { return ids.includes(id); }
export function getCurrentGoalAllocation(): Promise<JointPlanning> {
  return request<JointPlanning>('/planning/current-goal-allocation', 'GET', undefined, (value, original) => parseJointPlanning(value, original));
}
