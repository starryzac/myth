import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import type { components } from '../../../../packages/contracts/schema';
import GoalsPage from './GoalsPage';
import { dashboardFixture } from '../tests/dashboard-fixture';
import { installHttpFixture, policyFixture, policyId, versionId } from '../tests/policy-fixture';
import { actionFixture, demoActionId, time } from '../tests/demo-fixture';
import type { DemoAction } from '../api/demo';
import { fullGoalIntentFixture, fullGoalLookupFixture, fullModelFixture, jointFixture, modelGoal } from '../tests/full-goal-fixture';
import { beginFullGoalOperation, clearFullGoalOperationAfterLookup, endFullGoalAttempt, getFullGoalOperation } from '../features/full-goal-operation';
import { dynamicGoalFixture } from '../tests/dynamic-goal-fixture';
import { fullJointFixture, fullJointGoalFixture } from '../tests/full-joint-fixture';
import { repairBinding, repairFixture, repairPolicyFixture, repairRequestFixture } from '../tests/goal-reallocation-fixture';
import { conflictFixture, conflictGoals } from '../tests/goal-conflict-fixture';
import { beginGoalReleaseAuthorizationOperation, clearGoalReleaseAuthorizationAfterLookup, endGoalReleaseAuthorizationAttempt, getGoalReleaseAuthorizationOperation } from '../features/goal-release-authorization-operation';
import { releaseIntentFixture, releaseLookupFixture } from '../tests/goal-release-authorization-fixture';
afterEach(async () => { endGoalReleaseAuthorizationAttempt(); const release = getGoalReleaseAuthorizationOperation().pending; if (release) await clearGoalReleaseAuthorizationAfterLookup(release, await releaseLookupFixture(release)); endFullGoalAttempt(); const original = getFullGoalOperation().pending; if (original) await clearFullGoalOperationAfterLookup(original, fullGoalLookupFixture(original)); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function openPage(props: { mutationBlocked?: boolean; modelBlocked?: boolean } = {}) { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><GoalsPage {...props} /></QueryClientProvider>); }
function goalFixture(): components['schemas']['GoalView'] {
  return { id: '10000000-0000-0000-0000-000000000060', policy_id: policyId, policy_version_id: versionId,
    account_id: dashboardFixture().account_facts.facts.accounts[0]!.id, name: '旅行目标', target_cents: 120000, allocated_cents: 999999,
    deadline: '2027-01-01', monthly_min_cents: 0, monthly_target_cents: 20000, monthly_max_cents: 30000,
    importance: 50, minimum_protection_cents: 0, reducible: false, deferrable: false, cross_goal_reallocation_allowed: false, asset_policy_id: null };
}

test('原授权目标不在当前列表时仍可GET原epoch键，撤销后原记录匹配才解门', async () => {
  vi.stubGlobal('crypto', webcrypto);
  const original = await releaseIntentFixture('GOALS_ORIGINAL_RELEASE:1');
  const lookup = await releaseLookupFixture(original, true, 'STALE');
  await beginGoalReleaseAuthorizationOperation(original); endGoalReleaseAuthorizationAttempt();
  const calls = installHttpFixture((_method, path) => {
    if (path.includes('/goal-release-authorizations/commands/')) return lookup;
    if (path === '/api/v1/full-policies') return { simulation: true, items: [] };
    if (path === '/api/v1/goals' || path === '/api/v1/policies') return { simulation: true, items: [] };
    return {};
  });
  openPage({ mutationBlocked: true, modelBlocked: true });
  const area = await screen.findByRole('region', { name: '列表外原回拨授权恢复' });
  const button = await within(area).findByRole('button', { name: '只读核对原专用授权' });
  expect(button).toBeEnabled(); fireEvent.click(button);
  await within(area).findByRole('heading', { name: '专用授权原记录 · STALE' });
  await waitFor(() => expect(getGoalReleaseAuthorizationOperation().pending).toBeNull());
  expect(calls.filter((call) => call.path.includes('/goal-release-authorizations/commands/'))).toEqual([{ method: 'GET', path: `/api/v1/goal-release-authorizations/commands/${original.body.expected_epoch_id}/by-key/${encodeURIComponent(original.body.idempotency_key)}`, body: undefined }]);
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
});

test('现金回拨预览惰性读取原规则并越过金融写门，只有主动只读预览不授权限', async () => {
  const goal = { ...goalFixture(), ...repairBinding.goal };
  const dashboard = dashboardFixture();
  dashboard.user_id = repairBinding.userId;
  dashboard.account_facts.facts.user_id = repairBinding.userId;
  dashboard.audit.epoch_id = repairBinding.epochId;
  const calls = installHttpFixture((method, path) => {
    if (path === '/api/v1/policies') return { simulation: true, items: [] };
    if (path === '/api/v1/goals') return { simulation: true, items: [goal] };
    if (path === '/api/v1/accounts/summary') return dashboard.account_facts.facts;
    if (path === '/api/v1/dashboard') return dashboard;
    if (path === '/api/v1/positions' || path === '/api/v1/products') return { simulation: true, items: [] };
    if (method === 'GET' && path === '/api/v1/full-policies') return { simulation: true, bank_authority: false, dedicated_audit_event: false, items: [repairPolicyFixture()] };
    if (method === 'POST' && path === '/api/v1/goal-reallocation/preview') return repairFixture();
    throw new Error(`unexpected ${method} ${path}`);
  });
  openPage({ mutationBlocked: true, modelBlocked: true });
  const card = await screen.findByRole('article', { name: `目标 ${goal.name}` });
  expect(calls.some((call) => call.path === '/api/v1/full-policies')).toBe(false);
  expect(within(card).getByRole('button', { name: '查看当前收入分配预览' })).toBeDisabled();
  const detail = within(card).getByText('查看紧急回拨规则与当前修复下界').closest('details')!;
  detail.open = true; fireEvent(detail, new Event('toggle', { bubbles: true }));
  fireEvent.change(await within(card).findByLabelText('选择原CrossGoalReallocationPolicy'), { target: { value: repairPolicyFixture().policy_id } });
  const button = within(card).getByRole('button', { name: '只读预览当前期修复下界' });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await within(card).findByRole('heading', { name: '原当前期回拨预览 · UNKNOWN' });
  expect(within(card).getByText(/candidate_amount_cents=null/)).toBeVisible();
  expect(calls.filter((call) => call.method !== 'GET')).toEqual([{ method: 'POST', path: '/api/v1/goal-reallocation/preview', body: repairRequestFixture() }]);
  expect(within(card).queryByRole('button', { name: /执行回拨|确认回拨|创建回拨/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '刷新目标' }));
  await waitFor(() => expect(calls.filter((call) => call.path === '/api/v1/full-policies')).toHaveLength(2));
  expect(calls.filter((call) => call.method !== 'GET')).toHaveLength(1);
});

test('最小冲突在父页金融门关闭时仍可GET，限定修复需要当前可用的用户请求门', async () => {
  const goals = conflictGoals(); const dashboard = dashboardFixture();
  const calls = installHttpFixture((method, path) => {
    if (path === '/api/v1/policies') return { simulation: true, items: [] };
    if (path === '/api/v1/goals') return { simulation: true, items: goals };
    if (path === '/api/v1/accounts/summary') return dashboard.account_facts.facts;
    if (path === '/api/v1/dashboard') return dashboard;
    if (path === '/api/v1/positions' || path === '/api/v1/products') return { simulation: true, items: [] };
    if (method === 'GET' && path === '/api/v1/planning/full-goal-conflicts') return conflictFixture();
    throw new Error(`unexpected ${method} ${path}`);
  });
  openPage({ mutationBlocked: true, modelBlocked: true });
  await screen.findByRole('article', { name: `目标 ${goals[0]!.name}` });
  expect(calls.some((call) => call.path.endsWith('/full-goal-conflicts'))).toBe(false);
  const detail = screen.getByText('查看目标最小冲突与限定修复预览').closest('details')!;
  detail.open = true; fireEvent(detail, new Event('toggle', { bubbles: true }));
  const region = await screen.findByRole('region', { name: '逐项移除原反事实见证' });
  expect(region).toBeVisible();
  const select = screen.getByRole('checkbox', { name: /选择.*月max新范围/ });
  expect(select).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '刷新目标' }));
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/full-goal-conflicts'))).toHaveLength(2));
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
});

