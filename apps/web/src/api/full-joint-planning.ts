import type { Goal } from './goals';
import type { components } from '../../../../packages/contracts/schema';
import type { JointAllocation, JointPlanning } from './joint-planning';
import { parseJointPlanning } from './joint-planning';
import type { FullAnnualPlanning } from './full-annual';
import { parseFullAnnualPlanning } from './full-annual';
import { request } from './http';
import { object } from '../features/policy-form';
import { annualDayNumber } from './planning';

/** Explicit actual server DTO while the root regenerates shared contracts. */
export type FullJointSource = { user_id: string; evidence_id: string; content_hash: string };
export type FullJointPoint = { date: string; cash_cents: number; obligation_floor_cents: number; living_floor_cents: number; emergency_floor_cents: number; owned_goal_cash_cents: number; other_protection_floor_cents: number; source_refs: FullJointSource[] };
export type FullJointGoalInput = { goal_id: string; account_id: string; policy_id: string; effective_policy_version_id: string; policy_status: 'ACTIVE' | 'CONFIRMED' | 'SUSPENDED' | 'EXPIRED' | 'REVOKED'; target_cents: number; current_owned_cents: number; current_month_contributed_cents: number; monthly_min_cents: number; monthly_target_cents: number; monthly_max_cents: number; minimum_guarantee_cents: number; importance: number; deadline: string; allow_partial: boolean; allow_deferral: boolean; deferral_cost_cents_per_day: number; confirmed_at: string; valid_from: string; valid_until: string | null; negotiable_fields: ('deadline' | 'monthly_min_cents' | 'monthly_max_cents')[]; source_refs: FullJointSource[] };
export type FullJointLot = { fragment_id: string; origin_transaction_id: string; account_id: string; received_cents: number; available_cents: number; occurred_at: string; observed_at: string; owner_goal_id: null; bank_evidence_id: string; bank_evidence_hash: string; source_refs: FullJointSource[] };
export type FullJointInput = { schema_version: 'multi-goal-current-period-v1'; user_id: string; as_of: string; timezone: 'UTC' | 'Asia/Shanghai'; income_lots: FullJointLot[]; hard_protection_points: FullJointPoint[]; goals: FullJointGoalInput[]; source_issues: string[]; solver_node_budget: number };
export type FullJointBinding = { status: 'VERIFIED' | 'UNKNOWN'; original_point_count: number; full_point_count: number; bound_point_count: number; original_input_hash: string; full_projection_input_hash: string; verified_source_refs: FullJointSource[]; reasons: string[]; binding_hash: string; candidate: FullJointInput };
export type FullJointPlanning = components['schemas']['FullJointPlanningResponse'] & { schema_version: 'full-current-joint-goal-planning-v1'; user_id: string; as_of: string; simulation: true; planning_only: true; grants_authority: false; execution_support: 'NOT_IMPLEMENTED'; funds_scope: 'ACTUAL_CURRENT_UNASSIGNED_INCOME_ONLY'; planning_scope: 'CURRENT_PERIOD_WITH_ALL_365_DAY_ORIGINAL_AND_FULL_PROTECTION'; original_joint: JointPlanning; full_protection: FullAnnualPlanning; binding: FullJointBinding | null; allocation: JointAllocation | null; conflict: JointPlanning['conflict']; state: 'COMPUTED' | 'UNKNOWN'; reasons: string[]; input_hash: string; limitations: string[] };

const originals = new WeakMap<FullJointPlanning, string>();
export const getOriginalFullJointPlanning = (value: FullJointPlanning) => originals.get(value) ?? null;
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value);
const digest = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value);
const cents = (value: unknown): value is number => integer(value) && value >= 0;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((child) => typeof child === 'string');
const timestamp = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const sameTime = (left: string, right: string) => Date.parse(left) === Date.parse(right);
const ids = (values: string[]) => new Set(values).size === values.length;
const exact = (value: Record<string, unknown>, fields: readonly string[]) => Object.keys(value).sort().join('|') === [...fields].sort().join('|');
function requireValue(value: unknown): asserts value { if (!value) throw new Error('完整联合规划的原用户、版本、保护时点、分母或金额未通过校验'); }
const sum = (values: number[]) => values.reduce((total, value) => total + BigInt(value), 0n);
const originalNames = ['obligations', 'living', 'emergency', 'goal_cash', 'goal_minimum'] as const;
const pointFields = ['obligation_floor_cents', 'living_floor_cents', 'emergency_floor_cents', 'owned_goal_cash_cents', 'other_protection_floor_cents'] as const;
const extraNames = ['full_dated_expense', 'full_periodic_transfer', 'pending_cash_reservations'] as const;
const phases = ['BEFORE_PAYMENT', 'AFTER_PAYMENT', 'AFTER_PRINCIPAL'] as const;

