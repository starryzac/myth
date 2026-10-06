import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { snapshot } from '../tests/actual-action-set-fixture';
import ActualActionSetPanel from './ActualActionSetPanel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('shows unknown original denominators, unavailable cents and no financial controls', async () => {
  const data = snapshot();
  data.status = 'UNKNOWN'; data.global_action_set_complete = false; data.action_set_signature = null;
  data.candidates[0]! = { ...data.candidates[0]!, state: 'UNKNOWN', signature: null, amount_cents: null, reasons: ['SOURCE_UNVERIFIED'] };
  data.unsupported_producers = ['FULL_RECOVERY_UNPROVEN'];
  data.table_coverage[0]! = { ...data.table_coverage[0]!, actual_count: 5000, captured_count: 4096, complete: false };
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(data)));
  vi.stubGlobal('fetch', fetcher);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ActualActionSetPanel /></QueryClientProvider>);
  expect(await screen.findByText(/动作集合未知/)).toBeTruthy();
  expect(screen.getByText(/金额未知/)).toBeTruthy();
  expect(screen.getByText('FULL_RECOVERY_UNPROVEN')).toBeTruthy();
  expect(screen.getByText(/实际 5000 · 已捕获 4096/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /执行|确认|支付|扣款/ })).toBeNull();
  expect(fetcher.mock.calls.every((call) => call[1].method === 'GET')).toBe(true);
});
