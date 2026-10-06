import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { getDashboard } from './dashboard';
import { getPolicies } from './policies';
import { getFullPolicies } from './full-policies';
import { annualDayNumber, validateAnnualBoundary } from './planning';
import { releaseCanonicalJson } from './goal-release-authorizations';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';

export type MultiPreviewBody = components['schemas']['MultiTemplatePreviewRequest'];
type NativePreview = components['schemas']['MultiTemplatePreviewResponse'];
type NativeImpact = Required<NativePreview['financial_impact']>;
export type MultiPreview = Omit<Required<NativePreview>, 'financial_impact'> & { financial_impact: NativeImpact };
export type MultiPreviewSource = { sourceKind: 'MVP_POLICY' | 'FULL_POLICY'; policyId: string; versionId: string; userId: string; epochId: string; name: string; template: string; configuration: Record<string, unknown>; eligible: boolean; reason: string };
export type MultiPreviewInventory = { userId: string; epochId: string | null; asOf: string; items: MultiPreviewSource[] };
const originals = new WeakMap<object, string>();
export const getOriginalMultiPreview = (value: object): string | null => originals.get(value) ?? null;
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
const hash = (v: unknown) => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === 'string');
const ids = (v: unknown): v is string[] => strings(v) && v.every(uuid) && new Set(v).size === v.length;
const integer = (v: unknown, min = Number.MIN_SAFE_INTEGER): v is number => Number.isSafeInteger(v) && Number(v) >= min;
const nullable = (v: unknown, test: (x: unknown) => boolean) => v === null || test(v);
const phases = ['BEFORE_PAYMENT', 'AFTER_PAYMENT', 'AFTER_PRINCIPAL'];
const mvpTemplates: Record<string, string> = { recurring_obligation: 'RecurringObligationPolicy', living_reserve: 'LivingReservePolicy', emergency_buffer: 'EmergencyBufferPolicy', goal_saving: 'LongTermGoalPolicy', asset_authorization: 'AssetAuthorizationPolicy' };
function check(v: unknown): asserts v { if (!v) throw new Error('多模板预览的来源、完整分母、金额或无授权边界未通过校验'); }
/** Only the original Living/Seasonal quantile is a typed float; money is always integer cents. */
export async function multiConfigurationHash(configuration: Record<string, unknown>): Promise<string> {
  function canonical(v: unknown): string {
    if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`;
    if (object(v)) return `{${Object.keys(v).sort().map((k) => {
      if (k === 'quantile' && (v.type === 'seasonal_reserve' || v.name === 'rolling_window_quantile')) {
        const q = v[k]; check(typeof q === 'number' && Number.isFinite(q) && q > 0 && q <= 1);
        const text = q < 0.0001 ? q.toExponential().replace(/e(-?)(\d)$/, 'e$10$2') : Number.isInteger(q) ? `${q}.0` : String(q);
        return `${JSON.stringify(k)}:${text}`;
      }
      return `${JSON.stringify(k)}:${canonical(v[k])}`;
    }).join(',')}}`;
    return releaseCanonicalJson(v);
  }
  check(crypto.subtle); return [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(configuration))))].map((n) => n.toString(16).padStart(2, '0')).join('');
}
function delta(after: unknown, before: unknown, value: unknown) { check(integer(after) && integer(before) && integer(value) && BigInt(after) - BigInt(before) === BigInt(value)); }
function sameCandidate(submitted: unknown, actual: unknown): boolean {
  if (Array.isArray(submitted)) return Array.isArray(actual) && submitted.length === actual.length && submitted.every((v, i) => sameCandidate(v, actual[i]));
  if (object(submitted)) return object(actual) && Object.entries(submitted).every(([k, v]) => Object.hasOwn(actual, k) && sameCandidate(v, actual[k]));
  return time(submitted) && time(actual) ? Date.parse(submitted) === Date.parse(actual) : submitted === actual;
}
/** The editor can change only fields in the actual current configuration, never a fact envelope. */
export function parseMultiPreviewCandidate(text: string, current: Record<string, unknown>): Record<string, unknown> {
  const candidate: unknown = JSON.parse(text);
  function fields(before: unknown, after: unknown, depth = 0): boolean {
    if (depth > 32) return false;
    if (object(before)) return object(after) && Object.keys(before).sort().join('|') === Object.keys(after).sort().join('|') && Object.entries(before).every(([k, v]) => fields(v, after[k], depth + 1));
    if (Array.isArray(before)) return Array.isArray(after) && after.every((v) => before.length ? fields(before[0], v, depth + 1) : typeof v === 'string');
    if (typeof after === 'number' && !Number.isFinite(after)) return false;
    return before === null ? after === null || ['string', 'number', 'boolean'].includes(typeof after) : typeof before === typeof after && (!object(after) && !Array.isArray(after));
  }
  check(object(candidate) && fields(current, candidate) && candidate.type === current.type); assertMoneyFields(candidate);
  // Validate canonical JSON including finite ratios; the server performs the actual DSL and reference checks.
  check(JSON.stringify(candidate).length <= 100_000); return candidate;
}
export async function readMultiPreviewSources(): Promise<MultiPreviewInventory> {
  const [dashboard, mvp, full] = await Promise.all([getDashboard(), getPolicies(), getFullPolicies()]);
  check(uuid(dashboard.user_id)); const epoch = dashboard.audit.epoch_id;
  const live = uuid(epoch) && dashboard.audit.scope === 'CURRENT_LIVE_EPOCH' && dashboard.audit.status === 'VALID' && dashboard.audit.complete;
  const items: MultiPreviewSource[] = [];
  for (const policy of mvp.items) {
    const v = policy.current_version; if (!v) continue;
    const c = v.confirmation;
    const bound = live && uuid(policy.id) && uuid(v.id) && v.policy_id === policy.id && c.user_id === dashboard.user_id && c.policy_id === policy.id && c.version_id === v.id && c.accepted === true && c.reviewed_hash === v.content_hash && hash(v.content_hash) && ['ACTIVE', 'CONFIRMED'].includes(policy.effective_status) && policy.version_authorized && !!mvpTemplates[policy.policy_type];
    items.push({ sourceKind: 'MVP_POLICY', policyId: policy.id, versionId: v.id, userId: dashboard.user_id, epochId: epoch ?? '', name: policy.name, template: mvpTemplates[policy.policy_type] ?? 'UNKNOWN', configuration: v.configuration, eligible: !!bound, reason: bound ? '当前来源；服务器将再次验证OPEN期和原确认链' : '原确认归属、当前版本或当前期未知/失效，不能预览' });
  }
  for (const policy of full.items) {
    const v = policy.current_version;
    const bound = live && policy.epoch_id === epoch && v.confirmation.user_id === dashboard.user_id && policy.planning_confirmation_valid && policy.reference_validation === 'CURRENT';
    items.push({ sourceKind: 'FULL_POLICY', policyId: policy.policy_id, versionId: v.version_id, userId: dashboard.user_id, epochId: policy.epoch_id, name: policy.name, template: policy.template_name, configuration: v.configuration, eligible: !!bound, reason: bound ? '当前来源；服务器将再次验证OPEN期和原命令链' : '原owner/期/版本引用未知或失效，不能预览' });
  }
  check(new Set(items.map((v) => `${v.sourceKind}:${v.policyId}`)).size === items.length);
  return { userId: dashboard.user_id, epochId: live ? epoch : null, asOf: dashboard.as_of, items };
}
function curve(v: unknown, horizon = 365) {
  check(object(v) && Array.isArray(v.calculation_trace));
  if (v.status === 'INSUFFICIENT_EVIDENCE') { validateAnnualBoundary(v, 0, horizon); return; }
  check(v.calculation_trace.length === (horizon + 1) * 3 && object(v.calculation_trace[0]));
  validateAnnualBoundary(v, annualDayNumber(v.calculation_trace[0].date), horizon);
  for (const [i, row] of v.calculation_trace.entries()) {
    check(object(row) && row.day === Math.floor(i / 3) && row.phase === phases[i % 3] && object(row.protected_cents_by_reason));
    check(BigInt(row.margin_cents as number) === BigInt(row.cash_cents as number) - Object.values(row.protected_cents_by_reason).reduce<bigint>((s, n) => { check(integer(n, 0)); return s + BigInt(n); }, 0n));
  }
  check(v.minimum_margin_cents === Math.min(...v.calculation_trace.map((r) => Number(r.margin_cents))) && integer(v.safe_idle_cents, 0) && Number(v.safe_idle_cents) <= Math.max(0, Number(v.minimum_margin_cents)));
  check(object(v.max_allocatable_by_product) && Object.entries(v.max_allocatable_by_product).every(([id, n]) => uuid(id) && nullable(n, (x) => integer(x, 0))));
}
function goalResult(v: unknown): Set<string> {
  check(object(v) && v.algorithm_version === 'critical-flow-lexicographic-v1' && v.purpose === 'CURRENT_PERIOD_PLANNING_ONLY' && v.grants_authority === false && hash(v.input_hash) && ['OPTIMAL', 'INFEASIBLE', 'UNKNOWN'].includes(String(v.status)) && integer(v.budget_cents, 0) && integer(v.visited_nodes, 0) && strings(v.reasons) && v.delay_scope === 'ACTIVE_INCOMPLETE_GOALS_CURRENT_DECISION_LOWER_BOUND' && Array.isArray(v.goals) && v.goals.length <= 8 && Array.isArray(v.income_uses));
  const optimal = v.status === 'OPTIMAL'; check(optimal ? Array.isArray(v.objective_vector) && v.objective_vector.length === 8 && v.objective_vector.every((x) => integer(x, 0)) : v.objective_vector === null);
  const found = new Set<string>();
  for (const row of v.goals) { check(object(row) && uuid(row.goal_id) && !found.has(row.goal_id) && uuid(row.effective_policy_version_id) && typeof row.delay_censored === 'boolean'); found.add(row.goal_id);
    for (const k of ['amount_cents', 'minimum_shortfall_cents', 'projected_owned_cents', 'delay_lower_bound_days', 'deferral_cost_lower_bound_cents']) check(optimal ? integer(row[k], 0) : row[k] === null);
    check(nullable(row.completion_date, (d) => Number.isFinite(annualDayNumber(d))) && (!row.delay_censored || row.completion_date === null));
  }
  check(v.income_uses.every((r) => object(r) && uuid(r.fragment_id) && uuid(r.origin_transaction_id) && uuid(r.source_account_id) && typeof r.goal_id === 'string' && found.has(r.goal_id) && integer(r.amount_cents, 1)));
  if (optimal) { check(v.income_uses.reduce<bigint>((s, r) => s + BigInt(r.amount_cents as number), 0n) <= BigInt(v.budget_cents)); for (const row of v.goals) check(v.income_uses.filter((r) => r.goal_id === row.goal_id).reduce<bigint>((s, r) => s + BigInt(r.amount_cents as number), 0n) === BigInt(row.amount_cents as number)); }
  else check(v.income_uses.length === 0);
  return found;
}
function recovery(v: unknown, position: string) {
  check(object(v) && v.position_id === position && uuid(v.destination_account_id) && uuid(v.product_id) && nullable(v.goal_id, uuid) && uuid(v.catalogue_version_id) && hash(v.product_record_hash) && hash(v.terms_digest) && integer(v.product_version_number, 1) && integer(v.principal_cents, 0) && strings(v.reasons) && v.bank_authority === false && ['ASK_ONCE', 'BLOCKED', 'UNKNOWN', 'EXCLUDED_SCOPE'].includes(String(v.decision)));
  for (const key of ['independent_loss_cents', 'fee_cents', 'net_cents', 'liquidity_rank']) check(nullable(v[key], (x) => integer(x, 0)));
  check(nullable(v.earliest_conditional_cash_at, time) && nullable(v.on_time, (x) => typeof x === 'boolean') && typeof v.lossless_eligible === 'boolean' && typeof v.within_full_planning_limits === 'boolean' && ['BANK_CONFIRMED', 'DERIVED_ORIGINAL_TERMS', 'MISSING'].includes(String(v.quote_source)) && nullable(v.original_quote, object));
  if (v.conditional_impact_boundary !== null) curve(v.conditional_impact_boundary, 90);
}
export async function parseMultiPreview(value: unknown, source: MultiPreviewSource, body: MultiPreviewBody, raw?: string): Promise<MultiPreview> {
  check(source.eligible && uuid(source.policyId) && uuid(source.versionId) && uuid(source.userId) && uuid(source.epochId));
  check(Object.keys(body).sort().join('|') === 'configuration|expected_epoch_id|expected_version_id' && body.expected_version_id === source.versionId && body.expected_epoch_id === source.epochId);
  check(object(value) && value.protocol === 'full-policy-multi-template-change-v3' && value.simulation === true && value.hypothetical === true && value.preview_only === true && value.grants_authority === false && value.bank_authority === false && value.writes_policy_or_bank === false);
  check(value.user_id === source.userId && value.epoch_id === source.epochId && value.policy_id === source.policyId && value.expected_version_id === source.versionId && value.source_kind === source.sourceKind && value.template_name === source.template && time(value.as_of) && hash(value.current_fact_digest) && object(value.source_originals));
  check(object(value.before_configuration) && object(value.after_configuration) && sameCandidate(body.configuration, value.after_configuration) && await multiConfigurationHash(value.before_configuration) === value.current_configuration_hash && await multiConfigurationHash(value.after_configuration) === value.candidate_configuration_hash && await multiConfigurationHash(source.configuration) === value.current_configuration_hash && strings(value.changed_fields));
  check(ids(value.original_action_ids) && ids(value.original_position_ids) && ids(value.source_evidence_ids) && strings(value.limitations) && object(value.source_counts) && Object.values(value.source_counts).every((x) => integer(x, 0)) && value.source_counts.actions === value.original_action_ids.length && value.source_counts.positions === value.original_position_ids.length);
  const counts = value.source_counts;
  check(['actions', 'positions', 'boundary_products', 'boundary_versions', 'full_protection_sources', 'evidence', 'goals', 'asset_catalogue', 'recovery_holdings', 'joint_goals'].every((k) => integer(counts[k], 0)));
  const p = value.financial_impact;
  check(object(p) && p.protocol === 'full-policy-multi-template-impact-v3' && p.simulation === true && p.hypothetical === true && p.grants_authority === false && p.bank_authority === false && p.writes_policy_or_bank === false && p.future_income_in_current_cash_cents === 0 && p.future_income_in_execution_cents === 0 && p.current_owned_cash_delta_cents === 0 && p.current_position_principal_delta_cents === 0 && p.future_action_delta === 'UNKNOWN_REQUIRES_FRESH_EXECUTION_RECOMPUTATION' && p.horizon_days === 365 && p.phase_denominator === 1098 && p.template_name === source.template && hash(p.input_hash) && strings(p.reasons) && strings(p.limitations) && ['PROJECTED', 'PARTIAL', 'UNKNOWN'].includes(String(p.status)));
  // These two native *_cents fields are maps, not scalar amounts. Validate every map value below.
  assertMoneyFields({ ...p, delta_product_financial_capacity_cents: null, goal_allocation_delta_cents: null });
  check(['MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS', 'INDIVIDUAL_PRODUCT_CAPACITY', 'WHOLE_POSITION_RECOVERY_CANDIDATES', 'CURRENT_JOINT_GOAL_ALLOCATION', 'UNSUPPORTED'].includes(String(p.scope)) && Array.isArray(p.product_capacities) && Array.isArray(p.recovery_candidates));
  if (p.before !== null) curve(p.before);
  if (p.status === 'UNKNOWN') check(p.reasons.length > 0 && p.after === null && p.delta_safe_idle_cents === null && p.delta_minimum_margin_cents === null && p.delta_product_financial_capacity_cents === null && p.product_capacities.length === 0 && p.recovery_candidates.length === 0 && p.goal_allocation_before === null && p.goal_allocation_after === null && p.goal_allocation_delta_cents === null);
  else {
    check(object(p.before) && p.before.status !== 'INSUFFICIENT_EVIDENCE' && object(p.after) && p.after.status !== 'INSUFFICIENT_EVIDENCE'); curve(p.after);
    delta(p.after.safe_idle_cents, p.before.safe_idle_cents, p.delta_safe_idle_cents); delta(p.after.minimum_margin_cents, p.before.minimum_margin_cents, p.delta_minimum_margin_cents);
    const afterTrace = p.after.calculation_trace; check(Array.isArray(p.before.calculation_trace) && Array.isArray(afterTrace) && p.before.calculation_trace.every((r, i) => object(r) && object(afterTrace[i]) && r.date === afterTrace[i].date));
    if (p.delta_product_financial_capacity_cents !== null) { check(object(p.delta_product_financial_capacity_cents) && object(p.before.max_allocatable_by_product) && object(p.after.max_allocatable_by_product)); const keys = Object.keys(p.before.max_allocatable_by_product).sort().join('|'); check(keys === Object.keys(p.after.max_allocatable_by_product).sort().join('|') && keys === Object.keys(p.delta_product_financial_capacity_cents).sort().join('|')); for (const id of Object.keys(p.delta_product_financial_capacity_cents)) delta(p.after.max_allocatable_by_product[id], p.before.max_allocatable_by_product[id], p.delta_product_financial_capacity_cents[id]); }
    if (p.status === 'PROJECTED') check(p.scope === 'MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS' && p.delta_product_financial_capacity_cents !== null);
    else check(p.reasons.length > 0 && p.scope !== 'UNSUPPORTED' && p.scope !== 'MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS');
  }
  const productIds = new Set<string>(); for (const row of p.product_capacities) { check(object(row) && uuid(row.product_id) && !productIds.has(row.product_id) && integer(row.product_version, 1) && hash(row.terms_digest) && strings(row.before_reasons) && strings(row.after_reasons) && row.individual_capacity_not_portfolio_sum === true && row.grants_authority === false); productIds.add(row.product_id); check(nullable(row.before_capacity_cents, (x) => integer(x, 0)) && nullable(row.after_capacity_cents, (x) => integer(x, 0))); if (row.before_capacity_cents === null || row.after_capacity_cents === null) check(row.delta_cents === null); else delta(row.after_capacity_cents, row.before_capacity_cents, row.delta_cents); }
  const positionIds = new Set<string>(); for (const row of p.recovery_candidates) { check(object(row) && uuid(row.position_id) && value.original_position_ids.includes(row.position_id) && !positionIds.has(row.position_id) && row.conditional_cash_not_current_cash === true && row.conditional_boundary_scope === 'ORIGINAL_MVP_ONLY_NOT_FULL_RECOVERY_CURVE'); positionIds.add(row.position_id); recovery(row.before, row.position_id); recovery(row.after, row.position_id);
    function onTimeAmount(v: unknown): number | null { check(object(v)); if (v.decision === 'UNKNOWN' || v.on_time === null) return null; if (!v.lossless_eligible || !v.on_time) return 0; check(integer(v.net_cents, 0)); return v.net_cents; }
    const old = onTimeAmount(row.before), next = onTimeAmount(row.after); if (old === null || next === null) check(row.conditional_on_time_net_delta_cents === null); else delta(next, old, row.conditional_on_time_net_delta_cents);
  }
  if (p.scope === 'INDIVIDUAL_PRODUCT_CAPACITY' && p.status === 'PARTIAL') check(productIds.size === value.source_counts.asset_catalogue);
  else check(productIds.size === 0);
  if (p.scope === 'WHOLE_POSITION_RECOVERY_CANDIDATES' && p.status === 'PARTIAL') check(positionIds.size === value.source_counts.recovery_holdings);
  else check(positionIds.size === 0);
  if (p.scope === 'CURRENT_JOINT_GOAL_ALLOCATION' && p.status === 'PARTIAL') { const before = goalResult(p.goal_allocation_before), after = goalResult(p.goal_allocation_after); check(before.size === value.source_counts.joint_goals && before.size === after.size && [...before].every((id) => after.has(id)) && object(p.goal_allocation_delta_cents) && Object.keys(p.goal_allocation_delta_cents).length === before.size); const old = p.goal_allocation_before as Record<string, unknown>; const next = p.goal_allocation_after as Record<string, unknown>; for (const id of before) { const a = (old.goals as Record<string, unknown>[]).find((r) => r.goal_id === id)!, b = (next.goals as Record<string, unknown>[]).find((r) => r.goal_id === id)!; if (a.amount_cents === null || b.amount_cents === null) check(p.goal_allocation_delta_cents[id] === null); else delta(b.amount_cents, a.amount_cents, p.goal_allocation_delta_cents[id]); } }
  else check(p.goal_allocation_before === null && p.goal_allocation_after === null && p.goal_allocation_delta_cents === null);
  const result = value as unknown as MultiPreview; if (raw !== undefined) originals.set(result, raw); return result;
}
export async function previewMultiTemplate(source: MultiPreviewSource, configuration: Record<string, unknown>): Promise<MultiPreview> {
  check(source.eligible && uuid(source.policyId)); parseMultiPreviewCandidate(JSON.stringify(configuration), source.configuration);
  const body: MultiPreviewBody = { expected_version_id: source.versionId, expected_epoch_id: source.epochId, configuration };
  const response = await request<{ simulation: true; value: unknown; raw: string }>(`/policy-financial-previews/${source.sourceKind}/${source.policyId}`, 'POST', body, (value, raw) => ({ simulation: true, value, raw }));
  return parseMultiPreview(response.value, source, body, response.raw);
}
