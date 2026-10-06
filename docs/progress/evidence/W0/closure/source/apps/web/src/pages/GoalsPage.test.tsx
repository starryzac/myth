import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import type { components } from '../../../../packages/contracts/schema';
import GoalsPage from './GoalsPage';
import { dashboardFixture } from '../tests/dashboard-fixture';
import { installHttpFixture, policyFixture, policyId, versionId } from '../tests/policy-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function openPage() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><GoalsPage /></QueryClientProvider>); }
function goalFixture(): components['schemas']['GoalView'] {
  return { id: '10000000-0000-0000-0000-000000000060', policy_id: policyId, policy_version_id: versionId,
    account_id: dashboardFixture().account_facts.facts.accounts[0]!.id, name: '旅行目标', target_cents: 120000, allocated_cents: 999999,
    deadline: '2027-01-01', monthly_min_cents: 0, monthly_target_cents: 20000, monthly_max_cents: 30000,
    importance: 50, minimum_protection_cents: 0, reducible: false, deferrable: false, cross_goal_reallocation_allowed: false, asset_policy_id: null };
}
test('目标建立只提交真实关联和账户，零归属不划款，月度min0真实显示', async () => {
  const goals: components['schemas']['GoalView'][] = []; const goal = goalFixture(); const dashboard = dashboardFixture();
  const requests = installHttpFixture((method, path) => {
    if (path === '/api/v1/policies') return { simulation: true, items: [policyFixture('goal_saving')] };
    if (path === '/api/v1/goals' && method === 'GET') return { simulation: true, items: goals };
    if (path === '/api/v1/goals' && method === 'POST') {
      goals.push(goal); dashboard.goal_ownership.items.push({ goal_id: goal.id, policy_id: policyId, account_id: goal.account_id, name: goal.name,
        cash_owned_cents: 0, principal_owned_cents: 0, allocated_cents: 0, evidence_ids: [] }); return { simulation: true, goal };
    }
    if (path === '/api/v1/accounts/summary') return dashboard.account_facts.facts;
    if (path === '/api/v1/dashboard') return dashboard;
    if (path === '/api/v1/positions' || path === '/api/v1/products') return { simulation: true, items: [] };
    throw new Error(`unexpected ${method} ${path}`);
  });
  openPage(); const pending = await screen.findByRole('article', { name: '待建立目标 旅行目标' });
  const select = within(pending).getByLabelText('目标归属账户');
  await waitFor(() => expect(within(select).getAllByRole('option').length).toBeGreaterThan(1));
  fireEvent.change(select, { target: { value: goal.account_id } }); expect(within(pending).getByRole('button', { name: '建立目标归属' })).toBeDisabled();
  fireEvent.click(within(pending).getByRole('checkbox')); fireEvent.click(within(pending).getByRole('button', { name: '建立目标归属' }));
  const card = await screen.findByRole('article', { name: '目标 旅行目标' }); expect(card).toHaveTextContent('每月最低¥0.00'); expect(card).not.toHaveTextContent('9,999.99');
  expect(requests.filter((r) => r.method !== 'GET')).toEqual([{ method: 'POST', path: '/api/v1/goals', body: { policy_id: policyId, expected_version_id: versionId, account_id: goal.account_id } }]);
});
test('来源不足不能用投影allocated假冒现金，资产本金交叉单独显示', async () => {
  const goal = goalFixture(); const dashboard = dashboardFixture();
  dashboard.goal_ownership.state = 'NOT_PROVEN'; dashboard.goal_ownership.items = [];
  dashboard.managed_assets.by_goal = [{ goal_id: goal.id, principal_cents: 10000, pending_purchase_cents: 500 }];
  installHttpFixture((_method, path) => path === '/api/v1/policies' ? { simulation: true, items: [policyFixture('goal_saving')] }
    : path === '/api/v1/goals' ? { simulation: true, items: [goal] }
      : path === '/api/v1/dashboard' ? dashboard : path === '/api/v1/accounts/summary' ? dashboard.account_facts.facts : { simulation: true, items: [] });
  openPage(); const card = await screen.findByRole('article', { name: '目标 旅行目标' });
  await waitFor(() => expect(card).toHaveTextContent('已自主配置本金¥100.00'));
  expect(card).toHaveTextContent('已归属现金待核验'); expect(card).toHaveTextContent('已归属本金待核验'); expect(card).not.toHaveTextContent('9,999.99');
  expect(card).toHaveTextContent('不能再次加总'); expect(card).toHaveTextContent('未绑定'); expect(card).toHaveTextContent('不允许');
});
