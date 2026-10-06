/** Synthetic parent/HTTP risks only, never browser, PG or actual USER adoption evidence. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { seasonalFixture, seasonalIntentFixture, seasonalLookupFixture } from './tests/seasonal-adoption-fixture';
import { fullFlags, fullPolicyFixture } from './tests/full-policy-fixture';
import { cashAccountsFixture } from './tests/goal-cash-release-fixture';
import { futureCandidateBody, futureUser } from './tests/future-income-planning-fixture';
import { presetsFixture, stateFixture } from './tests/demo-fixture';
import { installHttpFixture } from './tests/policy-fixture';

// Isolate this parent prop boundary; this button is not a financial command producer.
vi.mock('./pages/AnnualPlanningPage', () => ({ default: ({ mutationBlocked }: { mutationBlocked: boolean }) => <button disabled={mutationBlocked}>年度条件写门测试点</button> }));
let App: typeof import('./App').default, store: typeof import('./features/seasonal-adoption-operation');
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); window.history.replaceState(null, '', '/#policies'); App = (await import('./App')).default; store = await import('./features/seasonal-adoption-operation'); });
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function open() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><App /></QueryClientProvider>); }
function navigate(hash: string) { window.location.hash = hash; window.dispatchEvent(new HashChangeEvent('hashchange')); }

test('列表503和无USER仍顶层同键GET，原NOT_FOUND保门，季节pending阻年度/演示写', async () => {
  const intent = await seasonalIntentFixture(), missing = await seasonalLookupFixture(intent, false), recorded = await seasonalLookupFixture(intent); let found = false;
  const calls = installHttpFixture((_method, path) => { if (path.includes('/seasonal-reserve-adoptions/commands/')) return found ? recorded : missing; if (path === '/api/v1/demo/state') return stateFixture(); if (path === '/api/v1/demo/presets') return presetsFixture(); return new Response('{}', { status: 503 }); });
  await store.beginSeasonalAdoptionOperation(intent); store.endSeasonalAdoptionAttempt(); open();
  expect(await screen.findByRole('button', { name: '准备新完整版策略' })).toBeDisabled(); const read = await screen.findByRole('button', { name: '只读核对原季节采纳请求' }); expect(read.closest('fieldset')).toBeNull(); await waitFor(() => expect(read).toBeEnabled()); fireEvent.click(read); await screen.findByText(/原键未终局/); expect(store.getSeasonalAdoptionOperation().pending).toEqual(intent);
  navigate('#annual'); expect(await screen.findByRole('button', { name: '年度条件写门测试点' })).toBeDisabled(); expect(screen.getByRole('button', { name: '只读核对原季节采纳请求' })).toBeEnabled();
  navigate('#demo'); const reset = await screen.findByRole('button', { name: '恢复演示初始状态' }); expect(reset).toBeDisabled(); found = true; fireEvent.click(screen.getByRole('button', { name: '只读核对原季节采纳请求' })); await waitFor(() => expect(store.getSeasonalAdoptionOperation().pending).toBeNull()); await waitFor(() => expect(reset).toBeEnabled());
  expect(calls.every((call) => call.method === 'GET' && call.body === undefined)).toBe(true); expect(calls.filter((call) => call.path.includes('/seasonal-reserve-adoptions/commands/'))).toHaveLength(2);
});

test('仅所选Seasonal读取实际owner/OPENepoch；未知周期不造scope，未来声明门仍阻采纳', async () => {
  const fixture = await seasonalFixture(), policy = fullPolicyFixture(); policy.policy_id = fixture.scope.policy_id; policy.epoch_id = fixture.scope.epoch_id; policy.template_name = 'SeasonalReservePolicy'; policy.name = '合成Seasonal父页来源'; policy.current_version = { ...policy.current_version, policy_id: policy.policy_id, version_id: fixture.scope.version_id, configuration: structuredClone(fixture.full.current_version.configuration), content_hash: fixture.scope.configuration_hash, confirmation: { ...policy.current_version.confirmation, user_id: fixture.scope.user_id, epoch_id: policy.epoch_id, policy_id: policy.policy_id, version_id: fixture.scope.version_id, template_name: 'SeasonalReservePolicy', reviewed_hash: fixture.scope.configuration_hash } };
  let available = false;
  const calls = installHttpFixture((_method, path) => { if (path === '/api/v1/full-policies') return { ...fullFlags, items: [policy] }; if (path === `/api/v1/full-policies/${policy.policy_id}`) return policy; if (path === '/api/v1/accounts/summary') return { ...cashAccountsFixture(), user_id: fixture.scope.user_id }; if (path === '/api/v1/demo/state') return { ...stateFixture(), epoch_id: available ? policy.epoch_id : null, available }; return new Response('{}', { status: 503 }); });
  const future = await import('./features/future-income-operation'), originalFuture = await future.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody()); await future.beginFutureIncomeOperation(originalFuture); future.endFutureIncomeAttempt(); open();
  expect(calls.some((call) => call.path === '/api/v1/accounts/summary')).toBe(false); fireEvent.click(await screen.findByRole('button', { name: /合成Seasonal父页来源.*SeasonalReservePolicy/ }));
  await screen.findByText(/未取得当前OPEN周期及匹配用户/); expect(screen.queryByRole('region', { name: `季节储备显式采纳 ${policy.policy_id}` })).toBeNull(); available = true; fireEvent.click(screen.getByRole('button', { name: '只读刷新采纳用户与周期' })); await screen.findByRole('region', { name: `季节储备显式采纳 ${policy.policy_id}` });
  fireEvent.change(screen.getByLabelText('用户指定登记节日窗口 ID'), { target: { value: fixture.scope.window_id } }); expect(screen.getByRole('button', { name: '只读预览原节日建议采纳' })).toBeDisabled(); expect(screen.getByRole('button', { name: '只读刷新当前季节采纳' })).toBeEnabled(); expect(future.getFutureIncomeOperation().pending).toEqual(originalFuture); expect(calls.some((call) => call.path === '/api/v1/accounts/summary')).toBe(true); expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
