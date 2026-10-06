import { createServer } from 'node:http';
import { webcrypto } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import App from './App';
import type { Dashboard } from './api/dashboard';
import { dashboardFixture } from './tests/dashboard-fixture';
import { beginDemoOperation, endDemoOperation } from './features/demo-operation';
import { clearFullPolicyOperationAfterLookup, endFullPolicyAttempt, getFullPolicyOperation } from './features/full-policy-operation';
import { createFullIntent, fullLookupFixture } from './tests/full-policy-fixture';
import { beginOnboardingCommand, emptyOnboardingDraft, endOnboardingAttempt, getOnboardingDraft, prepareOnboardingIntent, retainOnboardingCandidate } from './features/onboarding-draft';
import { compilationRef } from './api/onboarding';
import { compilationFixture, emergencyText, onboardingBinding } from './tests/onboarding-fixture';
import { presetsFixture, stateFixture } from './tests/demo-fixture';
import { beginFullGoalOperation, clearFullGoalOperationAfterLookup, endFullGoalAttempt, getFullGoalOperation } from './features/full-goal-operation';
import { fullGoalIntentFixture, fullGoalLookupFixture } from './tests/full-goal-fixture';
import { beginOneQuestionOperation, clearOneQuestionAfterLookup, endOneQuestionAttempt, getOneQuestionOperation } from './features/one-question-operation';
import { questionIntentFixture, questionLookupFixture } from './tests/question-fixture';
import { beginSpendingEvidenceOperation, clearSpendingEvidenceAfterLookup, endSpendingEvidenceAttempt, getSpendingEvidenceOperation } from './features/spending-evidence-operation';
import { categoryResultFixture, spendingIntentFixture } from './tests/spending-evidence-fixture';
import { beginInterventionOperation, clearInterventionAfterRead, endInterventionAttempt, getInterventionOperation } from './features/intervention-operation';
import { interventionIntentFixture, interventionLookupFixture } from './tests/intervention-fixture';
import { beginGoalReleaseAuthorizationOperation, clearGoalReleaseAuthorizationAfterLookup, endGoalReleaseAuthorizationAttempt, getGoalReleaseAuthorizationOperation } from './features/goal-release-authorization-operation';
import { releaseIntentFixture, releaseLookupFixture } from './tests/goal-release-authorization-fixture';

let disconnected: boolean;
let fixture: Dashboard;
let client: QueryClient;
let requests: string[];

// Unit HTTP fixture, deliberately distinct from the real API/Edge acceptance.
const api = createServer((request, response) => {
  requests.push(`${request.method} ${request.url}`);
  if (request.method === 'GET' && ['/api/v1/demo/state', '/api/v1/demo/presets'].includes(request.url ?? '')) {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(request.url === '/api/v1/demo/state' ? stateFixture() : presetsFixture())); return;
  }
  if (request.method === 'GET' && request.url?.startsWith('/api/v1/full-policies/commands/by-key/')) {
    const original = getFullPolicyOperation().pending;
    if (original) { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(fullLookupFixture(original))); return; }
  }
  if (request.method === 'GET' && request.url?.includes('/category-confirmations/')) {
    const original = getSpendingEvidenceOperation().pending;
    if (original) { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(categoryResultFixture(original))); return; }
  }
  if (request.method !== 'GET' || request.url !== '/api/v1/dashboard') {
    response.writeHead(404); response.end(); return;
  }
  if (disconnected) { request.socket.destroy(); return; }
  response.writeHead(200, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(fixture));
});

beforeEach(async () => {
  window.history.replaceState(null, '', '/');
  disconnected = false; fixture = dashboardFixture(); requests = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  await new Promise<void>((resolve) => api.listen(0, '127.0.0.1', resolve));
  const address = api.address() as AddressInfo;
  vi.stubEnv('VITE_API_BASE_URL', `http://127.0.0.1:${address.port}`);
});

