import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import DemoConsolePage from './DemoConsolePage';
import { hash, installHttpFixture, lifecycleFixture, policyId, previewFixture, versionId } from '../tests/policy-fixture';
import { beginDemoOperation, endDemoOperation, getDemoOperation, useDemoOperation } from '../features/demo-operation';
import { actionFixture, commandFixture, demoActionId, demoProposalId, epochId, newEpochId, presetsFixture, stateFixture } from '../tests/demo-fixture';
import type { DemoState } from '../api/demo';
afterEach(() => { endDemoOperation(true); sessionStorage.clear(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function Shell({ mutationBlocked = false }: { mutationBlocked?: boolean }) { const operation = useDemoOperation(); return <DemoConsolePage key={operation.read_generation} mutationBlocked={mutationBlocked} />; }
function openPage(mutationBlocked = false) { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><Shell mutationBlocked={mutationBlocked} /></QueryClientProvider>); }
function failed() { return new Response(JSON.stringify({ error: { code: 'UNIT_UNAVAILABLE', message: '单元HTTP夹具：控制台尚不可读取', request_id: 'unit-demo-error' } }), { status: 503 }); }
test('预设或实际状态未读成功时保留错误，不显示伪成功或发事件POST', async () => {
  const requests = installHttpFixture(() => failed()); openPage();
  expect(screen.getByRole('heading', { name: '演示控制台' })).toBeVisible(); await screen.findAllByRole('alert');
  expect(requests.every((item) => item.method === 'GET')).toBe(true); expect(screen.queryByText('演示成功')).not.toBeInTheDocument();
});
test('原身份等待核对跨页面保留，加载控制台不会自行POST或产生新键', async () => {
  const requests = installHttpFixture(() => failed()); const original = { kind: 'event' as const, epoch_id: '10000000-0000-0000-0000-000000000080', event_kind: 'SALARY_RECEIVED' };
  beginDemoOperation(original); endDemoOperation(false); openPage(); await screen.findAllByRole('alert');
  expect(requests.every((item) => item.method === 'GET')).toBe(true); expect(screen.getByText(/原命令结果尚待核对/)).toBeVisible();
});
function fixture(state: DemoState, write: (method: string, path: string, body: unknown) => unknown) {
  return installHttpFixture((method, path, body) => path === '/api/v1/demo/presets' ? presetsFixture() : path === '/api/v1/demo/state' ? state : write(method, path, body));
}

test('其他完整版原请求门阻止事件、模板和重置，实际状态GET仍可读取', async () => {
  const requests = fixture(stateFixture(), () => { throw new Error('不应提交另一个原请求'); });
  openPage(true); await screen.findByRole('button', { name: '注入事件：工资到账' });
  expect(screen.getByRole('button', { name: '注入事件：工资到账' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '生成候选：单元买车目标' })).toBeDisabled();
  const before = requests.filter((item) => item.path.endsWith('/state')).length;
  fireEvent.click(screen.getByRole('button', { name: '读取实际演示状态' }));
  await waitFor(() => expect(requests.filter((item) => item.path.endsWith('/state')).length).toBeGreaterThan(before));
  expect(requests.every((item) => item.method === 'GET')).toBe(true);
});
test('显式无epoch仍可复核重置；新轮次重新GET且模板授权为空，不把原摘要当当前余额', async () => {
  let state: DemoState = { ...stateFixture(), epoch_id: null, available: false }; const requests = installHttpFixture((method, path) => {
    if (path.endsWith('/presets')) return presetsFixture(); if (path.endsWith('/state')) return state;
    if (path.endsWith('/reset')) { state = { ...stateFixture(), epoch_id: newEpochId }; return { simulation: true, reset_key: String((requests.at(-1)!.body as { reset_key: string }).reset_key), reset_epoch_id: epochId, epoch_id: newEpochId, seed_summary: { balance_cents: 99999 } }; }
    throw new Error(`unexpected unit ${method} ${path}`);
  }); openPage(); const reset = await screen.findByRole('button', { name: '恢复演示初始状态' });
  expect(screen.getByRole('button', { name: '注入事件：工资到账' })).toBeDisabled(); fireEvent.click(reset);
  const review = screen.getByRole('region', { name: '重置明确确认' }); expect(within(review).getByRole('button', { name: '明确确认重置' })).toBeDisabled();
  fireEvent.click(within(review).getByRole('checkbox')); fireEvent.click(within(review).getByRole('button', { name: '明确确认重置' }));
  await screen.findByText(`实际轮次 ${newEpochId}`); expect(requests.find((item) => item.path.endsWith('/reset'))!.body).toMatchObject({ expected_epoch_id: null, accepted: true });
  const receipt = screen.getByRole('region', { name: '原重置回执' }); expect(receipt).toHaveTextContent(epochId); expect(receipt).toHaveTextContent(newEpochId); expect(receipt).toHaveTextContent('历史回执'); expect(receipt).not.toHaveTextContent('¥999.99');
  expect(screen.getAllByRole('button', { name: /生成候选/ })).toHaveLength(4); expect(requests.filter((item) => item.method === 'POST')).toHaveLength(1);
});
test('模板候选生成不自动授权，用户复核完整字段后只确认原proposal和hash', async () => {
  const state = stateFixture(); const template = state.templates[0]!;
  const requests = fixture(state, (_method, path) => {
    if (path.endsWith('/CAR_GOAL/prepare')) { template.proposal_id = demoProposalId; template.evidence_id = demoProposalId; template.status = 'PROPOSED'; return template; }
    if (path.endsWith(`/${demoProposalId}/confirm`)) { template.confirmed_policy_id = policyId; template.status = 'CONFIRMED'; return lifecycleFixture(); }
    throw new Error(`unexpected unit ${path}`);
  }); openPage(); fireEvent.click(await screen.findByRole('button', { name: '生成候选：单元买车目标' }));
  const button = await screen.findByRole('button', { name: '确认模板：单元买车目标' }); expect(button).toBeDisabled();
  expect(requests.filter((item) => item.method === 'POST')).toHaveLength(1); const card = screen.getByRole('article', { name: '演示模板 单元买车目标' }); expect(card).toHaveTextContent('¥30,000.00');
  fireEvent.click(within(card).getByRole('checkbox')); fireEvent.click(button); await screen.findByText(`本轮已确认策略 ${policyId}`);
  expect(requests.find((item) => item.path.endsWith('/confirm'))!.body).toEqual({ accepted: true, reviewed_hash: hash });
});
test('断网保留事件原epoch，禁止新事件，允许GET和显式重试同一请求', async () => {
  const state = stateFixture(); let count = 0; const requests = fixture(state, (_method, path) => {
    if (path.endsWith('/events')) { if (++count === 1) throw new Error('unit response lost'); const command = commandFixture(); state.commands = [command]; return command; }
    throw new Error(`unexpected unit ${path}`);
  }); openPage(); fireEvent.click(await screen.findByRole('button', { name: '注入事件：工资到账' })); await screen.findByText(/连接中断/);
  expect(getDemoOperation().pending).toEqual({ kind: 'event', epoch_id: epochId, event_kind: 'SALARY_RECEIVED' }); expect(screen.getByRole('button', { name: '注入事件：用户大额消费' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '读取实际演示状态' })); await waitFor(() => expect(requests.filter((item) => item.path.endsWith('/state')).length).toBeGreaterThan(1));
  expect(requests.filter((item) => item.method === 'POST')).toHaveLength(1); fireEvent.click(screen.getByRole('button', { name: '注入事件：工资到账' })); await screen.findByRole('heading', { name: '等待明确确认模板' });
  const bodies = requests.filter((item) => item.method === 'POST').map((item) => item.body); expect(bodies).toEqual([{ event_kind: 'SALARY_RECEIVED', expected_epoch_id: epochId }, { event_kind: 'SALARY_RECEIVED', expected_epoch_id: epochId }]);
  expect(getDemoOperation().pending).toBeNull();
});
test('有损动作具体确认后UNKNOWN保留原id/hash；再次恢复只执行原动作且不新prepare', async () => {
  const state = stateFixture(); const action = actionFixture(); const command = commandFixture('FIXED_EARLY_WITHDRAWAL'); command.status = 'WAITING_ACTION_CONFIRMATION'; command.actions = [action]; state.commands = [command]; let executions = 0;
  const requests = fixture(state, (_method, path) => {
    if (path.endsWith(`/${demoActionId}`)) return action;
    if (path.endsWith('/confirm')) { action.status = 'AUTHORIZED'; return action; }
    if (path.endsWith('/execute')) { action.status = ++executions === 1 ? 'UNKNOWN' : 'SUCCEEDED'; command.status = executions === 1 ? 'UNKNOWN' : 'COMPLETED'; return action; }
    if (path.endsWith('/events')) return command;
    throw new Error(`unexpected unit ${path}`);
  }); openPage(); const confirm = await screen.findByRole('button', { name: '具体确认并执行原动作' }); expect(confirm).toBeDisabled();
  const result = screen.getByRole('region', { name: '原事件结果 FIXED_EARLY_WITHDRAWAL' }); expect(result).toHaveTextContent('¥494.00'); expect(result).toHaveTextContent('¥5.00');
  fireEvent.click(within(result).getByRole('checkbox')); fireEvent.click(confirm); const retry = await screen.findByRole('button', { name: '核对并恢复原动作' });
  expect(retry).toBeEnabled(); expect(getDemoOperation().pending).toMatchObject({ kind: 'action-execute', resource_id: demoActionId, effect_hash: hash });
  expect(screen.getByRole('button', { name: '注入事件：工资到账' })).toBeDisabled(); fireEvent.click(retry); await screen.findByRole('heading', { name: '本事件原流程已完成' });
  expect(requests.filter((item) => item.path.includes('/prepare'))).toHaveLength(0); expect(requests.filter((item) => item.path.endsWith('/confirm')).map((item) => item.body)).toEqual([{ effect_hash: hash, accepted: true }]);
  expect(requests.filter((item) => item.path.endsWith('/execute')).map((item) => item.path)).toEqual([`/api/v1/actions/${demoActionId}/execute`, `/api/v1/actions/${demoActionId}/execute`]); expect(getDemoOperation().pending).toBeNull();
});
test('实际读取到ASK UNKNOWN也允许原动作恢复，加载与读取不会自动执行', async () => {
  const state = stateFixture(); const action = actionFixture(); action.status = 'UNKNOWN'; const command = commandFixture('FIXED_EARLY_WITHDRAWAL'); command.status = 'UNKNOWN'; command.actions = [action]; state.commands = [command];
  const requests = fixture(state, () => command); openPage(); const retry = await screen.findByRole('button', { name: '核对并恢复原动作' }); await waitFor(() => expect(retry).toBeEnabled());
  expect(getDemoOperation().pending).toMatchObject({ kind: 'action-execute', resource_id: demoActionId }); expect(requests.every((item) => item.method === 'GET')).toBe(true);
});
test('定存购入和有损早支取两段ASK各自复核，第一段确认不能自动授权第二段', async () => {
  const state = stateFixture(); const purchase = actionFixture(); purchase.effect.action_type = 'PURCHASE_ASSET'; purchase.effect.loss_cents = 0;
  const withdrawal = actionFixture(); withdrawal.action_id = demoProposalId; withdrawal.effect.operation_id = demoProposalId;
  const command = commandFixture('FIXED_EARLY_WITHDRAWAL'); command.status = 'WAITING_ACTION_CONFIRMATION'; command.actions = [purchase]; state.commands = [command];
  const requests = fixture(state, (_method, path) => {
    const action = path.includes(demoProposalId) ? withdrawal : purchase;
    if (path.endsWith('/confirm')) { action.status = 'AUTHORIZED'; return action; }
    if (path.endsWith('/execute')) { action.status = 'SUCCEEDED'; return action; }
    if (path.endsWith('/events')) { command.actions = [purchase, withdrawal]; if (withdrawal.status === 'SUCCEEDED') command.status = 'COMPLETED'; return command; }
    return action;
  }); openPage(); fireEvent.click(await screen.findByRole('checkbox', { name: /我已复核此原动作金额/ })); fireEvent.click(screen.getByRole('button', { name: '具体确认并执行原动作' }));
  await waitFor(() => expect(screen.getAllByRole('region', { name: /原动作经济后果/ })).toHaveLength(2));
  const second = screen.getByRole('button', { name: '具体确认并执行原动作' }); expect(second).toBeDisabled(); expect(requests.filter((item) => item.path.endsWith('/execute'))).toHaveLength(1);
  fireEvent.click(screen.getByRole('checkbox', { name: /我已复核此原动作金额/ })); fireEvent.click(second); await screen.findByRole('heading', { name: '本事件原流程已完成' });
  expect(requests.filter((item) => item.path.endsWith('/confirm')).map((item) => item.path)).toEqual([`/api/v1/actions/${demoActionId}/confirm`, `/api/v1/actions/${demoProposalId}/confirm`]);
});
test('报价过期409保留原动作与错误，不能重新prepare新报价', async () => {
  const state = stateFixture(); const action = actionFixture(); action.status = 'AUTHORIZED'; const command = commandFixture('FIXED_EARLY_WITHDRAWAL'); command.actions = [action]; command.status = 'WAITING_ACTION_CONFIRMATION'; state.commands = [command];
  const requests = fixture(state, (_method, path) => path.endsWith(`/${demoActionId}`) ? action : new Response(JSON.stringify({ error: { code: 'QUOTE_EXPIRED', message: '单元HTTP夹具：原报价已过期', request_id: 'unit-expired' } }), { status: 409 }));
  openPage(); fireEvent.click(await screen.findByRole('button', { name: '继续执行已确认的原动作' })); await screen.findByText(/原报价已过期/);
  expect(getDemoOperation().pending).toMatchObject({ kind: 'action-execute', resource_id: demoActionId }); expect(requests.filter((item) => item.path.endsWith('/execute'))).toHaveLength(1); expect(requests.some((item) => item.path.endsWith('/prepare'))).toBe(false);
});
test('房租完整配置与真实边界预览复核后PATCH保持原键，断网重试同一payload', async () => {
  const state = stateFixture(); const command = commandFixture('CHANGE_RENT'); command.status = 'WAITING_POLICY_CHANGE'; command.policy_change = { policy_id: policyId, expected_version_id: versionId, configuration: state.templates[3]!.configuration, reviewed_hash: hash, reason: '单元固定房租修改', idempotency_key: 'unit-rent-original' }; state.commands = [command]; let writes = 0;
  const requests = fixture(state, (method, path) => {
    if (path.endsWith('/change-preview')) { const preview = previewFixture(); preview.configuration = command.policy_change!.configuration; preview.before.minimum_margin_cents = -200; preview.after.safe_idle_cents = null; return preview; }
    if (method === 'PATCH') { if (++writes === 1) throw new Error('unit lost PATCH'); command.status = 'COMPLETED'; return lifecycleFixture(); }
    if (path.endsWith('/events')) return command;
    throw new Error(`unexpected unit ${method} ${path}`);
  }); openPage(); fireEvent.click(await screen.findByRole('button', { name: '读取房租修改边界预览' }));
  const confirm = await screen.findByRole('button', { name: '确认房租原修改' }); const review = screen.getByRole('region', { name: '房租原修改复核' }); expect(review).toHaveTextContent('¥-2.00'); expect(review).toHaveTextContent('待核验'); expect(confirm).toBeDisabled();
  const comparison = within(review).getByRole('region', { name: '91日房租修改前后边界' }); expect(within(comparison).queryByRole('table')).not.toBeInTheDocument(); expect(within(comparison).getAllByText('修改前')).toHaveLength(4); expect(within(comparison).getAllByText('假设修改后')).toHaveLength(4); expect(comparison).toHaveTextContent('资金缺口'); expect(comparison).toHaveTextContent('¥-2.00'); expect(comparison).toHaveTextContent('待核验');
  fireEvent.click(within(review).getByRole('checkbox')); fireEvent.click(confirm); await screen.findByText(/连接中断/); expect(confirm).toBeEnabled(); fireEvent.click(confirm);
  await screen.findByRole('heading', { name: '本事件原流程已完成' }); const patches = requests.filter((item) => item.method === 'PATCH'); expect(patches).toHaveLength(2); expect(patches[0]!.body).toEqual(patches[1]!.body); expect(patches[0]!.body).toEqual({ accepted: true, reviewed_hash: hash, expected_version_id: versionId, idempotency_key: 'unit-rent-original', configuration: command.policy_change!.configuration, reason: command.policy_change!.reason });
});
test('状态刷新失败撤下旧原件，不能把旧COMPLETED当当前结果', async () => {
  let failing = false; const state = stateFixture(); state.commands = [{ ...commandFixture(), status: 'COMPLETED', message: '单元旧完成结果' }];
  installHttpFixture((_method, path) => path.endsWith('/presets') ? presetsFixture() : failing ? failed() : state); openPage(); await screen.findByText('单元旧完成结果'); failing = true;
  fireEvent.click(screen.getByRole('button', { name: '读取实际演示状态' })); await screen.findByText(/实际状态读取失败/); expect(screen.queryByText('单元旧完成结果')).not.toBeInTheDocument();
});
