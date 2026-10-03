import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterAll, beforeAll, expect, test, vi } from 'vitest';
import App from './App';

// The HTTP fixture is the public API boundary; component internals are not mocked.
const api = createServer((request, response) => {
  if (request.method === 'GET' && request.url === '/api/v1/health') {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({
      status: 'ok', service: 'bounded-funds-api', simulation: true,
    }));
  } else {
    response.writeHead(404);
    response.end();
  }
});

beforeAll(async () => {
  await new Promise<void>((resolve) => api.listen(0, '127.0.0.1', resolve));
});

afterAll(async () => {
  api.closeAllConnections();
  await new Promise<void>((resolve, reject) => api.close((error) => {
    if (error) reject(error);
    else resolve();
  }));
});

test('用户看见模拟说明，并通过健康接口确认 API 已连接', async () => {
  const address = api.address() as AddressInfo;
  vi.stubEnv('VITE_API_BASE_URL', `http://127.0.0.1:${address.port}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);

  expect(screen.getByRole('heading', { name: '钱途有界' })).toBeVisible();
  expect(screen.getByText(/所有资金动作均为模拟/)).toBeVisible();
  expect(await screen.findByText('API 已连接')).toHaveAttribute('role', 'status');
});
