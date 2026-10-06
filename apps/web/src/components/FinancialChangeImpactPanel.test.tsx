import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import FinancialChangeImpactPanel from './FinancialChangeImpactPanel';
import { financialCandidate, financialPolicy, financialPreviewFixture, financialUser, financialVersion } from '../tests/financial-preview-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const props = () => ({ policyId: financialPolicy, userId: financialUser, expectedVersionId: financialVersion, candidateConfiguration: financialCandidate() });
test('无自动POST，手动366日期三阶段/精确负差额与没有授权展示', async () => {
  const source = await financialPreviewFixture(); const calls = installHttpFixture(() => source); render(<FinancialChangeImpactPanel {...props()} />); expect(calls).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '计算财务修改影响' })); await screen.findByText('已计算条件曲线，候选尚未确认');
  expect(within(screen.getByLabelText('查看修改影响日期')).getAllByRole('option')).toHaveLength(366); expect(screen.getAllByText(/差额 ¥-1.50/, { selector: 'dd' })).toHaveLength(2);
  fireEvent.change(screen.getByLabelText('查看修改影响日期'), { target: { value: '365' } }); expect(screen.getByText('2027-10-05 · 第365日')).toBeInTheDocument();
  expect(within(screen.getByRole('article', { name: '修改影响付款后' })).getByText('候选条件现金 ¥988.51 · 余量 ¥788.51')).toBeInTheDocument();
  fireEvent.click(screen.getByText('当前目标归属与持仓（1 / 1）')); expect(screen.getByText(/未来分配 UNKNOWN/)).toBeVisible(); expect(screen.getByText(/原记录本金 ¥230.09 · 当前未返还本金 ¥0.00/)).toBeVisible();
  expect(screen.getByText(/该预览没有确认按钮/)).toBeVisible(); expect(calls).toHaveLength(1);
});
test('UNKNOWN不产生零差额或候选曲线，展示原值与具体未证明原因', async () => {
  const source = await financialPreviewFixture(true); installHttpFixture(() => source); render(<FinancialChangeImpactPanel {...props()} />); fireEvent.click(screen.getByRole('button', { name: '计算财务修改影响' }));
  await screen.findByText('UNKNOWN：候选财务影响尚未证明'); expect(screen.getByText('TEMPLATE_OR_SOURCE_NOT_PROVEN_SYNTHETIC')).toBeVisible(); expect(screen.getAllByText(/候选 未知 · 尚未证明 · 差额 未知/)).toHaveLength(2); expect(screen.getByText(/候选产品上限未知，差额不是零/)).toBeInTheDocument();
});
test('刷新失败隐藏先前成功/金额，不自动重试', async () => {
  const source = await financialPreviewFixture(); let ordinal = 0; const calls = installHttpFixture(() => { ordinal += 1; if (ordinal === 2) return { ...source, grants_authority: true }; return source; });
  render(<FinancialChangeImpactPanel {...props()} />); fireEvent.click(screen.getByRole('button', { name: '计算财务修改影响' })); await screen.findByText('已计算条件曲线，候选尚未确认'); fireEvent.click(screen.getByRole('button', { name: '计算财务修改影响' })); await screen.findByRole('alert'); expect(screen.queryByLabelText('查看修改影响日期')).not.toBeInTheDocument(); expect(screen.queryByText('已计算条件曲线，候选尚未确认')).not.toBeInTheDocument(); await waitFor(() => expect(calls).toHaveLength(2));
});
test('其他族门禁止新POST，当前candidate/version变化隐藏旧预览', async () => {
  const source = await financialPreviewFixture(); const calls = installHttpFixture(() => source); const view = render(<FinancialChangeImpactPanel {...props()} mutationBlocked />); expect(screen.getByRole('button', { name: '计算财务修改影响' })).toBeDisabled(); expect(calls).toHaveLength(0);
  view.rerender(<FinancialChangeImpactPanel {...props()} />); fireEvent.click(screen.getByRole('button', { name: '计算财务修改影响' })); await screen.findByText('已计算条件曲线，候选尚未确认'); view.rerender(<FinancialChangeImpactPanel {...props()} expectedVersionId="77000000-0000-0000-0000-000000000099" />); expect(screen.queryByLabelText('查看修改影响日期')).not.toBeInTheDocument(); expect(calls).toHaveLength(1);
});
