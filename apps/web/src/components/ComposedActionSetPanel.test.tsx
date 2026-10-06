import { createServer } from 'node:http';
import { webcrypto } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { composedFixture } from '../tests/composed-action-set-fixture';
import ComposedActionSetPanel from './ComposedActionSetPanel';

let failed: boolean; let calls: string[]; let query: QueryClient;
const server = createServer((request, response) => {
  calls.push(`${request.method} ${request.url}`);
  response.writeHead(failed ? 503 : 200, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(failed ? { error: { message: '当前原件读取失败', code: 'SOURCE_UNAVAILABLE' } } : composedFixture()));
});
beforeEach(async () => {
  failed = false; calls = []; vi.stubGlobal('crypto', webcrypto);
  query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  vi.stubEnv('VITE_API_BASE_URL', `http://127.0.0.1:${(server.address() as AddressInfo).port}`);
});
afterEach(async () => { query.clear(); server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve())); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('only an explicit request reads current composition once; there is no payment or notification POST', async () => {
  render(<QueryClientProvider client={query}><ComposedActionSetPanel /></QueryClientProvider>);
  expect(calls).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: '核对周期划款与当前动作' }));
  await screen.findByText('当前登记来源已完整核对');
  expect(screen.getByText(/本期范围：完整/)).toHaveTextContent('同笔替代 1 笔');
  expect(screen.getByText(/¥3.00/)).toHaveTextContent('AUTO_EXECUTE');
  expect(calls).toEqual(['GET /api/v1/boundary/composed-action-set/current']);
});
test('failed refresh hides the previous complete claim and preserves a read-only retry', async () => {
  render(<QueryClientProvider client={query}><ComposedActionSetPanel /></QueryClientProvider>);
  const button = screen.getByRole('button', { name: '核对周期划款与当前动作' });
  fireEvent.click(button); await screen.findByText('当前登记来源已完整核对');
  failed = true; fireEvent.click(button); await screen.findByRole('alert');
  expect(screen.queryByText('当前登记来源已完整核对')).not.toBeInTheDocument();
  await waitFor(() => expect(button).toBeEnabled());
  expect(calls.every((call) => call === 'GET /api/v1/boundary/composed-action-set/current')).toBe(true);
});