function parseSources(value: unknown, owner: string): asserts value is FullJointSource[] {
  requireValue(Array.isArray(value) && value.length <= 1000 && value.every((row) => object(row) && exact(row, ['user_id', 'evidence_id', 'content_hash']) && row.user_id === owner && uuid(row.evidence_id) && digest(row.content_hash)));
  requireValue(ids(value.map((row) => row.evidence_id)));
}

function parseInput(value: unknown, owner: string, asOf: string): asserts value is FullJointInput {
  requireValue(object(value) && exact(value, ['schema_version', 'user_id', 'as_of', 'timezone', 'income_lots', 'hard_protection_points', 'goals', 'source_issues', 'solver_node_budget']) && value.schema_version === 'multi-goal-current-period-v1' && value.user_id === owner && timestamp(value.as_of) && sameTime(value.as_of, asOf) && ['UTC', 'Asia/Shanghai'].includes(String(value.timezone)) && strings(value.source_issues) && value.source_issues.length <= 1000 && cents(value.solver_node_budget) && value.solver_node_budget > 0 && value.solver_node_budget <= 1000000 && Array.isArray(value.goals) && value.goals.length <= 8 && Array.isArray(value.income_lots) && value.income_lots.length <= 128 && Array.isArray(value.hard_protection_points) && value.hard_protection_points.length > 0 && value.hard_protection_points.length <= 1098);
  for (const goal of value.goals) {
    requireValue(object(goal) && exact(goal, ['goal_id', 'account_id', 'policy_id', 'effective_policy_version_id', 'policy_status', 'target_cents', 'current_owned_cents', 'current_month_contributed_cents', 'monthly_min_cents', 'monthly_target_cents', 'monthly_max_cents', 'minimum_guarantee_cents', 'importance', 'deadline', 'allow_partial', 'allow_deferral', 'deferral_cost_cents_per_day', 'confirmed_at', 'valid_from', 'valid_until', 'negotiable_fields', 'source_refs']) && ['goal_id', 'account_id', 'policy_id', 'effective_policy_version_id'].every((key) => uuid(goal[key])) && ['ACTIVE', 'CONFIRMED', 'SUSPENDED', 'REVOKED', 'EXPIRED'].includes(String(goal.policy_status)) && ['target_cents', 'current_owned_cents', 'current_month_contributed_cents', 'monthly_min_cents', 'monthly_target_cents', 'monthly_max_cents', 'minimum_guarantee_cents', 'deferral_cost_cents_per_day', 'importance'].every((key) => cents(goal[key])));
    const row = goal as unknown as FullJointGoalInput;
    requireValue(row.target_cents > 0 && row.monthly_min_cents <= row.monthly_target_cents && row.monthly_target_cents <= row.monthly_max_cents && row.minimum_guarantee_cents <= row.target_cents && row.importance <= 100 && typeof row.allow_partial === 'boolean' && typeof row.allow_deferral === 'boolean' && (row.allow_deferral || row.deferral_cost_cents_per_day === 0) && timestamp(row.confirmed_at) && Date.parse(row.confirmed_at) <= Date.parse(asOf) && timestamp(row.valid_from) && (row.valid_until === null || timestamp(row.valid_until) && Date.parse(row.valid_until) >= Date.parse(row.valid_from)) && strings(row.negotiable_fields) && ids(row.negotiable_fields) && row.negotiable_fields.every((key) => ['monthly_min_cents', 'monthly_max_cents', 'deadline'].includes(key)));
    annualDayNumber(row.deadline); parseSources(row.source_refs, owner); requireValue(row.source_refs.length > 0);
  }
  requireValue(ids(value.goals.map((goal) => goal.goal_id)) && ids(value.goals.map((goal) => goal.policy_id)));
  for (const lot of value.income_lots) {
    requireValue(object(lot) && exact(lot, ['fragment_id', 'origin_transaction_id', 'account_id', 'received_cents', 'available_cents', 'occurred_at', 'observed_at', 'owner_goal_id', 'bank_evidence_id', 'bank_evidence_hash', 'source_refs']) && ['fragment_id', 'origin_transaction_id', 'account_id', 'bank_evidence_id'].every((key) => uuid(lot[key])) && lot.owner_goal_id === null && digest(lot.bank_evidence_hash) && cents(lot.received_cents) && cents(lot.available_cents) && lot.available_cents <= lot.received_cents && timestamp(lot.occurred_at) && timestamp(lot.observed_at) && Date.parse(lot.occurred_at) <= Date.parse(lot.observed_at) && Date.parse(lot.observed_at) <= Date.parse(asOf));
    parseSources(lot.source_refs, owner); requireValue(lot.source_refs.length > 0);
  }
  requireValue(ids(value.income_lots.map((lot) => lot.fragment_id)) && ids(value.income_lots.map((lot) => `${lot.origin_transaction_id}:${lot.account_id}`)));
  const origins = new Map<string, FullJointLot[]>();
  for (const lot of value.income_lots as FullJointLot[]) origins.set(lot.origin_transaction_id, [...(origins.get(lot.origin_transaction_id) ?? []), lot]);
  for (const lots of origins.values()) { const first = lots[0]!; requireValue(sum(lots.map((lot) => lot.available_cents)) <= BigInt(first.received_cents) && lots.every((lot) => lot.received_cents === first.received_cents && lot.bank_evidence_id === first.bank_evidence_id && lot.bank_evidence_hash === first.bank_evidence_hash && sameTime(lot.occurred_at, first.occurred_at) && sameTime(lot.observed_at, first.observed_at))); }
  for (const point of value.hard_protection_points) {
    requireValue(object(point) && exact(point, ['date', 'cash_cents', ...pointFields, 'source_refs']) && integer(point.cash_cents) && pointFields.every((key) => cents(point[key])));
    annualDayNumber(point.date); parseSources(point.source_refs, owner); requireValue(point.source_refs.length > 0);
  }
}

