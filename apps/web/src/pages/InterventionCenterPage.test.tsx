import { webcrypto } from 'node:crypto';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { interventionCurrentObservationFixture, interventionDeliveryFixture, interventionFixture, interventionId, interventionListFixture, interventionLookupFixture, interventionQuestionFixture, interventionResultFixture, interventionTraceFixture } from '../tests/intervention-fixture';
import { questionSession } from '../tests/question-fixture';
let page: typeof import('./InterventionCenterPage'); let operation: typeof import('../features/intervention-operation');
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubEnv('VITE_API_BASE_URL', 'http://intervention-page-unit.local'); vi.stubGlobal('crypto', webcrypto); page = await import('./InterventionCenterPage'); operation = await import('../features/intervention-operation'); });
function transport(options: { lost?: boolean; notFound?: boolean; mismatched?: boolean; stale?: boolean; freshStale?: boolean; rejected?: boolean; claimed?: boolean } = {}) {
  const calls: { method: string; url: URL; body: unknown }[] = []; let claimed = options.claimed ?? false; let acknowledgment: ReturnType<typeof interventionResultFixture> | null = null;
  const view = () => {
    const value = acknowledgment?.message ?? interventionFixture(claimed);
    if (options.stale || options.freshStale) { value.source_status = 'STALE'; value.current_source_binding = 'UNVERIFIED'; value.effective_state = 'INVALIDATED'; value.pending = false; value.current_question = null; }
    return value;
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)); const method = init?.method ?? 'GET'; const body = init?.body ? JSON.parse(String(init.body)) as unknown : undefined; calls.push({ method, url, body }); let value: unknown;
    if (method === 'POST') {
      const intent = operation.getInterventionOperation().pending!; expect(intent).not.toBeNull(); expect(intent.body).toEqual(body); expect(operation.getInterventionOperation().busy).toBe(true); expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe(JSON.stringify(intent));
      if (options.rejected) return new Response(JSON.stringify({ error: { code: 'STALE_INTERVENTION_QUESTION', message: '原来源已变化', request_id: 'TOOL_ONLY_INTERVENTION_REJECT' } }), { status: 409 });
      if (intent.kind === 'DELIVER') claimed = true;
      if (intent.kind === 'ACKNOWLEDGE') acknowledgment = interventionResultFixture(intent);
      if (options.lost) throw new Error('TOOL_ONLY_RESPONSE_LOST'); value = intent.kind === 'DELIVER' ? interventionDeliveryFixture() : interventionResultFixture(intent);
    } else if (url.pathname.includes('/by-key/')) {
      const intent = operation.getInterventionOperation().pending!; const original = interventionLookupFixture(intent, !options.notFound, view()); if (options.mismatched && original.original_receipt) original.original_receipt.request_hash = '0'.repeat(64); value = original;
    } else if (url.pathname.includes('/finite-planning/sessions/')) value = interventionQuestionFixture();
    else if (url.pathname.includes('/decisions/')) value = interventionTraceFixture();
    else if (url.pathname.endsWith('/interventions')) value = interventionListFixture([options.freshStale && !options.stale ? interventionFixture(claimed) : view()]);
    else if (url.pathname.endsWith(`/interventions/${interventionId}`)) value = view();
    else throw new Error(`TOOL_ONLY_UNEXPECTED_INTERVENTION_PATH:${url.pathname}`);
    return new Response(JSON.stringify(value));
  })); return calls;
}
function openPage(blocked = false) { return render(<page.default mutationBlocked={blocked} />); }
async function ready() { await screen.findByRole('button', { name: `领取通知 ${interventionId}` }); await waitFor(() => expect(screen.queryByText('正在只读核对通知原件…')).not.toBeInTheDocument()); }

test('刷新只GET完整实际库存；读取通知不自动领取、不回答，源不足保STALE', async () => {
  const calls = transport({ stale: true }); openPage(); await ready(); expect(screen.getByRole('region', { name: '持久通知列表' })).toHaveTextContent('实际消息总数 1'); expect(screen.getByRole('button', { name: `领取通知 ${interventionId}` })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: `读取通知 ${interventionId}` })); const original = await screen.findByRole('region', { name: '通知原件' }); expect(original).toHaveTextContent('原来源 STALE'); expect(within(original).queryByRole('link')).not.toBeInTheDocument(); expect(screen.queryByRole('checkbox')).not.toBeInTheDocument(); expect(calls.every((row) => row.method === 'GET' && row.body === undefined)).toBe(true);
});

