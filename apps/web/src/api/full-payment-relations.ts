import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { releaseHash, releaseUUID, releaseUUID5, releaseDigest, releaseCanonicalJson } from './goal-release-authorizations';

export type PaymentScope = components['schemas']['PaymentRelationScope'];
export type PaymentPreview = components['schemas']['PaymentScopePreview'];
export type PaymentReceipt = components['schemas']['PaymentCommandReceipt'];
export type PaymentLookup = components['schemas']['PaymentCommandLookup'];
export type PaymentPrepared = components['schemas']['PaymentPreparedLookup'];
export type PaymentConsent = components['schemas']['PaymentConsentLookup'];
export type PaymentAction = components['schemas']['ActionResponse'];
export type PaymentScopeRequest = components['schemas']['PaymentScopeRequest'];
type Start = components['schemas']['PaymentStartRequest'];
type Confirm = components['schemas']['PaymentConfirmRequest'];
type Prepare = components['schemas']['PaymentPrepareRequest'];
type Consent = components['schemas']['PaymentActionConfirmation'];
type Execute = components['schemas']['PaymentExecuteRequest'];
export type PaymentIntent = {
  protocol: 'fixed-payment-browser-command-v1'; user_id: string; full_policy_id: string; epoch_id: string;
  kind: 'START' | 'CONFIRM' | 'PREPARE' | 'ACTION_CONFIRM' | 'EXECUTE'; path: string;
  body: Start | Confirm | Prepare | Consent | Execute; body_json: string; client_request_hash: string;
  scope: PaymentScope; start_command_id: string | null; authorization_id: string | null; action: PaymentAction | null;
};
export type PaymentRead = PaymentLookup | PaymentPrepared | PaymentConsent | PaymentAction;
const originals = new WeakMap<object, string>();
const reads = new WeakSet<object>();
export const paymentOriginalText = (value: object) => originals.get(value) ?? null;
export const isFreshPaymentRead = (value: object) => reads.has(value);
const same = (a: unknown, b: unknown) => releaseCanonicalJson(a) === releaseCanonicalJson(b);
function check(value: unknown): asserts value { if (!value) throw new Error('固定关系原身份、完整请求、范围或回执未匹配；原请求继续保留'); }
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const cents = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const key = (value: unknown): value is string => typeof value === 'string' && value.length <= 140 && value.trim().length > 0;
function save<T extends object>(value: T, raw?: string): T { if (raw !== undefined) originals.set(value, raw); return value; }
const scopeKeys = ['user_id', 'epoch_id', 'full_policy_id', 'full_version_id', 'full_configuration_hash', 'original_policy_id', 'original_version_id', 'original_configuration_hash', 'payee_id', 'payee_evidence_id', 'payee_evidence_hash', 'source_account_id', 'source_account_identity_hash', 'amount_rule', 'due_day', 'single_action_cap_cents', 'auto_execute', 'timezone', 'valid_from', 'valid_until', 'full_planning_bank_authority', 'creates_original_mvp_permission'];
export function parsePaymentScope(value: unknown): PaymentScope {
  assertMoneyFields(value);
  check(object(value) && exact(value, scopeKeys) && ['user_id', 'epoch_id', 'full_policy_id', 'full_version_id', 'original_policy_id', 'original_version_id', 'payee_evidence_id', 'source_account_id'].every((k) => releaseUUID(value[k])) && ['full_configuration_hash', 'original_configuration_hash', 'payee_evidence_hash', 'source_account_identity_hash'].every((k) => releaseDigest(value[k])));
  check(typeof value.payee_id === 'string' && value.payee_id.trim().length > 0 && value.payee_id.length <= 160 && object(value.amount_rule));
  const rule = value.amount_rule;
  check(rule.kind === 'exact' ? exact(rule, ['kind', 'amount_cents']) && cents(rule.amount_cents) : rule.kind === 'range' && exact(rule, ['kind', 'min_cents', 'max_cents']) && cents(rule.min_cents) && cents(rule.max_cents) && rule.min_cents <= rule.max_cents);
  const maximum = rule.kind === 'exact' ? rule.amount_cents : rule.max_cents;
  check(cents(value.single_action_cap_cents) && value.single_action_cap_cents > 0 && Number(maximum) <= value.single_action_cap_cents && Number.isInteger(value.due_day) && Number(value.due_day) >= 1 && Number(value.due_day) <= 31 && typeof value.auto_execute === 'boolean' && ['UTC', 'Asia/Shanghai'].includes(String(value.timezone)) && time(value.valid_from) && time(value.valid_until) && Date.parse(value.valid_from) < Date.parse(value.valid_until) && value.full_planning_bank_authority === false && value.creates_original_mvp_permission === false);
  return value as PaymentScope;
}
export function scopeRequest(scope: PaymentScope): PaymentScopeRequest { return { expected_epoch_id: scope.epoch_id, full_policy_id: scope.full_policy_id, expected_full_version_id: scope.full_version_id, original_policy_id: scope.original_policy_id, expected_original_version_id: scope.original_version_id }; }
function validateScopeRequest(value: unknown): PaymentScopeRequest { check(object(value) && exact(value, ['expected_epoch_id', 'full_policy_id', 'expected_full_version_id', 'original_policy_id', 'expected_original_version_id']) && Object.values(value).every(releaseUUID)); return value as PaymentScopeRequest; }
export async function parsePaymentPreview(value: unknown, userId: string, body: PaymentScopeRequest, raw?: string): Promise<PaymentPreview> {
  validateScopeRequest(body); check(object(value) && exact(value, ['simulation', 'scope', 'scope_hash', 'preview_only', 'grants_authority', 'original_mvp_permission_reused']) && value.simulation === true && value.preview_only === true && value.grants_authority === false && value.original_mvp_permission_reused === true);
  const scope = parsePaymentScope(value.scope); check(scope.user_id === userId && same(scopeRequest(scope), body) && value.scope_hash === await releaseHash(scope)); return save(value as PaymentPreview, raw);
}
export const previewPaymentRelation = (userId: string, body: PaymentScopeRequest) => request('/full-payment-relations/preview', 'POST', validateScopeRequest(body), (v, raw) => parsePaymentPreview(v, userId, body, raw));
function principal(value: unknown, userId: string, at: string): void {
  check(object(value) && exact(value, ['user_id', 'role', 'session_id', 'issued_at', 'expires_at', 'authentication_source', 'authenticated', 'human_identity_verified']) && value.user_id === userId && value.role === 'USER' && releaseUUID(value.session_id) && value.authentication_source === 'LOCAL_SIGNED_SESSION' && value.authenticated === true && value.human_identity_verified === false && time(value.issued_at) && time(value.expires_at) && Date.parse(value.issued_at) <= Date.parse(at) && Date.parse(at) < Date.parse(value.expires_at) && Date.parse(value.expires_at) - Date.parse(value.issued_at) <= 900000);
}
function bodyFor(kind: PaymentIntent['kind'], value: unknown): PaymentIntent['body'] {
  check(object(value) && releaseUUID(value.expected_epoch_id));
  if (kind === 'START') { check(exact(value, ['expected_epoch_id', 'full_policy_id', 'expected_full_version_id', 'original_policy_id', 'expected_original_version_id', 'idempotency_key']) && key(value.idempotency_key)); validateScopeRequest(Object.fromEntries(Object.entries(value).filter(([k]) => k !== 'idempotency_key'))); }
  if (kind === 'CONFIRM') check(exact(value, ['expected_epoch_id', 'reviewed_scope_hash', 'accepted', 'reason', 'idempotency_key']) && value.accepted === true && releaseDigest(value.reviewed_scope_hash) && typeof value.reason === 'string' && value.reason.trim().length > 0 && value.reason.length <= 1000 && key(value.idempotency_key));
  if (kind === 'PREPARE') check(exact(value, ['expected_epoch_id', 'period', 'idempotency_key']) && key(value.idempotency_key) && typeof value.period === 'string' && /^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(value.period));
  if (kind === 'ACTION_CONFIRM') check(exact(value, ['expected_epoch_id', 'reviewed_effect_hash', 'accepted']) && value.accepted === true && releaseDigest(value.reviewed_effect_hash));
  if (kind === 'EXECUTE') check(exact(value, ['expected_epoch_id']));
  return value as PaymentIntent['body'];
}
export async function parsePaymentReceipt(value: unknown, intent?: PaymentIntent, raw?: string): Promise<PaymentReceipt> {
  check(object(value) && exact(value, ['simulation', 'original', 'evidence_id', 'evidence_hash', 'trace_hash', 'idempotent_replay', 'current_scope_status', 'receipt_is_current_authority', 'economic_effect_verified', 'transfers_funds']) && value.simulation === true && ['CURRENT', 'STALE', 'UNKNOWN'].includes(String(value.current_scope_status)) && typeof value.idempotent_replay === 'boolean' && value.receipt_is_current_authority === false && value.economic_effect_verified === false && value.transfers_funds === false && releaseDigest(value.trace_hash));
  const original = value.original;
  check(object(original) && exact(original, ['protocol', 'command_id', 'user_id', 'epoch_id', 'kind', 'idempotency_key', 'start_command_id', 'original_request', 'request_hash', 'principal_at_command', 'scope', 'scope_hash', 'recorded_at', 'creates_original_mvp_permission', 'transfers_funds']) && original.protocol === 'full-payment-relation-v1' && ['START', 'CONFIRM'].includes(String(original.kind)) && releaseUUID(original.user_id) && releaseUUID(original.epoch_id) && key(original.idempotency_key) && time(original.recorded_at) && original.creates_original_mvp_permission === false && original.transfers_funds === false);
  const scope = parsePaymentScope(original.scope); check(scope.user_id === original.user_id && scope.epoch_id === original.epoch_id && original.scope_hash === await releaseHash(scope) && Date.parse(scope.valid_from) <= Date.parse(original.recorded_at) && Date.parse(original.recorded_at) < Date.parse(scope.valid_until)); principal(original.principal_at_command, scope.user_id, original.recorded_at);
  let body: PaymentIntent['body'];
  if (original.kind === 'START') { check(original.start_command_id === null); body = bodyFor('START', original.original_request); const copy = { ...body } as Record<string, unknown>; delete copy.idempotency_key; check(same(copy, scopeRequest(scope))); }
  else { check(releaseUUID(original.start_command_id) && object(original.original_request) && exact(original.original_request, ['start_command_id', 'confirmation']) && original.original_request.start_command_id === original.start_command_id); body = bodyFor('CONFIRM', original.original_request.confirmation); check((body as Confirm).reviewed_scope_hash === original.scope_hash); }
  check(body.expected_epoch_id === original.epoch_id && 'idempotency_key' in body && body.idempotency_key === original.idempotency_key && original.command_id === await releaseUUID5('d4d1c4a1-bc4b-5a12-b073-aafdfc133e8f', `${original.user_id}:${original.epoch_id}:${original.idempotency_key}`) && original.request_hash === await releaseHash({ user_id: original.user_id, kind: original.kind, request: original.original_request }) && value.evidence_id === await releaseUUID5(original.command_id as string, 'evidence') && value.evidence_hash === await releaseHash(original));
  if (intent) { check(['START', 'CONFIRM'].includes(intent.kind) && original.kind === intent.kind && original.user_id === intent.user_id && same(scope, intent.scope) && same(body, intent.body) && original.start_command_id === intent.start_command_id); }
  return save(value as PaymentReceipt, raw);
}
export async function parsePaymentAction(value: unknown, scope: PaymentScope, previous?: PaymentAction | null, raw?: string): Promise<PaymentAction> {
  assertMoneyFields(value); check(object(value) && exact(value, ['simulation', 'user_id', 'action_id', 'decision_run_id', 'status', 'autonomy_level', 'effect', 'effect_hash', 'prepared_at', 'as_of', 'prepared_validation', 'bank_status', 'receipt']) && value.simulation === true && value.user_id === scope.user_id && releaseUUID(value.action_id) && releaseUUID(value.decision_run_id) && time(value.prepared_at) && time(value.as_of) && ['PLANNED', 'AUTHORIZED', 'SUBMITTED', 'UNKNOWN', 'SUCCEEDED', 'RECONCILED', 'REJECTED', 'FAILED', 'INVALIDATED'].includes(String(value.status)) && ['AUTO_EXECUTE', 'ASK_ONCE', 'ADVISE_ONLY', 'BLOCKED'].includes(String(value.autonomy_level)) && object(value.effect) && object(value.prepared_validation));
  const e = value.effect;
  check(e.simulation === true && e.operation_id === value.action_id && e.user_id === scope.user_id && e.action_type === 'PAY_RECURRING' && e.policy_id === scope.original_policy_id && e.policy_version_id === scope.original_version_id && same(e.policy_version_ids, [scope.original_version_id]) && e.payee_id === scope.payee_id && releaseUUID(e.payee_evidence_id) && cents(e.amount_cents) && e.amount_cents > 0 && e.amount_cents <= scope.single_action_cap_cents && e.fee_cents === 0 && e.loss_cents === 0 && e.net_cents === null && time(e.valid_from) && time(e.expires_at) && Date.parse(e.valid_from) < Date.parse(e.expires_at) && Array.isArray(e.cash_uses) && e.cash_uses.length === 1 && object(e.cash_uses[0]) && e.cash_uses[0].account_id === scope.source_account_id && e.cash_uses[0].amount_cents === e.amount_cents && Array.isArray(e.income_uses) && e.income_uses.length === 0 && object(e.liability) && e.liability.kind === 'occurrence' && e.liability.policy_id === scope.original_policy_id && /^\d{4}-(0[1-9]|1[0-2])$/.test(String(e.liability.period)) && Array.isArray(e.liability.evidence_ids) && e.liability.evidence_ids.length > 0 && e.liability.evidence_ids.every(releaseUUID));
  check(['position_id', 'position_account_id', 'product_id', 'product_version_number', 'terms_digest', 'quote_id', 'original_policy_version_id', 'goal_id', 'destination_account_id', 'return_account_id', 'purchase_exit'].every((k) => e[k] === null));
  const rule = scope.amount_rule; check(rule.kind === 'exact' ? e.amount_cents <= rule.amount_cents : e.amount_cents <= rule.max_cents);
  const ordered = structuredClone(e); ordered.policy_version_ids = [...e.policy_version_ids as string[]].sort(); ordered.cash_uses = [...e.cash_uses].sort((a, b) => String(a.account_id).localeCompare(String(b.account_id))); ordered.income_uses = []; ordered.liability = { ...e.liability, evidence_ids: [...e.liability.evidence_ids].sort() };
  check(value.effect_hash === await releaseHash(ordered) && value.prepared_validation.effect_hash === value.effect_hash && value.prepared_validation.simulation === true && value.prepared_validation.financial_only === true);
  if (previous) check(value.action_id === previous.action_id && value.effect_hash === previous.effect_hash && same(e, previous.effect) && value.prepared_at === previous.prepared_at && value.decision_run_id === previous.decision_run_id);
  if (value.receipt !== null) {
    const r = value.receipt; check(object(r) && exact(r, ['simulation', 'receipt_id', 'action_id', 'bank_operation_id', 'status', 'executed_cents', 'fee_cents', 'loss_cents', 'posting_ids', 'occurred_at', 'reconciled_at']) && r.simulation === true && r.action_id === value.action_id && r.bank_operation_id === value.action_id && releaseUUID(r.receipt_id) && r.status === 'SUCCEEDED' && r.executed_cents === e.amount_cents && r.fee_cents === 0 && r.loss_cents === 0 && Array.isArray(r.posting_ids) && r.posting_ids.length === 3 && r.posting_ids.every(releaseUUID) && new Set(r.posting_ids).size === 3 && time(r.occurred_at) && (r.reconciled_at === null || time(r.reconciled_at)));
  }
  if (['SUCCEEDED', 'RECONCILED'].includes(String(value.status))) check(value.receipt !== null && value.bank_status === 'SETTLED');
  return save(value as PaymentAction, raw);
}
const pathFor = (i: Pick<PaymentIntent, 'kind' | 'start_command_id' | 'authorization_id' | 'action'>) => i.kind === 'START' ? '/full-payment-relations/start' : i.kind === 'CONFIRM' ? `/full-payment-relations/starts/${i.start_command_id}/confirm` : i.kind === 'PREPARE' ? `/full-payment-relations/authorizations/${i.authorization_id}/prepare` : `/full-payment-relations/actions/${i.action?.action_id}/${i.kind === 'ACTION_CONFIRM' ? 'confirm' : 'execute'}`;
export async function parsePaymentIntent(value: unknown): Promise<PaymentIntent> {
  check(object(value) && exact(value, ['protocol', 'user_id', 'full_policy_id', 'epoch_id', 'kind', 'path', 'body', 'body_json', 'client_request_hash', 'scope', 'start_command_id', 'authorization_id', 'action']) && value.protocol === 'fixed-payment-browser-command-v1' && ['START', 'CONFIRM', 'PREPARE', 'ACTION_CONFIRM', 'EXECUTE'].includes(String(value.kind)));
  const kind = value.kind as PaymentIntent['kind'], scope = parsePaymentScope(value.scope), body = bodyFor(kind, value.body);
  check(value.user_id === scope.user_id && value.full_policy_id === scope.full_policy_id && value.epoch_id === scope.epoch_id && body.expected_epoch_id === scope.epoch_id && value.body_json === JSON.stringify(body));
  if (kind === 'START') { check(value.start_command_id === null && value.authorization_id === null && value.action === null); const copy = { ...body } as Record<string, unknown>; delete copy.idempotency_key; check(same(copy, scopeRequest(scope))); }
  else if (kind === 'CONFIRM') check(releaseUUID(value.start_command_id) && value.authorization_id === null && value.action === null && (body as Confirm).reviewed_scope_hash === await releaseHash(scope));
  else { check(releaseUUID(value.authorization_id) && value.start_command_id === null); if (kind === 'PREPARE') check(value.action === null); else { const action = await parsePaymentAction(value.action, scope); check(kind !== 'ACTION_CONFIRM' || (body as Consent).reviewed_effect_hash === action.effect_hash); } }
  const intent = value as PaymentIntent; check(intent.path === pathFor(intent) && intent.client_request_hash === await releaseHash({ kind, user_id: scope.user_id, path: intent.path, body })); return intent;
}
export async function preparePaymentIntent(kind: PaymentIntent['kind'], scope: PaymentScope, body: PaymentIntent['body'], identity: { start_command_id?: string; authorization_id?: string; action?: PaymentAction } = {}): Promise<PaymentIntent> {
  const input = { protocol: 'fixed-payment-browser-command-v1' as const, user_id: scope.user_id, full_policy_id: scope.full_policy_id, epoch_id: scope.epoch_id, kind, path: '', body: structuredClone(body), body_json: JSON.stringify(body), client_request_hash: '', scope: structuredClone(scope), start_command_id: identity.start_command_id ?? null, authorization_id: identity.authorization_id ?? null, action: identity.action ? structuredClone(identity.action) : null };
  input.path = pathFor(input); input.client_request_hash = await releaseHash({ kind, user_id: scope.user_id, path: input.path, body }); return parsePaymentIntent(input);
}
export async function postPaymentIntent(intent: PaymentIntent): Promise<PaymentReceipt | PaymentAction> { await parsePaymentIntent(intent); return request(intent.path, 'POST', intent.body, (v, raw) => ['START', 'CONFIRM'].includes(intent.kind) ? parsePaymentReceipt(v, intent, raw) : parsePaymentAction(v, intent.scope, intent.action, raw)); }
export async function parsePaymentRead(value: unknown, intent: PaymentIntent, raw?: string): Promise<PaymentRead> {
  await parsePaymentIntent(intent); check(object(value) && value.simulation === true);
  if (intent.kind === 'EXECUTE') return parsePaymentAction(value, intent.scope, intent.action, raw);
  check(value.replacement_allowed === false && ['RECORDED', 'NOT_FOUND_NOT_FINAL'].includes(String(value.status)));
  if (intent.kind === 'START' || intent.kind === 'CONFIRM') {
    check(exact(value, ['simulation', 'user_id', 'epoch_id', 'idempotency_key', 'status', 'original', 'replacement_allowed']) && value.user_id === intent.user_id && value.epoch_id === intent.epoch_id && value.idempotency_key === (intent.body as Start | Confirm).idempotency_key);
    if (value.status === 'NOT_FOUND_NOT_FINAL') check(value.original === null); else await parsePaymentReceipt(value.original, intent);
    return save(value as PaymentLookup, raw);
  }
  if (intent.kind === 'PREPARE') {
    check(exact(value, ['simulation', 'user_id', 'authorization_id', 'idempotency_key', 'status', 'original_binding', 'original_action', 'replacement_allowed']) && value.user_id === intent.user_id && value.authorization_id === intent.authorization_id && value.idempotency_key === (intent.body as Prepare).idempotency_key);
    if (value.status === 'NOT_FOUND_NOT_FINAL') check(value.original_binding === null && value.original_action === null);
    else { const b = value.original_binding; check(object(b) && exact(b, ['protocol', 'user_id', 'epoch_id', 'action_id', 'authorization_id', 'authorization_evidence_id', 'authorization_evidence_hash', 'scope', 'scope_hash', 'period', 'original_prepare_request', 'original_effect_hash', 'recorded_at']) && b.protocol === 'full-payment-action-binding-v1' && b.user_id === intent.user_id && b.epoch_id === intent.epoch_id && b.authorization_id === intent.authorization_id && releaseDigest(b.authorization_evidence_hash) && b.authorization_evidence_id === await releaseUUID5(String(intent.authorization_id), 'evidence') && same(b.scope, intent.scope) && b.scope_hash === await releaseHash(intent.scope) && same(b.original_prepare_request, intent.body) && b.period === (intent.body as Prepare).period && time(b.recorded_at) && Date.parse(intent.scope.valid_from) <= Date.parse(b.recorded_at) && Date.parse(b.recorded_at) < Date.parse(intent.scope.valid_until)); const a = await parsePaymentAction(value.original_action, intent.scope); check(a.action_id === b.action_id && a.effect_hash === b.original_effect_hash && a.effect.liability?.kind === 'occurrence' && a.effect.liability.period === b.period); }
    return save(value as PaymentPrepared, raw);
  }
  const action = intent.action; check(action !== null);
  check(exact(value, ['simulation', 'action_id', 'status', 'original', 'original_request', 'replacement_allowed']) && value.action_id === action.action_id);
  if (value.status === 'NOT_FOUND_NOT_FINAL') check(value.original === null && value.original_request === null);
  else { const o = value.original; check(object(o) && exact(o, ['protocol', 'user_id', 'epoch_id', 'action_id', 'original_effect_hash', 'original_confirmation_evidence_id', 'principal_at_confirmation', 'confirmed_at', 'accepted']) && o.protocol === 'full-payment-user-action-consent-v1' && o.user_id === intent.user_id && o.epoch_id === intent.epoch_id && o.action_id === action.action_id && o.original_effect_hash === action.effect_hash && o.accepted === true && time(o.confirmed_at) && Date.parse(action.effect.valid_from) <= Date.parse(o.confirmed_at) && Date.parse(o.confirmed_at) < Date.parse(action.effect.expires_at) && same(value.original_request, intent.body) && o.original_confirmation_evidence_id === await releaseUUID5(o.action_id as string, `confirmation:${o.original_effect_hash}`)); principal(o.principal_at_confirmation, intent.user_id, o.confirmed_at); }
  return save(value as PaymentConsent, raw);
}
export async function lookupPaymentIntent(intent: PaymentIntent): Promise<PaymentRead> {
  await parsePaymentIntent(intent); const body = intent.body as Start | Confirm | Prepare;
  const path = intent.kind === 'START' || intent.kind === 'CONFIRM' ? `/full-payment-relations/commands/${intent.epoch_id}/by-key/${encodeURIComponent(body.idempotency_key)}` : intent.kind === 'PREPARE' ? `/full-payment-relations/authorizations/${intent.authorization_id}/prepared/by-key/${encodeURIComponent(body.idempotency_key)}` : `/full-payment-relations/actions/${intent.action?.action_id}${intent.kind === 'ACTION_CONFIRM' ? '/user-consent' : ''}`;
  const result = await request(path, 'GET', undefined, (v, raw) => parsePaymentRead(v, intent, raw)); reads.add(result); return result;
}
export async function readPaymentAction(action: PaymentAction, scope: PaymentScope): Promise<PaymentAction> { const result = await request(`/full-payment-relations/actions/${action.action_id}`, 'GET', undefined, (v, raw) => parsePaymentAction(v, scope, action, raw)); reads.add(result); return result; }
