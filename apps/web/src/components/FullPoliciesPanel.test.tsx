import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import FullPoliciesPanel from './FullPoliciesPanel';
import type { FullPolicyIntent } from '../features/full-policy-operation';
import { clearFullPolicyOperationAfterLookup, endFullPolicyAttempt, getFullPolicyOperation, prepareFullPolicyIntent } from '../features/full-policy-operation';
import { createFullIntent, fullCandidateFixture, fullCatalogFixture, fullCommandFixture, fullConfig, fullFlags, fullLookupFixture, fullPolicyFixture, fullPolicyId, fullPreviewFixture, fullReceiptFixture, fullSchemaFixture, fullEpochId, fullHash, fullVersionId } from '../tests/full-policy-fixture';
afterEach(() => { cleanup(); endFullPolicyAttempt(); const original = getFullPolicyOperation().pending; if (original) clearFullPolicyOperationAfterLookup(original, fullLookupFixture(original)); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
type Call = { path: string; method: string; body: Record<string, unknown> | undefined; original: string | undefined };
function transport(options: { empty?: boolean; suspended?: boolean; archived?: boolean; lose?: boolean; notFound?: boolean; wrongLookup?: boolean; missingVersion?: boolean } = {}) {
  vi.stubEnv('VITE_API_BASE_URL', 'http://unit-full-panel.local'); const calls: Call[] = []; let original: FullPolicyIntent | null = null; let writes = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)); const path = url.pathname; const method = init?.method ?? 'GET'; const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined; calls.push({ path, method, body, original: init?.body === undefined ? undefined : String(init.body) });
    if (path === '/api/v1/policy-templates') return new Response(JSON.stringify(fullCatalogFixture()));
    if (path.includes('/policy-templates/') && path.endsWith('/schema')) return new Response(JSON.stringify(fullSchemaFixture(path.split('/').at(-2) as 'DatedExpensePolicy')));
    if (path.endsWith('/policy-templates/validate')) return new Response(JSON.stringify(fullCandidateFixture()));
    if (path.endsWith('/change-preview')) return new Response(JSON.stringify(fullPreviewFixture()));
    if (path.includes('/commands/by-key/')) { if (!original) original = getFullPolicyOperation().pending; const lookup = fullLookupFixture(original ?? createFullIntent(), !options.notFound); if (options.wrongLookup) lookup.idempotency_key = 'different-key'; return new Response(JSON.stringify(lookup)); }
    if (method === 'POST') {
      writes++; const kind = path.endsWith('/confirm') ? 'CREATE' : path.split('/').at(-1)!.toUpperCase() as FullPolicyIntent['kind'];
      original = prepareFullPolicyIntent({ kind, policy_id: kind === 'CREATE' ? null : fullPolicyId, original_epoch_id: kind === 'CREATE' ? null : fullEpochId, original_configuration_hash: typeof body?.reviewed_hash === 'string' ? body.reviewed_hash : fullHash, path: path.replace('/api/v1', ''), body: body as FullPolicyIntent['body'] });
      if (options.lose && writes === 1) throw new Error('UNIT_RESPONSE_LOST'); return new Response(JSON.stringify(fullReceiptFixture(original)));
    }
    if (path.endsWith('/versions')) { const version = fullPolicyFixture().current_version; if (options.missingVersion) { version.version_id = '71000000-0000-4000-8000-000000000099'; version.confirmation.version_id = version.version_id; } return new Response(JSON.stringify({ ...fullFlags, items: [version] })); }
    if (path.endsWith('/commands')) return new Response(JSON.stringify({ ...fullFlags, items: [fullCommandFixture()] }));
    const policy = fullPolicyFixture(options.suspended ? 'SUSPENDED' : 'ACTIVE'); if (options.archived) { policy.effective_status = 'ARCHIVED'; policy.reference_validation = 'ARCHIVED'; policy.planning_confirmation_valid = false; }
    return new Response(JSON.stringify(path === '/api/v1/full-policies' ? { ...fullFlags, items: options.empty ? [] : [policy] } : policy));
  })); return calls;
}
function open(blocked = false) { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); return render(<QueryClientProvider client={client}><FullPoliciesPanel blocked={blocked} /></QueryClientProvider>); }
async function selectPolicy() { fireEvent.click(await screen.findByRole('button', { name: /HTTP夹具支出 · DatedExpensePolicy ·/ })); return await screen.findByRole('region', { name: `完整版策略详情 ${fullPolicyId}` }); }
function authorizePolicy() { fireEvent.change(screen.getByLabelText('完整版命令理由'), { target: { value: 'USER_EXPLICIT_FIXTURE_REASON' } }); fireEvent.click(screen.getByLabelText('我已复核原版本、配置hash与操作含义，明确提交所选命令')); }
async function prepareCreate() { fireEvent.click(screen.getByRole('button', { name: '准备新完整版策略' })); await screen.findByRole('combobox', { name: '策略模板' }); fireEvent.change(screen.getByLabelText('新策略完整配置JSON（金额为整数分）'), { target: { value: JSON.stringify(fullConfig()) } }); fireEvent.click(screen.getByRole('button', { name: '仅校验候选配置' })); await screen.findByRole('region', { name: '服务器规范化策略候选' }); fireEvent.change(screen.getByLabelText('首次确认理由'), { target: { value: 'USER_EXPLICIT_FIXTURE_CREATE' } }); fireEvent.click(screen.getByLabelText('我已复核规范化候选和配置hash，明确确认这份新策略')); }
test('完整列表/版本/原命令只读加载，历史和未实现执行边界可见且无POST', async () => {
  const calls = transport(); open(); const detail = await selectPolicy(); expect(within(detail).getByText(/银行授权=false/)).toBeVisible(); fireEvent.click(screen.getByRole('button', { name: '读取完整版本与原命令历史' })); await screen.findByText(/命令 1 · CREATE/); expect(screen.getByText(/原回执不是当前授权/)).toBeVisible(); expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
test('候选Schema与字段校验不自动confirm；改变配置立即丢弃已复核候选', async () => {
  const calls = transport({ empty: true }); open(); await screen.findByText(/当前未登记独立FULL策略/); await prepareCreate(); expect(screen.getByRole('button', { name: '明确确认新策略' })).toBeEnabled(); expect(calls.filter((call) => call.path.endsWith('/confirm'))).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('新策略完整配置JSON（金额为整数分）'), { target: { value: '{}' } }); expect(screen.queryByRole('region', { name: '服务器规范化策略候选' })).not.toBeInTheDocument(); expect(screen.getByRole('button', { name: '明确确认新策略' })).toBeDisabled();
});
test('首次确认响应丢失后保原body/key；NOT_FOUND不清且手动只重放同body/key', async () => {
  const calls = transport({ empty: true, lose: true, notFound: true }); open(); await screen.findByText(/当前未登记独立FULL策略/); await prepareCreate(); fireEvent.click(screen.getByRole('button', { name: '明确确认新策略' })); await screen.findByRole('region', { name: '待核对的完整版原请求' }); await screen.findByText(/原请求与键已保留/);
  expect(calls.filter((call) => call.path.endsWith('/confirm'))).toHaveLength(1); fireEvent.click(screen.getByRole('button', { name: '只读核对原命令' })); await screen.findByText(/NOT_FOUND不是最终未提交证明/); expect(getFullPolicyOperation().pending).not.toBeNull(); expect(screen.getByRole('button', { name: '收起新策略候选' })).toBeDisabled();
  fireEvent.click(screen.getByLabelText('我已核对原请求，明确仅重放同一body和同一键')); fireEvent.click(screen.getByRole('button', { name: '手动重放同一原请求' })); await screen.findByText(/收到原服务回执/); const writes = calls.filter((call) => call.path.endsWith('/confirm')); expect(writes).toHaveLength(2); expect(writes[0]!.original).toBe(writes[1]!.original); expect(getFullPolicyOperation().pending!.body.idempotency_key).toBe(writes[0]!.body!.idempotency_key);
});
test('暂停原expectedVersion保持不造grant，HTTP0成功仍pending；匹配RECORDED才解除', async () => {
  const calls = transport(); open(); await selectPolicy(); authorizePolicy(); fireEvent.click(screen.getByRole('button', { name: '明确暂停策略' })); await screen.findByText(/收到原服务回执/); const stop = calls.find((call) => call.path.endsWith('/suspend'))!; expect(Object.keys(stop.body!).sort()).toEqual(['expected_version_id', 'idempotency_key', 'reason']); expect(stop.body!.expected_version_id).toBe(fullVersionId); expect(getFullPolicyOperation().pending).not.toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '只读核对原命令' })); await screen.findByText(/已解除本族待核对门/); expect(getFullPolicyOperation().pending).toBeNull(); expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1);
});
test('恢复沿原配置hash和原预期version明确确认，不发送未确认configuration', async () => {
  const calls = transport({ suspended: true }); open(); await selectPolicy(); authorizePolicy(); fireEvent.click(screen.getByRole('button', { name: '明确重新确认并恢复' })); await screen.findByText(/收到原服务回执/); const resume = calls.find((call) => call.path.endsWith('/resume'))!; expect(resume.body).toEqual({ expected_version_id: fullVersionId, reason: 'USER_EXPLICIT_FIXTURE_REASON', idempotency_key: expect.any(String), reviewed_hash: fullHash, accepted: true });
});
test('修改需原只读preview+复核hash，未知财务delta不显示成零，发送真实after配置', async () => {
  const calls = transport(); open(); await selectPolicy(); fireEvent.change(screen.getByLabelText('修改候选完整配置JSON（金额为整数分）'), { target: { value: JSON.stringify(fullPreviewFixture().after_configuration) } }); fireEvent.click(screen.getByRole('button', { name: '只读预览完整版修改' })); const preview = await screen.findByRole('region', { name: '完整版策略只读修改预览' }); expect(within(preview).getAllByText('UNKNOWN · NOT_IMPLEMENTED')).toHaveLength(3); authorizePolicy(); fireEvent.click(screen.getByRole('button', { name: '明确确认修改' })); await screen.findByText(/收到原服务回执/); const change = calls.find((call) => call.path.endsWith('/change'))!; expect(change.body!.reviewed_hash).toBe(fullPreviewFixture().configuration_hash); expect(change.body!.configuration).toEqual(fullPreviewFixture().after_configuration); expect(change.body!.expected_version_id).toBe(fullVersionId); expect(calls.filter((call) => call.path.endsWith('/change-preview'))).toHaveLength(1);
});
test('历史周期或其他family blocked不能新写，完整readonly历史仍可核', async () => {
  const calls = transport({ archived: true }); open(true); await selectPolicy(); expect(screen.getByText(/这是历史周期原件/)).toBeVisible(); expect(screen.getByRole('button', { name: '明确暂停策略' })).toBeDisabled(); expect(screen.getByRole('button', { name: '明确撤销策略' })).toBeDisabled(); expect(screen.getByRole('button', { name: '准备新完整版策略' })).toBeDisabled(); expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
test('lookup错原键不放行新写，不自动POST；本地恢复保原不可编辑identity', async () => {
  const original = createFullIntent(); sessionStorage.setItem('bounded-funds-full-policy-operation-v1:http://unit-full-panel.local', JSON.stringify(original)); const calls = transport({ wrongLookup: true }); open(); await screen.findByRole('region', { name: '待核对的完整版原请求' }); expect(calls.every((call) => call.method === 'GET')).toBe(true); fireEvent.click(screen.getByRole('button', { name: '只读核对原命令' })); await screen.findByText(/原请求继续保留/); expect(getFullPolicyOperation().pending).toEqual(original); expect(screen.getByRole('button', { name: '准备新完整版策略' })).toBeDisabled();
});
test('目录完整保留12模板，LongTerm选择路由原目标桥而不能此页独立确认', async () => {
  transport({ empty: true }); open(); await screen.findByText(/当前未登记独立FULL策略/); fireEvent.click(screen.getByRole('button', { name: '准备新完整版策略' })); const select = await screen.findByRole('combobox', { name: '策略模板' }); expect(within(select).getAllByRole('option')).toHaveLength(12); fireEvent.change(select, { target: { value: 'LongTermGoalPolicy' } }); await waitFor(() => expect(screen.getByRole('link', { name: '转目标中心复核双hash' })).toHaveAttribute('href', '#goals')); expect(screen.getByRole('button', { name: '明确确认新策略' })).toBeDisabled();
});
test('独立历史读缺所选原version时明确UNKNOWN，不回落当前详情伪造历史成功', async () => {
  transport({ missingVersion: true }); open(); await selectPolicy(); fireEvent.click(screen.getByRole('button', { name: '读取完整版本与原命令历史' })); await screen.findByText(/版本列表缺少所选原版本/); expect(screen.queryByText('所选原版本结构化配置与确认')).not.toBeInTheDocument(); expect(screen.getByRole('combobox', { name: '选择原策略版本' })).toHaveValue('');
});
