import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, test } from 'vitest';
import FullProductsPage from './FullProductsPage';
import { assetOptions, assetPlanFixture, catalogueId, productCatalogue, productPolicies, productPositions, recoveryPlanFixture, recoveryPolicyId } from '../tests/full-products-fixture';
import { fullPolicyId } from '../tests/full-policy-fixture';
import { installHttpFixture } from '../tests/policy-fixture';

function openPage() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><FullProductsPage /></QueryClientProvider>); }
function handler(path: string) { if (path.endsWith('/positions')) return productPositions(); if (path.endsWith('/full-policies')) return productPolicies(); if (path.endsWith('/asset-allocation')) return assetPlanFixture(); if (path.endsWith('/recovery-planning')) return recoveryPlanFixture(); return productCatalogue(); }
test('真实目录原字段/历史筛选、未知持仓日期与未实现续期可见；初始无资金或规划写入', async () => {
  const calls = installHttpFixture((_method, path) => path.endsWith('/catalog/products') ? productCatalogue(true) : handler(path)); openPage();
  const version = await screen.findByRole('option', { name: /SYNTHETIC_FIXED_30D v1/ }); expect(version).toHaveValue(catalogueId);
  fireEvent.change(screen.getByLabelText('产品原版本'), { target: { value: catalogueId } });
  expect(screen.getByText(/SYNTHETIC_SOURCE_DRIFT_RETAINED/, { selector: 'p' })).toBeVisible(); expect(screen.getByText(/续期执行接口未实现/)).toBeVisible(); expect(screen.getByText(/rollover=UNKNOWN，auto_rollover=UNKNOWN/)).toBeVisible();
  expect(await screen.findByRole('heading', { name: 'UNKNOWN · 原本金可用日期缺失' })).toBeVisible(); expect(screen.getByText(/本金 ¥670.03/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('版本来源筛选'), { target: { value: 'MATCHED' } }); expect(screen.queryByRole('option', { name: /SYNTHETIC_FIXED_30D v1/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('版本来源筛选'), { target: { value: 'DRIFTED' } }); expect(screen.getByRole('option', { name: /当前源漂移/ })).toBeVisible();
  expect(calls).toHaveLength(3); expect(calls.every((call) => call.method === 'GET' && call.body === undefined)).toBe(true); expect(screen.queryByRole('button', { name: /购买|赎回|自动续期/ })).not.toBeInTheDocument();
});
test('用户只读比较期限/今日条件组合；1098点是366日期三阶段，缺7/90类与原分桶可见', async () => {
  const options = { ...assetOptions(), mode: 'FIXED_LADDER' as const }; const calls = installHttpFixture((_method, path) => path.endsWith('/asset-allocation') ? assetPlanFixture(options) : handler(path)); openPage();
  await screen.findByRole('option', { name: 'HTTP夹具资产声明 · ACTIVE' }); fireEvent.change(screen.getByLabelText('实际完整资产声明'), { target: { value: fullPolicyId } }); fireEvent.change(screen.getByLabelText('规划模式'), { target: { value: 'FIXED_LADDER' } }); fireEvent.click(screen.getByRole('button', { name: '读取原策略条件组合' }));
  const result = await screen.findByRole('region', { name: '资产规划结果' }); expect(within(result).getByRole('heading', { name: '条件规划：OPTIMAL' })).toBeVisible();
  expect(within(result).getAllByText(/1098个原阶段点/)).toHaveLength(2); expect(within(result).getAllByText(/不是1098天/)).toHaveLength(2); expect(within(result).getByText(/FIXED_7D、FIXED_90D/)).toBeVisible(); expect(within(result).getByText(/SINGLE_MATURITY_AVAILABLE/, { selector: 'p' })).toBeVisible(); expect(within(result).getByText(/今日条件申购/)).toBeVisible(); expect(within(result).getByRole('heading', { name: /2026-11-04T12:00:00Z/ })).toBeVisible();
  expect(calls.filter((call) => call.path.endsWith('/asset-allocation'))).toHaveLength(1); expect(calls.every((call) => call.method === 'GET' && call.body === undefined)).toBe(true);
});
test('原报价整数损失和有效期、ASK_ONCE与无损条件分开显示；不生成新报价或确认', async () => {
  const calls = installHttpFixture((_method, path) => handler(path)); openPage(); await screen.findByRole('option', { name: 'HTTP夹具恢复声明 · ACTIVE' }); fireEvent.change(screen.getByLabelText('实际完整恢复声明'), { target: { value: recoveryPolicyId } }); fireEvent.click(screen.getByRole('button', { name: '读取原报价条件影响' }));
  const result = await screen.findByRole('region', { name: '原报价提前支取影响' }); expect(within(result).getByText('¥1.67')).toBeVisible(); expect(within(within(result).getByText('原费用').parentElement!).getByText('¥0.00')).toBeVisible(); expect(within(result).getByText('¥668.36')).toBeVisible(); expect(within(result).getByText(/BANK_CONFIRMED/, { selector: 'p' })).toBeVisible(); expect(within(result).getByText(/有效至 2026-10-05T12:15:00Z/)).toBeVisible(); expect(within(result).getByRole('heading', { name: /ASK_ONCE/ })).toBeVisible(); expect(within(result).getByText(/持续安全首阶段/)).toBeVisible(); expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
test('来源UNKNOWN保持null及具体issue，没有正值计划或伪0', async () => {
  installHttpFixture((_method, path) => path.endsWith('/asset-allocation') ? assetPlanFixture(assetOptions(), true) : handler(path)); openPage(); await screen.findByRole('option', { name: 'HTTP夹具资产声明 · ACTIVE' }); fireEvent.change(screen.getByLabelText('实际完整资产声明'), { target: { value: fullPolicyId } }); fireEvent.click(screen.getByRole('button', { name: '读取原策略条件组合' }));
  const result = await screen.findByRole('region', { name: '资产规划结果' }); expect(within(result).getByRole('heading', { name: 'UNKNOWN · 资产来源未证明' })).toBeVisible(); expect(within(result).getByText(/SYNTHETIC_MISSING/, { selector: 'p' })).toBeVisible(); expect(within(result).queryByText('¥0.00')).not.toBeInTheDocument(); expect(within(result).queryByRole('heading', { name: '今日条件批次到期分桶' })).not.toBeInTheDocument();
});
test('损坏原资金守恒响应显示错误，不发布规划；更早截止无时区不发请求', async () => {
  const bad = assetPlanFixture(); bad.allocation!.batches[0]!.cash_uses[0]!.amount_cents++;
  const calls = installHttpFixture((_method, path) => path.endsWith('/asset-allocation') ? bad : handler(path)); openPage(); await screen.findByRole('option', { name: 'HTTP夹具资产声明 · ACTIVE' }); fireEvent.change(screen.getByLabelText('实际完整资产声明'), { target: { value: fullPolicyId } }); fireEvent.click(screen.getByRole('button', { name: '读取原策略条件组合' })); await screen.findByRole('alert'); expect(screen.queryByRole('region', { name: '资产规划结果' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('实际完整恢复声明'), { target: { value: recoveryPolicyId } }); fireEvent.change(screen.getByLabelText('更早回款截止（带时区ISO，可留空）'), { target: { value: '2026-10-07T09:00:00' } }); fireEvent.click(screen.getByRole('button', { name: '读取原报价条件影响' })); expect(calls.filter((call) => call.path.endsWith('/recovery-planning'))).toHaveLength(0); expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
