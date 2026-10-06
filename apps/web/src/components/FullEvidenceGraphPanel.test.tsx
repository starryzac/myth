import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import FullEvidenceGraphPanel from './FullEvidenceGraphPanel';
import { fullGraphAccount, fullGraphFixture, fullGraphKnown, fullGraphTransaction, fullGraphUser } from '../tests/full-evidence-graph-fixture';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function open() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); return render(<QueryClientProvider client={client}><FullEvidenceGraphPanel initialKind="ACCOUNT" initialIdentity={fullGraphAccount} ownerUserId={fullGraphUser} /></QueryClientProvider>); }
function transport(mode: Parameters<typeof fullGraphFixture>[0] = 'CURRENT') {
  const calls: { url: URL; method: string; body: unknown }[] = []; vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local');
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => { calls.push({ url: new URL(String(input)), method: options?.method ?? 'GET', body: options?.body }); return new Response(JSON.stringify(fullGraphFixture(mode))); })); return calls;
}
const query = () => fireEvent.click(screen.getByRole('button', { name: '只读查询完整证据图' }));
test('只在用户查询后GET；35分母/原状态可读，双向导航不产生金融写入', async () => {
  const calls = transport(); open(); expect(calls).toHaveLength(0); query(); const result = await screen.findByRole('region', { name: '完整证据图结果' });
  expect(within(result).getByText('引用已解析 · 不代表金融成功')).toBeVisible(); expect(within(result).getByText(/35 类来源完整分母/)).toBeVisible(); expect(within(result).getByText('2 / 2')).toBeVisible();
  const details = within(result).getByRole('region', { name: '完整图节点原件与引用' }); fireEvent.click(within(details).getByRole('button', { name: `TRANSACTION:${fullGraphTransaction}` })); expect(within(details).getByRole('heading', { name: `TRANSACTION · ${fullGraphTransaction}` })).toBeVisible();
  expect(within(details).getByText(/指向/)).toBeVisible(); fireEvent.click(within(details).getByRole('button', { name: `ACCOUNT:${fullGraphAccount}` })); expect(within(details).getByRole('heading', { name: `ACCOUNT · ${fullGraphAccount}` })).toBeVisible();
  expect(calls).toHaveLength(1); expect(calls.every((call) => call.method === 'GET' && call.body === undefined)).toBe(true); expect(screen.queryByRole('button', { name: /确认|执行|修账/ })).not.toBeInTheDocument();
});
test('历史null保留具体缺口与知识时点，不显示当前余额填历史', async () => {
  const calls = transport('HISTORICAL'); open(); fireEvent.change(screen.getByLabelText('完整图知识时点（可选，需含时区）'), { target: { value: fullGraphKnown } }); query(); await screen.findByText('UNKNOWN · 证据图未完整证明');
  expect(screen.getByText(/历史原件内容不可用/)).toBeVisible(); expect(within(screen.getByRole('note')).getAllByText(/MUTABLE_HISTORICAL_VALUE_NOT_RECONSTRUCTED/)).toHaveLength(2); expect(calls[0]!.url.searchParams.get('known_at')).toBe(fullGraphKnown); expect(screen.queryByText('100007')).not.toBeInTheDocument();
});
test.each(['PARTIAL', 'LIMITED', 'BANK_UNKNOWN'] as const)('%s显示原不足和未缩减的分母，不能给完成标签', async (mode) => {
  transport(mode); open(); query(); await screen.findByText('UNKNOWN · 证据图未完整证明'); expect(screen.queryByText('引用已解析 · 不代表金融成功')).not.toBeInTheDocument();
  if (mode === 'PARTIAL') { fireEvent.click(screen.getByText(/35 类来源完整分母/)); expect(screen.getAllByText('100001')).toHaveLength(2); expect(screen.getByText('100000')).toBeVisible(); }
  if (mode === 'LIMITED') { expect(screen.getByText('2049 / 2')).toBeVisible(); expect(screen.getByText('20001 / 1')).toBeVisible(); }
  if (mode === 'BANK_UNKNOWN') expect(within(screen.getByRole('note')).getByText(/INDEPENDENT_INTEGRITY_NOT_FULLY_VERIFIED/)).toBeVisible();
});
test('根字段变化立即清除旧结果；再次相同根只接受新GET，不沿用缓存成功', async () => {
  const calls = transport(); open(); query(); await screen.findByText('引用已解析 · 不代表金融成功');
  const field = screen.getByLabelText('完整图根 UUID'); fireEvent.change(field, { target: { value: fullGraphTransaction } }); expect(screen.queryByRole('region', { name: '完整证据图结果' })).not.toBeInTheDocument(); fireEvent.change(field, { target: { value: fullGraphAccount } }); query(); await screen.findByText('引用已解析 · 不代表金融成功'); expect(calls).toHaveLength(2);
});
test('无效输入不联网，服务端404保原request_id且不自动重试', async () => {
  const calls = transport(); open(); fireEvent.change(screen.getByLabelText('完整图根 UUID'), { target: { value: '../wrong' } }); query(); expect(await screen.findByRole('alert')).toHaveTextContent('没有发出请求'); expect(calls).toHaveLength(0);
  const fetch = vi.fn(async () => new Response(JSON.stringify({ error: { code: 'GRAPH_ROOT_NOT_FOUND', message: '原件不存在', request_id: 'SYNTHETIC-404-REQUEST' } }), { status: 404 })); vi.stubGlobal('fetch', fetch); fireEvent.change(screen.getByLabelText('完整图根 UUID'), { target: { value: fullGraphAccount } }); query(); expect(await screen.findByRole('alert')).toHaveTextContent('SYNTHETIC-404-REQUEST'); await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1)); expect(screen.queryByRole('region', { name: '完整证据图结果' })).not.toBeInTheDocument();
});
test('另一owner响应不能显示来源；原HTTP大整数只按原字符呈现', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); const raw = JSON.stringify(fullGraphFixture()).replace('"balance_cents":100007', '"balance_cents":9223372036854775807'); vi.stubGlobal('fetch', vi.fn(async () => new Response(raw))); open(); query(); await screen.findByRole('region', { name: '完整证据图结果' }); expect(screen.getByText(raw)).toBeInTheDocument(); expect(screen.queryByText(/92,233,720,368,547,758/)).not.toBeInTheDocument();
  cleanup(); const other = fullGraphFixture(); other.user_id = fullGraphTransaction; vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(other)))); open(); query(); expect(await screen.findByRole('alert')).toHaveTextContent('校验'); expect(screen.queryByRole('region', { name: '完整证据图结果' })).not.toBeInTheDocument();
});
