import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import FullGoalModelPanel from './FullGoalModelPanel';
import { confirmedVersionId, fullGoalIntentFixture, fullGoalLookupFixture, fullGoalReceiptFixture, fullModelFixture, fullPreviewFixture, modelGoal, otherGoalId } from '../tests/full-goal-fixture';
import { beginFullGoalOperation, clearFullGoalOperationAfterLookup, endFullGoalAttempt, getFullGoalOperation } from '../features/full-goal-operation';
import type { FullGoalIntent } from '../features/full-goal-operation';
import type { FullGoalPreview } from '../api/full-goals';

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); sessionStorage.clear(); });
afterEach(async () => { cleanup(); endFullGoalAttempt(); const original = getFullGoalOperation().pending; if (original) await clearFullGoalOperationAfterLookup(original, fullGoalLookupFixture(original)); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function transport(missing = false, failPreview = false) {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); const calls: { path: string; method: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => { const url = new URL(String(input)); calls.push({ path: url.pathname, method: options?.method ?? 'GET', body: options?.body ? JSON.parse(String(options.body)) : undefined });
    return new Response(JSON.stringify(options?.method === 'POST' ? failPreview ? { error: { code: 'STALE_POLICY_VERSION', message: '原版本已变化', request_id: 'UNIT-FULL-PREVIEW-STALE' } } : fullPreviewFixture() : fullModelFixture(missing)), { status: options?.method === 'POST' && failPreview ? 409 : 200 }); })); return calls;
}
function open(blocked = false, reviewCandidate: FullGoalPreview | null = null) { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); return render(<QueryClientProvider client={client}><FullGoalModelPanel goal={modelGoal} blocked={blocked} reviewCandidate={reviewCandidate} /></QueryClientProvider>); }
test('原版本完整属性/证据/hash与未授银行权展示，缺模型不伪补额外属性', async () => {
  transport(true); open(); await screen.findByText(/UNKNOWN · MODEL_MISSING/); expect(screen.queryByText('目标金额')).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: '使用当前原模型作为候选' })).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: /确认|保存模型/ })).not.toBeInTheDocument();
});
test('用户使用原模型→只读预览规范配置/双hash/真实base影响，额外字段不冒充全年度影响', async () => {
  const calls = transport(); open(); await screen.findByText(/原服务报告模型 VERIFIED/); fireEvent.click(screen.getByText('准备完整目标的只读影响预览')); fireEvent.click(screen.getByRole('button', { name: '使用当前原模型作为候选' })); fireEvent.click(screen.getByRole('button', { name: '只读预览候选影响' }));
  const preview = await screen.findByRole('region', { name: '完整目标只读预览结果' }); expect(within(preview).getByText(/原财务影响仅由goal_saving执行策略计算/)).toBeVisible(); expect(within(preview).getByText('待复核FULL配置摘要')).toBeVisible(); expect(within(preview).getByText('待复核原执行策略摘要')).toBeVisible(); expect(within(preview).getAllByText('UNKNOWN · 尚未证明').length).toBeGreaterThan(0);
  expect(calls).toHaveLength(2); expect(calls[1]!.path).toMatch(/\/full-model\/preview$/); expect(calls.every((call) => !call.path.endsWith('/confirm'))).toBe(true);
  fireEvent.change(screen.getByLabelText('候选完整模型JSON（金额为整数分）'), { target: { value: '{}' } }); expect(screen.queryByRole('region', { name: '完整目标只读预览结果' })).not.toBeInTheDocument();
});
test('原版本失效preview错误保留requestId，读取不变且没有自动重试/confirm', async () => {
  const calls = transport(false, true); open(); await screen.findByText(/原服务报告模型 VERIFIED/); fireEvent.click(screen.getByText('准备完整目标的只读影响预览')); fireEvent.click(screen.getByRole('button', { name: '使用当前原模型作为候选' })); fireEvent.click(screen.getByRole('button', { name: '只读预览候选影响' }));
  const alert = await screen.findByRole('alert'); expect(alert).toHaveTextContent('UNIT-FULL-PREVIEW-STALE'); expect(calls).toHaveLength(2); expect(screen.queryByRole('region', { name: '完整目标只读预览结果' })).not.toBeInTheDocument();
});
test('目标prop版本切换隔离旧reader/候选，跨目标原model不能显示VERIFIED', async () => {
  transport(); const view = open(); await screen.findByText(/原服务报告模型 VERIFIED/);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); view.rerender(<QueryClientProvider client={client}><FullGoalModelPanel goal={{ ...modelGoal, id: otherGoalId }} /></QueryClientProvider>);
  await screen.findByRole('alert'); expect(screen.queryByText(/原服务报告模型 VERIFIED/)).not.toBeInTheDocument(); expect(screen.getByLabelText('候选完整模型JSON（金额为整数分）')).toHaveValue('');
});
type CommandCall = { path: string; method: string; body: unknown; raw: string | undefined };
type CommandOptions = { lose?: boolean; notFound?: boolean; wrongRequest?: boolean; reject?: boolean; preview?: FullGoalPreview; missingModel?: boolean };
function confirmationTransport(options: CommandOptions = {}) {
  vi.stubEnv('VITE_API_BASE_URL', 'http://unit-full-goal-panel.local'); const calls: CommandCall[] = []; let original: FullGoalIntent | null = null; let writes = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)); const path = url.pathname; const method = init?.method ?? 'GET'; calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined, raw: init?.body === undefined ? undefined : String(init.body) });
    if (path.endsWith('/preview')) return new Response(JSON.stringify(options.preview ?? fullPreviewFixture()));
    if (path.endsWith('/confirm')) { writes++; original = getFullGoalOperation().pending; if (!original) throw new Error('UNIT_NO_PERSISTED_ORIGINAL'); if (options.lose && writes === 1) throw new Error('UNIT_REPLY_LOST'); if (options.reject) return new Response(JSON.stringify({ error: { code: 'STALE_POLICY_VERSION', message: '原版本已变化', request_id: 'UNIT-FULL-GOAL-CONFIRM-STALE' } }), { status: 409 }); return new Response(JSON.stringify(fullGoalReceiptFixture(original, writes > 1))); }
    if (path.includes('/commands/by-key/')) { original ??= getFullGoalOperation().pending; if (!original) throw new Error('UNIT_MISSING_LOOKUP_ORIGINAL'); const value = fullGoalLookupFixture(original, !options.notFound); if (options.wrongRequest && value.record) value.record.original_request.reason = 'DIFFERENT_ORIGINAL'; return new Response(JSON.stringify(value)); }
    return new Response(JSON.stringify(fullModelFixture(options.missingModel)));
  })); return calls;
}
async function prepareConfirmation() { await screen.findByText(/原服务报告模型 VERIFIED/); fireEvent.click(screen.getByText('准备完整目标的只读影响预览')); fireEvent.click(screen.getByRole('button', { name: '使用当前原模型作为候选' })); fireEvent.click(screen.getByRole('button', { name: '只读预览候选影响' })); await screen.findByRole('region', { name: '完整目标双hash明确确认' }); fireEvent.change(screen.getByLabelText('完整目标确认理由'), { target: { value: 'USER_EXPLICIT_FULL_GOAL_REASON' } }); fireEvent.click(screen.getByLabelText('我已复核规范化完整配置、FULL与原执行策略两份hash，明确确认此目标')); }

