import { webcrypto } from 'node:crypto';
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { recoveryComposedFixture } from '../tests/recovery-composed-action-set-fixture';
import { observationFixture, observationIntentFixture } from '../tests/recovery-composed-observation-fixture';

beforeEach(() => { vi.resetModules(); localStorage.clear(); vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
test('explicit record gets owner/current scope, stores full request before POST and checks exact GET', async () => {
  const intentFixture = await observationIntentFixture(); const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/current')) return new Response(JSON.stringify(recoveryComposedFixture('complete')));
    const state = JSON.parse(localStorage.getItem('bounded-funds-recovery-composed-observation-v1:same-origin')!); const intent = state.pending ?? intentFixture;
    if (options?.method === 'POST') expect(options.body).toBe(intent.body_json);
    return new Response(JSON.stringify(await observationFixture(intent)));
  }); vi.stubGlobal('fetch', fetcher);
  const Panel = (await import('./RecoveryComposedObservationPanel')).default;
  render(<Panel />); expect(fetcher).not.toHaveBeenCalled(); const button = screen.getByRole('button', { name: '记录并核对当前组合观察' }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  await screen.findByText('原观察已核对'); expect(fetcher.mock.calls.map(call => [call[0], call[1]?.method])).toEqual([['/api/v1/boundary/recovery-composed-action-set/current', 'GET'], ['/api/v1/boundary/recovery-composed-action-set/observe', 'POST'], [expect.stringContaining('/observations/'), 'GET']]);
});
test('lost POST keeps identity; original GET recovery remains enabled under other write gate', async () => {
  const intent = await observationIntentFixture(null, '1', 't1_unknown'); const store = await import('../features/recovery-composed-observation-operation'); await store.beginRecoveryComposedObservation(intent, false); store.endRecoveryComposedObservationAttempt();
  const fetcher = vi.fn(async () => new Response(JSON.stringify(await observationFixture(intent, 't1_unknown')))); vi.stubGlobal('fetch', fetcher);
  const Panel = (await import('./RecoveryComposedObservationPanel')).RecoveryComposedObservationOriginalRecoveryPanel;
  render(<Panel mutationBlocked />); fireEvent.click(screen.getByRole('button', { name: '核对原观察' })); await screen.findByText('原观察已记录，来源比较仍未知');
  expect(fetcher.mock.calls).toHaveLength(1); expect(store.getRecoveryComposedObservationOperation().pending).toBeNull();
});
test('only explicit same-key retry after actual404 can POST; no new current scope/key is read', async () => {
  const intent = await observationIntentFixture(); const store = await import('../features/recovery-composed-observation-operation'); await store.beginRecoveryComposedObservation(intent, false); store.endRecoveryComposedObservationAttempt();
  let missing = true; const fetcher = vi.fn(async (_url: string, options?: RequestInit) => { if (options?.method === 'POST') { expect(options.body).toBe(intent.body_json); missing = false; } return new Response(JSON.stringify(missing ? { error: { code: 'NOT_FOUND', message: '没有原记录' } } : await observationFixture(intent)), { status: missing ? 404 : 200 }); }); vi.stubGlobal('fetch', fetcher);
  const Panel = (await import('./RecoveryComposedObservationPanel')).RecoveryComposedObservationOriginalRecoveryPanel;
  render(<Panel />); fireEvent.click(screen.getByRole('button', { name: '先核对，再同键重试原观察' })); await screen.findByText('原观察已核对'); expect(fetcher.mock.calls.map(call => call[1]?.method)).toEqual(['GET', 'POST', 'GET']); expect(fetcher.mock.calls.every(call => !call[0].endsWith('/current'))).toBe(true);
});
test('non404 source failure keeps pending and never POSTs', async () => {
  const intent = await observationIntentFixture(); const store = await import('../features/recovery-composed-observation-operation'); await store.beginRecoveryComposedObservation(intent, false); store.endRecoveryComposedObservationAttempt();
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ error: { message: '来源未取得', code: 'UNAVAILABLE' } }), { status: 503 })); vi.stubGlobal('fetch', fetcher);
  const Panel = (await import('./RecoveryComposedObservationPanel')).RecoveryComposedObservationOriginalRecoveryPanel;
  render(<Panel />); fireEvent.click(screen.getByRole('button', { name: '先核对，再同键重试原观察' })); await screen.findByRole('alert'); expect(fetcher.mock.calls).toHaveLength(1); expect(store.getRecoveryComposedObservationOperation().pending?.expected_run_id).toBe(intent.expected_run_id);
});
test('another family gate arriving during current GET prevents POST and new pending', async () => {
  let release!: (response: Response) => void;
  const fetcher = vi.fn(async () => await new Promise<Response>(resolve => { release = resolve; })); vi.stubGlobal('fetch', fetcher);
  const Panel = (await import('./RecoveryComposedObservationPanel')).default;
  const view = render(<Panel />); const button = screen.getByRole('button', { name: '记录并核对当前组合观察' }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce()); view.rerender(<Panel mutationBlocked />); release(new Response(JSON.stringify(recoveryComposedFixture('complete'))));
  await screen.findByRole('alert'); expect(fetcher).toHaveBeenCalledOnce(); const store = await import('../features/recovery-composed-observation-operation'); expect(store.getRecoveryComposedObservationOperation().pending).toBeNull();
});
