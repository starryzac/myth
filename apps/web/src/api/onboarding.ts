import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { getDashboard } from './dashboard';
import type { Dashboard } from './dashboard';
import { getDemoState } from './demo';
import type { DemoState } from './demo';
import { getProposals } from './policies';
import type { Compilation, Proposal } from './policies';
import { object, parseMoneyInput, validateForm } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { sameFullPolicyJson } from '../features/full-policy-operation';
import { isDeclarationKind, sameOnboardingBinding, validDeclarationRef, validOnboardingBinding, validOnboardingIntent } from '../features/onboarding-draft';
import type { CandidateRef, DeclarationRef, OnboardingBinding, OnboardingInputs, OnboardingIntent } from '../features/onboarding-draft';

export type Accounts = components['schemas']['AccountSummary'];
export type Transactions = components['schemas']['TransactionPage'];
export type Reserve = components['schemas']['ReserveEstimationResponse'];
export type Discovery = components['schemas']['DiscoveryResult'];
export type DeclarationRecord = components['schemas']['UserDeclarationRecord'];
export type DeclarationLookup = components['schemas']['UserDeclarationLookup'];
export type OnboardingContext = { accounts: Accounts; dashboard: Dashboard; demo: DemoState; binding: OnboardingBinding | null };
export type ReserveParameters = { horizon_days: number; lookback_days: number; quantile: number; extra_buffer_cents: number; essential_categories: string[]; exclude_one_off: boolean };
const originals = new WeakMap<object, string>();
export const getOriginalOnboardingResponse = (value: object): string | null => originals.get(value) ?? null;
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const sha = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const timestamp = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.every(uuid) && new Set(value).size === value.length;
function check(condition: unknown): asserts condition { if (!condition) throw new Error('引导来源、原用户/日期或候选响应不完整，未当作当前授权'); }
function save<T extends object>(value: T, raw?: string): T { if (raw !== undefined) originals.set(value, raw); return value; }
export function serverCalendarDate(asOf: string, timezone: string): string {
  check(timestamp(asOf) && ['UTC', 'Asia/Shanghai'].includes(timezone)); const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(asOf)); const part = (name: string) => parts.find((item) => item.type === name)?.value; return `${part('year')}-${part('month')}-${part('day')}`;
}
export function parseOnboardingAccounts(value: unknown, raw?: string): Accounts {
  check(object(value) && value.simulation === true && uuid(value.user_id) && ['UTC', 'Asia/Shanghai'].includes(value.timezone as string) && Array.isArray(value.accounts) && Array.isArray(value.credit_card_bills)); assertMoneyFields(value);
  check(value.accounts.every((item) => object(item) && uuid(item.id) && typeof item.name === 'string' && typeof item.account_type === 'string' && Number.isSafeInteger(item.balance_cents) && timestamp(item.observed_at)) && new Set(value.accounts.map((item) => item.id)).size === value.accounts.length);
  const accounts = value.accounts;
  check(value.credit_card_bills.every((item) => object(item) && uuid(item.id) && uuid(item.account_id) && accounts.some((account) => account.id === item.account_id)));
  check(['cash_balance_cents', 'position_principal_cents', 'unknown_position_principal_cents', 'credit_card_unpaid_cents'].every((field) => Number.isSafeInteger(value[field])));
  return save(value as Accounts, raw);
}
export async function getOnboardingContext(): Promise<OnboardingContext> {
  const [accounts, dashboard, demo] = await Promise.all([request<Accounts>('/accounts/summary', 'GET', undefined, parseOnboardingAccounts), getDashboard(), getDemoState()]);
  check(accounts.user_id === dashboard.user_id && accounts.timezone === dashboard.timezone);
  const binding: OnboardingBinding | null = demo.available && uuid(demo.epoch_id) ? { user_id: accounts.user_id, epoch_id: demo.epoch_id, reference_date: serverCalendarDate(dashboard.as_of, dashboard.timezone), timezone: dashboard.timezone as OnboardingBinding['timezone'] } : null;
  if (binding) check(validOnboardingBinding(binding)); return { accounts, dashboard, demo, binding };
}
export function parseOnboardingTransactions(value: unknown, offset: number, raw?: string): Transactions {
  check(object(value) && value.simulation === true && Number.isSafeInteger(value.total) && (value.total as number) >= 0 && value.limit === 50 && value.offset === offset && Array.isArray(value.items) && value.items.length <= 50); assertMoneyFields(value);
  check(value.items.every((item) => object(item) && uuid(item.id) && uuid(item.account_id) && typeof item.source_ref === 'string' && (item.evidence_id === null || uuid(item.evidence_id)) && ['CREDIT', 'DEBIT'].includes(item.direction as string) && Number.isSafeInteger(item.amount_cents) && timestamp(item.occurred_at) && timestamp(item.observed_at) && typeof item.category === 'string' && typeof item.is_one_off === 'boolean' && typeof item.category_confirmed === 'boolean') && new Set(value.items.map((item) => item.id)).size === value.items.length);
  check(offset >= (value.total as number) ? value.items.length === 0 : offset + value.items.length <= (value.total as number)); return save(value as Transactions, raw);
}
export function getOnboardingTransactions(offset: number): Promise<Transactions> { check(Number.isSafeInteger(offset) && offset >= 0 && offset <= 1_000_000); return request<Transactions>(`/transactions?limit=50&offset=${offset}`, 'GET', undefined, (value, raw) => parseOnboardingTransactions(value, offset, raw)); }
export function reserveParameters(inputs: OnboardingInputs): ReserveParameters {
  const integer = (text: string) => { check(/^\d+$/.test(text)); const value = Number(text); check(Number.isSafeInteger(value) && value >= 1 && value <= 366); return value; };
  const horizon = integer(inputs.horizon_days); const lookback = integer(inputs.lookback_days); check(horizon <= lookback && /^(?:0?\.\d+|1(?:\.0+)?)$/.test(inputs.quantile)); const quantile = Number(inputs.quantile); check(Number.isFinite(quantile) && quantile > 0 && quantile <= 1);
  const categories = inputs.categories.split(',').map((item) => item.trim()); check(categories.length > 0 && categories.length <= 48 && categories.every((item) => item.length > 0 && item.length <= 48) && new Set(categories).size === categories.length);
  return { horizon_days: horizon, lookback_days: lookback, quantile, extra_buffer_cents: parseMoneyInput(inputs.extra_buffer_yuan), essential_categories: categories, exclude_one_off: inputs.exclude_one_off };
}
export function parseOnboardingReserve(value: unknown, binding: OnboardingBinding, parameters: ReserveParameters, raw?: string): Reserve {
  check(object(value) && value.simulation === true && value.user_id === binding.user_id && timestamp(value.as_of) && serverCalendarDate(value.as_of, binding.timezone) === binding.reference_date && object(value.estimation) && ids(value.source_evidence_ids) && sha(value.input_digest) && Array.isArray(value.source_issues)); assertMoneyFields(value);
  const estimate = value.estimation; check(estimate.algorithm_version === 'living-reserve-nearest-rank-v1' && ['READY', 'INSUFFICIENT_HISTORY'].includes(estimate.status as string) && estimate.reference_date === binding.reference_date && estimate.timezone === binding.timezone && estimate.lookback_days === parameters.lookback_days && estimate.horizon_days === parameters.horizon_days && estimate.quantile === parameters.quantile && estimate.extra_buffer_cents === parameters.extra_buffer_cents && typeof estimate.quantile_fraction === 'string');
  check(object(estimate.normalized_configuration) && object(estimate.normalized_configuration.method) && estimate.normalized_configuration.type === 'living_reserve' && estimate.normalized_configuration.method.exclude_one_off === parameters.exclude_one_off && Array.isArray(estimate.selected_categories) && sameFullPolicyJson([...estimate.selected_categories].sort(), [...parameters.essential_categories].sort()));
  check(Array.isArray(estimate.daily_amounts) && estimate.daily_amounts.length === parameters.lookback_days && estimate.daily_amounts.every((item) => object(item) && typeof item.day === 'string' && typeof item.covered === 'boolean' && (item.amount_cents === null || Number.isSafeInteger(item.amount_cents))) && new Set(estimate.daily_amounts.map((item) => item.day)).size === parameters.lookback_days && Array.isArray(estimate.windows) && estimate.windows.length === estimate.window_count && estimate.windows.every((item) => object(item) && Number.isSafeInteger(item.amount_cents)) && Array.isArray(estimate.coverage_gaps) && ids(estimate.account_ids) && ids(estimate.included_transaction_ids) && Array.isArray(estimate.excluded_transactions) && Array.isArray(estimate.issues));
  check(value.source_issues.every((item) => object(item) && typeof item.code === 'string' && typeof item.source_ref === 'string' && typeof item.message === 'string'));
  if (estimate.status === 'READY') check(object(value.candidate_configuration) && sameFullPolicyJson(value.candidate_configuration, estimate.normalized_configuration) && sha(value.candidate_configuration_hash) && Number.isSafeInteger(estimate.base_reserve_cents) && Number.isSafeInteger(estimate.recommended_reserve_cents) && Number.isSafeInteger(estimate.rank) && (estimate.rank as number) >= 1 && (estimate.rank as number) <= (estimate.window_count as number) && estimate.coverage_gaps.length === 0);
  else check(value.candidate_configuration === null && value.candidate_configuration_hash === null && estimate.base_reserve_cents === null && estimate.recommended_reserve_cents === null && estimate.rank === null);
  return save(value as Reserve, raw);
}
export function estimateOnboardingReserve(binding: OnboardingBinding, parameters: ReserveParameters): Promise<Reserve> {
  check(validOnboardingBinding(binding)); const query = new URLSearchParams(); for (const [key, value] of Object.entries(parameters)) { if (Array.isArray(value)) value.forEach((item) => query.append(key, item)); else query.set(key, String(value)); }
  return request<Reserve>(`/living-reserve/estimate?${query}`, 'GET', undefined, (value, raw) => parseOnboardingReserve(value, binding, parameters, raw));
}
export function parseOnboardingCompilation(value: unknown, binding: OnboardingBinding, raw?: string): Compilation {
  check(object(value) && value.simulation === true && value.user_id === binding.user_id && uuid(value.compilation_id) && object(value.compilation)); const result = value.compilation;
  check(result.compiler_version === 'offline-policy-rules-v1' && result.reference_date === binding.reference_date && result.timezone === binding.timezone && object(result.draft) && Array.isArray(result.issues) && Array.isArray(result.assumptions) && result.assumptions.every((item) => typeof item === 'string')); assertMoneyFields(value);
  check(result.issues.every((item) => object(item) && ['code', 'field', 'message', 'source_fragment'].every((field) => typeof item[field] === 'string')));
  check(value.configuration === null ? value.configuration_hash === null && value.proposal_id === null : object(value.configuration) && ['goal_saving', 'emergency_buffer'].includes(value.configuration.type as string) && sha(value.configuration_hash) && uuid(value.proposal_id)); check(sameFullPolicyJson(result.configuration, value.configuration));
  check(value.proposal_status === null || ['PROPOSED', 'CONFIRMED', 'EXPIRED', 'REJECTED'].includes(value.proposal_status as string)); return save(value as Compilation, raw);
}
export function parseOnboardingDiscovery(value: unknown, binding: OnboardingBinding, raw?: string): Discovery {
  check(object(value) && value.simulation === true && value.user_id === binding.user_id && value.rule_version === 'mvp-104-v1' && timestamp(value.as_of) && serverCalendarDate(value.as_of, binding.timezone) === binding.reference_date && ids(value.created_proposal_ids) && ids(value.reused_proposal_ids) && Array.isArray(value.skipped));
  check(new Set([...value.created_proposal_ids, ...value.reused_proposal_ids]).size === value.created_proposal_ids.length + value.reused_proposal_ids.length && value.skipped.every((item) => object(item) && typeof item.reason_code === 'string' && typeof item.source_ref === 'string')); return save(value as Discovery, raw);
}
export function submitOnboardingIntent(intent: OnboardingIntent): Promise<Compilation | Discovery | DeclarationRecord> { check(validOnboardingIntent(intent)); return isDeclarationKind(intent.kind) ? request<DeclarationRecord>('/policy-declarations', 'POST', JSON.parse(intent.body_json), (value, raw) => parseOnboardingDeclaration(value, intent, raw)) : intent.kind === 'DISCOVERY' ? request<Discovery>('/policies/discover', 'POST', {}, (value, raw) => parseOnboardingDiscovery(value, intent.binding, raw)) : request<Compilation>('/policies/compile', 'POST', JSON.parse(intent.body_json), (value, raw) => parseOnboardingCompilation(value, intent.binding, raw)); }
export function readOnboardingCompilation(ref: Pick<CandidateRef, 'compilation_id' | 'binding'>): Promise<Compilation> { check(uuid(ref.compilation_id) && validOnboardingBinding(ref.binding)); return request<Compilation>(`/policy-compilations/${ref.compilation_id}`, 'GET', undefined, (value, raw) => { const result = parseOnboardingCompilation(value, ref.binding, raw); check(result.compilation_id === ref.compilation_id); return result; }); }
export const getOnboardingProposals = () => getProposals();
export function compilationRef(intent: OnboardingIntent, actual: Compilation): CandidateRef { check(['EMERGENCY', 'GOAL'].includes(intent.kind)); parseOnboardingCompilation(actual, intent.binding); return { compilation_id: actual.compilation_id, proposal_id: actual.proposal_id, configuration_hash: actual.configuration_hash, configuration_type: object(actual.configuration) ? String(actual.configuration.type) : object(actual.compilation.draft) && typeof actual.compilation.draft.type === 'string' ? actual.compilation.draft.type : null, input_text: JSON.parse(intent.body_json).text, binding: intent.binding }; }
/** No POST replay: only a unique existing original proposal+compilation can recover a lost reply. */
export async function findOnboardingCompilation(intent: OnboardingIntent, current: OnboardingBinding, proposals: readonly Proposal[]): Promise<CandidateRef> {
  check(validOnboardingIntent(intent) && ['EMERGENCY', 'GOAL'].includes(intent.kind) && sameOnboardingBinding(intent.binding, current)); const text = JSON.parse(intent.body_json).text;
  const matching = proposals.filter((item) => item.source_type === 'POLICY_COMPILATION' && item.source_text === text && item.compiler_version === 'offline-policy-rules-v1' && uuid(item.compilation_id)); check(matching.length <= 100);
  const originals = await Promise.all(matching.map(async (proposal) => { const actual = await readOnboardingCompilation({ compilation_id: proposal.compilation_id!, binding: intent.binding }); return actual.proposal_id === proposal.id && actual.configuration_hash === proposal.configuration_hash ? compilationRef(intent, actual) : null; })); const found = originals.filter((value): value is CandidateRef => value !== null); check(found.length === 1); return found[0]!;
}
export function parseOnboardingDeclaration(value: unknown, intent: OnboardingIntent, raw?: string): DeclarationRecord {
  check(validOnboardingIntent(intent) && isDeclarationKind(intent.kind) && object(value) && value.simulation === true && value.protocol === 'user-policy-declaration-v1' && value.grants_authority === false && value.dedicated_audit_event_recorded === false && value.receipt_is_current_authority === false && value.status === 'PROPOSED' && ['PROPOSED', 'CONFIRMED', 'REJECTED', 'EXPIRED'].includes(value.current_proposal_status as string) && uuid(value.proposal_id) && uuid(value.evidence_id) && value.epoch_id === intent.binding.epoch_id && timestamp(value.admitted_at) && sha(value.configuration_hash) && sha(value.request_hash) && object(value.configuration) && sameFullPolicyJson(value.original_request, JSON.parse(intent.body_json)) && sameFullPolicyJson(value.configuration, JSON.parse(intent.body_json).configuration)); assertMoneyFields(value); return save(value as DeclarationRecord, raw);
}
export function parseOnboardingDeclarationLookup(value: unknown, intent: OnboardingIntent, raw?: string): DeclarationLookup {
  check(validOnboardingIntent(intent) && isDeclarationKind(intent.kind) && object(value) && value.simulation === true && value.grants_authority === false && value.not_found_is_final === false && ['NOT_FOUND', 'RECORDED'].includes(value.status as string));
  if (value.status === 'NOT_FOUND') check(value.record === null); else parseOnboardingDeclaration(value.record, intent); return save(value as DeclarationLookup, raw);
}
export function readOnboardingDeclaration(intent: OnboardingIntent): Promise<DeclarationLookup> { check(validOnboardingIntent(intent) && isDeclarationKind(intent.kind)); const body = JSON.parse(intent.body_json); return request<DeclarationLookup>(`/policy-declarations/${intent.binding.epoch_id}/by-key/${encodeURIComponent(body.idempotency_key)}`, 'GET', undefined, (value, raw) => parseOnboardingDeclarationLookup(value, intent, raw)); }
export function declarationRef(intent: OnboardingIntent, actual: DeclarationRecord): DeclarationRef { parseOnboardingDeclaration(actual, intent); const ref = { proposal_id: actual.proposal_id, evidence_id: actual.evidence_id, configuration_hash: actual.configuration_hash, request_hash: actual.request_hash, body_json: intent.body_json, binding: intent.binding }; check(isDeclarationKind(intent.kind) && validDeclarationRef(ref, intent.kind)); return ref; }
/** Prepare exact original configuration only; server performs complete strict DSL validation. */
export function onboardingObligationConfiguration(proposal: Proposal, inputs: OnboardingInputs): Record<string, unknown> {
  check(uuid(proposal.id) && object(proposal.configuration) && proposal.configuration.type === 'recurring_obligation' && object(proposal.configuration.priority) && typeof proposal.configuration.auto_execute === 'boolean');
  const importance = integerInput(inputs.importance, 0, 100); const result = { ...structuredClone(proposal.configuration), valid_from: calendarInput(inputs.valid_from), valid_until: calendarInput(inputs.valid_until), priority: { ...structuredClone(proposal.configuration.priority), importance } }; check(validateForm(result).length === 0); return result;
}
function calendarInput(value: string): string | null { if (!value) return null; check(/^\d{4}-\d\d-\d\d$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value); return value; }
function integerInput(value: string, min: number, max = Number.MAX_SAFE_INTEGER): number { check(/^\d+$/.test(value)); const result = Number(value); check(Number.isSafeInteger(result) && result >= min && result <= max); return result; }
export function onboardingAssetConfiguration(inputs: OnboardingInputs): Record<string, unknown> {
  check(['general_idle_funds', 'goal'].includes(inputs.asset_scope) && inputs.asset_name.trim().length > 0 && inputs.asset_name.trim().length <= 120); const classes = inputs.asset_classes.split(',').filter(Boolean); check(classes.length > 0 && new Set(classes).size === classes.length && classes.every((name) => ['CASH', 'CASH_MGMT_T0', 'CASH_MGMT_T1', 'FIXED_DEPOSIT'].includes(name)) && (inputs.asset_scope !== 'goal' || uuid(inputs.asset_goal_id)));
  const result = { type: 'asset_authorization', name: inputs.asset_name.trim(), valid_from: calendarInput(inputs.asset_valid_from), valid_until: calendarInput(inputs.asset_valid_until), scope: inputs.asset_scope, goal_id: inputs.asset_scope === 'goal' ? inputs.asset_goal_id : null, allowed_asset_classes: classes, max_auto_managed_cents: parseMoneyInput(inputs.total_cap_yuan), single_action_cap_cents: parseMoneyInput(inputs.single_cap_yuan), max_redemption_delay_days: integerInput(inputs.redemption_days, 0), max_lock_days: integerInput(inputs.lock_days, 0), max_principal_risk_level: integerInput(inputs.principal_risk, 0, 5), allow_auto_recovery_without_penalty: inputs.auto_recovery, allow_early_withdrawal_with_penalty: inputs.penalty_withdrawal };
  check(result.single_action_cap_cents <= result.max_auto_managed_cents && validateForm(result).length === 0); return result;
}
