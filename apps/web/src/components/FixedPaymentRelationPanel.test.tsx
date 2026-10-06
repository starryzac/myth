import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { paymentActionFixture, paymentConsentFixture, paymentFixture, paymentLookupFixture, paymentPreparedFixture, paymentReceiptFixture } from '../tests/fixed-payment-fixture';
import type { PaymentRead } from '../api/full-payment-relations';
let Panel: typeof import('./FixedPaymentRelationPanel')['default'], operation: typeof import('../features/fixed-payment-operation');
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); vi.stubEnv('VITE_API_BASE_URL', 'http://fixed-payment-component.local'); Panel = (await import('./FixedPaymentRelationPanel')).default; operation = await import('../features/fixed-payment-operation'); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); sessionStorage.clear(); });
async function transport(auto = false, lose = false, notFound = false) {
  const f = await paymentFixture(auto), calls: { path: string; method: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(String(input)).pathname, method = options?.method ?? 'GET', body = options?.body ? JSON.parse(String(options.body)) : undefined; calls.push({ path, method, body });
    if (path.endsWith('/local-actor/session')) return new Response(JSON.stringify(f.identity));
    if (path.endsWith('/policies')) return new Response(JSON.stringify({ simulation: true, items: [f.original] }));
    if (path.endsWith('/full-payment-relations/preview')) return new Response(JSON.stringify(f.preview));
    const intent = operation.getFixedPaymentOperation().pending;
    let value: PaymentRead | Awaited<ReturnType<typeof paymentReceiptFixture>>;
    if (method === 'POST') {
      if (!intent) throw new Error('UNIT_MISSING_PERSISTED_INTENT');
      if (lose) throw new Error('UNIT_RESPONSE_LOSS');
      value = intent.kind === 'START' || intent.kind === 'CONFIRM' ? await paymentReceiptFixture(intent) : await paymentActionFixture(f.scope, intent.kind === 'EXECUTE' ? 'SUCCEEDED' : 'PLANNED');
    } else if (intent) {
      value = intent.kind === 'START' || intent.kind === 'CONFIRM' ? await paymentLookupFixture(intent, !notFound) : intent.kind === 'PREPARE' ? await paymentPreparedFixture(intent, !notFound) : intent.kind === 'ACTION_CONFIRM' ? await paymentConsentFixture(intent, !notFound) : await paymentActionFixture(f.scope, 'SUCCEEDED');
      if (intent.kind === 'PREPARE' && 'original_binding' in value && value.original_binding) value.original_binding.authorization_evidence_hash = operation.getFixedPaymentOperation().workflow.authorization!.evidence_hash;
    } else value = await paymentActionFixture(f.scope);
    return new Response(JSON.stringify(value));
  })); return { f, calls };
}
function open(f: Awaited<ReturnType<typeof paymentFixture>>, blocked = false) { const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); return render(<QueryClientProvider client={client}><Panel fullPolicy={f.full} userId={f.scope.user_id} epochId={f.scope.epoch_id} mutationBlocked={blocked} /></QueryClientProvider>); }
async function previewAndLogin() { await screen.findByRole('option', { name: /合成原周期关系/ }); fireEvent.change(screen.getByLabelText('选择实际已授权的原MVP周期策略'), { target: { value: (await paymentFixture()).original.id } }); fireEvent.click(screen.getByText('只读复核固定关系范围')); await screen.findByRole('region', { name: '固定关系发起复核' }); fireEvent.click(screen.getByText('只读检查本地USER会话')); await screen.findByText(/本地签名 USER/); }
async function lookup() { await waitFor(() => expect(screen.getByText('只读核对原固定付款请求')).toBeEnabled()); fireEvent.click(screen.getByText('只读核对原固定付款请求')); await waitFor(() => expect(operation.getFixedPaymentOperation().pending).toBeNull()); }
async function relation() { await previewAndLogin(); fireEvent.click(screen.getByLabelText('由我发起与此已验证银行收款身份的新固定关系')); fireEvent.click(screen.getByText('用户明确发起固定关系')); await lookup(); fireEvent.change(screen.getByLabelText('关系确认理由'), { target: { value: '合成明确复核理由' } }); fireEvent.click(screen.getByLabelText(/我已复核原身份、账户、额度/)); fireEvent.click(screen.getByText('明确确认原固定关系')); await lookup(); }
test('真实只读reader展示范围与原来源；无USER不可写，不自动发起/确认/execute', async () => { const { f, calls } = await transport(); open(f); await screen.findByRole('option', { name: /合成原周期关系/ }); fireEvent.change(screen.getByLabelText('选择实际已授权的原MVP周期策略'), { target: { value: f.original.id } }); fireEvent.click(screen.getByText('只读复核固定关系范围')); await screen.findByRole('region', { name: '固定关系发起复核' }); expect(screen.getByText('用户明确发起固定关系')).toBeDisabled(); expect(calls.filter((v) => v.method === 'POST').map((v) => v.path)).toEqual(['/api/v1/full-payment-relations/preview']); expect(screen.getByText(/银行已经|未知或歧义收款人/)).toBeVisible(); });
test('用户手动START与关系双复核，POST成功仍待核，原GET后才能继续', async () => { const { f, calls } = await transport(); open(f); await relation(); expect(operation.getFixedPaymentOperation().workflow.authorization!.original.kind).toBe('CONFIRM'); const writes = calls.filter((c) => c.method === 'POST'); expect(writes.map((c) => c.path.split('/').at(-1))).toEqual(['preview', 'start', 'confirm']); expect(JSON.stringify(writes.map((c) => c.body))).not.toMatch(/role|actor|amount_cents|clock|token/); expect(screen.getByText('准备原周期付款')).toBeDisabled(); });
test('丢START响应/未找到/外部门仍可原键GET，不自动重试或清空换key', async () => { const { f, calls } = await transport(false, true, true); open(f, false); await previewAndLogin(); fireEvent.click(screen.getByLabelText('由我发起与此已验证银行收款身份的新固定关系')); fireEvent.click(screen.getByText('用户明确发起固定关系')); await screen.findByRole('alert'); const original = operation.getFixedPaymentOperation().pending; expect(original).not.toBeNull(); await waitFor(() => expect(screen.getByText('只读核对原固定付款请求')).toBeEnabled()); fireEvent.click(screen.getByText('只读核对原固定付款请求')); await screen.findByText(/原键未终局/); expect(operation.getFixedPaymentOperation().pending).toEqual(original); expect(calls.filter((v) => v.path.endsWith('/start'))).toHaveLength(1); });
test.each([false, true])('周期prepare保原键→实际等级auto=%s→ASK仅手动同意→execute与原GETreceipt', async (auto) => { const { f, calls } = await transport(auto); open(f); await relation(); fireEvent.change(screen.getByLabelText('实际付款自然月'), { target: { value: '2026-10' } }); fireEvent.click(screen.getByText('准备原周期付款')); await lookup(); fireEvent.click(screen.getByText('只读刷新原固定付款动作')); await waitFor(() => expect(operation.getFixedPaymentOperation().busy).toBe(false));
    if (!auto) { await waitFor(() => expect(screen.getByLabelText(/我已复核此原付款金额/)).toBeEnabled()); expect(screen.getByText('执行已复核原周期付款')).toBeDisabled(); fireEvent.click(screen.getByLabelText(/我已复核此原付款金额/)); fireEvent.click(screen.getByText('明确同意本次原付款')); await lookup(); fireEvent.click(screen.getByText('只读刷新原固定付款动作')); }
    await waitFor(() => expect(screen.getByText('执行已复核原周期付款')).toBeEnabled()); fireEvent.click(screen.getByText('执行已复核原周期付款')); await lookup(); expect(operation.getFixedPaymentOperation().workflow.action!.receipt?.executed_cents).toBe(10003); expect(operation.getFixedPaymentOperation().workflow.action!.bank_status).toBe('SETTLED'); expect(calls.filter((c) => c.method === 'POST' && /actions\/.*\/confirm$/.test(c.path))).toHaveLength(auto ? 0 : 1); expect(calls.filter((c) => c.method === 'POST' && c.path.endsWith('/execute'))).toHaveLength(1); });
