import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { annualDayNumber } from './planning';
import { parseFullAnnualPlanning } from './full-annual';
import { parseFullGoalModel } from './full-goals';
import { parseFinancialChangePreview } from './full-policy-financial-preview';
import { productBoundaryPoints } from './full-products';
import { seasonalCanonicalJson, seasonalHash } from './seasonal-reserve-adoptions';

export type RiskRequest = components['schemas']['ScenarioRiskRequest'];
export type RiskContext = Required<components['schemas']['ScenarioRiskContext']>;
export type RiskComparison = Required<components['schemas']['ScenarioRiskComparison']>;
export type RiskCurve = components['schemas']['ScenarioRiskCurve'];
export type RiskFamily = RiskRequest['hypothesis']['kind'];
type Product = components['schemas']['AssetProductTerms'];
type Plan = components['schemas']['FullAssetPlanningResult'];
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
const hash = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const integer = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): v is number => Number.isSafeInteger(v) && Number(v) >= min && Number(v) <= max;
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((row) => typeof row === 'string');
const ids = (v: unknown): v is string[] => strings(v) && v.every(uuid) && new Set(v).size === v.length;
const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|\+00:00)$/.test(v) && Number.isFinite(Date.parse(v));
const exact = (v: Record<string, unknown>, fields: string[]) => Object.keys(v).sort().join('|') === [...fields].sort().join('|');
const same = (a: unknown, b: unknown) => seasonalCanonicalJson(a) === seasonalCanonicalJson(b);
const originals = new WeakMap<object, string>();
export const getOriginalRiskResponse = (value: object) => originals.get(value) ?? null;
function check(value: unknown): asserts value { if (!value) throw new Error('分项假设的原身份、精确金额、完整曲线或零授权边界不一致'); }
function flags(value: Record<string, unknown>) {
  check(['simulation', 'read_only', 'hypothetical_only'].every((key) => value[key] === true) && ['grants_authority', 'executes_funds', 'writes_facts', 'changes_bank_originals', 'resets_history', 'receipt_verified', 'economic_verified'].every((key) => value[key] === false) && value.future_income_in_current_cash_cents === 0 && value.future_income_in_execution_cents === 0 && value.execution_support === 'NOT_IMPLEMENTED');
}
function header(value: unknown): asserts value is Record<string, unknown> {
  check(object(value)); flags(value); assertMoneyFields(value);
  check(uuid(value.user_id) && uuid(value.epoch_id) && time(value.as_of) && hash(value.source_hash) && hash(value.engine_hash) && strings(value.limitations));
}
/** Validate the server's complete grid and arithmetic; do not re-create its financial engine. */
export function parseRiskCurve(value: unknown, first?: number): RiskCurve | null {
  if (value === null) return null;
  check(object(value) && ['READY', 'LIQUIDITY_RISK'].includes(String(value.status)) && integer(value.safe_idle_cents) && integer(value.minimum_margin_cents, Number.MIN_SAFE_INTEGER) && hash(value.curve_hash) && value.financial_capacity_is_authority === false && value.per_account_future_debit_allocation_verified === false && Array.isArray(value.calculation_trace) && value.calculation_trace.length === 1098);
  const trace = value.calculation_trace; check(object(trace[0])); const initial = first ?? annualDayNumber(trace[0].date);
  const phases = ['BEFORE_PAYMENT', 'AFTER_PAYMENT', 'AFTER_PRINCIPAL'];
  trace.forEach((row, index) => {
    check(object(row) && row.day === Math.floor(index / 3) && row.phase === phases[index % 3] && annualDayNumber(row.date) === initial + Number(row.day) && integer(row.cash_cents, Number.MIN_SAFE_INTEGER) && integer(row.margin_cents, Number.MIN_SAFE_INTEGER) && object(row.protected_cents_by_reason) && Object.values(row.protected_cents_by_reason).every((cents) => integer(cents)) && strings(row.obligation_occurrence_ids) && ids(row.principal_position_ids));
    check(BigInt(row.margin_cents) === BigInt(row.cash_cents) - Object.values(row.protected_cents_by_reason).reduce<bigint>((sum, cents) => sum + BigInt(cents as number), 0n));
  });
  const minimum = Math.min(...trace.map((row) => Number(row.margin_cents)));
  check(value.minimum_margin_cents === minimum && value.safe_idle_cents <= Math.max(0, minimum) && (value.status === 'READY' ? minimum >= 0 && value.safe_idle_cents === minimum : value.safe_idle_cents === 0));
  return value as unknown as RiskCurve;
}
export function parseRiskRequest(value: unknown): RiskRequest {
  check(object(value) && exact(value, ['expected_epoch_id', 'expected_source_hash', 'expected_engine_hash', 'hypothesis']) && uuid(value.expected_epoch_id) && hash(value.expected_source_hash) && hash(value.expected_engine_hash) && object(value.hypothesis));
  const h = value.hypothesis;
  if (h.kind === 'BILL') check(exact(h, ['kind', 'bill_id', 'remaining_due_cents', 'due_offset_days']) && uuid(h.bill_id) && integer(h.remaining_due_cents, 0, 10_000_000) && integer(h.due_offset_days, -365, 365));
  else if (h.kind === 'GOAL') check(exact(h, ['kind', 'goal_id', 'expected_version_id', 'monthly_min_cents', 'monthly_target_cents', 'monthly_max_cents', 'deadline_offset_days']) && uuid(h.goal_id) && uuid(h.expected_version_id) && ['monthly_min_cents', 'monthly_target_cents', 'monthly_max_cents'].every((key) => integer(h[key], 0, 10_000_000)) && Number(h.monthly_min_cents) <= Number(h.monthly_target_cents) && Number(h.monthly_target_cents) <= Number(h.monthly_max_cents) && integer(h.deadline_offset_days, -365, 365));
  else if (h.kind === 'FULL_POLICY') { check(exact(h, ['kind', 'policy_id', 'expected_version_id', 'configuration']) && uuid(h.policy_id) && uuid(h.expected_version_id) && object(h.configuration)); assertMoneyFields(h.configuration); seasonalCanonicalJson(h.configuration); }
  else check(h.kind === 'PRODUCT' && exact(h, ['kind', 'product_id', 'expected_version_number', 'asset_policy_id', 'expected_policy_version_id', 'risk_level', 'lock_days', 'redemption_delay_days', 'minimum_purchase_cents', 'early_withdrawal_loss_bps']) && uuid(h.product_id) && integer(h.expected_version_number, 1) && uuid(h.asset_policy_id) && uuid(h.expected_policy_version_id) && integer(h.risk_level, 0, 5) && integer(h.lock_days, 0, 365) && integer(h.redemption_delay_days, 0, 365) && integer(h.minimum_purchase_cents, 0, 10_000_000) && integer(h.early_withdrawal_loss_bps, 0, 10000));
  return value as unknown as RiskRequest;
}
async function product(value: unknown): Promise<Product> {
  check(object(value) && uuid(value.product_id) && typeof value.product_code === 'string' && value.product_code.length > 0 && integer(value.version_number, 1) && typeof value.asset_class === 'string' && integer(value.risk_level, 0, 5) && integer(value.minimum_purchase_cents) && ['lock_days', 'redemption_delay_days'].every((key) => integer(value[key], 0, 3660)) && ['annual_yield_bps', 'early_withdrawal_loss_bps'].every((key) => integer(value[key], 0, 10000)) && ['principal_fluctuation', 'auto_purchase_allowed', 'auto_redeem_allowed'].every((key) => typeof value[key] === 'boolean') && time(value.created_at) && time(value.effective_from) && (value.effective_until === null || time(value.effective_until) && Date.parse(value.effective_until) >= Date.parse(value.effective_from)) && object(value.maturity_rule) && hash(value.terms_digest) && await seasonalHash(value.maturity_rule) === value.terms_digest);
  return value as unknown as Product;
}
export async function parseRiskContext(value: unknown, raw?: string): Promise<RiskContext> {
  header(value); check(value.protocol === 'scenario-risk-context-v1' && ['UTC', 'Asia/Shanghai'].includes(String(value.timezone)) && object(value.engine_files) && Object.keys(value.engine_files).length > 0 && Object.entries(value.engine_files).every(([path, digest]) => /^(domain|services|api\/v1)\/[a-z_]+\.py$/.test(path) && hash(digest)) && await seasonalHash(value.engine_files) === value.engine_hash);
  const first = annualDayNumber(value.local_date); check(new Date(Date.parse(value.as_of as string) + (value.timezone === 'Asia/Shanghai' ? 8 * 3600000 : 0)).toISOString().slice(0, 10) === value.local_date);
  check(Array.isArray(value.bills) && value.bills.every((row) => object(row) && uuid(row.bill_id) && uuid(row.account_id) && integer(row.total_cents) && integer(row.paid_cents) && ['UNPAID', 'PARTIALLY_PAID', 'PAID', 'OVERDUE'].includes(String(row.status)) && annualDayNumber(row.statement_date) <= annualDayNumber(row.due_date) && ids(row.evidence_ids)));
  check(Array.isArray(value.goals) && Array.isArray(value.full_policies) && value.full_policies.length <= 200 && Array.isArray(value.products) && value.products.length <= 100 && ['VERIFIED', 'UNKNOWN'].includes(String(value.catalogue_status)));
  for (const goal of value.goals) {
    check(object(goal) && uuid(goal.goal_id) && object(goal.ownership_original) && object(goal.source_version)); flags(goal);
    const ownership = goal.ownership_original; check(ownership.goal_id === goal.goal_id && uuid(ownership.policy_id) && ['cash_owned_cents', 'principal_owned_cents', 'allocated_cents'].every((key) => integer(ownership[key])) && ids(ownership.evidence_ids));
    const v = goal.source_version; check(v.policy_id === ownership.policy_id && uuid(v.version_id) && object(v.configuration) && v.configuration.type === 'goal_saving' && hash(v.content_hash) && await seasonalHash(v.configuration) === v.content_hash && time(v.confirmed_at) && time(v.valid_from) && (v.valid_until === null || time(v.valid_until)) && ids(v.evidence_ids) && (goal.full_model_issue === null || typeof goal.full_model_issue === 'string'));
    if (goal.full_model !== null) parseFullGoalModel(goal.full_model, { id: goal.goal_id, policy_id: v.policy_id, policy_version_id: v.version_id });
  }
  for (const policy of value.full_policies) { check(object(policy) && uuid(policy.policy_id) && uuid(policy.version_id) && typeof policy.template_name === 'string' && object(policy.configuration) && hash(policy.configuration_hash) && await seasonalHash(policy.configuration) === policy.configuration_hash && ['ACTIVE', 'CONFIRMED', 'SUSPENDED', 'REVOKED', 'EXPIRED', 'ARCHIVED'].includes(String(policy.effective_status)) && typeof policy.planning_confirmation_valid === 'boolean'); flags(policy); }
  for (const row of value.products) await product(row);
  for (const [list, key] of [['bills', 'bill_id'], ['goals', 'goal_id'], ['full_policies', 'policy_id'], ['products', 'product_id']] as const) { const rows = value[list] as Record<string, unknown>[]; check(new Set(rows.map((row) => row[key])).size === rows.length); }
  check(object(value.inventory_counts) && exact(value.inventory_counts, ['bills', 'goals', 'goal_choices', 'full_policies', 'catalogue_products', 'eligible_product_choices']) && Object.values(value.inventory_counts).every((count) => integer(count)) && value.inventory_counts.bills === value.bills.length && Number(value.inventory_counts.goals) >= value.goals.length && value.inventory_counts.goal_choices === value.goals.length && value.inventory_counts.full_policies === value.full_policies.length && value.inventory_counts.catalogue_products === value.products.length && value.inventory_counts.eligible_product_choices === (value.catalogue_status === 'VERIFIED' ? value.products.length : 0) && (value.catalogue_status === 'VERIFIED' || value.products.length === 0));
  const full = parseFullAnnualPlanning(value.original_full_protection); check(full.user_id === value.user_id && full.as_of === value.as_of && full.audit.epoch_id === value.epoch_id);
  const curve = parseRiskCurve(value.baseline_full, first); check((curve === null) === (full.projection.full_annual_projection === null));
  if (curve !== null) { const original = full.projection.full_annual_projection; check(original && curve.safe_idle_cents === original.safe_idle_cents && curve.minimum_margin_cents === original.minimum_margin_cents && same(curve.calculation_trace, original.calculation_trace)); }
  check(Array.isArray(value.source_issues) && value.source_issues.every((row) => object(row) && ['code', 'source_ref', 'message'].every((key) => typeof row[key] === 'string')));
  const result = value as unknown as RiskContext; if (raw !== undefined) originals.set(result, raw); return result;
}
function plan(value: unknown, expectedPolicy: string, expectedVersion: string, products: Product[], asOf: string): Plan {
  check(object(value) && value.algorithm_version === 'finite-integer-original-product-portfolio-v1' && value.policy_id === expectedPolicy && value.policy_version_id === expectedVersion && ['general_idle_funds', 'goal'].includes(String(value.scope)) && (value.scope === 'goal' ? uuid(value.goal_id) : value.goal_id === null) && value.planning_only === true && value.bank_authority === false && value.execution_support === 'NOT_IMPLEMENTED' && value.future_income_included_cents === 0 && hash(value.input_hash) && ['OPTIMAL', 'NO_PURCHASE', 'UNKNOWN', 'INACTIVE_POLICY', 'INSUFFICIENT_EVIDENCE', 'LIQUIDITY_RISK'].includes(String(value.status)) && strings(value.reasons) && Array.isArray(value.candidates) && Array.isArray(value.batches) && value.batches.length <= 4);
  productBoundaryPoints(value.baseline_boundary); if (value.projected_boundary !== null) productBoundaryPoints(value.projected_boundary);
  const match = (row: Record<string, unknown>) => products.find((p) => p.product_id === row.product_id && p.product_code === row.product_code && p.version_number === row.version_number && p.terms_digest === row.terms_digest);
  value.candidates.forEach((row) => check(object(row) && match(row) && ['FEASIBLE', 'REJECTED', 'RETAIN_CASH'].includes(String(row.status)) && row.bank_auto_eligible === false && strings(row.reasons) && ['financial_cap_cents', 'maximum_batch_cents'].every((key) => row[key] === null || integer(row[key]))));
  value.batches.forEach((row) => { check(object(row) && match(row) && row.bank_authority === false && integer(row.amount_cents, 1) && integer(row.net_simulated_yield_cents) && time(row.purchase_at) && Date.parse(row.purchase_at) === Date.parse(asOf) && time(row.principal_available_at) && Date.parse(row.principal_available_at) >= Date.parse(asOf) && object(row.exit_plan) && row.exit_plan.terms_digest === row.terms_digest && row.exit_plan.principal_available_at === row.principal_available_at && Array.isArray(row.cash_uses) && row.cash_uses.every((use) => object(use) && uuid(use.account_id) && integer(use.amount_cents, 1))); check(row.cash_uses.reduce<bigint>((sum, use) => sum + BigInt(use.amount_cents as number), 0n) === BigInt(row.amount_cents)); });
  check(new Set(value.candidates.map((row) => row.product_id)).size === value.candidates.length && new Set(value.batches.map((row) => row.product_id)).size === value.batches.length);
  if (['OPTIMAL', 'NO_PURCHASE'].includes(String(value.status))) check(value.purchase_count === value.batches.length && value.total_purchase_cents === value.batches.reduce((sum, row) => sum + Number(row.amount_cents), 0) && value.net_simulated_yield_cents === value.batches.reduce((sum, row) => sum + Number(row.net_simulated_yield_cents), 0));
  else check(value.batches.length === 0 && value.total_purchase_cents === null && value.net_simulated_yield_cents === null && value.purchase_count === null);
  return value as unknown as Plan;
}
export async function parseRiskComparison(value: unknown, context: RiskContext, submitted: RiskRequest, raw?: string): Promise<RiskComparison> {
  header(value); const body = parseRiskRequest(submitted); const h = body.hypothesis;
  check(value.protocol === 'scenario-risk-comparison-v1' && value.user_id === context.user_id && value.epoch_id === context.epoch_id && value.epoch_id === body.expected_epoch_id && value.source_hash === context.source_hash && value.source_hash === body.expected_source_hash && value.engine_hash === context.engine_hash && value.engine_hash === body.expected_engine_hash && same(value.original_request, body) && value.request_hash === await seasonalHash(body) && hash(value.comparison_hash) && ['PROJECTED', 'UNKNOWN'].includes(String(value.status)) && value.family === h.kind && object(value.original_selected) && object(value.hypothetical_selected) && strings(value.reasons));
  const baseline = parseRiskCurve(value.baseline_full, annualDayNumber(context.local_date)); const candidate = parseRiskCurve(value.hypothetical_full, annualDayNumber(context.local_date)); check(same(baseline, context.baseline_full));
  let projected = false;
  if (h.kind === 'BILL' || h.kind === 'GOAL') {
    check(value.full_policy_preview === null && value.product_preview === null && value.delta_safe_idle_cents === (baseline && candidate ? candidate.safe_idle_cents - baseline.safe_idle_cents : null));
    if (h.kind === 'BILL') {
      const source = context.bills.find((row) => row.bill_id === h.bill_id); check(source && same(value.original_selected, source) && value.hypothetical_selected.kind === 'BILL' && value.hypothetical_selected.bill_id === h.bill_id && value.hypothetical_selected.remaining_due_cents === h.remaining_due_cents && value.hypothetical_selected.due_offset_days === h.due_offset_days && value.hypothetical_selected.original_paid_cents_retained === source.paid_cents && value.hypothetical_selected.original_overdue_retained === (source.status === 'OVERDUE') && annualDayNumber(value.hypothetical_selected.hypothetical_due_date) === annualDayNumber(source.due_date) + h.due_offset_days);
    } else {
      const source = context.goals.find((row) => row.goal_id === h.goal_id); check(source && source.source_version.version_id === h.expected_version_id && same(value.original_selected, source.source_version) && object(value.hypothetical_selected.configuration) && value.hypothetical_selected.hypothetical_only === true && hash(value.hypothetical_selected.configuration_hash) && await seasonalHash(value.hypothetical_selected.configuration) === value.hypothetical_selected.configuration_hash);
      const c = value.hypothetical_selected.configuration; check(object(c.monthly_contribution) && c.monthly_contribution.min_cents === h.monthly_min_cents && c.monthly_contribution.target_cents === h.monthly_target_cents && c.monthly_contribution.max_cents === h.monthly_max_cents && annualDayNumber(c.deadline) === annualDayNumber(source.source_version.configuration.deadline) + h.deadline_offset_days);
    }
    projected = candidate !== null;
  } else if (h.kind === 'FULL_POLICY') {
    check(candidate === null && value.product_preview === null);
    if (value.full_policy_preview !== null) { const source = context.full_policies.find((row) => row.policy_id === h.policy_id); const p = await parseFinancialChangePreview(value.full_policy_preview, h.policy_id, { expected_version_id: h.expected_version_id, configuration: h.configuration }); check(source && source.version_id === h.expected_version_id && same(source.configuration, p.before_configuration) && p.epoch_id === context.epoch_id && p.as_of === value.as_of && same(p.financial_impact.before, context.original_full_protection.projection.full_annual_projection) && same(p.before_configuration, value.original_selected) && same(p.after_configuration, value.hypothetical_selected) && value.delta_safe_idle_cents === p.financial_impact.delta_safe_idle_cents); projected = p.financial_impact.status === 'PROJECTED'; }
    else check(value.delta_safe_idle_cents === null);
  } else {
    check(candidate === null && value.full_policy_preview === null && value.delta_safe_idle_cents === null);
    if (value.product_preview !== null) {
      const p = value.product_preview; check(object(p) && p.loss_basis === 'CEILING_SELECTED_PRINCIPAL_TIMES_DECLARED_BPS' && p.actual_fee_cents === null && p.actual_loss_cents === null && p.early_loss_consumed_by_optimizer === false && p.full_protection_consumed_by_optimizer === false && p.original_positions_unchanged === true && p.quotation_verified === false);
      const beforeProduct = await product(p.original_product); const afterProduct = await product(p.hypothetical_product); const source = context.products.find((row) => row.product_id === h.product_id);
      check(source && same(beforeProduct, source) && beforeProduct.version_number === h.expected_version_number && afterProduct.product_id === h.product_id && afterProduct.version_number === h.expected_version_number && ['risk_level', 'lock_days', 'redemption_delay_days', 'minimum_purchase_cents', 'early_withdrawal_loss_bps'].every((key) => afterProduct[key as keyof Product] === h[key as keyof typeof h]) && same(value.original_selected, beforeProduct) && same(value.hypothetical_selected, afterProduct));
      const before = plan(p.before, h.asset_policy_id, h.expected_policy_version_id, context.products, value.as_of as string); const after = plan(p.after, h.asset_policy_id, h.expected_policy_version_id, context.products.map((row) => row.product_id === h.product_id ? afterProduct : row), value.as_of as string);
      const principal = (a: Plan) => (a.batches ?? []).filter((row) => row.product_id === h.product_id).reduce((sum, row) => sum + row.amount_cents, 0);
      for (const [name, a, productRow] of [['original', before, beforeProduct], ['hypothetical', after, afterProduct]] as const) { const amount = principal(a); check(p[`${name}_selected_principal_cents`] === amount && p[`${name}_early_loss_upper_bound_cents`] === Number((BigInt(amount) * BigInt(productRow.early_withdrawal_loss_bps) + 9999n) / 10000n)); }
      projected = ['OPTIMAL', 'NO_PURCHASE', 'LIQUIDITY_RISK'].includes(after.status);
    }
  }
  check(value.status === (projected ? 'PROJECTED' : 'UNKNOWN') && (projected || value.reasons.length > 0));
  check(await seasonalHash({ protocol: 'scenario-risk-comparison-v1', request_hash: value.request_hash, source_hash: value.source_hash, engine_hash: value.engine_hash, as_of: (value.as_of as string).replace(/Z$/, '+00:00'), curve: value.hypothetical_full, full: value.full_policy_preview, product: value.product_preview }) === value.comparison_hash);
  const result = value as unknown as RiskComparison; if (raw !== undefined) originals.set(result, raw); return result;
}
export async function getRiskContext(): Promise<RiskContext> { const original = await request('/scenario-risk-review/context', 'GET', undefined, (value, raw) => ({ value, raw })); return parseRiskContext(original.value, original.raw); }
export async function compareRisk(context: RiskContext, original: RiskRequest): Promise<RiskComparison> {
  const body = parseRiskRequest(structuredClone(original)); check(body.expected_epoch_id === context.epoch_id && body.expected_source_hash === context.source_hash && body.expected_engine_hash === context.engine_hash);
  const h = body.hypothesis;
  check(h.kind === 'BILL' ? context.bills.some((row) => row.bill_id === h.bill_id) : h.kind === 'GOAL' ? context.goals.some((row) => row.goal_id === h.goal_id && row.source_version.version_id === h.expected_version_id) : h.kind === 'FULL_POLICY' ? context.full_policies.some((row) => row.policy_id === h.policy_id && row.version_id === h.expected_version_id) : context.catalogue_status === 'VERIFIED' && context.products.some((row) => row.product_id === h.product_id && row.version_number === h.expected_version_number) && context.full_policies.some((row) => row.policy_id === h.asset_policy_id && row.version_id === h.expected_policy_version_id && row.template_name === 'AssetAuthorizationPolicy'));
  const data = await request('/scenario-risk-review/compare', 'POST', body, (value, raw) => ({ value, raw })); return parseRiskComparison(data.value, context, body, data.raw);
}