test('完整保护联合规划惰性GET仍可越过写门，刷新重取且不授予执行', async () => {
  const goal = fullJointGoalFixture(); const dashboard = dashboardFixture();
  const requests = installHttpFixture((method, path) => {
    if (path === '/api/v1/policies') return { simulation: true, items: [] };
    if (path === '/api/v1/goals') return { simulation: true, items: [goal] };
    if (path === '/api/v1/accounts/summary') return dashboard.account_facts.facts;
    if (path === '/api/v1/dashboard') return dashboard;
    if (path === '/api/v1/positions' || path === '/api/v1/products') return { simulation: true, items: [] };
    if (method === 'GET' && path === '/api/v1/planning/full-current-goal-allocation') return fullJointFixture();
    throw new Error(`unexpected ${method} ${path}`);
  });
  openPage({ mutationBlocked: true, modelBlocked: true });
  await screen.findByRole('article', { name: `目标 ${goal.name}` });
  expect(requests.some((row) => row.path.endsWith('/full-current-goal-allocation'))).toBe(false);
  const detail = screen.getByText('查看完整支出保护下的联合目标规划').closest('details')!;
  detail.open = true; fireEvent(detail, new Event('toggle', { bubbles: true }));
  const panel = await screen.findByRole('region', { name: '完整保护当前期目标规划' });
  await waitFor(() => expect(panel).toHaveTextContent('¥120.02'));
  expect(within(panel).getByRole('button', { name: '只读刷新Full联合规划' })).toBeEnabled();
  expect(panel).toHaveTextContent('不提交行动');
  fireEvent.click(screen.getByRole('button', { name: '刷新目标' }));
  await waitFor(() => expect(requests.filter((row) => row.path.endsWith('/full-current-goal-allocation'))).toHaveLength(2));
  expect(requests.every((row) => row.method === 'GET')).toBe(true);
});

