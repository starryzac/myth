import { webcrypto } from 'node:crypto';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import CalendarPeriodicSuggestionsPanel from './CalendarPeriodicSuggestionsPanel';
import { calendarPeriodicFixture } from '../tests/calendar-periodic-fixture';
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { vi.unstubAllGlobals(); });
async function read(mutationBlocked = false, onReviewCandidate = vi.fn()) {
  const r = calendarPeriodicFixture(); const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(r), { status: 200 })); vi.stubGlobal('fetch', fetch);
  render(<CalendarPeriodicSuggestionsPanel userId={r.user_id} mutationBlocked={mutationBlocked} onReviewCandidate={onReviewCandidate} />);
  fireEvent.change(screen.getByLabelText('回看天数'), { target: { value: '95' } });
  fireEvent.click(screen.getByRole('button', { name: '读取并比较周期' }));
  await waitFor(() => expect(screen.getAllByRole('button', { name: /复核.*候选/ })).toHaveLength(2));
  return { r, fetch, onReviewCandidate };
}
it('explicitly reads actual candidates/all source denominator without POST/obligation/financial completion', async () => {
  const { fetch } = await read();
  expect(screen.getByText(/9 项；普通/)).toBeInTheDocument(); expect(screen.getAllByText(/我发现一个可能需要持续预留的规律/)).toHaveLength(2);
  expect(screen.getAllByText(/完整审计和独立经济验真未在此证明/)).toHaveLength(1);
  expect(fetch).toHaveBeenCalledTimes(1); expect(fetch.mock.calls.every((call) => call[1]?.method === 'GET')).toBe(true);
});
it('review is only an explicit draft handoff carrying exact original owner/epoch/config/source', async () => {
  const { r, fetch, onReviewCandidate } = await read(); fireEvent.click(screen.getByRole('button', { name: '复核每月固定日期附近候选' }));
  expect(onReviewCandidate).toHaveBeenCalledWith(expect.objectContaining({ user_id: r.user_id, epoch_id: r.epoch_id, source_digest: r.source_digest, pattern: expect.objectContaining({ candidate_configuration_hash: r.patterns.find((p) => p.candidate_configuration !== null)?.candidate_configuration_hash }) }));
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('other original requests block draft handoff while its own history GET remains reachable', async () => {
  const { onReviewCandidate, fetch } = await read(true); const button = screen.getByRole('button', { name: '复核每月固定日期附近候选' }); expect(button).toBeDisabled(); fireEvent.click(button);
  expect(onReviewCandidate).not.toHaveBeenCalled(); expect(fetch).toHaveBeenCalledTimes(1);
});
it('changing parameters removes the reviewed candidate and never submits or reuses stale result', async () => {
  const { fetch } = await read(); fireEvent.change(screen.getByLabelText('最少连续周期'), { target: { value: '4' } });
  expect(screen.queryAllByRole('button', { name: /复核.*候选/ })).toHaveLength(0); expect(fetch).toHaveBeenCalledTimes(1);
});
it('UNKNOWN original coverage remains visible and has no candidate handoff', async () => {
  const r = calendarPeriodicFixture(); r.history_proof.verified = false; r.history_proof.reason_codes = ['MISSING_HISTORY_COVERAGE'];
  for (const p of r.patterns) { p.status = 'UNKNOWN'; p.candidate_support = 'NOT_READY'; p.candidate_configuration = null; p.candidate_configuration_hash = null; p.template_name = null; p.schedule.next_occurrence = null; }
  vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(r), { status: 200 })));
  render(<CalendarPeriodicSuggestionsPanel />); fireEvent.change(screen.getByLabelText('回看天数'), { target: { value: '95' } }); fireEvent.click(screen.getByRole('button', { name: '读取并比较周期' }));
  await waitFor(() => expect(screen.getByText(/历史 .*覆盖 UNKNOWN/)).toBeInTheDocument()); expect(screen.queryAllByRole('button', { name: /复核.*候选/ })).toHaveLength(0);
});
