import { request } from '../api/http';
import { object } from '../features/policy-form';
import { parsePaymentAction, parsePaymentPreview, parsePaymentRead, parsePaymentReceipt, parsePaymentScope, preparePaymentIntent, type PaymentAction, type PaymentIntent, type PaymentPreview, type PaymentReceipt, type PaymentPrepared, type PaymentScopeRequest } from '../api/full-payment-relations';
import { releaseHash, releaseUUID, releaseUUID5, releaseDigest } from '../api/goal-release-authorizations';
import { context, same } from './policies';
import { paymentPath, parsePaymentRecovery, type PaymentRecovery } from './payment-recovery';
import type { PendingOperation } from './operation';
import type { NextState, OperationResult } from './api';
import { parsePartialPayment, type PartialPayment } from './payment-partial';

export type PaymentObservation = { client_request_id: string; evidence_id: string; content_hash: string; observation: Record<string, unknown>; server_period: string };
export type PaymentUpdate = { phase: PaymentIntent['kind'] | 'OBSERVE'; recovery: PaymentRecovery; receipt?: PaymentReceipt; prepared?: PaymentPrepared; partial_preparation?: PartialPayment; action?: PaymentAction; observation?: PaymentObservation; resume_original: boolean };
export type PaymentPair = { full_policy_id: string; full_version_id: string; full_configuration_hash: string; full_name: string; original_policy_id: string; original_version_id: string; original_configuration_hash: string; original_name: string; payee_name: string; source_account_name: string };
export type PaymentDiscovery = { server_period: string; pairs: PaymentPair[]; relations: { receipt: PaymentReceipt; full_name: string; original_name: string; payee_name: string; source_account_name: string }[]; prepared: PaymentPrepared[]; observation_available: boolean };
function valid(condition: unknown): asserts condition { if (!condition) throw new Error('付款原件、范围或实际结果未通过核实，请保留同一操作。'); }
const native = (value: Record<string, unknown>, keys: string[]) => Object.fromEntries(keys.map((key) => [key, value[key]]));
const lookupKeys = ['simulation', 'user_id', 'epoch_id', 'idempotency_key', 'status', 'original', 'replacement_allowed'];
const preparedKeys = ['simulation', 'user_id', 'authorization_id', 'idempotency_key', 'status', 'original_binding', 'original_action', 'replacement_allowed'];
const consentKeys = ['simulation', 'action_id', 'status', 'original', 'original_request', 'replacement_allowed'];
function wrapperRejected(value: Record<string, unknown>, original: PendingOperation, environment: NextState): boolean {
  if (value.wrapper_operation === null || value.wrapper_operation === undefined) return false;
  const operation = value.wrapper_operation;
  valid(object(operation) && operation.status === 'REJECTED' && operation.bank_authority === false && object(operation.original_request) && operation.original_request.path === `/api/v1${original.path}` && same(operation.original_request.body, original.body) && object(operation.error) && typeof operation.error.code === 'string' && typeof operation.error.message === 'string' && Number.isInteger(operation.error.status_code) && object(operation.rejection_proof));
  const proof = operation.rejection_proof;
  valid(Object.keys(proof).sort().join('|') === 'audit_chain_status|checked_at|epoch_id|protocol|requested_native_acceptance_absent|user_id' && proof.protocol === 'zhiyu-next-payment-no-native-acceptance-v1' && proof.user_id === environment.dashboard.user_id && proof.epoch_id === environment.epoch_id && proof.audit_chain_status === 'VALID' && proof.requested_native_acceptance_absent === true && typeof proof.checked_at === 'string' && Number.isFinite(Date.parse(proof.checked_at)) && /(?:Z|[+-]\d\d:\d\d)$/.test(proof.checked_at));
  valid(value.status === 'NOT_FOUND_NOT_FINAL' && value.replacement_allowed === false && (paymentPath(original.path)?.kind === 'PREPARE' ? value.original_binding === null && value.original_action === null : value.original === null));
  return true;
}
export async function paymentIntent(original: PendingOperation, environment: NextState): Promise<PaymentIntent | null> {
  const info = paymentPath(original.path); valid(info && original.environment_id === environment.environment_id && original.epoch_id === environment.epoch_id);
  const recovery = parsePaymentRecovery(original.payment_recovery, original.path, environment.epoch_id);
  valid(recovery.scope.user_id === environment.dashboard.user_id);
  if (info!.kind === 'OBSERVE') { valid(original.body.expected_epoch_id === environment.epoch_id && original.body.original_policy_id === recovery.scope.original_policy_id && original.body.client_request_id === original.client_request_id && Object.keys(original.body).sort().join('|') === 'client_request_id|expected_epoch_id|original_policy_id'); return null; }
  if (info!.kind === 'PREPARE') { valid(original.body.expected_epoch_id === environment.epoch_id && original.body.idempotency_key === original.client_request_id && Object.keys(original.body).sort().join('|') === 'expected_epoch_id|idempotency_key'); return null; }
  return preparePaymentIntent(info!.kind, recovery.scope, original.body as PaymentIntent['body'], { ...(recovery.start_command_id ? { start_command_id: recovery.start_command_id } : {}), ...(recovery.authorization_id ? { authorization_id: recovery.authorization_id } : {}), ...(recovery.action ? { action: recovery.action } : {}) });
}
export async function previewNextPayment(body: PaymentScopeRequest, environment: NextState): Promise<PaymentPreview> {
  valid(body.expected_epoch_id === environment.epoch_id);
  return request('/zhiyu-next/payments/relation/preview', 'POST', body, async (value) => { context(value, environment); valid(object(value.preview)); return parsePaymentPreview(value.preview, environment.dashboard.user_id, body); });
}
export async function readNextPaymentAction(recovery: PaymentRecovery, environment: NextState): Promise<PaymentAction> {
  valid(recovery.action && recovery.scope.user_id === environment.dashboard.user_id && recovery.scope.epoch_id === environment.epoch_id);
  const action = await request(`/zhiyu-next/payments/actions/${recovery.action!.action_id}`, 'GET', undefined, async (value) => { context(value, environment); return parsePaymentAction(value.action, recovery.scope, recovery.action); });
  valid(action.effect.liability?.kind === 'occurrence' && action.effect.business_key === `recurring:${recovery.scope.original_policy_id}:${action.effect.liability.period}`);
  valid(!action.receipt || ['SUCCEEDED', 'RECONCILED'].includes(action.status) && action.bank_status === 'SETTLED');
  return action;
}
export async function readPaymentOriginal(original: PendingOperation, environment: NextState): Promise<OperationResult> {
  const intent = await paymentIntent(original, environment); const recovery = original.payment_recovery!;
  const base = { simulation: true as const, environment_id: environment.environment_id, epoch_id: environment.epoch_id, client_request_id: original.client_request_id };
  const result = (status: OperationResult['status'], update?: PaymentUpdate): OperationResult => ({ ...base, status, ...(update ? { result: update as unknown as Record<string, unknown> } : {}) });
  if (paymentPath(original.path)?.kind === 'OBSERVE') {
    const read = await request(`/zhiyu-next/payments/observations/${original.client_request_id}`, 'GET', undefined, (value) => { context(value, environment); return value; });
    valid(['COMPLETED', 'REJECTED', 'PENDING', 'NOT_FOUND_NOT_FINAL'].includes(String(read.status)) && (read.client_request_id === undefined || read.client_request_id === original.client_request_id));
    if (['PENDING', 'NOT_FOUND_NOT_FINAL'].includes(String(read.status))) return result('PENDING');
    valid(object(read.original_request) && read.original_request.path === `/api/v1${original.path}` && same(read.original_request.body, original.body));
    if (read.status === 'REJECTED') { valid(object(read.result) && Object.keys(read.result).sort().join('|') === 'code|message|status_code' && typeof read.result.code === 'string' && typeof read.result.message === 'string' && Number.isInteger(read.result.status_code)); return result('REJECTED'); }
    valid(object(read.result) && read.result.bank_authority === false && read.result.transfers_funds === false && typeof read.result.server_period === 'string' && /^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(read.result.server_period) && object(read.result.original_observation));
    const proof = read.result.original_observation; valid(releaseUUID(proof.evidence_id) && releaseDigest(proof.content_hash) && object(proof.observation));
    const observation = proof.observation;
    valid(observation.simulation === true && observation.protocol === 'recurring-settlement-v1' && observation.observation_protocol === 'scenario-empty-bank-payment-v1' && observation.user_id === environment.dashboard.user_id && observation.policy_id === recovery.scope.original_policy_id && observation.period === read.result.server_period && observation.payee_id === recovery.scope.payee_id && observation.paid_cents === 0 && observation.complete === true && typeof observation.as_of === 'string' && Number.isFinite(Date.parse(observation.as_of)) && /(?:Z|[+-]\d\d:\d\d)$/.test(observation.as_of) && await releaseHash(observation) === proof.content_hash && await releaseUUID5(recovery.scope.original_policy_id, `scenario-empty-bank-payment-v1:${String(observation.period)}`) === proof.evidence_id);
    return result('COMPLETED', { phase: 'OBSERVE', recovery, observation: { client_request_id: original.client_request_id, evidence_id: String(proof.evidence_id), content_hash: String(proof.content_hash), observation, server_period: String(read.result.server_period) }, resume_original: false });
  }
  if (intent?.kind === 'START' || intent?.kind === 'CONFIRM') {
    const key = String(original.body.idempotency_key);
    let rejected = false;
    const read = await request(`/zhiyu-next/payments/commands/${environment.epoch_id}/by-key/${encodeURIComponent(key)}`, 'GET', undefined, async (value) => { context(value, environment); rejected = wrapperRejected(value, original, environment); return parsePaymentRead(native(value, lookupKeys), intent); });
    if (rejected) return result('REJECTED');
    if (read.status !== 'RECORDED') return result('PENDING');
    valid('original' in read && read.original);
    const receipt = await parsePaymentReceipt((read as { original: unknown }).original, intent);
    return result('COMPLETED', { phase: intent.kind, recovery, receipt, resume_original: false });
  }
  if (paymentPath(original.path)?.kind === 'PREPARE') {
    const key = String(original.body.idempotency_key);
    let rejected = false;
    const prepared = await request(`/zhiyu-next/payments/authorizations/${recovery.authorization_id}/prepared/by-key/${encodeURIComponent(key)}`, 'GET', undefined, async (value) => {
      context(value, environment); const raw = native(value, preparedKeys);
      if (value.status === 'PARTIAL_BINDING_NOT_FINAL') return parsePartialPayment(value, original, environment);
      rejected = wrapperRejected(value, original, environment);
      valid(raw.user_id === environment.dashboard.user_id && raw.authorization_id === recovery.authorization_id && raw.idempotency_key === key && raw.replacement_allowed === false && ['RECORDED', 'NOT_FOUND_NOT_FINAL'].includes(String(raw.status)));
      if (raw.status === 'NOT_FOUND_NOT_FINAL') { valid(raw.original_binding === null && raw.original_action === null); return raw as PaymentPrepared; }
      valid(object(raw.original_binding) && object(raw.original_binding.original_prepare_request));
      const body = raw.original_binding.original_prepare_request;
      valid(body.expected_epoch_id === original.body.expected_epoch_id && body.idempotency_key === key);
      const actual = await preparePaymentIntent('PREPARE', recovery.scope, body as PaymentIntent['body'], { authorization_id: recovery.authorization_id! });
      return await parsePaymentRead(raw, actual) as PaymentPrepared;
    });
    if ('continuation_available' in prepared) return result('PENDING', { phase: 'PREPARE', recovery, partial_preparation: prepared, action: prepared.action, resume_original: prepared.continuation_available });
    if (rejected) return result('REJECTED');
    if (prepared.status !== 'RECORDED') return result('PENDING');
    valid(prepared.original_action && prepared.original_binding && prepared.original_binding.scope_hash === await releaseHash(recovery.scope));
    const action = prepared.original_action!;
    valid(action.effect.liability?.kind === 'occurrence' && action.effect.business_key === `recurring:${recovery.scope.original_policy_id}:${action.effect.liability.period}`);
    return result('COMPLETED', { phase: 'PREPARE', recovery: { ...recovery, action }, prepared, action, resume_original: false });
  }
  valid(intent);
  let consentRecorded = true;
  if (intent!.kind === 'ACTION_CONFIRM') {
    const read = await request(`/zhiyu-next/payments/actions/${recovery.action!.action_id}/user-consent`, 'GET', undefined, async (value) => { context(value, environment); return parsePaymentRead(native(value, consentKeys), intent!); });
    consentRecorded = read.status === 'RECORDED';
  }
  const action = await readNextPaymentAction(recovery, environment);
  const update: PaymentUpdate = { phase: intent!.kind, recovery: { ...recovery, action }, action, resume_original: action.bank_status === 'SETTLED' && !action.receipt || ['PLANNED', 'AUTHORIZED'].includes(action.status) && action.bank_status === null && (intent!.kind === 'ACTION_CONFIRM' || action.autonomy_level === 'AUTO_EXECUTE') };
  if (!consentRecorded && !(['REJECTED', 'FAILED', 'INVALIDATED'].includes(action.status) && !['UNKNOWN', 'SUBMITTED', 'SETTLED'].includes(action.bank_status ?? ''))) return result('PENDING');
  if (['SUCCEEDED', 'RECONCILED'].includes(action.status)) { valid(action.receipt && action.bank_status === 'SETTLED'); return result('COMPLETED', update); }
  if (['REJECTED', 'FAILED', 'INVALIDATED'].includes(action.status) && !['UNKNOWN', 'SUBMITTED', 'SETTLED'].includes(action.bank_status ?? '')) return result('REJECTED', update);
  return result('PENDING', update);
}
export function samePaymentUpdate(update: PaymentUpdate | null, original: PendingOperation): boolean { return !!update && update.phase === paymentPath(original.path)?.kind && !!original.payment_recovery && same(update.recovery.scope, original.payment_recovery.scope) && (update.partial_preparation ? update.phase === 'PREPARE' && update.partial_preparation.original_request.path === `/api/v1${original.path}` && same(update.partial_preparation.original_request.body, original.body) : update.action?.action_id === original.payment_recovery.action?.action_id && update.action?.effect_hash === original.payment_recovery.action?.effect_hash); }
export async function parsePaymentDiscovery(value: unknown, environment: NextState): Promise<PaymentDiscovery> {
  context(value, environment); valid(value.bank_authority === false && typeof value.server_period === 'string' && /^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(value.server_period) && typeof value.observation_available === 'boolean' && Array.isArray(value.pairs) && Array.isArray(value.relations) && Array.isArray(value.prepared));
  const pairs = value.pairs as unknown[];
  valid(pairs.every((row) => object(row) && ['full_policy_id', 'full_version_id', 'original_policy_id', 'original_version_id'].every((key) => releaseUUID(row[key])) && ['full_configuration_hash', 'original_configuration_hash'].every((key) => releaseDigest(row[key])) && ['full_name', 'original_name', 'payee_name', 'source_account_name'].every((key) => typeof row[key] === 'string' && !!row[key])) && new Set(pairs.map((row) => object(row) ? `${String(row.full_policy_id)}:${String(row.original_policy_id)}` : null)).size === pairs.length);
  const relations: PaymentDiscovery['relations'] = [];
  for (const item of value.relations as unknown[]) {
    valid(object(item) && ['full_name', 'original_name', 'payee_name', 'source_account_name'].every((key) => typeof item[key] === 'string' && !!item[key]));
    const receipt = await parsePaymentReceipt((item as Record<string, unknown>).receipt);
    valid(receipt.original.user_id === environment.dashboard.user_id && receipt.original.epoch_id === environment.epoch_id);
    relations.push({ ...(item as PaymentDiscovery['relations'][number]), receipt });
  }
  valid(new Set(relations.map((row) => row.receipt.original.command_id)).size === relations.length);
  const prepared: PaymentPrepared[] = [];
  for (const item of value.prepared as unknown[]) {
    valid(object(item) && item.status === 'RECORDED' && object(item.original_binding)); const binding = (item as Record<string, unknown>).original_binding as Record<string, unknown>;
    const scope = parsePaymentScope(binding.scope); const receipt = relations.find((row) => row.receipt.original.kind === 'CONFIRM' && row.receipt.original.command_id === binding.authorization_id)?.receipt;
    valid(receipt && same(scope, receipt.original.scope) && binding.authorization_evidence_id === receipt.evidence_id && binding.authorization_evidence_hash === receipt.evidence_hash && scope.user_id === environment.dashboard.user_id && scope.epoch_id === environment.epoch_id);
    const original = await preparePaymentIntent('PREPARE', scope, binding.original_prepare_request as PaymentIntent['body'], { authorization_id: String(binding.authorization_id) });
    const lookup = await parsePaymentRead(item, original) as PaymentPrepared;
    valid(lookup.original_action?.effect.liability?.kind === 'occurrence' && lookup.original_action.effect.business_key === `recurring:${scope.original_policy_id}:${lookup.original_action.effect.liability.period}`);
    prepared.push(lookup);
  }
  valid(new Set(prepared.map((row) => row.original_action!.action_id)).size === prepared.length);
  return { server_period: String(value.server_period), pairs: pairs as PaymentPair[], relations, prepared, observation_available: value.observation_available as boolean };
}
export async function getPaymentDiscovery(environment: NextState): Promise<PaymentDiscovery> { return request('/zhiyu-next/payments/state', 'GET', undefined, (value) => parsePaymentDiscovery(value, environment)); }
