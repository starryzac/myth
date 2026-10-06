/** Local HTTP doubles only, no real database or browser acceptance. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { installHttpFixture } from '../tests/policy-fixture';
import { recoveryComposedFixture } from '../tests/recovery-composed-action-set-fixture';
import RecoveryComposedActionSetPanel from './RecoveryComposedActionSetPanel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const renderPanel = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><RecoveryComposedActionSetPanel /></QueryClientProvider>);
test('opt-in exact GET displays recovery ASK and retained original unknown', async () => {
  vi.stubGlobal('crypto', webcrypto); const calls = installHttpFixture((method, path) => { expect(method).toBe('GET'); expect(path).toBe('/api/v1/boundary/recovery-composed-action-set/current'); return recoveryComposedFixture(); });
  renderPanel(); expect(calls).toHaveLength(0); fireEvent.click(screen.getByRole('button', { name: '核对付款、整仓恢复与当前动作' }));
  await screen.findByText('当前动作仍未知：全部未覆盖来源保留'); expect(calls).toHaveLength(1); expect(screen.getByText(/full-recovery:.*¥500.00.*ASK_ONCE/)).toBeInTheDocument();
});
test('failed refresh hides earlier complete conclusion and sends no money request', async () => {
  vi.stubGlobal('crypto', webcrypto); let count = 0;
  const calls = installHttpFixture((method, path) => { expect(method).toBe('GET'); expect(path).toBe('/api/v1/boundary/recovery-composed-action-set/current'); count++; return count === 1 ? recoveryComposedFixture('complete') : new Response(JSON.stringify({ error: { code: 'SOURCE_UNVERIFIED', message: '当前原件缺失', request_id: 'synthetic-only' } }), { status: 409 }); });
  renderPanel(); const button = screen.getByRole('button', { name: '核对付款、整仓恢复与当前动作' }); fireEvent.click(button); await screen.findByText('当前登记来源已完整核对'); fireEvent.click(button);
  await screen.findByRole('alert'); expect(screen.queryByText('当前登记来源已完整核对')).not.toBeInTheDocument(); expect(calls).toHaveLength(2);
});