export function parseFullJointPlanning(value: unknown, originalText?: string): FullJointPlanning {
  requireValue(object(value) && exact(value, ['schema_version', 'user_id', 'as_of', 'simulation', 'planning_only', 'grants_authority', 'execution_support', 'funds_scope', 'planning_scope', 'original_joint', 'full_protection', 'binding', 'allocation', 'conflict', 'state', 'reasons', 'input_hash', 'limitations']) && value.schema_version === 'full-current-joint-goal-planning-v1' && uuid(value.user_id) && timestamp(value.as_of) && value.simulation === true && value.planning_only === true && value.grants_authority === false && value.execution_support === 'NOT_IMPLEMENTED' && value.funds_scope === 'ACTUAL_CURRENT_UNASSIGNED_INCOME_ONLY' && value.planning_scope === 'CURRENT_PERIOD_WITH_ALL_365_DAY_ORIGINAL_AND_FULL_PROTECTION' && ['COMPUTED', 'UNKNOWN'].includes(String(value.state)) && strings(value.reasons) && strings(value.limitations) && digest(value.input_hash));
  const old = parseJointPlanning(value.original_joint); const full = parseFullAnnualPlanning(value.full_protection);
  requireValue(old.user_id === value.user_id && full.user_id === value.user_id && sameTime(old.as_of, value.as_of) && sameTime(full.as_of, value.as_of));
  // Reuse the original actionless result reader with the same goal denominator.
  const newView = parseJointPlanning({ ...old, allocation: value.allocation, conflict: value.conflict, state: value.state });
  if (value.binding === null) requireValue(value.state === 'UNKNOWN' && value.allocation === null && value.conflict === null && value.reasons.length > 0);
  else {
    const binding = value.binding;
    requireValue(object(binding) && exact(binding, ['status', 'original_point_count', 'full_point_count', 'bound_point_count', 'original_input_hash', 'full_projection_input_hash', 'verified_source_refs', 'reasons', 'binding_hash', 'candidate']) && ['VERIFIED', 'UNKNOWN'].includes(String(binding.status)) && ['original_point_count', 'full_point_count', 'bound_point_count'].every((key) => cents(binding[key])) && Number(binding.bound_point_count) <= 1098 && strings(binding.reasons) && digest(binding.original_input_hash) && digest(binding.full_projection_input_hash) && digest(binding.binding_hash));
    parseInput(binding.candidate, value.user_id, value.as_of); parseSources(binding.verified_source_refs, value.user_id);
    const candidate = binding.candidate; const current = new Map(binding.verified_source_refs.map((row) => [row.evidence_id, row.content_hash]));
    requireValue(old.allocation !== null && newView.allocation !== null && binding.original_input_hash === old.allocation.input_hash && binding.full_projection_input_hash === full.projection.input_hash && candidate.goals.length === old.included_goal_ids.length && candidate.goals.every((goal) => old.included_goal_ids.includes(goal.goal_id) && old.allocation!.goals.some((row) => row.goal_id === goal.goal_id && row.effective_policy_version_id === goal.effective_policy_version_id)));
    requireValue(newView.allocation.goals.every((row) => candidate.goals.some((goal) => goal.goal_id === row.goal_id && goal.effective_policy_version_id === row.effective_policy_version_id)));
    if (old.allocation.status === 'OPTIMAL') requireValue(old.allocation.goals.every((row) => { const goal = candidate.goals.find((entry) => entry.goal_id === row.goal_id)!; return BigInt(row.projected_owned_cents!) === BigInt(goal.current_owned_cents)+BigInt(row.amount_cents!); }));
    if (binding.status === 'UNKNOWN') requireValue(value.state === 'UNKNOWN' && newView.allocation.status === 'UNKNOWN' && binding.reasons.length > 0 && candidate.source_issues.length > 0);
    else {
      requireValue(binding.reasons.length === 0 && candidate.source_issues.length === 0 && binding.original_point_count === 1098 && binding.full_point_count === 1098 && binding.bound_point_count === 1098 && old.source_issues.length === 0 && full.source_issues.length === 0 && old.independent_bank_projection_matched && full.audit.complete && full.audit.status === 'VALID' && full.projection.status !== 'UNKNOWN' && full.projection.full_obligations_complete_within_registered_current_scope && full.projection.source_account_checks.length === 0);
      requireValue([...old.source_evidence_ids, ...full.source_evidence_ids].every((id) => current.has(id)));
      for (const refs of [...candidate.goals.map((goal) => goal.source_refs), ...candidate.income_lots.map((lot) => lot.source_refs), ...candidate.hard_protection_points.map((point) => point.source_refs)]) requireValue(refs.every((row) => current.get(row.evidence_id) === row.content_hash));
      requireValue(candidate.income_lots.every((lot) => current.get(lot.bank_evidence_id) === lot.bank_evidence_hash));
    }
    const originalsTrace = full.projection.original_annual_projection.calculation_trace;
    const fullTrace = full.projection.full_annual_projection?.calculation_trace ?? [];
    requireValue(binding.original_point_count === candidate.hard_protection_points.length && binding.full_point_count === fullTrace.length && Number(binding.bound_point_count) <= Math.min(originalsTrace.length, fullTrace.length, candidate.hard_protection_points.length));
    const first = new Date(Date.parse(candidate.as_of) + (candidate.timezone === 'Asia/Shanghai' ? 8*3600000 : 0)).toISOString().slice(0, 10);
    requireValue(candidate.hard_protection_points.length === originalsTrace.length);
    for (const [index, point] of candidate.hard_protection_points.entries()) {
      const baseline = originalsTrace[index]!; const layered = fullTrace[index];
      requireValue(baseline.day === Math.floor(index/3) && baseline.phase === phases[index%3] && annualDayNumber(baseline.date) === annualDayNumber(first)+baseline.day && point.date === baseline.date && Object.keys(baseline.protected_cents_by_reason).every((key) => (originalNames as readonly string[]).includes(key)));
      const verified = binding.status === 'VERIFIED';
      requireValue(point.cash_cents === (verified ? layered!.cash_cents : baseline.cash_cents));
      for (const [i, key] of pointFields.entries()) requireValue(BigInt(point[key]) === BigInt(baseline.protected_cents_by_reason[originalNames[i]!] ?? 0) + (verified && i === 4 ? sum(extraNames.map((name) => layered!.protected_cents_by_reason[name] ?? 0)) : 0n));
      if (verified) {
        requireValue(layered && layered.day === baseline.day && layered.date === baseline.date && layered.phase === baseline.phase && originalNames.every((key) => layered.protected_cents_by_reason[key] === baseline.protected_cents_by_reason[key]) && Object.keys(layered.protected_cents_by_reason).every((key) => [...originalNames, ...extraNames].includes(key as typeof originalNames[number])));
        const paid = full.projection.occurrences.filter((entry) => entry.hypothetical_payment_date < point.date || entry.hypothetical_payment_date === point.date && index%3 > 0);
        const unpaid = full.projection.occurrences.filter((entry) => !paid.includes(entry));
        requireValue(BigInt(layered.cash_cents) === BigInt(baseline.cash_cents)-sum(paid.map((entry) => entry.conservative_unpaid_cents)));
        requireValue(BigInt(layered.protected_cents_by_reason.full_dated_expense ?? 0) === sum(unpaid.filter((entry) => entry.kind === 'DATED_EXPENSE').map((entry) => entry.conservative_unpaid_cents)) && BigInt(layered.protected_cents_by_reason.full_periodic_transfer ?? 0) === sum(unpaid.filter((entry) => entry.kind === 'PERIODIC_TRANSFER').map((entry) => entry.conservative_unpaid_cents)));
      }
    }
    if (newView.allocation.status === 'OPTIMAL') {
      const lots = new Map(candidate.income_lots.map((lot) => [lot.fragment_id, lot]));
      const goals = new Map(candidate.goals.map((goal) => [goal.goal_id, goal]));
      const available = sum(candidate.income_lots.map((lot) => lot.available_cents));
      const lowest = candidate.hard_protection_points.reduce((worst, point) => { const remaining = BigInt(point.cash_cents)-sum(pointFields.map((key) => point[key])); return remaining < worst ? remaining : worst; }, BigInt(candidate.hard_protection_points[0]!.cash_cents));
      requireValue(BigInt(newView.allocation.budget_cents) === (lowest < 0n ? 0n : lowest < available ? lowest : available));
      for (const row of newView.allocation.goals) { const goal = goals.get(row.goal_id)!; requireValue(BigInt(row.projected_owned_cents!) === BigInt(goal.current_owned_cents)+BigInt(row.amount_cents!) && row.amount_cents! <= Math.max(0, Math.min(goal.target_cents-goal.current_owned_cents, goal.monthly_max_cents-goal.current_month_contributed_cents))); }
      for (const use of newView.allocation.income_uses) { const lot = lots.get(use.fragment_id); const goal = goals.get(use.goal_id)!; requireValue(lot && use.origin_transaction_id === lot.origin_transaction_id && use.source_account_id === lot.account_id && Date.parse(lot.occurred_at) >= Math.max(Date.parse(goal.confirmed_at), Date.parse(goal.valid_from)) && ['ACTIVE', 'CONFIRMED'].includes(goal.policy_status) && Date.parse(goal.valid_from) <= Date.parse(value.as_of) && (goal.valid_until === null || Date.parse(value.as_of) < Date.parse(goal.valid_until))); }
      for (const lot of candidate.income_lots) requireValue(sum(newView.allocation.income_uses.filter((use) => use.fragment_id === lot.fragment_id).map((use) => use.amount_cents)) <= BigInt(lot.available_cents));
    }
  }
  const result = value as unknown as FullJointPlanning;
  if (originalText !== undefined) originals.set(result, originalText);
  return result;
}

/** Separate GET Goal rows are a freshness cross-check, never a same-snapshot claim. */
export function validateFullJointGoals(data: FullJointPlanning, goals: readonly Goal[] | undefined): void {
  if (goals === undefined) return;
  const all = new Set([...data.original_joint.included_goal_ids, ...data.original_joint.uncovered_goal_ids]);
  requireValue(ids(goals.map((goal) => goal.id)) && goals.length === all.size && goals.every((goal) => all.has(goal.id)));
  for (const input of data.binding?.candidate.goals ?? []) {
    const actual = goals.find((goal) => goal.id === input.goal_id);
    requireValue(actual && actual.policy_id === input.policy_id && actual.policy_version_id === input.effective_policy_version_id && actual.account_id === input.account_id && actual.target_cents === input.target_cents && actual.allocated_cents === input.current_owned_cents && actual.monthly_min_cents === input.monthly_min_cents && actual.monthly_target_cents === input.monthly_target_cents && actual.monthly_max_cents === input.monthly_max_cents && actual.deadline === input.deadline);
  }
}

export const getFullCurrentGoalAllocation = () => request<FullJointPlanning>('/planning/full-current-goal-allocation', 'GET', undefined, parseFullJointPlanning);
