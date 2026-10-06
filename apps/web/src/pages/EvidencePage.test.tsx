import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import EvidencePage from './EvidencePage';
import { evidenceId, factsFixture, graphFixture, priorEvidenceId } from '../tests/evidence-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function fixtureTransport(handler?: (url: URL) => Response) {
  const requests: URL[] = []; vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local');
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    expect(options?.method).toBe('GET'); expect(options?.body).toBeUndefined(); const url = new URL(String(input)); requests.push(url);
    return handler?.(url) ?? new Response(JSON.stringify(url.pathname.endsWith('/facts') ? factsFixture() : graphFixture()));
  })); return requests;
}
function openPage(props: { kind?: string; identity?: string } = {}) { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); return render(<QueryClientProvider client={client}><EvidencePage {...props} /></QueryClientProvider>); }
test('事实双时态与原SUPERSEDES关系可追溯，沿用查询known时点且不称银行验真', async () => {
  const requests = fixtureTransport(); openPage(); const facts = await screen.findByRole('region', { name: '事实查询结果' });
  expect(within(facts).getByText(/有效时.*知悉时/)).toBeVisible(); fireEvent.click(within(facts).getByRole('button', { name: '追溯此证据关系' }));
  expect(screen.getByRole('region', { name: '原件关系查询' })).toHaveFocus();
  const graph = await screen.findByRole('region', { name: '证据关系结果' }); expect(within(graph).getByText(/不能替代原银行核验与审计链验证/)).toBeVisible();
  expect(within(screen.getByRole('list', { name: '实际证据边' })).getByText(/SUPERSEDES/)).toBeVisible(); expect(within(graph).getByText('此视图未验证外部银行锚点')).toBeVisible();
  const actual = requests.find((url) => url.pathname.includes('/graph/')); expect(actual!.searchParams.get('known_at')).toBe('2026-10-05T13:00:00Z');
  fireEvent.click(within(graph).getByRole('button', { name: '查看关系目标' })); expect(within(graph).getByRole('button', { pressed: true })).toHaveTextContent(priorEvidenceId);
});
test('未来known由服务端拒绝，原错误/request_id展示，旧事实不作为新查询成功', async () => {
  const requests = fixtureTransport((url) => url.searchParams.get('known_at') === '2099-01-01T00:00:00Z'
    ? new Response(JSON.stringify({ error: { code: 'FUTURE_KNOWLEDGE', message: '不能查询尚未观察到的系统知识', request_id: 'UNIT-FUTURE-READ' } }), { status: 422 })
    : new Response(JSON.stringify(factsFixture())));
  openPage(); await screen.findByRole('region', { name: '事实查询结果' });
  fireEvent.change(screen.getByLabelText('系统知悉时（含时区；空白用服务端当前时点）'), { target: { value: '2099-01-01T00:00:00Z' } }); fireEvent.click(screen.getByRole('button', { name: '查询事实' }));
  await screen.findByRole('alert'); expect(screen.getByRole('alert')).toHaveTextContent('UNIT-FUTURE-READ'); expect(screen.queryByRole('region', { name: '事实查询结果' })).not.toBeInTheDocument();
  expect(requests.at(-1)!.searchParams.get('known_at')).toBe('2099-01-01T00:00:00Z');
});
test('深链根错误不会发图请求；实际断链显示UNKNOWN且不补节点', async () => {
  const graph = graphFixture(); graph.nodes.pop(); graph.state = 'UNKNOWN'; graph.issues = [{ code: 'BROKEN_REFERENCE', kind: 'EVIDENCE', id: priorEvidenceId }];
  const requests = fixtureTransport((url) => new Response(JSON.stringify(url.pathname.endsWith('/facts') ? factsFixture() : graph)));
  openPage({ kind: 'BANK', identity: evidenceId }); await screen.findByRole('alert'); expect(requests.some((url) => url.pathname.includes('/graph/'))).toBe(false);
  fireEvent.change(screen.getByLabelText('根原件UUID'), { target: { value: evidenceId } }); fireEvent.click(screen.getByRole('button', { name: '查询原件关系' }));
  const result = await screen.findByRole('region', { name: '证据关系结果' }); expect(within(result).getByText('BROKEN_REFERENCE', { selector: 'strong' })).toBeVisible(); expect(within(result).getByText(/目标原件未提供/)).toBeVisible();
  expect(within(screen.getByRole('list', { name: '实际证据节点' })).getAllByRole('button')).toHaveLength(1);
});
test('冲突、旧SUPERSEDED与不可精确数字不改成成功金额，原响应保持原整数文本', async () => {
  const source = factsFixture(); source.state = 'UNKNOWN'; source.groups[0]!.state = 'UNKNOWN'; source.groups[0]!.originals[0]!.status = 'SUPERSEDED'; source.issues = [{ code: 'STATUS_HISTORY_NOT_PROVEN' }];
  const original = JSON.stringify(source).replace('"amount_cents":1', '"amount_cents":9223372036854775807'); fixtureTransport(() => new Response(original)); openPage();
  const result = await screen.findByRole('region', { name: '事实查询结果' }); expect(within(result).getByText(/原件标记已被替代/)).toBeVisible(); expect(within(result).getByText(/不能精确展示的数字/)).toBeVisible();
  expect(within(result).queryByText(/¥/)).not.toBeInTheDocument(); expect(within(result).getByText(original)).toHaveTextContent('9223372036854775807');
});
test('只读事实与图可分别重读，节点类型筛选有原生标签', async () => {
  const requests = fixtureTransport(); openPage({ kind: 'EVIDENCE', identity: evidenceId }); const graph = await screen.findByRole('region', { name: '证据关系结果' });
  fireEvent.change(within(graph).getByLabelText('查看节点种类'), { target: { value: 'RECEIPT' } }); expect(screen.getByRole('list', { name: '实际证据节点' }).children).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '重读原关系' })); await waitFor(() => expect(requests.filter((url) => url.pathname.includes('/graph/'))).toHaveLength(2));
});
