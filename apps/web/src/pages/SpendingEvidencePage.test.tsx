import { webcrypto } from 'node:crypto';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { categoryResultFixture, categoryReviewFixture, periodicFixture, seasonalFixture, spendingAccount, spendingAccountsFixture, spendingTransaction, spendingTransactionsFixture } from '../tests/spending-evidence-fixture';
let page: typeof import('./SpendingEvidencePage'); let operation: typeof import('../features/spending-evidence-operation'); let queries: typeof import('@tanstack/react-query');
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubEnv('VITE_API_BASE_URL', 'http://spending-page-unit.local'); vi.stubGlobal('crypto', webcrypto); page = await import('./SpendingEvidencePage'); operation = await import('../features/spending-evidence-operation'); queries = await import('@tanstack/react-query'); });
function transport(options: { lost?: boolean; notFound?: boolean; mismatched?: boolean; confirmed?: boolean; stale?: boolean; seasonalError?: boolean } = {}) {
  const calls: { method: string; url: URL; body: unknown }[] = []; let posted = false;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)); const method = init?.method ?? 'GET'; const body = init?.body ? JSON.parse(String(init.body)) as unknown : undefined; calls.push({ method, url, body });
    let result: unknown;
    if (url.pathname.endsWith('/accounts/summary')) result = spendingAccountsFixture();
    else if (url.pathname.endsWith('/transactions')) result = spendingTransactionsFixture(options.confirmed ?? posted);
    else if (url.pathname.endsWith('/periodic')) result = periodicFixture(true);
    else if (url.pathname.endsWith('/seasonal')) {
      if (options.seasonalError) return new Response(JSON.stringify({ error: { code: 'INVALID_WINDOW', message: '原窗口无效', request_id: 'TOOL_ONLY_WINDOW_READ' } }), { status: 422 });
      result = seasonalFixture(url.searchParams.get('window_id')!);
    } else if (url.pathname.includes('/by-key/')) { const actual = categoryResultFixture(operation.getSpendingEvidenceOperation().pending!, !options.notFound); if (options.mismatched && actual.original_receipt) actual.original_receipt.bank_evidence_hash = '0'.repeat(64); result = actual; }
    else if (method === 'POST') {
      const intent = operation.getSpendingEvidenceOperation().pending!; expect(intent).not.toBeNull(); expect(intent.body).toEqual(body); expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe(JSON.stringify(intent)); posted = true;
      if (options.lost) throw new Error('TOOL_ONLY_RESPONSE_LOST'); result = categoryResultFixture(intent);
    } else if (url.pathname.endsWith('/category-review')) { const actual = categoryReviewFixture(options.confirmed ?? posted); if (options.stale && calls.filter((item) => item.url.pathname.endsWith('/category-review')).length > 1) actual.reviewed_transaction_hash = '0'.repeat(64); result = actual; }
    else throw new Error(`TOOL_ONLY_UNEXPECTED_PATH:${url.pathname}`);
    return new Response(JSON.stringify(result));
  })); return calls;
}
function openPage(blocked = false) { const client = new queries.QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); return render(<queries.QueryClientProvider client={client}><page.default mutationBlocked={blocked} /></queries.QueryClientProvider>); }
async function reviewTransaction() { await screen.findByRole('option', { name: /HTTP 夹具现金账户/ }); fireEvent.change(screen.getByLabelText('交易账户'), { target: { value: spendingAccount } }); fireEvent.click(await screen.findByRole('button', { name: `读取交易 ${spendingTransaction}` })); return screen.findByRole('region', { name: '原消费交易类别复核' }); }
function acceptCategory() { fireEvent.change(screen.getByLabelText('用户明确选择类别'), { target: { value: 'food' } }); fireEvent.change(screen.getByLabelText('分类确认原因'), { target: { value: '我复核此笔模拟银行消费为食品' } }); fireEvent.click(screen.getByRole('checkbox')); }
test('真实周期原样本/窗口READY只读，节日明确ID再读官方窗口与UNKNOWN；无POST', async () => {
  const calls = transport(); openPage(); const periodic = await screen.findByRole('heading', { name: 'RENT · READY' }); expect(periodic).toBeVisible(); expect(screen.getByRole('region', { name: '周期规律建议' })).toHaveTextContent('周期 2 / 样本 2'); expect(calls.some((item) => item.url.pathname.endsWith('/seasonal'))).toBe(false);
  fireEvent.change(screen.getByLabelText('用户指定官方窗口 ID'), { target: { value: 'CN-2026-NATIONAL_DAY' } }); fireEvent.click(screen.getByRole('button', { name: '读取指定节日建议' })); const region = screen.getByRole('region', { name: '节日储备建议' }); await within(region).findByRole('heading', { name: 'UNKNOWN' }); expect(region).toHaveTextContent('UNKNOWN（未补零）'); expect(region).toHaveTextContent('TOOL_ONLY_MISSING_COVERAGE'); expect(within(region).getByRole('link', { name: '官方通知原来源' })).toHaveAttribute('href', seasonalFixture().suggestion.target!.source_url); expect(screen.getByLabelText('选择本次服务返回的登记窗口')).toBeVisible(); expect(calls.every((item) => item.method === 'GET' && item.body === undefined)).toBe(true);
});
test('不自动勾选；POST成功仍pending，原键NOT_FOUND保原命令而RECORDED独立核对才清', async () => {
  const options = { notFound: true }; const calls = transport(options); openPage(); await reviewTransaction(); expect(screen.getByRole('checkbox')).not.toBeChecked(); expect(screen.getByRole('button', { name: '提交首次类别确认' })).toBeDisabled(); acceptCategory(); fireEvent.click(screen.getByRole('button', { name: '提交首次类别确认' })); await screen.findByText('确认请求已返回；原请求仍待单独按原键读回核对。'); const original = operation.getSpendingEvidenceOperation().pending!; expect(original.body.accepted).toBe(true); expect(Object.keys(original.body).sort()).toEqual(['accepted', 'category', 'expected_epoch_id', 'idempotency_key', 'reason', 'reviewed_transaction_hash']); expect(calls.filter((item) => item.method === 'POST')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '只读核对原分类请求' })); await screen.findByText('原键未找到不是终局；保留原命令，不允许换键重提。'); expect(operation.getSpendingEvidenceOperation().pending).toEqual(original); expect(screen.getByRole('button', { name: '提交首次类别确认' })).toBeDisabled(); options.notFound = false;
  fireEvent.click(screen.getByRole('button', { name: '只读核对原分类请求' })); await screen.findByText('已按原键核对原命令、交易与银行原件回执。分类回执不授予资金权限。'); expect(operation.getSpendingEvidenceOperation().pending).toBeNull(); expect(calls.filter((item) => item.method === 'POST')).toHaveLength(1);
});
test('响应丢失后刷新只恢复原件；跨族门仍允许自己的只读原键查询', async () => {
  const calls = transport({ lost: true }); const view = openPage(); await reviewTransaction(); acceptCategory(); fireEvent.click(screen.getByRole('button', { name: '提交首次类别确认' })); await screen.findByRole('alert'); await waitFor(() => expect(operation.getSpendingEvidenceOperation().busy).toBe(false)); const saved = structuredClone(operation.getSpendingEvidenceOperation().pending!); view.unmount(); openPage(true);
  expect(screen.getByRole('region', { name: '待核对分类原请求' })).toHaveTextContent(saved.body.idempotency_key); fireEvent.click(screen.getByRole('button', { name: '只读核对原分类请求' })); await screen.findByText('已按原键核对原命令、交易与银行原件回执。分类回执不授予资金权限。'); expect(calls.filter((item) => item.method === 'POST')).toHaveLength(1); const lookup = calls.find((item) => item.url.pathname.includes('/by-key/'))!; expect(lookup.url.pathname).toContain(saved.body.idempotency_key); expect(lookup.url.search).toBe('');
});
test('不同银行原回执不能清，已确认类别与跨族blocked均不产生写', async () => {
  const calls = transport({ confirmed: true }); const view = openPage(); const result = await reviewTransaction(); expect(result).toHaveTextContent('类别更正尚未支持'); expect(screen.queryByRole('checkbox')).not.toBeInTheDocument(); view.unmount(); transport(); openPage(true); await reviewTransaction(); expect(screen.getByLabelText('用户明确选择类别')).toBeDisabled(); expect(calls.every((item) => item.method === 'GET')).toBe(true);
});
test('原交易在发送前变化阻止POST，不拿新review继承原明确确认', async () => {
  const calls = transport({ stale: true }); openPage(); await reviewTransaction(); acceptCategory(); fireEvent.click(screen.getByRole('button', { name: '提交首次类别确认' })); expect(await screen.findByRole('alert')).toHaveTextContent('原交易或模拟期已变化'); expect(operation.getSpendingEvidenceOperation().pending).toBeNull(); expect(calls.every((item) => item.method === 'GET')).toBe(true);
});
test('分类错误原件保pending；建议422保request_id并清旧成功视图', async () => {
  const options = { mismatched: true, seasonalError: false }; transport(options); openPage(); await reviewTransaction(); acceptCategory(); fireEvent.click(screen.getByRole('button', { name: '提交首次类别确认' })); await screen.findByText('确认请求已返回；原请求仍待单独按原键读回核对。'); fireEvent.click(screen.getByRole('button', { name: '只读核对原分类请求' })); await screen.findByRole('alert'); expect(operation.getSpendingEvidenceOperation().pending).not.toBeNull();
  fireEvent.change(screen.getByLabelText('用户指定官方窗口 ID'), { target: { value: 'CN-2026-NATIONAL_DAY' } }); fireEvent.click(screen.getByRole('button', { name: '读取指定节日建议' })); await screen.findByRole('heading', { name: 'UNKNOWN' }); options.seasonalError = true; fireEvent.click(screen.getByRole('button', { name: '读取指定节日建议' })); await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('TOOL_ONLY_WINDOW_READ')); expect(screen.queryByRole('link', { name: '官方通知原来源' })).not.toBeInTheDocument();
});
