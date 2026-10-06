/** Synthetic host sources; these cases do not establish actual financial or browser evidence. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import FullRecoveryExecutionHost from './FullRecoveryExecutionHost';
import { fullPolicyFixture, fullUserId } from '../tests/full-policy-fixture';
import { stateFixture } from '../tests/demo-fixture';
import { getAccounts } from '../api/goals';
import { getDemoState } from '../api/demo';

vi.mock('../api/goals', () => ({ getAccounts: vi.fn() }));
vi.mock('../api/demo', () => ({ getDemoState: vi.fn() }));
vi.mock('./FullRecoveryExecutionPanel', () => ({ default: (props: { userId: string; epochId: string; mutationBlocked: boolean }) => <section aria-label="合成恢复子面板" data-user={props.userId} data-epoch={props.epochId} data-blocked={String(props.mutationBlocked)} /> }));
vi.mock('./FullRecoveryNextPanel', () => ({ default: (props: { userId: string; epochId: string; mutationBlocked: boolean }) => <section aria-label="合成下一整仓选择子面板" data-user={props.userId} data-epoch={props.epochId} data-blocked={String(props.mutationBlocked)} /> }));
vi.mock('./FullMaturityExecutionPanel', () => ({ default: (props: { userId: string; epochId: string; mutationBlocked: boolean }) => <section aria-label="合成到期确认子面板" data-user={props.userId} data-epoch={props.epochId} data-blocked={String(props.mutationBlocked)} /> }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const owner = () => ({ simulation: true as const, user_id: fullUserId, timezone: 'Asia/Shanghai', oldest_account_observed_at: null, latest_account_observed_at: null, accounts: [], credit_card_bills: [], cash_balance_cents: 0, position_principal_cents: 0, unknown_position_principal_cents: 0, credit_card_unpaid_cents: 0 });
function open(blocked = false, maturityBlocked?: boolean) { render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><FullRecoveryExecutionHost policy={fullPolicyFixture()} mutationBlocked={blocked} maturityMutationBlocked={maturityBlocked} /></QueryClientProvider>); }

test('未取得开放周期时不伪造UUID或挂新的恢复执行表单', async () => {
  vi.mocked(getAccounts).mockResolvedValue(owner());
  vi.mocked(getDemoState).mockResolvedValue({ ...stateFixture(), available: false, epoch_id: null });
  open(); await screen.findByText(/当前开放周期未取得/);
  expect(screen.queryByRole('region', { name: '合成恢复子面板' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '合成下一整仓选择子面板' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '合成到期确认子面板' })).not.toBeInTheDocument();
});

test('跨族阻挡传递到当前真实owner和开放epoch所绑定的恢复表单', async () => {
  const current = stateFixture();
  vi.mocked(getAccounts).mockResolvedValue(owner());
  vi.mocked(getDemoState).mockResolvedValue(current);
  open(true); const panel = await screen.findByRole('region', { name: '合成恢复子面板' });
  expect(panel).toHaveAttribute('data-user', fullUserId);
  expect(panel).toHaveAttribute('data-epoch', current.epoch_id);
  expect(panel).toHaveAttribute('data-blocked', 'true');
  const next = screen.getByRole('region', { name: '合成下一整仓选择子面板' });
  expect(next).toHaveAttribute('data-user', fullUserId);
  expect(next).toHaveAttribute('data-epoch', current.epoch_id);
  expect(next).toHaveAttribute('data-blocked', 'true');
  const maturity = screen.getByRole('region', { name: '合成到期确认子面板' });
  expect(maturity).toHaveAttribute('data-user', fullUserId);
  expect(maturity).toHaveAttribute('data-epoch', current.epoch_id);
  expect(maturity).toHaveAttribute('data-blocked', 'true');
});

test('本族未决请求不会自锁到期确认，其它恢复流程继续阻挡', async () => {
  vi.mocked(getAccounts).mockResolvedValue(owner());
  vi.mocked(getDemoState).mockResolvedValue(stateFixture());
  open(true, false);
  expect(await screen.findByRole('region', { name: '合成到期确认子面板' })).toHaveAttribute('data-blocked', 'false');
  expect(screen.getByRole('region', { name: '合成恢复子面板' })).toHaveAttribute('data-blocked', 'true');
  expect(screen.getByRole('region', { name: '合成下一整仓选择子面板' })).toHaveAttribute('data-blocked', 'true');
});
