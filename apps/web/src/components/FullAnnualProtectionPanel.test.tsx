import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import FullAnnualProtectionPanel from './FullAnnualProtectionPanel';
import { fullAnnualFixture } from '../tests/full-annual-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function open() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><FullAnnualProtectionPanel /></QueryClientProvider>); }

test('366日期/三阶段只读GET显示保守金额及登记上限，不授予资金执行；跨月年与最后日期可读', async () => {
  const calls = installHttpFixture(() => fullAnnualFixture()); open(); const select = await screen.findByLabelText('查看完整保护日期'); expect(within(select).getAllByRole('option')).toHaveLength(366); expect(screen.getByText(/当前登记范围内的完整保护已计算/)).toBeVisible(); expect(screen.getByText(/登记固定金额 ¥50.03/)).toBeInTheDocument(); expect(screen.getByText(/登记上限 ¥70.01/)).toBeInTheDocument();
  fireEvent.change(select, { target: { value: '87' } }); expect(screen.getByText(/2026-12-31 · 最小日内余量 ¥669.97/)).toBeVisible(); fireEvent.change(select, { target: { value: '88' } }); expect(screen.getByText(/2027-01-01 · 最小日内余量 ¥669.97/)).toBeVisible(); fireEvent.change(select, { target: { value: '365' } }); expect(screen.getByText(/2027-10-05 · 最小日内余量 ¥669.97/)).toBeVisible(); expect(within(screen.getByRole('region', { name: '完整付款前' })).getByText(/条件现金 ¥1,000.01 · 余量 ¥679.97/)).toBeVisible(); expect(within(screen.getByRole('region', { name: '完整付款后' })).getByText(/条件现金 ¥990.01 · 余量 ¥669.97/)).toBeVisible(); expect(within(screen.getByRole('region', { name: '完整本金到账后' })).getByText(/条件现金 ¥995.01 · 余量 ¥674.97/)).toBeVisible(); expect(calls).toEqual([{ method: 'GET', path: '/api/v1/planning/full-annual', body: undefined }]); expect(screen.getByText(/新增FULL曲线没有被原执行消费者采用/)).toBeVisible(); expect(screen.getByText(/并非未来收入真实金额为零/)).toBeVisible();
});

test('FULL来源UNKNOWN不借原READY发布金额，null三阶段保366日期和具体原因/源问题', async () => {
  installHttpFixture(() => fullAnnualFixture(true)); open(); const select = await screen.findByLabelText('查看完整保护日期'); expect(within(select).getAllByRole('option')).toHaveLength(366); expect(screen.getByText(/完整保护来源不足，全年金额未知/)).toBeVisible(); const phase = within(screen.getByRole('region', { name: '完整付款前' })); expect(phase.getByText(/此阶段金额\/原引用未知/)).toBeVisible(); expect(phase.getByText('条件现金 未知 · 尚未证明 · 余量 未知 · 尚未证明')).toBeVisible(); expect(phase.queryByText(/¥0.00/)).not.toBeInTheDocument(); expect(screen.getByText('FULL_ORIGINAL_OR_HISTORICAL_COVERAGE_NOT_PROVEN')).toBeInTheDocument(); expect(screen.getByText(/UNIT_FULL_ORIGINAL_NOT_PROVEN/, { selector: 'p' })).toBeInTheDocument(); expect(screen.queryByText('当前登记范围内的完整保护已计算')).not.toBeInTheDocument();
});

test('账户局部风险保留负金额与原账户/constraint，原版本证据及Seasonal建议边界可查看', async () => {
  const calls = installHttpFixture(() => fullAnnualFixture(false, true)); open(); await screen.findByLabelText('查看完整保护日期'); expect(screen.getByText('完整保护存在流动性缺口')).toBeVisible(); fireEvent.click(screen.getByText('来源账户局部检查（1项）')); expect(screen.getByText('¥-10.02')).toBeVisible(); expect(screen.getByText(/SOURCE_LIQUIDITY_RISK/, { selector: 'p' })).toBeVisible(); expect(screen.getByText(/未独立分配全部未来MVP账户支出/)).toBeVisible(); fireEvent.click(screen.getByText('原FULL约束与条件问题')); expect(screen.getByText(/PERIODIC_SOURCE_LIQUIDITY_LIMIT/, { selector: 'p' })).toBeVisible(); fireEvent.click(screen.getByText('完整策略状态与来源')); expect(screen.getAllByText(/原版本.*（1）/, { selector: 'p' })).toHaveLength(3); expect(screen.getAllByText(/SeasonalReservePolicy/).length).toBeGreaterThan(0); expect(screen.getByText(/仅建议，尚无采纳的附加金额/)).toBeVisible(); fireEvent.click(screen.getByText(/完整保护实际来源证据/)); expect(screen.getByRole('link', { name: '20000000-0000-0000-0000-000000000030' })).toHaveAttribute('href', '#evidence/EVIDENCE/20000000-0000-0000-0000-000000000030'); expect(calls.every((call) => call.method === 'GET')).toBe(true);
});

test('刷新篡改报错隐藏旧金额/日期成功，不重试；新原文本响应保留而非结构共享丢原件', async () => {
  const source = fullAnnualFixture(); let reads = 0; const calls = installHttpFixture(() => { reads += 1; if (reads === 2) { const bad = fullAnnualFixture(); bad.daily_checkpoints.pop(); return bad; } return new Response(` \n${JSON.stringify(source)}\n`); }); open(); await screen.findByLabelText('查看完整保护日期'); fireEvent.click(screen.getByText('完整保护原JSON响应')); expect(screen.getByText(JSON.stringify(source), { exact: false, selector: 'pre' }).textContent).toBe(` \n${JSON.stringify(source)}\n`); fireEvent.click(screen.getByRole('button', { name: '刷新完整保护规划' })); await screen.findByRole('alert'); expect(screen.queryByLabelText('查看完整保护日期')).not.toBeInTheDocument(); expect(screen.queryByText('当前登记范围内的完整保护已计算')).not.toBeInTheDocument(); await waitFor(() => expect(calls).toHaveLength(2));
});

test('第一次unsafe金额失败不发布金额/日期或伪成功报告', async () => {
  const source = fullAnnualFixture(); source.projection.occurrences[0]!.conservative_unpaid_cents = Number.MAX_SAFE_INTEGER + 1; installHttpFixture(() => source); open(); await screen.findByRole('alert'); expect(screen.queryByLabelText('查看完整保护日期')).not.toBeInTheDocument(); expect(screen.queryByText('¥70.01')).not.toBeInTheDocument();
});
