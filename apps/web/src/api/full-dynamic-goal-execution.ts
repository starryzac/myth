import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { spendingCanonicalJson as canonical, spendingHash as hash, spendingUUID as uuid, spendingDigest as digest } from './spending-evidence';

// The six identity fields mirror the actual server DTO; no money or authority input.
export type DynamicPrepare = components['schemas']['FullDynamicGoalPrepareRequest'];
export type DynamicProof = components['schemas']['FullDynamicGoalProof'];
export type DynamicPreview = components['schemas']['FullDynamicGoalPreview'];
export type DynamicAction = components['schemas']['ActionResponse'];
export type DynamicLookup = Required<components['schemas']['FullDynamicGoalLookup']>;
export type DynamicIntent = { protocol: 'full-dynamic-goal-browser-v1'; kind: 'PREPARE' | 'CONFIRM' | 'EXECUTE'; user_id: string; prepare_request: DynamicPrepare; action: DynamicAction | null; path: string; body: DynamicPrepare | { accepted: true; effect_hash: string } | Record<string, never>; body_json: string; request_hash: string };
export const dynamicCanonicalJson = canonical;
export const dynamicRequestHash = hash;
export const dynamicUUID = uuid;
export const dynamicDigest = digest;
const originals = new WeakMap<object, string>();
const reads = new WeakSet<object>();
export const getOriginalDynamicExecutionResponse = (value: object) => originals.get(value) ?? null;
export const isFreshDynamicExecutionRead = (value: object) => reads.has(value);
export function dynamicCheck(value: unknown): asserts value { if (!value) throw new Error('动态目标原模型、周期、请求或固定经济后果不一致；保留原件，未解除请求。'); }
const exact = (value: Record<string, unknown>, fields: string[]) => Object.keys(value).sort().join('|') === [...fields].sort().join('|');
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const cents = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((row) => typeof row === 'string');
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.every(uuid) && new Set(value).size === value.length;
const save = <T extends object>(value: T, raw?: string): T => { if (raw !== undefined) originals.set(value, raw); return value; };

