/** SYNTHETIC_HTTP_FIXTURE_ONLY. These invented records test readers; they are not financial, PG or browser proof. */
import type { components } from '../../../../packages/contracts/schema';
import type { AssetOptions, FullAssetPlan, FullRecoveryPlan, Positions, ProductCatalog } from '../api/full-products';
import { annualFixture } from './annual-fixture';
import { fullFlags, fullPolicyFixture, fullPolicyId, fullUserId, fullVersionId } from './full-policy-fixture';

export const productId = '81000000-0000-4000-8000-000000000001';
export const catalogueId = '81000000-0000-4000-8000-000000000002';
export const accountId = '81000000-0000-4000-8000-000000000003';
export const positionId = '81000000-0000-4000-8000-000000000004';
export const quoteId = '81000000-0000-4000-8000-000000000005';
export const recoveryPolicyId = '81000000-0000-4000-8000-000000000006';
export const recoveryVersionId = '81000000-0000-4000-8000-000000000007';
export const productHash = 'b'.repeat(64), termsDigest = 'c'.repeat(64), inputHash = 'd'.repeat(64);
export const productTime = '2026-10-05T12:00:00Z', availableTime = '2026-11-04T12:00:00Z';
export const assetOptions = (): AssetOptions => ({ comparison_days: 90, max_components: 3, max_turnover_cents: null, funds_use_date: null, mode: 'PORTFOLIO' });
const flags = { simulation: true as const, planning_only: true as const, bank_authority: false as const, execution_support: 'NOT_IMPLEMENTED' as const, protection_scope: 'ORIGINAL_VERIFIED_365_DAY_CURVE' as const };
export function productTerms(): components['schemas']['AssetProductTerms'] {
  return { product_id: productId, product_code: 'SYNTHETIC_FIXED_30D', version_number: 1, asset_class: 'FIXED_DEPOSIT', risk_level: 0, principal_fluctuation: false,
    minimum_purchase_cents: 10001, lock_days: 30, redemption_delay_days: 0, annual_yield_bps: 137, early_withdrawal_loss_bps: 25, auto_purchase_allowed: false,
    auto_redeem_allowed: false, created_at: productTime, effective_from: productTime, effective_until: null, maturity_rule: { term_days: 30 }, terms_digest: termsDigest };
}
export const productBinding = () => ({ product_id: productId, catalogue_version_id: catalogueId, product_record_hash: productHash, terms_digest: termsDigest });
export function productCatalogue(drifted = false): ProductCatalog {
  const { product_id, terms_digest: _terms, ...terms } = productTerms(); void _terms;
  return { simulation: true, grants_authority: false, state: drifted ? 'UNKNOWN' : 'REGISTERED', complete_within_registered_capacity: !drifted,
    unregistered_product_ids: [], issues: drifted ? ['SYNTHETIC_SOURCE_DRIFT_RETAINED'] : [], versions: [{ id: catalogueId, product_id, product_code: terms.product_code, version_number: 1,
      original_product: { id: product_id, name: 'HTTP夹具三十天原产品', ...terms, early_withdrawal_rule: {} }, product_hash: productHash, terms_digest: termsDigest,
      observed_at: productTime, effective_from: productTime, effective_until: null, immutable_original_verified: true, current_source_matched: !drifted, bank_authority: false, legacy_decisions_bound_to_this_catalogue: false }] };
}
export function productPositions(): Positions {
  return { simulation: true, items: [{ id: positionId, account_id: accountId, product_id: productId, goal_id: null, policy_version_id: fullVersionId,
    principal_cents: 67003, accrued_yield_cents: 0, purchased_at: productTime, maturity_at: availableTime, available_at: null, status: 'HELD' }] };
}
export function productPolicies(): components['schemas']['FullPolicyList'] {
  const asset = fullPolicyFixture(); asset.template_name = 'AssetAuthorizationPolicy'; asset.name = 'HTTP夹具资产声明'; asset.current_version.confirmation.template_name = asset.template_name;
  asset.current_version.configuration = { type: 'full_asset_authorization', name: asset.name, fixture_only: true };
  const recovery = fullPolicyFixture(); recovery.policy_id = recoveryPolicyId; recovery.name = 'HTTP夹具恢复声明'; recovery.template_name = 'RecoveryPolicy';
  recovery.current_version.policy_id = recoveryPolicyId; recovery.current_version.version_id = recoveryVersionId;
  recovery.current_version.confirmation.policy_id = recoveryPolicyId; recovery.current_version.confirmation.version_id = recoveryVersionId; recovery.current_version.confirmation.template_name = 'RecoveryPolicy';
  recovery.current_version.configuration = { type: 'full_recovery', name: recovery.name, fixture_only: true };
  return { ...fullFlags, items: [asset, recovery] };
}
export function assetPlanFixture(options = assetOptions(), unknown = false): FullAssetPlan {
  const boundary = annualFixture().annual_projection;
  const exit_plan: components['schemas']['PlannedExit'] = { kind: 'FIXED_MATURITY', request_at: null, principal_available_at: availableTime, earning_days: 30, liquidity_days: 30, terms_digest: termsDigest };
  return { ...flags, schema_version: 'verified-full-asset-planning-v1', user_id: fullUserId, policy_id: fullPolicyId, as_of: productTime, state: unknown ? 'UNKNOWN' : 'COMPUTED',
    planning_constraints: { ...options }, catalogue: unknown ? { status: 'UNKNOWN', products: [], bindings: [], issues: ['SYNTHETIC_CATALOGUE_MISSING'] } : { status: 'VERIFIED', products: [productTerms()], bindings: [productBinding()], issues: [] },
    unavailable_asset_classes: ['FIXED_7D', 'FIXED_90D'], source_evidence_ids: [], source_issues: unknown ? [{ code: 'SYNTHETIC_MISSING', source_ref: 'synthetic-source', message: '原件不足，未证明' }] : [],
    input_hash: inputHash, limitations: ['SYNTHETIC_HTTP_NOT_FINANCIAL_PROOF', '未来收入0，FULL新保护尚未接入'], allocation: unknown ? null : { algorithm_version: 'finite-integer-original-product-portfolio-v1',
      policy_id: fullPolicyId, policy_version_id: fullVersionId, scope: 'general_idle_funds', goal_id: null, planning_only: true, bank_authority: false, execution_support: 'NOT_IMPLEMENTED', future_income_included_cents: 0,
      status: 'OPTIMAL', input_hash: inputHash, baseline_boundary: boundary, projected_boundary: structuredClone(boundary), candidates: [{ product_id: productId, product_code: 'SYNTHETIC_FIXED_30D', version_number: 1,
        catalog_asset_class: 'FIXED_DEPOSIT', planning_asset_class: 'FIXED_30D', terms_digest: termsDigest, status: 'FEASIBLE', bank_auto_eligible: false, financial_cap_cents: 79001, maximum_batch_cents: 67003, exit_plan, reasons: ['SYNTHETIC_READONLY_CANDIDATE'] }],
      batches: [{ product_id: productId, product_code: 'SYNTHETIC_FIXED_30D', version_number: 1, terms_digest: termsDigest, amount_cents: 67003, net_simulated_yield_cents: 75, purchase_at: productTime, principal_available_at: availableTime,
        exit_plan: { ...exit_plan }, cash_uses: [{ account_id: accountId, amount_cents: 67003 }], bank_authority: false }], total_purchase_cents: 67003, retained_scope_cash_cents: 11998, net_simulated_yield_cents: 75,
      purchase_count: 1, search_nodes: 7, funds_use_date: '2027-01-03', ladder_status: options.mode === 'FIXED_LADDER' ? 'SINGLE_MATURITY_AVAILABLE' : 'NOT_REQUESTED', reasons: ['SYNTHETIC_NO_FUTURE_PURCHASES'] } };
}
export function recoveryPlanFixture(unknown = false): FullRecoveryPlan {
  const candidate: components['schemas']['FullRecoveryCandidate'] = { position_id: positionId, goal_id: null, destination_account_id: accountId, product_id: productId, product_version_number: 1, terms_digest: termsDigest,
    catalogue_version_id: catalogueId, product_record_hash: productHash, original_policy_version_id: fullVersionId, principal_cents: 67003, original_quote: { quote_id: quoteId, user_id: fullUserId,
      position_id: positionId, product_id: productId, product_version_number: 1, terms_digest: termsDigest, kind: 'EARLY_WITHDRAW', principal_cents: 67003, fee_cents: 0, loss_cents: 167, net_cents: 66836,
      request_at: productTime, principal_available_at: productTime, expires_at: '2026-10-05T12:15:00Z', evidence_ids: [] }, quote_source: 'BANK_CONFIRMED', independent_loss_cents: 167, fee_cents: 0, net_cents: 66836,
    earliest_conditional_cash_at: productTime, liquidity_rank: 0, on_time: true, within_full_planning_limits: false, lossless_eligible: false, decision: 'ASK_ONCE', reasons: ['SYNTHETIC_LOSSY_REQUIRES_INDEPENDENT_CHOICE'], conditional_impact_boundary: annualFixture().annual_projection, bank_authority: false };
  return { ...flags, schema_version: 'verified-full-recovery-planning-v1', user_id: fullUserId, policy_id: recoveryPolicyId, as_of: productTime, state: unknown ? 'UNKNOWN' : 'COMPUTED', catalogue: productCatalogue(),
    catalogue_bindings: [productBinding()], source_evidence_ids: [], source_issues: unknown ? [{ code: 'SYNTHETIC_QUOTE_MISSING', source_ref: 'synthetic-source', message: '归属未证明' }] : [], input_hash: inputHash,
    limitations: ['SYNTHETIC_HTTP_NOT_FINANCIAL_PROOF', '原报价不是银行当前授权'], plan: unknown ? null : { algorithm_version: 'full-whole-position-recovery-v1', user_id: fullUserId, policy_id: recoveryPolicyId,
      policy_version_id: recoveryVersionId, linked_asset_policy_id: fullPolicyId, linked_asset_policy_version_id: fullVersionId, linked_asset_configuration_hash: 'a'.repeat(64), as_of: productTime,
      planning_only: true, bank_authority: false, execution_support: 'NOT_IMPLEMENTED', status: 'LIQUIDITY_RISK', scope: 'general_idle_funds', goal_id: null, actual_boundary: annualFixture().annual_projection,
      lossless_conditional_boundary: annualFixture().annual_projection, deadline_at: '2026-10-07T09:00:00+08:00', actual_scope_cash_cents: 11998, required_recovery_cents: 10001, conditional_on_time_recovery_cents: 0,
      observed_triggers: ['LIQUIDITY_SHORTFALL'], candidates: [candidate], lossless_steps: [], uncovered_checkpoints: [], first_sustained_safe_point: annualFixture().annual_projection.calculation_trace[0]!, reasons: ['SYNTHETIC_LOSSY_NOT_IN_LOSSLESS_PLAN'], input_hash: inputHash } };
}
