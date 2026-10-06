import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
export type DynamicGoalReserve = components['schemas']['DynamicGoalReserveResponse'];
export type DynamicReserve = NonNullable<DynamicGoalReserve['reserve']>;
export type DynamicGoalBinding = { id: string; policy_version_id: string };
const originals = new WeakMap<DynamicGoalReserve, string>();
export const getOriginalDynamicGoalResponse = (value: DynamicGoalReserve): string | null => originals.get(value) ?? null;
const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const digest = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const timestamp = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const strings = (value: unknown) => Array.isArray(value) && value.every((item) => typeof item === 'string');
function requireValue(value: unknown): asserts value { if (!value) throw new Error('动态目标节奏的原身份、当前月、精确金额或只读边界未通过校验'); }
const moneyKeys = ['current_owned_cents', 'current_month_contributed_cents', 'remaining_goal_cents', 'excess_owned_cents', 'uncapped_gross_pace_cents', 'dynamic_month_total_cents', 'nominal_month_target_cents', 'eligible_available_income_cents', 'independently_protected_budget_cents', 'desired_additional_cents', 'minimum_shortfall_cents', 'guarantee_shortfall_cents', 'deferral_cost_to_date_cents'] as const;
const numberKeys = [...moneyKeys, 'pace_delta_from_nominal_cents', 'suggested_additional_cents', 'progress_basis_points', 'remaining_calendar_month_slots', 'overdue_days'] as const;
const blocked = ['EXPIRED_POLICY', 'INACTIVE_POLICY', 'LIQUIDITY_RISK', 'INSUFFICIENT_EVIDENCE'];
export function parseDynamicGoalReserve(value: unknown, binding: DynamicGoalBinding, originalText?: string): DynamicGoalReserve {
  requireValue(uuid(binding.id) && uuid(binding.policy_version_id)); assertMoneyFields(value);
  requireValue(object(value) && value.schema_version === 'verified-dynamic-goal-reserve-v1' && value.simulation === true && value.grants_authority === false && uuid(value.user_id) && value.goal_id === binding.id && timestamp(value.as_of) && ['COMPUTED', 'EXPIRED', 'INACTIVE', 'UNKNOWN'].includes(value.state as string) && (value.policy_effective_status === null || typeof value.policy_effective_status === 'string') && value.protection_scope === 'ALL_ORIGINAL_365_DAY_RESERVES_RETAINED' && digest(value.input_hash) && Array.isArray(value.source_evidence_ids) && value.source_evidence_ids.every(uuid) && new Set(value.source_evidence_ids).size === value.source_evidence_ids.length && Array.isArray(value.source_issues) && value.source_issues.every((issue) => object(issue) && (['code', 'source_ref', 'message'] as const).every((field) => typeof issue[field] === 'string')) && strings(value.limitations));
  requireValue((value.state === 'COMPUTED') === (value.reserve !== null));
  if (value.state === 'EXPIRED') requireValue(value.policy_effective_status === 'EXPIRED');
  if (value.state === 'INACTIVE') requireValue(['SUSPENDED', 'REVOKED', 'CONFIRMED'].includes(value.policy_effective_status as string));
  const result = value as unknown as DynamicGoalReserve;
  if (result.reserve !== null) {
    const reserve = result.reserve;
    requireValue(object(reserve) && reserve.algorithm_version === 'remaining-month-gross-pacing-v1' && reserve.goal_id === binding.id && reserve.policy_version_id === binding.policy_version_id && /^\d{4}-(0[1-9]|1[0-2])$/.test(reserve.period) && [0, 8 * 3600000].some((offset) => new Date(Date.parse(result.as_of) + offset).toISOString().slice(0, 7) === reserve.period) && reserve.grants_authority === false && reserve.preview_only === true && reserve.future_income_included_cents === 0 && digest(reserve.input_hash) && strings(reserve.reasons) && reserve.actual_completion_date === null && result.source_issues.length === 0);
    requireValue([...blocked, 'READY', 'PARTIAL', 'MINIMUM_SHORTFALL', 'HARD_GUARANTEE_SHORTFALL', 'DEADLINE_BLOCKED', 'OVERDUE_READY', 'COMPLETE', 'MONTHLY_MAX_ALREADY_EXCEEDED'].includes(reserve.status));
    if (blocked.includes(reserve.status)) requireValue(numberKeys.every((key) => reserve[key] === null));
    else {
      requireValue(moneyKeys.every((key) => Number.isSafeInteger(reserve[key]) && reserve[key]! >= 0) && Number.isSafeInteger(reserve.pace_delta_from_nominal_cents) && Number.isSafeInteger(reserve.progress_basis_points) && reserve.progress_basis_points! >= 0 && reserve.progress_basis_points! <= 10000 && Number.isSafeInteger(reserve.remaining_calendar_month_slots) && reserve.remaining_calendar_month_slots! >= 1 && Number.isSafeInteger(reserve.overdue_days) && reserve.overdue_days! >= 0);
      requireValue(BigInt(reserve.dynamic_month_total_cents!) - BigInt(reserve.nominal_month_target_cents!) === BigInt(reserve.pace_delta_from_nominal_cents!) && reserve.desired_additional_cents! <= reserve.remaining_goal_cents!);
      if (['HARD_GUARANTEE_SHORTFALL', 'DEADLINE_BLOCKED'].includes(reserve.status)) requireValue(reserve.suggested_additional_cents === null && reserve.guarantee_shortfall_cents! > 0);
      else requireValue(Number.isSafeInteger(reserve.suggested_additional_cents) && reserve.suggested_additional_cents! >= 0 && reserve.suggested_additional_cents! <= Math.min(reserve.desired_additional_cents!, reserve.remaining_goal_cents!, reserve.eligible_available_income_cents!, reserve.independently_protected_budget_cents!));
      if (reserve.status === 'COMPLETE') requireValue(reserve.remaining_goal_cents === 0 && reserve.suggested_additional_cents === 0 && reserve.progress_basis_points === 10000);
      if (['MINIMUM_SHORTFALL', 'MONTHLY_MAX_ALREADY_EXCEEDED'].includes(reserve.status)) requireValue(reserve.suggested_additional_cents === 0);
      if (reserve.status === 'OVERDUE_READY') requireValue(reserve.overdue_days! > 0);
    }
  }
  if (originalText !== undefined) originals.set(result, originalText); return result;
}
export function getDynamicGoalReserve(binding: DynamicGoalBinding): Promise<DynamicGoalReserve> {
  requireValue(uuid(binding.id) && uuid(binding.policy_version_id));
  return request<DynamicGoalReserve>(`/goals/${binding.id}/dynamic-reserve`, 'GET', undefined, (value, raw) => parseDynamicGoalReserve(value, binding, raw));
}