afterEach(async () => {
  endGoalReleaseAuthorizationAttempt(); const releaseOriginal = getGoalReleaseAuthorizationOperation().pending;
  if (releaseOriginal) await clearGoalReleaseAuthorizationAfterLookup(releaseOriginal, await releaseLookupFixture(releaseOriginal));
  endInterventionAttempt(); const interventionOriginal = getInterventionOperation().pending;
  if (interventionOriginal) await clearInterventionAfterRead(interventionOriginal, interventionLookupFixture(interventionOriginal));
  endSpendingEvidenceAttempt(); const spendingOriginal = getSpendingEvidenceOperation().pending;
  if (spendingOriginal) await clearSpendingEvidenceAfterLookup(spendingOriginal, categoryResultFixture(spendingOriginal));
  endOneQuestionAttempt(); const questionOriginal = getOneQuestionOperation().pending;
  if (questionOriginal) await clearOneQuestionAfterLookup(questionOriginal, questionLookupFixture(questionOriginal));
  endOnboardingAttempt(); const onboardingOriginal = getOnboardingDraft().draft.pending;
  if (onboardingOriginal?.kind === 'EMERGENCY') retainOnboardingCandidate(onboardingOriginal, compilationRef(onboardingOriginal, compilationFixture()));
  endFullPolicyAttempt(); const fullOriginal = getFullPolicyOperation().pending;
  if (fullOriginal) clearFullPolicyOperationAfterLookup(fullOriginal, fullLookupFixture(fullOriginal));
  endFullGoalAttempt(); const goalOriginal = getFullGoalOperation().pending;
  if (goalOriginal) await clearFullGoalOperationAfterLookup(goalOriginal, fullGoalLookupFixture(goalOriginal));
  endDemoOperation(true); sessionStorage.clear();
  client.clear(); api.closeAllConnections();
  await new Promise<void>((resolve, reject) => api.close((error) => {
    if (error) reject(error); else resolve();
  }));
});

test('引导、目标及问答原请求刷新恢复后阻挡其他资金写入，自身只读核对入口保持可达', async () => {
  vi.stubGlobal('crypto', webcrypto);
  const goalOriginal = await fullGoalIntentFixture('ROOT_APP_ORIGINAL_GOAL/1');
  sessionStorage.setItem(`bounded-funds-full-goal-operation-v1:${import.meta.env.VITE_API_BASE_URL}`, JSON.stringify(goalOriginal));
  const original = prepareOnboardingIntent('EMERGENCY', onboardingBinding(), emergencyText);
  sessionStorage.setItem(`bounded-funds-onboarding-draft-v1:${import.meta.env.VITE_API_BASE_URL}`, JSON.stringify({ ...emptyOnboardingDraft(), pending: original }));
  const questionOriginal = await questionIntentFixture('START', 'ROOT_APP_QUESTION_RESTORE');
  sessionStorage.setItem(`bounded-funds-one-question-operation-v1:${import.meta.env.VITE_API_BASE_URL}`, JSON.stringify(questionOriginal));
  openApp(); await connected();
  expect(await screen.findByText(/引导原候选请求正在处理或待核对/)).toBeVisible();
  expect(await screen.findByText(/完整目标原确认正在处理或待核对/)).toBeVisible();
  expect(getFullGoalOperation().pending).toEqual(goalOriginal);
  expect(await screen.findByText(/一次一问原请求正在处理或待核对/)).toBeVisible();
  expect(getOneQuestionOperation().pending).toEqual(questionOriginal);
  fireEvent.click(screen.getByRole('link', { name: '策略中心' }));
  await screen.findByRole('heading', { name: '策略中心' });
  expect(screen.getByRole('button', { name: '从真实模拟历史发现候选' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '首次引导' }));
  await screen.findByRole('heading', { name: '首次引导 · 明确事实与授权' });
  expect(screen.getByRole('button', { name: '只读核对原引导候选' })).toBeEnabled();
  expect(getOnboardingDraft().draft.pending).toEqual(original);
  fireEvent.click(screen.getByRole('link', { name: '一次一问' }));
  await screen.findByRole('heading', { name: '一次一问' });
  expect(screen.getByRole('button', { name: '原键只读核对' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '手动重放同一原请求' })).toBeDisabled();
  expect(requests.every((entry) => entry.startsWith('GET'))).toBe(true);
});

