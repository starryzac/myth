import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import MultiTemplateFinancialPreviewPanel from './MultiTemplateFinancialPreviewPanel';
import { multiFixture, multiInventoryFixture, multiSource, multiUnknown } from '../tests/multi-template-preview-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function http(unknown = false) { const v = multiInventoryFixture(); return installHttpFixture((method, path) => method === 'POST' ? unknown ? multiUnknown() : multiFixture() : path.endsWith('/dashboard') ? v.dashboard : path.endsWith('/full-policies') ? v.full : v.mvp); }
async function select() { fireEvent.click(screen.getByRole('button', { name: '读取当前策略来源' })); await screen.findByLabelText('选择当前策略'); fireEvent.change(screen.getByLabelText('选择当前策略'), { target: { value: `MVP_POLICY:${multiSource().policyId}` } }); const c = structuredClone(multiSource().configuration); c.amount_cents = 3000; fireEvent.change(screen.getByLabelText('完整候选配置（仅当前模板字段）'), { target: { value: JSON.stringify(c) } }); }
test('无自动请求，手动完整366日期3阶段/负差额/只读无confirm', async () => {
  const calls = http(); render(<MultiTemplateFinancialPreviewPanel />); expect(calls).toHaveLength(0); await select(); expect(calls.every((r) => r.method === 'GET')).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '计算多模板只读影响' })); await screen.findByText(/PROJECTED · MVP保护差量/);
  expect(within(screen.getByLabelText('选择预览日期')).getAllByRole('option')).toHaveLength(366); expect(screen.getAllByText(/差额 ¥-10.00/, { selector: 'dd' })).toHaveLength(2);
  fireEvent.change(screen.getByLabelText('选择预览日期'), { target: { value: '365' } }); expect(screen.getByText(/第365日/, { selector: 'option' })).toBeInTheDocument();
  expect(screen.getByText(/该预览没有确认按钮/)).toBeInTheDocument(); expect(calls.filter((r) => r.method === 'POST')).toHaveLength(1);
});
test('他族gate只阻POST、自身来源GET可达；编辑后旧预览废弃', async () => {
  const calls = http(); const view = render(<MultiTemplateFinancialPreviewPanel mutationBlocked />); await select(); expect(screen.getByRole('button', { name: '计算多模板只读影响' })).toBeDisabled(); expect(calls).toHaveLength(3);
  view.rerender(<MultiTemplateFinancialPreviewPanel />); fireEvent.click(screen.getByRole('button', { name: '计算多模板只读影响' })); await screen.findByText(/PROJECTED · MVP保护差量/);
  fireEvent.change(screen.getByLabelText('完整候选配置（仅当前模板字段）'), { target: { value: '{}' } }); expect(screen.queryByLabelText('选择预览日期')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '计算多模板只读影响' })); await screen.findByRole('alert'); expect(calls.filter((r) => r.method === 'POST')).toHaveLength(1);
});
test('UNKNOWN保留原1098与真实原因/null，无自动重试', async () => {
  const calls = http(true); render(<MultiTemplateFinancialPreviewPanel />); await select(); fireEvent.click(screen.getByRole('button', { name: '计算多模板只读影响' })); await screen.findByText(/UNKNOWN · 候选金融影响未支持/);
  expect(screen.getByText('TOOL_ONLY_MISSING_ORIGINAL')).toBeInTheDocument(); expect(screen.getAllByText(/差额 UNKNOWN/, { selector: 'dd' })).toHaveLength(2); expect(calls.filter((r) => r.method === 'POST')).toHaveLength(1);
});
