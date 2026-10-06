/** TOOL_ONLY_SYNTHETIC: Python pure domain-derived data, never PG/bank/browser evidence. */
import raw from './multi-template-preview-fixture.json';
import type { MultiPreview, MultiPreviewSource, MultiPreviewBody } from '../api/full-policy-change-multi';
import { dashboardFixture } from './dashboard-fixture';
import { policyFixture } from './policy-fixture';
import { fullPolicyFixture } from './full-policy-fixture';
export type MultiFixtureKind = keyof typeof raw;
export function multiFixture(kind: MultiFixtureKind = 'projected'): MultiPreview { return structuredClone(raw[kind]) as unknown as MultiPreview; }
export function multiSource(kind: MultiFixtureKind = 'projected'): MultiPreviewSource {
  const v = multiFixture(kind); return { sourceKind: v.source_kind, policyId: v.policy_id, versionId: v.expected_version_id, userId: v.user_id, epochId: v.epoch_id, template: v.template_name, name: `纯域${kind}夹具`, configuration: v.before_configuration, eligible: true, reason: 'TOOL_ONLY_SYNTHETIC_CURRENT_SOURCE' };
}
export function multiBody(kind: MultiFixtureKind = 'projected'): MultiPreviewBody { const v = multiFixture(kind); return { expected_version_id: v.expected_version_id, expected_epoch_id: v.epoch_id, configuration: v.after_configuration }; }
export function multiUnknown(): MultiPreview { const v = multiFixture(); Object.assign(v.financial_impact, { status: 'UNKNOWN', scope: 'UNSUPPORTED', after: null, delta_safe_idle_cents: null, delta_minimum_margin_cents: null, delta_product_financial_capacity_cents: null, reasons: ['TOOL_ONLY_MISSING_ORIGINAL'] }); return v; }
export function multiInventoryFixture() {
  const source = multiSource(), value = multiFixture();
  const old = dashboardFixture(); const dashboard = JSON.parse(JSON.stringify(old).replaceAll(old.user_id, source.userId)) as ReturnType<typeof dashboardFixture>;
  dashboard.audit.epoch_id = source.epochId; dashboard.audit.status = 'VALID'; dashboard.audit.complete = true;
  const policy = policyFixture(); policy.id = source.policyId; policy.name = source.name;
  const version = policy.current_version!; version.id = source.versionId; version.policy_id = source.policyId; version.configuration = source.configuration; version.content_hash = value.current_configuration_hash;
  version.confirmation = { user_id: source.userId, policy_id: source.policyId, version_id: source.versionId, reviewed_hash: version.content_hash, accepted: true };
  const asset = multiFixture('asset'); const full = fullPolicyFixture();
  full.policy_id = asset.policy_id; full.epoch_id = source.epochId; full.template_name = 'AssetAuthorizationPolicy'; full.name = '纯域asset夹具'; full.current_version.policy_id = full.policy_id; full.current_version.version_id = asset.expected_version_id;
  full.current_version.configuration = asset.before_configuration; full.current_version.content_hash = asset.current_configuration_hash;
  Object.assign(full.current_version.confirmation, { policy_id: full.policy_id, version_id: full.current_version.version_id, epoch_id: full.epoch_id, user_id: source.userId, template_name: full.template_name, reviewed_hash: full.current_version.content_hash });
  return { dashboard, mvp: { simulation: true, items: [policy] }, full: { simulation: true, bank_authority: false, dedicated_audit_event: false, items: [full] } };
}