test('问答原键待核对阻挡其他族写入，自身原键GET和资金总览仍可达', async () => {
  vi.stubGlobal('crypto', webcrypto);
  const original = await questionIntentFixture('START', 'ROOT_APP_QUESTION_PENDING');
  await beginOneQuestionOperation(original); endOneQuestionAttempt();
  window.history.replaceState(null, '', '/#policies'); openApp();
  await screen.findByRole('heading', { name: '策略中心' });
  expect(screen.getByRole('button', { name: '从真实模拟历史发现候选' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '首次引导' }));
  await screen.findByRole('heading', { name: '首次引导 · 明确事实与授权' });
  fireEvent.click(screen.getByRole('button', { name: '2. 发现义务候选' }));
  expect(screen.getByRole('button', { name: '用户主动从历史发现候选' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '演示控制台' }));
  await screen.findByRole('heading', { name: '演示控制台' });
  expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '一次一问' }));
  await screen.findByRole('heading', { name: '一次一问' });
  expect(screen.getByRole('button', { name: '原键只读核对' })).toBeEnabled();
  expect(getOneQuestionOperation().pending).toEqual(original);
  fireEvent.click(screen.getByRole('link', { name: '资金总览' })); await connected();
  expect(requests.every((entry) => entry.startsWith('GET'))).toBe(true);
});

test('分类原请求阻挡问答与资金写入，自己的原键GET完整匹配才解门', async () => {
  vi.stubGlobal('crypto', webcrypto); const original = spendingIntentFixture('ROOT_APP_CATEGORY_PENDING');
  await beginSpendingEvidenceOperation(original); endSpendingEvidenceAttempt();
  window.history.replaceState(null, '', '/#policies'); openApp();
  await screen.findByRole('heading', { name: '策略中心' });
  expect(screen.getByRole('button', { name: '从真实模拟历史发现候选' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '一次一问' }));
  await screen.findByRole('heading', { name: '一次一问' });
  expect(screen.getByRole('button', { name: '保存原请求并开始问答' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '演示控制台' }));
  await screen.findByRole('heading', { name: '演示控制台' });
  expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '消费证据' }));
  await screen.findByRole('heading', { name: '支出证据与历史规律' });
  const lookup = screen.getByRole('button', { name: '只读核对原分类请求' });
  expect(lookup).toBeEnabled(); fireEvent.click(lookup);
  await waitFor(() => expect(getSpendingEvidenceOperation().pending).toBeNull());
  fireEvent.click(screen.getByRole('link', { name: '策略中心' }));
  await screen.findByRole('heading', { name: '策略中心' });
  expect(screen.getByRole('button', { name: '从真实模拟历史发现候选' })).toBeEnabled();
  expect(requests.some((entry) => entry.includes('/category-confirmations/'))).toBe(true);
  expect(requests.every((entry) => entry.startsWith('GET'))).toBe(true);
});

test('完整目标原确认跨页阻挡新策略、引导声明和演示重置，导航仍可读', async () => {
  vi.stubGlobal('crypto', webcrypto); const original = await fullGoalIntentFixture('ROOT_APP_GOAL_PENDING/1');
  beginFullGoalOperation(original); endFullGoalAttempt();
  window.history.replaceState(null, '', '/#policies'); openApp();
  await screen.findByRole('heading', { name: '策略中心' });
  expect(screen.getByRole('button', { name: '从真实模拟历史发现候选' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '首次引导' }));
  await screen.findByRole('heading', { name: '首次引导 · 明确事实与授权' });
  fireEvent.click(screen.getByRole('button', { name: '2. 发现义务候选' }));
  expect(screen.getByRole('button', { name: '用户主动从历史发现候选' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '演示控制台' }));
  await screen.findByRole('heading', { name: '演示控制台' });
  expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  expect(getFullGoalOperation().pending).toEqual(original);
  expect(requests.every((entry) => entry.startsWith('GET'))).toBe(true);
});