test('实际双hash确认发送服务器canonical完整配置与原version/epoch，HTTP成功仍pending', async () => {
  const calls = confirmationTransport(); open(); await prepareConfirmation(); fireEvent.click(screen.getByRole('button', { name: '明确确认完整目标双hash' })); await screen.findByText(/收到原服务回执；原请求继续保留/); const writes = calls.filter((call) => call.path.endsWith('/confirm')); expect(writes).toHaveLength(1); const original = getFullGoalOperation().pending!; expect(writes[0]!.raw).toBe(original.body_json); expect(writes[0]!.body).toEqual({ expected_version_id: modelGoal.policy_version_id, expected_epoch_id: fullPreviewFixture().epoch_id, configuration: fullPreviewFixture().full_configuration, reviewed_full_hash: fullPreviewFixture().full_configuration_hash, reviewed_base_hash: fullPreviewFixture().base_configuration_hash, accepted: true, reason: 'USER_EXPLICIT_FULL_GOAL_REASON', idempotency_key: expect.any(String) }); expect(screen.getByText(/此current_version_id是该命令首次形成版本/)).toBeVisible(); expect(screen.getByLabelText('候选完整模型JSON（金额为整数分）')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '只读核对原完整目标确认' })); await screen.findByText(/已解除本族待核对门/); expect(getFullGoalOperation().pending).toBeNull(); expect(calls.filter((call) => call.path.endsWith('/confirm'))).toHaveLength(1);
});
test('未知响应/NOT_FOUND保门；用户明确同原body/key重放，不生成新键或确认版本', async () => {
  const options: CommandOptions = { lose: true, notFound: true }; const calls = confirmationTransport(options); open(); await prepareConfirmation(); fireEvent.click(screen.getByRole('button', { name: '明确确认完整目标双hash' })); await screen.findByText(/连接中断.*完整原body\/key\/双hash保留/); const original = getFullGoalOperation().pending; fireEvent.click(screen.getByRole('button', { name: '只读核对原完整目标确认' })); await screen.findByText(/NOT_FOUND不是最终未提交证明/); expect(getFullGoalOperation().pending).toEqual(original); expect(calls.filter((call) => call.path.endsWith('/confirm'))).toHaveLength(1);
  options.notFound = false; fireEvent.click(screen.getByLabelText('我已核对原请求，明确只重放同一目标、原body和原键')); fireEvent.click(screen.getByRole('button', { name: '手动重放原完整目标确认' })); await screen.findByText(/收到原服务回执；原请求继续保留/); const writes = calls.filter((call) => call.path.endsWith('/confirm')); expect(writes).toHaveLength(2); expect(writes[0]!.raw).toBe(writes[1]!.raw); expect(getFullGoalOperation().pending!.body.idempotency_key).toBe(original!.body.idempotency_key); fireEvent.click(screen.getByRole('button', { name: '只读核对原完整目标确认' })); await screen.findByText(/已解除本族待核对门/);
});
test('明确409/NOT_FOUND也不blind清门；错原body查询不能冒匹配成功', async () => {
  const options: CommandOptions = { reject: true, notFound: true }; const calls = confirmationTransport(options); open(); await prepareConfirmation(); fireEvent.click(screen.getByRole('button', { name: '明确确认完整目标双hash' })); await screen.findByText(/UNIT-FULL-GOAL-CONFIRM-STALE/); const original = getFullGoalOperation().pending; fireEvent.click(screen.getByRole('button', { name: '只读核对原完整目标确认' })); await screen.findByText(/NOT_FOUND不是最终未提交证明/); expect(getFullGoalOperation().pending).toEqual(original); options.notFound = false; options.wrongRequest = true; fireEvent.click(screen.getByRole('button', { name: '只读核对原完整目标确认' })); await screen.findByText(/未解除原完整目标请求/); expect(getFullGoalOperation().pending).toEqual(original); expect(calls.filter((call) => call.path.endsWith('/confirm'))).toHaveLength(1);
});
test('编辑候选/理由撤回复核，预览不能自动confirm；他族blocked保原只读GET', async () => {
  const calls = confirmationTransport(); const view = open(); await prepareConfirmation(); expect(screen.getByRole('button', { name: '明确确认完整目标双hash' })).toBeEnabled(); fireEvent.change(screen.getByLabelText('完整目标确认理由'), { target: { value: 'REVIEW_AGAIN' } }); expect(screen.getByRole('button', { name: '明确确认完整目标双hash' })).toBeDisabled(); fireEvent.click(screen.getByLabelText('我已复核规范化完整配置、FULL与原执行策略两份hash，明确确认此目标')); fireEvent.change(screen.getByLabelText('候选完整模型JSON（金额为整数分）'), { target: { value: '{}' } }); expect(screen.queryByRole('region', { name: '完整目标双hash明确确认' })).not.toBeInTheDocument(); expect(calls.filter((call) => call.path.endsWith('/confirm'))).toHaveLength(0); const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); view.rerender(<QueryClientProvider client={client}><FullGoalModelPanel goal={modelGoal} blocked /></QueryClientProvider>); await screen.findByText(/原服务报告模型 VERIFIED/); expect(screen.getByRole('button', { name: '使用当前原模型作为候选' })).toBeDisabled(); expect(screen.getByRole('button', { name: '只读刷新完整模型' })).toBeEnabled();
});
test('原pending跨prop版本保完整旧请求；blocked仍允许自身GET，原历史回执不变当前模型', async () => {
  const original = await fullGoalIntentFixture(); confirmationTransport(); beginFullGoalOperation(original); endFullGoalAttempt(); const view = open(true); await screen.findByRole('region', { name: `待核对完整目标原请求 ${modelGoal.id}` }); expect(screen.getByRole('button', { name: '只读核对原完整目标确认' })).toBeEnabled(); expect(screen.getByRole('button', { name: '手动重放原完整目标确认' })).toBeDisabled(); const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); view.rerender(<QueryClientProvider client={client}><FullGoalModelPanel goal={{ ...modelGoal, policy_version_id: confirmedVersionId }} blocked /></QueryClientProvider>); expect(getFullGoalOperation().pending!.body.expected_version_id).toBe(original.body.expected_version_id); fireEvent.click(screen.getByRole('button', { name: '只读核对原完整目标确认' })); await screen.findByText(/已解除本族待核对门/); expect(getFullGoalOperation().pending).toBeNull(); expect(await screen.findByRole('region', { name: '完整目标原确认历史回执' })).toBeVisible(); await waitFor(() => expect(screen.queryByText(/原服务报告模型 VERIFIED/)).not.toBeInTheDocument());
});
test('他目标未决只指向原Goal，不能本目标执行/清除或偷偷同当前prop换goal', async () => {
  const original = await fullGoalIntentFixture(); const { prepareFullGoalIntent } = await import('../features/full-goal-operation'); const other = await prepareFullGoalIntent({ user_id: original.user_id, goal_id: otherGoalId, policy_id: original.policy_id, body: original.body }); beginFullGoalOperation(other); endFullGoalAttempt(); const calls = confirmationTransport(); open(); await screen.findByText(/另一原目标/); expect(screen.queryByRole('button', { name: '只读核对原完整目标确认' })).not.toBeInTheDocument(); await screen.findByText(/原服务报告模型 VERIFIED/); expect(screen.getByRole('button', { name: '使用当前原模型作为候选' })).toBeDisabled(); expect(calls.every((call) => call.method === 'GET')).toBe(true); expect(getFullGoalOperation().pending).toEqual(other);
});

