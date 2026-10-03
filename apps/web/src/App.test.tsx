import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import App from './App';

type ApiMode = 'healthy' | 'disconnected' | 'not-simulation';
let apiMode: ApiMode;
let client: QueryClient;

// The HTTP fixture is the public API boundary; component internals are not mocked.
const api = createServer((request, response) => {
  if (request.method === 'GET' && request.url === '/api/v1/health') {
    if (apiMode === 'disconnected') {
      request.socket.destroy();
      return;
    }
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({
      status: 'ok', service: 'bounded-funds-api', simulation: apiMode !== 'not-simulation',
    }));
  } else {
    response.writeHead(404);
    response.end();
  }
});

beforeEach(async () => {
  apiMode = 'healthy';
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  await new Promise<void>((resolve) => api.listen(0, '127.0.0.1', resolve));
  const address = api.address() as AddressInfo;
  vi.stubEnv('VITE_API_BASE_URL', `http://127.0.0.1:${address.port}`);
});

afterEach(async () => {
  client.clear();
  api.closeAllConnections();
  await new Promise<void>((resolve, reject) => api.close((error) => {
    if (error) reject(error);
    else resolve();
  }));
});

function openApp() {
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

test('用户看见模拟说明，并通过健康接口确认 API 已连接', async () => {
  openApp();

  expect(screen.getByRole('heading', { name: '钱途有界' })).toBeVisible();
  expect(screen.getByText(/所有资金动作均为模拟/)).toBeVisible();
  expect(await screen.findByText('API 已连接')).toHaveAttribute('role', 'status');
});

test('健康 API 断开连接时用户看见错误状态和重试入口', async () => {
  apiMode = 'disconnected';
  openApp();

  expect(await screen.findByText('API 暂未连接')).toHaveAttribute('role', 'status');
  expect(screen.getByRole('button', { name: '重新连接' })).toBeEnabled();
  expect(screen.queryByText('API 已连接')).not.toBeInTheDocument();
});

test('API 恢复后用户可点击重试并看见已连接状态', async () => {
  apiMode = 'disconnected';
  openApp();
  const retry = await screen.findByRole('button', { name: '重新连接' });

  apiMode = 'healthy';
  fireEvent.click(retry);

  expect(await screen.findByText('API 已连接')).toHaveAttribute('role', 'status');
  expect(screen.queryByText('API 暂未连接')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '重新连接' })).not.toBeInTheDocument();
});

test('HTTP 成功但 simulation 为 false 时不宣称已连接模拟 API', async () => {
  apiMode = 'not-simulation';
  openApp();

  expect(await screen.findByText('API 暂未连接')).toHaveAttribute('role', 'status');
  expect(screen.queryByText('API 已连接')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '重新连接' })).toBeEnabled();
});
