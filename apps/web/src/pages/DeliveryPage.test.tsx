import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import DeliveryPage from './DeliveryPage';
import { deliveryFixture, otherOutboxId, outboxId } from '../tests/delivery-fixture';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function transport(handler?: (url: URL, detailReads: number) => Response) {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); const calls: URL[] = []; let detailReads = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    expect(options?.method).toBe('GET'); expect(options?.body).toBeUndefined(); const url = new URL(String(input)); calls.push(url); if (!url.search) detailReads++;
    return handler?.(url, detailReads) ?? new Response(JSON.stringify(url.search ? { simulation: true, economic_verified: false, items: [deliveryFixture()] } : deliveryFixture()));
  })); return calls;
}
function openPage() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); return render(<QueryClientProvider client={client}><DeliveryPage /></QueryClientProvider>); }
async function chooseMessage() { fireEvent.click(await screen.findByRole('button', { name: new RegExp(`查看原消息 ${outboxId}`) })); return screen.getByRole('region', { name: '原消息详情' }); }

test('实际Inbox/UNKNOWN/bankSETTLED与原连续attempt全部显示，缺hash/key不伪造，无写控件', async () => {
  const calls = transport(); openPage(); const detail = await chooseMessage(); expect(detail).toHaveFocus(); await within(detail).findByRole('heading', { name: '原消息当前核对' });
  expect(within(detail).getByText(/银行可能已受理或已结算/)).toBeVisible(); expect(within(detail).getByText('当前读取合同未提供，未生成新键')).toBeVisible();
  expect(within(detail).getByText(/独立资金验真：未验证/)).toBeVisible(); const attempts = within(detail).getByRole('list', { name: '投递尝试列表' }); expect(attempts.children).toHaveLength(1); expect(within(attempts).getByText('SETTLED')).toBeVisible();
  expect(screen.getByText(/最近最多50条原消息/)).toBeVisible(); expect(screen.queryByRole('button', { name: /投递|执行|enqueue/ })).not.toBeInTheDocument(); expect(calls).toHaveLength(2);
});
test('原服务receipt通过仍明确economic未验，历史claim不一致另显示当前核对警示', async () => {
  let source = deliveryFixture('VERIFIED'); transport((url) => new Response(JSON.stringify(url.search ? { simulation: true, economic_verified: false, items: [source] } : source))); openPage(); const detail = await chooseMessage();
  await within(detail).findByText(/原服务本次读取报告：SERVICE_RECEIPT_VERIFIED/); expect(within(detail).getByText(/economic_verified=false/)).toBeVisible();
  source = deliveryFixture(); source.inbox_state = 'SERVICE_RECEIPT_VERIFIED'; source.outbox_state = 'DELIVERED'; fireEvent.click(within(detail).getByRole('button', { name: '只读核对同一原消息' }));
  await within(detail).findByRole('alert'); expect(within(detail).getByRole('alert')).toHaveTextContent('当前未报告核验通过'); expect(within(detail).queryByText(/原服务本次读取报告：SERVICE_RECEIPT_VERIFIED/)).not.toBeInTheDocument();
});
test('读错误保留原消息身份，明确再次只读核对且绝不自动POST或展示旧详情为新成功', async () => {
  const calls = transport((url, count) => url.search ? new Response(JSON.stringify({ simulation: true, economic_verified: false, items: [deliveryFixture()] })) : count === 2 ? new Response(JSON.stringify({ error: { code: 'INVALID_DELIVERY_SOURCE', message: '原消息冲突', request_id: 'UNIT-READ-ERROR' } }), { status: 409 }) : new Response(JSON.stringify(deliveryFixture())));
  openPage(); const detail = await chooseMessage(); await within(detail).findByRole('heading', { name: '原消息当前核对' }); fireEvent.click(within(detail).getByRole('button', { name: '只读核对同一原消息' }));
  const error = await within(detail).findByRole('alert'); expect(error).toHaveTextContent('UNIT-READ-ERROR'); expect(within(detail).queryByRole('heading', { name: '原消息当前核对' })).not.toBeInTheDocument();
  expect(within(detail).getByText(new RegExp(`选定原消息`))).toHaveTextContent(outboxId); expect(calls).toHaveLength(3);
  fireEvent.click(within(detail).getByRole('button', { name: '只读核对同一原消息' })); await within(detail).findByRole('heading', { name: '原消息当前核对' }); expect(calls.filter((url) => !url.search).every((url) => url.pathname.endsWith(outboxId))).toBe(true);
});
test('列表根与详情返回另一原消息拒绝，原响应前后文本保留不覆盖', async () => {
  const first = ` \n${JSON.stringify(deliveryFixture())}\n`; const second = `${JSON.stringify(deliveryFixture())} \n`; let mismatched = false;
  transport((url, count) => new Response(url.search ? JSON.stringify({ simulation: true, economic_verified: false, items: [deliveryFixture()] }) : mismatched ? JSON.stringify({ ...deliveryFixture(), outbox_id: otherOutboxId }) : count === 1 ? first : second));
  openPage(); const detail = await chooseMessage(); await within(detail).findByRole('heading', { name: '原消息当前核对' }); fireEvent.click(within(detail).getByRole('button', { name: '只读核对同一原消息' }));
  await waitFor(() => expect(within(detail).getByText('本页捕获的详情原 JSON 响应 · 2 次')).toBeInTheDocument()); const originals = detail.querySelectorAll('pre'); expect([...originals].map((node) => node.textContent)).toEqual([first, second]);
  mismatched = true; fireEvent.click(within(detail).getByRole('button', { name: '只读核对同一原消息' })); await within(detail).findByRole('alert'); expect(within(detail).getByRole('alert')).toHaveTextContent('原消息身份'); expect(detail.querySelectorAll('pre')).toHaveLength(2);
});
test('归档原消息保留历史不重建动作，空列表不宣称全部历史为空', async () => {
  let empty = false; transport((url) => new Response(JSON.stringify(url.search ? { simulation: true, economic_verified: false, items: empty ? [] : [deliveryFixture('ARCHIVED')] } : deliveryFixture('ARCHIVED')))); openPage(); const detail = await chooseMessage();
  await within(detail).findByText(/原动作已不在当前可用范围/); expect(within(detail).getByRole('list', { name: '投递尝试列表' }).children).toHaveLength(1);
  empty = true; fireEvent.click(screen.getByRole('button', { name: '刷新原消息列表' })); await screen.findByText('当前读取范围没有原消息，不表示全部历史为空。');
});
