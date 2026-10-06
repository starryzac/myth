/** HTTP doubles prove host gates and original GET, not PostgreSQL or bank settlement. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { recoveryUser, recoveryPrepareFixture, recoveryLookupFixture } from './tests/full-recovery-execution-fixture';
import { presetsFixture, stateFixture } from './tests/demo-fixture';
import { installHttpFixture } from './tests/policy-fixture';
let App: typeof import('./App').default;
let store: typeof import('./features/full-recovery-execution-operation');
let api: typeof import('./api/full-recovery-execution');
beforeEach(async () => {
  vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); window.history.replaceState(null, '', '/#policies');
  App = (await import('./App')).default; store = await import('./features/full-recovery-execution-operation'); api = await import('./api/full-recovery-execution');
});
function open() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><App /></QueryClientProvider>); }
function demo() { window.location.hash = '#demo'; window.dispatchEvent(new HashChangeEvent('hashchange')); }

test('pending原安全恢复准备在当前列表失败时仍独立GET，NOT_FOUND保留完整原件并阻挡reset', async () => {
  const calls = installHttpFixture((_method, path) => {
    if (path.includes('/full-recovery-actions/by-key/')) return recoveryLookupFixture(intent, 'NOT_FOUND');
    if (path === '/api/v1/demo/presets') return presetsFixture();
    if (path === '/api/v1/demo/state') return stateFixture();
    return new Response('{}', { status: 503 });
  });
  const intent = await store.prepareRecoveryIntent('PREPARE', recoveryUser, recoveryPrepareFixture());
  await store.beginFullRecoveryOperation(intent); store.endFullRecoveryAttempt(); open();
  const read = await screen.findByRole('button', { name: '只读核对原安全恢复请求' }); expect(read).toBeEnabled(); fireEvent.click(read);
  await screen.findByText(/NOT_FOUND和HTTP错误不解除原门/); expect(store.getFullRecoveryOperation().pending).toEqual(intent);
  demo(); await screen.findByRole('heading', { name: '演示控制台' });
  expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
  expect(calls.filter((call) => call.path.includes('/full-recovery-actions/by-key/'))).toHaveLength(1);
});

test('prepare已独立核对但固定PLANNED尚未终局，跨页门保留且原GET仍可达', async () => {
  const calls = installHttpFixture((_method, path) => {
    if (path.includes('/full-recovery-actions/by-key/')) return recoveryLookupFixture(intent, 'PREPARED');
    if (path === '/api/v1/demo/presets') return presetsFixture();
    if (path === '/api/v1/demo/state') return stateFixture();
    return new Response('{}', { status: 503 });
  });
  const intent = await store.prepareRecoveryIntent('PREPARE', recoveryUser, recoveryPrepareFixture());
  await store.beginFullRecoveryOperation(intent); store.endFullRecoveryAttempt();
  await store.acceptFullRecoveryRead(intent, await api.lookupRecoveryExecution(intent));
  expect(store.getFullRecoveryOperation().pending).toBeNull(); expect(store.getFullRecoveryOperation().workspace?.action?.status).toBe('PLANNED');
  open(); await screen.findByText(/原安全恢复请求或固定动作尚未终局/);
  fireEvent.click(screen.getByRole('button', { name: '只读核对原安全恢复请求' }));
  await screen.findByText(/独立原GET已刷新工作区/);
  await waitFor(() => expect(calls.filter((call) => call.path.includes('/full-recovery-actions/by-key/'))).toHaveLength(2));
  demo(); await screen.findByRole('heading', { name: '演示控制台' });
  expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
});


test('键盘跳过导航不改变当前路由，实际路由变化聚焦当前内容', async () => {
  const calls = installHttpFixture((_method, path) => {
    if (path === '/api/v1/demo/presets') return presetsFixture();
    if (path === '/api/v1/demo/state') return stateFixture();
    return new Response('{}', { status: 503 });
  });
  open(); const content = await screen.findByRole('region', { name: '当前页面内容' });
  expect(content).not.toHaveFocus();
  fireEvent.click(screen.getByRole('link', { name: '跳到当前页面内容' }));
  expect(content).toHaveFocus(); expect(window.location.hash).toBe('#policies');
  demo(); await screen.findByRole('heading', { name: '演示控制台' });
  expect(screen.getByRole('region', { name: '当前页面内容' })).toHaveFocus();
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
