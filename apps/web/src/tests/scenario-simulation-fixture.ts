/** SYNTHETIC_HTTP_ONLY: constructed client-shape fixtures, never financial-engine/PG/browser evidence. */
import { createHash } from 'node:crypto';
import type { components } from '../../../../packages/contracts/schema';
import type { ScenarioComparison, ScenarioContext, ScenarioRequest } from '../api/scenario-simulation';
import { scenarioCanonicalJson } from '../api/scenario-simulation';
export const simUser = '92000000-0000-4000-8000-000000000001';
export const simEpoch = '92000000-0000-4000-8000-000000000002';
export const simAccount = '92000000-0000-4000-8000-000000000003';
export const simPolicy = '92000000-0000-4000-8000-000000000004';
export const simVersion = '92000000-0000-4000-8000-000000000005';
export const simProduct = '92000000-0000-4000-8000-000000000006';
export const simEvidence = '92000000-0000-4000-8000-000000000007';
export const simNow = '2026-10-05T00:00:00Z';
const hash = (input: unknown) => createHash('sha256').update(scenarioCanonicalJson(input)).digest('hex');
const flags = { simulation: true as const, read_only: true as const, hypothetical_only: true as const, grants_authority: false as const, executes_funds: false as const, writes_facts: false as const, resets_history: false as const, receipt_verified: false as const, economic_verified: false as const, future_income_included_cents: 0 as const, execution: 'NOT_IMPLEMENTED' as const };
function shapeBoundary(horizon: number, unknown = false, cash = 100007, protection = 50005): components['schemas']['BoundaryResult'] {
  const trace: components['schemas']['BoundaryPoint'][] = [];
  if (!unknown) for (let day = 0; day <= horizon; day++) for (const phase of ['BEFORE_PAYMENT', 'AFTER_PAYMENT', 'AFTER_PRINCIPAL'] as const) trace.push({ day, date: new Date(Date.parse(simNow) + day * 86400000).toISOString().slice(0, 10), phase, cash_cents: cash, protected_cents_by_reason: { synthetic_protection: protection }, margin_cents: cash - protection, obligation_occurrence_ids: [], principal_position_ids: [] });
  const min = cash - protection;
  return { algorithm_version: 'strict-cash-boundary-v1', financial_only: true, status: unknown ? 'INSUFFICIENT_EVIDENCE' : min < 0 ? 'LIQUIDITY_RISK' : 'READY', safe_idle_cents: unknown ? null : Math.max(0, min), minimum_margin_cents: unknown ? null : min, deficit_cents: unknown ? null : Math.max(0, -min), protected_cents_by_reason: unknown ? {} : { synthetic_protection: protection }, max_allocatable_by_product: { [simProduct]: unknown ? null : Math.max(0, min) }, blocking_constraints: unknown ? [{ code: 'SYNTHETIC_MISSING', entity_id: 'fixture', date: null, required_cents: null, available_cents: null }] : [], calculation_trace: trace, boundary_hash: 'a'.repeat(64), calculation_notes: ['SYNTHETIC_HTTP_ONLY_NOT_ENGINE_OUTPUT'] };
}
export function scenarioContextFixture(unknown = false): ScenarioContext {
  const files = { 'domain/boundary.py': 'a'.repeat(64), 'services/scenario_simulation.py': 'b'.repeat(64) };
  return { ...flags, schema_version: 'counterfactual-context-v1', user_id: simUser, epoch_id: simEpoch, as_of: simNow, timezone: 'UTC', source_hash: 'c'.repeat(64), engine_hash: hash(files), engine_files: files,
    cash_choices: [{ account_id: simAccount, balance_cents: 100007, evidence_ids: [simEvidence] }], emergency_choices: [{ policy_id: simPolicy, version_id: simVersion, configuration_hash: 'e'.repeat(64), amount_cents: 20000, evidence_ids: [simEvidence] }], product_choices: [{ product_id: simProduct, version_number: 1, asset_class: 'FIXED_DEPOSIT', minimum_purchase_cents: 100, terms_digest: 'f'.repeat(64), term_days: 30, settlement_delay_days: 1 }],
    baseline_90: shapeBoundary(90, unknown), baseline_365: shapeBoundary(365, unknown), source_evidence_ids: [simEvidence], source_issues: unknown ? [{ code: 'SYNTHETIC_MISSING', source_ref: simEvidence, message: '合成缺失来源，非金融实证' }] : [], audit: { scope: 'CURRENT_LIVE_EPOCH', epoch_id: simEpoch, status: unknown ? 'INTEGRITY_ERROR' : 'VALID', complete: !unknown, anchored_run_statuses: {} }, limitations: ['SYNTHETIC_HTTP_ONLY：未运行金融引擎/数据库/浏览器'] };
}
export function scenarioRequestFixture(base = scenarioContextFixture()): ScenarioRequest { return { expected_epoch_id: base.epoch_id, expected_source_hash: base.source_hash, expected_engine_hash: base.engine_hash, horizon_days: 90, cash_change: null, emergency_change: null, product_change: null }; }
export function scenarioComparisonFixture(body = scenarioRequestFixture(), unknown = false): ScenarioComparison {
  const base = scenarioContextFixture(unknown), cash = 100007 + (body.cash_change?.delta_cents ?? 0), protection = 50005 + (body.emergency_change ? body.emergency_change.amount_cents - 20000 : 0);
  const baseline = shapeBoundary(body.horizon_days, unknown), hypothetical = shapeBoundary(body.horizon_days, unknown, cash, protection), parameters: ScenarioComparison['changed_parameters'] = [];
  if (body.cash_change) parameters.push({ kind: 'CASH_BALANCE_DELTA', entity_id: simAccount, original: { balance_cents: 100007, evidence_ids: [simEvidence] }, hypothetical: { balance_cents: cash, delta_cents: body.cash_change.delta_cents }, source_is_hypothetical: true });
  if (body.product_change) parameters.push({ kind: 'PRODUCT_OCCUPANCY', entity_id: simProduct, original: { ...base.product_choices[0]! }, hypothetical: { ...body.product_change }, source_is_hypothetical: true });
  if (body.emergency_change) parameters.push({ kind: 'EMERGENCY_AMOUNT', entity_id: simPolicy, original: { version_id: simVersion, configuration_hash: 'e'.repeat(64), amount_cents: 20000 }, hypothetical: { configuration_hash: 'd'.repeat(64), amount_cents: body.emergency_change.amount_cents }, source_is_hypothetical: true });
  return { ...flags, schema_version: 'counterfactual-comparison-v1', user_id: simUser, epoch_id: simEpoch, as_of: simNow, timezone: 'UTC', local_date: '2026-10-05', horizon_days: body.horizon_days, source_hash: body.expected_source_hash, engine_hash: body.expected_engine_hash, original_request: structuredClone(body), request_hash: hash(body), scenario_hash: 'd'.repeat(64), baseline, hypothetical, delta_safe_idle_cents: unknown ? null : hypothetical.safe_idle_cents! - baseline.safe_idle_cents!, changed_parameters: parameters, limitations: base.limitations };
}