test('运行中的引导候选跨页阻挡演示重置与新资金动作', async () => {
  const original = prepareOnboardingIntent('EMERGENCY', onboardingBinding(), emergencyText);
  beginOnboardingCommand(original); endOnboardingAttempt();
  window.history.replaceState(null, '', '/#demo'); openApp();
  await screen.findByRole('heading', { name: '演示控制台' });
  expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  expect(getOnboardingDraft().draft.pending).toEqual(original);
  expect(requests.every((entry) => entry.startsWith('GET'))).toBe(true);
});

test('刷新恢复完整版原body与键后阻止其他资金提交，策略页原查询不被自身门锁住', async () => {
  const original = createFullIntent('root-app/original-key');
  sessionStorage.setItem(`bounded-funds-full-policy-operation-v1:${import.meta.env.VITE_API_BASE_URL}`, JSON.stringify(original));
  openApp(); await connected();
  expect(await screen.findByText(/完整版策略原请求正在处理或待核对/)).toBeVisible();
  fireEvent.click(screen.getByRole('link', { name: '策略中心' }));
  await screen.findByRole('region', { name: '完整版策略生命周期' });
  expect(screen.getByRole('button', { name: '从真实模拟历史发现候选' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '只读核对原命令' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '只读核对原命令' }));
  await screen.findByText(/已解除本族待核对门/);
  expect(getFullPolicyOperation().pending).toBeNull();
  await waitFor(() => expect(screen.getByRole('button', { name: '从真实模拟历史发现候选' })).toBeEnabled());
  expect(requests).toContain('GET /api/v1/full-policies/commands/by-key/root-app%2Foriginal-key');
  expect(requests.every((item) => item.startsWith('GET'))).toBe(true);
});

test('演示原请求等待核对时资金表单不能另写，导航与总览GET保持可读', async () => {
  beginDemoOperation({ kind: 'event', epoch_id: '10000000-0000-0000-0000-000000000090', event_kind: 'SALARY_RECEIVED' }); endDemoOperation(false);
  window.history.replaceState(null, '', '/#policies'); openApp(); await screen.findByRole('heading', { name: '策略中心' });
  expect(screen.getByRole('button', { name: '从真实模拟历史发现候选' })).toBeDisabled(); expect(screen.getByText(/演示原命令结果尚待核对/)).toBeVisible();
  fireEvent.click(screen.getByRole('link', { name: '资金总览' })); await connected(); expect(requests.every((item) => item.startsWith('GET'))).toBe(true);
});

