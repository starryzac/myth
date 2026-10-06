import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import PolicyCenterPage from './PolicyCenterPage';
import { compilationFixture, failure, hash, installHttpFixture, lifecycleFixture, policyFixture, policyId, previewFixture, proposalFixture } from '../tests/policy-fixture';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function openPage() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); render(<QueryClientProvider client={client}><PolicyCenterPage /></QueryClientProvider>); }

test('修改必须真实预览，编辑即废弃预览与接受，网络重试保持原payload/key', async () => {
  const policy = policyFixture(); let networkLost = true;
  const requests = installHttpFixture((method, path, body) => {
    if (path === '/api/v1/policies') return { simulation: true, items: [policy] };
    if (path === '/api/v1/policy-proposals') return { simulation: true, items: [] };
    if (path.endsWith('/change-preview')) return { ...previewFixture(), configuration: (body as { configuration: object }).configuration };
    if (method === 'PATCH') { if (networkLost) { networkLost = false; throw new Error('unit response lost'); } policy.effective_status = 'REVOKED'; policy.status = 'REVOKED'; return lifecycleFixture(); }
    throw new Error(`unexpected unit request ${method} ${path}`);
  });
  openPage(); const card = await screen.findByRole('article', { name: '策略 单元应急金' });
  fireEvent.click(within(card).getByRole('button', { name: '修改策略' }));
  const editor = screen.getByRole('region', { name: '修改策略' });
  fireEvent.change(within(editor).getByLabelText('应急金金额（元）'), { target: { value: '3000.01' } });
  fireEvent.click(within(editor).getByRole('button', { name: '预览修改影响' }));
  await within(editor).findByRole('region', { name: '修改前后资金边界' });
  const acceptance = within(editor).getByRole('checkbox', { name: /我已复核完整配置与本次/ });
  expect(acceptance).not.toBeChecked(); fireEvent.click(acceptance);
  fireEvent.change(within(editor).getByLabelText('应急金金额（元）'), { target: { value: '3000.02' } });
  expect(within(editor).queryByRole('region', { name: '修改前后资金边界' })).not.toBeInTheDocument();
  fireEvent.click(within(editor).getByRole('button', { name: '预览修改影响' }));
  await within(editor).findByRole('region', { name: '修改前后资金边界' });
  fireEvent.change(within(editor).getByLabelText('修改原因'), { target: { value: '增加保护' } });
  fireEvent.click(within(editor).getByRole('checkbox', { name: /我已复核完整配置与本次/ }));
  fireEvent.click(within(editor).getByRole('button', { name: '确认修改' }));
  const retry = await within(editor).findByRole('button', { name: '重试原修改请求' });
  await waitFor(() => expect(retry).toBeEnabled()); fireEvent.click(retry);
  await screen.findByText('已撤销');
  const commands = requests.filter((r) => r.method === 'PATCH'); expect(commands).toHaveLength(2); expect(commands[0]!.body).toEqual(commands[1]!.body);
  expect(commands[0]!.body).toMatchObject({ configuration: { amount_cents: 300002 }, reviewed_hash: hash, reason: '增加保护' });
  expect(screen.queryByRole('region', { name: '修改策略' })).not.toBeInTheDocument();
});

test('409保留草稿、废弃旧比较，当前到期禁改且无恢复入口', async () => {
  const policy = policyFixture();
  installHttpFixture((method, path) => path === '/api/v1/policies' ? { simulation: true, items: [policy] }
    : path === '/api/v1/policy-proposals' ? { simulation: true, items: [] }
      : path.endsWith('/change-preview') ? failure() : method === 'GET' ? { simulation: true, items: [] } : failure());
  openPage(); fireEvent.click(within(await screen.findByRole('article', { name: '策略 单元应急金' })).getByRole('button', { name: '修改策略' }));
  const input = screen.getByLabelText('应急金金额（元）'); fireEvent.change(input, { target: { value: '3333.33' } });
  fireEvent.click(screen.getByRole('button', { name: '预览修改影响' }));
  await screen.findByText(/草稿保留，旧预览已废弃/); expect(input).toHaveValue('3333.33');
  expect(screen.queryByRole('region', { name: '修改前后资金边界' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '预览修改影响' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '关闭编辑' }));
  policy.effective_status = 'EXPIRED'; policy.status = 'EXPIRED'; fireEvent.click(screen.getByRole('button', { name: '刷新策略' }));
  const expired = await screen.findByText('已到期'); const card = expired.closest('article')!;
  expect(within(card).getByRole('button', { name: '修改策略' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: /恢复/ })).not.toBeInTheDocument();
});

