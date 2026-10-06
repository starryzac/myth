/** Parent host HTTP doubles; no bank or PostgreSQL acceptance evidence. */
import { webcrypto } from 'node:crypto';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import FullPoliciesPanel from './FullPoliciesPanel';
import { fullFlags, fullPolicyFixture } from '../tests/full-policy-fixture';
import { financialCandidate, financialPolicy, financialVersion, financialUser, financialPreviewFixture } from '../tests/financial-preview-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

async function install() {
  vi.stubGlobal('crypto', webcrypto);
  const response = await financialPreviewFixture(); const policy = fullPolicyFixture();
  policy.policy_id = financialPolicy; policy.current_version.policy_id = financialPolicy; policy.current_version.version_id = financialVersion;
  policy.current_version.confirmation.policy_id = financialPolicy; policy.current_version.confirmation.version_id = financialVersion;
  policy.current_version.configuration = response.before_configuration;
  policy.current_version.content_hash = response.current_configuration_hash; policy.current_version.confirmation.reviewed_hash = response.current_configuration_hash;
  const calls = installHttpFixture((method, path) => {
    if (path === '/api/v1/full-policies') return { ...fullFlags, items: [policy] };
    if (path === `/api/v1/full-policies/${financialPolicy}`) return policy;
    if (path === '/api/v1/accounts/summary') return { simulation: true, user_id: financialUser, accounts: [] };
    if (method === 'POST' && path.endsWith('/financial-change-preview')) return response;
    throw new Error(`SYNTHETIC_UNEXPECTED_${method}_${path}`);
  }); return calls;
}
async function open(blocked = false) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><FullPoliciesPanel blocked={blocked} /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: /HTTP夹具支出 · DatedExpensePolicy/ }));
  await screen.findByRole('region', { name: `完整版策略详情 ${financialPolicy}` });
}

test('资金差量只由当前草稿显式读取，不能替代原修改预览或解除确认门', async () => {
  const calls = await install(); await open();
  expect(calls.some((call) => call.path === '/api/v1/accounts/summary')).toBe(false);
  fireEvent.change(screen.getByLabelText('修改候选完整配置JSON（金额为整数分）'), { target: { value: JSON.stringify(financialCandidate()) } });
  fireEvent.click(screen.getByRole('button', { name: '打开候选资金差量预览' }));
  const read = await screen.findByRole('button', { name: '计算财务修改影响' });
  expect(calls.every((call) => call.method === 'GET')).toBe(true); fireEvent.click(read);
  await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1));
  expect(calls.find((call) => call.method === 'POST')).toEqual({ method: 'POST', path: `/api/v1/full-policies/${financialPolicy}/financial-change-preview`, body: { expected_version_id: financialVersion, configuration: financialCandidate() } });
  await waitFor(() => expect(screen.getByRole('button', { name: '计算财务修改影响' })).toBeEnabled());
  expect(screen.getByRole('button', { name: '明确确认修改' })).toBeDisabled();
  expect(screen.getByLabelText('我已复核原版本、配置hash与操作含义，明确提交所选命令')).not.toBeChecked();
  fireEvent.change(screen.getByLabelText('修改候选完整配置JSON（金额为整数分）'), { target: { value: '{' } });
  expect(screen.getByText(/候选JSON尚不是完整对象/)).toBeVisible();
  expect(screen.queryByRole('button', { name: '计算财务修改影响' })).not.toBeInTheDocument();
});

test('跨族未决门关闭新增候选预览入口且未展开不额外载入owner或POST', async () => {
  const calls = await install(); await open(true);
  expect(screen.getByRole('button', { name: '打开候选资金差量预览' })).toBeDisabled();
  expect(calls.some((call) => call.path === '/api/v1/accounts/summary')).toBe(false);
  expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
