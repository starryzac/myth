import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';
import { annualDayNumber, validateAnnualBoundary } from './planning';

type Boundary = components['schemas']['BoundaryResult'];
type Flags = { simulation: true; read_only: true; hypothetical_only: true; grants_authority: false; executes_funds: false; writes_facts: false; resets_history: false; receipt_verified: false; economic_verified: false; future_income_included_cents: 0; execution: 'NOT_IMPLEMENTED' };
export type ScenarioRequest = { expected_epoch_id: string; expected_source_hash: string; expected_engine_hash: string; horizon_days: 90 | 365; cash_change: { account_id: string; delta_cents: number } | null; emergency_change: { policy_id: string; expected_version_id: string; amount_cents: number } | null; product_change: { product_id: string; expected_version_number: number; term_days: number; settlement_delay_days: number } | null };
export type ScenarioContext = Flags & { schema_version: 'counterfactual-context-v1'; user_id: string; epoch_id: string; as_of: string; timezone: 'Asia/Shanghai' | 'UTC'; source_hash: string; engine_hash: string; engine_files: Record<string, string>; cash_choices: { account_id: string; balance_cents: number; evidence_ids: string[] }[]; emergency_choices: { policy_id: string; version_id: string; configuration_hash: string; amount_cents: number; evidence_ids: string[] }[]; product_choices: { product_id: string; version_number: number; asset_class: string; minimum_purchase_cents: number; terms_digest: string; term_days: number; settlement_delay_days: number }[]; baseline_90: Boundary; baseline_365: Boundary; source_evidence_ids: string[]; source_issues: { code: string; source_ref: string; message: string }[]; audit: components['schemas']['DashboardAuditCard']; limitations: string[] };
export type ScenarioComparison = Flags & { schema_version: 'counterfactual-comparison-v1'; user_id: string; epoch_id: string; as_of: string; timezone: 'Asia/Shanghai' | 'UTC'; local_date: string; horizon_days: 90 | 365; source_hash: string; engine_hash: string; original_request: ScenarioRequest; request_hash: string; scenario_hash: string; baseline: Boundary; hypothetical: Boundary; delta_safe_idle_cents: number | null; changed_parameters: { kind: 'CASH_BALANCE_DELTA' | 'EMERGENCY_AMOUNT' | 'PRODUCT_OCCUPANCY'; entity_id: string; original: Record<string, unknown>; hypothetical: Record<string, unknown>; source_is_hypothetical: true }[]; limitations: string[] };
const originals = new WeakMap<object, string>();
export const getOriginalScenarioResponse = (data: ScenarioContext | ScenarioComparison) => originals.get(data) ?? null;
const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === 'string');
const integer = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && Number(value) >= min && Number(value) <= max;
const exact = (value: Record<string, unknown>, fields: string[]) => Object.keys(value).sort().join('|') === [...fields].sort().join('|');
function check(value: unknown): asserts value { if (!value) throw new Error('场景响应的原身份、精确金额、参数或只读边界不一致'); }
export function scenarioCanonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(scenarioCanonicalJson).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${scenarioCanonicalJson(value[key])}`).join(',')}}`;
  if (value === null || typeof value === 'string' || typeof value === 'boolean' || Number.isSafeInteger(value)) return JSON.stringify(value);
  throw new Error('场景参数不能含不精确数字或非JSON值');
}
export async function scenarioHash(value: unknown): Promise<string> { const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(scenarioCanonicalJson(value))); return [...new Uint8Array(hash)].map((n) => n.toString(16).padStart(2, '0')).join(''); }
function flags(value: Record<string, unknown>) {
  check(['simulation', 'read_only', 'hypothetical_only'].every((field) => value[field] === true) && ['grants_authority', 'executes_funds', 'writes_facts', 'resets_history', 'receipt_verified', 'economic_verified'].every((field) => value[field] === false) && value.future_income_included_cents === 0 && value.execution === 'NOT_IMPLEMENTED');
}
function header(value: Record<string, unknown>): number {
  flags(value); check(uuid(value.user_id) && uuid(value.epoch_id) && digest(value.source_hash) && digest(value.engine_hash) && typeof value.as_of === 'string' && /(?:Z|\+00:00)$/.test(value.as_of) && Number.isFinite(Date.parse(value.as_of)) && ['UTC', 'Asia/Shanghai'].includes(String(value.timezone)) && strings(value.limitations));
  const localDate = new Date(Date.parse(value.as_of) + (value.timezone === 'Asia/Shanghai' ? 8 * 3600000 : 0)).toISOString().slice(0, 10);
  return annualDayNumber(localDate);
}
function boundary(value: unknown, first: number, horizon: number): asserts value is Boundary {
  const points = validateAnnualBoundary(value, first, horizon); check(object(value));
  for (const p of points.values()) { const protectedSum = Object.values(p.protected_cents_by_reason).reduce((sum, n) => sum + BigInt(n), 0n); check(Object.values(p.protected_cents_by_reason).every((n) => n >= 0) && BigInt(p.cash_cents) - protectedSum === BigInt(p.margin_cents)); }
  if (value.status !== 'INSUFFICIENT_EVIDENCE') {
    const min = Math.min(...[...points.values()].map((p) => p.margin_cents));
    check(value.minimum_margin_cents === min && value.safe_idle_cents === Math.max(0, min) && value.deficit_cents === Math.max(0, -min) && value.status === (min < 0 ? 'LIQUIDITY_RISK' : 'READY'));
    check(Object.values(value.max_allocatable_by_product as Record<string, unknown>).every((n) => integer(n)));
  } else check(Object.values(value.max_allocatable_by_product as Record<string, unknown>).every((n) => n === null));
}
export function parseScenarioRequest(value: unknown): ScenarioRequest {
  check(object(value) && exact(value, ['expected_epoch_id', 'expected_source_hash', 'expected_engine_hash', 'horizon_days', 'cash_change', 'emergency_change', 'product_change']) && uuid(value.expected_epoch_id) && digest(value.expected_source_hash) && digest(value.expected_engine_hash) && [90, 365].includes(Number(value.horizon_days)) && typeof value.horizon_days === 'number');
  const cash = value.cash_change, reserve = value.emergency_change, product = value.product_change;
  check(cash === null || object(cash) && exact(cash, ['account_id', 'delta_cents']) && uuid(cash.account_id) && integer(cash.delta_cents, -10_000_000, 10_000_000));
  check(reserve === null || object(reserve) && exact(reserve, ['policy_id', 'expected_version_id', 'amount_cents']) && uuid(reserve.policy_id) && uuid(reserve.expected_version_id) && integer(reserve.amount_cents, 0, 10_000_000));
  check(product === null || object(product) && exact(product, ['product_id', 'expected_version_number', 'term_days', 'settlement_delay_days']) && uuid(product.product_id) && integer(product.expected_version_number, 1) && integer(product.term_days, 0, 365) && integer(product.settlement_delay_days, 0, 365) && Number(product.term_days) + Number(product.settlement_delay_days) <= 365);
  return value as unknown as ScenarioRequest;
}
export function parseScenarioContext(value: unknown, raw?: string): ScenarioContext {
  assertMoneyFields(value); check(object(value) && value.schema_version === 'counterfactual-context-v1'); const first = header(value);
  boundary(value.baseline_90, first, 90); boundary(value.baseline_365, first, 365);
  check(object(value.engine_files) && Object.keys(value.engine_files).length > 0 && Object.entries(value.engine_files).every(([path, hash]) => /^(domain|services|api\/v1)\/[a-z_]+\.py$/.test(path) && digest(hash)) && 'domain/boundary.py' in value.engine_files && 'services/scenario_simulation.py' in value.engine_files);
  check(Array.isArray(value.cash_choices) && value.cash_choices.length <= 100 && value.cash_choices.every((a) => object(a) && uuid(a.account_id) && integer(a.balance_cents) && strings(a.evidence_ids) && a.evidence_ids.length > 0 && a.evidence_ids.every(uuid)));
  check(Array.isArray(value.emergency_choices) && value.emergency_choices.length <= 100 && value.emergency_choices.every((p) => object(p) && uuid(p.policy_id) && uuid(p.version_id) && digest(p.configuration_hash) && integer(p.amount_cents) && strings(p.evidence_ids) && p.evidence_ids.length > 0 && p.evidence_ids.every(uuid)));
  check(Array.isArray(value.product_choices) && value.product_choices.length <= 100 && value.product_choices.every((p) => object(p) && uuid(p.product_id) && integer(p.version_number, 1) && typeof p.asset_class === 'string' && digest(p.terms_digest) && integer(p.minimum_purchase_cents) && integer(p.term_days, 0, 3660) && integer(p.settlement_delay_days, 0, 3660)));
  check([['cash_choices', 'account_id'], ['emergency_choices', 'policy_id'], ['product_choices', 'product_id']].every(([list, key]) => { const rows = value[list!] as Record<string, unknown>[]; return new Set(rows.map((row) => row[key!])).size === rows.length; }));
  check(strings(value.source_evidence_ids) && value.source_evidence_ids.every(uuid) && new Set(value.source_evidence_ids).size === value.source_evidence_ids.length && Array.isArray(value.source_issues) && value.source_issues.every((issue) => object(issue) && ['code', 'source_ref', 'message'].every((field) => typeof issue[field] === 'string')));
  check(object(value.audit) && value.audit.scope === 'CURRENT_LIVE_EPOCH' && value.audit.epoch_id === value.epoch_id && typeof value.audit.status === 'string' && typeof value.audit.complete === 'boolean' && object(value.audit.anchored_run_statuses));
  if (value.source_issues.length || !value.audit.complete || value.audit.status !== 'VALID') check(value.baseline_90.status === 'INSUFFICIENT_EVIDENCE' && value.baseline_365.status === 'INSUFFICIENT_EVIDENCE');
  const result = value as unknown as ScenarioContext; if (raw !== undefined) originals.set(result, raw); return result;
}
export function parseScenarioComparison(value: unknown, raw?: string): ScenarioComparison {
  assertMoneyFields(value); check(object(value) && value.schema_version === 'counterfactual-comparison-v1'); const first = header(value);
  const body = parseScenarioRequest(value.original_request); check(value.epoch_id === body.expected_epoch_id && value.source_hash === body.expected_source_hash && value.engine_hash === body.expected_engine_hash && value.horizon_days === body.horizon_days && annualDayNumber(value.local_date) === first && digest(value.request_hash) && digest(value.scenario_hash));
  boundary(value.baseline, first, body.horizon_days); boundary(value.hypothetical, first, body.horizon_days);
  check(value.delta_safe_idle_cents === (value.baseline.safe_idle_cents === null || value.hypothetical.safe_idle_cents === null ? null : value.hypothetical.safe_idle_cents - value.baseline.safe_idle_cents));
  const expected = [body.cash_change && ['CASH_BALANCE_DELTA', body.cash_change.account_id], body.product_change && ['PRODUCT_OCCUPANCY', body.product_change.product_id], body.emergency_change && ['EMERGENCY_AMOUNT', body.emergency_change.policy_id]].filter((entry): entry is string[] => Array.isArray(entry));
  check(Array.isArray(value.changed_parameters) && value.changed_parameters.length === expected.length && value.changed_parameters.every((row, index) => object(row) && row.kind === expected[index]![0] && row.entity_id === expected[index]![1] && object(row.original) && object(row.hypothetical) && row.source_is_hypothetical === true));
  for (const row of value.changed_parameters) {
    const before = row.original, after = row.hypothetical;
    if (row.kind === 'CASH_BALANCE_DELTA') { check(body.cash_change && integer(before.balance_cents) && strings(before.evidence_ids) && before.evidence_ids.every(uuid) && after.delta_cents === body.cash_change.delta_cents && integer(after.balance_cents) && BigInt(after.balance_cents as number) === BigInt(before.balance_cents as number) + BigInt(body.cash_change.delta_cents)); }
    if (row.kind === 'EMERGENCY_AMOUNT') check(body.emergency_change && before.version_id === body.emergency_change.expected_version_id && digest(before.configuration_hash) && integer(before.amount_cents) && digest(after.configuration_hash) && after.amount_cents === body.emergency_change.amount_cents);
    if (row.kind === 'PRODUCT_OCCUPANCY') check(body.product_change && before.product_id === body.product_change.product_id && before.version_number === body.product_change.expected_version_number && digest(before.terms_digest) && integer(before.term_days, 0, 3660) && integer(before.settlement_delay_days, 0, 3660) && scenarioCanonicalJson(after) === scenarioCanonicalJson(body.product_change));
  }
  const result = value as unknown as ScenarioComparison; if (raw !== undefined) originals.set(result, raw); return result;
}
export async function getScenarioContext(): Promise<ScenarioContext> { const data = await request('/scenario-simulation/context', 'GET', undefined, parseScenarioContext); check(await scenarioHash(data.engine_files) === data.engine_hash); return data; }
export async function compareScenario(context: ScenarioContext, original: ScenarioRequest): Promise<ScenarioComparison> {
  const body = parseScenarioRequest(JSON.parse(JSON.stringify(original))); check(body.expected_epoch_id === context.epoch_id && body.expected_source_hash === context.source_hash && body.expected_engine_hash === context.engine_hash);
  if (body.cash_change) { const row = context.cash_choices.find((a) => a.account_id === body.cash_change!.account_id); check(row && integer(row.balance_cents + body.cash_change.delta_cents)); }
  if (body.emergency_change) check(context.emergency_choices.some((p) => p.policy_id === body.emergency_change!.policy_id && p.version_id === body.emergency_change!.expected_version_id));
  if (body.product_change) check(context.product_choices.some((p) => p.product_id === body.product_change!.product_id && p.version_number === body.product_change!.expected_version_number));
  const data = await request('/scenario-simulation/compare', 'POST', body, parseScenarioComparison);
  check(data.user_id === context.user_id && data.timezone === context.timezone && scenarioCanonicalJson(data.original_request) === scenarioCanonicalJson(body) && await scenarioHash(body) === data.request_hash);
  for (const row of data.changed_parameters) {
    if (row.kind === 'CASH_BALANCE_DELTA') { const a = context.cash_choices.find((a) => a.account_id === row.entity_id); check(a && row.original.balance_cents === a.balance_cents && scenarioCanonicalJson(row.original.evidence_ids) === scenarioCanonicalJson(a.evidence_ids)); }
    if (row.kind === 'EMERGENCY_AMOUNT') { const p = context.emergency_choices.find((p) => p.policy_id === row.entity_id); check(p && row.original.configuration_hash === p.configuration_hash && row.original.amount_cents === p.amount_cents); }
    if (row.kind === 'PRODUCT_OCCUPANCY') { const p = context.product_choices.find((p) => p.product_id === row.entity_id); check(p && scenarioCanonicalJson(row.original) === scenarioCanonicalJson(p)); }
  }
  return data;
}
