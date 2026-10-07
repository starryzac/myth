import { object } from '../features/policy-form';
import { parsePaymentAction, parsePaymentReceipt, type PaymentAction } from '../api/full-payment-relations';
import { parseLocalActorSession } from '../api/local-actor';
import { releaseHash } from '../api/goal-release-authorizations';
import type { NextState } from './api';
import type { PendingOperation } from './operation';
import { same } from './policies';

export type PartialPayment = { server_period: string; continuation_available: boolean; original_request: { path: string; body: Record<string, unknown> }; action: PaymentAction };
const exact = (v: Record<string, unknown>, f: string[]) => Object.keys(v).sort().join('|') === f.sort().join('|');
const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
function check(v: unknown): asserts v { if (!v) throw new Error('原部分付款准备未通过核实，请保留同一原键，不能执行或替换。'); }
/** This proof permits only completing the SAME preparation. It cannot create ASK or bank authority. */
export async function parsePartialPayment(v: Record<string, unknown>, original: PendingOperation, environment: NextState): Promise<PartialPayment> {
  const r = original.payment_recovery!;
  check(exact(v, ['simulation', 'variant', 'environment_id', 'epoch_id', 'user_id', 'authorization_id', 'idempotency_key', 'status', 'partial_protocol', 'original_binding', 'original_action', 'original_preparation', 'original_authorization', 'original_request', 'server_period', 'replacement_allowed', 'bank_execute_allowed', 'bank_authority', 'continuation_available', 'continue_original_preparation', 'wrapper_operation']) && v.status === 'PARTIAL_BINDING_NOT_FINAL' && v.partial_protocol === 'zhiyu-next-payment-partial-preparation-v1' && v.user_id === environment.dashboard.user_id && v.authorization_id === r.authorization_id && v.idempotency_key === original.client_request_id && v.original_binding === null && v.replacement_allowed === false && v.bank_execute_allowed === false && v.bank_authority === false && v.wrapper_operation === null && typeof v.continuation_available === 'boolean' && typeof v.server_period === 'string' && /^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(v.server_period));
  const locator = v.original_request; check(object(locator) && exact(locator, ['path', 'body']) && locator.path === `/api/v1${original.path}` && same(locator.body, original.body) && same(v.continue_original_preparation, locator));
  const authorization = await parsePaymentReceipt(v.original_authorization); check(authorization.original.kind === 'CONFIRM' && authorization.original.command_id === r.authorization_id && authorization.original.user_id === v.user_id && authorization.original.epoch_id === environment.epoch_id && same(authorization.original.scope, r.scope));
  const p = v.original_preparation; check(object(p) && exact(p, ['protocol', 'user_id', 'epoch_id', 'authorization_id', 'authorization_evidence_hash', 'request', 'full_payment_action_key', 'principal_at_prepare', 'recorded_at']) && p.protocol === 'full-payment-prepare-original-v1' && p.user_id === v.user_id && p.epoch_id === environment.epoch_id && p.authorization_id === r.authorization_id && p.authorization_evidence_hash === authorization.evidence_hash && object(p.request) && exact(p.request, ['expected_epoch_id', 'period', 'idempotency_key']) && p.request.expected_epoch_id === environment.epoch_id && p.request.period === v.server_period && p.request.idempotency_key === original.client_request_id && p.full_payment_action_key === `action:${await releaseHash({ key: `full-payment:${await releaseHash({ authorization_id: r.authorization_id, key: original.client_request_id })}` })}` && time(p.recorded_at));
  const principal = parseLocalActorSession({ simulation: true, principal: p.principal_at_prepare, bank_authority: false, confirms_financial_action: false }).principal;
  check(principal && principal.user_id === v.user_id && principal.role === 'USER' && Date.parse(principal.issued_at) <= Date.parse(p.recorded_at) && Date.parse(p.recorded_at) < Date.parse(principal.expires_at));
  const action = await parsePaymentAction(v.original_action, r.scope); check(action.effect.liability?.kind === 'occurrence' && action.effect.liability.period === v.server_period && action.effect.business_key === `recurring:${r.scope.original_policy_id}:${String(v.server_period)}` && Date.parse(action.prepared_at) >= Date.parse(p.recorded_at));
  if (v.continuation_available) check(['PLANNED', 'AUTHORIZED'].includes(action.status) && action.bank_status === null && action.receipt === null);
  return { server_period: v.server_period, continuation_available: v.continuation_available, original_request: locator as PartialPayment['original_request'], action };
}