test('修复候选须手动采用并重新服务器预览，新双hash经原持久确认/原键核对闭环', async () => {
  const incoming = fullPreviewFixture(); const fresh = fullPreviewFixture(); fresh.full_configuration_hash = '1'.repeat(64); fresh.base_configuration_hash = '2'.repeat(64); fresh.base_policy_impact.configuration_hash = fresh.base_configuration_hash;
  const calls = confirmationTransport({ preview: fresh, lose: true }); open(false, incoming); await screen.findByText(/原服务报告模型 VERIFIED/);
  expect(calls.every((call) => call.method === 'GET')).toBe(true); expect(screen.getByLabelText('候选完整模型JSON（金额为整数分）')).toHaveValue(''); expect(screen.queryByRole('region', { name: '完整目标双hash明确确认' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '使用修复候选并重新只读预览' })); const preview = await screen.findByRole('region', { name: '完整目标只读预览结果' }); expect(within(preview).getByText(fresh.full_configuration_hash)).toBeVisible();
  expect(calls.filter((call) => call.path.endsWith('/preview'))[0]!.body).toEqual({ expected_version_id: modelGoal.policy_version_id, configuration: incoming.full_configuration }); expect(calls.filter((call) => call.path.endsWith('/confirm'))).toHaveLength(0);
  expect(screen.getByRole('button', { name: '明确确认完整目标双hash' })).toBeDisabled(); fireEvent.change(screen.getByLabelText('完整目标确认理由'), { target: { value: 'EXPLICIT_REPAIR_NEW_VERSION' } }); fireEvent.click(screen.getByLabelText('我已复核规范化完整配置、FULL与原执行策略两份hash，明确确认此目标')); fireEvent.click(screen.getByRole('button', { name: '明确确认完整目标双hash' }));
  await screen.findByText(/连接中断.*完整原body\/key\/双hash保留/); const original = getFullGoalOperation().pending!; expect(original.body.reviewed_full_hash).toBe(fresh.full_configuration_hash); expect(original.body.reviewed_base_hash).toBe(fresh.base_configuration_hash); expect(calls.find((call) => call.path.endsWith('/confirm'))!.raw).toBe(original.body_json);
  fireEvent.click(screen.getByRole('button', { name: '只读核对原完整目标确认' })); await screen.findByText(/已解除本族待核对门/); expect(getFullGoalOperation().pending).toBeNull(); expect(calls.filter((call) => call.path.endsWith('/confirm'))).toHaveLength(1);
});

