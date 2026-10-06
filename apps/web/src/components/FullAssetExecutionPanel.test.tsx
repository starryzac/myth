import { webcrypto } from 'node:crypto';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { assetConfirmFixture, assetEpoch, assetExecuteFixture, assetLookupFixture, assetMvpId, assetMvpVersion, assetPortfolioFixture, assetPortfolioId, assetPrepareFixture, assetPreviewFixture, assetResponseFixture, assetUser } from '../tests/full-asset-execution-fixture';
import { productPolicies } from '../tests/full-products-fixture';
import { policyFixture } from '../tests/policy-fixture';
import { stateFixture } from '../tests/demo-fixture';
import type { AssetConfirm, AssetPrepare } from '../api/full-asset-execution';
let operation: typeof import('../features/full-asset-execution-operation'), panel: typeof import('./FullAssetExecutionPanel');
const endpoint = 'http://unit-full-asset-ui.local';
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubEnv('VITE_API_BASE_URL', endpoint); vi.stubGlobal('crypto', webcrypto); operation = await import('../features/full-asset-execution-operation'); panel = await import('./FullAssetExecutionPanel'); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); sessionStorage.clear(); });
type Options = { unknownPreview?: boolean; notFound?: boolean; lostExecute?: boolean; rejectPrepare?: boolean; stage?: 'PREPARED' | 'CONFIRMED' | 'UNKNOWN' | 'SETTLED' };
function transport(options: Options = {}) {
  const calls: { path: string; method: string; body: unknown; raw: string | undefined }[] = []; let portfolio = assetPortfolioFixture(), consent = assetConfirmFixture();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input)).pathname, method = init?.method ?? 'GET', raw = init?.body === undefined ? undefined : String(init.body), body: unknown = raw === undefined ? undefined : JSON.parse(raw); calls.push({ path, method, body, raw }); let data: unknown;
    if (path === '/api/v1/accounts/summary') data = { simulation: true, user_id: assetUser, accounts: [], positions: [] };
    else if (path === '/api/v1/demo/state') { const state = stateFixture(); state.epoch_id = assetEpoch; data = state; }
    else if (path === '/api/v1/full-policies') data = productPolicies();
    else if (path === '/api/v1/policies') { const p = policyFixture('asset_authorization'); p.id = assetMvpId; p.current_version!.id = assetMvpVersion; p.current_version!.policy_id = p.id; data = { simulation: true, items: [p] }; }
    else if (path === '/api/v1/goals') data = { simulation: true, items: [] };
    else if (path.endsWith('/preview')) { portfolio = assetPortfolioFixture(body as AssetPrepare); consent = assetConfirmFixture(portfolio); data = assetPreviewFixture(!!options.unknownPreview, body as AssetPrepare); }
    else if (path.endsWith('/prepare')) {
      const original = operation.getFullAssetExecutionOperation().pending!; expect(raw).toBe(original.body_json); expect(sessionStorage.getItem(`bounded-funds-full-asset-execution-operation-v1:${endpoint}`)).toBe(JSON.stringify(original));
      if (options.rejectPrepare) return new Response(JSON.stringify({ error: { code: 'SOURCE_CHANGED', message: 'TOOL_ONLY_409', request_id: 'TOOL_ONLY' } }), { status: 409 });
      options.stage = 'PREPARED'; data = assetResponseFixture(options.stage, portfolio, consent);
    } else if (path.endsWith('/confirm')) { const original = operation.getFullAssetExecutionOperation().pending!; expect(raw).toBe(original.body_json); consent = body as AssetConfirm; options.stage = 'CONFIRMED'; data = assetResponseFixture(options.stage, portfolio, consent); }
    else if (path.endsWith('/execute-next')) { const original = operation.getFullAssetExecutionOperation().pending!; expect(raw).toBe(original.body_json); expect((body as ReturnType<typeof assetExecuteFixture>).expected_batch_number).toBe(1); options.stage = 'UNKNOWN'; if (options.lostExecute) throw new Error('TOOL_ONLY_LOST_REPLY'); data = assetResponseFixture(options.stage, portfolio, consent); }
    else if (path.includes('/by-key/')) { const intent = operation.getFullAssetExecutionOperation().pending!; const lookup = assetLookupFixture(intent, !options.notFound); if (lookup.original) lookup.original = assetResponseFixture(options.stage ?? 'PREPARED', portfolio, consent); data = lookup; }
    else if (path === `/api/v1/full-asset-executions/portfolios/${assetPortfolioId}`) data = assetResponseFixture(options.stage ?? 'CONFIRMED', portfolio, consent);
    else throw new Error(`TOOL_ONLY_UNHANDLED_PATH:${path}`);
    return new Response(JSON.stringify(data));
  })); return calls;
}
function open(blocked = false) { const Component = panel.default; return render(<Component userId={assetUser} epochId={assetEpoch} mutationBlocked={blocked} />); }
async function preview() { fireEvent.click(screen.getByRole('button', { name: '只读加载现有资产权限与周期' })); fireEvent.change(await screen.findByLabelText('完整资产策略'), { target: { value: assetPrepareFixture().full_policy_id } }); fireEvent.change(screen.getByLabelText('原MVP资产权限'), { target: { value: assetMvpId } }); fireEvent.click(screen.getByRole('button', { name: '读取服务器整组预览' })); return screen.findByRole('region', { name: '资产整组只读预览' }); }
async function recover() { await waitFor(() => expect(screen.getByRole('button', { name: '独立读取原资产请求结果' })).toBeEnabled()); fireEvent.click(screen.getByRole('button', { name: '独立读取原资产请求结果' })); }
async function readExisting() { fireEvent.change(screen.getByLabelText('读取既存原组合ID'), { target: { value: assetPortfolioId } }); fireEvent.click(screen.getByRole('button', { name: '只读读取原组合' })); return screen.findByRole('region', { name: '资产组合持久原状态' }); }

