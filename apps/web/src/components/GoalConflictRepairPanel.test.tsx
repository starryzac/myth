import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import GoalConflictRepairPanel from './GoalConflictRepairPanel';
import { conflictFixture, conflictGoals, repairFixture, repairSelection, unknownConflictFixture, unknownRepairFixture } from '../tests/goal-conflict-fixture';
import { fullModelFixture } from '../tests/full-goal-fixture';
const family = vi.hoisted(() => ({ busy: false, pending: null as { goal_id: string } | null, storage_error: null as string | null }));
vi.mock('../features/full-goal-operation', async (importOriginal) => ({ ...await importOriginal<typeof import('../features/full-goal-operation')>(), recoverFullGoalOperation: vi.fn(), useFullGoalOperation: () => family }));

function open(value = conflictFixture(), blocked = false) {
  family.busy = false; family.pending = null; family.storage_error = null;
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local');
  const fetch = vi.fn(async (_url: unknown, options: { method: string }) => new Response(JSON.stringify(options.method === 'GET' ? value : repairFixture()))); vi.stubGlobal('fetch', fetch);
  const query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const view = render(<QueryClientProvider client={query}><GoalConflictRepairPanel goals={conflictGoals()} mutationBlocked={blocked} /></QueryClientProvider>);
  return { fetch, query, view };
}
async function selectRange(min = '6', max = '20') {
  fireEvent.click(await screen.findByRole('checkbox', { name: /选择.*月max新范围/ }));
  fireEvent.change(screen.getByRole('textbox', { name: '新月max下界（整数分）' }), { target: { value: min } });
  fireEvent.change(screen.getByRole('textbox', { name: '新月max上界（整数分）' }), { target: { value: max } });
}
test('GET展示真实最小集与两个反事实，不自动选择或确认', async () => {
  const { fetch } = open(); const checks = await screen.findByRole('region', { name: '逐项移除原反事实见证' });
  expect(within(checks).getAllByText(/counterfactual_only=true/)).toHaveLength(2);
  expect(screen.getByText(/原许可修复：NO_PERMITTED_REPAIR/)).toBeVisible();
  expect(screen.getByText(/不证明其他全部约束兼容/)).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /选择.*月max新范围/ })).not.toBeChecked();
  expect(screen.getByRole('button', { name: '只读预览所选修复范围' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: /明确确认|执行资金|放松硬/ })).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
});
test('主动范围仅POST预览，呈现实际旧配置和双hash绑定，未影响与精确排序', async () => {
  const { fetch } = open(); await selectRange(); fireEvent.click(screen.getByRole('button', { name: '只读预览所选修复范围' }));
  const result = await screen.findByRole('region', { name: '只读修复候选结果' });
  expect(within(result).getByText('只读修复候选 · PROPOSAL')).toBeVisible();
  expect(within(result).getByText(/改变策略数 1 → 精确参数偏离 3\/5/)).toBeVisible();
  expect(within(result).getByText(/未受影响目标：本次没有/)).toBeVisible();
  const candidate = within(result).getByRole('article', { name: `待复核新目标版本 ${conflictGoals()[0]!.id}` });
  expect(within(candidate).getByText(/原max ¥0.05 \/ 新max ¥0.08/)).toBeVisible();
  const bindings = within(candidate).getByRole('textbox', { name: /原完整目标确认绑定/ }) as HTMLTextAreaElement;
  expect(JSON.parse(bindings.value)).toEqual(repairFixture().version_previews[0]!.confirmation_bindings);
  expect(bindings).toHaveAttribute('readonly');
  expect(within(candidate).getByText(/尚未确认。请在该目标原完整模型工作区/)).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(JSON.parse((fetch.mock.calls[1]![1] as unknown as { body: string }).body)).toEqual(repairSelection());
  expect(fetch.mock.calls.map((call) => String(call[0]))).toEqual([
    'http://http-unit-fixture.local/api/v1/planning/full-goal-conflicts',
    'http://http-unit-fixture.local/api/v1/planning/full-goal-repairs/preview',
  ]);
});
test('GET来源UNKNOWN保目标分母，不生成编辑范围或假金额', async () => {
  open(unknownConflictFixture()); await screen.findByText(/UNKNOWN · 原来源、银行、版本或完整分母未证明/);
  expect(screen.getByText(/原登记 1 个 \/ 输入 1 个/)).toBeVisible();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '用户主动选择修复范围' })).not.toBeInTheDocument();
});
test.each(['6.0', '-1', '9007199254740992', ''])('不精确范围%s不发POST', async (min) => {
  const { fetch } = open(); await selectRange(min); fireEvent.click(screen.getByRole('button', { name: '只读预览所选修复范围' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('调整范围必须为可精确表示的非负整数分');
  expect(fetch).toHaveBeenCalledTimes(1);
});
test('跨族门阻挡新预览，原只读刷新仍可用', async () => {
  const { fetch, query, view } = open(conflictFixture(), true); await screen.findByRole('checkbox');
  expect(screen.getByRole('checkbox')).toBeDisabled(); expect(screen.getByRole('button', { name: '只读刷新目标冲突' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '只读刷新目标冲突' })); await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  family.pending = { goal_id: conflictGoals()[0]!.id };
  view.rerender(<QueryClientProvider client={query}><GoalConflictRepairPanel goals={conflictGoals()} /></QueryClientProvider>);
  expect(screen.getByRole('checkbox')).toBeDisabled(); family.pending = null;
});
test('已看到候选后编辑范围或目标版本变化不沿用旧成功', async () => {
  const { query, view } = open(); await selectRange(); fireEvent.click(screen.getByRole('button', { name: '只读预览所选修复范围' }));
  await screen.findByRole('region', { name: '只读修复候选结果' });
  fireEvent.change(screen.getByRole('textbox', { name: '新月max上界（整数分）' }), { target: { value: '21' } });
  expect(screen.queryByRole('region', { name: '只读修复候选结果' })).not.toBeInTheDocument();
  view.rerender(<QueryClientProvider client={query}><GoalConflictRepairPanel goals={[{ ...conflictGoals()[0]!, policy_version_id: 'ffffffff-ffff-ffff-ffff-ffffffffffff' }]} /></QueryClientProvider>);
  expect(await screen.findByRole('alert')).toHaveTextContent('当前目标列表/版本不匹配');
  expect(screen.queryByRole('region', { name: '用户主动选择修复范围' })).not.toBeInTheDocument();
});
test('预览网络失败不显示旧成功、不确认；刷新GET失败隐藏旧报告', async () => {
  const { fetch } = open(); await selectRange(); fetch.mockImplementationOnce(async () => { throw new Error('offline'); });
  fireEvent.click(screen.getByRole('button', { name: '只读预览所选修复范围' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('未生成成功候选');
  expect(screen.queryByRole('region', { name: '只读修复候选结果' })).not.toBeInTheDocument();
  fetch.mockImplementationOnce(async () => { throw new Error('offline'); }); fireEvent.click(screen.getByRole('button', { name: '只读刷新目标冲突' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('旧报告不作为本次读取成功');
  expect(screen.queryByRole('region', { name: '原目标最小冲突与反事实' })).not.toBeInTheDocument();
});
test('POST时新银行来源UNKNOWN保真原null，不沿用先前GET成功', async () => {
  const { fetch } = open(); await selectRange(); fetch.mockImplementationOnce(async () => new Response(JSON.stringify(unknownRepairFixture())));
  fireEvent.click(screen.getByRole('button', { name: '只读预览所选修复范围' }));
  const result = await screen.findByRole('region', { name: '只读修复候选结果' });
  expect(within(result).getByText('只读修复候选 · UNKNOWN')).toBeVisible();
  expect(within(result).queryByRole('article', { name: /待复核新目标版本/ })).not.toBeInTheDocument();
});

test('匹配实际Goal手动展开原工作区，再采用才发新只读预览；编辑范围撤销旧候选', async () => {
  const { fetch } = open(); const row = repairFixture().version_previews[0]!; const current = { ...fullModelFixture(), goal_id: row.goal_id, policy_id: row.actual_existing_preview.base_policy_impact.policy_id, base_policy_version_id: row.current_version_id, epoch_id: row.actual_existing_preview.epoch_id, full_configuration: row.original_full_configuration, full_configuration_hash: row.original_full_configuration_hash };
  fetch.mockImplementation(async (url, options) => new Response(JSON.stringify(String(url).endsWith('/full-model') ? current : String(url).endsWith('/full-model/preview') ? row.actual_existing_preview : options.method === 'GET' ? conflictFixture() : repairFixture())));
  await selectRange(); fireEvent.click(screen.getByRole('button', { name: '只读预览所选修复范围' })); await screen.findByRole('region', { name: '只读修复候选结果' }); expect(fetch).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button', { name: '展开原完整目标确认工作区' })); await screen.findByText(/原服务报告模型 VERIFIED/); expect(fetch).toHaveBeenCalledTimes(3); expect(screen.queryByRole('region', { name: '完整目标双hash明确确认' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '使用修复候选并重新只读预览' })); await screen.findByRole('region', { name: '完整目标双hash明确确认' }); expect(fetch).toHaveBeenCalledTimes(4); expect(String(fetch.mock.calls[3]![0])).toMatch(/\/full-model\/preview$/); expect(JSON.parse((fetch.mock.calls[3]![1] as unknown as { body: string }).body)).toEqual(row.preview_request); expect(fetch.mock.calls.every((call) => !String(call[0]).endsWith('/confirm'))).toBe(true);
  fireEvent.change(screen.getByRole('textbox', { name: '新月max上界（整数分）' }), { target: { value: '21' } }); expect(screen.queryByRole('region', { name: '完整目标双hash明确确认' })).not.toBeInTheDocument(); expect(screen.queryByRole('region', { name: '只读修复候选结果' })).not.toBeInTheDocument();
});

test('无独立当前Goal原件时仍可读候选但不能打开确认', async () => {
  const { query, view, fetch } = open(); view.rerender(<QueryClientProvider client={query}><GoalConflictRepairPanel /></QueryClientProvider>); await selectRange(); fireEvent.click(screen.getByRole('button', { name: '只读预览所选修复范围' })); await screen.findByRole('region', { name: '只读修复候选结果' }); expect(screen.getByRole('button', { name: '展开原完整目标确认工作区' })).toBeDisabled(); expect(screen.getByText(/当前Goal原件\/版本\/策略不匹配或未提供/)).toBeVisible(); expect(fetch).toHaveBeenCalledTimes(2); expect(screen.queryByRole('region', { name: '修复候选进入原完整目标确认' })).not.toBeInTheDocument();
});