test.each(['goal', 'version', 'policy', 'epoch'] as const)('修复候选%s原绑定不匹配不能采用或POST', async (field) => {
  const candidate = fullPreviewFixture(); if (field === 'goal') candidate.goal_id = otherGoalId; if (field === 'version') candidate.expected_version_id = confirmedVersionId; if (field === 'policy') candidate.base_policy_impact.policy_id = otherGoalId; if (field === 'epoch') candidate.epoch_id = otherGoalId;
  const calls = confirmationTransport(); open(false, candidate); await screen.findByText(/原服务报告模型 VERIFIED/); expect(await screen.findByRole('alert')).toHaveTextContent('旧候选不能使用'); expect(screen.getByRole('button', { name: '使用修复候选并重新只读预览' })).toBeDisabled(); expect(calls.every((call) => call.method === 'GET')).toBe(true);
});

test('原模型缺失不能把修复候选当成已验证模型，跨族门仍可读取原件', async () => {
  const calls = confirmationTransport({ missingModel: true }); open(true, fullPreviewFixture()); await screen.findByText(/UNKNOWN · MODEL_MISSING/); expect(screen.getByRole('button', { name: '使用修复候选并重新只读预览' })).toBeDisabled(); expect(screen.getByRole('button', { name: '只读刷新完整模型' })).toBeEnabled(); expect(calls.every((call) => call.method === 'GET')).toBe(true);
});