function openApp() {
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

async function connected() {
  expect(await screen.findByText('资金总览已连接')).toHaveAttribute('role', 'status');
}

test('所有卡片来自唯一总览请求，当前保护取今天分层而非窗口限制点', async () => {
  openApp(); await connected();
  expect(screen.getByRole('heading', { name: '知余' })).toBeVisible();
  expect(screen.getByText(/所有资金动作均为模拟/)).toBeVisible();
  expect(requests).toEqual(['GET /api/v1/dashboard']);
  expect(screen.getByRole('region', { name: '91日资金边界' })).toHaveTextContent('¥3,800.00');
  const protection = screen.getByRole('region', { name: '当前保护资金' });
  expect(protection).toHaveTextContent('¥1,200.00');
  expect(protection).toHaveTextContent('¥700.00');
  expect(protection).not.toHaveTextContent('¥0.01');
  expect(screen.getByText(/执行仍需满足对应策略和确认权限/)).toBeVisible();
  expect(screen.getByRole('region', { name: '已自主配置' })).toHaveTextContent('已排除 2 笔手工持仓');
});

test('十六个页面导航实际切换，分项风险与场景模拟可达并保留原演示入口', async () => {
  openApp(); await connected(); const nav = screen.getByRole('navigation', { name: '页面导航' });
  expect(within(nav).getAllByRole('link')).toHaveLength(16);
  fireEvent.click(within(nav).getByRole('link', { name: '首次引导' }));
  await screen.findByRole('heading', { name: '首次引导 · 明确事实与授权' });
  fireEvent.click(within(nav).getByRole('link', { name: '年度规划' }));
  await screen.findByRole('heading', { name: '年度规划' });
  fireEvent.click(within(nav).getByRole('link', { name: '一次一问' }));
  await screen.findByRole('heading', { name: '一次一问' });
  fireEvent.click(within(nav).getByRole('link', { name: '介入中心' }));
  await screen.findByRole('heading', { name: '介入中心' });
  fireEvent.click(within(nav).getByRole('link', { name: '分项反事实评估' }));
  await screen.findByRole('heading', { name: '分项反事实评估' });
  expect(within(nav).getByRole('link', { name: '分项反事实评估' })).toHaveAttribute('aria-current', 'page');
  fireEvent.click(within(nav).getByRole('link', { name: '场景模拟器' }));
  await screen.findByRole('heading', { name: '场景模拟器' });
  fireEvent.click(within(nav).getByRole('link', { name: '消费证据' }));
  await screen.findByRole('heading', { name: '支出证据与历史规律' });
  fireEvent.click(within(nav).getByRole('link', { name: '产品与期限' }));
  await screen.findByRole('heading', { name: '产品与期限' });
  fireEvent.click(within(nav).getByRole('link', { name: '事实与证据' }));
  await screen.findByRole('heading', { name: '事实与证据' });
  fireEvent.click(within(nav).getByRole('link', { name: '投递记录' }));
  await screen.findByRole('heading', { name: '命令投递' });
  fireEvent.click(within(nav).getByRole('link', { name: '对账中心' }));
  await screen.findByRole('heading', { name: '对账中心' });
  fireEvent.click(within(nav).getByRole('link', { name: '策略中心' }));
  await screen.findByRole('heading', { name: '策略中心' });
  expect(within(nav).getByRole('link', { name: '策略中心' })).toHaveAttribute('aria-current', 'page');
  fireEvent.click(within(nav).getByRole('link', { name: '目标储备' })); await screen.findByRole('heading', { name: '目标储备' });
  fireEvent.click(within(nav).getByRole('link', { name: '演示控制台' })); await screen.findByRole('heading', { name: '演示控制台' });
  fireEvent.click(within(nav).getByRole('link', { name: '资金总览' })); await connected();
  expect(screen.getByRole('region', { name: '91日资金边界' })).toBeVisible();
});

test('通知原请求待核对阻挡其他变更，自身GET与只读模拟器保持可达', async () => {
  vi.stubGlobal('crypto', webcrypto);
  const original = interventionIntentFixture('OBSERVE', 'ROOT_APP_INTERVENTION_PENDING');
  await beginInterventionOperation(original); endInterventionAttempt();
  window.history.replaceState(null, '', '/#policies'); openApp();
  await screen.findByRole('heading', { name: '策略中心' });
  expect(screen.getByRole('button', { name: '从真实模拟历史发现候选' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '一次一问' }));
  await screen.findByRole('heading', { name: '一次一问' });
  expect(screen.getByRole('button', { name: '保存原请求并开始问答' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '演示控制台' }));
  await screen.findByRole('heading', { name: '演示控制台' });
  expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '介入中心' }));
  await screen.findByRole('heading', { name: '介入中心' });
  expect(await screen.findByRole('button', { name: '只读核对原通知请求' })).toBeEnabled();
  expect(getInterventionOperation().pending).toEqual(original);
  fireEvent.click(screen.getByRole('link', { name: '场景模拟器' }));
  await screen.findByRole('heading', { name: '场景模拟器' });
  fireEvent.click(screen.getByRole('link', { name: '资金总览' })); await connected();
  expect(requests.every((entry) => entry.startsWith('GET'))).toBe(true);
});

