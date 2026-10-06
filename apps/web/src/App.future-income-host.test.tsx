/** HTTP doubles prove original GET and cross-family host gates, not bank/PG acceptance. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { futureCandidateBody, futureLookupFixture, futureUser } from './tests/future-income-planning-fixture';
import { presetsFixture, stateFixture } from './tests/demo-fixture';
import { installHttpFixture } from './tests/policy-fixture';
let App: typeof import('./App').default;
let store: typeof import('./features/future-income-operation');
let api: typeof import('./api/future-income-planning');
beforeEach(async () => {
  vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); window.history.replaceState(null, '', '/#policies');
  App = (await import('./App')).default; store = await import('./features/future-income-operation'); api = await import('./api/future-income-planning');
});
function open() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><App /></QueryClientProvider>); }
function demo() { window.location.hash = '#demo'; window.dispatchEvent(new HashChangeEvent('hashchange')); }

test('声明响应未知及列表失败仍可跨页GET原键，原body/hash保留并阻止reset', async () => {
  const intent = await store.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody());
  const calls = installHttpFixture((_method, path) => {
    if (path.includes('/planning/future-income/commands/')) return futureLookupFixture('CANDIDATE', intent.body, undefined, true);
    if (path === '/api/v1/demo/presets') return presetsFixture();
    if (path === '/api/v1/demo/state') return stateFixture();
    return new Response('{}', { status: 503 });
  });
  await store.beginFutureIncomeOperation(intent); store.endFutureIncomeAttempt(); open();
  const read = await screen.findByRole('button', { name: '只读核对原未来收入声明' }); await waitFor(() => expect(read).toBeEnabled()); fireEvent.click(read);
  await screen.findByText('原结果非终局，保留body/key。'); expect(store.getFutureIncomeOperation().pending).toEqual(intent);
  demo(); await screen.findByRole('heading', { name: '演示控制台' }); expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  expect(calls.every(call => call.method === 'GET' && call.body === undefined)).toBe(true);
  expect(calls.filter(call => call.path.includes('/planning/future-income/commands/'))).toHaveLength(1);
});

test('候选原GET已匹配仍保留复核门，明确关闭本地工作区后解除而不发任何写入', async () => {
  const intent = await store.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody());
  const calls = installHttpFixture((_method, path) => {
    if (path.includes('/planning/future-income/commands/')) return futureLookupFixture('CANDIDATE', intent.body);
    if (path === '/api/v1/demo/presets') return presetsFixture();
    if (path === '/api/v1/demo/state') return stateFixture();
    return new Response('{}', { status: 503 });
  });
  await store.beginFutureIncomeOperation(intent); store.endFutureIncomeAttempt();
  await store.acceptFutureIncomeRead(intent, await api.lookupFutureIncomeCommand(intent.user_id, intent.epoch_id, intent.body.idempotency_key));
  expect(store.getFutureIncomeOperation().pending).toBeNull(); open();
  await screen.findByText(/未来收入原声明或复核工作区尚未核对/);
  const read = screen.getByRole('button', { name: '只读核对原未来收入声明' }); await waitFor(() => expect(read).toBeEnabled()); fireEvent.click(read);
  await screen.findByText('原条件声明已只读刷新，不代表未来收入已入账。');
  demo(); await screen.findByRole('heading', { name: '演示控制台' }); const reset = await screen.findByRole('button', { name: '恢复演示初始状态' }); expect(reset).toBeDisabled();
  store.discardFutureIncomeReview(); await waitFor(() => expect(reset).toBeEnabled());
  expect(calls.every(call => call.method === 'GET' && call.body === undefined)).toBe(true);
  expect(calls.filter(call => call.path.includes('/planning/future-income/commands/'))).toHaveLength(2);
});
