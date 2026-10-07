import { request } from '../api/http';
import { object } from '../features/policy-form';
import { spendingHash as hash, spendingUUID as uuid, spendingDigest as digest, spendingCanonicalJson as canonical } from '../api/spending-evidence';
import { parseLocalActorSession } from '../api/local-actor';
import type { Lifecycle } from '../api/policies';
import type { NextState, OperationResult } from './api';
import { context } from './policies';
import { assetCheck as check, assetExact as exact } from './asset-recovery';
import { initialForm, type Schema } from './policy-schema';
import type { PendingOperation } from './operation';

const base = '/zhiyu-next/assets';
export const permissionProtocol = 'zhiyu-asset-native-permission-v1';
const identityFields = ['simulation', 'variant', 'environment_id', 'epoch_id', 'user_id', 'protocol', 'source_kind', 'dsl_version'];
export const isPermissionPath = (path: string) => path === `${base}/mvp-permissions/candidates` || path === `${base}/mvp-permissions/confirm`;
export type NativePermissionConfiguration = Record<string, unknown> & { type: 'asset_authorization'; scope: 'general_idle_funds' | 'goal'; goal_id: string | null; allowed_asset_classes: string[]; max_auto_managed_cents: number; single_action_cap_cents: number; allow_auto_recovery_without_penalty: boolean; allow_early_withdrawal_with_penalty: boolean };
export type PermissionRelationship = { source_kind: 'FULL'; policy_id: string; version_id: string; configuration_hash: string; relationship_is_authority: false };
export type PermissionSchema = { configuration_schema: Schema; summary: string };
export type PermissionCandidate = { candidate_id: string; canonical_configuration: NativePermissionConfiguration; configuration_hash: string; full_relationship: PermissionRelationship; can_confirm: boolean; issues: string[]; summary: string };
export type PermissionConfirmation = { candidate_id: string; policy_id: string; current_version_id: string; canonical_configuration: NativePermissionConfiguration; configuration_hash: string; full_relationship: PermissionRelationship; native_result: Lifecycle };
export type PermissionUpdate = { candidate: PermissionCandidate | null; confirmation: PermissionConfirmation | null };
const cents = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const date = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().startsWith(value);
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
export function parseNativePermissionConfiguration(value: unknown): NativePermissionConfiguration {
  check(object(value) && exact(value, ['type', 'name', 'valid_from', 'valid_until', 'scope', 'goal_id', 'allowed_asset_classes', 'max_auto_managed_cents', 'single_action_cap_cents', 'max_redemption_delay_days', 'max_lock_days', 'max_principal_risk_level', 'allow_auto_recovery_without_penalty', 'allow_early_withdrawal_with_penalty']) && value.type === 'asset_authorization' && (value.name === null || typeof value.name === 'string' && !!value.name.trim() && value.name.length <= 120) && (value.valid_from === null || date(value.valid_from)) && (value.valid_until === null || date(value.valid_until)) && (value.valid_from === null || value.valid_until === null || value.valid_from <= value.valid_until));
  check(['general_idle_funds', 'goal'].includes(String(value.scope)) && (value.scope === 'goal' ? uuid(value.goal_id) : value.goal_id === null) && Array.isArray(value.allowed_asset_classes) && value.allowed_asset_classes.length > 0 && new Set(value.allowed_asset_classes).size === value.allowed_asset_classes.length && value.allowed_asset_classes.every((v) => ['CASH', 'CASH_MGMT_T0', 'CASH_MGMT_T1', 'FIXED_DEPOSIT'].includes(String(v))) && ['max_auto_managed_cents', 'single_action_cap_cents', 'max_redemption_delay_days', 'max_lock_days', 'max_principal_risk_level'].every((k) => cents(value[k])) && Number(value.max_principal_risk_level) <= 5 && Number(value.single_action_cap_cents) <= Number(value.max_auto_managed_cents) && typeof value.allow_auto_recovery_without_penalty === 'boolean' && typeof value.allow_early_withdrawal_with_penalty === 'boolean');
  return value as NativePermissionConfiguration;
}
export function validatePermissionBody(body: Record<string, unknown>, path: string, epoch: string, client: string): void {
  check(isPermissionPath(path) && body.expected_epoch_id === epoch && uuid(body.client_request_id) && body.client_request_id === client);
  if (path.endsWith('/candidates')) { check(exact(body, ['expected_epoch_id', 'full_policy_id', 'expected_full_policy_version_id', 'configuration', 'client_request_id']) && uuid(body.full_policy_id) && uuid(body.expected_full_policy_version_id)); parseNativePermissionConfiguration(body.configuration); }
  else check(exact(body, ['expected_epoch_id', 'candidate_id', 'reviewed_hash', 'accepted', 'client_request_id']) && uuid(body.candidate_id) && digest(body.reviewed_hash) && body.accepted === true);
}
function permissionIdentity(value: unknown, environment: NextState): asserts value is Record<string, unknown> {
  context(value, environment); check(value.user_id === environment.dashboard.user_id && value.protocol === permissionProtocol && value.source_kind === 'MVP' && value.dsl_version === 'MVP_V1' && value.bank_authority === false);
}
export function parsePermissionRelationship(value: unknown): PermissionRelationship {
  check(object(value) && exact(value, ['source_kind', 'policy_id', 'version_id', 'configuration_hash', 'relationship_is_authority']) && value.source_kind === 'FULL' && uuid(value.policy_id) && uuid(value.version_id) && digest(value.configuration_hash) && value.relationship_is_authority === false); return value as PermissionRelationship;
}
export async function getPermissionSchema(environment: NextState): Promise<PermissionSchema> {
  const value = await request(`${base}/mvp-permissions/schema`, 'GET', undefined, (value) => { permissionIdentity(value, environment); return value; });
  check(exact(value, [...identityFields, 'template_name', 'configuration_schema', 'bank_authority', 'relationship_is_authority', 'summary']) && value.template_name === 'AssetAuthorizationPolicy' && value.relationship_is_authority === false && object(value.configuration_schema) && typeof value.summary === 'string');
  initialForm(value.configuration_schema); return { configuration_schema: value.configuration_schema, summary: value.summary };
}
export async function parsePermissionCandidate(value: unknown, body: Record<string, unknown>, environment: NextState): Promise<PermissionCandidate> {
  validatePermissionBody(body, `${base}/mvp-permissions/candidates`, environment.epoch_id, String(body.client_request_id));
  permissionIdentity(value, environment);
  check(exact(value, [...identityFields, 'client_request_id', 'candidate_id', 'template_name', 'canonical_configuration', 'configuration_hash', 'full_relationship', 'can_confirm', 'issues', 'bank_authority', 'permission_confirmed', 'execution_confirmation_created', 'summary']) && value.template_name === 'AssetAuthorizationPolicy' && value.client_request_id === body.client_request_id && value.candidate_id === body.client_request_id && value.permission_confirmed === false && value.execution_confirmation_created === false && value.can_confirm === true && Array.isArray(value.issues) && value.issues.length === 0 && typeof value.summary === 'string');
  const configuration = parseNativePermissionConfiguration(value.canonical_configuration); const input = parseNativePermissionConfiguration(body.configuration); check(canonical(configuration) === canonical(input) && value.configuration_hash === await hash(configuration));
  const relationship = parsePermissionRelationship(value.full_relationship); check(relationship.policy_id === body.full_policy_id && relationship.version_id === body.expected_full_policy_version_id);
  return value as unknown as PermissionCandidate;
}
export async function parsePermissionConfirmation(value: unknown, body: Record<string, unknown>, candidate: PermissionCandidate, environment: NextState): Promise<PermissionConfirmation> {
  validatePermissionBody(body, `${base}/mvp-permissions/confirm`, environment.epoch_id, String(body.client_request_id));
  permissionIdentity(value, environment);
  check(exact(value, [...identityFields, 'client_request_id', 'candidate_id', 'template_name', 'policy_id', 'current_version_id', 'configuration_hash', 'canonical_configuration', 'full_relationship', 'native_result', 'original_signed_binding', 'permission_confirmed', 'bank_authority', 'execution_confirmation_created']) && value.client_request_id === body.client_request_id && value.candidate_id === body.candidate_id && value.candidate_id === candidate.candidate_id && value.template_name === 'AssetAuthorizationPolicy' && value.permission_confirmed === true && value.execution_confirmation_created === false && uuid(value.policy_id) && uuid(value.current_version_id) && value.configuration_hash === body.reviewed_hash && value.configuration_hash === candidate.configuration_hash && canonical(parseNativePermissionConfiguration(value.canonical_configuration)) === canonical(candidate.canonical_configuration) && canonical(parsePermissionRelationship(value.full_relationship)) === canonical(candidate.full_relationship));
  const native = value.native_result;
  check(object(native) && exact(native, ['simulation', 'policy_id', 'current_version_id', 'previous_version_id', 'status', 'effective_status', 'invalidated_action_ids', 'inflight_action_ids', 'requires_recompute']) && native.simulation === true && native.policy_id === value.policy_id && native.current_version_id === value.current_version_id && native.previous_version_id === null && native.status === 'CONFIRMED' && ['ACTIVE', 'CONFIRMED', 'EXPIRED'].includes(String(native.effective_status)) && Array.isArray(native.invalidated_action_ids) && native.invalidated_action_ids.length === 0 && Array.isArray(native.inflight_action_ids) && native.inflight_action_ids.length === 0 && native.requires_recompute === true);
  const binding = value.original_signed_binding;
  check(object(binding) && exact(binding, ['protocol', 'source_kind', 'dsl_version', 'user_id', 'epoch_id', 'candidate_id', 'canonical_configuration', 'configuration_hash', 'native_policy_id', 'native_version_id', 'native_proposal_id', 'native_declaration_evidence_id', 'original_declaration_request', 'original_confirmation_request', 'full_relationship', 'principal_at_confirmation', 'confirmed_at', 'bank_authority', 'execution_confirmation_created']) && binding.protocol === permissionProtocol && binding.source_kind === 'MVP' && binding.dsl_version === 'MVP_V1' && binding.user_id === environment.dashboard.user_id && binding.epoch_id === environment.epoch_id && binding.candidate_id === candidate.candidate_id && binding.configuration_hash === candidate.configuration_hash && canonical(binding.canonical_configuration) === canonical(candidate.canonical_configuration) && canonical(binding.full_relationship) === canonical(candidate.full_relationship) && binding.native_policy_id === value.policy_id && binding.native_version_id === value.current_version_id && uuid(binding.native_proposal_id) && uuid(binding.native_declaration_evidence_id) && binding.bank_authority === false && binding.execution_confirmation_created === false && canonical(binding.original_confirmation_request) === canonical(body) && time(binding.confirmed_at));
  const declaration = binding.original_declaration_request;
  check(object(declaration) && exact(declaration, ['configuration', 'expected_epoch_id', 'idempotency_key', 'source_proposal_id']) && declaration.expected_epoch_id === environment.epoch_id && declaration.idempotency_key === `zhiyu-next:${environment.epoch_id}:asset-permission:${candidate.candidate_id}` && declaration.source_proposal_id === null && canonical(declaration.configuration) === canonical(candidate.canonical_configuration));
  const principal = parseLocalActorSession({ simulation: true, bank_authority: false, confirms_financial_action: false, principal: binding.principal_at_confirmation }).principal;
  check(principal.user_id === environment.dashboard.user_id && principal.role === 'USER' && Date.parse(principal.issued_at) <= Date.parse(binding.confirmed_at) && Date.parse(binding.confirmed_at) < Date.parse(principal.expires_at));
  return value as unknown as PermissionConfirmation;
}
async function permissionWire(key: string, environment: NextState) {
  check(uuid(key)); const value = await request(`${base}/mvp-permission-commands/${key}`, 'GET', undefined, (value) => { permissionIdentity(value, environment); return value; });
  check(exact(value, [...identityFields, 'client_request_id', 'status', 'not_found_is_final', 'original_request', 'result', 'bank_authority']) && value.client_request_id === key && value.not_found_is_final === false && ['COMPLETED', 'REJECTED', 'NOT_FOUND_NOT_FINAL'].includes(String(value.status)));
  if (value.status === 'NOT_FOUND_NOT_FINAL') check(value.original_request === null && value.result === null);
  else check(object(value.original_request) && exact(value.original_request, ['path', 'body']) && object(value.original_request.body) && typeof value.original_request.path === 'string');
  return value;
}
export async function readPermissionOriginal(pending: PendingOperation, environment: NextState): Promise<OperationResult & { permission?: PermissionUpdate }> {
  check(isPermissionPath(pending.path) && pending.environment_id === environment.environment_id && pending.epoch_id === environment.epoch_id);
  const value = await permissionWire(pending.client_request_id, environment); const result: OperationResult & { permission?: PermissionUpdate } = { simulation: true, environment_id: pending.environment_id, epoch_id: pending.epoch_id, client_request_id: pending.client_request_id, status: 'PENDING' };
  if (value.status === 'NOT_FOUND_NOT_FINAL') return result;
  check(object(value.original_request) && value.original_request.path === `/api/v1${pending.path}` && canonical(value.original_request.body) === canonical(pending.body));
  if (value.status === 'REJECTED') { check(object(value.result) && exact(value.result, ['code', 'message', 'status_code']) && typeof value.result.code === 'string' && !!value.result.code && typeof value.result.message === 'string' && !!value.result.message && Number.isSafeInteger(value.result.status_code) && Number(value.result.status_code) >= 400 && Number(value.result.status_code) < 500); return { ...result, status: 'REJECTED', result: value.result }; }
  if (pending.path.endsWith('/candidates')) result.permission = { candidate: await parsePermissionCandidate(value.result, pending.body, environment), confirmation: null };
  else {
    const original = await permissionWire(String(pending.body.candidate_id), environment);
    check(original.status === 'COMPLETED' && object(original.original_request) && original.original_request.path === `/api/v1${base}/mvp-permissions/candidates` && object(original.original_request.body) && original.original_request.body.client_request_id === pending.body.candidate_id);
    const candidate = await parsePermissionCandidate(original.result, original.original_request.body, environment);
    result.permission = { candidate: null, confirmation: await parsePermissionConfirmation(value.result, pending.body, candidate, environment) };
  }
  return { ...result, status: 'COMPLETED' };
}
