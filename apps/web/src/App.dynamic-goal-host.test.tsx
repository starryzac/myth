/** HTTP doubles prove host gates and original GET, not PostgreSQL or bank settlement. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { dynamicExecutionUser, dynamicPrepareFixture, dynamicLookupFixture } from './tests/full-dynamic-goal-execution-fixture';
import { presetsFixture, stateFixture } from './tests/demo-fixture';
import { installHttpFixture } from './tests/policy-fixture';
let App: typeof import('./App').default;
let store: typeof import('./features/full-dynamic-goal-operation');
let api: typeof import('./api/full-dynamic-goal-execution');
beforeEach(async () => {
  vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); window.history.replaceState(null, '', '/#goals');
  App = (await import('./App')).default; store = await import('./features/full-dynamic-goal-operation'); api = await import('./api/full-dynamic-goal-execution');
});
function open() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><App /></QueryClientProvider>); }
function demo() { window.location.hash = '#demo'; window.dispatchEvent(new HashChangeEvent('hashchange')); }

test('pending原动态准备在当前列表失败时仍独立GET，NOT_FOUND保留完整原件并阻挡reset', async () => {
  const calls = installHttpFixture((_method, path) => {
    if (path.includes('/dynamic-goal-actions/by-key/')) return dynamicLookupFixture(intent, 'NOT_FOUND');
    if (path === '/api/v1/demo/presets') return presetsFixture();
    if (path === '/api/v1/demo/state') return stateFixture();
    return new Response('{}', { status: 503 });
  });
  const intent = await store.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture());
  await store.beginDynamicGoalOperation(intent); store.endDynamicGoalAttempt(); open();
  const read = await screen.findByRole('button', { name: '独立只读核对原动态目标原件' }); expect(read).toBeEnabled(); fireEvent.click(read);
  await screen.findByText(/NOT_FOUND不是最终未提交证明/); expect(store.getDynamicGoalOperation().pending).toEqual(intent);
  demo(); await screen.findByRole('heading', { name: '演示控制台' });
  expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
  expect(calls.filter((call) => call.path.includes('/dynamic-goal-actions/by-key/'))).toHaveLength(1);
});

test('prepare已独立核对但固定PLANNED尚未终局，跨页门保留且原GET仍可达', async () => {
  const calls = installHttpFixture((_method, path) => {
    if (path.includes('/dynamic-goal-actions/by-key/')) return dynamicLookupFixture(intent, 'PREPARED');
    if (path === '/api/v1/demo/presets') return presetsFixture();
    if (path === '/api/v1/demo/state') return stateFixture();
    return new Response('{}', { status: 503 });
  });
  const intent = await store.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture());
  await store.beginDynamicGoalOperation(intent); store.endDynamicGoalAttempt();
  await store.acceptDynamicGoalRead(intent, await api.lookupDynamicGoal(intent));
  expect(store.getDynamicGoalOperation().pending).toBeNull(); expect(store.getDynamicGoalOperation().workspace?.action?.status).toBe('PLANNED');
  open(); await screen.findByText(/动态目标原请求或固定动作尚未终局/);
  fireEvent.click(screen.getByRole('button', { name: '独立只读核对原动态目标原件' }));
  await screen.findByText(/独立GET已核对固定原动作/);
  await waitFor(() => expect(calls.filter((call) => call.path.includes('/dynamic-goal-actions/by-key/'))).toHaveLength(2));
  demo(); await screen.findByRole('heading', { name: '演示控制台' });
  expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
