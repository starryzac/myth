import type { components } from '../../../../packages/contracts/schema';
import { vi } from 'vitest';
import { dashboardFixture } from './dashboard-fixture';
export const policyId = '10000000-0000-0000-0000-000000000040';
export const versionId = '10000000-0000-0000-0000-000000000041';
export const proposalId = '10000000-0000-0000-0000-000000000042';
export const compilationId = '10000000-0000-0000-0000-000000000043';
export const hash = 'a'.repeat(64);
export function policyFixture(type = 'emergency_buffer'): components['schemas']['PolicyView'] {
  const configuration = type === 'goal_saving' ? { type, name: '旅行目标', target_cents: 120000, deadline: '2027-01-01',
    monthly_contribution: { min_cents: 0, target_cents: 20000, max_cents: 30000 },
    priority: { importance: 50, minimum_cents: 0, reducible: false, deferrable: false }, cross_goal_reallocation_allowed: false, asset_policy_id: null }
    : { type, name: '单元应急金', amount_cents: 200000, valid_from: null, valid_until: null };
  return { id: policyId, name: type === 'goal_saving' ? '旅行目标' : '单元应急金', policy_type: type, status: 'ACTIVE', effective_status: 'ACTIVE', version_authorized: true,
    updated_at: '2026-10-04T00:00:00Z', current_version: { id: versionId, policy_id: policyId, version_number: 1, configuration, summary: '单元fixture', confirmation: {},
      confirmed_at: '2026-10-04T00:00:00Z', valid_from: '2026-10-04T00:00:00Z', valid_until: null, change_reason: '明确确认', evidence_ids: [],
      content_hash: hash, previous_hash: null, impact_analysis: {}, created_at: '2026-10-04T00:00:00Z' } };
}
export function compilationFixture(): components['schemas']['CompilationResponse'] {
  const configuration = policyFixture().current_version!.configuration;
  return { simulation: true, user_id: dashboardFixture().user_id, compilation_id: compilationId,
    compilation: { compiler_version: 'rules-unit-v1', reference_date: '2026-10-04', timezone: 'Asia/Shanghai', draft: configuration,
      configuration, issues: [], assumptions: ['单元测试原始假设'] }, configuration, configuration_hash: hash, proposal_id: proposalId, proposal_status: 'PROPOSED' };
}
export function proposalFixture(): components['schemas']['ProposalView'] {
  return { id: proposalId, status: 'PROPOSED', source_type: 'RULES_COMPILED', source_text: '应急金保留2000元', compiler_version: 'rules-unit-v1', evidence_ids: [],
    configuration: policyFixture().current_version!.configuration, configuration_hash: hash, validation_ready: true, confirmed_policy_id: null, compilation_id: compilationId };
}
export function previewFixture(): components['schemas']['PolicyChangePreviewResponse'] {
  const dashboard = dashboardFixture();
  return { schema_version: 'policy-change-preview-v1', simulation: true, preview_only: true, financial_only: true, user_id: dashboard.user_id, as_of: dashboard.as_of, timezone: dashboard.timezone,
    policy_id: policyId, expected_version_id: versionId, configuration: policyFixture().current_version!.configuration, configuration_hash: hash,
    assumed_status: 'ACTIVE', assumed_valid_from: '2026-10-04T00:00:00Z', assumed_valid_until: null, assumption_digest: hash,
    current_fact_input_digest: hash, hypothetical_input_digest: hash, before: dashboard.boundary, after: dashboard.boundary,
    delta_safe_idle_cents: 0, delta_minimum_margin_cents: 0, notes: ['单元HTTP夹具，非金融计算实测'] };
}
export function lifecycleFixture(): components['schemas']['LifecycleResult'] {
  return { simulation: true, policy_id: policyId, current_version_id: versionId, previous_version_id: null,
    status: 'ACTIVE', effective_status: 'ACTIVE', invalidated_action_ids: [], inflight_action_ids: [], requires_recompute: true };
}
export function failure(status = 409): Response {
  return new Response(JSON.stringify({ error: { code: 'STALE_VERSION', message: '单元fixture版本冲突', request_id: 'unit-request-409' } }), { status });
}
export function installHttpFixture(handler: (method: string, path: string, body: unknown) => unknown) {
  const requests: { method: string; path: string; body: unknown }[] = [];
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local');
  // HTTP unit fixture only. This does not exercise PG, bank simulation or Edge.
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const method = options?.method ?? 'GET'; const path = new URL(String(input)).pathname;
    const body = typeof options?.body === 'string' ? JSON.parse(options.body) as unknown : undefined;
    requests.push({ method, path, body });
    const result = handler(method, path, body);
    return result instanceof Response ? result : new Response(JSON.stringify(result), { status: 200 });
  }));
  return requests;
}
