/** SYNTHETIC_HTTP_ONLY: explicit existing policy host, no financial execution. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import FullPoliciesPanel from './FullPoliciesPanel';
import { installHttpFixture } from '../tests/policy-fixture';
import { paymentFixture } from '../tests/fixed-payment-fixture';
import { fullFlags } from '../tests/full-policy-fixture';
import { cashAccountsFixture } from '../tests/goal-cash-release-fixture';
import { stateFixture } from '../tests/demo-fixture';

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); sessionStorage.clear(); });
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function mount() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><FullPoliciesPanel blocked paymentMutationBlocked /></QueryClientProvider>); }
async function hostFixture() {
  const fixture = await paymentFixture();
  // This synthetic host fixture includes the original FULL confirmation too;
  // changing a template/config without its matching confirmation must be refused.
  fixture.full.current_version.confirmation = { ...fixture.full.current_version.confirmation, template_name: 'PeriodicTransferPolicy', reviewed_hash: fixture.full.current_version.content_hash };
  return fixture;
}

test('当前周期策略详情加载实际用户周期与原MVP权限，其他族门限制金融提交', async () => {
  const fixture = await hostFixture();
  const calls = installHttpFixture((_method, path) => {
    if (path.endsWith('/full-policies')) return { ...fullFlags, items: [fixture.full] };
    if (path.endsWith('/full-policies/' + fixture.full.policy_id)) return fixture.full;
    if (path.endsWith('/accounts/summary')) return { ...cashAccountsFixture(), user_id: fixture.scope.user_id };
    if (path.endsWith('/demo/state')) return { ...stateFixture(), epoch_id: fixture.scope.epoch_id };
    if (path.endsWith('/policies')) return { simulation: true, items: [fixture.original] };
    return { simulation: true, items: [] };
  });
  mount(); fireEvent.click(await screen.findByRole('button', { name: /合成Full周期关系.*PeriodicTransferPolicy/ }));
  expect(await screen.findByRole('region', { name: `固定收款关系 ${fixture.full.policy_id}` })).toBeVisible();
  expect(await screen.findByRole('option', { name: /合成原周期关系/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '只读刷新付款用户与周期' })).toBeEnabled();
  expect(calls.some((call) => call.path.endsWith('/accounts/summary'))).toBe(true);
  expect(calls.some((call) => call.path.endsWith('/demo/state'))).toBe(true);
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
});

test('账户用户来源失败时不挂付款表单，保留当前源GET刷新和策略详情', async () => {
  const fixture = await hostFixture();
  const calls = installHttpFixture((_method, path) => {
    if (path.endsWith('/full-policies')) return { ...fullFlags, items: [fixture.full] };
    if (path.endsWith('/full-policies/' + fixture.full.policy_id)) return fixture.full;
    if (path.endsWith('/accounts/summary')) return new Response('{}', { status: 503 });
    if (path.endsWith('/demo/state')) return { ...stateFixture(), epoch_id: fixture.scope.epoch_id };
    return { simulation: true, items: [] };
  });
  mount(); fireEvent.click(await screen.findByRole('button', { name: /合成Full周期关系.*PeriodicTransferPolicy/ }));
  await waitFor(() => expect(screen.getByText(/当前用户或周期未证明/)).toBeVisible());
  expect(screen.queryByRole('region', { name: `固定收款关系 ${fixture.full.policy_id}` })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '只读刷新付款用户与周期' })).toBeEnabled();
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
