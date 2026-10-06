import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import PolicyMap from './PolicyMap';
import type { Policy } from '../api/policies';
import { installHttpFixture, policyFixture } from '../tests/policy-fixture';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function openMap(policies: Policy[], onEdit = vi.fn(), client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })) {
  render(<QueryClientProvider client={client}><PolicyMap policies={policies} onEdit={onEdit} /></QueryClientProvider>); return onEdit;
}
test('地图读原版本、选关系显示字段；历史选择仍只进入当前版本真实修改入口', async () => {
  const policy = policyFixture('goal_saving'); policy.current_version!.configuration.asset_policy_id = 'missing-asset';
  const old = { ...policy.current_version!, id: '10000000-0000-0000-0000-000000000044', version_number: 2,
    change_reason: '工具夹具历史版本', configuration: { ...policy.current_version!.configuration, asset_policy_id: 'historical-missing' } };
  const requests = installHttpFixture(() => ({ simulation: true, items: [old, policy.current_version] })); const edit = openMap([policy]);
  expect(requests).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: /旅行目标.*目标储蓄/ }));
  expect(screen.getByRole('region', { name: '地图所选策略' })).toHaveFocus();
  const versions = await screen.findByLabelText('查看配置版本');
  fireEvent.change(versions, { target: { value: old.id } }); await screen.findByText(/正在查看历史配置/);
  const relations = screen.getByRole('list', { name: '所选策略的有向关系' });
  expect(within(relations).getByText(/historical-missing/)).toBeVisible(); expect(within(relations).queryByText(/missing-asset/)).not.toBeInTheDocument();
  fireEvent.click(within(relations).getByRole('button'));
  expect(screen.getByText('asset_policy_id')).toBeVisible(); expect(screen.getByText(old.id)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '修改当前策略并预览影响' })); expect(edit).toHaveBeenCalledExactlyOnceWith(policy);
  expect(requests).toHaveLength(1); expect(requests[0]).toMatchObject({ method: 'GET', path: `/api/v1/policies/${policy.id}/versions` });
});
test('版本身份拒绝、重读入口和缺版本不会补成功时间线', async () => {
  const policy = policyFixture(); let foreign = true;
  installHttpFixture(() => ({ simulation: true, items: foreign ? [{ ...policy.current_version, policy_id: 'foreign' }] : [] }));
  openMap([policy, { ...policyFixture(), id: 'missing-policy', name: '工具缺版本', current_version: null, version_authorized: false }]);
  fireEvent.click(screen.getByRole('button', { name: /单元应急金.*应急金/ })); await screen.findByRole('alert');
  expect(screen.queryByLabelText('查看配置版本')).not.toBeInTheDocument();
  foreign = false; fireEvent.click(screen.getByRole('button', { name: '重读所选版本' })); await screen.findByText(/时间线不完整/);
  expect(screen.getByRole('list', { name: '原版本时间线' }).children).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: /工具缺版本/ }));
  await waitFor(() => expect(screen.getByRole('button', { name: '修改当前策略并预览影响' })).toBeDisabled());
  expect(screen.getByText(/所选版本原件不可用/)).toBeVisible();
});
test('状态/名称和确认时点筛选有标签，暂停及到期状态以服务端为准', async () => {
  const policy = { ...policyFixture(), effective_status: 'SUSPENDED', version_authorized: false };
  installHttpFixture(() => ({ simulation: true, items: [{ ...policy.current_version, confirmed_at: null }] })); openMap([policy]);
  fireEvent.change(screen.getByLabelText('策略状态'), { target: { value: 'ACTIVE' } }); expect(screen.getByRole('list', { name: '地图策略节点' }).children).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('策略状态'), { target: { value: 'SUSPENDED' } });
  fireEvent.click(screen.getByRole('button', { name: /单元应急金.*已暂停/ })); await screen.findByLabelText('查看配置版本');
  fireEvent.click(screen.getByRole('checkbox', { name: '时间线仅显示有确认时点的原件' })); expect(screen.getByRole('list', { name: '原版本时间线' }).children).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('查找策略'), { target: { value: '不存在' } }); expect(screen.getByText(/没有匹配的策略/)).toBeVisible();
  expect(screen.getByRole('button', { name: '修改当前策略并预览影响' })).toBeEnabled();
});

test('普通历史缓存不能绕过地图reader的策略身份校验', async () => {
  const policy = policyFixture(); const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  client.setQueryData(['policy-versions', policy.id], { simulation: true, items: [{ ...policy.current_version, policy_id: 'foreign-cached-policy', change_reason: '不能显示的其他策略缓存' }] });
  installHttpFixture(() => ({ simulation: true, items: [policy.current_version] })); openMap([policy], vi.fn(), client);
  fireEvent.click(screen.getByRole('button', { name: /单元应急金.*应急金/ }));
  expect(screen.queryByText(/不能显示的其他策略缓存/)).not.toBeInTheDocument(); await screen.findByLabelText('查看配置版本');
  expect(screen.queryByText(/不能显示的其他策略缓存/)).not.toBeInTheDocument();
});
