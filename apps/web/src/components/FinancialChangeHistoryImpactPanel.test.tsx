import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import FinancialChangeHistoryImpactPanel from './FinancialChangeHistoryImpactPanel';
import { historyBinding, historyBody, historyFixture, historyUnknownFixture } from '../tests/financial-history-preview-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const props = () => ({ ...historyBinding(), candidateConfiguration: historyBody().configuration });

test('手动只读新协议，366日三阶段、原历史分母和负差额可读；无确认入口', async () => {
  const calls = installHttpFixture(() => historyFixture()); render(<FinancialChangeHistoryImpactPanel {...props()} />); expect(calls).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '计算再次修改影响' })); await screen.findByText('已计算历史链条件曲线，候选尚未确认');
  expect(within(screen.getByLabelText('查看修改影响日期')).getAllByRole('option')).toHaveLength(366);
  expect(screen.getAllByText(/差额 ¥-1.25/, { selector: 'dd' })).toHaveLength(2);
  fireEvent.click(screen.getByText('原历史链与完整分母')); expect(screen.getByText(/原版本实际\/捕获 2 \/ 2/)).toBeVisible();
  expect(screen.getByText(/不证明欠付金额、已结算、银行承诺或当前权限/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('查看修改影响日期'), { target: { value: '365' } }); expect(screen.getByText('2027-10-06 · 第365日')).toBeInTheDocument();
  expect(screen.getByText(/该预览没有确认按钮/)).toBeVisible(); expect(calls).toHaveLength(1);
});
test('不可重建UNKNOWN保留原值和精确原因，missing proof不称当前已验证', async () => {
  const source = historyUnknownFixture(); source.financial_impact.history_proof = null; installHttpFixture(() => source);
  render(<FinancialChangeHistoryImpactPanel {...props()} />); fireEvent.click(screen.getByRole('button', { name: '计算再次修改影响' }));
  await screen.findByText('UNKNOWN：再次修改财务影响尚未证明'); expect(screen.getByText('ORIGINAL_FULL_CURVE_NOT_RECONSTRUCTIBLE_FOR_THIS_HISTORY_BRANCH')).toBeVisible();
  fireEvent.click(screen.getByText('原历史链与完整分母')); expect(screen.getByText(/完整历史原件 MISSING/)).toBeVisible();
  expect(screen.getAllByText(/候选 未知 · 尚未证明 · 差额 未知/)).toHaveLength(2);
});
test('other-family门阻新POST；owner/epoch/version/config变动隐藏旧曲线', async () => {
  const calls = installHttpFixture(() => historyFixture()); const view = render(<FinancialChangeHistoryImpactPanel {...props()} mutationBlocked />);
  expect(screen.getByRole('button', { name: '计算再次修改影响' })).toBeDisabled(); expect(calls).toHaveLength(0);
  view.rerender(<FinancialChangeHistoryImpactPanel {...props()} />); fireEvent.click(screen.getByRole('button', { name: '计算再次修改影响' })); await screen.findByText('已计算历史链条件曲线，候选尚未确认');
  view.rerender(<FinancialChangeHistoryImpactPanel {...props()} expectedVersionNumber={3} />); expect(screen.queryByLabelText('查看修改影响日期')).not.toBeInTheDocument(); expect(calls).toHaveLength(1);
});
test('拒绝/解析失败隐藏旧成功，用户手动重新读且不自动重试', async () => {
  let count = 0; const calls = installHttpFixture(() => { count += 1; const value = historyFixture(); if (count === 2) Object.assign(value.financial_impact, { grants_authority: true }); return value; });
  render(<FinancialChangeHistoryImpactPanel {...props()} />); fireEvent.click(screen.getByRole('button', { name: '计算再次修改影响' })); await screen.findByText('已计算历史链条件曲线，候选尚未确认');
  fireEvent.click(screen.getByRole('button', { name: '计算再次修改影响' })); await screen.findByRole('alert'); expect(screen.queryByLabelText('查看修改影响日期')).not.toBeInTheDocument(); expect(calls).toHaveLength(2);
});
