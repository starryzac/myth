import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { annualDayNumber, validateAnnualBoundary } from './planning';
import { spendingHash } from './spending-evidence';

export type FinancialPreviewBody = components['schemas']['FullPreviewRequest'];
type NativePreview = components['schemas']['FullPolicyChangeFinancialPreview'];
type Impact = Required<NativePreview['financial_impact']>;
/** Defaults remain optional in OpenAPI; the reader requires their actual presence. */
export type FinancialChangePreview = Omit<Required<NativePreview>, 'financial_impact'> & {
  financial_impact: Omit<Impact, 'goals' | 'positions' | 'candidate_commitments'> & {
    goals: Required<Impact['goals'][number]>[];
    positions: Required<Impact['positions'][number]>[];
    candidate_commitments: Required<Impact['candidate_commitments'][number]>[];
  };
};
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value);
const hash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((row) => typeof row === 'string');
const ids = (value: unknown): value is string[] => strings(value) && value.every(uuid) && new Set(value).size === value.length;
const integer = (value: unknown, min = Number.MIN_SAFE_INTEGER): value is number => Number.isSafeInteger(value) && Number(value) >= min;
const nullable = (value: unknown, predicate: (item: unknown) => boolean) => value === null || predicate(value);
const phases = ['BEFORE_PAYMENT', 'AFTER_PAYMENT', 'AFTER_PRINCIPAL'] as const;
const originals = new WeakMap<object, string>();
export const getOriginalFinancialPreviewResponse = (value: object) => originals.get(value) ?? null;
function check(value: unknown): asserts value { if (!value) throw new Error('财务修改预览的原版本、完整三阶段、差额或只读边界未通过校验'); }
function centsMap(value: unknown, nonnegative = false): asserts value is Record<string, number> {
  check(object(value) && Object.entries(value).every(([id, cents]) => uuid(id) && integer(cents, nonnegative ? 0 : Number.MIN_SAFE_INTEGER)));
}
function difference(after: number, before: number, delta: unknown) { check(integer(delta) && BigInt(delta) === BigInt(after) - BigInt(before)); }
/** Server may fill DSL defaults; every submitted field must still describe this candidate. */
function candidateFieldsMatch(submitted: unknown, canonical: unknown): boolean {
  if (Array.isArray(submitted)) return Array.isArray(canonical) && submitted.length === canonical.length && submitted.every((row, index) => candidateFieldsMatch(row, canonical[index]));
  if (object(submitted)) return object(canonical) && Object.entries(submitted).every(([key, row]) => Object.hasOwn(canonical, key) && candidateFieldsMatch(row, canonical[key]));
  if (time(submitted) && time(canonical)) return Date.parse(submitted) === Date.parse(canonical);
  return submitted === canonical;
}
export function parseFinancialPreviewBody(value: unknown): FinancialPreviewBody {
  check(object(value) && Object.keys(value).sort().join('|') === 'configuration|expected_version_id' && uuid(value.expected_version_id) && object(value.configuration));
  assertMoneyFields(value); return value as FinancialPreviewBody;
}
/** Validate the original server curve, not a client-side financial recomputation. */
function beforeCurve(value: unknown): void {
  check(object(value) && Array.isArray(value.calculation_trace));
  if (value.status === 'INSUFFICIENT_EVIDENCE') { validateAnnualBoundary(value, 0, 365); return; }
  check(value.calculation_trace.length === 1098 && object(value.calculation_trace[0]));
  const first = annualDayNumber(value.calculation_trace[0].date); validateAnnualBoundary(value, first, 365);
  value.calculation_trace.forEach((row, index) => check(object(row) && row.day === Math.floor(index / 3) && row.phase === phases[index % 3]));
  if (value.safe_idle_cents !== null) check(integer(value.safe_idle_cents, 0));
  check(object(value.max_allocatable_by_product) && Object.entries(value.max_allocatable_by_product).every(([id, cents]) => uuid(id) && nullable(cents, (item) => integer(item, 0))));
}
export async function parseFinancialChangePreview(value: unknown, policyId: string, body: FinancialPreviewBody, raw?: string): Promise<FinancialChangePreview> {
  parseFinancialPreviewBody(body); assertMoneyFields(value);
  check(uuid(policyId) && object(value) && value.simulation === true && value.hypothetical === true && value.grants_authority === false && value.policy_id === policyId && value.expected_version_id === body.expected_version_id && uuid(value.epoch_id) && time(value.as_of));
  check(hash(value.configuration_hash) && hash(value.current_configuration_hash) && hash(value.current_fact_digest) && object(value.before_configuration) && object(value.after_configuration) && candidateFieldsMatch(body.configuration, value.after_configuration) && strings(value.changed_fields) && Array.isArray(value.reference_snapshots) && value.reference_snapshots.every(object));
  if (['dated_expense', 'periodic_transfer'].includes(String(value.after_configuration.type))) check(await spendingHash(value.after_configuration) === value.configuration_hash && await spendingHash(value.before_configuration) === value.current_configuration_hash);
  check(ids(value.original_action_ids) && ids(value.original_position_ids) && value.action_impact === 'ORIGINAL_ACTIONS_UNCHANGED_REQUIRES_FRESH_RECOMPUTATION_AFTER_CONFIRMATION');
  const originalPositionIds = value.original_position_ids;
  const p = value.financial_impact;
  check(object(p) && p.protocol === 'full-policy-financial-impact-v1' && p.simulation === true && p.hypothetical === true && p.grants_authority === false && p.writes_policy_or_bank === false && p.future_income_cents === 0 && p.horizon_days === 365 && p.initial_day_and_365_future_days === true && p.basis === 'FUTURE_ONLY_CONSERVATIVE_UNPAID_REPLACEMENT' && ['PROJECTED', 'UNKNOWN'].includes(String(p.status)) && hash(p.input_hash) && strings(p.reasons) && strings(p.limitations) && strings(p.retained_original_occurrence_ids) && new Set(p.retained_original_occurrence_ids).size === p.retained_original_occurrence_ids.length);
  const retainedIds = p.retained_original_occurrence_ids;
  if (p.before !== null) beforeCurve(p.before);
  check(Array.isArray(p.goals) && p.goals.every((row) => object(row) && uuid(row.goal_id) && integer(row.current_owned_cash_cents, 0) && integer(row.current_owned_principal_cents, 0) && row.current_allocation_delta_cents === 0 && row.current_principal_delta_cents === 0 && row.future_allocation_cents === null && row.future_allocation_status === 'UNKNOWN_NO_CANDIDATE_GOAL_SOLVER' && ids(row.original_evidence_ids)) && new Set(p.goals.map((row) => row.goal_id)).size === p.goals.length);
  check(Array.isArray(p.positions) && p.positions.every((row) => object(row) && uuid(row.position_id) && nullable(row.goal_id, uuid) && integer(row.original_recorded_principal_cents, 0) && integer(row.current_outstanding_principal_cents, 0) && row.current_principal_delta_cents === 0 && typeof row.original_status === 'string' && row.current_outstanding_principal_cents === (row.original_status === 'REDEEMED' ? 0 : row.original_recorded_principal_cents) && nullable(row.original_principal_available_at, time) && ids(row.original_evidence_ids) && row.future_disposition_status === 'UNKNOWN_NO_CANDIDATE_ACTION_GENERATION') && new Set(p.positions.map((row) => row.position_id)).size === p.positions.length && p.positions.every((row) => originalPositionIds.includes(row.position_id)));
  check(Array.isArray(p.candidate_commitments) && p.candidate_commitments.every((row) => object(row) && typeof row.identity === 'string' && row.identity.length > 0 && nullable(row.original_occurrence_id, (item) => typeof item === 'string') && uuid(row.policy_id) && ['RETAINED_ORIGINAL', 'UNCONFIRMED_CANDIDATE'].includes(String(row.source_kind)) && ['DATED_EXPENSE', 'PERIODIC_TRANSFER'].includes(String(row.kind)) && integer(row.amount_cents, 0) && nullable(row.source_account_id, uuid) && row.bank_authority === false && Number.isInteger(annualDayNumber(row.due_date)) && (row.source_kind === 'UNCONFIRMED_CANDIDATE' ? row.original_occurrence_id === null && row.policy_id === policyId : row.original_occurrence_id === row.identity && retainedIds.includes(row.identity))) && new Set(p.candidate_commitments.map((row) => row.identity)).size === p.candidate_commitments.length);
  if (p.status === 'UNKNOWN') check(p.reasons.length > 0 && p.after === null && p.delta_safe_idle_cents === null && p.delta_minimum_margin_cents === null && p.delta_max_allocatable_by_product === null);
  else {
    const before = p.before; const after = p.after;
    check(object(before) && before.status !== 'INSUFFICIENT_EVIDENCE' && integer(before.safe_idle_cents, 0) && integer(before.minimum_margin_cents) && Array.isArray(before.calculation_trace) && object(after) && ['READY', 'LIQUIDITY_RISK'].includes(String(after.status)) && after.financial_capacity_is_authority === false && integer(after.safe_idle_cents, 0) && integer(after.minimum_margin_cents) && strings(after.source_account_limitations) && hash(after.curve_hash) && Array.isArray(after.calculation_trace) && after.calculation_trace.length === 1098 && p.reasons.length === 0);
    centsMap(before.max_allocatable_by_product, true); centsMap(after.max_allocatable_by_product, true); centsMap(p.delta_max_allocatable_by_product);
    check(before.minimum_margin_cents === Math.min(...before.calculation_trace.map((row) => Number(row.margin_cents))) && before.safe_idle_cents <= Math.max(0, before.minimum_margin_cents));
    check(Object.keys(before.max_allocatable_by_product).sort().join('|') === Object.keys(after.max_allocatable_by_product).sort().join('|') && Object.keys(before.max_allocatable_by_product).sort().join('|') === Object.keys(p.delta_max_allocatable_by_product).sort().join('|'));
    for (const [id, cents] of Object.entries(after.max_allocatable_by_product)) difference(cents, before.max_allocatable_by_product[id]!, p.delta_max_allocatable_by_product[id]);
    difference(after.safe_idle_cents, before.safe_idle_cents, p.delta_safe_idle_cents); difference(after.minimum_margin_cents, before.minimum_margin_cents, p.delta_minimum_margin_cents);
    const beforePoints = before.calculation_trace;
    after.calculation_trace.forEach((row, index) => {
      const original = beforePoints[index];
      check(object(original) && object(row) && row.day === original.day && row.date === original.date && row.phase === original.phase && integer(row.cash_cents) && integer(row.margin_cents) && object(row.protected_cents_by_reason) && Object.values(row.protected_cents_by_reason).every((cents) => integer(cents, 0)) && strings(row.obligation_occurrence_ids) && ids(row.principal_position_ids) && JSON.stringify(row.principal_position_ids) === JSON.stringify(original.principal_position_ids));
      check(BigInt(row.margin_cents) === BigInt(row.cash_cents) - Object.values(row.protected_cents_by_reason).reduce<bigint>((sum, cents) => sum + BigInt(cents as number), 0n));
    });
    check(after.minimum_margin_cents === Math.min(...after.calculation_trace.map((row) => Number(row.margin_cents))) && after.safe_idle_cents <= Math.max(0, after.minimum_margin_cents) && (after.minimum_margin_cents < 0 ? after.status === 'LIQUIDITY_RISK' : true));
    check(await spendingHash({ protocol: 'hypothetical-full-curve-v1', input_hash: p.input_hash, trace: after.calculation_trace }) === after.curve_hash);
    check(p.positions.length === value.original_position_ids.length);
  }
  const result = value as unknown as FinancialChangePreview; if (raw !== undefined) originals.set(result, raw); return result;
}
export async function previewFullPolicyFinancialChange(policyId: string, body: FinancialPreviewBody): Promise<FinancialChangePreview> {
  check(uuid(policyId)); parseFinancialPreviewBody(body);
  const original = await request<{ simulation: true; value: unknown; raw: string }>(`/full-policies/${policyId}/financial-change-preview`, 'POST', body, (value, raw) => ({ simulation: true, value, raw }));
  return parseFinancialChangePreview(original.value, policyId, body, original.raw);
}