test('专用授权原确认跨页暂停其他写入，列表外原键只读核对仍可达', async () => {
  vi.stubGlobal('crypto', webcrypto);
  const original = await releaseIntentFixture('ROOT_APP_RELEASE_PENDING');
  await beginGoalReleaseAuthorizationOperation(original); endGoalReleaseAuthorizationAttempt();
  window.history.replaceState(null, '', '/#policies'); openApp();
  await screen.findByRole('heading', { name: '策略中心' });
  expect(screen.getByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '一次一问' }));
  await screen.findByRole('heading', { name: '一次一问' });
  expect(screen.getByRole('button', { name: '保存原请求并开始问答' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '演示控制台' }));
  expect(await screen.findByRole('button', { name: '恢复演示初始状态' })).toBeDisabled();
  fireEvent.click(screen.getByRole('link', { name: '目标储备' }));
  const lookup = await screen.findByRole('button', { name: '只读核对原专用授权' });
  expect(lookup).toBeEnabled(); fireEvent.click(lookup);
  await waitFor(() => expect(requests.some((entry) => entry.includes('/goal-release-authorizations/commands/') && entry.includes('ROOT_APP_RELEASE_PENDING'))).toBe(true));
  expect(getGoalReleaseAuthorizationOperation().pending).toEqual(original);
  expect(requests.every((entry) => entry.startsWith('GET'))).toBe(true);
});

test('待归属目标账户现金仍受保护，不能标成已归属现金', async () => {
  // HTTP unit fixture only: this checks labels, not bank or financial computation.
  const cashAccount = fixture.account_facts.facts.accounts[0]!;
  cashAccount.balance_cents = 340000;
  fixture.account_facts.facts.accounts.push({ ...cashAccount,
    id: '10000000-0000-0000-0000-000000000020', external_ref: 'unit-unassigned-goal',
    name: '单元测试待归属目标账户', account_type: 'GOAL', balance_cents: 160000 });
  fixture.goal_ownership.unassigned_goal_cash_cents = 160000;
  fixture.boundary.current_protected_cents_by_reason!.goal_cash = 160000;
  fixture.boundary.current_protected_cents = 280000;
  fixture.boundary.current_margin_cents = 220000;
  fixture.boundary.safe_idle_cents = 220000;
  fixture.boundary.minimum_margin_cents = 220000;
  openApp(); await connected();
  const protection = screen.getByRole('region', { name: '当前保护资金' });
  expect(protection).toHaveTextContent('目标现金保护（含待归属）');
  expect(protection).toHaveTextContent('¥1,600.00');
  expect(protection).not.toHaveTextContent('目标已归属现金');
  const ownership = screen.getByRole('region', { name: '目标资金归属' });
  expect(ownership).toHaveTextContent(/目标现金\s*¥0\.00/);
  expect(ownership).toHaveTextContent(/待归属的目标账户现金\s*¥1,600\.00/);
  expect(within(ownership).getAllByText('0.00')).toHaveLength(3);
  expect(requests).toEqual(['GET /api/v1/dashboard']);
});

test('断开时保留重试入口，恢复后整体重新获取', async () => {
  disconnected = true; openApp();
  expect(await screen.findByText('资金总览暂未连接')).toHaveAttribute('role', 'status');
  expect(screen.queryByRole('region', { name: '91日资金边界' })).not.toBeInTheDocument();
  disconnected = false;
  fireEvent.click(screen.getByRole('button', { name: '重新连接' }));
  await connected();
  expect(requests).toEqual(['GET /api/v1/dashboard', 'GET /api/v1/dashboard']);
});

test('刷新失败后撤下旧卡片，不宣称旧资金边界为当前结果', async () => {
  openApp(); await connected(); disconnected = true;
  fireEvent.click(screen.getByRole('button', { name: '刷新总览' }));
  expect(await screen.findByText('资金总览暂未连接')).toHaveAttribute('role', 'status');
  expect(screen.queryByRole('region', { name: '91日资金边界' })).not.toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('当前不显示缺失金额为零');
});

