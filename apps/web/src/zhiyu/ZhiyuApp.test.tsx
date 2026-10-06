import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { compilationFixture, proposalFixture } from '../tests/policy-fixture';
import { goalFixture, travelPolicyFixture, zhiyuAction, zhiyuPresets, zhiyuState } from './test-fixtures';
import type { State } from './api';
let App: typeof import('./ZhiyuApp').default;
let environment: State;
let posts: { path: string; body: unknown }[];
let reads: string[];
let loseExecution: boolean;
let refusalScope: 'MATCH' | 'OTHER' | null;
beforeEach(async () => {
  sessionStorage.clear(); localStorage.clear(); vi.resetModules();
  App = (await import('./ZhiyuApp')).default; environment = zhiyuState(); posts = []; reads = []; loseExecution = false; refusalScope = null;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(String(input), 'http://unit.local').pathname; const method = options?.method ?? 'GET';
    if (method === 'GET') reads.push(path);
    if (method !== 'GET') posts.push({ path, body: JSON.parse(String(options?.body ?? '{}')) as unknown });
    let response: unknown;
    if (path.endsWith('/zhiyu/state')) response = environment;
    else if (path.endsWith('/zhiyu/presets')) response = zhiyuPresets();
    else if (path.endsWith('/policies')) response = { simulation: true, items: [travelPolicyFixture()] };
    else if (path.endsWith('/policy-proposals')) response = { simulation: true, items: [proposalFixture()] };
    else if (path.endsWith('/policies/compile')) response = compilationFixture();
    else if (path.endsWith('/zhiyu/actions/prepare') && refusalScope) {
      environment.activity.push({ id: 'unit-rejection', at: environment.dashboard.as_of, intent: '目标储备请求', authorization: '旅行规则已撤销', decision: 'POLICY_INACTIVE', amount_cents: null, status: 'REJECTED', action_id: null, scenario: refusalScope === 'MATCH' ? 'REVOKED' : 'SAFE', goal_id: goalFixture().id });
      return new Response(JSON.stringify({ error: { code: 'POLICY_INACTIVE', message: '旅行规则已撤销', request_id: 'unit' } }), { status: 409 });
    }
    else if (path.endsWith('/allocation-preview')) response = { simulation: true, user_id: environment.dashboard.user_id, goal_id: goalFixture().id, as_of: environment.dashboard.as_of, source_evidence_ids: [], input_digest: 'unit', source_issues: [], allocation: { goal_id: goalFixture().id, policy_version_id: goalFixture().policy_version_id, preview_only: true, financial_only: true, status: 'READY', suggested_cents: 100000, baseline_boundary: zhiyuAction().prepared_validation.baseline_boundary, candidate_boundary: { ...zhiyuAction().prepared_validation.baseline_boundary, safe_idle_cents: 280000 }, lot_allocations: [], reasons: [] } };
    else if (/\/zhiyu\/actions\/.+\/execute$/.test(path)) {
      if (loseExecution) { loseExecution = false; environment.actions = [zhiyuAction('UNKNOWN')]; throw new TypeError('UNIT_RESPONSE_LOSS'); }
      environment.actions = [zhiyuAction('SUCCEEDED')]; environment.goals[0]!.allocated_cents = 100000; response = environment.actions[0];
    } else if (/\/actions\/[0-9a-f-]{36}$/.test(path)) response = environment.actions[0];
    else throw new Error(`Unhandled fixture ${method} ${path}`);
    return new Response(JSON.stringify(response), { status: 200 });
  }));
});
function mount() { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><App /></QueryClientProvider>); }
test('candidate creation and cancellation grant no financial permission or execution', async () => {
  mount(); await screen.findByText('先守住生活，再安排余钱'); fireEvent.click(screen.getByRole('button', { name: '我的规则' }));
  fireEvent.change(screen.getByLabelText('你的意图'), { target: { value: '保留3000元应急金' } });
  fireEvent.click(screen.getByRole('button', { name: '生成待确认候选' })); await screen.findByText('待确认 · 尚无权限');
  fireEvent.click(screen.getByRole('button', { name: '取消本次确认' }));
  expect(posts).toEqual([{ path: '/api/v1/policies/compile', body: { text: '保留3000元应急金', engine: 'rules' } }]);
  expect(localStorage.length).toBe(0);
});
test('lost execution keeps original across remount; recovery reuses ID and shows only server-read completion', async () => {
  environment.goals = [goalFixture()]; environment.actions = [zhiyuAction()]; environment.income_received = true; loseExecution = true;
  const mounted = mount(); await screen.findByText('先守住生活，再安排余钱'); fireEvent.click(screen.getByRole('button', { name: '目标与执行' }));
  reads = [];
  fireEvent.click(screen.getByLabelText('我已复核固定金额、来源、目标、费用和权限，接受此原操作。'));
  fireEvent.click(screen.getByRole('button', { name: '执行这个原操作' }));
  await screen.findByRole('heading', { name: '先核对这一笔，再安排下一笔' });
  await waitFor(() => expect(screen.getByRole('button', { name: '读取原结果' })).toBeEnabled());
  expect(screen.getByRole('button', { name: '准备并查看计划' })).toBeDisabled(); expect(localStorage.length).toBe(1);
  expect(reads.filter((path) => path.endsWith('/zhiyu/state'))).toHaveLength(1);
  expect(reads.filter((path) => /\/actions\/[0-9a-f-]{36}$/.test(path))).toHaveLength(0);
  expect(reads.filter((path) => path.endsWith('/policy-proposals'))).toHaveLength(0);
  const count = posts.length; mounted.unmount(); mount(); await screen.findByRole('heading', { name: '先核对这一笔，再安排下一笔' });
  expect(posts.length).toBe(count);
  fireEvent.click(screen.getByRole('button', { name: '恢复同一原请求' }));
  await waitFor(() => expect(localStorage.length).toBe(0));
  expect(posts.map((row) => row.path)).toEqual([`/api/v1/zhiyu/actions/${zhiyuAction().action_id}/execute`, `/api/v1/zhiyu/actions/${zhiyuAction().action_id}/execute`]);
  fireEvent.click(screen.getByRole('button', { name: '目标与执行' })); await screen.findByText('实际执行 ¥1,000.00，回执已保存。目标进度与边界已重新读取。');
});
test('overview starts only state and fixed presets; inactive rules and previews stay unloaded', async () => {
  mount(); await screen.findByText('先守住生活，再安排余钱'); await screen.findByText('等待一笔演示收入');
  expect(reads.sort()).toEqual(['/api/v1/zhiyu/presets', '/api/v1/zhiyu/state']);
});
test('activity translates known codes without changing raw records or hiding an unknown reason', async () => {
  const raw = { id: 'unit-activity', at: environment.dashboard.as_of, intent: '目标储备 · SAFE', authorization: goalFixture().policy_version_id, decision: 'AUTO_EXECUTE；EXACT_GOAL_POLICY_VERSION_REQUIRED；UNRECOGNIZED_REASON', amount_cents: 100000, status: 'SETTLED', action_id: null };
  environment.activity = [raw];
  mount(); await screen.findByText('先守住生活，再安排余钱'); fireEvent.click(screen.getByRole('button', { name: '活动记录' }));
  expect(screen.getByRole('heading', { name: '目标储备 · 安全资金安排' })).toBeVisible();
  expect(screen.getByText('已到账')).toBeVisible();
  expect(screen.getByText('授权来源：用户确认的策略版本')).toBeVisible();
  expect(screen.getAllByText('已确认规则允许本次资金安排；权限版本需有效，并与目标的确认版本一致；UNRECOGNIZED_REASON')[0]).toBeVisible();
  const details = screen.getByText('为什么允许或拒绝 · 查看原标识').closest('details')!;
  expect(details).toHaveTextContent(raw.intent); expect(details).toHaveTextContent(raw.decision); expect(details).toHaveTextContent(raw.authorization); expect(details).toHaveTextContent(raw.status);
  expect(details).not.toHaveAttribute('open'); expect(environment.activity[0]).toEqual(raw);
});
test.each(['MATCH', 'OTHER'] as const)('409 clears only after independent same-scenario refusal evidence (%s)', async (scope) => {
  refusalScope = scope; environment.goals = [goalFixture()]; environment.income_received = true;
  mount(); await screen.findByText('先守住生活，再安排余钱'); fireEvent.click(screen.getByRole('button', { name: '目标与执行' }));
  fireEvent.change(screen.getByLabelText('场景'), { target: { value: 'REVOKED' } }); fireEvent.click(screen.getByRole('button', { name: '准备并查看计划' }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('旅行规则已撤销'));
  await waitFor(() => expect(screen.queryByText('正在处理，请稍候。原操作身份已保留，请勿重复提交。')).not.toBeInTheDocument());
  expect(localStorage.length).toBe(scope === 'MATCH' ? 0 : 1);
  expect(environment.goals[0]!.allocated_cents).toBe(0); expect(environment.actions).toEqual([]);
});
