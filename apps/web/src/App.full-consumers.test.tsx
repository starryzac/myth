/** SYNTHETIC_HTTP_ONLY: parent gates and exact original reads, no bank acceptance. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import App from './App';
import { installHttpFixture } from './tests/policy-fixture';
import { dashboardFixture } from './tests/dashboard-fixture';
import { presetsFixture, stateFixture } from './tests/demo-fixture';
import { assetLookupFixture, assetPrepareFixture, assetUser } from './tests/full-asset-execution-fixture';
import { lookupAssetExecution } from './api/full-asset-execution';
import { acceptFullAssetExecutionRead, beginFullAssetExecutionOperation, endFullAssetExecutionAttempt, getFullAssetExecutionOperation, prepareAssetIntent } from './features/full-asset-execution-operation';
import { paymentIntentFixture, paymentLookupFixture } from './tests/fixed-payment-fixture';
import { lookupPaymentIntent } from './api/full-payment-relations';
import { acceptFixedPaymentRead, beginFixedPaymentOperation, endFixedPaymentAttempt, getFixedPaymentOperation } from './features/fixed-payment-operation';

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); window.history.replaceState(null, '', '/#policies'); sessionStorage.clear(); });
afterEach(async () => {
  cleanup(); endFullAssetExecutionAttempt();
  const pending = getFullAssetExecutionOperation().pending;
  if (pending) {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(assetLookupFixture(pending)))));
    await acceptFullAssetExecutionRead(pending, await lookupAssetExecution(pending));
  }
  endFixedPaymentAttempt(); const payment = getFixedPaymentOperation().pending;
  if (payment) {
    const lookup = await paymentLookupFixture(payment);
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(lookup))));
    await acceptFixedPaymentRead(payment, await lookupPaymentIntent(payment));
  }
  sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs();
});

test('固定付款原件在Full列表和当前身份失败时仍可独立GET，跨族阻挡与NOT_FOUND保持', async () => {
  const original = await paymentIntentFixture();
  const unknown = await paymentLookupFixture(original, false), recorded = await paymentLookupFixture(original);
  let found = false;
  const calls = installHttpFixture((_method, path) => {
    if (path.includes('/full-payment-relations/commands/')) return found ? recorded : unknown;
    if (path.endsWith('/full-policies') || path.endsWith('/local-actor/session')) return new Response('{}', { status: 503 });
    if (path.endsWith('/demo/state')) return stateFixture();
    if (path.endsWith('/demo/presets')) return presetsFixture();
    return { simulation: true, items: [] };
  });
  await beginFixedPaymentOperation(original); endFixedPaymentAttempt(); mount();
  expect(await screen.findByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  navigate('#products'); expect(await screen.findByRole('button', { name: '只读加载现有资产权限与周期' })).toBeEnabled();
  expect(screen.getByText(/固定付款原请求正在处理或待核对/)).toBeVisible();
  navigate('#demo'); expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  navigate('#policies');
  const region = await screen.findByRole('region', { name: '固定付款独立原件恢复' });
  const read = within(region).getByRole('button', { name: '独立只读核对固定付款原件' }); expect(read).toBeEnabled();
  fireEvent.click(read); await waitFor(() => expect(within(region).getByText(/原键未终局/)).toBeVisible());
  expect(getFixedPaymentOperation().pending?.body_json).toBe(original.body_json);
  found = true; fireEvent.click(read); await waitFor(() => expect(getFixedPaymentOperation().pending).toBeNull());
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
  expect(await screen.findByRole('button', { name: '准备新完整版策略' })).toBeEnabled();
});
function mount() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><App /></QueryClientProvider>); }
function navigate(hash: string) { window.history.replaceState(null, '', `/${hash}`); fireEvent(window, new Event('hashchange')); }

test('资产原请求跨页阻挡新策略和重置，列表失败不阻原GET，NOT_FOUND不放行', async () => {
  const original = await prepareAssetIntent('PREPARE', assetUser, assetPrepareFixture());
  let found = false;
  const calls = installHttpFixture((_method, path) => {
    if (path.includes('/full-asset-executions/commands/')) return assetLookupFixture(original, found);
    if (path.endsWith('/demo/state')) return stateFixture();
    if (path.endsWith('/demo/presets')) return presetsFixture();
    if (path.endsWith('/dashboard')) return dashboardFixture();
    return { simulation: true, items: [] };
  });
  await beginFullAssetExecutionOperation(original); endFullAssetExecutionAttempt(); mount();
  expect(await screen.findByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  expect(screen.getByText(/原资产组合或固定批次正在处理或待核对/)).toBeVisible();
  navigate('#demo'); expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  navigate('#products');
  const pending = await screen.findByRole('region', { name: '待核对原资产请求' });
  const read = within(pending).getByRole('button', { name: '独立读取原资产请求结果' }); expect(read).toBeEnabled();
  fireEvent.click(read); await waitFor(() => expect(calls.filter((call) => call.path.includes('/full-asset-executions/commands/'))).toHaveLength(1));
  await waitFor(() => expect(getFullAssetExecutionOperation().busy).toBe(false));
  expect(getFullAssetExecutionOperation().pending?.body_json).toBe(original.body_json);
  expect(within(pending).getByRole('checkbox', { name: /我明确恢复同一完整原请求/ })).toBeEnabled();
  found = true; fireEvent.click(read); await waitFor(() => expect(getFullAssetExecutionOperation().pending).toBeNull());
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
  navigate('#policies'); expect(await screen.findByRole('button', { name: '准备新完整版策略' })).toBeEnabled();
});
