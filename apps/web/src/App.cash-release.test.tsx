/** SYNTHETIC_HTTP_ONLY. Parent routing and write gates, not actual bank execution. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import App from './App';
import { installHttpFixture } from './tests/policy-fixture';
import { dashboardFixture } from './tests/dashboard-fixture';
import { presetsFixture, stateFixture } from './tests/demo-fixture';
import { cashAccountsFixture, cashActionFixture, cashBinding, cashIntentFixture, cashLookupFixture } from './tests/goal-cash-release-fixture';
import { readCashIntent } from './api/goal-cash-releases';
import { acceptCashRead, beginCashPrepare, endCashAttempt, getGoalCashReleaseOperation, retainCashPostResponse } from './features/goal-cash-release-operation';
import { localActorFixture } from './tests/local-actor-fixture';

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); window.history.replaceState(null, '', '/#policies'); sessionStorage.clear(); });
afterEach(async () => {
  cleanup(); endCashAttempt();
  const pending = getGoalCashReleaseOperation().pending;
  if (pending) {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(await cashActionFixture(pending.intent, 'SUCCEEDED')))));
    await acceptCashRead(pending.intent, await readCashIntent(pending.intent, pending.original_action));
  }
  sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs();
});
function mount() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><App /></QueryClientProvider>); }
function navigate(hash: string) { window.history.replaceState(null, '', `/${hash}`); fireEvent(window, new Event('hashchange')); }
function dashboard() { const value = dashboardFixture(); value.user_id = cashBinding.userId; value.account_facts.facts.user_id = cashBinding.userId; value.audit.epoch_id = cashBinding.epochId; return value; }

test('原现金回拨跨页挡新写和reset，列表外GET保留NOT_FOUND并完整原回执才解门', async () => {
  const intent = await cashIntentFixture('ROOT_CASH_MISSING_GOAL:1');
  let final = false;
  const paid = await cashActionFixture(intent, 'SUCCEEDED');
  const calls = installHttpFixture((_method, path) => {
    if (path.includes('/goal-cash-releases/commands/')) return cashLookupFixture(intent, final ? paid : null);
    if (path.endsWith('/accounts/summary')) return cashAccountsFixture();
    if (path.endsWith('/dashboard')) return dashboard();
    if (path.endsWith('/demo/state')) return stateFixture();
    if (path.endsWith('/demo/presets')) return presetsFixture();
    if (path.endsWith('/local-actor/session')) return { ...localActorFixture(), principal: { ...localActorFixture().principal, user_id: cashBinding.userId } };
    return { simulation: true, items: [] };
  });
  await beginCashPrepare(intent); endCashAttempt(); mount();
  expect(await screen.findByText(/原现金回拨请求正在处理或待核对/)).toBeVisible();
  expect(await screen.findByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  const identity = screen.getByText('本地用户身份会话').closest('details')!; identity.open = true;
  expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '明确注销本地会话' })).toBeDisabled();
  const readIdentity = screen.getByRole('button', { name: '只读读取当前身份' }); expect(readIdentity).toBeEnabled(); fireEvent.click(readIdentity);
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/local-actor/session'))).toHaveLength(1));
  await waitFor(() => expect(screen.getByRole('button', { name: '明确登录本地会话' })).toBeEnabled());
  expect(screen.getByRole('button', { name: '明确注销本地会话' })).toBeDisabled();
  expect(getGoalCashReleaseOperation().pending?.intent.body_json).toBe(intent.body_json);
  navigate('#questions'); expect(await screen.findByRole('button', { name: '保存原请求并开始问答' })).toBeDisabled();
  navigate('#demo'); expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  navigate('#goals'); const area = await screen.findByRole('region', { name: '列表外原现金回拨恢复' });
  const read = within(area).getByRole('button', { name: '只读核对原回拨行动与回执' }); expect(read).toBeEnabled();
  fireEvent.click(read); await waitFor(() => expect(calls.filter((call) => call.path.includes('/goal-cash-releases/commands/'))).toHaveLength(1));
  expect(getGoalCashReleaseOperation().pending?.intent.body_json).toBe(intent.body_json);
  final = true; fireEvent.click(read); await waitFor(() => expect(getGoalCashReleaseOperation().pending).toBeNull());
  expect(within(area).getByText(/原效果 · SUCCEEDED/)).toBeVisible();
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
  navigate('#policies'); expect(await screen.findByRole('button', { name: '准备新完整版策略' })).toBeEnabled();
});

test('自身PLANNED回拨可明确执行原效果，POST保留全局门，独立GET完成后才释放', async () => {
  const intent = await cashIntentFixture('ROOT_CASH_OWN_EFFECT:1');
  const original = await cashActionFixture(intent), paid = await cashActionFixture(intent, 'SUCCEEDED');
  const goal = { ...cashBinding.goal, account_id: '10000000-0000-0000-0000-000000000060', target_cents: 120000, allocated_cents: 67003, deadline: '2027-01-01', monthly_min_cents: 0, monthly_target_cents: 20000, monthly_max_cents: 30000, importance: 50, minimum_protection_cents: 0, reducible: false, deferrable: false, cross_goal_reallocation_allowed: false, asset_policy_id: null };
  const calls = installHttpFixture((method, path) => {
    if (path.endsWith('/execute') && method === 'POST') return paid;
    if (path.includes('/goal-cash-releases/actions/')) return paid;
    if (path.endsWith('/accounts/summary')) return cashAccountsFixture();
    if (path.endsWith('/dashboard')) return dashboard();
    if (path === '/api/v1/goals') return { simulation: true, items: [goal] };
    return { simulation: true, items: [] };
  });
  await beginCashPrepare(intent); await retainCashPostResponse(intent, original); endCashAttempt();
  window.history.replaceState(null, '', '/#goals'); mount();
  const card = await screen.findByRole('article', { name: `目标 ${goal.name}` });
  expect(within(card).getByRole('button', { name: '查看当前收入分配预览' })).toBeDisabled();
  const detail = within(card).getByText('复核紧急现金回拨行动或查询原回执').closest('details')!; detail.open = true; fireEvent(detail, new Event('toggle', { bubbles: true }));
  const checkbox = await within(card).findByRole('checkbox', { name: /明确接受并只执行或恢复此原行动/ }); expect(checkbox).toBeEnabled(); fireEvent.click(checkbox);
  const execute = within(card).getByRole('button', { name: '明确确认原效果并执行' }); expect(execute).toBeEnabled(); fireEvent.click(execute);
  await waitFor(() => expect(getGoalCashReleaseOperation().pending?.original_action?.original_action_status).toBe('SUCCEEDED'));
  expect(getGoalCashReleaseOperation().pending).not.toBeNull();
  fireEvent.click(within(card).getByRole('button', { name: '只读核对原回拨行动与回执' }));
  await waitFor(() => expect(getGoalCashReleaseOperation().pending).toBeNull());
  expect(calls.filter((call) => call.method === 'POST')).toEqual([{ method: 'POST', path: `/api/v1/goal-cash-releases/actions/${original.action_id}/execute`, body: { accepted: true, reviewed_effect_hash: original.original_command.effect_hash, expected_epoch_id: intent.body.expected_epoch_id } }]);
});
