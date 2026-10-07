import { request, ApiError } from '../api/http';
import { isRunId } from '../api/decisions';
import { parseLocalActorSession, type LocalActorSession } from '../api/local-actor';
import { parseMultiPreview, multiConfigurationHash, type MultiPreview, type MultiPreviewSource } from '../api/full-policy-change-multi';
import { parseFullGoalConfiguration } from '../api/full-goals';
import { object } from '../features/policy-form';
import { context, same, safeConfiguration, type PolicyRecord } from './policies';
import type { PendingOperation } from './operation';
import type { NextState, OperationResult } from './api';

export const reviewedScopes = ['MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS', 'INDIVIDUAL_PRODUCT_CAPACITY', 'WHOLE_POSITION_RECOVERY_CANDIDATES', 'CURRENT_JOINT_GOAL_ALLOCATION'] as const;
export type ReviewedScope = typeof reviewedScopes[number];
export const scopeTitles: Record<ReviewedScope, string> = { MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS: '资金保护变化（保留原完整规划负担）', INDIVIDUAL_PRODUCT_CAPACITY: '每产品独立容量（不能相加为组合）', WHOLE_POSITION_RECOVERY_CANDIDATES: '逐持仓回收候选（不构成多仓执行）', CURRENT_JOINT_GOAL_ALLOCATION: '当前期目标分配（不构成多期最优）' };
export type ChangeReview = { protocol: 'reviewed-policy-change-record-v1'; review_id: string; user_id: string; epoch_id: string; source_kind: 'MVP_POLICY' | 'FULL_POLICY'; policy_id: string; request: Record<string, unknown>; captured_at: string; expires_at: string; actor: LocalActorSession['principal']; source_basis: Record<string, unknown>; source_basis_hash: string; preview: MultiPreview; covered_scopes: ReviewedScope[]; uncovered_items: string[]; confirmation_eligible: boolean; review_hash: string; bank_authority: false; independent_financial_verification: false };
export type ChangeCommit = { protocol: 'reviewed-policy-change-commit-v1'; simulation: true; bank_authority: false; receipt_is_current_authority: false; full_financial_effects_verified: false; user_id: string; source_kind: 'MVP_POLICY' | 'FULL_POLICY'; policy_id: string; idempotency_key: string; original_request: Record<string, unknown>; outer_request_hash: string; legacy_request_hash: string; commit_state: 'COMMITTED'; status: 'COMMITTED_REVIEW_SCOPE_MATCHED' | 'COMMITTED_BUT_FINANCIAL_UNKNOWN'; lifecycle_receipt: Record<string, unknown>; actual_version_id: string | null; actual_configuration_hash: string | null; reviewed_preview: MultiPreview; actual_readback: MultiPreview | null; covered_scopes: ReviewedScope[]; uncovered_items: string[]; reasons: string[]; actual_delta_safe_idle_cents: number | null; actual_delta_minimum_margin_cents: number | null };
const valid = (condition: unknown) => { if (!condition) throw new Error('修改原件、财务分母或确认范围未通过核实，请保留原请求。'); };
const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((row) => typeof row === 'string');
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
export function changeIdentity(original: PendingOperation) {
  const match = /^\/zhiyu-next\/policies\/(MVP_POLICY|FULL_POLICY)\/([0-9a-f-]{36})\/change-(review|confirm)$/.exec(original.path);
  valid(match && isRunId(match[2])); return { sourceKind: match![1] as 'MVP_POLICY' | 'FULL_POLICY', policyId: match![2]! };
}
function source(value: Record<string, unknown>, environment: NextState): MultiPreviewSource {
  valid(['MVP_POLICY', 'FULL_POLICY'].includes(String(value.source_kind)) && isRunId(value.policy_id) && isRunId(value.expected_version_id) && safeConfiguration(value.before_configuration) && typeof value.template_name === 'string');
  return { sourceKind: value.source_kind as 'MVP_POLICY' | 'FULL_POLICY', policyId: String(value.policy_id), versionId: String(value.expected_version_id), userId: environment.dashboard.user_id, epochId: environment.epoch_id, name: '原规则', template: String(value.template_name), configuration: value.before_configuration as Record<string, unknown>, eligible: true, reason: '独立原件回读' };
}
function actor(value: unknown, environment: NextState): LocalActorSession['principal'] {
  const parsed = parseLocalActorSession({ simulation: true, bank_authority: false, confirms_financial_action: false, principal: value }).principal;
  valid(parsed.user_id === environment.dashboard.user_id && parsed.role === 'USER'); return parsed;
}
export function parseNextActor(value: unknown, environment: NextState): LocalActorSession {
  context(value, environment); valid(value.bank_authority === false && value.confirms_financial_action === false);
  const parsed = parseLocalActorSession({ simulation: value.simulation, bank_authority: value.bank_authority, confirms_financial_action: value.confirms_financial_action, principal: value.principal });
  valid(parsed.principal.user_id === environment.dashboard.user_id && parsed.principal.role === 'USER'); return parsed;
}
export async function readNextActor(environment: NextState): Promise<LocalActorSession | null> { try { return await request('/zhiyu-next/local-actor/session', 'GET', undefined, (value) => parseNextActor(value, environment)); } catch (error) { if (error instanceof ApiError && error.status === 401) return null; throw error; } }
export const loginNextActor = (secret: string, environment: NextState) => request('/zhiyu-next/local-actor/login', 'POST', { username: 'bounded-user', secret }, (value) => parseNextActor(value, environment));
export async function logoutNextActor(environment: NextState) { return request('/zhiyu-next/local-actor/logout', 'POST', {}, (value) => { context(value, environment); valid(value.logged_out === true && value.bank_authority === false); return true; }); }
function goalBase(configuration: Record<string, unknown>): Record<string, unknown> {
  const full = parseFullGoalConfiguration(configuration);
  return { type: 'goal_saving', name: full.name, valid_from: full.valid_from, valid_until: full.valid_until, target_cents: full.target_cents, deadline: full.deadline, monthly_contribution: full.monthly_contribution, priority: { importance: full.importance, minimum_cents: full.minimum_guarantee_cents, reducible: full.allow_partial, deferrable: full.allow_deferral }, cross_goal_reallocation_allowed: false, asset_policy_id: full.asset_policy_id };
}
export async function previewPolicyChange(record: PolicyRecord, configuration: Record<string, unknown>, environment: NextState): Promise<MultiPreview> {
  valid(record.planning_confirmed && ['ACTIVE', 'CONFIRMED'].includes(record.effective_status) && safeConfiguration(configuration) && configuration.type === record.configuration.type);
  const kind = record.source_kind === 'FULL_POLICY' ? 'FULL_POLICY' : 'MVP_POLICY';
  const body = { template_name: record.template_name, dsl_version: record.dsl_version, expected_epoch_id: environment.epoch_id, expected_version_id: record.current_version_id, configuration };
  const received = await request(`/zhiyu-next/policies/${kind}/${record.policy_id}/change-preview`, 'POST', body, (value) => { context(value, environment); return value; });
  valid(received.template_name === record.template_name && received.backend === record.lifecycle_backend && received.preview_only === true && received.bank_authority === false && received.source_kind === kind && received.extra_goal_fields_in_financial_preview === false && digest(received.candidate_configuration_hash) && object(received.financial_preview));
  valid(await multiConfigurationHash(configuration) === received.candidate_configuration_hash);
  const bridge = record.source_kind === 'GOAL_BRIDGE';
  const before = bridge ? goalBase(record.configuration) : record.configuration;
  const after = bridge ? goalBase(configuration) : configuration;
  if (bridge) valid(await multiConfigurationHash(after) === received.base_configuration_hash);
  const original: MultiPreviewSource = { sourceKind: kind, policyId: record.policy_id, versionId: record.current_version_id, userId: environment.dashboard.user_id, epochId: environment.epoch_id, template: record.template_name, name: record.name, configuration: before, eligible: true, reason: '当前已确认来源' };
  return parseMultiPreview(received.financial_preview, original, { expected_epoch_id: environment.epoch_id, expected_version_id: record.current_version_id, configuration: after });
}
function eligible(preview: MultiPreview): boolean {
  const impact = preview.financial_impact;
  if (!['PROJECTED', 'PARTIAL'].includes(impact.status) || !impact.before || !impact.after) return false;
  if (impact.scope === reviewedScopes[0]) return true;
  if (impact.scope === reviewedScopes[1]) return impact.product_capacities.length === preview.source_counts.asset_catalogue && impact.product_capacities.every((row) => row.before_capacity_cents !== null && row.after_capacity_cents !== null);
  if (impact.scope === reviewedScopes[2]) return impact.recovery_candidates.length === preview.source_counts.recovery_holdings && impact.recovery_candidates.every((row) => row.conditional_on_time_net_delta_cents !== null);
  return impact.scope === reviewedScopes[3] && !!impact.goal_allocation_before && !!impact.goal_allocation_after && impact.goal_allocation_before.status !== 'UNKNOWN' && impact.goal_allocation_after.status !== 'UNKNOWN';
}
export async function parseChangeReview(value: unknown, original: PendingOperation, environment: NextState): Promise<ChangeReview> {
  const binding = changeIdentity(original);
  valid(object(value) && value.protocol === 'reviewed-policy-change-record-v1' && isRunId(value.review_id) && value.user_id === environment.dashboard.user_id && value.epoch_id === environment.epoch_id && value.source_kind === binding.sourceKind && value.policy_id === binding.policyId && same(value.request, original.body) && value.bank_authority === false && value.independent_financial_verification === false && time(value.captured_at) && time(value.expires_at) && Date.parse(value.expires_at) > Date.parse(value.captured_at) && Date.parse(value.expires_at) - Date.parse(value.captured_at) <= 900000 && object(value.preview) && object(value.source_basis) && digest(value.source_basis_hash) && digest(value.review_hash) && typeof value.confirmation_eligible === 'boolean' && strings(value.covered_scopes) && new Set(value.covered_scopes).size === value.covered_scopes.length && strings(value.uncovered_items));
  const row = value as Record<string, unknown>; const principal = actor(row.actor, environment);
  valid(Date.parse(String(row.expires_at)) <= Date.parse(principal.expires_at) && Date.parse(String(row.captured_at)) >= Date.parse(principal.issued_at));
  const basis = row.source_basis as Record<string, unknown>;
  valid(basis.protocol === 'registered-policy-change-source-basis-v1' && basis.complete === true && basis.user_id === environment.dashboard.user_id && basis.epoch_id === environment.epoch_id && strings(basis.registered_tables) && strings(basis.excluded_metadata_tables) && object(basis.tables) && object(basis.row_counts));
  const tables = basis.tables as Record<string, unknown>; const counts = basis.row_counts as Record<string, unknown>;
  const excluded = ['decision_runs', 'decision_constraints', 'audit_events', 'audit_epochs', 'audit_subject_snapshots', 'audit_archive_snapshots'];
  valid(same(basis.registered_tables, [...new Set(basis.registered_tables as string[])].sort()) && same(Object.keys(tables).sort(), Object.keys(counts).sort()) && same((basis.registered_tables as string[]).slice().sort(), [...Object.keys(tables), ...(basis.excluded_metadata_tables as string[])].sort()) && (basis.excluded_metadata_tables as string[]).every((name) => excluded.includes(name)) && Object.keys(tables).every((name) => !excluded.includes(name)) && Object.entries(tables).every(([name, rows]) => Array.isArray(rows) && rows.length <= 10000 && Number.isSafeInteger(counts[name]) && counts[name] === rows.length && rows.every((item) => object(item) && isRunId(item.id) && (['users', 'asset_products', 'product_catalog_versions'].includes(name) || item.user_id === environment.dashboard.user_id)) && same(rows.map((item: Record<string, unknown>) => item.id), [...new Set(rows.map((item: Record<string, unknown>) => String(item.id)))].sort())) && Array.isArray(tables.users) && tables.users.length === 1 && object(tables.users[0]) && tables.users[0].id === environment.dashboard.user_id && await multiConfigurationHash(basis) === row.source_basis_hash);
  const content = { ...row }; delete content.review_hash; valid(await multiConfigurationHash(content) === row.review_hash);
  const preview = row.preview as Record<string, unknown>;
  const versionRows = tables[binding.sourceKind === 'MVP_POLICY' ? 'policy_versions' : 'full_policy_versions'];
  valid(Array.isArray(versionRows) && versionRows.some((version) => object(version) && version.id === original.body.expected_version_id && version.policy_id === binding.policyId && same(version.configuration, preview.before_configuration) && version.content_hash === preview.current_configuration_hash) && preview.as_of === row.captured_at);
  const parsed = await parseMultiPreview(preview, source(preview, environment), { expected_epoch_id: environment.epoch_id, expected_version_id: String(original.body.expected_version_id), configuration: original.body.configuration as Record<string, unknown> });
  const canConfirm = eligible(parsed); valid(row.confirmation_eligible === canConfirm && same(row.covered_scopes, canConfirm ? [parsed.financial_impact.scope] : []));
  valid(typeof basis.local_day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(basis.local_day) && (parsed.financial_impact.before === null || parsed.financial_impact.before.calculation_trace[0]?.date === basis.local_day));
  return { ...row, preview: parsed } as ChangeReview;
}
export async function parseChangeCommit(value: unknown, original: PendingOperation, environment: NextState): Promise<ChangeCommit> {
  const binding = changeIdentity(original);
  valid(object(value) && value.protocol === 'reviewed-policy-change-commit-v1' && value.simulation === true && value.user_id === environment.dashboard.user_id && value.source_kind === binding.sourceKind && value.policy_id === binding.policyId && value.idempotency_key === original.client_request_id && same(value.original_request, original.body) && value.bank_authority === false && value.receipt_is_current_authority === false && value.full_financial_effects_verified === false && value.commit_state === 'COMMITTED' && ['COMMITTED_REVIEW_SCOPE_MATCHED', 'COMMITTED_BUT_FINANCIAL_UNKNOWN'].includes(String(value.status)) && object(value.lifecycle_receipt) && object(value.reviewed_preview) && digest(value.outer_request_hash) && digest(value.legacy_request_hash) && strings(value.covered_scopes) && (value.covered_scopes as string[]).includes(String(original.body.reviewed_scope)) && strings(value.uncovered_items) && strings(value.reasons));
  const row = value as Record<string, unknown>;
  const outer = { protocol: 'reviewed-policy-change-command-v1', user_id: environment.dashboard.user_id, source_kind: binding.sourceKind, policy_id: binding.policyId, body: original.body };
  const legacy = binding.sourceKind === 'MVP_POLICY' ? { user_id: environment.dashboard.user_id, policy_id: binding.policyId, expected_version_id: original.body.expected_version_id, configuration: original.body.configuration, reason: original.body.reason, accepted: true } : { protocol: 'full-policy-command-v1', kind: 'CHANGE', user_id: environment.dashboard.user_id, policy_id: binding.policyId, body: { expected_version_id: original.body.expected_version_id, configuration: original.body.configuration, accepted: true, reviewed_hash: original.body.reviewed_configuration_hash, reason: original.body.reason, idempotency_key: original.client_request_id } };
  valid(await multiConfigurationHash(outer) === row.outer_request_hash && await multiConfigurationHash(legacy) === row.legacy_request_hash);
  const preview = row.reviewed_preview as Record<string, unknown>;
  const reviewed = await parseMultiPreview(preview, source(preview, environment), { expected_epoch_id: environment.epoch_id, expected_version_id: String(original.body.expected_version_id), configuration: original.body.configuration as Record<string, unknown> });
  valid(reviewed.candidate_configuration_hash === original.body.reviewed_configuration_hash);
  const receipt = row.lifecycle_receipt as Record<string, unknown>;
  const version = binding.sourceKind === 'MVP_POLICY' ? receipt.current_version_id : receipt.version_id;
  valid(receipt.policy_id === binding.policyId && isRunId(version) && (binding.sourceKind === 'MVP_POLICY' ? receipt.previous_version_id === original.body.expected_version_id : receipt.epoch_id === environment.epoch_id && receipt.configuration_hash === original.body.reviewed_configuration_hash));
  valid(row.actual_version_id === null || isRunId(row.actual_version_id) && row.actual_version_id === version);
  valid(row.actual_configuration_hash === null || row.actual_configuration_hash === original.body.reviewed_configuration_hash);
  let actual: MultiPreview | null = null;
  if (row.actual_readback !== null) {
    valid(object(row.actual_readback) && isRunId(row.actual_version_id)); const read = row.actual_readback as Record<string, unknown>;
    actual = await parseMultiPreview(read, source(read, environment), { expected_epoch_id: environment.epoch_id, expected_version_id: String(row.actual_version_id), configuration: original.body.configuration as Record<string, unknown> });
    valid(actual.current_configuration_hash === original.body.reviewed_configuration_hash);
  }
  if (row.status === 'COMMITTED_REVIEW_SCOPE_MATCHED') valid(actual && row.actual_version_id === version && row.actual_configuration_hash === original.body.reviewed_configuration_hash);
  return { ...row, reviewed_preview: reviewed, actual_readback: actual } as ChangeCommit;
}
export async function readPolicyChange(original: PendingOperation, environment: NextState): Promise<OperationResult> {
  const review = original.path.endsWith('/change-review'); const url = review ? `/zhiyu-next/policy-change-reviews/by-key/${original.client_request_id}` : `/zhiyu-next/policy-change-commands/${original.client_request_id}`;
  const value = await request(url, 'GET', undefined, (row) => { context(row, environment); return row; });
  valid(value.idempotency_key === original.client_request_id && ['RECORDED', 'REJECTED', 'NOT_FOUND_NOT_FINAL'].includes(String(value.status)));
  const base = { simulation: true as const, environment_id: environment.environment_id, epoch_id: environment.epoch_id, client_request_id: original.client_request_id };
  if (value.status === 'NOT_FOUND_NOT_FINAL') return { ...base, status: 'PENDING' };
  if (value.status === 'REJECTED') { valid(object(value.original_request) && value.original_request.path === `/api/v1${original.path}` && same(value.original_request.body, original.body) && object(value.error) && typeof value.error.code === 'string'); return { ...base, status: 'REJECTED' }; }
  if (review) {
    valid(object(value.original_request) && value.original_request.path === `/api/v1${original.path}` && same(value.original_request.body, original.body));
    return { ...base, status: 'COMPLETED', result: await parseChangeReview(value.review, original, environment) as unknown as Record<string, unknown> };
  }
  valid(value.user_id === environment.dashboard.user_id && value.bank_authority === false && value.current_authority === false && value.not_found_is_final === false && same(value.original_request, original.body));
  const commit = await parseChangeCommit(value.response, original, environment);
  if (!commit.actual_version_id || commit.actual_configuration_hash !== original.body.reviewed_configuration_hash) return { ...base, status: 'PENDING', result: commit as unknown as Record<string, unknown> };
  return { ...base, status: 'COMPLETED', result: commit as unknown as Record<string, unknown> };
}