test('资料不足显示待核验，原始账面现金仍独立展示', async () => {
  fixture.boundary = { ...fixture.boundary, state: 'NOT_PROVEN', status: 'INSUFFICIENT_EVIDENCE',
    safe_idle_cents: null, minimum_margin_cents: null, deficit_cents: null,
    current_protected_cents: null, current_protected_cents_by_reason: null,
    current_margin_cents: null, protected_cents_by_reason: null };
  fixture.goal_ownership = { ...fixture.goal_ownership, state: 'NOT_PROVEN', cash_owned_cents: null,
    principal_owned_cents: null, allocated_cents: null, unassigned_goal_cash_cents: null };
  fixture.managed_assets = { ...fixture.managed_assets, state: 'NOT_PROVEN', managed_current_principal_cents: null,
    general_principal_cents: null, held_or_matured_cents: null, redeeming_cents: null, pending_purchase_cents: null };
  fixture.next_obligations = { ...fixture.next_obligations, status: 'NOT_PROVEN', next_due_date: null,
    next_count: null, next_remaining_protection_cents: null, basis_summary: null, items: [], items_complete: false };
  openApp(); await connected();
  expect(screen.getByRole('region', { name: '91日资金边界' })).toHaveTextContent('资料不足，边界待核验');
  expect(screen.getByRole('region', { name: '91日资金边界' })).not.toHaveTextContent('¥0.00');
  expect(screen.getByRole('region', { name: '当前保护资金' })).not.toHaveTextContent('¥0.00');
  expect(screen.getByRole('region', { name: '目标资金归属' })).not.toHaveTextContent('¥0.00');
  expect(screen.getByRole('region', { name: '已自主配置' })).not.toHaveTextContent('¥0.00');
  expect(screen.getByRole('region', { name: '账面现金' })).toHaveTextContent('¥5,000.00');
  expect(screen.getByText(/不能据此判断已无待付义务/)).toBeVisible();
});

test('流动性风险保留负余量与缺口，安全闲置明确为已知零', async () => {
  fixture.boundary = { ...fixture.boundary, status: 'LIQUIDITY_RISK', safe_idle_cents: 0,
    minimum_margin_cents: -25000, deficit_cents: 25000, current_margin_cents: -25000 };
  openApp(); await connected();
  const region = screen.getByRole('region', { name: '91日资金边界' });
  expect(region).toHaveTextContent('存在流动性缺口');
  expect(region).toHaveTextContent('¥-250.00');
  expect(region).toHaveTextContent('¥250.00');
  expect(region).toHaveTextContent('¥0.00');
});

test.each(['simulation', 'unsafe-money', 'wrong-user'] as const)('拒绝不可信资金响应：%s', async (mode) => {
  if (mode === 'simulation') Object.assign(fixture, { simulation: false });
  if (mode === 'unsafe-money') fixture.boundary.safe_idle_cents = Number.MAX_SAFE_INTEGER + 1;
  if (mode === 'wrong-user') fixture.account_facts.facts.user_id = 'different-user';
  openApp();
  expect(await screen.findByText('资金总览暂未连接')).toHaveAttribute('role', 'status');
  expect(screen.queryByRole('region', { name: '91日资金边界' })).not.toBeInTheDocument();
});

test('可安全表达的大金额保留精确的最后一分', async () => {
  fixture.account_facts.facts.cash_balance_cents = 9007199254740990;
  openApp(); await connected();
  expect(screen.getByRole('region', { name: '账面现金' })).toHaveTextContent('¥90,071,992,547,409.90');
});

test('逾期保留原到期日，区间上限与截断列表如实说明', async () => {
  const original = fixture.next_obligations.items[0]!;
  fixture.next_obligations = { ...fixture.next_obligations, next_due_date: '2026-10-01',
    next_count: 21, next_remaining_protection_cents: 630000, basis_summary: 'UPPER_BOUND', items_complete: false,
    items: Array.from({ length: 20 }, (_, index) => ({ ...original, occurrence_id: `unit-${index}`,
      due_date: '2026-10-01', projection_payment_date: '2026-10-04', overdue: true,
      total_basis: 'POLICY_RANGE_MAX', remaining_protection_cents: 30000, protected_total_cents: 30000 })) };
  openApp(); await connected();
  const obligations = screen.getByRole('region', { name: '下一组义务' });
  expect(obligations).toHaveTextContent('2026年10月1日');
  expect(obligations).toHaveTextContent('同日 21 笔');
  expect(obligations).toHaveTextContent('¥6,300.00');
  expect(obligations).toHaveTextContent('展示前 20 笔');
  expect(within(obligations).getAllByText('按策略区间上限保护 · 2026-10')).toHaveLength(20);
  expect(within(obligations).getAllByText('已逾期')).toHaveLength(20);
  expect(obligations).toHaveTextContent('测算支付日为 2026年10月4日');
});

