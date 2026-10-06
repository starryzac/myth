import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import CurrentGoalAllocationPanel from './CurrentGoalAllocationPanel';
import { goalId, jointFixture, otherGoalId } from '../tests/full-goal-fixture';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function open(source = jointFixture()) {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); const fetch = vi.fn(async () => new Response(JSON.stringify(source))); vi.stubGlobal('fetch', fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><CurrentGoalAllocationPanel goals={[{ id: goalId, name: 'HTTP名称' }]} /></QueryClientProvider>); return fetch;
}
test('八层向量/原金额/条件归属与延期下界分开显示，无execute或确认控件', async () => {
  const fetch = open(); await screen.findByRole('region', { name: '当前期八层目标结果' }); const objectives = screen.getByRole('region', { name: '当前期八层目标结果' }); expect(within(objectives).getAllByRole('listitem')).toHaveLength(8);
  const goal = screen.getByRole('article', { name: `目标计划 ${goalId}` }); expect(within(goal).getByText('¥200.03')).toBeVisible(); expect(within(goal).getByText('¥1,200.04')).toBeVisible(); expect(within(goal).getByText(/预计归属不是已经到账/)).toBeVisible();
  expect(within(goal).getByText('0 日')).toBeVisible(); expect(within(goal).getByText(/0日也不表示已完成/)).toBeVisible(); expect(screen.getByText(/未证明原完整问题的全局多期最优/)).toBeVisible(); expect(screen.getByText(/零计划不等于不存在收入事实/)).toBeVisible(); expect(screen.queryByRole('button', { name: /执行|确认|分配资金/ })).not.toBeInTheDocument(); expect(fetch).toHaveBeenCalledTimes(1);
});
test('缺模型UNKNOWN保留原目标分母/原issue/空八层，不填0金额', async () => {
  open(jointFixture('UNKNOWN')); const issues = await screen.findByRole('list', { name: '联合规划来源问题' }); expect(within(issues).getByText('MISSING_FULL_GOAL_MODEL')).toBeVisible(); expect(screen.getByText(/UNKNOWN，不用旧模型或零金额替代/)).toHaveTextContent(otherGoalId);
  expect(screen.getByText('2 个')).toBeVisible(); const objectives = screen.getByRole('region', { name: '当前期八层目标结果' }); expect(within(objectives).getAllByText('UNKNOWN · 尚无结果')).toHaveLength(8); const goal = screen.getByRole('article', { name: `目标计划 ${goalId}` }); expect(within(goal).getAllByText('UNKNOWN · 金额尚未证明')).toHaveLength(4);
});
test('capacity全9原件不省分母，来源错误只GET一次；可用户只读刷新', async () => {
  const fetch = open(jointFixture('CAPACITY')); await screen.findByText(/完整输入候选与未覆盖名单可能重叠/); expect(screen.getAllByText('9 个')).toHaveLength(3); expect(screen.queryByRole('region', { name: '当前期八层目标结果' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '只读刷新联合规划' })); await screen.findByText(/尚无当前期求解结果/); expect(fetch).toHaveBeenCalledTimes(2);
});
