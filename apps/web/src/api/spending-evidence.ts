import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { parseOnboardingAccounts, parseOnboardingTransactions } from './onboarding';
import type { Accounts, Transactions } from './onboarding';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';

export type CategoryReview = components['schemas']['CategoryReviewResponse'];
export type CategoryBody = components['schemas']['CategoryConfirmationRequest'];
export type CategoryResult = components['schemas']['CategoryCommandResponse'];
export type Periodic = components['schemas']['PeriodicSuggestions'];
export type Seasonal = components['schemas']['SeasonalSuggestions'];
export type SpendingIntent = {
  protocol: 'spending-category-browser-command-v1'; user_id: string; transaction_id: string;
  path: string; body: CategoryBody; body_json: string; request_hash: string;
  bank_evidence_id: string; bank_evidence_hash: string; before_category: string;
};
export const categoryOptions = ['food', 'transport', 'daily_necessities', 'rent', 'utilities', 'education', 'healthcare', 'other'] as const;
export const spendingUUID = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value);
export const spendingDigest = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const date = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d\d-\d\d$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const integer = (value: unknown, minimum = 0): value is number => Number.isSafeInteger(value) && (value as number) >= minimum;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === 'string');
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.every(spendingUUID) && new Set(value).size === value.length;
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join('|') === keys.sort().join('|');
function check(value: unknown): asserts value { if (!value) throw new Error('支出原件、用户/原键或只读建议响应不完整，未解除原请求'); }
const originals = new WeakMap<object, string>();
export const getOriginalSpendingResponse = (value: object) => originals.get(value) ?? null;
function save<T extends object>(value: T, raw?: string): T { if (raw !== undefined) originals.set(value, raw); return value; }
/** Exact JSON command comparison; no rounded money or derived authority. */
export function spendingCanonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(spendingCanonicalJson).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${spendingCanonicalJson(value[key])}`).join(',')}}`;
  if (value === null || typeof value === 'string' || typeof value === 'boolean' || Number.isSafeInteger(value)) return JSON.stringify(value);
  throw new Error('原分类请求含无法精确表示的 JSON');
}
export async function spendingHash(value: unknown): Promise<string> {
  check(crypto.subtle); const result = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(spendingCanonicalJson(value)));
  return [...new Uint8Array(result)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
export function parseCategoryBody(value: unknown): CategoryBody {
  check(object(value) && exact(value, ['category', 'accepted', 'reviewed_transaction_hash', 'reason', 'idempotency_key', 'expected_epoch_id']) && categoryOptions.includes(value.category as CategoryBody['category']) && value.accepted === true && spendingDigest(value.reviewed_transaction_hash) && spendingUUID(value.expected_epoch_id) && typeof value.reason === 'string' && value.reason.trim().length > 0 && value.reason.length <= 500 && typeof value.idempotency_key === 'string' && /^[A-Za-z0-9_.:-]{1,160}$/.test(value.idempotency_key));
  return value as CategoryBody;
}
export function spendingCommand(intent: Pick<SpendingIntent, 'user_id' | 'transaction_id' | 'body'>) {
  return { protocol: 'transaction-category-command-v1', user_id: intent.user_id, transaction_id: intent.transaction_id, request: intent.body };
}
export function parseCategoryReview(value: unknown, user: string, transaction: string, raw?: string): CategoryReview {
  check(object(value) && value.protocol === 'transaction-category-review-v1' && value.simulation === true && value.user_id === user && value.transaction_id === transaction && spendingUUID(value.epoch_id) && time(value.as_of) && spendingDigest(value.reviewed_transaction_hash) && typeof value.first_confirmation_supported === 'boolean' && value.grants_authority === false && value.bank_facts_changed === false && object(value.transaction) && object(value.bank_fact));
  assertMoneyFields(value); const tx = value.transaction; const bank = value.bank_fact;
  check(tx.id === transaction && tx.user_id === user && spendingUUID(tx.account_id) && spendingUUID(tx.evidence_id) && tx.direction === 'DEBIT' && integer(tx.amount_cents, 1) && typeof tx.category === 'string' && typeof tx.category_confirmed === 'boolean' && typeof tx.is_one_off === 'boolean' && time(tx.occurred_at) && time(tx.observed_at) && value.first_confirmation_supported === !tx.category_confirmed);
  check(bank.transaction_id === transaction && bank.user_id === user && bank.account_id === tx.account_id && bank.evidence_id === tx.evidence_id && spendingDigest(bank.evidence_hash) && bank.economic_role === 'CONSUMPTION' && bank.direction === 'DEBIT' && bank.evidence_level === 'BANK_CONFIRMED' && bank.evidence_source_type === 'SIMULATED_BANK_TRANSACTION' && bank.evidence_status === 'VALID' && time(bank.evidence_observed_at) && time(bank.evidence_valid_from) && (bank.evidence_valid_to === null || time(bank.evidence_valid_to)));
  for (const key of ['amount_cents', 'balance_after_cents', 'source_ref', 'counterparty_ref']) check(bank[key] === tx[key]);
  for (const key of ['occurred_at', 'observed_at']) check(time(bank[key]) && Date.parse(bank[key]) === Date.parse(tx[key] as string));
  check(Date.parse(tx.observed_at) <= Date.parse(value.as_of) && Date.parse(tx.occurred_at) <= Date.parse(value.as_of));
  return save(value as CategoryReview, raw);
}
export function parseCategoryResult(value: unknown, intent: SpendingIntent, raw?: string): CategoryResult {
  const body = parseCategoryBody(intent.body);
  check(object(value) && value.protocol === 'transaction-category-command-result-v1' && value.simulation === true && value.user_id === intent.user_id && value.transaction_id === intent.transaction_id && value.epoch_id === body.expected_epoch_id && value.idempotency_key === body.idempotency_key && ['RECORDED', 'NOT_FOUND_NOT_FINAL'].includes(value.status as string) && typeof value.replayed_original === 'boolean' && value.grants_authority === false && value.receipt_is_current_authority === false && value.bank_facts_changed === false && value.replacement_allowed === false);
  if (value.status === 'NOT_FOUND_NOT_FINAL') {
    check(['original_command', 'request_hash', 'original_receipt', 'evidence_id', 'audit_event_id', 'audit_event_hash'].every((key) => value[key] === null));
  } else {
    check(object(value.original_command) && exact(value.original_command, ['protocol', 'user_id', 'transaction_id', 'request']) && spendingCanonicalJson(value.original_command) === spendingCanonicalJson(spendingCommand(intent)) && value.request_hash === intent.request_hash && spendingUUID(value.evidence_id) && spendingUUID(value.audit_event_id) && spendingDigest(value.audit_event_hash) && object(value.original_receipt));
    const receipt = value.original_receipt;
    check(exact(receipt, ['transaction_id', 'before_category', 'category', 'before_confirmed', 'confirmed', 'bank_evidence_id', 'bank_evidence_hash', 'confirmed_at']) && receipt.transaction_id === intent.transaction_id && receipt.before_category === intent.before_category && receipt.category === body.category && receipt.before_confirmed === false && receipt.confirmed === true && receipt.bank_evidence_id === intent.bank_evidence_id && receipt.bank_evidence_hash === intent.bank_evidence_hash && time(receipt.confirmed_at));
  }
  return save(value as CategoryResult, raw);
}
function history(value: unknown): void {
  check(object(value) && typeof value.verified === 'boolean' && (value.period_start === null || date(value.period_start)) && (value.period_end === null || date(value.period_end)) && ids(value.account_ids) && ids(value.evidence_ids) && strings(value.reason_codes));
  if (value.verified) check(date(value.period_start) && date(value.period_end) && value.period_start <= value.period_end && value.reason_codes.length === 0);
}
function source(value: unknown): void {
  check(object(value) && typeof value.fact_type === 'string' && spendingUUID(value.fact_id) && (value.account_id === null || spendingUUID(value.account_id)) && typeof value.source_ref === 'string' && spendingUUID(value.evidence_id) && spendingDigest(value.evidence_hash) && typeof value.evidence_source_type === 'string' && typeof value.evidence_source_ref === 'string' && time(value.evidence_observed_at) && object(value.fact)); assertMoneyFields(value);
}
function commonSuggestion(value: unknown, user: string, rule: string): asserts value is Record<string, unknown> {
  check(object(value) && value.simulation === true && value.user_id === user && time(value.as_of) && value.rule_version === rule && value.bank_authority === false && value.hard_protection_changed === false && spendingDigest(value.source_digest) && ids(value.source_evidence_ids) && Array.isArray(value.source_issues));
  history(value.history_proof);
  // The periodic DTO explicitly carries an exact rational mean, not integer money.
  // Exempt only that registered field at its registered pattern location; all other
  // money fields, including raw source facts/configurations, retain safe-integer checks.
  if (rule === 'full-monthly-pattern-variance-v1' && Array.isArray(value.patterns)) assertMoneyFields({ ...value, patterns: value.patterns.map((row) => object(row) ? { ...row, mean_fraction_cents: null } : row) });
  else assertMoneyFields(value);
  value.source_issues.forEach((issue) => check(object(issue) && typeof issue.code === 'string' && typeof issue.message === 'string'));
}
function advice(value: unknown, protection = false): asserts value is Record<string, unknown> & { reason_codes: string[] } {
  check(object(value) && value.advice_only === true && value.requires_confirmation === true && value.bank_authority === false && strings(value.reason_codes));
  if (protection) check(value.hard_protection_changed === false); else check(value.future_obligation_guaranteed === false);
  check(value.candidate_configuration === null ? value.candidate_configuration_hash === null : object(value.candidate_configuration) && spendingDigest(value.candidate_configuration_hash));
  if (value.status !== 'READY') check(value.candidate_configuration === null);
}
export function parsePeriodic(value: unknown, user: string, raw?: string): Periodic {
  commonSuggestion(value, user, 'full-monthly-pattern-variance-v1'); check(date(value.history_start) && date(value.history_end) && value.history_start <= value.history_end && integer(value.excluded_transaction_count) && Array.isArray(value.patterns));
  for (const row of value.patterns) {
    advice(row); check(spendingDigest(row.pattern_id) && spendingUUID(row.account_id) && ['FIXED_TRANSFER', 'RENT', 'CREDIT_CARD_BILL'].includes(row.kind as string) && typeof row.payee_ref === 'string' && ['READY', 'INSUFFICIENT_HISTORY', 'UNSTABLE', 'UNKNOWN'].includes(row.status as string) && integer(row.cycle_count) && integer(row.sample_count, 1) && strings(row.months) && row.months.length === row.sample_count && row.cycle_count <= row.sample_count && integer(row.day_spread) && (row.suggested_due_day === null || integer(row.suggested_due_day, 1) && row.suggested_due_day <= 31) && integer(row.amount_min_cents) && integer(row.amount_max_cents) && row.amount_min_cents <= row.amount_max_cents && typeof row.mean_fraction_cents === 'string' && /^\d+(?:\/[1-9]\d*)?$/.test(row.mean_fraction_cents) && typeof row.variance_fraction_cents_squared === 'string' && Array.isArray(row.sources)); row.sources.forEach(source);
    if (row.status === 'READY') check(object(value.history_proof) && value.history_proof.verified === true && row.reason_codes.length === 0 && row.candidate_configuration !== null && row.suggested_due_day !== null);
  }
  check(new Set(value.patterns.map((row) => row.pattern_id)).size === value.patterns.length);
  return save(value as Periodic, raw);
}
function window(value: unknown): asserts value is Record<string, unknown> {
  check(object(value) && typeof value.window_id === 'string' && integer(value.year, 1) && typeof value.holiday_code === 'string' && date(value.start) && date(value.end) && value.start <= value.end && date(value.notice_date) && typeof value.notice_reference === 'string' && typeof value.source_url === 'string' && /^https:\/\//.test(value.source_url) && value.source_verification === 'OFFICIAL_NOTICE_MANUAL_EXTRACTION');
}
export function parseSeasonal(value: unknown, user: string, selected: string, raw?: string): Seasonal {
  commonSuggestion(value, user, 'full-same-festival-excess-nearest-rank-v1'); check(Array.isArray(value.public_windows) && spendingDigest(value.calendar_extraction_hash) && value.calendar_verified_on === '2026-10-05' && Array.isArray(value.sources)); value.public_windows.forEach(window); value.sources.forEach(source);
  check(new Set(value.public_windows.map((row) => row.window_id)).size === value.public_windows.length);
  const row = value.suggestion; advice(row, true); check(['READY', 'UNKNOWN', 'INSUFFICIENT_HISTORY'].includes(row.status as string) && integer(row.window_count) && integer(row.required_window_count, 2) && Array.isArray(row.comparisons) && typeof row.quantile_fraction === 'string' && (row.rank === null || integer(row.rank, 1)) && (row.cap_limited === null || typeof row.cap_limited === 'boolean'));
  if (row.target !== null) { window(row.target); const target = row.target; check(target.window_id === selected && value.public_windows.some((item) => spendingCanonicalJson(item) === spendingCanonicalJson(target))); }
  else check(row.status !== 'READY' && row.reason_codes.includes('UNSUPPORTED_OR_NOT_YET_PUBLISHED_WINDOW'));
  check(row.effective_window_start === null || date(row.effective_window_start)); check(row.effective_window_end === null || date(row.effective_window_end));
  for (const comparison of row.comparisons) { check(object(comparison)); window(comparison.window); check(['VERIFIED', 'MISSING'].includes(comparison.status as string) && strings(comparison.reason_codes) && ids(comparison.transaction_ids) && date(comparison.baseline_start) && date(comparison.baseline_end)); }
  if (row.status === 'READY') check(object(value.history_proof) && value.history_proof.verified === true && row.target !== null && row.reason_codes.length === 0 && row.candidate_configuration !== null && integer(row.required_adjustment_cents) && integer(row.proposed_adjustment_cents) && row.proposed_adjustment_cents <= row.required_adjustment_cents);
  return save(value as Seasonal, raw);
}
export const getSpendingAccounts = () => request<Accounts>('/accounts/summary', 'GET', undefined, parseOnboardingAccounts);
export function getSpendingTransactions(accounts: Accounts, account: string, offset: number): Promise<Transactions> {
  check(spendingUUID(account) && accounts.accounts.some((row) => row.id === account) && integer(offset) && offset <= 1_000_000);
  return request<Transactions>(`/transactions?limit=50&offset=${offset}&account_id=${account}`, 'GET', undefined, (value, raw) => { const page = parseOnboardingTransactions(value, offset, raw); check(page.items.every((row) => row.account_id === account)); return page; });
}
export const getCategoryReview = (user: string, transaction: string) => { check(spendingUUID(user) && spendingUUID(transaction)); return request<CategoryReview>(`/transactions/${transaction}/category-review`, 'GET', undefined, (value, raw) => parseCategoryReview(value, user, transaction, raw)); };
export const getPeriodicSuggestions = (user: string) => { check(spendingUUID(user)); return request<Periodic>('/policy-suggestions/periodic', 'GET', undefined, (value, raw) => parsePeriodic(value, user, raw)); };
export const getSeasonalSuggestions = (user: string, selected: string) => { check(spendingUUID(user) && /^[A-Za-z0-9_.:-]{1,160}$/.test(selected)); return request<Seasonal>(`/policy-suggestions/seasonal?window_id=${encodeURIComponent(selected)}`, 'GET', undefined, (value, raw) => parseSeasonal(value, user, selected, raw)); };
export const postCategoryConfirmation = (intent: SpendingIntent) => request<CategoryResult>(intent.path, 'POST', intent.body, (value, raw) => parseCategoryResult(value, intent, raw));
export const getCategoryCommand = (intent: SpendingIntent) => request<CategoryResult>(`/transactions/${intent.transaction_id}/category-confirmations/${intent.body.expected_epoch_id}/by-key/${encodeURIComponent(intent.body.idempotency_key)}`, 'GET', undefined, (value, raw) => parseCategoryResult(value, intent, raw));
