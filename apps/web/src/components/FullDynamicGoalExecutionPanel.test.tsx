import { webcrypto } from 'node:crypto';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { dynamicExecutionGoal, dynamicExecutionUser, dynamicExecutionEpoch, dynamicModelFixture, dynamicPreviewFixture, dynamicActionFixture, dynamicPrepareFixture, dynamicLookupFixture, dynamicFixtureHash } from '../tests/full-dynamic-goal-execution-fixture';
import type { DynamicIntent, DynamicPrepare } from '../api/full-dynamic-goal-execution';
import { installHttpFixture } from '../tests/policy-fixture';
let Panel: typeof import('./FullDynamicGoalExecutionPanel').default;
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); Panel = (await import('./FullDynamicGoalExecutionPanel')).default; });
function open(blocked = false) { return render(<Panel goal={dynamicExecutionGoal} userId={dynamicExecutionUser} epochId={dynamicExecutionEpoch} mutationBlocked={blocked} />); }
const fixtureIntent = (body: DynamicPrepare): DynamicIntent => ({ protocol: 'full-dynamic-goal-browser-v1', kind: 'PREPARE', user_id: dynamicExecutionUser, prepare_request: body, action: null, path: '/dynamic-goal-actions/prepare', body, body_json: JSON.stringify(body), request_hash: dynamicFixtureHash(body) });
function install(options: { missing?: boolean; status?: 'UNKNOWN' | 'BLOCKED'; loss?: boolean; missingConfirm?: boolean } = {}) {
  let prepare = dynamicPrepareFixture(), stage: 'PREPARED' | 'CONFIRMED' | 'UNKNOWN' | 'SETTLED' = 'PREPARED';
  const calls = installHttpFixture((method, path, body) => {
    if (path.endsWith('/accounts/summary')) return { simulation: true, user_id: dynamicExecutionUser, accounts: [] };
    if (path.endsWith('/full-model')) return dynamicModelFixture(options.missing);
    if (path.endsWith('/preview')) { prepare = body as DynamicPrepare; return dynamicPreviewFixture(prepare, options.status); }
    if (path.endsWith('/prepare')) { prepare = body as DynamicPrepare; if (options.loss) return new Response(JSON.stringify({ error: { code: 'UNIT_LOSS', message: 'TOOL_ONLY response loss' } }), { status: 500 }); return dynamicActionFixture(stage); }
    if (method === 'POST' && path.endsWith('/confirm')) { stage = 'CONFIRMED'; return dynamicActionFixture(stage); }
    if (method === 'POST' && path.endsWith('/execute')) { stage = options.loss ? 'UNKNOWN' : 'SETTLED'; return dynamicActionFixture(stage); }
    if (path.includes('/by-key/')) return dynamicLookupFixture(fixtureIntent(prepare), options.missingConfirm && stage === 'CONFIRMED' ? 'MISSING_CONFIRM' : stage);
    throw new Error(`TOOL_ONLY_UNEXPECTED_${path}`);
  }); return calls;
}
async function loadPreview() { fireEvent.click(screen.getByRole('button', { name: '只读加载当前目标模型与证据' })); await screen.findByText(/原模型 VERIFIED/); fireEvent.click(screen.getByRole('button', { name: '读取服务器动态执行预览' })); await screen.findByRole('heading', { name: /执行范围/ }); }
async function recover() { fireEvent.click(screen.getByRole('button', { name: '独立读取原动态目标结果' })); await waitFor(() => expect(screen.queryByRole('region', { name: '动态目标待核对请求' })).not.toBeInTheDocument()); }
test('手动读取真实模型+serverpreview，金额无输入，原sourceHash/模型/evidence/365明确且不自动执行', async () => {
  const calls = install(); open(); expect(calls).toHaveLength(0); await loadPreview(); expect(screen.getByText(/sourceHash a{64}/)).toBeVisible(); expect(within(screen.getByRole('region', { name: '动态服务器预览' })).getAllByText('¥300.06')).toHaveLength(2); expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument(); expect(calls.map((c) => c.method)).toEqual(['GET', 'GET', 'POST']); expect(calls.at(-1)!.body).not.toHaveProperty('amount_cents');
});
test('准备→独立GET→原明确确认→独立GET→固定执行→原GET回执完整UI路径，POST不清门', async () => {
  const calls = install(); open(); await loadPreview(); fireEvent.click(screen.getByRole('button', { name: '明确准备原动态目标动作' })); await screen.findByRole('heading', { name: '原 PREPARE 待核对' }); await screen.findByText(/原请求已返回/); expect(screen.queryByRole('button', { name: '提交原动作明确确认' })).not.toBeInTheDocument(); await recover(); await screen.findByRole('button', { name: '提交原动作明确确认' });
  fireEvent.click(screen.getByRole('checkbox', { name: /我复核固定原金额/ })); fireEvent.click(screen.getByRole('button', { name: '提交原动作明确确认' })); await screen.findByRole('heading', { name: '原 CONFIRM 待核对' }); await waitFor(() => expect(screen.getByRole('button', { name: '独立读取原动态目标结果' })).toBeEnabled()); await recover();
  fireEvent.click(screen.getByRole('checkbox', { name: /我复核固定原金额/ })); fireEvent.click(screen.getByRole('button', { name: '仅执行/恢复这个固定原动作' })); await screen.findByRole('heading', { name: '原 EXECUTE 待核对' }); await waitFor(() => expect(screen.getByRole('button', { name: '独立读取原动态目标结果' })).toBeEnabled()); await recover(); await screen.findByText(/原服务回执/);
  expect(calls.filter((c) => c.method === 'POST').map((c) => c.path.split('/').at(-1))).toEqual(['preview', 'prepare', 'confirm', 'execute']); expect(calls.find((c) => c.path.endsWith('/confirm'))!.body).toEqual({ accepted: true, effect_hash: dynamicActionFixture().effect_hash }); expect(calls.find((c) => c.path.endsWith('/execute'))!.body).toEqual({});
});
test.each(['UNKNOWN', 'BLOCKED'] as const)('%s与nullable金额不显示0，不提供prepare放行', async (status) => { const calls = install({ status }); open(); await loadPreview(); expect(screen.getByRole('button', { name: '明确准备原动态目标动作' })).toBeDisabled(); if (status === 'UNKNOWN') expect(within(screen.getByRole('region', { name: '动态服务器预览' })).getAllByText('UNKNOWN · 尚未证明')).toHaveLength(4); expect(calls).toHaveLength(3); });
test('缺MODEL显示UNKNOWN，没有预览、默认金额或自动创建新模型', async () => { const calls = install({ missing: true }); open(); fireEvent.click(screen.getByRole('button', { name: '只读加载当前目标模型与证据' })); await screen.findByText(/当前完整目标模型缺失/); expect(screen.queryByRole('button', { name: '读取服务器动态执行预览' })).not.toBeInTheDocument(); expect(calls.every((c) => c.method === 'GET')).toBe(true); });
test('准备响应500保原key/body，独立GET可以跨自身/他族mutationBlocked；不自动重发', async () => {
  const calls = install({ loss: true }); const view = open(); await loadPreview(); fireEvent.click(screen.getByRole('button', { name: '明确准备原动态目标动作' })); await screen.findByRole('alert'); const prepares = calls.filter((c) => c.path.endsWith('/prepare')); expect(prepares).toHaveLength(1); const key = (prepares[0]!.body as DynamicPrepare).idempotency_key;
  view.rerender(<Panel goal={dynamicExecutionGoal} userId={dynamicExecutionUser} epochId={dynamicExecutionEpoch} mutationBlocked />); await recover(); expect(screen.getByRole('button', { name: '提交原动作明确确认' })).toBeDisabled(); expect(calls.filter((c) => c.path.endsWith('/prepare'))).toHaveLength(1); expect(calls.find((c) => c.path.includes('/by-key/'))!.path).toContain(key);
});
test('AUTHORIZED但原确认MISSING保confirm请求，不能执行或把原状态当同意', async () => {
  const calls = install({ missingConfirm: true }); open(); await loadPreview(); fireEvent.click(screen.getByRole('button', { name: '明确准备原动态目标动作' })); await screen.findByText(/原请求已返回/); await recover(); fireEvent.click(screen.getByRole('checkbox', { name: /我复核固定原金额/ })); fireEvent.click(screen.getByRole('button', { name: '提交原动作明确确认' })); await screen.findByRole('heading', { name: '原 CONFIRM 待核对' }); await waitFor(() => expect(screen.getByRole('button', { name: '独立读取原动态目标结果' })).toBeEnabled()); fireEvent.click(screen.getByRole('button', { name: '独立读取原动态目标结果' })); await screen.findByText(/原确认 MISSING/); expect(screen.getByRole('heading', { name: '原 CONFIRM 待核对' })).toBeVisible(); expect(screen.queryByRole('button', { name: '仅执行/恢复这个固定原动作' })).not.toBeInTheDocument(); expect(calls.filter((c) => c.path.endsWith('/execute'))).toHaveLength(0);
});
test('刷新只恢复workspace，需要独立GET才展示可写原Action；不自动金融POST', async () => {
  const body = dynamicPrepareFixture(); sessionStorage.setItem('bounded-funds-full-dynamic-goal-operation-v1:http://http-unit-fixture.local:workspace', JSON.stringify(dynamicLookupFixture(fixtureIntent(body)))); const calls = install(); open(); await screen.findByText(/保留原目标动作/); expect(calls).toHaveLength(0); expect(screen.queryByRole('button', { name: '提交原动作明确确认' })).not.toBeInTheDocument(); fireEvent.click(screen.getByRole('button', { name: '只读刷新保留的原目标动作' })); await screen.findByRole('button', { name: '提交原动作明确确认' }); expect(calls).toHaveLength(1); expect(calls[0]!.method).toBe('GET');
});
test('预览原请求存储被拒绝时不POST，保真实来源读取并显示错误', async () => {
  const calls = install(); open(); fireEvent.click(screen.getByRole('button', { name: '只读加载当前目标模型与证据' })); await screen.findByText(/原模型 VERIFIED/);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('TOOL_ONLY_STORAGE_DENIED'); }); fireEvent.click(screen.getByRole('button', { name: '读取服务器动态执行预览' })); await screen.findByRole('alert'); expect(calls.every((c) => c.method === 'GET')).toBe(true); expect(screen.queryByRole('button', { name: '明确准备原动态目标动作' })).not.toBeInTheDocument();
});
test('当前版本prop改变时旧preview不冒新版本，不发送prepare', async () => {
  const calls = install(); const view = open(); await loadPreview(); view.rerender(<Panel goal={{ ...dynamicExecutionGoal, policy_version_id: '92000000-0000-4000-8000-000000000090' }} userId={dynamicExecutionUser} epochId={dynamicExecutionEpoch} />);
  expect(screen.queryByRole('region', { name: '动态服务器预览' })).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: '明确准备原动态目标动作' })).not.toBeInTheDocument(); expect(calls.filter((c) => c.path.endsWith('/prepare'))).toHaveLength(0);
});
