import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import FullCurrentGoalAllocationPanel from './FullCurrentGoalAllocationPanel';
import { fullJointFixture, fullJointGoalFixture } from '../tests/full-joint-fixture';
import { goalId, otherGoalId } from '../tests/full-goal-fixture';
import type { Goal } from '../api/goals';

function open(value = fullJointFixture(), goals: readonly Goal[] | null = [fullJointGoalFixture()]) {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); const fetch = vi.fn(async () => new Response(JSON.stringify(value))); vi.stubGlobal('fetch', fetch);
  const query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); const view = render(<QueryClientProvider client={query}><FullCurrentGoalAllocationPanel goals={goals ?? undefined} /></QueryClientProvider>); return { fetch, query, view };
}
test('只读Full逐层金额、收入/owned与版本、八层/1098分母保持不执行', async () => {
  const { fetch } = open(); const objective = await screen.findByRole('region', { name: 'Full当前期八层结果' }); expect(within(objective).getAllByRole('listitem')).toHaveLength(8);
  expect(screen.getByText('1098 / 1098 原点 · VERIFIED')).toBeVisible(); const goal = screen.getByRole('article', { name: `Full目标计划 ${goalId}` }); expect(within(goal).getByText('¥100.00')).toBeVisible(); expect(within(goal).getByText('¥120.02')).toBeVisible(); expect(within(goal).getByText(/预计归属和新增分配均未执行/)).toBeVisible(); expect(screen.getByText(/未来指定来源账户扣款尚未完整重放/)).toBeVisible(); expect(screen.queryByRole('button', { name: /执行|确认|分配资金/ })).not.toBeInTheDocument(); expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByRole('combobox', { name: '逐层查看保护日期' }), { target: { value: '7' } }); const paid = screen.getByRole('region', { name: 'Full联合保护付款后' }); expect(within(paid).getByText('full_dated_expense')).toBeVisible(); expect(within(paid).getAllByText('¥0.00').length).toBeGreaterThan(0);
  expect(screen.getByText(/不声称重新计算这些SHA/)).toBeInTheDocument();
});
test('来源UNKNOWN新增归属/计划与八层全未知，不用零替代', async () => {
  open(fullJointFixture('UNKNOWN')); await screen.findByText('UNKNOWN · 未证明可分配'); const goal = screen.getByRole('article', { name: `Full目标计划 ${goalId}` }); expect(within(goal).getAllByText('UNKNOWN · 金额未证明')).toHaveLength(6); expect(within(goal).queryByText('¥0.00')).not.toBeInTheDocument(); expect(screen.getByText('UNKNOWN · 来源或求解未证明')).toBeVisible(); const objectives = screen.getByRole('region', { name: 'Full当前期八层结果' }); expect(within(objectives).getAllByText(/UNKNOWN · 尚无向量/)).toHaveLength(8); expect(screen.getByText(/尚无证明的计划，不代表实际收入不存在/)).toBeInTheDocument();
});
test('9目标容量无allocation保完整未覆盖，不展示空成功', async () => {
  open(fullJointFixture('CAPACITY'), null); const goals = await screen.findByRole('list', { name: 'Full未覆盖目标' }); expect(within(goals).getAllByRole('listitem')).toHaveLength(9); expect(screen.getAllByText('9 个')).toHaveLength(3); expect(screen.getByText('UNKNOWN · 原候选未取得')).toBeVisible();
});
test('当前Goal版本更新后旧查询不称本次成功，只允许只读刷新', async () => {
  const value = fullJointFixture(); const { query, view, fetch } = open(value); await screen.findByRole('article', { name: `Full目标计划 ${goalId}` }); view.rerender(<QueryClientProvider client={query}><FullCurrentGoalAllocationPanel goals={[{ ...fullJointGoalFixture(), policy_version_id: otherGoalId }]} /></QueryClientProvider>); expect(await screen.findByRole('alert')).toHaveTextContent('当前目标列表已变化或不一致'); expect(screen.queryByRole('article', { name: `Full目标计划 ${goalId}` })).not.toBeInTheDocument(); expect(fetch).toHaveBeenCalledTimes(1);
});
test('刷新来源失败不继续显示旧Full计划为成功，原请求只GET', async () => {
  const { fetch } = open(); await screen.findByRole('article', { name: `Full目标计划 ${goalId}` }); fetch.mockImplementationOnce(async () => { throw new Error('offline'); }); fireEvent.click(screen.getByRole('button', { name: '只读刷新Full联合规划' })); expect(await screen.findByRole('alert')).toHaveTextContent('旧报告不作为本次读取成功'); expect(screen.queryByRole('article', { name: `Full目标计划 ${goalId}` })).not.toBeInTheDocument(); expect(fetch).toHaveBeenCalledTimes(2);
});
