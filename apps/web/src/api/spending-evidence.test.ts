import { expect, test, vi } from 'vitest';
import { getSeasonalSuggestions, getSpendingTransactions, parseCategoryBody, parseCategoryResult, parseCategoryReview, parsePeriodic, parseSeasonal } from './spending-evidence';
import { categoryBodyFixture, categoryResultFixture, categoryReviewFixture, periodicFixture, seasonalFixture, spendingAccountsFixture, spendingIntentFixture, spendingTransaction, spendingUser, spendingTransactionsFixture } from '../tests/spending-evidence-fixture';
test('消费原件与建议只读约束严格，未知不补金额，已确认只能只读', () => {
  expect(parseCategoryReview(categoryReviewFixture(true), spendingUser, spendingTransaction).first_confirmation_supported).toBe(false);
  expect(parsePeriodic(periodicFixture(), spendingUser).patterns[0]!.status).toBe('UNKNOWN'); expect(parsePeriodic(periodicFixture(true), spendingUser).patterns[0]!.status).toBe('READY');
  expect(parseSeasonal(seasonalFixture(), spendingUser, 'CN-2026-NATIONAL_DAY').suggestion.proposed_adjustment_cents).toBeNull();
  expect(parseSeasonal(seasonalFixture('USER_UNKNOWN_WINDOW'), spendingUser, 'USER_UNKNOWN_WINDOW').suggestion.target).toBeNull();
});
test('周期精确有理数均值保持原字符串，仅此注册字段豁免整数检查', () => {
  const value = periodicFixture(true); value.patterns[0]!.mean_fraction_cents = '200007/2'; expect(parsePeriodic(value, spendingUser).patterns[0]!.mean_fraction_cents).toBe('200007/2');
  value.patterns[0]!.mean_fraction_cents = 'NaN'; expect(() => parsePeriodic(value, spendingUser)).toThrow(); value.patterns[0]!.mean_fraction_cents = '1'; value.patterns[0]!.amount_min_cents = Number('9223372036854775807'); expect(() => parsePeriodic(value, spendingUser)).toThrow();
});
test.each(['accepted', 'extra', 'epoch', 'money', 'role', 'bank', 'owner', 'first'] as const)('真实分类 %s 错误不能转为可写', (field) => {
  if (field === 'accepted' || field === 'extra') { const body: Record<string, unknown> = categoryBodyFixture(); if (field === 'accepted') body.accepted = 'true'; else body.amount_cents = 0; expect(() => parseCategoryBody(body)).toThrow(); return; }
  const review = categoryReviewFixture(); if (field === 'epoch') review.epoch_id = 'INVALID'; if (field === 'money') review.transaction.amount_cents = Number('9223372036854775807'); if (field === 'role') review.bank_fact.economic_role = 'INTERNAL_TRANSFER'; if (field === 'bank') review.bank_fact.amount_cents = 2; if (field === 'owner') review.user_id = review.epoch_id; if (field === 'first') review.first_confirmation_supported = false;
  expect(() => parseCategoryReview(review, spendingUser, spendingTransaction)).toThrow();
});
test.each(['body', 'receipt', 'bank', 'hash', 'grant', 'epoch', 'event', 'notfound'] as const)('回执 %s 漂移拒绝，不能借新latest清原键', (field) => {
  const intent = spendingIntentFixture(); const value = categoryResultFixture(intent);
  if (field === 'body') (value.original_command!.request as Record<string, unknown>).reason = 'OTHER'; if (field === 'receipt') value.original_receipt!.category = 'other'; if (field === 'bank') value.original_receipt!.bank_evidence_hash = '0'.repeat(64); if (field === 'hash') value.request_hash = '0'.repeat(64); if (field === 'grant') (value as unknown as Record<string, unknown>).grants_authority = true; if (field === 'epoch') value.epoch_id = value.user_id; if (field === 'event') value.audit_event_id = null; if (field === 'notfound') value.status = 'NOT_FOUND_NOT_FINAL';
  expect(() => parseCategoryResult(value, intent)).toThrow();
});
test('建议不可冒充权限、READY覆盖、不同窗口或不精确金额；请求仅用户窗口ID', async () => {
  const bad = periodicFixture(true); bad.history_proof.verified = false; expect(() => parsePeriodic(bad, spendingUser)).toThrow();
  const seasonal = seasonalFixture(); expect(() => parseSeasonal(seasonal, spendingUser, 'OTHER')).toThrow();
  (seasonal as unknown as Record<string, unknown>).bank_authority = true; expect(() => parseSeasonal(seasonal, spendingUser, 'CN-2026-NATIONAL_DAY')).toThrow();
  vi.stubEnv('VITE_API_BASE_URL', 'http://spending-reader-unit.local'); const actual: URL[] = []; vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => { expect(options?.method).toBe('GET'); expect(options?.body).toBeUndefined(); const url = new URL(String(input)); actual.push(url); return new Response(JSON.stringify(seasonalFixture())); }));
  await getSeasonalSuggestions(spendingUser, 'CN-2026-NATIONAL_DAY'); expect([...actual[0]!.searchParams.keys()]).toEqual(['window_id']);
});
test('账户筛选只接受实际本人账户且逐行核账户，分页分母不改', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://spending-reader-unit.local'); const page = spendingTransactionsFixture(); page.items[0]!.account_id = spendingUser;
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(page)))); await expect(getSpendingTransactions(spendingAccountsFixture(), spendingAccountsFixture().accounts[0]!.id, 0)).rejects.toThrow();
  expect(() => getSpendingTransactions(spendingAccountsFixture(), spendingUser, 0)).toThrow();
});
