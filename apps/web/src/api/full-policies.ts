import type { components } from '../../../../packages/contracts/schema';
import { ApiError, request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { recordedFullPolicyLookupMatches, sameFullPolicyJson, validFullPolicyIntent } from '../features/full-policy-operation';
import type { FullPolicyIntent } from '../features/full-policy-operation';

export type FullPolicy = components['schemas']['FullPolicyView'];
export type FullVersion = components['schemas']['FullVersionView'];
export type FullCommand = components['schemas']['FullCommandView'];
export type FullPolicyList = components['schemas']['FullPolicyList'];
export type FullVersionList = components['schemas']['FullVersionList'];
export type FullCommandList = components['schemas']['FullCommandList'];
export type FullPolicyReceipt = components['schemas']['FullLifecycleResult'];
export type FullPolicyPreview = components['schemas']['FullChangePreview'];
export type FullPolicyLookup = components['schemas']['FullCommandLookup'];
export type TemplateCatalog = components['schemas']['TemplateCatalog'];
export type TemplateSchema = components['schemas']['TemplateSchemaResponse'];
export type TemplateCandidate = components['schemas']['ValidatedCandidate'];
export type TemplateName = TemplateCandidate['template_name'];
export const fullTemplateNames: readonly TemplateName[] = ['DatedExpensePolicy', 'PeriodicTransferPolicy', 'AssetAuthorizationPolicy', 'RecoveryPolicy', 'GoalAllocationPolicy', 'CrossGoalReallocationPolicy', 'SeasonalReservePolicy', 'InterventionPolicy'];
const allTemplates: readonly TemplateName[] = ['RecurringObligationPolicy', 'LivingReservePolicy', 'EmergencyBufferPolicy', 'DatedExpensePolicy', 'LongTermGoalPolicy', 'PeriodicTransferPolicy', 'AssetAuthorizationPolicy', 'RecoveryPolicy', 'GoalAllocationPolicy', 'CrossGoalReallocationPolicy', 'SeasonalReservePolicy', 'InterventionPolicy'];
const states = ['ACTIVE', 'CONFIRMED', 'SUSPENDED', 'EXPIRED', 'REVOKED'];
const kinds = ['CREATE', 'CHANGE', 'SUSPEND', 'REVOKE', 'RESUME', 'REFRESH_TIME'];
const originalResponses = new WeakMap<object, string>();
export const getOriginalFullPolicyResponse = (value: object): string | null => originalResponses.get(value) ?? null;
const text = (value: unknown): value is string => typeof value === 'string';
const uuid = (value: unknown): value is string => text(value) && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const digest = (value: unknown) => text(value) && /^[0-9a-f]{64}$/.test(value);
const nullable = (value: unknown, check: (value: unknown) => boolean) => value === null || check(value);
const timestamp = (value: unknown): value is string => text(value) && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const uniqueIds = (value: unknown): value is string[] => Array.isArray(value) && value.every(uuid) && new Set(value).size === value.length;
const strings = (value: unknown) => Array.isArray(value) && value.every(text);
function requireValue(condition: unknown): asserts condition { if (!condition) throw new Error('完整策略响应未通过原确认、身份或无银行授权校验'); }
function flags(value: unknown): asserts value is Record<string, unknown> { requireValue(object(value) && value.simulation === true && value.bank_authority === false && value.dedicated_audit_event === false); }
function save<T extends object>(value: T, originalText?: string): T { if (originalText !== undefined) originalResponses.set(value, originalText); return value; }
function safeConfiguration(value: unknown, depth = 0): boolean {
  if (depth > 64) return false; if (typeof value === 'number') return Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value));
  if (Array.isArray(value)) return value.every((item) => safeConfiguration(item, depth + 1));
  if (object(value)) return Object.values(value).every((item) => safeConfiguration(item, depth + 1));
  return value === null || typeof value === 'string' || typeof value === 'boolean';
}
export function parseFullVersion(value: unknown, policyId?: string): FullVersion {
  flags(value); requireValue(uuid(value.version_id) && uuid(value.policy_id) && (policyId === undefined || value.policy_id === policyId) && Number.isSafeInteger(value.version_number) && (value.version_number as number) >= 1 && digest(value.content_hash) && nullable(value.previous_hash, digest));
  requireValue(object(value.configuration) && safeConfiguration(value.configuration) && text(value.summary) && text(value.change_reason) && timestamp(value.confirmed_at) && timestamp(value.valid_from) && nullable(value.valid_until, timestamp) && uniqueIds(value.evidence_ids) && object(value.impact_analysis));
  const confirmation = value.confirmation;
  requireValue(object(confirmation) && confirmation.protocol === 'full-policy-confirmation-v1' && uuid(confirmation.user_id) && uuid(confirmation.epoch_id) && confirmation.policy_id === value.policy_id && confirmation.version_id === value.version_id && fullTemplateNames.includes(confirmation.template_name as TemplateName) && confirmation.reviewed_hash === value.content_hash && confirmation.accepted === true && confirmation.bank_authority === false && timestamp(confirmation.confirmed_at) && Date.parse(confirmation.confirmed_at) === Date.parse(value.confirmed_at));
  requireValue(uuid(confirmation.confirmation_evidence_id) && value.evidence_ids.includes(confirmation.confirmation_evidence_id) && text(confirmation.request_key) && confirmation.request_key.trim().length > 0 && digest(confirmation.request_hash));
  requireValue(value.valid_until === null || Date.parse(value.valid_until as string) > Date.parse(value.valid_from));
  requireValue(['CURRENT_EVIDENCE_MATCHED', 'RETAINED_IN_VERSION_CURRENT_EVIDENCE_MISSING'].includes(value.confirmation_evidence_status as string));
  requireValue(value.impact_analysis.bank_authority === false && value.impact_analysis.dedicated_audit_event === false && value.impact_analysis.action_dependencies_supported === false && value.impact_analysis.candidate_financial_status === 'NOT_IMPLEMENTED' && value.impact_analysis.candidate_financial_delta_cents === null && Array.isArray(value.impact_analysis.reference_snapshots));
  assertMoneyFields(value.configuration); return value as FullVersion;
}
export function parseFullPolicy(value: unknown, policyId?: string, originalText?: string): FullPolicy {
  flags(value); requireValue(uuid(value.policy_id) && (policyId === undefined || value.policy_id === policyId) && uuid(value.epoch_id) && fullTemplateNames.includes(value.template_name as TemplateName) && text(value.name) && states.includes(value.status as string) && text(value.effective_status) && typeof value.planning_confirmation_valid === 'boolean' && ['CURRENT', 'CHANGED_OR_UNAVAILABLE', 'ARCHIVED'].includes(value.reference_validation as string) && value.execution_support === 'NOT_IMPLEMENTED' && timestamp(value.updated_at));
  const version = parseFullVersion(value.current_version, value.policy_id); requireValue(version.confirmation.epoch_id === value.epoch_id && version.confirmation.template_name === value.template_name);
  requireValue(value.planning_confirmation_valid === (value.reference_validation === 'CURRENT' && ['ACTIVE', 'CONFIRMED'].includes(value.effective_status)));
  if (value.effective_status === 'ARCHIVED') requireValue(value.reference_validation === 'ARCHIVED');
  return save(value as FullPolicy, originalText);
}
export function parseFullPolicies(value: unknown, originalText?: string): FullPolicyList {
  flags(value); requireValue(Array.isArray(value.items) && value.items.length <= 10000); const items = value.items.map((item) => parseFullPolicy(item));
  requireValue(new Set(items.map((item) => item.policy_id)).size === items.length && new Set(items.map((item) => item.current_version.confirmation.user_id)).size <= 1); return save(value as FullPolicyList, originalText);
}
export function parseFullVersions(value: unknown, policyId: string, originalText?: string): FullVersionList {
  flags(value); requireValue(Array.isArray(value.items) && value.items.length > 0 && value.items.length <= 10000); const items = value.items.map((item) => parseFullVersion(item, policyId));
  const ids = new Set<string>(); for (const [index, version] of items.entries()) { requireValue(version.version_number === index + 1 && !ids.has(version.version_id) && version.previous_hash === (items[index - 1]?.content_hash ?? null)); ids.add(version.version_id); }
  requireValue(['user_id', 'epoch_id', 'template_name'].every((field) => new Set(items.map((version) => version.confirmation[field])).size === 1));
  return save(value as FullVersionList, originalText);
}
export function parseFullReceipt(value: unknown, intent?: FullPolicyIntent): FullPolicyReceipt {
  flags(value); requireValue(value.receipt_is_current_authority === false && value.action_dependencies_supported === false && value.requires_recompute === true && uuid(value.policy_id) && uuid(value.epoch_id) && uuid(value.version_id) && uuid(value.command_id) && Number.isSafeInteger(value.command_number) && (value.command_number as number) >= 1 && states.includes(value.status as string) && digest(value.configuration_hash) && uniqueIds(value.invalidated_action_ids) && uniqueIds(value.inflight_action_ids));
  requireValue(value.command_number === 1 ? value.previous_command_hash === null : digest(value.previous_command_hash));
  if (intent) {
    requireValue(validFullPolicyIntent(intent) && value.configuration_hash === intent.original_configuration_hash && (intent.policy_id === null || value.policy_id === intent.policy_id) && (intent.original_epoch_id === null || value.epoch_id === intent.original_epoch_id));
    if (intent.kind === 'CREATE') requireValue(value.command_number === 1);
    if (intent.kind === 'CREATE') requireValue(['ACTIVE', 'CONFIRMED', 'EXPIRED'].includes(value.status as string));
    if (intent.kind === 'RESUME') requireValue(['ACTIVE', 'CONFIRMED'].includes(value.status as string));
    if (['CHANGE', 'RESUME'].includes(intent.kind)) requireValue(value.version_id !== (intent.body as components['schemas']['FullResumeRequest']).expected_version_id);
    if (['SUSPEND', 'REVOKE'].includes(intent.kind)) requireValue(value.version_id === (intent.body as components['schemas']['FullStateRequest']).expected_version_id && value.status === (intent.kind === 'SUSPEND' ? 'SUSPENDED' : 'REVOKED'));
  }
  return value as FullPolicyReceipt;
}
export function parseFullCommand(value: unknown, policyId?: string): FullCommand {
  flags(value); requireValue(uuid(value.command_id) && uuid(value.policy_id) && (policyId === undefined || value.policy_id === policyId) && uuid(value.version_id) && kinds.includes(value.kind as string) && Number.isSafeInteger(value.command_number) && (value.command_number as number) >= 1 && nullable(value.previous_hash, digest) && text(value.idempotency_key) && value.idempotency_key.trim().length > 0 && digest(value.request_hash) && nullable(value.previous_status, (status) => states.includes(status as string)) && states.includes(value.resulting_status as string) && digest(value.result_hash) && timestamp(value.created_at));
  const result = parseFullReceipt(value.result); requireValue(result.command_id === value.command_id && result.policy_id === value.policy_id && result.version_id === value.version_id && result.command_number === value.command_number && result.previous_command_hash === value.previous_hash && result.status === value.resulting_status);
  return value as FullCommand;
}
export function parseFullCommands(value: unknown, policyId: string, originalText?: string): FullCommandList {
  flags(value); requireValue(Array.isArray(value.items) && value.items.length > 0 && value.items.length <= 10000); const items = value.items.map((item) => parseFullCommand(item, policyId));
  requireValue(items[0]!.kind === 'CREATE'); const ids = new Set<string>(); const keys = new Set<string>();
  for (const [index, command] of items.entries()) { requireValue(command.command_number === index + 1 && !ids.has(command.command_id) && !keys.has(command.idempotency_key) && command.previous_hash === (items[index - 1]?.result_hash ?? null) && command.previous_status === (items[index - 1]?.resulting_status ?? null)); ids.add(command.command_id); keys.add(command.idempotency_key); }
  requireValue(new Set(items.map((command) => command.result.epoch_id)).size === 1);
  return save(value as FullCommandList, originalText);
}
export function parseFullPreview(value: unknown, policy: FullPolicy, originalText?: string): FullPolicyPreview {
  flags(value); requireValue(value.preview_only === true && value.policy_id === policy.policy_id && value.epoch_id === policy.epoch_id && value.expected_version_id === policy.current_version.version_id && timestamp(value.as_of) && object(value.before_configuration) && sameFullPolicyJson(value.before_configuration, policy.current_version.configuration) && object(value.after_configuration) && safeConfiguration(value.after_configuration) && digest(value.configuration_hash) && strings(value.changed_fields) && digest(value.current_fact_digest));
  requireValue(Array.isArray(value.reference_snapshots) && uniqueIds(value.relevant_goal_ids) && uniqueIds(value.relevant_position_ids) && uniqueIds(value.relevant_current_action_ids) && value.candidate_financial_status === 'NOT_IMPLEMENTED' && value.delta_safe_idle_cents === null && value.delta_goal_allocation_cents === null && value.delta_position_principal_cents === null && value.future_action_impact === 'NOT_IMPLEMENTED_NO_FULL_EXECUTION_ADAPTER' && strings(value.limitations));
  const boundary = value.current_financial_boundary;
  requireValue(object(boundary) && boundary.financial_only === true && ['PROVEN', 'NOT_PROVEN', 'INCOMPLETE'].includes(boundary.state as string) && ['READY', 'LIQUIDITY_RISK', 'INSUFFICIENT_EVIDENCE'].includes(boundary.status as string) && ['safe_idle_cents', 'minimum_margin_cents', 'deficit_cents'].every((key) => nullable(boundary[key], Number.isSafeInteger)));
  assertMoneyFields(value.before_configuration); assertMoneyFields(value.after_configuration); assertMoneyFields(boundary); return save(value as FullPolicyPreview, originalText);
}
export function parseFullLookup(value: unknown, intent: FullPolicyIntent, originalText?: string): FullPolicyLookup {
  requireValue(object(value) && value.simulation === true && value.bank_authority === false && value.dedicated_audit_event === false && value.receipt_is_current_authority === false && value.not_found_is_final === false && value.idempotency_key === intent.body.idempotency_key);
  if (value.status === 'NOT_FOUND') requireValue(value.original_request === null && value.request_hash === null && value.command === null);
  else { requireValue(recordedFullPolicyLookupMatches(intent, value)); parseFullCommand(value.command, intent.policy_id ?? undefined); }
  return save(value as FullPolicyLookup, originalText);
}
export const getFullPolicies = () => request<FullPolicyList>('/full-policies', 'GET', undefined, parseFullPolicies);
export function getFullPolicy(id: string): Promise<FullPolicy> { requireValue(uuid(id)); return request<FullPolicy>(`/full-policies/${id}`, 'GET', undefined, (value, original) => parseFullPolicy(value, id, original)); }
export function getFullPolicyVersions(id: string): Promise<FullVersionList> { requireValue(uuid(id)); return request<FullVersionList>(`/full-policies/${id}/versions`, 'GET', undefined, (value, original) => parseFullVersions(value, id, original)); }
export function getFullPolicyCommands(id: string): Promise<FullCommandList> { requireValue(uuid(id)); return request<FullCommandList>(`/full-policies/${id}/commands`, 'GET', undefined, (value, original) => parseFullCommands(value, id, original)); }
export function previewFullPolicyChange(policy: FullPolicy, configuration: Record<string, unknown>): Promise<FullPolicyPreview> {
  requireValue(uuid(policy.policy_id) && object(configuration) && safeConfiguration(configuration)); assertMoneyFields(configuration);
  return request<FullPolicyPreview>(`/full-policies/${policy.policy_id}/change-preview`, 'POST', { expected_version_id: policy.current_version.version_id, configuration }, (value, original) => parseFullPreview(value, policy, original));
}
export function sendFullPolicyCommand(intent: FullPolicyIntent): Promise<FullPolicyReceipt> {
  requireValue(validFullPolicyIntent(intent)); return request<FullPolicyReceipt>(intent.path, 'POST', JSON.parse(intent.body_json), (value, original) => save(parseFullReceipt(value, intent), original));
}
export function lookupFullPolicyCommand(intent: FullPolicyIntent): Promise<FullPolicyLookup> {
  requireValue(validFullPolicyIntent(intent)); return request<FullPolicyLookup>(`/full-policies/commands/by-key/${encodeURIComponent(intent.body.idempotency_key)}`, 'GET', undefined, (value, original) => parseFullLookup(value, intent, original));
}

