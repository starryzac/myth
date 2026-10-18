import { request } from '../api/http';
import { object } from '../features/policy-form';
import { parseFullPolicy } from '../api/full-policies';
import type { FullPolicy } from '../api/full-policies';
import { parseFullGoalModel } from '../api/full-goals';
import type { ReleaseSource } from '../api/goal-release-authorizations';
import { releaseDigest, releaseHash, releaseUUID, releaseUUID5 } from '../api/goal-release-authorizations';
import type { NextState, OperationResult } from './api';
import type { PendingOperation } from './operation';
import { context, same } from './policies';
import { parseTransferRecovery, parseTransferRequest, transferCheck as check, transferExact as exact, transferKey, transferPath, transferPrefix } from './goal-transfer-recovery';
import type { TransferPreviewRequest, TransferRecovery } from './goal-transfer-recovery';

type GoalBinding = { goal_id: string; account_id: string; policy_id: string; policy_version_id: string; full_model_evidence_id: string; full_model_evidence_hash: string; full_configuration_hash: string; minimum_guarantee_cents: number; target_cents: number };
type Permission = { role: 'RELEASE_SOURCE' | 'RECEIVE_TARGET'; goal: GoalBinding; amount_cents: number };
export type TransferScopeV1 = { protocol: 'full-goal-transfer-paired-scope-v1'; user_id: string; epoch_id: string; policy: { policy_id: string; version_id: string; configuration: Record<string, unknown>; configuration_hash: string }; permissions: [Permission, Permission]; timezone: 'Asia/Shanghai' | 'UTC'; valid_from: string; valid_until: string; authority_kind: 'NEW_USER_PAIRED_GOAL_TRANSFER'; standing_authority: false; inherits_protected_cash_authority: false; cumulative_scope: 'POLICY_ID_ALL_VERSIONS' };
export type TransferCurrentScope = TransferScopeV1 & { source_provenance_version: 'goal-transfer-current-provenance-v1'; current_provenance_hash: string };
export type TransferScope = TransferScopeV1 | TransferCurrentScope;
export type TransferPreview = { schema_version: 'goal-transfer-preview-v1'; preview_id: string; user_id: string; epoch_id: string; scope: TransferScope; scope_hash: string; input_hash: string; source_available_cents: number; target_current_cents: number; policy_used_cents: number; protection_point_count: 1098; expires_at: string; bank_authority: false };
type PrepareRequest = { expected_epoch_id: string; preview_id: string; reviewed_scope_hash: string; accepted: true; idempotency_key: string };
type TransferEffect = Record<string, unknown> & { scope: TransferScope; scope_hash: string; operation_id: string; bank_idempotency_key: string; fee_cents: number; loss_cents: number; expires_at: string };
export type TransferAction = { schema_version: 'goal-transfer-action-v1'; simulation: true; user_id: string; as_of: string; action_id: string; original_action_status: string; original_command: { protocol: 'full-goal-transfer-bank-v1'; effect: TransferEffect; effect_hash: string }; original_prepare_request: PrepareRequest; original_request_hash: string; authorization: Record<string, unknown>; authorization_evidence_id: string; authorization_evidence_hash: string; bank_operation_id: string | null; original_bank_status: string | null; bank_settlement_legs_verified: boolean; receipt_id: string | null; service_receipt_verified: boolean; original_receipt: Record<string, unknown> | null; unresolved: boolean; read_only_response: true; current_authority_assessed: false; receipt_is_current_authority: false };
export type TransferContext = { policy: FullPolicy; sources: ReleaseSource[]; targets: ReleaseSource[]; originals: TransferAction[]; any_unresolved: boolean };
export type TransferUpdate = { environment_id: string; recovery: TransferRecovery; phase: 'PREVIEW' | 'PREPARE' | 'EXECUTE'; preview: TransferPreview | null; action: TransferAction | null; resume_original: boolean };
export type TransferCommand = { path: string; body: Record<string, unknown>; recovery: TransferRecovery };
export type TransferPerform = (path: string, body: Record<string, unknown>, recovery: TransferRecovery) => Promise<void>;
export type TransferRead = OperationResult & { goal_transfer?: TransferUpdate };
const namespace = 'b360811c-b870-5162-a877-9f7eddb330af';
const cents = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
export const isTransferConfiguration = (value: Record<string, unknown>) => value.type === 'cross_goal_reallocation' && value.scope_version === 'GOAL_TRANSFER_V1' && value.destination_scope === 'GOAL';

