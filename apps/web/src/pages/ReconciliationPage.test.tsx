import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, test } from 'vitest';
import ReconciliationPage from './ReconciliationPage';
import { reconcileAccount, reconcileAction, reconcileGoal, reconciliationFixture, reconcilePosition } from '../tests/full-reconciliation-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
function openPage() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><ReconciliationPage /></QueryClientProvider>); }
test('三账分别显示原精确金额、八表分母；MATCHED不把准备行动译作已执行', async () => {
  const calls = installHttpFixture(() => reconciliationFixture()); openPage(); await screen.findByRole('heading', { name: '当前范围匹配 · MATCHED' });
  const cash = screen.getByRole('article', { name: `账户 ${reconcileAccount}` }); expect(within(cash).getAllByText('¥1,000.07')).toHaveLength(2); const goal = screen.getByRole('article', { name: `目标归属 ${reconcileGoal}` }); expect(within(goal).getByText(/原已归属 ¥1,000.09/)).toBeVisible(); expect(screen.getByRole('article', { name: `持仓 ${reconcilePosition}` })).toBeVisible(); expect(within(screen.getByRole('region', { name: '完整原行分母' })).getAllByRole('listitem')).toHaveLength(8);
  const action = screen.getByRole('article', { name: `原行动 ${reconcileAction}` }); expect(within(action).getByRole('heading', { name: /原行动已准备，尚无银行原件/ })).toBeVisible(); expect(within(action).getByText(/未知 · 未证明 \/ 未知 · 未证明 \/ 未知 · 未证明/)).toBeVisible(); expect(screen.getByText(/economic_verified=false/)).toBeVisible(); expect(screen.queryByRole('button', { name: /修复|执行|重试|扣款/ })).not.toBeInTheDocument(); expect(calls).toEqual([{ method: 'GET', path: '/api/v1/reconciliation/current', body: undefined }]);
});
test('SETTLED但应用UNKNOWN无回执保原key、实际两腿和正负差额，不称资金未变', async () => {
  const calls = installHttpFixture(() => reconciliationFixture('UNRESOLVED')); openPage(); await screen.findByRole('heading', { name: '需要人工核对 · MANUAL_REVIEW_REQUIRED' }); const action = screen.getByRole('article', { name: `原行动 ${reconcileAction}` });
  expect(within(action).getByRole('heading', { name: /银行已结算，应用结果未解决/ })).toBeVisible(); expect(within(action).getByText(/状态 UNKNOWN/)).toBeVisible(); expect(within(action).getByText('synthetic-original-key')).toBeVisible(); expect(within(action).getByText(/银行可能已改变资金/)).toBeVisible(); expect(within(action).getByText(/原应用回执缺失/)).toBeVisible(); expect(screen.getByText('¥-100.01')).toBeVisible(); expect(within(screen.getByRole('region', { name: '具体未证明项' })).getByText(/SYNTHETIC_PENDING/, { selector: 'strong' })).toBeVisible(); expect(calls.every((call) => call.method === 'GET')).toBe(true);
});
test('UNKNOWN与容量不足显示实际100001原分母及null，不隐去未覆盖或补0', async () => {
  installHttpFixture(() => reconciliationFixture('PARTIAL')); openPage(); await screen.findByRole('heading', { name: '未证明 · UNKNOWN' }); const cash = screen.getByRole('article', { name: `账户 ${reconcileAccount}` }); expect(within(cash).getAllByText('未知 · 未证明')).toHaveLength(2); expect(within(cash).queryByText('¥0.00')).not.toBeInTheDocument(); expect(screen.getByText(/捕获 4 \/ 实际 100001/)).toBeVisible(); expect(screen.getByText(/本表未覆盖完整原分母/)).toBeVisible();
});
test('原坏hash和银行腿矛盾仅作为未核人工诊断可读，原JSON保留而非修账', async () => {
  installHttpFixture(() => reconciliationFixture('CORRUPT')); openPage(); await screen.findByRole('heading', { name: '需要人工核对 · MANUAL_REVIEW_REQUIRED' }); expect(within(screen.getByRole('article', { name: `原行动 ${reconcileAction}` })).getByText('stored-invalid-hash')).toBeVisible(); expect(screen.getByText(/没有创建人工任务、修账/)).toBeVisible(); fireEvent.click(screen.getByText('逐腿原值与引用')); expect(within(screen.getByRole('region', { name: '原银行结算腿' })).getByText(/原变动 ¥1,000.08/)).toBeVisible();
});
test('只读刷新失败不把旧报告当当前成功，也不发修复请求', async () => {
  let reads = 0; const calls = installHttpFixture(() => ++reads === 1 ? reconciliationFixture() : new Response(JSON.stringify({ error: { code: 'FAILED', message: '原读取失败', request_id: 'synthetic-read' } }), { status: 409 })); openPage(); await screen.findByRole('heading', { name: '当前范围匹配 · MATCHED' }); fireEvent.click(screen.getByRole('button', { name: '只读刷新当前对账' })); const alert = await screen.findByRole('alert'); expect(alert).toHaveTextContent('未把上次报告当成当前成功'); expect(screen.queryByRole('heading', { name: '当前范围匹配 · MATCHED' })).not.toBeInTheDocument(); expect(calls).toHaveLength(2); expect(calls.every((call) => call.method === 'GET' && call.body === undefined)).toBe(true);
});
test('银行腿20项只读分页保完整21条捕获和分母，切页不发新请求', async () => {
  const source = reconciliationFixture('UNKNOWN');
  for (let n = 100; n < 117; n++) source.bank_postings.push({ ...source.bank_postings[0]!, posting_id: `91000000-0000-4000-8000-${String(n).padStart(12, '0')}`, ledger_key: `SYNTHETIC:${n}` });
  source.inventory.find((row) => row.table === 'simulated_bank_postings')!.actual_count = 21; source.inventory.find((row) => row.table === 'simulated_bank_postings')!.captured_count = 21;
  const calls = installHttpFixture(() => source); openPage(); await screen.findByRole('heading', { name: '未证明 · UNKNOWN' }); fireEvent.click(screen.getByText('逐腿原值与引用')); const region = screen.getByRole('region', { name: '原银行结算腿' }); expect(within(region).getAllByRole('article')).toHaveLength(20); expect(within(region).getByText(/完整捕获 21条原腿/)).toBeVisible(); fireEvent.click(within(region).getByRole('button', { name: '下一页原银行腿' })); expect(within(region).getAllByRole('article')).toHaveLength(1); expect(within(region).getByRole('button', { name: '下一页原银行腿' })).toBeDisabled(); expect(calls).toHaveLength(1);
});
