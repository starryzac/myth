import type { components } from '../../../../packages/contracts/schema';
import type { Goal } from './goals';
import { ApiError, request } from './http';
import { parseOnboardingAccounts } from './onboarding';
import { parseReleaseConfirmation, parseReleaseLookup, parseReleaseResponse, parseReleaseScope, releaseDigest, releaseHash, releaseUUID, releaseUUID5 } from './goal-release-authorizations';
import type { ReleaseIntent, ReleaseLookup, ReleaseResponse } from './goal-release-authorizations';
import { parseReallocationPreview, repairConditions } from './goal-reallocation';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { beginWriteFlight, endWriteFlight } from '../features/write-flight';

export type CashGoal = Pick<Goal, 'id' | 'name' | 'policy_id' | 'policy_version_id'>;
export type CashPrepare = components['schemas']['GoalReleasePrepareRequest'];
export type CashExecute = components['schemas']['GoalReleaseExecuteRequest'] & { accepted: true };
export type CashAction = components['schemas']['GoalReleaseActionResponse'];
export type CashCandidate = components['schemas']['GoalReleaseCandidate'];
export type CashLookup = components['schemas']['GoalReleaseLookup'];
export type CashUse = components['schemas']['GoalReleaseUse'];
export type CashBinding = { goal: CashGoal; userId: string; epochId: string };
export type CashIntent = { protocol: 'goal-cash-release-browser-v1'; user_id: string; owner_goal: CashGoal; body: CashPrepare; body_json: string; client_intent_hash: string; authorization: ReleaseResponse };
const originals = new WeakMap<object, string>();
const freshReads = new WeakSet<object>();
export const cashOriginalJson = (value: object) => originals.get(value) ?? null;
export const isFreshCashRead = (value: object) => freshReads.has(value);
function save<T extends object>(value: T, raw?: string): T { if (raw !== undefined) originals.set(value, raw); return value; }
function check(value: unknown): asserts value { if (!value) throw new Error('回拨原身份、效果、来源或回执未完整匹配；原请求继续保留'); }
const exact = (value: Record<string, unknown>, keys: readonly string[]) => Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const money = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0;
const list = (v: unknown): v is string[] => Array.isArray(v) && v.every((s) => typeof s === 'string');
export const cashKey = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,149}$/.test(v);
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const sameSet = (a: unknown, b: string[]) => list(a) && a.length === b.length && new Set(a).size === a.length && [...a].sort().join('|') === [...b].sort().join('|');
export function parseCashPrepare(value: unknown): CashPrepare {
  check(object(value) && exact(value, ['policy_id', 'source_goal_id', 'expected_policy_version_id', 'expected_goal_policy_version_id', 'expected_epoch_id', 'authorization_epoch_id', 'authorization_idempotency_key', 'destination_account_id', 'idempotency_key']));
  check(['policy_id', 'source_goal_id', 'expected_policy_version_id', 'expected_goal_policy_version_id', 'expected_epoch_id', 'authorization_epoch_id', 'destination_account_id'].every((k) => releaseUUID(value[k])) && cashKey(value.idempotency_key) && cashKey(value.authorization_idempotency_key) && value.authorization_epoch_id === value.expected_epoch_id);
  return value as CashPrepare;
}
export function parseCashExecute(value: unknown): CashExecute {
  check(object(value) && exact(value, ['accepted', 'reviewed_effect_hash', 'expected_epoch_id']) && value.accepted === true && releaseDigest(value.reviewed_effect_hash) && releaseUUID(value.expected_epoch_id)); return value as CashExecute;
}
export const cashIntentEnvelope = (userId: string, body: CashPrepare) => ({ kind: 'PREPARE_GOAL_CASH_RELEASE', user_id: userId, body });
function authIntent(value: unknown, goal: CashGoal, userId: string): ReleaseIntent {
  check(object(value) && object(value.original_authorization)); const a = value.original_authorization;
  const body = parseReleaseConfirmation(a.original_request), scope = parseReleaseScope(a.scope);
  check(a.user_id === userId && releaseUUID(a.policy_id) && releaseDigest(a.request_hash) && scope.source_goals.some((g) => g.goal_id === goal.id));
  return { protocol: 'goal-release-browser-command-v1', user_id: userId, owner_goal_id: goal.id, policy_id: a.policy_id, path: `/goal-release-authorizations/policies/${a.policy_id}/confirm`, body, body_json: JSON.stringify(body), reviewed_scope: scope, request_hash: a.request_hash };
}
export async function parseCashAuthorization(value: unknown, goal: CashGoal, userId: string, raw?: string): Promise<ReleaseResponse> {
  return parseReleaseResponse(value, authIntent(value, goal, userId), raw);
}
export async function lookupCashAuthorization(goal: CashGoal, userId: string, epoch: string, key: string): Promise<ReleaseLookup> {
  check(releaseUUID(epoch) && cashKey(key));
  return request(`/goal-release-authorizations/commands/${epoch}/by-key/${encodeURIComponent(key)}`, 'GET', undefined, async (value, raw) => {
    check(object(value) && value.user_id === userId && value.epoch_id === epoch && value.idempotency_key === key && value.replacement_allowed === false);
    if (value.status === 'NOT_FOUND_NOT_FINAL') { check(value.original === null); return value as ReleaseLookup; }
    check(value.status === 'RECORDED'); return parseReleaseLookup(value, authIntent(value.original, goal, userId), raw);
  });
}
export const getCashAccounts = (userId: string) => request('/accounts/summary', 'GET', undefined, (value, raw) => { const parsed = parseOnboardingAccounts(value, raw); check(parsed.user_id === userId); return parsed; });
export async function createCashIntent(binding: CashBinding, authorization: ReleaseResponse, destination: string, key: string): Promise<CashIntent> {
  const a = (await parseCashAuthorization(authorization, binding.goal, binding.userId)).original_authorization;
  const source = a.scope.source_goals.find((g) => g.goal_id === binding.goal.id); check(source && source.original_policy_id === binding.goal.policy_id && source.original_policy_version_id === binding.goal.policy_version_id && a.epoch_id === binding.epochId);
  const body = parseCashPrepare({ policy_id: a.policy_id, source_goal_id: binding.goal.id, expected_policy_version_id: a.policy_version_id, expected_goal_policy_version_id: binding.goal.policy_version_id, expected_epoch_id: binding.epochId, authorization_epoch_id: a.epoch_id, authorization_idempotency_key: a.idempotency_key, destination_account_id: destination, idempotency_key: key });
  return { protocol: 'goal-cash-release-browser-v1', user_id: binding.userId, owner_goal: structuredClone(binding.goal), body, body_json: JSON.stringify(body), client_intent_hash: await releaseHash(cashIntentEnvelope(binding.userId, body)), authorization: structuredClone(authorization) };
}
export async function parseCashIntent(value: unknown): Promise<CashIntent> {
  check(object(value) && exact(value, ['protocol', 'user_id', 'owner_goal', 'body', 'body_json', 'client_intent_hash', 'authorization']) && value.protocol === 'goal-cash-release-browser-v1' && releaseUUID(value.user_id) && object(value.owner_goal));
  const goal = value.owner_goal; check(['id', 'policy_id', 'policy_version_id'].every((k) => releaseUUID(goal[k])) && typeof goal.name === 'string'); const body = parseCashPrepare(value.body);
  check(body.source_goal_id === goal.id && body.expected_goal_policy_version_id === goal.policy_version_id && value.body_json === JSON.stringify(body) && value.client_intent_hash === await releaseHash(cashIntentEnvelope(value.user_id, body)));
  const authorization = await parseCashAuthorization(value.authorization, goal as CashGoal, value.user_id); const a = authorization.original_authorization;
  check(a.policy_id === body.policy_id && a.policy_version_id === body.expected_policy_version_id && a.epoch_id === body.expected_epoch_id && a.idempotency_key === body.authorization_idempotency_key);
  return value as CashIntent;
}
async function uses(value: unknown): Promise<CashUse[]> {
  check(Array.isArray(value) && value.length > 0 && value.length <= 10000); const identities: string[] = [];
  for (const row of value) {
    check(object(row) && exact(row, ['allocation_action_id', 'original_policy_version_id', 'fragment_id', 'origin_transaction_id', 'income_location_account_id', 'allocation_effect_hash', 'allocation_bank_request_hash', 'allocation_action_request_hash', 'amount_cents']));
    check(['allocation_action_id', 'original_policy_version_id', 'fragment_id', 'origin_transaction_id', 'income_location_account_id'].every((k) => releaseUUID(row[k])) && ['allocation_effect_hash', 'allocation_bank_request_hash', 'allocation_action_request_hash'].every((k) => releaseDigest(row[k])) && money(row.amount_cents) && row.amount_cents > 0);
    check(row.fragment_id === await releaseUUID5(row.origin_transaction_id as string, `income-location:${row.income_location_account_id}`)); identities.push(`${row.allocation_action_id}:${row.fragment_id}`);
  }
  check(new Set(identities).size === identities.length && same(identities, [...identities].sort())); return value as CashUse[];
}
export async function parseCashCandidate(value: unknown, intent: CashIntent, raw?: string): Promise<CashCandidate> {
  await parseCashIntent(intent); assertMoneyFields(value);
  check(object(value) && exact(value, ['user_id', 'epoch_id', 'as_of', 'original_request', 'state', 'amount_cents', 'actual_preview', 'actual_inventory', 'original_authorization', 'protection', 'selected_release_uses', 'reasons', 'input_hash', 'bank_authority', 'creates_new_income']) && value.user_id === intent.user_id && value.epoch_id === intent.body.expected_epoch_id && time(value.as_of) && same(value.original_request, intent.body) && ['READY', 'UNKNOWN', 'BLOCKED'].includes(String(value.state)) && releaseDigest(value.input_hash) && list(value.reasons) && value.bank_authority === false && value.creates_new_income === false);
  const subset = { policy_id: intent.body.policy_id, source_goal_id: intent.body.source_goal_id, expected_policy_version_id: intent.body.expected_policy_version_id, expected_goal_policy_version_id: intent.body.expected_goal_policy_version_id, expected_epoch_id: intent.body.expected_epoch_id };
  const preview = parseReallocationPreview(value.actual_preview, { goal: intent.owner_goal, userId: intent.user_id, epochId: intent.body.expected_epoch_id }, subset);
  const inventory = value.actual_inventory; check(object(inventory) && inventory.schema_version === 'goal-release-inventory-v1' && inventory.user_id === intent.user_id && inventory.epoch_id === value.epoch_id && inventory.as_of === value.as_of && inventory.goal_id === intent.body.source_goal_id && inventory.goal_policy_version_id === intent.body.expected_goal_policy_version_id && inventory.full_policy_id === intent.body.policy_id && releaseDigest(inventory.source_binding_hash) && list(inventory.reasons) && ['VERIFIED', 'UNKNOWN'].includes(String(inventory.state)) && inventory.bank_authority === false && inventory.funds_released === false && inventory.creates_new_income === false && inventory.changes_original_assigned_income === false && Array.isArray(inventory.inventory) && inventory.inventory.length === 4 && new Set(inventory.inventory.map((r) => object(r) ? r.table : null)).size === 4 && object(inventory.policy_usage));
  if (value.state === 'READY') {
    check(money(value.amount_cents) && value.amount_cents > 0 && value.reasons.length === 0 && preview.source_issues.length === 0 && preview.decision.math.minimum_repair_cents === value.amount_cents && inventory.state === 'VERIFIED' && inventory.financial_truth_verified === true && inventory.application_projection_matched === true && inventory.reasons.length === 0 && inventory.inventory.every((r) => object(r) && r.complete === true && r.actual_count === r.captured_count && money(r.actual_count) && Array.isArray(r.original_refs) && r.original_refs.length === r.actual_count));
    const auth = await parseCashAuthorization(value.original_authorization, intent.owner_goal, intent.user_id); check(auth.current_scope_status === 'CURRENT' && auth.evidence_id === intent.authorization.evidence_id && auth.evidence_hash === intent.authorization.evidence_hash);
    const protection = value.protection; check(object(protection) && protection.status === 'VERIFIED_NONWORSENING' && protection.compared_point_count === 1098 && protection.expected_point_count === 1098 && releaseDigest(protection.actual_before_input_hash) && releaseDigest(protection.hypothetical_after_input_hash) && releaseDigest(protection.source_hash) && same(protection.reasons, []) && protection.bank_authority === false && protection.hypothetical_is_bank_fact === false);
    const selected = await uses(value.selected_release_uses); check(selected.reduce((sum, row) => sum + BigInt(row.amount_cents), 0n) === BigInt(value.amount_cents));
    check(sameSet(inventory.inventory.map((r) => object(r) ? r.table : null), ['action_plans', 'action_receipts', 'bank_operations', 'simulated_bank_postings']));
    const available = await uses(inventory.release_uses_available);
    for (const row of selected) {
      const original = available.find((r) => r.allocation_action_id === row.allocation_action_id && r.fragment_id === row.fragment_id);
      check(original && row.amount_cents <= original.amount_cents && await releaseHash({ ...original, amount_cents: row.amount_cents }) === await releaseHash(row));
    }
    check(inventory.policy_usage.state === 'VERIFIED' && money(inventory.policy_usage.cap_occupied_cents) && BigInt(inventory.policy_usage.cap_occupied_cents) + BigInt(value.amount_cents) <= BigInt(auth.original_authorization.scope.total_cap_cents) && value.amount_cents <= auth.original_authorization.scope.single_action_cap_cents);
  } else check(value.amount_cents === null && same(value.selected_release_uses, []) && value.reasons.length > 0);
  return save(value as CashCandidate, raw);
}
export async function parseCashAction(value: unknown, intent: CashIntent, previous: CashAction | null = null, raw?: string): Promise<CashAction> {
  await parseCashIntent(intent); assertMoneyFields(value);
  check(object(value) && exact(value, ['schema_version', 'simulation', 'user_id', 'as_of', 'action_id', 'original_action_status', 'original_command', 'original_request_hash', 'original_prepare_request', 'decision_run_id', 'action_confirmation_evidence_id', 'action_confirmation_verified', 'bank_operation_id', 'original_bank_status', 'bank_settlement_legs_verified', 'receipt_id', 'service_receipt_verified', 'original_receipt', 'unresolved', 'read_only_response', 'current_authority_assessed', 'receipt_is_current_authority', 'economic_experiment_verified', 'grants_new_authority']) && value.schema_version === 'goal-release-action-v1' && value.simulation === true && value.user_id === intent.user_id && time(value.as_of) && releaseDigest(value.original_request_hash) && releaseUUID(value.decision_run_id) && value.read_only_response === true && ['current_authority_assessed', 'receipt_is_current_authority', 'economic_experiment_verified', 'grants_new_authority'].every((k) => value[k] === false) && same(value.original_prepare_request, intent.body));
  check(value.action_id === await releaseUUID5('321c7149-2db6-5338-a4de-0166d6cdf303', `${intent.user_id}:${intent.body.expected_epoch_id}:${intent.body.idempotency_key}`));
  const command = value.original_command; check(object(command) && exact(command, ['protocol', 'effect', 'effect_hash']) && command.protocol === 'full-goal-release-bank-v1' && object(command.effect) && releaseDigest(command.effect_hash)); const effect = command.effect;
  check(exact(effect, ['protocol', 'simulation', 'action_type', 'operation_id', 'user_id', 'epoch_id', 'business_key', 'bank_idempotency_key', 'policy_id', 'policy_version_id', 'policy_configuration_hash', 'authorization_id', 'authorization_evidence_id', 'authorization_evidence_hash', 'authorization_scope_hash', 'authorization_request_hash', 'source_goal_id', 'original_goal_policy_id', 'original_goal_policy_version_id', 'full_model_evidence_id', 'full_model_evidence_hash', 'full_configuration_hash', 'minimum_guarantee_cents', 'source_account_id', 'destination_account_id', 'destination_scope', 'amount_cents', 'emergency_conditions', 'release_uses', 'source_provenance_hash', 'financial_input_hash', 'valid_from', 'expires_at', 'cumulative_scope', 'fee_cents', 'loss_cents', 'principal_change_cents', 'other_goal_change_cents', 'available_income_increase_cents', 'assigned_income_decrease_cents']));
  const a = intent.authorization.original_authorization, source = a.scope.source_goals.find((g) => g.goal_id === intent.body.source_goal_id); check(source);
  check(effect.protocol === 'full-goal-release-effect-v1' && effect.simulation === true && effect.action_type === 'RELEASE_GOAL' && effect.operation_id === value.action_id && effect.user_id === intent.user_id && effect.epoch_id === intent.body.expected_epoch_id && effect.policy_id === a.policy_id && effect.policy_version_id === a.policy_version_id && effect.policy_configuration_hash === a.scope.policy_configuration_hash && effect.authorization_id === a.authorization_id && effect.authorization_evidence_id === intent.authorization.evidence_id && effect.authorization_evidence_hash === intent.authorization.evidence_hash && effect.authorization_scope_hash === a.scope_hash && effect.authorization_request_hash === a.request_hash && effect.source_goal_id === source.goal_id && effect.original_goal_policy_id === source.original_policy_id && effect.original_goal_policy_version_id === source.original_policy_version_id && effect.full_model_evidence_id === source.full_model_evidence_id && effect.full_model_evidence_hash === source.full_model_evidence_hash && effect.full_configuration_hash === source.full_configuration_hash && effect.minimum_guarantee_cents === source.minimum_guarantee_cents && releaseUUID(effect.source_account_id) && effect.source_account_id !== effect.destination_account_id && effect.destination_account_id === intent.body.destination_account_id && effect.destination_scope === 'PROTECTED_CASH' && effect.cumulative_scope === 'POLICY_ID_ALL_VERSIONS');
  check(effect.business_key === `goal-release:${value.action_id}` && effect.bank_idempotency_key === 'goal-release:' + await releaseHash({ user_id: intent.user_id, epoch_id: intent.body.expected_epoch_id, key: intent.body.idempotency_key }) && money(effect.amount_cents) && effect.amount_cents > 0 && effect.amount_cents <= a.scope.single_action_cap_cents && time(effect.valid_from) && time(effect.expires_at) && Date.parse(effect.valid_from) >= Date.parse(a.confirmed_at) && Date.parse(effect.valid_from) < Date.parse(effect.expires_at) && Date.parse(effect.expires_at) <= Date.parse(a.valid_until) && ['source_provenance_hash', 'financial_input_hash'].every((k) => releaseDigest(effect[k])) && ['fee_cents', 'loss_cents', 'principal_change_cents', 'other_goal_change_cents', 'available_income_increase_cents', 'assigned_income_decrease_cents'].every((k) => effect[k] === 0));
  const selected = await uses(effect.release_uses); check(selected.reduce((n, r) => n + BigInt(r.amount_cents), 0n) === BigInt(effect.amount_cents) && command.effect_hash === await releaseHash(effect) && list(effect.emergency_conditions) && effect.emergency_conditions.length > 0 && effect.emergency_conditions.every((c) => repairConditions.includes(c as typeof repairConditions[number]) && a.scope.emergency_conditions.includes(c as typeof repairConditions[number])) && same(effect.emergency_conditions, [...new Set(effect.emergency_conditions)].sort()));
  if (previous) check(value.original_request_hash === previous.original_request_hash && same(command, previous.original_command) && value.decision_run_id === previous.decision_run_id);
  check(['PLANNED', 'AUTHORIZED', 'SUBMITTED', 'UNKNOWN', 'SUCCEEDED', 'RECONCILED', 'INVALIDATED', 'CANCELLED', 'FAILED'].includes(String(value.original_action_status)) && ['action_confirmation_verified', 'bank_settlement_legs_verified', 'service_receipt_verified', 'unresolved'].every((k) => typeof value[k] === 'boolean'));
  if (value.action_confirmation_verified) check(value.action_confirmation_evidence_id === await releaseUUID5(value.action_id as string, 'release-confirmation:' + command.effect_hash)); else check(value.action_confirmation_evidence_id === null && !['AUTHORIZED', 'SUBMITTED', 'UNKNOWN', 'SUCCEEDED', 'RECONCILED'].includes(String(value.original_action_status)));
  if (value.bank_operation_id === null) check(value.original_bank_status === null && value.bank_settlement_legs_verified === false && value.receipt_id === null && value.service_receipt_verified === false && value.original_receipt === null);
  else check(value.bank_operation_id === value.action_id && ['ACCEPTED', 'SETTLED', 'UNKNOWN', 'REJECTED'].includes(String(value.original_bank_status)) && value.bank_settlement_legs_verified === (value.original_bank_status === 'SETTLED'));
  const unresolved = ['SUBMITTED', 'UNKNOWN'].includes(String(value.original_action_status)) || value.bank_operation_id !== null && value.receipt_id === null; check(value.unresolved === unresolved);
  if (value.service_receipt_verified) {
    check(['SUCCEEDED', 'RECONCILED'].includes(String(value.original_action_status)) && value.bank_settlement_legs_verified === true && value.action_confirmation_verified === true && value.unresolved === false && value.receipt_id === await releaseUUID5(value.action_id as string, 'receipt') && object(value.original_receipt)); const r = value.original_receipt;
    check(r.id === value.receipt_id && r.user_id === intent.user_id && r.action_plan_id === value.action_id && r.attempt_number === 1 && r.status === 'SUCCEEDED' && r.receipt_ref === `bank-operation:${value.action_id}` && r.executed_cents === effect.amount_cents && r.fee_cents === 0 && r.loss_cents === 0 && time(r.occurred_at) && time(r.reconciled_at) && time(r.created_at) && Date.parse(r.occurred_at) >= Date.parse(effect.valid_from) && Date.parse(r.occurred_at) < Date.parse(effect.expires_at) && Date.parse(r.reconciled_at) >= Date.parse(r.occurred_at) && Date.parse(r.reconciled_at) <= Date.parse(value.as_of) && r.created_at === r.reconciled_at && object(r.response) && exact(r.response, ['bank_operation_id', 'posting_ids', 'transaction_ids']) && r.response.bank_operation_id === value.action_id);
    const legRefs = [`cash:${effect.source_account_id}`, `cash:${effect.destination_account_id}`, 'goal_cash']; check(sameSet(r.response.posting_ids, await Promise.all(legRefs.map((leg) => releaseUUID5(value.action_id as string, 'posting:' + leg)))) && sameSet(r.response.transaction_ids, await Promise.all(legRefs.slice(0, 2).map((leg) => releaseUUID5(value.action_id as string, 'transaction:' + leg)))));
  } else check(value.receipt_id === null && value.original_receipt === null && !['SUCCEEDED', 'RECONCILED'].includes(String(value.original_action_status)));
  return save(value as CashAction, raw);
}
export async function parseCashLookup(value: unknown, intent: CashIntent, previous: CashAction | null = null, raw?: string): Promise<CashLookup> {
  check(object(value) && exact(value, ['user_id', 'epoch_id', 'idempotency_key', 'status', 'original', 'not_found_is_final', 'replacement_allowed']) && value.user_id === intent.user_id && value.epoch_id === intent.body.expected_epoch_id && value.idempotency_key === intent.body.idempotency_key && value.not_found_is_final === false && value.replacement_allowed === false);
  if (value.status === 'NOT_FOUND_NOT_FINAL') check(value.original === null); else { check(value.status === 'RECORDED'); await parseCashAction(value.original, intent, previous, raw); }
  return save(value as CashLookup, raw);
}
/** Candidate and lookup DTOs lack simulation; preserve their exact original shape. */
async function rawRequest<T>(path: string, method: 'GET' | 'POST', body: unknown, parse: (value: unknown, raw: string) => Promise<T>): Promise<T> {
  if (method === 'POST') beginWriteFlight();
  try {
    let response: Response; try { response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? ''}/api/v1${path}`, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }); } catch { throw new ApiError('连接中断，原回拨请求保留；只读核对原键', 0, 'NETWORK_ERROR', null); }
    const raw = await response.text(); let value: unknown; try { value = JSON.parse(raw); } catch { throw new ApiError('原回拨响应不可解析，未解除原请求', response.status, 'INVALID_RESPONSE', null); }
    if (!response.ok) { const e = object(value) && object(value.error) ? value.error : {}; throw new ApiError(typeof e.message === 'string' ? e.message : '原服务拒绝或未知', response.status, typeof e.code === 'string' ? e.code : 'REQUEST_FAILED', typeof e.request_id === 'string' ? e.request_id : null); } return await parse(value, raw);
  } finally { if (method === 'POST') endWriteFlight(); }
}
export const previewCashIntent = (intent: CashIntent) => rawRequest('/goal-cash-releases/preview', 'POST', intent.body, (v, raw) => parseCashCandidate(v, intent, raw));
export const prepareCashIntent = (intent: CashIntent) => request('/goal-cash-releases/prepare', 'POST', intent.body, (v, raw) => parseCashAction(v, intent, null, raw));
export const executeCashIntent = (intent: CashIntent, original: CashAction, body: CashExecute) => { parseCashExecute(body); check(body.reviewed_effect_hash === original.original_command.effect_hash && body.expected_epoch_id === intent.body.expected_epoch_id); return request(`/goal-cash-releases/actions/${original.action_id}/execute`, 'POST', body, (v, raw) => parseCashAction(v, intent, original, raw)); };
export async function readCashIntent(intent: CashIntent, previous: CashAction | null): Promise<CashLookup> {
  await parseCashIntent(intent);
  if (previous) { const action = await request(`/goal-cash-releases/actions/${previous.action_id}`, 'GET', undefined, (v, raw) => parseCashAction(v, intent, previous, raw)); const result: CashLookup = { user_id: intent.user_id, epoch_id: intent.body.expected_epoch_id, idempotency_key: intent.body.idempotency_key, status: 'RECORDED', original: action, not_found_is_final: false, replacement_allowed: false }; save(result, cashOriginalJson(action) ?? undefined); freshReads.add(result); return result; }
  const value = await rawRequest(`/goal-cash-releases/commands/${intent.body.expected_epoch_id}/by-key/${encodeURIComponent(intent.body.idempotency_key)}`, 'GET', undefined, (v, raw) => parseCashLookup(v, intent, null, raw)); freshReads.add(value); return value;
}
