import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';

export type AnnualPlanning = components['schemas']['AnnualProjectionResponse'];
export type AnnualCheckpoint = AnnualPlanning['initial_checkpoint'];
export type AnnualPoint = NonNullable<AnnualCheckpoint['before_payment']>;
const originals = new WeakMap<AnnualPlanning, string>();
export const getOriginalAnnualResponse = (data: AnnualPlanning) => originals.get(data) ?? null;
const phases = ['BEFORE_PAYMENT', 'AFTER_PAYMENT', 'AFTER_PRINCIPAL'] as const;
function requireValue(condition: unknown): asserts condition { if (!condition) throw new Error('年度规划响应的日期、三阶段、精确金额或只读边界未通过校验'); }
const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const timestamp = (value: unknown) => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const strings = (value: unknown) => Array.isArray(value) && value.every((item) => typeof item === 'string');
const money = (value: unknown) => value === null || Number.isSafeInteger(value);
export function annualDayNumber(value: unknown): number {
  requireValue(typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value));
  const milliseconds = Date.parse(`${value}T00:00:00Z`);
  requireValue(Number.isFinite(milliseconds) && new Date(milliseconds).toISOString().slice(0, 10) === value);
  return milliseconds / 86400000;
}
const dayNumber = annualDayNumber;
function point(value: unknown, first: number, horizon: number): asserts value is AnnualPoint {
  requireValue(object(value) && Number.isSafeInteger(value.day) && Number(value.day) >= 0 && Number(value.day) <= horizon &&
    dayNumber(value.date) === first + Number(value.day) && phases.includes(value.phase as typeof phases[number]) &&
    Number.isSafeInteger(value.cash_cents) && Number.isSafeInteger(value.margin_cents) && object(value.protected_cents_by_reason) &&
    Object.values(value.protected_cents_by_reason).every(Number.isSafeInteger) && strings(value.obligation_occurrence_ids) &&
    Array.isArray(value.principal_position_ids) && value.principal_position_ids.every(uuid));
}
function pointFields(value: AnnualPoint): string {
  return JSON.stringify([value.day, value.date, value.phase, value.cash_cents, Object.entries(value.protected_cents_by_reason).sort(([a], [b]) => a.localeCompare(b)),
    value.margin_cents, value.obligation_occurrence_ids, value.principal_position_ids]);
}
export function validateAnnualBoundary(value: unknown, first: number, horizon: number): Map<string, AnnualPoint> {
  requireValue(object(value) && typeof value.algorithm_version === 'string' && value.financial_only === true &&
    ['READY', 'LIQUIDITY_RISK', 'INSUFFICIENT_EVIDENCE'].includes(String(value.status)) &&
    ['safe_idle_cents', 'minimum_margin_cents', 'deficit_cents'].every((field) => money(value[field])) &&
    object(value.protected_cents_by_reason) && Object.values(value.protected_cents_by_reason).every(Number.isSafeInteger) &&
    object(value.max_allocatable_by_product) && Object.values(value.max_allocatable_by_product).every(money) &&
    typeof value.boundary_hash === 'string' && /^[0-9a-f]{64}$/.test(value.boundary_hash) && strings(value.calculation_notes) &&
    Array.isArray(value.blocking_constraints) && value.blocking_constraints.every((item) => object(item) && typeof item.code === 'string') &&
    Array.isArray(value.calculation_trace));
  const result = new Map<string, AnnualPoint>();
  for (const entry of value.calculation_trace) {
    point(entry, first, horizon); const key = `${entry.day}:${entry.phase}`;
    requireValue(!result.has(key)); result.set(key, entry);
  }
  if (value.status === 'INSUFFICIENT_EVIDENCE') requireValue(result.size === 0 && value.safe_idle_cents === null && value.minimum_margin_cents === null && value.deficit_cents === null);
  else requireValue(result.size === (horizon + 1) * 3);
  return result;
}
const boundary = validateAnnualBoundary;
export function parseAnnualPlanning(value: unknown, originalText?: string): AnnualPlanning {
  assertMoneyFields(value);
  requireValue(object(value) && value.schema_version === 'annual-planning-v1' && value.simulation === true && uuid(value.user_id) && timestamp(value.as_of) &&
    ['Asia/Shanghai', 'UTC'].includes(String(value.timezone)) && value.horizon_days === 365 && value.execution_view_horizon_days === 90 &&
    value.grants_authority === false && value.future_points_are_settled_cash === false && value.projection_basis === 'CURRENT_VERIFIED_FACTS_CONDITIONAL_COMMITMENTS' &&
    object(value.initial_checkpoint) && Array.isArray(value.daily_checkpoints) && value.daily_checkpoints.length === 365);
  const first = dayNumber(value.initial_checkpoint.date);
  const sourceDate = new Date(Date.parse(String(value.as_of)) + (value.timezone === 'Asia/Shanghai' ? 8 * 3600000 : 0)).toISOString().slice(0, 10);
  requireValue(value.initial_checkpoint.date === sourceDate);
  const annualTrace = boundary(value.annual_projection, first, 365); boundary(value.execution_view, first, 90);
  for (const [day, checkpoint] of [value.initial_checkpoint, ...value.daily_checkpoints].entries()) {
    requireValue(object(checkpoint) && checkpoint.day === day && dayNumber(checkpoint.date) === first + day &&
      ['PROVEN', 'NOT_PROVEN'].includes(String(checkpoint.status)) && money(checkpoint.minimum_intraday_margin_cents));
    for (const [index, field] of ['before_payment', 'after_payment', 'after_principal'].entries()) {
      const entry = checkpoint[field]; const original = annualTrace.get(`${day}:${phases[index]}`);
      if (checkpoint.status === 'NOT_PROVEN') requireValue(entry === null && !original && checkpoint.minimum_intraday_margin_cents === null);
      else {
        point(entry, first, 365); requireValue(entry.day === day && entry.phase === phases[index] && original && pointFields(entry) === pointFields(original));
        requireValue(Number.isSafeInteger(checkpoint.minimum_intraday_margin_cents));
      }
    }
  }
  requireValue(object(value.future_income) && value.future_income.status === 'NOT_IMPLEMENTED_NO_REGISTERED_SOURCE' &&
    value.future_income.included_in_execution_cents === 0 && value.future_income.included_in_planning_cents === 0 && typeof value.future_income.reason === 'string' &&
    Array.isArray(value.unavailable_principal) && value.unavailable_principal.every((item) => object(item) && uuid(item.position_id) && item.reason === 'NO_VERIFIED_RETURN_DATE') &&
    Array.isArray(value.source_evidence_ids) && value.source_evidence_ids.every(uuid) && typeof value.input_digest === 'string' && /^[0-9a-f]{64}$/.test(value.input_digest) &&
    Array.isArray(value.source_issues) && value.source_issues.every((item) => object(item) && ['code', 'source_ref', 'message'].every((field) => typeof item[field] === 'string')) &&
    object(value.audit) && value.audit.scope === 'CURRENT_LIVE_EPOCH' && (value.audit.epoch_id === null || uuid(value.audit.epoch_id)) &&
    typeof value.audit.status === 'string' && typeof value.audit.complete === 'boolean' && object(value.audit.anchored_run_statuses) &&
    Object.entries(value.audit.anchored_run_statuses).every(([id, state]) => uuid(id) && typeof state === 'string'));
  const result = value as unknown as AnnualPlanning;
  if (originalText !== undefined) originals.set(result, originalText); return result;
}
export const getAnnualPlanning = () => request<AnnualPlanning>('/planning/annual', 'GET', undefined, parseAnnualPlanning);
