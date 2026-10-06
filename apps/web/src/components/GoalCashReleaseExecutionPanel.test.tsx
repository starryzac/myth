import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import GoalCashReleaseExecutionPanel from './GoalCashReleaseExecutionPanel';
import { cashAccountsFixture, cashActionFixture, cashBinding, cashCandidateFixture, cashDestination, cashIntentFixture } from '../tests/goal-cash-release-fixture';
import { cashLookupFixture } from '../tests/goal-cash-release-fixture';
import { createCashIntent, readCashIntent } from '../api/goal-cash-releases';
import { acceptCashRead, endCashAttempt, getGoalCashReleaseOperation } from '../features/goal-cash-release-operation';
beforeEach(() => { sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); });
afterEach(async () => { cleanup(); endCashAttempt(); const pending = getGoalCashReleaseOperation().pending; if (pending) { vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(await cashActionFixture(pending.intent, 'SUCCEEDED'))))); await acceptCashRead(pending.intent, await readCashIntent(pending.intent, pending.original_action)); } sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
test('手动预览→prepare→明确effect确认；响应丢失原GET→同银行恢复，所有POST留门直到独立完整GET', async () => {
  const original = await cashIntentFixture(), auth = original.authorization; const calls: { path: string; method: string; body: unknown }[] = []; let executions = 0; let final = false;
  vi.stubGlobal('fetch', vi.fn(async (input: string, init: RequestInit) => { const path = new URL(input, 'http://synthetic.local').pathname; const body = init.body ? JSON.parse(String(init.body)) : undefined; calls.push({ path, method: init.method!, body });
    if (path.endsWith('/accounts/summary')) return new Response(JSON.stringify(cashAccountsFixture()));
    if (path.endsWith('/preview')) { const intent = await createCashIntent(cashBinding, auth, body.destination_account_id, body.idempotency_key); return new Response(JSON.stringify(await cashCandidateFixture(intent))); }
    const pending = getGoalCashReleaseOperation().pending!; if (path.endsWith('/prepare')) return new Response(JSON.stringify(await cashActionFixture(pending.intent)));
    if (path.endsWith('/execute')) { executions++; if (executions === 1) throw new Error('SYNTHETIC_RESPONSE_LOSS_NOT_REAL_BANK'); final = true; return new Response(JSON.stringify(await cashActionFixture(pending.intent, 'SUCCEEDED'))); }
    return new Response(JSON.stringify(path.includes('/by-key/') ? cashLookupFixture(pending.intent, null) : await cashActionFixture(pending.intent, final ? 'SUCCEEDED' : 'UNKNOWN')));
  }));
  const query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); const view = render(<QueryClientProvider client={query}><GoalCashReleaseExecutionPanel {...cashBinding} authorization={auth} /></QueryClientProvider>);
  expect(calls.every((c) => c.method === 'GET')).toBe(true); fireEvent.click(screen.getByText('读取已提供的原授权范围')); await screen.findByText(/服务读取报告 CURRENT/);
  fireEvent.change(screen.getByLabelText('保护现金目的账户'), { target: { value: cashDestination } }); fireEvent.click(screen.getByText('手动只读预览最低修复')); await screen.findByRole('region', { name: '回拨实际当前预览' });
  expect(calls.filter((c) => c.path.endsWith('/prepare'))).toHaveLength(0); fireEvent.click(screen.getByText('手动准备原回拨行动')); await screen.findByText(/收到原prepare/);
  expect(screen.getByText('明确确认原效果并执行')).toBeDisabled(); fireEvent.click(screen.getByLabelText(/我已复核原金额/)); fireEvent.click(screen.getByText('明确确认原效果并执行')); await screen.findByRole('alert');
  const saved = getGoalCashReleaseOperation().pending!; expect(saved.execute_body!.accepted).toBe(true); expect(saved.execute_body!.reviewed_effect_hash).toBe(saved.original_action!.original_command.effect_hash);
  fireEvent.click(screen.getByText('只读核对原回拨行动与回执')); await screen.findByText(/原PLANNED\/SUBMITTED\/UNKNOWN仍保留/); expect(getGoalCashReleaseOperation().pending).not.toBeNull();
  view.rerender(<QueryClientProvider client={query}><GoalCashReleaseExecutionPanel {...cashBinding} mutationBlocked authorization={auth} /></QueryClientProvider>);
  fireEvent.click(screen.getByLabelText(/我已复核原金额/)); fireEvent.click(screen.getByText('明确按原银行键恢复应用投影')); await screen.findByText(/收到原execute响应/); expect(getGoalCashReleaseOperation().pending).not.toBeNull();
  fireEvent.click(screen.getByText('只读核对原回拨行动与回执')); await screen.findByText(/解除本族待核对门/); expect(getGoalCashReleaseOperation().pending).toBeNull();
  const writes = calls.filter((c) => c.path.endsWith('/execute')); expect(writes).toHaveLength(2); expect(writes[0]!.body).toEqual(writes[1]!.body); expect(calls.filter((c) => c.path.endsWith('/prepare'))).toHaveLength(1); expect(calls.every((c) => c.method !== 'POST' || !Object.keys(c.body as object).some((k) => ['amount_cents', 'now', 'bank_facts'].includes(k)))).toBe(true);
});