test('明确领取成功仍pending，单独GET固定Inbox原件后才可明确收阅；ACK原key恢复', async () => {
  const options = { notFound: true }; const calls = transport(options); openPage(); await ready(); fireEvent.click(screen.getByRole('button', { name: `领取通知 ${interventionId}` })); await screen.findByText('请求已返回；仍保留完整原命令，需单独只读核对。'); expect(operation.getInterventionOperation().pending!.kind).toBe('DELIVER'); expect(calls.filter((row) => row.method === 'POST')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '只读核对原通知请求' })); await screen.findByText('原固定收件身份已核对；是否曾呈现或被人看到仍未知，不重新投递。'); expect(operation.getInterventionOperation().pending).toBeNull(); expect(screen.getByRole('checkbox')).not.toBeChecked(); expect(screen.getByRole('button', { name: '提交明确收阅' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '提交明确收阅' })); await waitFor(() => expect(operation.getInterventionOperation().pending?.kind).toBe('ACKNOWLEDGE')); await waitFor(() => expect(operation.getInterventionOperation().busy).toBe(false)); const saved = structuredClone(operation.getInterventionOperation().pending!); expect(saved.body).toHaveProperty('acknowledged', true); expect(calls.filter((row) => row.method === 'POST')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: '只读核对原通知请求' })); await screen.findByRole('alert'); expect(operation.getInterventionOperation().pending).toEqual(saved); options.notFound = false;
  fireEvent.click(screen.getByRole('button', { name: '只读核对原通知请求' })); await screen.findByText('原键、完整命令和原回执已核对。收阅与通知记录均不回答或确认金融动作。'); expect(operation.getInterventionOperation().pending).toBeNull(); expect(screen.queryByRole('checkbox')).not.toBeInTheDocument(); expect(calls.filter((row) => row.method === 'POST')).toHaveLength(2);
});

test('DELIVER丢回复重开不POST，不重弹；其它族blocked仍允许自己只读核对', async () => {
  const calls = transport({ lost: true }); const mounted = openPage(); await ready(); fireEvent.click(screen.getByRole('button', { name: `领取通知 ${interventionId}` })); await screen.findByRole('alert'); await waitFor(() => expect(operation.getInterventionOperation().busy).toBe(false)); const saved = structuredClone(operation.getInterventionOperation().pending!); mounted.unmount(); openPage(true); await ready(); expect(screen.getByRole('region', { name: '待核对通知原请求' })).toHaveTextContent(saved.message_id!);
  fireEvent.click(screen.getByRole('button', { name: '只读核对原通知请求' })); await screen.findByText('原固定收件身份已核对；是否曾呈现或被人看到仍未知，不重新投递。'); expect(operation.getInterventionOperation().pending).toBeNull(); expect(screen.getByRole('checkbox')).toBeDisabled(); expect(calls.filter((row) => row.method === 'POST')).toHaveLength(1); expect(calls.filter((row) => row.method === 'GET' && row.url.pathname.endsWith(interventionId)).every((row) => row.url.search === '')).toBe(true);
});

test('登记问题必须读取当前revision与完整原trace；409保同原键，不能换键重提', async () => {
  const calls = transport({ rejected: true }); openPage(); await ready(); fireEvent.change(screen.getByLabelText('当前问答会话 ID'), { target: { value: questionSession } }); fireEvent.click(screen.getByRole('button', { name: '读取当前问题并登记通知' })); expect(await screen.findByRole('alert')).toHaveTextContent('TOOL_ONLY_INTERVENTION_REJECT'); const saved = operation.getInterventionOperation().pending!; expect(saved.kind).toBe('OBSERVE'); expect(Object.keys(saved.body).sort()).toEqual(['expected_epoch_id', 'expected_revision', 'expected_run_id', 'idempotency_key', 'intervention_policy_id', 'kind', 'reviewed_source_trace_hash', 'session_id']); expect(calls.filter((row) => row.method === 'POST')).toHaveLength(1);
  expect(calls.some((row) => row.method === 'GET' && row.url.pathname.includes('/finite-planning/sessions/'))).toBe(true); expect(calls.some((row) => row.method === 'GET' && row.url.pathname.includes('/decisions/'))).toBe(true); expect(screen.getByRole('button', { name: '读取当前问题并登记通知' })).toBeDisabled();
});