test('动态储备惰性只读仍可越过资金写门，刷新按真实当前Goal版本重新GET', async () => {
  const goal = { ...goalFixture(), ...modelGoal }; const dashboard = dashboardFixture();
  const requests = installHttpFixture((method, path) => {
    if (path === '/api/v1/policies') return { simulation: true, items: [] };
    if (path === '/api/v1/goals') return { simulation: true, items: [goal] };
    if (path === '/api/v1/accounts/summary') return dashboard.account_facts.facts;
    if (path === '/api/v1/dashboard') return dashboard;
    if (path === '/api/v1/positions' || path === '/api/v1/products') return { simulation: true, items: [] };
    if (method === 'GET' && path === `/api/v1/goals/${goal.id}/dynamic-reserve`) return dynamicGoalFixture();
    throw new Error(`unexpected ${method} ${path}`);
  });
  openPage({ mutationBlocked: true, modelBlocked: true });
  const card = await screen.findByRole('article', { name: `目标 ${goal.name}` });
  expect(within(card).getByRole('button', { name: '查看当前收入分配预览' })).toBeDisabled();
  expect(requests.some((row) => row.path.endsWith('/dynamic-reserve'))).toBe(false);
  const detail = within(card).getByText('查看动态月储备与真实进度').closest('details')!;
  detail.open = true; fireEvent(detail, new Event('toggle', { bubbles: true }));
  const panel = await screen.findByRole('region', { name: `目标动态节奏 ${goal.id}` });
  await waitFor(() => expect(panel).toHaveTextContent('当前月节奏已计算'));
  expect(within(panel).getByRole('button', { name: '只读刷新动态节奏' })).toBeEnabled();
  expect(panel).toHaveTextContent('动态金额尚未接入');
  fireEvent.click(screen.getByRole('button', { name: '刷新目标' }));
  await waitFor(() => expect(requests.filter((row) => row.path.endsWith('/dynamic-reserve'))).toHaveLength(2));
  expect(requests.every((row) => row.method === 'GET')).toBe(true);
});

