import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import AnnualPlanningPage from './AnnualPlanningPage';
import { fullAnnualFixture } from '../tests/full-annual-fixture';
import { annualFixture } from '../tests/annual-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
import { dashboardFixture } from '../tests/dashboard-fixture';
import { stateFixture } from '../tests/demo-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function openPage() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><AnnualPlanningPage /></QueryClientProvider>); }
function hostOriginal(path: string, planning: unknown) { return path === '/api/v1/accounts/summary' ? dashboardFixture().account_facts.facts : path === '/api/v1/demo/state' ? stateFixture() : planning; }
test('真实GET消费者显示365未来日加初始日期/三阶段，并区分90参考与无授权年度规划', async () => {
  const requests = installHttpFixture((_method, path) => hostOriginal(path, path.endsWith('/full-annual') ? fullAnnualFixture() : annualFixture())); openPage(); const select = await screen.findByLabelText('查看规划日期');
  expect(within(select).getAllByRole('option')).toHaveLength(366); expect(screen.getByRole('heading', { name: '90日财务执行参考' })).toBeVisible();
  expect(screen.getByRole('heading', { name: '365日条件规划' })).toBeVisible(); expect(screen.getByText(/未来曲线不是已到账现金/)).toBeVisible();
  fireEvent.change(select, { target: { value: '200' } }); expect(screen.getByText(/2027-04-23 · 最小日内余量/)).toBeVisible();
  expect(within(screen.getByRole('region', { name: '付款前' })).getByText('¥1,000.01')).toBeVisible();
  expect(within(screen.getByRole('region', { name: '付款后' })).getByText('¥990.01')).toBeVisible();
  expect(within(screen.getByRole('region', { name: '本金到账后' })).getByText('¥995.01')).toBeVisible();
  expect(screen.getByRole('img', { name: /365日条件规划/ })).toBeVisible(); expect(within(screen.getByRole('region', { name: '规划约束与证据' })).getByRole('link', { name: '10000000-0000-0000-0000-000000000011' })).toHaveAttribute('href', '#evidence/EVIDENCE/10000000-0000-0000-0000-000000000011');
  expect(requests.every((request) => request.method === 'GET' && request.body === undefined)).toBe(true);
});
test('原件不足保日期与null未知，不画零曲线或隐去来源问题', async () => {
  installHttpFixture((_method, path) => hostOriginal(path, path.endsWith('/full-annual') ? fullAnnualFixture(true) : annualFixture(true))); openPage(); const select = await screen.findByLabelText('查看规划日期');
  expect(within(select).getAllByRole('option')).toHaveLength(366); expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.getByText(/全年余量尚未证明/)).toBeVisible(); expect(within(screen.getByRole('region', { name: '规划约束与证据' })).getByText(/UNIT_SOURCE_NOT_PROVEN/, { selector: 'li' })).toBeVisible();
  expect(within(screen.getByRole('region', { name: '付款前' })).getByText(/现金、保护与余量均未知/)).toBeVisible();
  expect(within(screen.getByRole('region', { name: '年度日期时间轴' })).queryByText('¥0.00')).not.toBeInTheDocument(); expect(within(screen.getByRole('region', { name: '执行参考与年度规划' })).queryByText('¥0.00')).not.toBeInTheDocument(); expect(screen.getByText(/现金边界未计入未来收入；下方条件假设独立展示/)).toBeVisible();
});
test('错误响应不发布曲线或成功金额', async () => {
  const source = annualFixture(); source.daily_checkpoints.pop(); installHttpFixture((_method, path) => hostOriginal(path, path.endsWith('/full-annual') ? fullAnnualFixture(true) : source)); openPage(); await screen.findByRole('alert');
  expect(screen.queryByLabelText('查看规划日期')).not.toBeInTheDocument(); expect(screen.queryByRole('img')).not.toBeInTheDocument();
});


test('旧90/365区域与FULL保护独立GET；FULL篡改不会掩盖已核对旧视图或发起资金请求', async () => {
  const bad = fullAnnualFixture(); bad.projection.bank_authority = true as never; const calls = installHttpFixture((_method, path) => hostOriginal(path, path.endsWith('/full-annual') ? bad : annualFixture())); openPage(); await screen.findByLabelText('查看规划日期'); const full = screen.getByRole('region', { name: '完整策略年度保护' }); await within(full).findByRole('alert'); expect(screen.getByRole('heading', { name: '90日财务执行参考' })).toBeVisible(); expect(screen.queryByLabelText('查看完整保护日期')).not.toBeInTheDocument(); expect(calls.map((call) => call.path).sort()).toEqual(['/api/v1/accounts/summary', '/api/v1/demo/state', '/api/v1/planning/annual', '/api/v1/planning/full-annual']); expect(calls.every((call) => call.method === 'GET' && call.body === undefined)).toBe(true);
});