test('发送前来源变更阻止领取；跨族门不允许新观察、领取、收阅', async () => {
  const calls = transport({ freshStale: true }); const mounted = openPage(); await ready(); fireEvent.click(screen.getByRole('button', { name: `领取通知 ${interventionId}` })); expect(await screen.findByRole('alert')).toHaveTextContent('通知或来源已变化'); expect(operation.getInterventionOperation().pending).toBeNull(); expect(calls.every((row) => row.method === 'GET')).toBe(true);
  mounted.unmount(); const blockedCalls = transport({ claimed: true }); openPage(true); await ready(); fireEvent.click(screen.getByRole('button', { name: `读取通知 ${interventionId}` })); await screen.findByRole('checkbox'); expect(screen.getByRole('checkbox')).toBeDisabled(); expect(screen.getByRole('button', { name: '提交明确收阅' })).toBeDisabled(); expect(blockedCalls.every((row) => row.method === 'GET')).toBe(true);
});

test('不同原回执不能清请求；完整body在原键只读查回，无自动重试', async () => {
  const calls = transport({ mismatched: true }); openPage(); await ready(); fireEvent.change(screen.getByLabelText('当前问答会话 ID'), { target: { value: questionSession } }); fireEvent.click(screen.getByRole('button', { name: '读取当前问题并登记通知' })); await screen.findByText('请求已返回；仍保留完整原命令，需单独只读核对。'); const saved = structuredClone(operation.getInterventionOperation().pending!); fireEvent.click(screen.getByRole('button', { name: '只读核对原通知请求' })); await screen.findByRole('alert'); expect(operation.getInterventionOperation().pending).toEqual(saved); expect(calls.filter((row) => row.method === 'POST')).toHaveLength(1); expect(calls.filter((row) => row.url.pathname.includes('/by-key/')).every((row) => row.method === 'GET' && row.url.search === '')).toBe(true);
});

test('原payload来源和当前新观察分开展示，读取不自动投递或回答', async () => {
  const view = interventionCurrentObservationFixture(); const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push(init?.method ?? 'GET'); return new Response(JSON.stringify(String(input).includes(`/${interventionId}`) ? view : interventionListFixture([view])));
  }));
  openPage(); await ready(); fireEvent.click(screen.getByRole('button', { name: `读取通知 ${interventionId}` }));
  const original = await screen.findByRole('region', { name: '不可变消息来源' }); const current = screen.getByRole('region', { name: '服务端当前问题观察' });
  expect(original).toHaveTextContent(view.original_message.source_run_id); expect(original).toHaveTextContent(view.payload_hash);
  expect(current).toHaveTextContent(view.current_question_observation!.session_id); expect(current).toHaveTextContent(view.current_question_observation!.source_run_id);
  expect(current).toHaveTextContent(view.current_question_observation!.original_receipt.idempotency_key);
  expect(current).toHaveTextContent('完整世界语义、原轨迹和审计由服务器核验');
  expect(screen.getByRole('link', { name: '到一次一问页面读取当前问题并回答' })).toHaveAttribute('href', '#questions');
  expect(calls).toEqual(['GET', 'GET']); expect(operation.getInterventionOperation().pending).toBeNull();
});

test.each(['LEGACY_TERMINAL_SOURCE', 'UNVERIFIED'] as const)('%s不复活通知或展示可操作当前问题', async (binding) => {
  const view = binding === 'LEGACY_TERMINAL_SOURCE' ? interventionCurrentObservationFixture() : interventionFixture();
  view.current_source_binding = binding; view.pending = false; view.effective_state = 'INVALIDATED'; view.stored_state = 'INVALIDATED';
  if (binding === 'UNVERIFIED') { view.source_status = 'UNKNOWN'; view.effective_state = 'UNKNOWN'; view.current_question = null; }
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push(init?.method ?? 'GET'); return new Response(JSON.stringify(String(input).includes(`/${interventionId}`) ? view : interventionListFixture([view])));
  }));
  openPage(); await ready(); expect(screen.getByRole('button', { name: `领取通知 ${interventionId}` })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: `读取通知 ${interventionId}` })); const section = await screen.findByRole('region', { name: '通知原件' });
  expect(within(section).queryByRole('link')).not.toBeInTheDocument(); expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  if (binding === 'LEGACY_TERMINAL_SOURCE') { expect(section).toHaveTextContent('不会复活此消息、领取或收阅'); expect(within(section).queryByRole('region', { name: '服务端当前问题观察' })).not.toBeInTheDocument(); expect(within(section).getByRole('region', { name: '历史终态观察原件' })).toBeInTheDocument(); }
  else expect(section).toHaveTextContent('当前来源未证明');
  expect(calls).toEqual(['GET', 'GET']);
});
