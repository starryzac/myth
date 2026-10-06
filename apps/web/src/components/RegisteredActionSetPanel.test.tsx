/** Local GET doubles and synthetic pure domain outputs only, no browser/PG acceptance. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { installHttpFixture } from '../tests/policy-fixture';
import { registeredFixture } from '../tests/registered-action-set-fixture';
import RegisteredActionSetPanel from './RegisteredActionSetPanel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const renderPanel = (client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })) => render(<QueryClientProvider client={client}><RegisteredActionSetPanel /></QueryClientProvider>);
test('manual GET exposes all family denominators, missing sources and preserved v4/raw hashes without writes', async () => {
  vi.stubGlobal('crypto', webcrypto); const value = registeredFixture('joint_missing');
  const calls = installHttpFixture((method, path) => { expect(method).toBe('GET'); expect(path).toBe('/api/v1/boundary/registered-action-set/current'); return value; });
  renderPanel(); expect(calls).toHaveLength(0); fireEvent.click(screen.getByRole('button', { name: '刷新当前登记动作集合' }));
  await screen.findByText('当前动作集合未知：未覆盖分母完整保留');
  expect(screen.getByText('付款家族')).toBeInTheDocument(); expect(screen.getByText('恢复家族')).toBeInTheDocument(); expect(screen.getByText('归属释放家族')).toBeInTheDocument(); expect(screen.getByText('目标联动家族')).toBeInTheDocument();
  expect(screen.getByText(/候选分母 2 · 已返回 2/)).toBeInTheDocument();
  expect(screen.getAllByText('SYNTHETIC_TOOL_ONLY_MISSING_JOINT_SOURCE').length).toBeGreaterThan(0);
  expect(screen.getAllByText(value.original_recovery_composed_snapshot.snapshot_hash).length).toBeGreaterThan(0);
  expect(screen.getByText(/没有金融写入或观察、通知、确认操作/)).toBeInTheDocument(); expect(calls).toHaveLength(1);
});
test('failed refresh removes prior complete conclusion and does not reuse it as current authority', async () => {
  vi.stubGlobal('crypto', webcrypto); let count = 0;
  const calls = installHttpFixture(method => { expect(method).toBe('GET'); return ++count === 1 ? registeredFixture() : new Response(JSON.stringify({ error: { code: 'SOURCE_UNVERIFIED', message: '当前原件缺失', request_id: 'TOOL_ONLY' } }), { status: 409 }); });
  renderPanel(); const button = screen.getByRole('button', { name: '刷新当前登记动作集合' }); fireEvent.click(button);
  await screen.findByText('当前登记范围完整：只读核对，不授执行权限'); fireEvent.click(button);
  await screen.findByRole('alert'); expect(screen.queryByText('当前登记范围完整：只读核对，不授执行权限')).not.toBeInTheDocument(); expect(calls).toHaveLength(2);
});
test('previous query cache is hidden until an explicit current GET completes', async () => {
  vi.stubGlobal('crypto', webcrypto); const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  client.setQueryData(['registered-current-action-set-v5'], registeredFixture());
  const calls = installHttpFixture(method => { expect(method).toBe('GET'); return registeredFixture('release_missing'); });
  renderPanel(client); expect(calls).toHaveLength(0); expect(screen.queryByText('当前登记范围完整：只读核对，不授执行权限')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '刷新当前登记动作集合' }));
  await screen.findByText('当前动作集合未知：未覆盖分母完整保留');
  await waitFor(() => expect(calls).toHaveLength(1)); expect(screen.queryByText('当前登记范围完整：只读核对，不授执行权限')).not.toBeInTheDocument();
});