test('挂载无HTTP；手动加载真实权限，原整组金额/两批/1098点，preview后不自动prepare', async () => {
  const calls = transport(); open(); expect(calls).toHaveLength(0); const view = await preview(); expect(within(view).getByText('完整批次数 2。服务器提供哪些原期限就展示哪些，不补造7/30/90/180天产品。')).toBeVisible(); expect(within(view).getByRole('heading', { name: '完整原组合 · ¥200.05' })).toBeVisible(); expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1); expect(calls.at(-1)!.path).toBe('/api/v1/full-asset-executions/preview'); expect(screen.getByText(/跨银行操作整体回滚不可用/)).toBeVisible();
});
test('用户依次prepare→原GET→整组checkbox确认→原GET→固定单批；POST从不自行解门/推进', async () => {
  const options: Options = {}, calls = transport(options); open(); await preview(); fireEvent.click(screen.getByRole('button', { name: '明确保存原组合候选（尚未确认执行）' })); await waitFor(() => expect(operation.getFullAssetExecutionOperation().pending?.kind).toBe('PREPARE')); await recover(); await screen.findByRole('heading', { name: '原服务状态 PREPARED_UNRESERVED' }); expect(operation.getFullAssetExecutionOperation().pending).toBeNull(); expect(screen.getByRole('button', { name: '提交原整组明确确认' })).toBeDisabled();
  fireEvent.click(screen.getByLabelText('我复核全部原批次、金额、条款与整组hash，明确确认整组')); fireEvent.click(screen.getByRole('button', { name: '提交原整组明确确认' })); await waitFor(() => expect(operation.getFullAssetExecutionOperation().pending?.kind).toBe('CONFIRM')); await recover(); await screen.findByRole('heading', { name: '原服务状态 CONFIRMED_UNRESERVED' }); expect(operation.getFullAssetExecutionOperation().pending).toBeNull();
  fireEvent.click(screen.getByLabelText('我明确执行/恢复原批 1，使用原action与银行键')); fireEvent.click(screen.getByRole('button', { name: '仅执行这个固定原批' })); await waitFor(() => expect(operation.getFullAssetExecutionOperation().pending?.kind).toBe('EXECUTE')); await recover(); await screen.findByRole('heading', { name: '原服务状态 UNRESOLVED' }); expect(operation.getFullAssetExecutionOperation().pending?.kind).toBe('EXECUTE'); expect(screen.getByRole('button', { name: '仅执行这个固定原批' })).toBeDisabled(); expect(calls.filter((call) => call.path.endsWith('/execute-next'))).toHaveLength(1); const execute = calls.find((call) => call.path.endsWith('/execute-next'))!; expect(Object.keys(execute.body as object).sort()).toEqual(['accepted', 'expected_action_id', 'expected_batch_number', 'expected_epoch_id', 'reviewed_portfolio_hash']);
});
test('固定原批丢响应后先GET，用户checkbox手动仅重放同body；前批UNKNOWN不启动后批', async () => {
  const options: Options = { lostExecute: true }, calls = transport(options); open(); await readExisting(); fireEvent.click(screen.getByLabelText('我明确执行/恢复原批 1，使用原action与银行键')); fireEvent.click(screen.getByRole('button', { name: '仅执行这个固定原批' })); await screen.findByRole('alert'); const saved = operation.getFullAssetExecutionOperation().pending!; expect(calls.filter((call) => call.path.endsWith('/execute-next'))).toHaveLength(1);
  await recover(); await screen.findByText(/原结果仍未决\/未找到/); expect(screen.getByRole('button', { name: '手动恢复同一原资产请求' })).toBeDisabled(); options.lostExecute = false; fireEvent.click(screen.getByLabelText('我明确恢复同一完整原请求，不换键或推进下一批')); fireEvent.click(screen.getByRole('button', { name: '手动恢复同一原资产请求' })); await waitFor(() => expect(calls.filter((call) => call.path.endsWith('/execute-next'))).toHaveLength(2)); await waitFor(() => expect(operation.getFullAssetExecutionOperation().busy).toBe(false)); const attempts = calls.filter((call) => call.path.endsWith('/execute-next')); expect(attempts[0]!.raw).toBe(saved.body_json); expect(attempts[1]!.raw).toBe(saved.body_json); expect(operation.getFullAssetExecutionOperation().pending).toEqual(saved);
});
test('页面刷新不POST，他族blocked仍可核本族原键，NOT_FOUND非终局不换键或允许写', async () => {
  const intent = await operation.prepareAssetIntent('PREPARE', assetUser, assetPrepareFixture()); sessionStorage.setItem(`bounded-funds-full-asset-execution-operation-v1:${endpoint}`, JSON.stringify(intent)); const calls = transport({ notFound: true }); open(true); await screen.findByRole('region', { name: '待核对原资产请求' }); expect(calls).toHaveLength(0); await recover(); await screen.findByText(/原结果仍未决\/未找到/); expect(operation.getFullAssetExecutionOperation().pending).toEqual(intent); expect(calls.every((call) => call.method === 'GET')).toBe(true); expect(screen.getByLabelText('我明确恢复同一完整原请求，不换键或推进下一批')).toBeDisabled();
});
test('UNKNOWN/null无可保存组合；4xx仍持久原完整请求，不自动重复', async () => {
  const options: Options = { unknownPreview: true }, calls = transport(options); open(); await preview(); expect(screen.getByRole('button', { name: '明确保存原组合候选（尚未确认执行）' })).toBeDisabled(); expect(screen.getByText('TOOL_ONLY_SOURCE_NOT_PROVEN')).toBeVisible();
  options.unknownPreview = false; await preview(); options.rejectPrepare = true; fireEvent.click(screen.getByRole('button', { name: '明确保存原组合候选（尚未确认执行）' })); await screen.findByText(/TOOL_ONLY_409/); expect(operation.getFullAssetExecutionOperation().pending?.kind).toBe('PREPARE'); expect(calls.filter((call) => call.path.endsWith('/prepare'))).toHaveLength(1);
});
test('GET仅解除原已SETTLED批，后批要新的明确点击；历史同receipt不提供持续权限', async () => {
  const intent = await operation.prepareAssetIntent('EXECUTE', assetUser, assetExecuteFixture(), assetPortfolioFixture()); sessionStorage.setItem(`bounded-funds-full-asset-execution-operation-v1:${endpoint}`, JSON.stringify(intent)); const calls = transport({ stage: 'SETTLED' }); open(); await screen.findByRole('region', { name: '待核对原资产请求' }); await recover(); await screen.findByRole('heading', { name: '原服务状态 PARTIALLY_SETTLED' }); expect(operation.getFullAssetExecutionOperation().pending).toBeNull(); expect(screen.getByLabelText('我明确执行/恢复原批 2，使用原action与银行键')).toBeVisible(); expect(screen.getByRole('button', { name: '仅执行这个固定原批' })).toBeDisabled(); expect(calls.every((call) => call.method === 'GET')).toBe(true); expect(screen.getByText(/SERVICE_RECEIPTS_VERIFIED只表示原服务回执核验/)).toBeVisible();
});