test('rules原锚点与完整复核，编辑后的候选必须先保存再确认', async () => {
  let compilation = compilationFixture();
  const requests = installHttpFixture((method, path, body) => {
    if (path === '/api/v1/policies') return { simulation: true, items: [] };
    if (path === '/api/v1/policy-proposals') return { simulation: true, items: [] };
    if (path === '/api/v1/policies/compile') return compilation;
    if (path.endsWith('/revise')) { compilation = { ...compilation, configuration: (body as { configuration: Record<string, unknown> }).configuration, configuration_hash: 'b'.repeat(64) }; return compilation; }
    if (path.endsWith('/confirm')) return lifecycleFixture();
    throw new Error(`unexpected ${method} ${path}`);
  });
  openPage(); fireEvent.change(screen.getByLabelText('策略描述'), { target: { value: '应急金保留2000元' } }); fireEvent.click(screen.getByRole('button', { name: '编译候选' }));
  await screen.findByText(/原编译日期锚点 2026-10-04/); expect(screen.getByText(/单元测试原始假设/)).toBeVisible();
  expect(screen.getByRole('button', { name: '确认编译策略' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('应急金金额（元）'), { target: { value: '2500.05' } });
  expect(screen.getByRole('checkbox', { name: /我已逐项复核完整配置与原编译/ })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '保存修订候选' }));
  await waitFor(() => expect(screen.getByRole('checkbox', { name: /我已逐项复核完整配置与原编译/ })).toBeEnabled());
  fireEvent.click(screen.getByRole('checkbox', { name: /我已逐项复核完整配置与原编译/ })); fireEvent.click(screen.getByRole('button', { name: '确认编译策略' }));
  await screen.findByText(/服务端已返回此命令/);
  expect(requests.find((r) => r.path.endsWith('/confirm'))!.body).toEqual({ accepted: true, reviewed_hash: 'b'.repeat(64) });
});

test('原编译reader可恢复，暂停明确接受后仅传版本并保留原在途说明', async () => {
  const policy = policyFixture(); const proposal = proposalFixture();
  const requests = installHttpFixture((method, path) => {
    if (path === '/api/v1/policies') return { simulation: true, items: [policy] };
    if (path === '/api/v1/policy-proposals') return { simulation: true, items: [proposal] };
    if (path.includes('/policy-compilations/')) return compilationFixture();
    if (path.endsWith('/suspend')) { policy.effective_status = 'SUSPENDED'; return { ...lifecycleFixture(), inflight_action_ids: [policyId] }; }
    throw new Error(`unexpected ${method} ${path}`);
  });
  openPage(); fireEvent.click(await screen.findByRole('button', { name: '读取原编译并修订' }));
  await screen.findByText(/原编译日期锚点/);
  const card = screen.getByRole('article', { name: '策略 单元应急金' }); fireEvent.click(within(card).getByRole('button', { name: '暂停' }));
  const review = within(card).getByRole('region', { name: '暂停复核' }); expect(within(review).getByRole('button', { name: '确认暂停' })).toBeDisabled();
  fireEvent.click(within(review).getByRole('checkbox')); fireEvent.click(within(review).getByRole('button', { name: '确认暂停' }));
  await screen.findByText(/在途原项待核对 1 项/);
  expect(requests.find((r) => r.path.endsWith('/suspend'))!.body).toEqual({ expected_version_id: policy.current_version!.id });
});