test('完整Goal原pending仅阻挡金融分配，完整模型自身原键GET仍可恢复且不POST', async () => {
  vi.stubGlobal('crypto', webcrypto); const original = await fullGoalIntentFixture('GOALS_PAGE_ORIGINAL/1');
  beginFullGoalOperation(original); endFullGoalAttempt();
  const goal = { ...goalFixture(), ...modelGoal }; const dashboard = dashboardFixture();
  const calls = installHttpFixture((method, path) => {
    if (path === '/api/v1/policies') return { simulation: true, items: [] };
    if (path === '/api/v1/goals') return { simulation: true, items: [goal] };
    if (path === '/api/v1/accounts/summary') return dashboard.account_facts.facts;
    if (path === '/api/v1/dashboard') return dashboard;
    if (path === '/api/v1/positions' || path === '/api/v1/products') return { simulation: true, items: [] };
    if (method === 'GET' && path === `/api/v1/goals/${goal.id}/full-model`) return fullModelFixture();
    if (method === 'GET' && path.includes('/full-model/commands/by-key/')) return fullGoalLookupFixture(original);
    throw new Error(`unexpected ${method} ${path}`);
  });
  openPage({ mutationBlocked: true }); const card = await screen.findByRole('article', { name: `目标 ${goal.name}` });
  expect(within(card).getByRole('button', { name: '查看当前收入分配预览' })).toBeDisabled();
  const detail = within(card).getByText('查看完整目标模型与只读预览').closest('details')!;
  detail.open = true; fireEvent(detail, new Event('toggle', { bubbles: true }));
  const button = await screen.findByRole('button', { name: '只读核对原完整目标确认' });
  expect(button).toBeEnabled(); fireEvent.click(button);
  await waitFor(() => expect(getFullGoalOperation().pending).toBeNull());
  expect(calls.filter((call) => call.path.includes('/commands/by-key/'))).toHaveLength(1);
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
test('展开完整模型和联合规划才读取原只读接口，缺模型保持UNKNOWN且不触发确认', async () => {
  const goal = goalFixture(); const dashboard = dashboardFixture();
  const requests = installHttpFixture((method, path) => {
    if (path === '/api/v1/policies') return { simulation: true, items: [policyFixture('goal_saving')] };
    if (path === '/api/v1/goals') return { simulation: true, items: [goal] };
    if (path === '/api/v1/accounts/summary') return dashboard.account_facts.facts;
    if (path === '/api/v1/dashboard') return dashboard;
    if (path === '/api/v1/positions' || path === '/api/v1/products') return { simulation: true, items: [] };
    if (method === 'GET' && path === `/api/v1/goals/${goal.id}/full-model`) return { ...fullModelFixture(true), goal_id: goal.id, policy_id: goal.policy_id, base_policy_version_id: goal.policy_version_id };
    if (method === 'GET' && path === '/api/v1/planning/current-goal-allocation') return jointFixture('UNKNOWN');
    throw new Error(`unexpected ${method} ${path}`);
  });
  openPage(); const card = await screen.findByRole('article', { name: '目标 旅行目标' });
  expect(requests.some((item) => item.path.endsWith('/full-model') || item.path.endsWith('/current-goal-allocation'))).toBe(false);
  const model = within(card).getByText('查看完整目标模型与只读预览').closest('details')!;
  model.open = true; fireEvent(model, new Event('toggle', { bubbles: true }));
  const modelSection = await screen.findByRole('region', { name: `完整目标模型 ${goal.id}` });
  await waitFor(() => expect(modelSection).toHaveTextContent('MODEL_MISSING'));
  const joint = screen.getByText('查看当前期联合目标规划').closest('details')!;
  joint.open = true; fireEvent(joint, new Event('toggle', { bubbles: true }));
  const jointSection = await screen.findByRole('region', { name: '当前期联合目标规划' });
  await waitFor(() => expect(jointSection).toHaveTextContent('MISSING_FULL_GOAL_MODEL'));
  expect(jointSection).toHaveTextContent('UNKNOWN');
  expect(requests.filter((item) => item.method !== 'GET')).toEqual([]);
});
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
  expect(select).toHaveAccessibleName('目标归属账户');
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

function allocationFixture(level: 'AUTO_EXECUTE' | 'ASK_ONCE' = 'AUTO_EXECUTE') {
  const goal = goalFixture(); goal.allocated_cents = 0;
  const action = actionFixture(); action.autonomy_level = level;
  action.effect = { ...action.effect, action_type: 'ALLOCATE_GOAL', goal_id: goal.id, policy_id: goal.policy_id, policy_version_id: goal.policy_version_id,
    destination_account_id: goal.account_id, amount_cents: 20000, fee_cents: 0, loss_cents: 0, net_cents: null };
  action.prepared_validation.status = level === 'AUTO_EXECUTE' ? 'READY' : 'CONFIRMATION_REQUIRED';
  return { goal, action, dashboard: dashboardFixture() };
}
function succeed(action: DemoAction) {
  action.status = 'SUCCEEDED'; action.bank_status = 'SETTLED';
  action.receipt = { simulation: true, receipt_id: demoActionId, action_id: action.action_id, bank_operation_id: action.action_id, status: 'SUCCEEDED',
    executed_cents: action.effect.amount_cents, fee_cents: 0, loss_cents: 0, posting_ids: [policyId, versionId], occurred_at: time, reconciled_at: time };
}
function installAllocation(fixture: ReturnType<typeof allocationFixture>, command: (method: string, path: string, body: unknown) => unknown) {
  return installHttpFixture((method, path, body) => {
    if (path.startsWith('/api/v1/actions/')) return command(method, path, body);
    if (path === '/api/v1/policies') return { simulation: true, items: [policyFixture('goal_saving')] };
    if (path === '/api/v1/goals') return { simulation: true, items: [fixture.goal] };
    if (path === '/api/v1/dashboard') return fixture.dashboard;
    if (path === '/api/v1/accounts/summary') return fixture.dashboard.account_facts.facts;
    if (path.endsWith('/allocation-preview')) return { simulation: true, allocation: { status: 'READY', suggested_cents: 20000, max_safe_cents: 30000, eligible_new_funds_cents: 40000, minimum_shortfall_cents: 0, reasons: [] } };
    return { simulation: true, items: [] };
  });
}
async function openAllocation() {
  openPage(); const card = await screen.findByRole('article', { name: '目标 旅行目标' });
  fireEvent.click(within(card).getByRole('button', { name: '查看当前收入分配预览' }));
  const prepare = await screen.findByRole('button', { name: '准备当前收入分配' });
  await waitFor(() => expect(prepare).toBeEnabled()); return prepare;
}
test('后续收入分配仅准备GoalIntent，复核原服务结果后实际执行，准备不自动执行', async () => {
  const fixture = allocationFixture();
  const requests = installAllocation(fixture, (method, path) => {
    if (method === 'POST' && path.endsWith('/execute')) succeed(fixture.action);
    return fixture.action;
  });
  fireEvent.click(await openAllocation());
  const execute = await screen.findByRole('button', { name: '执行已复核的原分配' }); await waitFor(() => expect(execute).toBeEnabled());
  const preparation = requests.find((item) => item.path === '/api/v1/actions/prepare')!;
  expect(preparation.body).toEqual({ idempotency_key: expect.stringMatching(/^goal-action:/), intent: { kind: 'allocate_goal', goal_id: fixture.goal.id } });
  expect(requests.filter((item) => item.method === 'POST')).toHaveLength(1);
  expect(screen.getByRole('region', { name: `原动作经济后果 ${fixture.action.action_id}` })).toHaveTextContent(fixture.action.effect_hash);
  fireEvent.click(execute); await screen.findByText(/实际原回执/);
  expect(requests.filter((item) => item.method === 'POST').map((item) => item.path)).toEqual(['/api/v1/actions/prepare', `/api/v1/actions/${fixture.action.action_id}/execute`]);
  expect(requests.find((item) => item.path.endsWith('/execute'))!.body).toEqual({});
});
test('ASK原分配需要用户具体复核，确认严格绑定同一action和effect摘要', async () => {
  const fixture = allocationFixture('ASK_ONCE');
  const requests = installAllocation(fixture, (method, path) => {
    if (method === 'POST' && path.endsWith('/confirm')) fixture.action.status = 'AUTHORIZED';
    if (method === 'POST' && path.endsWith('/execute')) succeed(fixture.action);
    return fixture.action;
  });
  fireEvent.click(await openAllocation()); const execute = await screen.findByRole('button', { name: '具体确认并执行原分配' });
  expect(execute).toBeDisabled(); const checkbox = screen.getByRole('checkbox'); expect(checkbox).not.toBeChecked();
  expect(requests.filter((item) => item.method === 'POST')).toHaveLength(1);
  fireEvent.click(checkbox); await waitFor(() => expect(execute).toBeEnabled()); fireEvent.click(execute);
  await screen.findByText(/实际原回执/);
  expect(requests.find((item) => item.path.endsWith('/confirm'))!.body).toEqual({ effect_hash: fixture.action.effect_hash, accepted: true });
  expect(requests.filter((item) => item.method === 'POST').map((item) => item.path)).toEqual(['/api/v1/actions/prepare', `/api/v1/actions/${fixture.action.action_id}/confirm`, `/api/v1/actions/${fixture.action.action_id}/execute`]);
});
test('UNKNOWN保留原身份，重建页面必须GET原件后只恢复同action，不生成新prepare键', async () => {
  const fixture = allocationFixture(); let executions = 0;
  const requests = installAllocation(fixture, (method, path) => {
    if (method === 'POST' && path.endsWith('/execute')) {
      executions += 1;
      if (executions === 1) { fixture.action.status = 'UNKNOWN'; fixture.action.bank_status = 'UNKNOWN'; }
      else succeed(fixture.action);
    }
    return fixture.action;
  });
  fireEvent.click(await openAllocation()); const execute = await screen.findByRole('button', { name: '执行已复核的原分配' });
  await waitFor(() => expect(execute).toBeEnabled()); fireEvent.click(execute); await screen.findByRole('button', { name: '核对并恢复原分配' });
  const key = Object.keys(sessionStorage).find((item) => item.startsWith('bounded-funds-goal-action-v1:'))!;
  const original = JSON.parse(sessionStorage.getItem(key)!);
  expect(Object.keys(original).sort()).toEqual(['action_id', 'effect_hash', 'goal_id', 'idempotency_key']);
  expect(original.action_id).toBe(fixture.action.action_id); expect(original.effect_hash).toBe(fixture.action.effect_hash);
  cleanup(); openPage(); const card = await screen.findByRole('article', { name: '目标 旅行目标' });
  fireEvent.click(within(card).getByRole('button', { name: '查看当前收入分配预览' }));
  expect(screen.queryByRole('button', { name: '核对并恢复原分配' })).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole('button', { name: '读取原分配' })); const resume = await screen.findByRole('button', { name: '核对并恢复原分配' });
  await waitFor(() => expect(resume).toBeEnabled()); fireEvent.click(resume); await screen.findByText(/实际原回执/);
  expect(requests.filter((item) => item.path === '/api/v1/actions/prepare')).toHaveLength(1);
  expect(requests.filter((item) => item.path.endsWith('/execute')).map((item) => item.path)).toEqual([`/api/v1/actions/${fixture.action.action_id}/execute`, `/api/v1/actions/${fixture.action.action_id}/execute`]);
});
test('原准备响应丢失只重试同一键，存储失败不得先发出金融请求', async () => {
  const fixture = allocationFixture(); let prepares = 0;
  const requests = installAllocation(fixture, (_method, path) => {
    if (path === '/api/v1/actions/prepare') { prepares += 1; if (prepares === 1) throw new TypeError('unit lost response'); }
    return fixture.action;
  });
  fireEvent.click(await openAllocation()); const retry = await screen.findByRole('button', { name: '重试原分配准备' });
  await screen.findByRole('alert'); await waitFor(() => expect(retry).toBeEnabled()); fireEvent.click(retry);
  await screen.findByRole('button', { name: '执行已复核的原分配' });
  const bodies = requests.filter((item) => item.path === '/api/v1/actions/prepare').map((item) => item.body);
  expect(bodies).toHaveLength(2); expect(bodies[0]).toEqual(bodies[1]);
  cleanup(); sessionStorage.clear(); const next = allocationFixture();
  const secondRequests = installAllocation(next, () => next.action);
  const preparation = await openAllocation(); vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('unit storage refused'); });
  fireEvent.click(preparation); await screen.findAllByRole('alert');
  expect(secondRequests.some((item) => item.method === 'POST')).toBe(false);
});
test('执行前重新读取的原摘要变化不得沿用已勾选确认或发送执行', async () => {
  const fixture = allocationFixture('ASK_ONCE'); const originalHash = fixture.action.effect_hash; let reads = 0;
  const requests = installAllocation(fixture, (method) => {
    if (method === 'GET') {
      reads += 1;
      if (reads > 1) { fixture.action.effect_hash = 'f'.repeat(64); fixture.action.prepared_validation.effect_hash = fixture.action.effect_hash; }
    }
    return fixture.action;
  });
  fireEvent.click(await openAllocation()); const execute = await screen.findByRole('button', { name: '具体确认并执行原分配' });
  fireEvent.click(screen.getByRole('checkbox')); await waitFor(() => expect(execute).toBeEnabled()); fireEvent.click(execute);
  await screen.findByRole('alert'); expect(requests.filter((item) => item.method === 'POST').map((item) => item.path)).toEqual(['/api/v1/actions/prepare']);
  const key = Object.keys(sessionStorage).find((item) => item.startsWith('bounded-funds-goal-action-v1:'))!;
  expect(JSON.parse(sessionStorage.getItem(key)!).effect_hash).toBe(originalHash);
});
test('其他目标的待核对身份阻止新准备，恢复存储不能夹带授权或结果', async () => {
  const fixture = allocationFixture(); const requests = installAllocation(fixture, () => fixture.action);
  const key = `bounded-funds-goal-action-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
  const other = { goal_id: '10000000-0000-0000-0000-000000000070', idempotency_key: 'goal-action:10000000-0000-0000-0000-000000000071' };
  sessionStorage.setItem(key, JSON.stringify(other));
  openPage(); const card = await screen.findByRole('article', { name: '目标 旅行目标' }); fireEvent.click(within(card).getByRole('button', { name: '查看当前收入分配预览' }));
  await screen.findByText(/原分配目标暂不在当前列表/); await screen.findByText(/已核验新收入/);
  await waitFor(() => expect(screen.queryByText('正在读取预览…')).not.toBeInTheDocument());
  expect(await screen.findByRole('button', { name: '准备当前收入分配' })).toBeDisabled();
  expect(requests.some((item) => item.method === 'POST')).toBe(false);
  cleanup(); sessionStorage.setItem(key, JSON.stringify({ ...other, autonomy_level: 'AUTO_EXECUTE', accepted: true, balance_cents: 999999 })); openPage();
  await screen.findByRole('alert'); expect(sessionStorage.getItem(key)).toContain('accepted');
  expect(requests.some((item) => item.method === 'POST')).toBe(false);
});