test('采用后刷新清候选和双hash复核，原旧配置不自动重新采用', async () => {
  const calls = confirmationTransport(); open(false, fullPreviewFixture()); await screen.findByText(/原服务报告模型 VERIFIED/); fireEvent.click(screen.getByRole('button', { name: '使用修复候选并重新只读预览' })); await screen.findByRole('region', { name: '完整目标双hash明确确认' }); fireEvent.change(screen.getByLabelText('完整目标确认理由'), { target: { value: 'MUST_NOT_CARRY_FORWARD' } }); fireEvent.click(screen.getByLabelText('我已复核规范化完整配置、FULL与原执行策略两份hash，明确确认此目标'));
  fireEvent.click(screen.getByRole('button', { name: '只读刷新完整模型' })); await screen.findByText(/旧修复候选已清除/); await waitFor(() => expect(calls.filter((call) => call.method === 'GET')).toHaveLength(2)); expect(screen.queryByRole('region', { name: '完整目标双hash明确确认' })).not.toBeInTheDocument(); expect(screen.getByLabelText('候选完整模型JSON（金额为整数分）')).toHaveValue(''); expect(screen.getByRole('button', { name: '使用修复候选并重新只读预览' })).toBeDisabled(); expect(calls.filter((call) => call.path.endsWith('/confirm'))).toHaveLength(0);
});

test('重新预览若返回新epoch不能沿用候选生成确认', async () => {
  const fresh = fullPreviewFixture(); fresh.epoch_id = otherGoalId; const calls = confirmationTransport({ preview: fresh }); open(false, fullPreviewFixture()); await screen.findByText(/原服务报告模型 VERIFIED/); fireEvent.click(screen.getByRole('button', { name: '使用修复候选并重新只读预览' })); expect(await screen.findByRole('alert')).toHaveTextContent('新预览的原周期与当前完整目标不一致'); expect(screen.queryByRole('region', { name: '完整目标双hash明确确认' })).not.toBeInTheDocument(); expect(calls.filter((call) => call.path.endsWith('/confirm'))).toHaveLength(0);
});
