import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import DynamicGoalReservePanel from './DynamicGoalReservePanel';
import { dynamicGoal, dynamicGoalFixture } from '../tests/dynamic-goal-fixture';
import { otherGoalId } from '../tests/full-goal-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function open() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); const view = render(<QueryClientProvider client={client}><DynamicGoalReservePanel goal={dynamicGoal} /></QueryClientProvider>); return { client, view }; }
const metric = (label: string) => within(screen.getByText(label, { selector: 'dt' }).parentElement!);

test('显示当前贡献/实际收入资格/365保护/动态累计与名义差额，条件建议不提交动作', async () => {
  const calls = installHttpFixture(() => dynamicGoalFixture()); open(); await screen.findByText(/当前月节奏已计算 · READY/); expect(metric('实际本月已贡献').getByText('¥100.01')).toBeVisible(); expect(metric('当前月动态累计目标').getByText('¥400.07')).toBeVisible(); expect(metric('原名义月target').getByText('¥300.01')).toBeVisible(); expect(metric('条件建议新增').getByText('¥300.06')).toBeVisible(); expect(metric('符合当前版本窗口的实际可用收入').getByText('¥500.09')).toBeVisible(); expect(metric('本次原365日保护下预算').getByText('¥490.07')).toBeVisible(); expect(metric('原进度').getByText(/10.00%（1000 basis points）/)).toBeVisible(); expect(screen.getByText(/原贡献不加回可用资金/)).toBeVisible(); expect(screen.getByText(/动态金额尚未接入原nominal执行或联合调度/)).toBeVisible(); expect(screen.queryByRole('button', { name: /确认|分配|执行|采用/ })).not.toBeInTheDocument(); expect(calls).toHaveLength(1); expect(calls[0]!.method).toBe('GET');
});

test.each(['UNKNOWN', 'EXPIRED', 'INACTIVE'] as const)('%s保具体来源问题且不显示0贡献/资金/旧reserve', async (state) => {
  const calls = installHttpFixture(() => dynamicGoalFixture(state)); open(); await screen.findByText(/当前月贡献、可用收入、保护预算及新增建议均尚未证明/); expect(screen.queryByText('条件建议新增')).not.toBeInTheDocument(); expect(screen.queryByText(/¥0.00/)).not.toBeInTheDocument(); expect(within(screen.getByRole('list', { name: '动态节奏来源问题' })).getByText(/UNIT_DYNAMIC_SOURCE_NOT_PROVEN/)).toBeVisible(); expect(calls).toHaveLength(1);
});

test('硬保证短缺条件建议null不填0；已逾期字段是原实际观察而非完成预测', async () => {
  const source = dynamicGoalFixture('COMPUTED', 'HARD_GUARANTEE_SHORTFALL'); source.reserve!.overdue_days = 3; source.reserve!.deferral_cost_to_date_cents = 303; installHttpFixture(() => source); open(); await screen.findByText(/硬最低保证短缺，追加金额未知/); expect(metric('条件建议新增').getByText('UNKNOWN · 尚未证明')).toBeVisible(); expect(metric('实际已逾期').getByText('3日')).toBeVisible(); expect(metric('截至现在的原延期成本').getByText('¥3.03')).toBeVisible(); expect(metric('实际完成日期').getByText('未提供，不预测或重建完成历史')).toBeVisible();
});

test('只读刷新实际贡献后的新报告少建议，不自动再次分配；原文本可查看', async () => {
  const source = dynamicGoalFixture(); const after = dynamicGoalFixture(); after.reserve!.current_owned_cents = 30003; after.reserve!.current_month_contributed_cents = 20001; after.reserve!.remaining_goal_cents = 170027; after.reserve!.eligible_available_income_cents = 40009; after.reserve!.independently_protected_budget_cents = 39007; after.reserve!.desired_additional_cents = 20006; after.reserve!.suggested_additional_cents = 20006; after.reserve!.progress_basis_points = 1499; let reads = 0; const original = ` \n${JSON.stringify(after)}\n`; const calls = installHttpFixture(() => ++reads === 1 ? source : new Response(original)); open(); await screen.findByText(/当前月节奏已计算 · READY/); fireEvent.click(screen.getByRole('button', { name: '只读刷新动态节奏' })); await metric('实际本月已贡献').findByText('¥200.01'); expect(metric('条件建议新增').getByText('¥200.06')).toBeVisible(); expect(metric('当前月动态累计目标').getByText('¥400.07')).toBeVisible(); fireEvent.click(screen.getByText('动态节奏原JSON响应')); expect(screen.getByText(JSON.stringify(after), { selector: 'pre', exact: false }).textContent).toBe(original); expect(calls).toHaveLength(2); expect(calls.every((call) => call.method === 'GET' && call.body === undefined)).toBe(true);
});

test('prop当前版本变化时新GET身份拒绝，不让旧节奏冒当前版本，无自动retry', async () => {
  const calls = installHttpFixture(() => dynamicGoalFixture()); const { client, view } = open(); await screen.findByText(/当前月节奏已计算 · READY/); view.rerender(<QueryClientProvider client={client}><DynamicGoalReservePanel goal={{ ...dynamicGoal, policy_version_id: otherGoalId }} /></QueryClientProvider>); await screen.findByRole('alert'); expect(screen.queryByText('条件建议新增')).not.toBeInTheDocument(); expect(screen.queryByText(/当前月节奏已计算 · READY/)).not.toBeInTheDocument(); expect(calls).toHaveLength(2);
});


test('本月已超新上限仍显示原贡献，不回退旧金额或再次分配；零建议是已计算结果', async () => {
  const source = dynamicGoalFixture(); Object.assign(source.reserve!, { status: 'MONTHLY_MAX_ALREADY_EXCEEDED', current_owned_cents: 45007, current_month_contributed_cents: 45007, remaining_goal_cents: 155023, progress_basis_points: 2249, uncapped_gross_pace_cents: 66677, desired_additional_cents: 0, suggested_additional_cents: 0 }); const calls = installHttpFixture(() => source); open(); await screen.findByText(/实际本月贡献已超新上限，保留旧贡献不再追加/); expect(metric('实际本月已贡献').getByText('¥450.07')).toBeVisible(); expect(metric('当前月动态累计目标').getByText('¥400.07')).toBeVisible(); expect(metric('条件建议新增').getByText('¥0.00')).toBeVisible(); expect(calls).toHaveLength(1); expect(calls[0]!.method).toBe('GET');
});