function envelope(value: unknown, environment: NextState, kind: string): unknown {
  context(value, environment);
  check(exact(value, ['simulation', 'variant', 'environment_id', 'epoch_id', 'protocol', 'user_id', 'kind', 'native', 'bank_authority', 'receipt_is_current_authority']) && value.protocol === 'zhiyu-next-goal-transfer-read-v1' && value.user_id === environment.dashboard.user_id && value.kind === kind && value.bank_authority === false && value.receipt_is_current_authority === false);
  return value.native;
}
export function transferAcknowledgment(value: unknown, environment: NextState) { context(value, environment); check(value.protocol === 'zhiyu-next-goal-transfer-read-v1' && value.user_id === environment.dashboard.user_id && value.bank_authority === false && value.receipt_is_current_authority === false); return value; }
async function parseScope(value: unknown, environment: NextState): Promise<TransferScope> {
  check(object(value));
  const common = ['protocol', 'user_id', 'epoch_id', 'policy', 'permissions', 'timezone', 'valid_from', 'valid_until', 'authority_kind', 'standing_authority', 'inherits_protected_cash_authority', 'cumulative_scope'];
  // Public reads stay v1. Only this closed, paired scope extension selects current provenance.
  // Its economic scope-input hash differs from the effect's complete frozen proof hash.
  check(exact(value, common) || exact(value, [...common, 'source_provenance_version', 'current_provenance_hash']) && value.source_provenance_version === 'goal-transfer-current-provenance-v1' && releaseDigest(value.current_provenance_hash));
  check(value.protocol === 'full-goal-transfer-paired-scope-v1' && value.user_id === environment.dashboard.user_id && value.epoch_id === environment.epoch_id && value.authority_kind === 'NEW_USER_PAIRED_GOAL_TRANSFER' && value.standing_authority === false && value.inherits_protected_cash_authority === false && value.cumulative_scope === 'POLICY_ID_ALL_VERSIONS' && ['UTC', 'Asia/Shanghai'].includes(String(value.timezone)) && time(value.valid_from) && time(value.valid_until) && Date.parse(value.valid_until) > Date.parse(value.valid_from) && Date.parse(value.valid_until) - Date.parse(value.valid_from) <= 900000);
  const policy = value.policy;
  check(object(policy) && exact(policy, ['policy_id', 'version_id', 'configuration', 'configuration_hash']) && releaseUUID(policy.policy_id) && releaseUUID(policy.version_id) && object(policy.configuration) && isTransferConfiguration(policy.configuration) && policy.configuration.enabled === true && releaseDigest(policy.configuration_hash) && await releaseHash(policy.configuration) === policy.configuration_hash);
  const config = policy.configuration;
  check(Array.isArray(config.source_goal_ids) && Array.isArray(config.target_goal_ids) && cents(config.single_action_cap_cents) && config.single_action_cap_cents > 0 && cents(config.total_cap_cents) && config.total_cap_cents >= config.single_action_cap_cents && typeof config.valid_from === 'string' && typeof config.valid_until === 'string');
  check(Array.isArray(value.permissions) && value.permissions.length === 2);
  for (const [index, permission] of value.permissions.entries()) {
    check(object(permission) && exact(permission, ['role', 'goal', 'amount_cents']) && permission.role === (index === 0 ? 'RELEASE_SOURCE' : 'RECEIVE_TARGET') && cents(permission.amount_cents) && permission.amount_cents > 0 && permission.amount_cents <= config.single_action_cap_cents);
    const goal = permission.goal;
    check(object(goal) && exact(goal, ['goal_id', 'account_id', 'policy_id', 'policy_version_id', 'full_model_evidence_id', 'full_model_evidence_hash', 'full_configuration_hash', 'minimum_guarantee_cents', 'target_cents']) && ['goal_id', 'account_id', 'policy_id', 'policy_version_id', 'full_model_evidence_id'].every((key) => releaseUUID(goal[key])) && releaseDigest(goal.full_model_evidence_hash) && releaseDigest(goal.full_configuration_hash) && cents(goal.minimum_guarantee_cents) && cents(goal.target_cents) && goal.target_cents >= goal.minimum_guarantee_cents);
    check((index === 0 ? config.source_goal_ids : config.target_goal_ids).includes(goal.goal_id));
  }
  const scope = value as unknown as TransferScope;
  check(scope.permissions[0].goal.goal_id !== scope.permissions[1].goal.goal_id && scope.permissions[0].goal.account_id !== scope.permissions[1].goal.account_id && scope.permissions[0].amount_cents === scope.permissions[1].amount_cents);
  return scope;
}
function matchRequest(scope: TransferScope, original: TransferPreviewRequest) {
  const [source, target] = scope.permissions;
  check(scope.epoch_id === original.expected_epoch_id && scope.policy.policy_id === original.policy_id && scope.policy.version_id === original.expected_policy_version_id && source.goal.goal_id === original.source_goal_id && source.goal.policy_version_id === original.expected_source_policy_version_id && target.goal.goal_id === original.target_goal_id && target.goal.policy_version_id === original.expected_target_policy_version_id && source.amount_cents === original.amount_cents);
}
export async function parseTransferPreview(value: unknown, original: TransferPreviewRequest, environment: NextState): Promise<TransferPreview> {
  parseTransferRequest(original);
  check(object(value) && exact(value, ['schema_version', 'preview_id', 'user_id', 'epoch_id', 'scope', 'scope_hash', 'input_hash', 'source_available_cents', 'target_current_cents', 'policy_used_cents', 'protection_point_count', 'expires_at', 'bank_authority']) && value.schema_version === 'goal-transfer-preview-v1' && value.user_id === environment.dashboard.user_id && value.epoch_id === environment.epoch_id && releaseUUID(value.preview_id) && releaseDigest(value.scope_hash) && releaseDigest(value.input_hash) && value.bank_authority === false && value.protection_point_count === 1098 && ['source_available_cents', 'target_current_cents', 'policy_used_cents'].every((key) => cents(value[key])) && time(value.expires_at));
  const scope = await parseScope(value.scope, environment); matchRequest(scope, original);
  check(await releaseHash(scope) === value.scope_hash && value.preview_id === await releaseUUID5(namespace, `preview:${value.user_id}:${value.epoch_id}:${original.idempotency_key}`) && value.expires_at === scope.valid_until && Number(value.source_available_cents) >= original.amount_cents && BigInt(Number(value.policy_used_cents)) + BigInt(original.amount_cents) <= BigInt(Number(scope.policy.configuration.total_cap_cents)));
  return value as unknown as TransferPreview;
}
export async function parseTransferAction(value: unknown, environment: NextState, preview?: TransferPreview | null, expected?: PrepareRequest | null, previous?: TransferAction | null): Promise<TransferAction> {
  check(object(value) && exact(value, ['schema_version', 'simulation', 'user_id', 'as_of', 'action_id', 'original_action_status', 'original_command', 'original_prepare_request', 'original_request_hash', 'authorization', 'authorization_evidence_id', 'authorization_evidence_hash', 'bank_operation_id', 'original_bank_status', 'bank_settlement_legs_verified', 'receipt_id', 'service_receipt_verified', 'original_receipt', 'unresolved', 'read_only_response', 'current_authority_assessed', 'receipt_is_current_authority']) && value.schema_version === 'goal-transfer-action-v1' && value.simulation === true && value.user_id === environment.dashboard.user_id && time(value.as_of) && releaseUUID(value.action_id) && releaseDigest(value.original_request_hash) && value.read_only_response === true && value.current_authority_assessed === false && value.receipt_is_current_authority === false && ['PLANNED', 'AUTHORIZED', 'SUBMITTED', 'SUCCEEDED', 'RECONCILED', 'FAILED', 'UNKNOWN', 'INVALIDATED', 'CANCELLED'].includes(String(value.original_action_status)) && ['unresolved', 'service_receipt_verified', 'bank_settlement_legs_verified'].every((key) => typeof value[key] === 'boolean'));
  const command = value.original_command;
  check(object(command) && exact(command, ['protocol', 'effect', 'effect_hash']) && command.protocol === 'full-goal-transfer-bank-v1' && object(command.effect) && releaseDigest(command.effect_hash) && await releaseHash(command.effect) === command.effect_hash);
  const effect = command.effect, scope = await parseScope(effect.scope, environment);
  check(effect.protocol === 'full-goal-transfer-effect-v1' && effect.simulation === true && effect.action_type === 'TRANSFER_GOAL' && effect.operation_id === value.action_id && effect.authorization_id === value.action_id && effect.scope_hash === await releaseHash(scope) && ['authorization_evidence_hash', 'authorization_request_hash', 'source_provenance_hash', 'target_ownership_hash', 'financial_input_hash', 'protection_input_hash'].every((key) => releaseDigest(effect[key])) && releaseUUID(effect.authorization_evidence_id) && effect.authorization_evidence_id === value.authorization_evidence_id && effect.authorization_evidence_hash === value.authorization_evidence_hash && ['fee_cents', 'loss_cents', 'principal_change_cents', 'new_income_cents', 'available_income_change_cents', 'assigned_income_change_cents'].every((key) => effect[key] === 0) && time(effect.valid_from) && time(effect.expires_at) && Date.parse(effect.valid_from) >= Date.parse(scope.valid_from) && Date.parse(effect.expires_at) <= Date.parse(scope.valid_until) && Date.parse(effect.valid_from) < Date.parse(effect.expires_at));
  const prepare = value.original_prepare_request;
  check(object(prepare) && exact(prepare, ['expected_epoch_id', 'reviewed_scope_hash', 'accepted', 'idempotency_key', 'preview_id']) && prepare.expected_epoch_id === environment.epoch_id && prepare.accepted === true && prepare.reviewed_scope_hash === effect.scope_hash && releaseUUID(prepare.preview_id) && typeof prepare.idempotency_key === 'string');
  check(new RegExp(`^zhiyu-next:${environment.epoch_id}:goal-transfer-prepare:[0-9a-f-]{36}$`).test(prepare.idempotency_key) && await releaseUUID5(namespace, `${value.user_id}:${environment.epoch_id}:${prepare.idempotency_key}`) === value.action_id);
  const bankKey = `goal-transfer:${await releaseHash({ user_id: value.user_id, epoch_id: environment.epoch_id, key: prepare.idempotency_key })}`;
  check(effect.business_key === bankKey && effect.bank_idempotency_key === bankKey);
  const { preview_id: ignored, ...confirmation } = prepare; void ignored;
  const authorization = value.authorization;
  check(object(authorization) && authorization.protocol === 'full-goal-transfer-authorization-v1' && authorization.authorization_id === value.action_id && same(authorization.scope, scope) && authorization.scope_hash === effect.scope_hash && same(authorization.original_request, confirmation) && authorization.request_hash === effect.authorization_request_hash && authorization.request_hash === await releaseHash({ user_id: value.user_id, request: confirmation }) && object(authorization.principal_at_confirmation) && authorization.principal_at_confirmation.user_id === value.user_id && authorization.principal_at_confirmation.role === 'USER' && time(authorization.confirmed_at));
  check(Array.isArray(effect.source_uses) && effect.source_uses.length > 0 && effect.source_uses.length <= 10000);
  let total = 0n; const keys: string[] = [];
  for (const use of effect.source_uses) {
    check(object(use) && ['allocation_action_id', 'original_policy_version_id', 'fragment_id', 'origin_transaction_id', 'income_location_account_id'].every((key) => releaseUUID(use[key])) && ['allocation_effect_hash', 'allocation_bank_request_hash', 'allocation_action_request_hash'].every((key) => releaseDigest(use[key])) && cents(use.amount_cents) && use.amount_cents > 0);
    check(use.fragment_id === await releaseUUID5(String(use.origin_transaction_id), `income-location:${String(use.income_location_account_id)}`));
    total += BigInt(use.amount_cents); keys.push(`${String(use.allocation_action_id)}:${String(use.fragment_id)}`);
  }
  check(total === BigInt(scope.permissions[0].amount_cents) && new Set(keys).size === keys.length && same(keys, [...keys].sort()));
  if (preview) check(same(scope, preview.scope) && prepare.preview_id === preview.preview_id);
  if (expected) check(same(prepare, expected));
  if (previous) check(value.action_id === previous.action_id && same(command, previous.original_command) && same(prepare, previous.original_prepare_request) && value.original_request_hash === previous.original_request_hash && same(authorization, previous.authorization));
  check(value.bank_operation_id === null || releaseUUID(value.bank_operation_id)); check(value.receipt_id === null || releaseUUID(value.receipt_id));
  if (value.service_receipt_verified) {
    const receipt = value.original_receipt;
    check(object(receipt) && receipt.id === value.receipt_id && receipt.action_plan_id === value.action_id && receipt.user_id === value.user_id && cents(receipt.executed_cents) && receipt.fee_cents === 0 && receipt.loss_cents === 0 && ['SUCCEEDED', 'FAILED'].includes(String(receipt.status)) && !value.unresolved && value.bank_operation_id !== null);
    if (receipt.status === 'SUCCEEDED') check(value.bank_settlement_legs_verified && value.original_bank_status === 'SETTLED' && receipt.executed_cents === scope.permissions[0].amount_cents);
  } else check(value.original_receipt === null && value.receipt_id === null && !['SUCCEEDED', 'RECONCILED'].includes(String(value.original_action_status)));
  return value as unknown as TransferAction;
}
export async function getTransferContext(policyId: string, environment: NextState): Promise<TransferContext> {
  check(releaseUUID(policyId));
  return request(`${transferPrefix}/context/${policyId}`, 'GET', undefined, async (value) => {
    const native = envelope(value, environment, 'CONTEXT');
    check(object(native) && exact(native, ['policy', 'sources', 'targets', 'originals', 'any_unresolved']) && Array.isArray(native.sources) && Array.isArray(native.targets) && Array.isArray(native.originals) && native.originals.length <= 1000 && typeof native.any_unresolved === 'boolean');
    const policy = parseFullPolicy(native.policy); check(policy.policy_id === policyId && policy.epoch_id === environment.epoch_id && policy.current_version.confirmation.user_id === environment.dashboard.user_id && isTransferConfiguration(policy.current_version.configuration));
    const groups = [native.sources, native.targets] as ReleaseSource[][];
    for (const group of groups) { check(group.length > 0 && group.length <= 32 && new Set(group.map((row) => row.goal.id)).size === group.length); for (const row of group) { check(object(row) && exact(row, ['goal', 'model']) && object(row.goal) && releaseUUID(row.goal.id)); const model = parseFullGoalModel(row.model, row.goal); check(model.epoch_id === environment.epoch_id); } }
    const originals = await Promise.all(native.originals.map((row) => parseTransferAction(row, environment)));
    check(new Set(originals.map((row) => row.action_id)).size === originals.length && originals.every((row) => row.original_command.effect.scope.policy.policy_id === policyId) && (native.any_unresolved || !originals.some((row) => row.unresolved)));
    return { policy, sources: groups[0]!, targets: groups[1]!, originals, any_unresolved: native.any_unresolved };
  });
}
export function previewTransferCommand(entry: TransferContext, source: ReleaseSource, target: ReleaseSource, amount: number, environment: NextState, key: string = crypto.randomUUID()): TransferCommand {
  const preview_request = parseTransferRequest({ expected_epoch_id: environment.epoch_id, policy_id: entry.policy.policy_id, expected_policy_version_id: entry.policy.current_version.version_id, source_goal_id: source.goal.id, expected_source_policy_version_id: source.goal.policy_version_id, target_goal_id: target.goal.id, expected_target_policy_version_id: target.goal.policy_version_id, amount_cents: amount, idempotency_key: transferKey(environment.epoch_id, 'preview', key) });
  const { idempotency_key: ignored, ...body } = preview_request; void ignored;
  return { path: `${transferPrefix}/preview`, body: { ...body, client_request_id: key }, recovery: { protocol: 'zhiyu-next-goal-transfer-recovery-v1', user_id: environment.dashboard.user_id, preview_request, preview: null, action: null } };
}
export function prepareTransferCommand(update: TransferUpdate, environment: NextState, key: string = crypto.randomUUID()): TransferCommand {
  check(update.preview && update.phase === 'PREVIEW' && update.recovery.user_id === environment.dashboard.user_id && update.preview.epoch_id === environment.epoch_id && Date.parse(update.preview.expires_at) > Date.now());
  return { path: `${transferPrefix}/prepare`, body: { expected_epoch_id: environment.epoch_id, preview_id: update.preview.preview_id, reviewed_scope_hash: update.preview.scope_hash, accepted: true, client_request_id: key }, recovery: { ...update.recovery, preview: update.preview, action: null } };
}
export function nextTransferCommand(update: TransferUpdate): TransferCommand | null {
  const action = update.action;
  if (update.phase !== 'PREPARE' || !action || action.unresolved || action.service_receipt_verified || !['PLANNED', 'AUTHORIZED'].includes(action.original_action_status)) return null;
  check(update.recovery.preview); if (Date.parse(action.original_command.effect.expires_at) <= Date.now()) return null;
  return { path: `${transferPrefix}/actions/${action.action_id}/execute`, body: { expected_epoch_id: update.recovery.preview_request.expected_epoch_id, reviewed_effect_hash: action.original_command.effect_hash, accepted: true, client_request_id: crypto.randomUUID() }, recovery: { ...update.recovery, action } };
}
export async function validateTransferOriginal(original: PendingOperation, environment: NextState): Promise<TransferRecovery> {
  check(original.environment_id === environment.environment_id && original.epoch_id === environment.epoch_id && original.body_json === JSON.stringify(original.body));
  const recovery = parseTransferRecovery(original.goal_transfer_recovery, original.path, original.body, original.epoch_id, original.client_request_id);
  check(recovery.user_id === environment.dashboard.user_id);
  if (recovery.preview) await parseTransferPreview(recovery.preview, recovery.preview_request, environment);
  if (recovery.action) { check(recovery.preview); await parseTransferAction(recovery.action, environment, recovery.preview); }
  return recovery;
}
export async function readTransferOriginal(original: PendingOperation, environment: NextState): Promise<TransferRead> {
  const recovery = await validateTransferOriginal(original, environment), phase = transferPath(original.path)!;
  const base = { simulation: true as const, environment_id: environment.environment_id, epoch_id: environment.epoch_id, client_request_id: original.client_request_id };
  if (phase.phase === 'PREVIEW') {
    const native = await request(`${transferPrefix}/previews/${environment.epoch_id}/${original.client_request_id}`, 'GET', undefined, (value) => envelope(value, environment, 'PREVIEW_LOOKUP'));
    if (native === null) return { ...base, status: 'PENDING', goal_transfer: { environment_id: environment.environment_id, recovery, phase: phase.phase, preview: null, action: null, resume_original: true } };
    const preview = await parseTransferPreview(native, recovery.preview_request, environment);
    return { ...base, status: 'COMPLETED', goal_transfer: { environment_id: environment.environment_id, recovery, phase: phase.phase, preview, action: null, resume_original: false } };
  }
  let action: TransferAction;
  if (phase.phase === 'PREPARE') {
    const native = await request(`${transferPrefix}/operations/${environment.epoch_id}/${original.client_request_id}`, 'GET', undefined, (value) => envelope(value, environment, 'ACTION_LOOKUP'));
    check(object(native) && exact(native, ['user_id', 'epoch_id', 'idempotency_key', 'status', 'original', 'replacement_allowed']) && native.user_id === environment.dashboard.user_id && native.epoch_id === environment.epoch_id && native.idempotency_key === transferKey(environment.epoch_id, 'prepare', original.client_request_id) && native.replacement_allowed === false);
    if (native.status === 'NOT_FOUND_NOT_FINAL') { check(native.original === null); return { ...base, status: 'PENDING', goal_transfer: { environment_id: environment.environment_id, recovery, phase: phase.phase, preview: recovery.preview, action: null, resume_original: true } }; }
    check(native.status === 'RECORDED' && recovery.preview);
    const { client_request_id: ignored, ...body } = original.body; void ignored;
    action = await parseTransferAction(native.original, environment, recovery.preview, { ...body, idempotency_key: transferKey(environment.epoch_id, 'prepare', original.client_request_id) } as PrepareRequest);
  } else action = await request(`${transferPrefix}/actions/${phase.id}`, 'GET', undefined, (value) => parseTransferAction(envelope(value, environment, 'ACTION'), environment, recovery.preview, undefined, recovery.action));
  const terminal = phase.phase === 'PREPARE' ? !action.unresolved : action.service_receipt_verified || !action.unresolved && ['INVALIDATED', 'CANCELLED', 'FAILED'].includes(action.original_action_status);
  return { ...base, status: terminal ? 'COMPLETED' : 'PENDING', goal_transfer: { environment_id: environment.environment_id, recovery, phase: phase.phase, preview: recovery.preview, action, resume_original: !terminal && phase.phase === 'EXECUTE' } };
}