/** Catalog/Schema endpoints intentionally lack simulation; their candidate/no-authority flags have their own check. */
async function templateRequest<T extends object>(path: string, parse: (value: unknown) => T, body?: unknown): Promise<T> {
  let response: Response; try { response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? ''}/api/v1/policy-templates${path}`, { method: body === undefined ? 'GET' : 'POST', ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }); } catch { throw new ApiError('模板读取或校验连接中断，没有提交策略确认', 0, 'NETWORK_ERROR', null); }
  let original: string; let value: unknown; try { original = await response.text(); value = JSON.parse(original); } catch { throw new ApiError('模板响应未通过JSON校验', response.status, 'INVALID_RESPONSE', null); }
  if (!response.ok) { const error = object(value) && object(value.error) ? value.error : null; throw new ApiError(text(error?.message) ? error.message : '模板字段或关联条件未通过服务器校验', response.status, text(error?.code) ? error.code : 'TEMPLATE_VALIDATION_FAILED', text(error?.request_id) ? error.request_id : null); }
  return save(parse(value), original);
}
export function getFullTemplateCatalog(): Promise<TemplateCatalog> {
  return templateRequest('', (value) => { requireValue(object(value) && value.candidate_only === true && value.authority_granted === false && Array.isArray(value.templates) && value.templates.length === allTemplates.length);
    requireValue(new Set(value.templates.map((item) => object(item) ? item.template_name : null)).size === allTemplates.length && value.templates.every((item) => object(item) && allTemplates.includes(item.template_name as TemplateName) && text(item.full_configuration_type) && nullable(item.mvp_configuration_type, text) && Array.isArray(item.available_versions) && item.available_versions.includes('FULL_V1'))); return value as TemplateCatalog; });
}
export function getFullTemplateSchema(template: TemplateName): Promise<TemplateSchema> {
  requireValue(allTemplates.includes(template)); return templateRequest(`/${template}/schema?dsl_version=FULL_V1`, (value) => { requireValue(object(value) && value.template_name === template && value.dsl_version === 'FULL_V1' && object(value.json_schema) && digest(value.schema_sha256) && value.cross_field_validation_required === true && value.candidate_only === true && value.authority_granted === false); return value as TemplateSchema; });
}
export function validateFullPolicyCandidate(template: TemplateName, configuration: Record<string, unknown>): Promise<TemplateCandidate> {
  requireValue(allTemplates.includes(template) && object(configuration) && safeConfiguration(configuration)); assertMoneyFields(configuration);
  return templateRequest('/validate', (value) => { requireValue(object(value) && value.template_name === template && value.dsl_version === 'FULL_V1' && object(value.normalized_configuration) && safeConfiguration(value.normalized_configuration) && digest(value.configuration_hash) && value.candidate_only === true && value.authority_granted === false && value.reference_validation_pending === true); assertMoneyFields(value.normalized_configuration); return value as TemplateCandidate; }, { template_name: template, dsl_version: 'FULL_V1', configuration });
}
