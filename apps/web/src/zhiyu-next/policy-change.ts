import { request, ApiError } from '../api/http';
import { isRunId } from '../api/decisions';
import { parseLocalActorSession, type LocalActorSession } from '../api/local-actor';
import { parseMultiPreview, multiConfigurationHash, type MultiPreview, type MultiPreviewSource } from '../api/full-policy-change-multi';
import { parseFullGoalConfiguration } from '../api/full-goals';
import { releaseCanonicalJson } from '../api/goal-release-authorizations';
import { object } from '../features/policy-form';
import { context, same, safeConfiguration, type PolicyRecord } from './policies';
import type { PendingOperation } from './operation';
import type { NextState, OperationResult } from './api';

export const reviewedScopes = ['MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS', 'INDIVIDUAL_PRODUCT_CAPACITY', 'WHOLE_POSITION_RECOVERY_CANDIDATES', 'CURRENT_JOINT_GOAL_ALLOCATION'] as const;
export type ReviewedScope = typeof reviewedScopes[number];
export const scopeTitles: Record<ReviewedScope, string> = { MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS: '资金保护变化（保留原完整规划负担）', INDIVIDUAL_PRODUCT_CAPACITY: '每产品独立容量（不能相加为组合）', WHOLE_POSITION_RECOVERY_CANDIDATES: '逐持仓回收候选（不构成多仓执行）', CURRENT_JOINT_GOAL_ALLOCATION: '当前期目标分配（不构成多期最优）' };
export type ChangeReview = { protocol: 'reviewed-policy-change-record-v1' | 'reviewed-policy-change-record-v2'; view_protocol?: 'resolved-reviewed-policy-change-view-v1'; review_id: string; user_id: string; epoch_id: string; source_kind: 'MVP_POLICY' | 'FULL_POLICY'; policy_id: string; request: Record<string, unknown>; captured_at: string; expires_at: string; actor: LocalActorSession['principal']; source_basis: Record<string, unknown>; source_basis_hash: string; preview: MultiPreview; covered_scopes: ReviewedScope[]; uncovered_items: string[]; confirmation_eligible: boolean; review_hash: string; bank_authority: false; independent_financial_verification: false };
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
const sharedOriginalsProtocol = 'reviewed-policy-shared-source-originals-v1';
const canonicalReviews = new WeakMap<object, string>();
/** Canonical packed record text after validation, never the HTTP raw text or expanded display view. */
export const canonicalOriginalReview = (view: object): string | null => canonicalReviews.get(view) ?? null;
const closed = (value: unknown, keys: string[]): value is Record<string, unknown> => object(value) && Object.keys(value).sort().join('|') === keys.slice().sort().join('|');
const canonicalId = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value);
/** Count JSON values (root depth zero) while producing bounded canonical material before any expansion. */
function reviewMaterial(value: unknown, byteLimit = 64 * 1024 * 1024) {
  let bytes = 0, nodes = 0, depth = 0;
  const encoder = new TextEncoder();
  function token(text: string) { bytes += encoder.encode(text).byteLength; valid(bytes <= byteLimit); return text; }
  function visit(item: unknown, level: number, parent?: Record<string, unknown>, key?: string): string {
    valid(++nodes <= 1000000 && level <= 64); depth = Math.max(depth, level);
    if (Array.isArray(item)) { const parts = [token('[')]; for (const [i, child] of item.entries()) { if (i) parts.push(token(',')); parts.push(visit(child, level + 1)); } parts.push(token(']')); return parts.join(''); }
    if (object(item)) { const parts = [token('{')]; for (const [i, name] of Object.keys(item).sort().entries()) { if (i) parts.push(token(',')); parts.push(token(releaseCanonicalJson(name)), token(':'), visit(item[name], level + 1, item, name)); } parts.push(token('}')); return parts.join(''); }
    // Match the existing multiConfigurationHash's sole typed-float exception exactly.
    if (key === 'quantile' && parent && (parent.type === 'seasonal_reserve' || parent.name === 'rolling_window_quantile')) {
      valid(typeof item === 'number' && Number.isFinite(item) && item > 0 && item <= 1); const q = item as number;
      return token(q < 0.0001 ? q.toExponential().replace(/e(-?)(\d)$/, 'e$10$2') : Number.isInteger(q) ? `${q}.0` : String(q));
    }
    return token(releaseCanonicalJson(item));
  }
  const text = visit(value, 0); return { text, bytes, nodes, depth };
}
async function resolveSharedReviewPreview(row: Record<string, unknown>, tables: Record<string, unknown>): Promise<Record<string, unknown>> {
  const preview = row.preview as Record<string, unknown>, capsule = preview.source_originals;
  valid(preview.template_name === 'GoalAllocationPolicy' && preview.user_id === row.user_id && preview.epoch_id === row.epoch_id && preview.source_kind === row.source_kind && preview.policy_id === row.policy_id && preview.expected_version_id === (row.request as Record<string, unknown>).expected_version_id);
  valid(closed(capsule, ['protocol', 'source_basis_hash', 'evidence', 'joint_source_refs', 'originals', 'original_preview_hash', 'original_preview_bytes']));
  const packed = capsule as Record<string, unknown>;
  valid(packed.protocol === sharedOriginalsProtocol && packed.source_basis_hash === row.source_basis_hash && digest(packed.original_preview_hash) && Number.isSafeInteger(packed.original_preview_bytes) && Number(packed.original_preview_bytes) > 0 && Number(packed.original_preview_bytes) <= 64 * 1024 * 1024 && object(packed.originals));
  valid(closed(packed.evidence, ['row_ids', 'rows_hash']) && closed(packed.joint_source_refs, ['mode', 'point_count', 'source_refs_hash']));
  const evidence = packed.evidence as Record<string, unknown>, reference = packed.joint_source_refs as Record<string, unknown>, originals = packed.originals as Record<string, unknown>;
  valid(!Object.hasOwn(originals, 'evidence') && Array.isArray(tables.evidence_items) && Array.isArray(evidence.row_ids) && evidence.row_ids.every(canonicalId) && digest(evidence.rows_hash) && reference.mode === 'ALL_1098_HARD_POINTS_SHARE_VERIFIED_SOURCE_REFS' && reference.point_count === 1098 && digest(reference.source_refs_hash));
  const table = tables.evidence_items as Record<string, unknown>[], ids = evidence.row_ids as string[];
  valid(table.every((item) => canonicalId(item.id) && item.user_id === row.user_id) && ids.length === table.length && new Set(ids).size === ids.length && object(preview.source_counts) && preview.source_counts.evidence === ids.length);
  const byId = new Map(table.map((item) => [String(item.id), item]));
  valid(byId.size === table.length && ids.every((id) => byId.has(id)));
  const rows = ids.map((id) => byId.get(id)!);
  valid(object(originals.joint_binding)); const joint = originals.joint_binding as Record<string, unknown>;
  valid(joint.status === 'VERIFIED' && joint.original_point_count === 1098 && joint.full_point_count === 1098 && joint.bound_point_count === 1098 && Array.isArray(joint.reasons) && joint.reasons.length === 0 && object(joint.candidate) && Array.isArray(joint.verified_source_refs) && joint.verified_source_refs.length > 0 && joint.verified_source_refs.length <= 1000);
  const candidate = joint.candidate as Record<string, unknown>, refs = joint.verified_source_refs as Record<string, unknown>[];
  valid(candidate.user_id === row.user_id && Array.isArray(candidate.hard_protection_points) && candidate.hard_protection_points.length === 1098);
  const points = candidate.hard_protection_points as Record<string, unknown>[];
  const floors = ['obligation_floor_cents', 'living_floor_cents', 'emergency_floor_cents', 'owned_goal_cash_cents', 'other_protection_floor_cents'];
  valid(points.every((point) => closed(point, ['date', 'cash_cents', ...floors]) && typeof point.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(point.date) && Number.isFinite(Date.parse(`${point.date}T00:00:00Z`)) && new Date(`${point.date}T00:00:00Z`).toISOString().slice(0, 10) === point.date && Number.isSafeInteger(point.cash_cents) && floors.every((key) => Number.isSafeInteger(point[key]) && Number(point[key]) >= 0)));
  valid(refs.every((ref) => closed(ref, ['user_id', 'evidence_id', 'content_hash']) && ref.user_id === row.user_id && canonicalId(ref.evidence_id) && digest(ref.content_hash) && byId.get(ref.evidence_id)?.content_hash === ref.content_hash) && new Set(refs.map((ref) => ref.evidence_id)).size === refs.length);
  // Only these two fixed field locations can expand. Count their exact bytes/nodes/depth first.
  const bare = { ...preview, source_originals: originals }, bareMaterial = reviewMaterial(bare), rowMaterial = reviewMaterial(rows), refMaterial = reviewMaterial(refs);
  const expandedBytes = bareMaterial.bytes + 12 + rowMaterial.bytes + 1098 * (15 + refMaterial.bytes);
  valid(expandedBytes === packed.original_preview_bytes && expandedBytes <= 64 * 1024 * 1024 && bareMaterial.nodes + rowMaterial.nodes + 1098 * refMaterial.nodes <= 1000000 && Math.max(bareMaterial.depth, 2 + rowMaterial.depth, 6 + refMaterial.depth) <= 64);
  valid(await multiConfigurationHash({ rows }) === evidence.rows_hash && await multiConfigurationHash({ source_refs: refs }) === reference.source_refs_hash);
  const resolved = { ...preview, source_originals: { ...originals, evidence: rows, joint_binding: { ...joint, candidate: { ...candidate, hard_protection_points: points.map((point) => ({ ...point, source_refs: refs })) } } } };
  valid(await multiConfigurationHash(resolved) === packed.original_preview_hash);
  return resolved;
}
export async function parseChangeReview(value: unknown, original: PendingOperation, environment: NextState): Promise<ChangeReview> {
  const binding = changeIdentity(original);
  const shared = object(value) && value.protocol === 'reviewed-policy-change-record-v2';
  let packedText: string | null = null;
  if (shared) { valid(closed(value, ['protocol', 'review_id', 'user_id', 'epoch_id', 'source_kind', 'policy_id', 'request', 'captured_at', 'expires_at', 'actor', 'source_basis', 'source_basis_hash', 'preview', 'covered_scopes', 'uncovered_items', 'confirmation_eligible', 'review_hash', 'bank_authority', 'independent_financial_verification'])); packedText = reviewMaterial(value, 6 * 1024 * 1024).text; }
  valid(object(value) && (value.protocol === 'reviewed-policy-change-record-v1' || shared) && isRunId(value.review_id) && value.user_id === environment.dashboard.user_id && value.epoch_id === environment.epoch_id && value.source_kind === binding.sourceKind && value.policy_id === binding.policyId && same(value.request, original.body) && value.bank_authority === false && value.independent_financial_verification === false && time(value.captured_at) && time(value.expires_at) && Date.parse(value.expires_at) > Date.parse(value.captured_at) && Date.parse(value.expires_at) - Date.parse(value.captured_at) <= 900000 && object(value.preview) && object(value.source_basis) && digest(value.source_basis_hash) && digest(value.review_hash) && typeof value.confirmation_eligible === 'boolean' && strings(value.covered_scopes) && new Set(value.covered_scopes).size === value.covered_scopes.length && strings(value.uncovered_items));
  const row = value as Record<string, unknown>; const principal = actor(row.actor, environment);
  valid(Date.parse(String(row.expires_at)) <= Date.parse(principal.expires_at) && Date.parse(String(row.captured_at)) >= Date.parse(principal.issued_at));
  const basis = row.source_basis as Record<string, unknown>;
  if (shared) valid(closed(basis, ['protocol', 'user_id', 'epoch_id', 'local_day', 'registered_tables', 'excluded_metadata_tables', 'tables', 'row_counts', 'complete']) && canonicalId(basis.user_id) && canonicalId(basis.epoch_id) && strings(basis.excluded_metadata_tables) && same(basis.excluded_metadata_tables, [...new Set(basis.excluded_metadata_tables as string[])].sort()));
  valid(basis.protocol === 'registered-policy-change-source-basis-v1' && basis.complete === true && basis.user_id === environment.dashboard.user_id && basis.epoch_id === environment.epoch_id && strings(basis.registered_tables) && strings(basis.excluded_metadata_tables) && object(basis.tables) && object(basis.row_counts));
  const tables = basis.tables as Record<string, unknown>; const counts = basis.row_counts as Record<string, unknown>;
  const excluded = ['decision_runs', 'decision_constraints', 'audit_events', 'audit_epochs', 'audit_subject_snapshots', 'audit_archive_snapshots'];
  valid(same(basis.registered_tables, [...new Set(basis.registered_tables as string[])].sort()) && same(Object.keys(tables).sort(), Object.keys(counts).sort()) && same((basis.registered_tables as string[]).slice().sort(), [...Object.keys(tables), ...(basis.excluded_metadata_tables as string[])].sort()) && (basis.excluded_metadata_tables as string[]).every((name) => excluded.includes(name)) && Object.keys(tables).every((name) => !excluded.includes(name)) && Object.entries(tables).every(([name, rows]) => Array.isArray(rows) && rows.length <= 10000 && Number.isSafeInteger(counts[name]) && counts[name] === rows.length && rows.every((item) => object(item) && (shared ? canonicalId(item.id) : isRunId(item.id)) && (['users', 'asset_products', 'product_catalog_versions'].includes(name) || item.user_id === environment.dashboard.user_id)) && same(rows.map((item: Record<string, unknown>) => item.id), [...new Set(rows.map((item: Record<string, unknown>) => String(item.id)))].sort())) && Array.isArray(tables.users) && tables.users.length === 1 && object(tables.users[0]) && tables.users[0].id === environment.dashboard.user_id && await multiConfigurationHash(basis) === row.source_basis_hash);
  const content = { ...row }; delete content.review_hash; valid(await multiConfigurationHash(content) === row.review_hash);
  const preview = shared ? await resolveSharedReviewPreview(row, tables) : row.preview as Record<string, unknown>;
  valid(shared || !object(preview.source_originals) || preview.source_originals.protocol !== sharedOriginalsProtocol);
  const versionRows = tables[binding.sourceKind === 'MVP_POLICY' ? 'policy_versions' : 'full_policy_versions'];
  valid(Array.isArray(versionRows) && versionRows.some((version) => object(version) && version.id === original.body.expected_version_id && version.policy_id === binding.policyId && same(version.configuration, preview.before_configuration) && version.content_hash === preview.current_configuration_hash) && preview.as_of === row.captured_at);
  const parsed = await parseMultiPreview(preview, source(preview, environment), { expected_epoch_id: environment.epoch_id, expected_version_id: String(original.body.expected_version_id), configuration: original.body.configuration as Record<string, unknown> });
  const canConfirm = eligible(parsed); valid(row.confirmation_eligible === canConfirm && same(row.covered_scopes, canConfirm ? [parsed.financial_impact.scope] : []));
  valid(typeof basis.local_day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(basis.local_day) && (parsed.financial_impact.before === null || parsed.financial_impact.before.calculation_trace[0]?.date === basis.local_day));
  if (packedText !== null) { const view = { ...row, preview: parsed, view_protocol: 'resolved-reviewed-policy-change-view-v1' } as ChangeReview; canonicalReviews.set(view, packedText); return view; }
  return { ...row, preview: parsed } as ChangeReview;
}
export async function parseChangeCommit(value: unknown, original: PendingOperation, environment: NextState): Promise<ChangeCommit> {
  const binding = changeIdentity(original);
  valid(object(value) && value.protocol === 'reviewed-policy-change-commit-v1' && value.simulation === true && value.user_id === environment.dashboard.user_id && value.source_kind === binding.sourceKind && value.policy_id === binding.policyId && value.idempotency_key === original.client_request_id && same(value.original_request, original.body) && value.bank_authority === false && value.receipt_is_current_authority === false && value.full_financial_effects_verified === false && value.commit_state === 'COMMITTED' && ['COMMITTED_REVIEW_SCOPE_MATCHED', 'COMMITTED_BUT_FINANCIAL_UNKNOWN'].includes(String(value.status)) && object(value.lifecycle_receipt) && object(value.reviewed_preview) && digest(value.outer_request_hash) && digest(value.legacy_request_hash) && strings(value.covered_scopes) && (value.covered_scopes as string[]).includes(String(original.body.reviewed_scope)) && strings(value.uncovered_items) && strings(value.reasons));
  const row = value as Record<string, unknown>;
  const outer = { protocol: 'reviewed-policy-change-command-v1', user_id: environment.dashboard.user_id, source_kind: binding.sourceKind, policy_id: binding.policyId, body: original.body };
  valid(await multiConfigurationHash(outer) === row.outer_request_hash);
  const preview = row.reviewed_preview as Record<string, unknown>;
  const reviewed = await parseMultiPreview(preview, source(preview, environment), { expected_epoch_id: environment.epoch_id, expected_version_id: String(original.body.expected_version_id), configuration: original.body.configuration as Record<string, unknown> });
  valid(reviewed.candidate_configuration_hash === original.body.reviewed_configuration_hash);
  const legacy = binding.sourceKind === 'MVP_POLICY' ? { user_id: environment.dashboard.user_id, policy_id: binding.policyId, expected_version_id: original.body.expected_version_id, configuration: reviewed.after_configuration, reason: original.body.reason, accepted: true } : { protocol: 'full-policy-command-v1', kind: 'CHANGE', user_id: environment.dashboard.user_id, policy_id: binding.policyId, body: { expected_version_id: original.body.expected_version_id, configuration: reviewed.after_configuration, accepted: true, reviewed_hash: original.body.reviewed_configuration_hash, reason: original.body.reason, idempotency_key: original.client_request_id } };
  valid(await multiConfigurationHash(legacy) === row.legacy_request_hash);
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
