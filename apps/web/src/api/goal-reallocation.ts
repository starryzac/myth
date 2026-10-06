import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { parseFullPolicy } from './full-policies';
import { sameFullPolicyJson } from '../features/full-policy-operation';
import type { FullPolicy } from './full-policies';
import { parseFullGoalModel } from './full-goals';
import type { GoalModelBinding } from './full-goals';
import { annualDayNumber } from './planning';

/** Exact server-generated DTO aliases. No client math creates a bank candidate. */
export type ReallocationPreview = components['schemas']['FullGoalReallocationPreview'];
export type ReallocationRequest = components['schemas']['ReallocationPreviewRequest'];
export type ReallocationMath = ReallocationPreview['decision']['math'];
export type ReallocationBinding = { goal: GoalModelBinding; userId: string; epochId: string };
export const repairConditions = ['HARD_OBLIGATION_SHORTFALL', 'LIVING_RESERVE_SHORTFALL', 'EMERGENCY_BUFFER_SHORTFALL'] as const;
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
const digest = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const integer = (v: unknown, min = 0): v is number => Number.isSafeInteger(v) && Number(v) >= min;
const nullable = (v: unknown, predicate: (value: unknown) => boolean) => v === null || predicate(v);
const ids = (v: unknown): v is string[] => Array.isArray(v) && v.every(uuid) && new Set(v).size === v.length;
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((item) => typeof item === 'string');
const timestamp = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const exact = (v: Record<string, unknown>, keys: readonly string[]) => Object.keys(v).sort().join('|') === [...keys].sort().join('|');
function check(v: unknown): asserts v { if (!v) throw new Error('紧急回拨预览的原版本、归属、三层数学或无授权边界不一致'); }
const originals = new WeakMap<object, string>();
export const getOriginalReallocationResponse = (v: ReallocationPreview) => originals.get(v) ?? null;
export function validateReallocationRequest(value: unknown): ReallocationRequest {
  check(object(value) && exact(value, ['policy_id', 'source_goal_id', 'expected_policy_version_id', 'expected_goal_policy_version_id', 'expected_epoch_id']) && Object.values(value).every(uuid));
  return value as ReallocationRequest;
}
export function parseCrossGoalConfiguration(value: unknown): Record<string, unknown> {
  assertMoneyFields(value); check(object(value) && exact(value, ['type', 'name', 'valid_from', 'valid_until', 'enabled', 'source_goal_ids', 'emergency_conditions', 'destination_scope', 'single_action_cap_cents', 'total_cap_cents']) && value.type === 'cross_goal_reallocation' && typeof value.enabled === 'boolean' && ids(value.source_goal_ids) && Array.isArray(value.emergency_conditions) && value.emergency_conditions.every((item) => repairConditions.includes(item)) && new Set(value.emergency_conditions).size === value.emergency_conditions.length && value.destination_scope === 'PROTECTED_CASH' && integer(value.single_action_cap_cents) && integer(value.total_cap_cents));
  for (const key of ['valid_from', 'valid_until']) check(value[key] === null || typeof value[key] === 'string' && Number.isInteger(annualDayNumber(value[key])));
  check(value.name === null || typeof value.name === 'string' && value.name.trim().length > 0 && value.name.length <= 120);
  if (value.enabled) check(value.source_goal_ids.length > 0 && value.emergency_conditions.length > 0 && typeof value.valid_from === 'string' && typeof value.valid_until === 'string' && value.valid_from <= value.valid_until && value.single_action_cap_cents > 0 && value.single_action_cap_cents <= value.total_cap_cents);
  else check(value.single_action_cap_cents === 0 && value.total_cap_cents === 0 && value.emergency_conditions.length === 0);
  return value;
}
function ownedAmount(value: unknown, goalId: string, kind: string): asserts value is components['schemas']['ReconciliationAmount'] {
  check(object(value) && value.entity_id === goalId && value.kind === kind && ['MATCHED', 'DIFFERENCE', 'MISSING'].includes(String(value.state)) && ['application_cents', 'bank_cents', 'difference_cents'].every((key) => nullable(value[key], (v) => integer(v, Number.MIN_SAFE_INTEGER))));
  if (value.bank_head !== null) check(object(value.bank_head) && uuid(value.bank_head.posting_id) && value.bank_head.ledger_key === `${kind}:${goalId}` && integer(value.bank_head.sequence_number, 1) && timestamp(value.bank_head.occurred_at));
  else check(value.bank_cents === null);
  if (value.bank_cents === null || value.application_cents === null) check(value.difference_cents === null && value.state === 'MISSING');
  else { const difference = BigInt(value.application_cents as number) - BigInt(value.bank_cents as number); check(value.difference_cents !== null && difference === BigInt(value.difference_cents as number) && value.state === (difference === 0n ? 'MATCHED' : 'DIFFERENCE')); }
}
function ownership(value: unknown, goalId: string): asserts value is components['schemas']['ReconciliationGoal'] {
  check(object(value) && value.goal_id === goalId && nullable(value.account_id, uuid) && integer(value.allocated_cents) && ids(value.position_ids) && typeof value.current_ownership_proof_verified === 'boolean' && nullable(value.ownership_evidence_id, uuid) && nullable(value.ownership_evidence_hash, digest) && (value.ownership_evidence_id === null) === (value.ownership_evidence_hash === null));
  if (value.current_ownership_proof_verified) check(value.ownership_evidence_id !== null);
  ownedAmount(value.cash, goalId, 'GOAL_CASH'); ownedAmount(value.principal, goalId, 'GOAL_PRINCIPAL');
  if (value.cash.application_cents !== null && value.principal.application_cents !== null) check(BigInt(value.cash.application_cents) + BigInt(value.principal.application_cents) === BigInt(value.allocated_cents));
}
function math(value: unknown): asserts value is ReallocationMath {
  check(object(value) && ['COMPUTED', 'UNKNOWN'].includes(String(value.status)) && value.principal_release_cents === 0 && value.future_income_used_cents === 0 && typeof value.unique_minimum_for_registered_current_scope === 'boolean' && nullable(value.source_cash_releasable_above_minimum_cents, integer));
  const names = ['cash_cents', 'locked_goal_cash_cents', 'reserved_cash_cents', 'unowned_unreserved_cash_cents', 'minimum_repair_cents'] as const;
  if (value.status === 'UNKNOWN') { check(names.every((key) => value[key] === null) && value.required_by_condition === null && value.shortfall_by_condition === null && value.unique_minimum_for_registered_current_scope === false); return; }
  check(names.every((key) => integer(value[key])) && value.unique_minimum_for_registered_current_scope === true && object(value.required_by_condition) && exact(value.required_by_condition, repairConditions) && Object.values(value.required_by_condition).every((n) => integer(n)) && object(value.shortfall_by_condition) && exact(value.shortfall_by_condition, repairConditions) && Object.values(value.shortfall_by_condition).every((n) => integer(n)));
  const available = BigInt(value.cash_cents as number) - BigInt(value.locked_goal_cash_cents as number) - BigInt(value.reserved_cash_cents as number); check(available >= 0n && available === BigInt(value.unowned_unreserved_cash_cents as number));
  let prefix = 0n, prior = 0n;
  for (const condition of repairConditions) { prefix += BigInt(value.required_by_condition[condition] as number); const deficit = prefix > available ? prefix - available : 0n; check(deficit - prior === BigInt(value.shortfall_by_condition[condition] as number)); prior = deficit; }
  check(prior === BigInt(value.minimum_repair_cents as number));
}
export function parseReallocationPreview(value: unknown, binding: ReallocationBinding, expected: ReallocationRequest, raw?: string): ReallocationPreview {
  assertMoneyFields(value); validateReallocationRequest(expected); check(uuid(binding.userId) && uuid(binding.epochId) && expected.expected_epoch_id === binding.epochId && expected.source_goal_id === binding.goal.id && expected.expected_goal_policy_version_id === binding.goal.policy_version_id);
  check(object(value) && value.schema_version === 'full-goal-reallocation-preview-v1' && value.user_id === binding.userId && value.epoch_id === binding.epochId && timestamp(value.as_of) && value.simulation === true && value.read_only === true && ['bank_authority', 'financial_grant_created', 'original_goal_bridge_cross_enabled'].every((key) => value[key] === false) && value.execution_support === 'NOT_IMPLEMENTED' && sameFullPolicyJson(value.original_request, expected));
  const policy = parseFullPolicy(value.policy, expected.policy_id); check(policy.epoch_id === binding.epochId && policy.template_name === 'CrossGoalReallocationPolicy' && policy.current_version.version_id === expected.expected_policy_version_id && policy.current_version.confirmation.user_id === binding.userId); const config = parseCrossGoalConfiguration(policy.current_version.configuration);
  const originalModel = value.goal_model === null ? null : parseFullGoalModel(value.goal_model, binding.goal); if (originalModel !== null) check(originalModel.epoch_id === binding.epochId);
  if (value.goal_ownership !== null) ownership(value.goal_ownership, binding.goal.id);
  if (value.original_current_protection_point !== null) {
    const p = value.original_current_protection_point; check(object(p) && p.day === 0 && p.phase === 'BEFORE_PAYMENT' && typeof p.date === 'string' && Number.isInteger(annualDayNumber(p.date)) && integer(p.cash_cents, Number.MIN_SAFE_INTEGER) && integer(p.margin_cents, Number.MIN_SAFE_INTEGER) && object(p.protected_cents_by_reason));
    const floors = p.protected_cents_by_reason; check(['obligations', 'living', 'emergency', 'goal_cash', 'goal_minimum'].every((key) => integer(floors[key])) && Object.values(floors).every((n) => integer(n)) && strings(p.obligation_occurrence_ids) && ids(p.principal_position_ids));
    check(BigInt(p.cash_cents) - Object.values(floors).reduce<bigint>((sum, n) => sum + BigInt(n as number), 0n) === BigInt(p.margin_cents));
  }
  const decision = value.decision; check(object(decision) && decision.algorithm_version === 'current-core-cash-ownership-repair-v1' && ['NO_EMERGENCY', 'BLOCKED', 'UNKNOWN'].includes(String(decision.state)) && decision.candidate_amount_cents === null && decision.source_goal_id === binding.goal.id && decision.destination_scope === 'PROTECTED_CASH' && decision.planning_only === true && decision.bank_authority === false && decision.dedicated_confirmation_required === true && decision.execution_support === 'NOT_IMPLEMENTED' && decision.preserves_principal_placement === true && decision.repairs_performed === false && strings(decision.reasons) && decision.reasons.includes('DEDICATED_ORIGINAL_GOAL_GRANT_AND_EXECUTION_NOT_IMPLEMENTED') && digest(decision.input_hash));
  math(decision.math); check(nullable(decision.cumulative_used_cents, integer) && nullable(decision.cumulative_remaining_cents, integer) && (decision.cumulative_used_cents === null) === (decision.cumulative_remaining_cents === null));
  if (decision.reasons.includes('LIFETIME_POLICY_USAGE_ORIGINALS_MISSING')) check(decision.cumulative_used_cents === null && decision.cumulative_remaining_cents === null);
  if (decision.cumulative_used_cents !== null) { const total = BigInt(config.total_cap_cents as number), used = BigInt(decision.cumulative_used_cents as number); check(BigInt(decision.cumulative_remaining_cents as number) === (total > used ? total - used : 0n)); }
  const decisionMath = decision.math;
  const triggered = decisionMath.shortfall_by_condition === null ? [] : repairConditions.filter((condition) => decisionMath.shortfall_by_condition![condition]! > 0); check(sameFullPolicyJson(decision.triggered_conditions, triggered));
  check((decision.state === 'NO_EMERGENCY') === (decision.math.status === 'COMPUTED' && decision.math.minimum_repair_cents === 0));
  if (decisionMath.status === 'COMPUTED') { const p = value.original_current_protection_point; check(object(p) && object(p.protected_cents_by_reason) && p.cash_cents === decisionMath.cash_cents && p.protected_cents_by_reason.goal_cash === decisionMath.locked_goal_cash_cents); const floors = p.protected_cents_by_reason; const map = { HARD_OBLIGATION_SHORTFALL: 'obligations', LIVING_RESERVE_SHORTFALL: 'living', EMERGENCY_BUFFER_SHORTFALL: 'emergency' }; for (const condition of repairConditions) check(decisionMath.required_by_condition![condition] === floors[map[condition]]); }
  if (decision.math.source_cash_releasable_above_minimum_cents !== null) { const goal = value.goal_ownership, model = originalModel; check(object(goal) && goal.current_ownership_proof_verified === true && object(goal.cash) && object(goal.principal) && goal.cash.state === 'MATCHED' && goal.principal.state === 'MATCHED' && integer(goal.cash.bank_cents) && integer(goal.principal.bank_cents) && model !== null && model.status === 'VERIFIED' && object(model.full_configuration) && integer(model.full_configuration.minimum_guarantee_cents)); const amount = BigInt(decision.math.source_cash_releasable_above_minimum_cents), above = BigInt(goal.cash.bank_cents) + BigInt(goal.principal.bank_cents) - BigInt(model.full_configuration.minimum_guarantee_cents); check(amount <= BigInt(goal.cash.bank_cents) && amount <= (above > 0n ? above : 0n)); }
  const reasons = decision.reasons;
  check(['reconciliation_input_hash', 'full_protection_input_hash', 'source_binding_hash'].every((key) => digest(value[key])) && strings(value.limitations) && Array.isArray(value.source_issues) && value.source_issues.every((issue) => object(issue) && ['code', 'entity_type'].every((key) => typeof issue[key] === 'string') && nullable(issue.entity_id, (v) => typeof v === 'string') && reasons.includes(issue.code as string)));
  const result = value as ReallocationPreview; if (raw !== undefined) originals.set(result, raw); return result;
}
export async function previewGoalReallocation(binding: ReallocationBinding, policy: FullPolicy): Promise<ReallocationPreview> {
  parseFullPolicy(policy); parseCrossGoalConfiguration(policy.current_version.configuration); check(policy.template_name === 'CrossGoalReallocationPolicy' && policy.epoch_id === binding.epochId && policy.current_version.confirmation.user_id === binding.userId);
  const body: ReallocationRequest = validateReallocationRequest({ policy_id: policy.policy_id, source_goal_id: binding.goal.id, expected_policy_version_id: policy.current_version.version_id, expected_goal_policy_version_id: binding.goal.policy_version_id, expected_epoch_id: binding.epochId });
  return request('/goal-reallocation/preview', 'POST', body, (value, raw) => parseReallocationPreview(value, binding, body, raw));
}