export function parseDynamicPrepare(value: unknown): DynamicPrepare {
  dynamicCheck(object(value) && exact(value, ['goal_id', 'expected_policy_version_id', 'expected_model_evidence_id', 'expected_model_evidence_hash', 'expected_epoch_id', 'idempotency_key']));
  dynamicCheck(['goal_id', 'expected_policy_version_id', 'expected_model_evidence_id', 'expected_epoch_id'].every((field) => uuid(value[field])) && digest(value.expected_model_evidence_hash) && typeof value.idempotency_key === 'string' && value.idempotency_key.trim().length > 0 && value.idempotency_key.length <= 120); return value as DynamicPrepare;
}
/** Compact proof integrity only. The server verifies actual sources/audit and recomputes permission. */
export async function parseDynamicProof(value: unknown, body: DynamicPrepare, user: string): Promise<DynamicProof> {
  dynamicCheck(object(value) && exact(value, ['protocol', 'simulation', 'bank_authority', 'preserves_original_permission_checks', 'user_id', 'goal_id', 'epoch_id', 'policy_version_id', 'model_evidence_id', 'model_evidence_hash', 'as_of', 'status', 'minimum_cents', 'dynamic_cap_cents', 'remaining_max_cents', 'nominal_remaining_target_cents', 'context_hash', 'input_hash', 'full_projection_input_hash', 'effect_hash', 'proof_hash', 'reserve', 'reasons']));
  dynamicCheck(value.protocol === 'full-dynamic-goal-execution-v1' && value.simulation === true && value.bank_authority === false && value.preserves_original_permission_checks === true && value.user_id === user && value.goal_id === body.goal_id && value.epoch_id === body.expected_epoch_id && value.policy_version_id === body.expected_policy_version_id && value.model_evidence_id === body.expected_model_evidence_id && value.model_evidence_hash === body.expected_model_evidence_hash && time(value.as_of) && ['VERIFIED_RANGE', 'BLOCKED', 'UNKNOWN'].includes(value.status as string) && strings(value.reasons));
  assertMoneyFields(value); for (const field of ['minimum_cents', 'dynamic_cap_cents', 'remaining_max_cents', 'nominal_remaining_target_cents']) dynamicCheck(value[field] === null || cents(value[field]));
  dynamicCheck(['context_hash', 'input_hash', 'proof_hash'].every((field) => digest(value[field])) && [value.full_projection_input_hash, value.effect_hash].every((row) => row === null || digest(row)));
  if (value.reserve !== null) {
    const r = value.reserve; dynamicCheck(object(r) && r.algorithm_version === 'remaining-month-gross-pacing-v1' && r.goal_id === body.goal_id && r.policy_version_id === body.expected_policy_version_id && r.grants_authority === false && r.preview_only === true && r.future_income_included_cents === 0 && r.actual_completion_date === null && digest(r.input_hash) && strings(r.reasons));
    dynamicCheck(typeof r.period === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(r.period) && ['READY', 'PARTIAL', 'MINIMUM_SHORTFALL', 'HARD_GUARANTEE_SHORTFALL', 'DEADLINE_BLOCKED', 'OVERDUE_READY', 'COMPLETE', 'MONTHLY_MAX_ALREADY_EXCEEDED', 'EXPIRED_POLICY', 'INACTIVE_POLICY', 'LIQUIDITY_RISK', 'INSUFFICIENT_EVIDENCE'].includes(r.status as string));
    dynamicCheck(value.dynamic_cap_cents === r.suggested_additional_cents);
  }
  if (value.status === 'VERIFIED_RANGE') dynamicCheck(object(value.reserve) && value.reserve.status === 'READY' && cents(value.minimum_cents) && cents(value.dynamic_cap_cents) && cents(value.remaining_max_cents) && cents(value.nominal_remaining_target_cents) && value.dynamic_cap_cents > 0 && value.minimum_cents <= value.dynamic_cap_cents && value.dynamic_cap_cents <= value.remaining_max_cents && digest(value.full_projection_input_hash) && value.reasons.length === 0);
  else dynamicCheck(value.reasons.length > 0);
  const { proof_hash: declared, ...compact } = value; dynamicCheck(declared === await hash(compact)); return value as DynamicProof;
}
export async function parseDynamicPreview(value: unknown, body: DynamicPrepare, user: string, raw?: string): Promise<DynamicPreview> {
  parseDynamicPrepare(body); dynamicCheck(uuid(user) && object(value) && value.simulation === true && value.bank_authority === false && value.grants_authority === false && value.preview_only === true && value.user_id === user && canonical(value.request) === canonical(body) && value.execution_pipeline === 'ORIGINAL_ALLOCATE_GOAL_REQUIRES_INSTALLED_TYPED_HOOK' && strings(value.limitations)); await parseDynamicProof(value.proof, body, user); return save(value as DynamicPreview, raw);
}
/** Match the actual immutable Action effect; this does not reproduce financial authorization. */
export async function parseDynamicAction(value: unknown, body: DynamicPrepare, user: string, expected?: DynamicAction | null, raw?: string): Promise<DynamicAction> {
  dynamicCheck(object(value) && value.simulation === true && value.user_id === user && uuid(value.action_id) && uuid(value.decision_run_id) && ['PLANNED', 'AUTHORIZED', 'SUBMITTED', 'EXECUTING', 'UNKNOWN', 'SUCCEEDED', 'RECONCILED', 'FAILED', 'INVALIDATED', 'CANCELLED'].includes(value.status as string) && ['AUTO_EXECUTE', 'ASK_ONCE', 'BLOCKED'].includes(value.autonomy_level as string) && digest(value.effect_hash) && time(value.prepared_at) && time(value.as_of) && (value.bank_status === null || ['PENDING', 'UNKNOWN', 'SETTLED', 'REJECTED', 'FAILED', 'EXECUTING'].includes(value.bank_status as string)));
  const e = value.effect; dynamicCheck(object(e) && e.simulation === true && e.user_id === user && e.operation_id === value.action_id && e.action_type === 'ALLOCATE_GOAL' && e.goal_id === body.goal_id && e.policy_version_id === body.expected_policy_version_id && uuid(e.policy_id) && uuid(e.destination_account_id) && cents(e.amount_cents) && e.amount_cents > 0 && e.fee_cents === 0 && e.loss_cents === 0 && e.net_cents === null && e.quote_id === null && e.settlement_delay_days === 0 && e.latest_arrival_at === null && time(e.valid_from) && time(e.expires_at) && Date.parse(e.expires_at) > Date.parse(e.valid_from) && ids(e.policy_version_ids) && e.policy_version_ids.includes(body.expected_policy_version_id) && Array.isArray(e.cash_uses) && e.cash_uses.length > 0 && Array.isArray(e.income_uses) && e.income_uses.length > 0);
  for (const field of ['liability', 'payee_id', 'payee_evidence_id', 'product_id', 'product_version_number', 'terms_digest', 'position_id', 'position_account_id', 'return_account_id', 'purchase_exit', 'original_policy_version_id']) dynamicCheck(e[field] === null);
  let cash = 0n, income = 0n; const accounts = new Set<string>(), locations = new Set<string>();
  for (const row of e.cash_uses) { dynamicCheck(object(row) && uuid(row.account_id) && cents(row.amount_cents) && row.amount_cents > 0 && !accounts.has(row.account_id)); accounts.add(row.account_id); cash += BigInt(row.amount_cents); }
  for (const row of e.income_uses) { dynamicCheck(object(row) && uuid(row.fragment_id) && uuid(row.origin_transaction_id) && uuid(row.account_id) && cents(row.amount_cents) && row.amount_cents > 0 && accounts.has(row.account_id)); const location = `${row.origin_transaction_id}:${row.account_id}`; dynamicCheck(!locations.has(location)); locations.add(location); income += BigInt(row.amount_cents); }
  dynamicCheck(cash === BigInt(e.amount_cents) && income === cash && typeof e.business_key === 'string' && e.business_key.length > 0);
  const sorted = { ...e, cash_uses: [...e.cash_uses].sort((a, b) => String(a.account_id).localeCompare(String(b.account_id))), income_uses: [...e.income_uses].sort((a, b) => `${a.origin_transaction_id}:${a.account_id}`.localeCompare(`${b.origin_transaction_id}:${b.account_id}`)), policy_version_ids: [...e.policy_version_ids].sort() }; dynamicCheck(value.effect_hash === await hash(sorted));
  dynamicCheck(object(value.prepared_validation) && value.prepared_validation.simulation === true && value.prepared_validation.financial_only === true && value.prepared_validation.effect_hash === value.effect_hash && ['READY', 'CONFIRMATION_REQUIRED', 'BLOCKED', 'INSUFFICIENT_EVIDENCE'].includes(value.prepared_validation.status as string));
  if (expected) dynamicCheck(value.action_id === expected.action_id && value.decision_run_id === expected.decision_run_id && value.effect_hash === expected.effect_hash && canonical(e) === canonical(expected.effect));
  if (value.receipt !== null) { const r = value.receipt; dynamicCheck(object(r) && r.simulation === true && r.action_id === value.action_id && uuid(r.receipt_id) && uuid(r.bank_operation_id) && typeof r.status === 'string' && r.executed_cents === e.amount_cents && r.fee_cents === 0 && r.loss_cents === 0 && ids(r.posting_ids) && time(r.occurred_at) && (r.reconciled_at === null || time(r.reconciled_at))); }
  if (['SUCCEEDED', 'RECONCILED'].includes(value.status as string)) dynamicCheck(value.bank_status === 'SETTLED' && object(value.receipt));
  assertMoneyFields(value); return save(value as DynamicAction, raw);
}
export async function parseDynamicIntent(value: unknown): Promise<DynamicIntent> {
  dynamicCheck(object(value) && exact(value, ['protocol', 'kind', 'user_id', 'prepare_request', 'action', 'path', 'body', 'body_json', 'request_hash']) && value.protocol === 'full-dynamic-goal-browser-v1' && uuid(value.user_id) && ['PREPARE', 'CONFIRM', 'EXECUTE'].includes(value.kind as string) && object(value.body) && typeof value.body_json === 'string' && value.body_json === JSON.stringify(value.body) && digest(value.request_hash) && value.request_hash === await hash(value.body));
  const prepare = parseDynamicPrepare(value.prepare_request);
  if (value.kind === 'PREPARE') dynamicCheck(value.path === '/dynamic-goal-actions/prepare' && value.action === null && canonical(value.body) === canonical(prepare));
  else { const action = await parseDynamicAction(value.action, prepare, value.user_id); dynamicCheck(value.path === `/actions/${action.action_id}/${value.kind === 'CONFIRM' ? 'confirm' : 'execute'}`); if (value.kind === 'CONFIRM') dynamicCheck(exact(value.body, ['accepted', 'effect_hash']) && value.body.accepted === true && value.body.effect_hash === action.effect_hash); else dynamicCheck(exact(value.body, [])); }
  return value as DynamicIntent;
}
export async function previewDynamicGoal(body: DynamicPrepare, user: string): Promise<DynamicPreview> { parseDynamicPrepare(body); const value = await request<{ simulation: true; value: unknown; raw: string }>('/dynamic-goal-actions/preview', 'POST', body, (value, raw) => ({ simulation: true, value, raw })); return parseDynamicPreview(value.value, body, user, value.raw); }
export async function postDynamicGoal(intent: DynamicIntent): Promise<DynamicAction> { await parseDynamicIntent(intent); const value = await request<{ simulation: true; value: unknown; raw: string }>(intent.path, 'POST', intent.body, (value, raw) => ({ simulation: true, value, raw })); return parseDynamicAction(value.value, intent.prepare_request, intent.user_id, intent.action, value.raw); }
export async function parseDynamicLookup(value: unknown, intent: DynamicIntent, raw?: string): Promise<DynamicLookup> {
  await parseDynamicIntent(intent); dynamicCheck(object(value) && value.simulation === true && value.bank_authority === false && value.grants_authority === false && value.current_authority === false && value.not_found_is_final === false && value.confirmation_is_current_authority === false && value.user_id === intent.user_id && value.idempotency_key === intent.prepare_request.idempotency_key && ['NOT_FOUND', 'RECORDED'].includes(value.status as string) && ['OPEN', 'SEALED', 'MISSING'].includes(value.epoch_state as string) && typeof value.historical === 'boolean' && ['ABSENT', 'VERIFIED_AT_CONFIRMATION', 'MISSING'].includes(value.confirmation_status as string));
  if (value.status === 'NOT_FOUND') { for (const field of ['original_request', 'original_action_request', 'client_request_hash', 'server_request_hash', 'action', 'confirmation', 'confirmation_verified_at']) dynamicCheck(value[field] === null); dynamicCheck(value.epoch_state === 'MISSING' && value.historical === false && value.confirmation_status === 'ABSENT'); }
  else {
    dynamicCheck(canonical(parseDynamicPrepare(value.original_request)) === canonical(intent.prepare_request) && value.client_request_hash === await hash(intent.prepare_request) && object(value.original_action_request) && digest(value.server_request_hash) && value.server_request_hash === await hash(value.original_action_request) && value.epoch_state !== 'MISSING' && value.historical === (value.epoch_state !== 'OPEN'));
    const action = await parseDynamicAction(value.action, intent.prepare_request, intent.user_id, intent.action); const original = value.original_action_request; const marker = original.full_dynamic_goal_execution;
    dynamicCheck(object(marker) && marker.protocol === 'full-dynamic-goal-execution-v1' && marker.user_id === intent.user_id && marker.epoch_id === intent.prepare_request.expected_epoch_id && canonical(marker.request) === canonical(intent.prepare_request) && marker.request_hash === value.client_request_hash && marker.effect_hash === action.effect_hash && object(original.execution) && canonical(original.execution.effect) === canonical(action.effect) && original.execution.effect_hash === action.effect_hash);
    const proof = await parseDynamicProof(marker.original_proof, intent.prepare_request, intent.user_id); dynamicCheck(proof.status === 'VERIFIED_RANGE' && proof.effect_hash === action.effect_hash && proof.dynamic_cap_cents === action.effect.amount_cents);
    if (value.confirmation_status === 'VERIFIED_AT_CONFIRMATION') { const c = value.confirmation; dynamicCheck(object(c) && c.user_id === intent.user_id && c.operation_id === action.action_id && c.effect_hash === action.effect_hash && uuid(c.evidence_id) && original.confirmation_evidence_id === c.evidence_id && time(c.confirmed_at) && time(c.expires_at) && c.expires_at === action.effect.expires_at && Date.parse(c.confirmed_at) >= Date.parse(action.prepared_at) && Date.parse(c.expires_at) > Date.parse(c.confirmed_at) && value.confirmation_verified_at === c.confirmed_at); }
    else dynamicCheck(value.confirmation === null && value.confirmation_verified_at === null);
    if (value.confirmation_status === 'MISSING') dynamicCheck(action.autonomy_level === 'ASK_ONCE' && ['AUTHORIZED', 'SUBMITTED', 'UNKNOWN', 'SUCCEEDED', 'RECONCILED'].includes(action.status));
  }
  return save(value as DynamicLookup, raw);
}
/** Only this independent server-owned GET may release pending. */
export async function lookupDynamicGoal(intent: DynamicIntent): Promise<DynamicLookup> { await parseDynamicIntent(intent); const value = await request<{ simulation: true; value: unknown; raw: string }>(`/dynamic-goal-actions/by-key/${encodeURIComponent(intent.prepare_request.idempotency_key)}`, 'GET', undefined, (value, raw) => ({ simulation: true, value, raw })); const parsed = await parseDynamicLookup(value.value, intent, value.raw); reads.add(parsed); return parsed; }