test('UNKNOWN原操作、截断范围与历史恢复建议可查看，不产生确认或重扣请求', async () => {
  fixture.pending_actions = { state: 'INCOMPLETE', total: 2, list_complete: false, has_more: true,
    items: [{ action_id: '10000000-0000-0000-0000-000000000011',
      decision_run_id: '10000000-0000-0000-0000-000000000012', action_type: 'REDEEM_ASSET', amount_cents: 250000,
      status: 'UNKNOWN', prepared_level: 'ASK_ONCE', prepared_at: fixture.as_of, current_decision: null,
      effect_hash: 'b'.repeat(64), fee_cents: 100, loss_cents: 1000,
      bank_operation_id: '10000000-0000-0000-0000-000000000013', bank_status: 'UNKNOWN', bank_state_proven: true,
      receipt_id: null, receipt_status: null, receipt_verified: false, audit_status: 'VALID',
      reason_codes: ['ORIGINAL_OPERATION_UNKNOWN'] }] };
  fixture.recovery_proposals = { state: 'PROVEN', total: 1, list_complete: true, has_more: false,
    items: [{ run_id: '10000000-0000-0000-0000-000000000014', as_of: fixture.as_of,
      status: 'REVIEW_REQUIRED', original_status: 'ASK_ONCE', fee_cents: 100, loss_cents: 1000,
      audit_status: 'VALID', reason_codes: ['LEGACY_PROPOSAL_NEEDS_REVIEW'] }] };
  fixture.intervention = { status: 'RECONCILIATION_REQUIRED', known_required_count: 1, complete: false,
    reason_codes: ['ORIGINAL_OPERATION_UNKNOWN'] };
  openApp(); await connected();
  const pending = screen.getByRole('region', { name: '待处理事项' });
  expect(pending).toHaveTextContent('不能判断没有待介入事项');
  expect(pending).toHaveTextContent('已展示 1 / 2 个原动作');
  const originalSummary = within(pending).getByText('查看原项').closest('summary')!;
  fireEvent.click(originalSummary);
  await waitFor(() => expect(originalSummary.parentElement).toHaveAttribute('open'));
  expect(within(pending).getByText('10000000-0000-0000-0000-000000000011')).toBeVisible();
  expect(within(pending).getByText(/本页不会创建重试扣款/)).toBeVisible();
  expect(pending).toHaveTextContent('不能替代当前的具体确认');
  expect(screen.queryByRole('button', { name: /确认|执行|支付|划拨/ })).not.toBeInTheDocument();
  expect(requests).toEqual(['GET /api/v1/dashboard']);
});

test('刷新整体替换快照，保留目标归属交叉说明与审计范围', async () => {
  openApp(); await connected();
  fixture.as_of = '2026-10-04T02:01:00Z'; fixture.boundary.safe_idle_cents = 580000;
  fixture.account_facts.facts.cash_balance_cents = 700000;
  fixture.audit.status = 'LEGACY_UNAUDITED'; fixture.audit.epoch_id = null; fixture.audit.complete = false;
  fireEvent.click(screen.getByRole('button', { name: '刷新总览' }));
  await waitFor(() => expect(screen.getByRole('region', { name: '91日资金边界' })).toHaveTextContent('¥5,800.00'));
  expect(screen.getByRole('region', { name: '账面现金' })).toHaveTextContent('¥7,000.00');
  expect(screen.getByText(/查询时点 10\/04 10:01/)).toBeVisible();
  expect(screen.getByText(/目标本金可能也包含在已自主配置中/)).toBeVisible();
  expect(screen.getByRole('region', { name: '来源与审计' })).toHaveTextContent('历史数据未建立审计链');
  fireEvent.click(screen.getByText('查看计算与核验说明'));
  expect(screen.getByText(/核验范围：当前有效审计纪元。范围未完整核验/)).toBeVisible();
  expect(requests).toEqual(['GET /api/v1/dashboard', 'GET /api/v1/dashboard']);
});
