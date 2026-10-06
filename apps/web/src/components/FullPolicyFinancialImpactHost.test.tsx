/** Local HTTP doubles only: no PG, bank or browser acceptance. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import FullPolicyFinancialImpactHost from './FullPolicyFinancialImpactHost';
import { historyBinding, historyBody, historyFixture } from '../tests/financial-history-preview-fixture';
import { installHttpFixture } from '../tests/policy-fixture';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const binding = historyBinding();
const host = (blocked = false) => <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><FullPolicyFinancialImpactHost policyId={binding.policyId} expectedVersionId={binding.expectedVersionId} expectedVersionNumber={binding.expectedVersionNumber} expectedEpochId={binding.expectedEpochId} candidateText={JSON.stringify(historyBody().configuration)} blocked={blocked} /></QueryClientProvider>;

test('实际后续版本宿主只手动GET当前owner与新history-v2 POST，复用原配置身份', async () => {
  const calls = installHttpFixture((method, path, body) => {
    if (method === 'GET' && path === '/api/v1/accounts/summary') return { simulation: true, user_id: binding.userId, accounts: [] };
    expect(method).toBe('POST'); expect(path).toBe(`/api/v1/full-policies/${binding.policyId}/financial-change-preview-history`);
    expect(body).toEqual(historyBody()); return historyFixture();
  });
  render(host()); expect(calls).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '打开候选资金差量预览' }));
  const calculate = await screen.findByRole('button', { name: '计算再次修改影响' });
  await waitFor(() => expect(calculate).toBeEnabled()); expect(calls).toHaveLength(1);
  expect(screen.queryByRole('button', { name: '计算财务修改影响' })).not.toBeInTheDocument();
  fireEvent.click(calculate); await screen.findByText('已计算历史链条件曲线，候选尚未确认');
  expect(calls).toHaveLength(2);
});

test('跨功能写门关闭宿主入口，不发owner或资金预览请求', () => {
  const calls = installHttpFixture(() => { throw new Error('Blocked gate must send nothing'); });
  render(host(true)); expect(screen.getByRole('button', { name: '打开候选资金差量预览' })).toBeDisabled();
  expect(calls).toHaveLength(0);
});
