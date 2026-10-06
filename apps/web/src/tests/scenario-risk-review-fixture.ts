/** SYNTHETIC_SOURCE_DOUBLE_HTTP_SHAPE_NOT_PRODUCT_PROOF; no PG, bank or browser outcomes. */
import original from './scenario-risk-review-fixture.json';
import type { RiskComparison, RiskContext, RiskRequest } from '../api/scenario-risk-review';
import { seasonalHash } from '../api/seasonal-reserve-adoptions';
import { financialPreviewFixture } from './financial-preview-fixture';
type Fixture = { context: RiskContext; bill: RiskComparison; product_request: RiskRequest; product_sensitivity: NonNullable<RiskComparison['product_preview']> };
const data = original as unknown as Fixture;
export const riskBill = data.context.bills[0]!.bill_id;
export const riskGoal = data.context.goals[0]!.goal_id;
export const riskFullPolicy = data.context.full_policies[0]!.policy_id;
export const riskAssetPolicy = data.context.full_policies[1]!.policy_id;
export const riskProduct = data.context.products[0]!.product_id;
export const riskContextFixture = () => structuredClone(data.context);
export const riskBillBody = (): RiskRequest => structuredClone(data.bill.original_request);
export const riskProductBody = (): RiskRequest => structuredClone(data.product_request);
export async function sealRiskComparison(result: RiskComparison): Promise<RiskComparison> {
  result.request_hash = await seasonalHash(result.original_request);
  result.comparison_hash = await seasonalHash({ protocol: 'scenario-risk-comparison-v1', request_hash: result.request_hash, source_hash: result.source_hash, engine_hash: result.engine_hash, as_of: result.as_of.replace(/Z$/, '+00:00'), curve: result.hypothetical_full, full: result.full_policy_preview, product: result.product_preview }); return result;
}
export async function riskComparisonFixture(body: RiskRequest = riskBillBody(), context = riskContextFixture()): Promise<RiskComparison> {
  const result = structuredClone(data.bill); result.original_request = structuredClone(body); result.family = body.hypothesis.kind; result.user_id = context.user_id; result.epoch_id = context.epoch_id; result.source_hash = context.source_hash; result.engine_hash = context.engine_hash; result.as_of = context.as_of; result.baseline_full = structuredClone(context.baseline_full);
  if (body.hypothesis.kind === 'BILL') { if (body.hypothesis.remaining_due_cents !== 300 || body.hypothesis.due_offset_days !== 0) throw new Error('Synthetic BILL fixture only describes its literal 300-cent input'); }
  else {
    result.hypothetical_full = null; result.delta_safe_idle_cents = null; result.status = 'UNKNOWN'; result.reasons = ['SYNTHETIC_SHAPE_UNKNOWN_NOT_FINANCIAL_PROOF']; result.full_policy_preview = null; result.product_preview = null;
    const h = body.hypothesis;
    if (h.kind === 'GOAL') { const source = context.goals.find((row) => row.goal_id === h.goal_id)!; const config = structuredClone(source.source_version.configuration); config.monthly_contribution = { min_cents: h.monthly_min_cents, target_cents: h.monthly_target_cents, max_cents: h.monthly_max_cents }; config.deadline = new Date(Date.parse(`${config.deadline}T00:00:00Z`) + h.deadline_offset_days * 86400000).toISOString().slice(0, 10); result.original_selected = structuredClone(source.source_version) as unknown as Record<string, unknown>; result.hypothetical_selected = { configuration: config, configuration_hash: await seasonalHash(config), hypothetical_only: true }; }
    else if (h.kind === 'FULL_POLICY') { const source = context.full_policies.find((row) => row.policy_id === h.policy_id)!; const p = await financialPreviewFixture(true); p.policy_id = h.policy_id; p.expected_version_id = h.expected_version_id; p.epoch_id = context.epoch_id; p.as_of = context.as_of; p.before_configuration = structuredClone(source.configuration); p.current_configuration_hash = await seasonalHash(source.configuration); p.after_configuration = structuredClone(h.configuration); p.configuration_hash = await seasonalHash(h.configuration); p.financial_impact.before = structuredClone(context.original_full_protection.projection.full_annual_projection); result.full_policy_preview = p; result.original_selected = p.before_configuration; result.hypothetical_selected = p.after_configuration; }
    else { if (JSON.stringify(body.hypothesis) !== JSON.stringify(data.product_request.hypothesis)) throw new Error('Synthetic PRODUCT fixture only describes its literal original-optimizer input'); result.status = 'PROJECTED'; result.reasons = []; result.product_preview = structuredClone(data.product_sensitivity); result.original_selected = result.product_preview.original_product as unknown as Record<string, unknown>; result.hypothetical_selected = result.product_preview.hypothetical_product as unknown as Record<string, unknown>; }
  }
  return sealRiskComparison(result);
}
