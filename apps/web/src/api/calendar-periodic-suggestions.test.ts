import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { calendarPeriodicFixture } from '../tests/calendar-periodic-fixture';
import { getCalendarOriginalResponse, parseCalendarParameters, parseCalendarPeriodicReport, readCalendarPeriodicSuggestions } from './calendar-periodic-suggestions';
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { vi.unstubAllGlobals(); });
it('consumes actual-service-shaped complete sources and valid short-month due_day31 only as advice', async () => {
  const report = calendarPeriodicFixture(); const parsed = await parseCalendarPeriodicReport(report, report.parameters, report.user_id);
  expect(parsed.patterns).toHaveLength(9);
  const ready = parsed.patterns.find((p) => p.schedule.cadence === 'MONTH_END' && p.candidate_support === 'AVAILABLE_FOR_USER_REVIEW');
  expect(ready?.schedule.cadence).toBe('MONTH_END'); expect(ready?.candidate_configuration?.due_day).toBe(31);
  expect(ready?.samples).toHaveLength(3); expect(parsed.future_obligation_created).toBe(false);
});
it.each(['writes_performed', 'grants_authority', 'bank_authority', 'hard_protection_changed', 'future_obligation_created', 'audit_chain_verified'])('rejects false completion/authority flag %s', async (flag) => {
  const r = calendarPeriodicFixture(); Object.assign(r, { [flag]: true }); await expect(parseCalendarPeriodicReport(r)).rejects.toThrow();
});
it.each(['sample_count', 'cycle_count', 'sample_money', 'fact_money', 'config_hash', 'source_missing', 'source_known_future', 'candidate_auto', 'unverified', 'foreign_user'])('rejects original/candidate mismatch %s', async (change) => {
  const r = calendarPeriodicFixture(), p = r.patterns.find((p) => p.candidate_support === 'AVAILABLE_FOR_USER_REVIEW')!;
  if (change === 'sample_count') p.sample_count += 1;
  if (change === 'cycle_count') p.cycle_count += 1;
  if (change === 'sample_money') p.samples[0]!.amount_cents += 1;
  if (change === 'fact_money') p.samples[0]!.original.source.fact.amount_cents = 1;
  if (change === 'config_hash') p.candidate_configuration_hash = '0'.repeat(64);
  if (change === 'source_missing') r.source_evidence_ids = [];
  if (change === 'source_known_future') p.samples[0]!.original.source.evidence_observed_at = '2099-01-01T00:00:00Z';
  if (change === 'candidate_auto') p.candidate_configuration!.auto_execute = true;
  if (change === 'unverified') r.history_proof.verified = false;
  if (change === 'foreign_user') r.user_id = '00000000-0000-0000-0000-000000000999';
  await expect(parseCalendarPeriodicReport(r, undefined, calendarPeriodicFixture().user_id)).rejects.toThrow();
});
it('cannot offer monthly confirm configuration for weekly or offset/jitter month-end', async () => {
  const r = calendarPeriodicFixture(), p = r.patterns.find((p) => p.candidate_support === 'AVAILABLE_FOR_USER_REVIEW')!;
  p.schedule.cadence = 'WEEKLY'; p.schedule.due_day = null; p.schedule.weekday = 0; p.schedule.days_before_month_end = null;
  p.schedule.observed_cycle_keys = ['2026-07-27', '2026-08-31', '2026-09-28'];
  await expect(parseCalendarPeriodicReport(r)).rejects.toThrow();
  const next = calendarPeriodicFixture().patterns.find((p) => p.candidate_support === 'AVAILABLE_FOR_USER_REVIEW')!;
  const second = calendarPeriodicFixture(); next.schedule.days_before_month_end = 1; second.patterns[second.patterns.findIndex((p) => p.pattern_id === next.pattern_id)] = next;
  await expect(parseCalendarPeriodicReport(second)).rejects.toThrow();
});
it('retains exact rational means without allowing unsafe integer source/candidate money', async () => {
  const r = calendarPeriodicFixture(); r.patterns[0]!.mean_fraction_cents = '30003/3';
  expect((await parseCalendarPeriodicReport(r)).patterns[0]?.mean_fraction_cents).toBe('30003/3');
  r.patterns[0]!.samples[0]!.original.source.fact.amount_cents = Number.MAX_SAFE_INTEGER + 1;
  await expect(parseCalendarPeriodicReport(r)).rejects.toThrow();
});
it('unknown coverage keeps null candidates/next dates and observed-bill scope', async () => {
  const r = calendarPeriodicFixture(); r.history_proof.verified = false; r.history_proof.reason_codes = ['MISSING_HISTORY_COVERAGE'];
  for (const p of r.patterns) { p.status = 'UNKNOWN'; p.candidate_support = 'NOT_READY'; p.candidate_configuration = null; p.candidate_configuration_hash = null; p.template_name = null; p.schedule.next_occurrence = null; }
  const parsed = await parseCalendarPeriodicReport(r);
  expect(parsed.patterns.every((p) => p.status === 'UNKNOWN')).toBe(true); expect(parsed.patterns.find((p) => p.kind === 'CREDIT_CARD_BILL')?.source_scope).toBe('OBSERVED_BILLS_ONLY');
});
it.each([{ lookback_days: 366 }, { minimum_cycles: 2 }, { maximum_day_spread: 3 }, { maximum_cv_bps: 1001 }, { now: '2099' }, { lookback_days: true }])('rejects invalid/client-fact discovery option %j', (change) => {
  expect(() => parseCalendarParameters({ ...calendarPeriodicFixture().parameters, ...change })).toThrow();
});
it('actual reader sends only GET bounded options and retains exact HTTP text', async () => {
  const fixture = calendarPeriodicFixture(), raw = JSON.stringify(fixture, null, 2);
  const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(raw, { status: 200 })); vi.stubGlobal('fetch', fetch);
  const parsed = await readCalendarPeriodicSuggestions(fixture.parameters, fixture.user_id);
  expect(getCalendarOriginalResponse(parsed)).toBe(raw); expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0]?.[0]).toContain('/policy-suggestions/calendar-periodic?lookback_days=95');
  expect(fetch.mock.calls[0]?.[1]).toMatchObject({ method: 'GET' }); expect(fetch.mock.calls[0]?.[1]?.body).toBeUndefined();
});
it('changed server parameters and malformed HTTP cannot become a usable candidate', async () => {
  const r = calendarPeriodicFixture(); await expect(parseCalendarPeriodicReport(r, { ...r.parameters, minimum_cycles: 4 })).rejects.toThrow();
  vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async () => new Response('not json', { status: 200 })));
  await expect(readCalendarPeriodicSuggestions(r.parameters)).rejects.toThrow();
});
