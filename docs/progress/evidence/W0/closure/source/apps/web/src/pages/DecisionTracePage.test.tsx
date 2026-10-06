import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import App from '../App';
import DecisionTracePage from './DecisionTracePage';
import { installHttpFixture } from '../tests/policy-fixture';
import { actionId, evidenceId, runId, traceFixture } from '../tests/trace-fixture';

afterEach(() => { window.history.replaceState(null, '', '/'); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
function openPage(run: string | null = runId, app = false) {
  window.history.replaceState(null, '', run ? `/#decisions/${run}` : '/#decisions');
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>{app ? <App /> : <DecisionTracePage runId={run ?? undefined} />}</QueryClientProvider>);
}
function failure(status: number, code: string) { return new Response(JSON.stringify({ error: { code, message: `单元HTTP失败 ${code}`, request_id: 'unit-error' } }), { status }); }

test('八层历史详情、当前来源另注、声明转义且理由定位展开第30条并保持同run', async () => {
  const fixture = traceFixture(); fixture.explanation!.reasons[0]!.references = ['constraints[29].required_cents', 'candidates[0].reasons[0]'];
  const requests = installHttpFixture(() => fixture); openPage(runId, true);
  await screen.findByRole('region', { name: '8 审计哈希状态' });
  for (const name of ['1 银行事实与证据', '2 当时用户策略', '3 受保护金额与约束', '4 候选动作', '5 拒绝与限制原因', '6 当时自主等级', '7 当前关联动作与回执']) expect(screen.getByRole('region', { name })).toBeVisible();
  expect(screen.getByRole('region', { name: '1 银行事实与证据' })).toHaveTextContent('当时状态 VALID');
  expect(screen.getByText(/当前关联状态/)).toHaveTextContent('SUPERSEDED');
  expect(screen.getByRole('region', { name: '8 审计哈希状态' })).toHaveTextContent('所选记录审计链：已核验');
  expect(screen.queryByText('NOT_IMPLEMENTED')).not.toBeInTheDocument();
  expect(screen.queryByText('baseline:29:BEFORE_PAYMENT')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '定位 constraints[29].required_cents' }));
  await waitFor(() => expect(screen.getByText('baseline:29:BEFORE_PAYMENT')).toBeVisible());
  expect(window.location.hash).toBe(`#decisions/${runId}`); expect(document.activeElement).toHaveAttribute('id', 'trace-constraint-29');
  fireEvent.click(within(document.activeElement as HTMLElement).getByText('查看该约束计算点')); expect(await screen.findByText('¥-0.10')).toBeVisible();
  fireEvent.click(screen.getByText('查看证据声明原文'));
  expect(await screen.findByText('<script>原文</script>')).toBeVisible(); expect(document.querySelector('script')).toBeNull();
  expect(screen.getByText('错误金额声明')).toBeVisible(); expect(screen.getByText(/声明金额未作为可信计算金额/)).toBeVisible();
  expect(requests.map((r) => `${r.method} ${r.path}`)).toEqual([`GET /api/v1/decisions/${runId}`]);
});

test('UNKNOWN当前关联不充作历史结论，回执按需GET且409仍未完成', async () => {
  const requests = installHttpFixture((_method, path) => path.endsWith('/receipt') ? failure(409, 'RECEIPT_NOT_READY') : traceFixture()); openPage();
  const actions = await screen.findByRole('region', { name: '7 当前关联动作与回执' });
  expect(actions).toHaveTextContent('UNKNOWN'); expect(requests).toHaveLength(1);
  expect(screen.getByRole('region', { name: '6 当时自主等级' })).toHaveTextContent('BLOCKED');
  fireEvent.click(within(actions).getByRole('button', { name: '读取原动作回执' }));
  expect(await within(actions).findByRole('alert')).toHaveTextContent('RECEIPT_NOT_READY');
  expect(actions).toHaveTextContent('仍未完成'); expect(requests.every((r) => r.method === 'GET')).toBe(true);
});

test('已核对回执关联、精确金额与经济发生/对账时点分开显示', async () => {
  const fixture = traceFixture(); fixture.actions[0]!.receipt_id = evidenceId; fixture.actions[0]!.status = 'SUCCEEDED';
  installHttpFixture((_method, path) => path.endsWith('/receipt') ? { simulation: true, receipt_id: evidenceId, action_id: actionId, bank_operation_id: actionId,
    status: 'SUCCEEDED', executed_cents: 101, fee_cents: 1, loss_cents: 0, posting_ids: [evidenceId], occurred_at: '2026-10-04T01:00:00Z', reconciled_at: '2026-10-04T01:01:00Z' } : fixture);
  openPage(); fireEvent.click(await screen.findByRole('button', { name: '读取原动作回执' }));
  const receipt = await screen.findByRole('region', { name: `回执 ${evidenceId}` });
  expect(receipt).toHaveTextContent('¥1.01'); expect(receipt).toHaveTextContent('经济发生时点 2026-10-04T01:00:00Z'); expect(receipt).toHaveTextContent('对账时点 2026-10-04T01:01:00Z');
  expect(screen.getByRole('region', { name: '6 当时自主等级' })).toHaveTextContent('BLOCKED');
});

test('列表真实opaque翻页、记录状态非资金成功、父子只用真实run', async () => {
  const fixture = traceFixture(); let page = 0;
  installHttpFixture(() => ({ simulation: true, user_id: fixture.user_id, items: [{ run_id: page++ ? evidenceId : runId, as_of: fixture.as_of,
    trigger_type: 'UNIT', status: 'SUCCEEDED', completeness: 'COMPLETE', phase: 'EVALUATION', parent_run_id: null, action_id: null }], next_cursor: page === 1 ? 'opaque/+_=' : null }));
  openPage(null); await screen.findByRole('link', { name: `查看 ${runId}` }); expect(screen.getByText(/记录状态与资金执行结果分别展示/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '加载下一页' })); await screen.findByRole('link', { name: `查看 ${evidenceId}` });
  expect(String(vi.mocked(fetch).mock.calls[1]![0])).toContain('cursor=opaque%2F%2B_%3D');
  expect(screen.queryByRole('button', { name: '加载下一页' })).not.toBeInTheDocument();
});

test('历史不完整与不支持版本保留真实状态，不补造八层结论', async () => {
  const fixture = traceFixture(); fixture.completeness = 'LEGACY_PARTIAL'; fixture.trace = null; fixture.explanation = null; fixture.legacy_result = { amount_cents: '旧坏声明' };
  installHttpFixture(() => fixture); openPage(); expect(await screen.findByText('历史仅保存部分内容')).toBeVisible();
  expect(screen.queryByRole('region', { name: '6 当时自主等级' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('查看旧记录原文')); fireEvent.click(await screen.findByText('legacy_result · 1 项')); expect(await screen.findByText('旧坏声明')).toBeVisible();
  fixture.completeness = 'UNSUPPORTED_VERSION'; fireEvent.click(screen.getByRole('button', { name: '刷新所选记录' }));
  expect(await screen.findByText('当前版本不能完整解释')).toBeVisible();
});

test('详情404/409失败隐藏旧成功面板，可重试恢复', async () => {
  let status = 200; installHttpFixture(() => status === 200 ? traceFixture() : failure(status, status === 404 ? 'NOT_FOUND' : 'DECISION_TRACE_INTEGRITY_ERROR')); openPage();
  await screen.findByRole('region', { name: '8 审计哈希状态' }); status = 409; fireEvent.click(screen.getByRole('button', { name: '刷新所选记录' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('DECISION_TRACE_INTEGRITY_ERROR'); expect(screen.queryByRole('region', { name: '8 审计哈希状态' })).not.toBeInTheDocument();
  status = 404; fireEvent.click(screen.getByRole('button', { name: '重试读取' })); expect(await screen.findByRole('alert')).toHaveTextContent('NOT_FOUND');
  status = 200; fireEvent.click(screen.getByRole('button', { name: '重试读取' })); expect(await screen.findByRole('region', { name: '8 审计哈希状态' })).toBeVisible();
});

test('声明unsafe数字明确保真限制，按需原响应仍保留9007199254740993词法', async () => {
  const raw = JSON.stringify(traceFixture()).replace('"错误金额声明"', '9007199254740993'); installHttpFixture(() => new Response(raw)); openPage();
  expect(await screen.findByText(/解析字段可能已舍入/)).toBeVisible(); expect(screen.queryByText(/9007199254740993/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('查看原响应文本（保留原始数字词法）'));
  for (let count = 0; count < 10 && !screen.queryByText(/9007199254740993/); count++) fireEvent.click(await screen.findByRole('button', { name: '继续显示原文字符' }));
  expect(screen.getByText(/9007199254740993/)).toBeVisible();
});

test('实际父子编号可导航，null金额待核验且可信坏金额不会显示八层成功', async () => {
  const fixture = traceFixture(); fixture.trace!.parent_run_id = evidenceId; fixture.children = [actionId]; fixture.trace!.constraints![0]!.available_cents = null;
  installHttpFixture(() => fixture); openPage(); expect(await screen.findByRole('link', { name: `查看父阶段 ${evidenceId}` })).toHaveAttribute('href', `#decisions/${evidenceId}`);
  expect(screen.getByRole('link', { name: `查看子阶段 ${actionId}` })).toHaveAttribute('href', `#decisions/${actionId}`); expect(screen.getByRole('region', { name: '3 受保护金额与约束' })).toHaveTextContent('待核验');
  fixture.trace!.inputs.amount_cents = 1.1; fireEvent.click(screen.getByRole('button', { name: '刷新所选记录' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('精确金额'); expect(screen.queryByRole('region', { name: '8 审计哈希状态' })).not.toBeInTheDocument();
});

test('刷新read_at后原响应仍保留原始数字词法，不被query结构共享丢失', async () => {
  const fixture = traceFixture(); let reads = 0;
  installHttpFixture(() => { fixture.read_at = reads++ ? '2026-10-04T00:01:00Z' : '2026-10-04T00:00:00Z'; return new Response(JSON.stringify(fixture).replace('"错误金额声明"', '9007199254740993')); });
  openPage(); await screen.findByRole('region', { name: '8 审计哈希状态' }); fireEvent.click(screen.getByRole('button', { name: '刷新所选记录' }));
  await screen.findByText('当前关联读取时点 2026-10-04T00:01:00Z'); fireEvent.click(screen.getByText('查看原响应文本（保留原始数字词法）'));
  await screen.findByText('服务原响应，含未核验声明。仅分段展示文字，不据此计算金额。');
  for (let count = 0; count < 10 && !screen.queryByText(/9007199254740993/); count++) fireEvent.click(await screen.findByRole('button', { name: '继续显示原文字符' }));
  expect(screen.getByText(/9007199254740993/)).toBeVisible(); expect(screen.queryByText('本次读取未保留原响应文本。')).not.toBeInTheDocument();
});

test('理由能展开第21字段之后的数组分支，不存在的引用不造可点击锚点', async () => {
  const fixture = traceFixture(); fixture.trace!.outcome = Object.fromEntries(Array.from({ length: 25 }, (_, index) => [`field_${index}`, index]));
  fixture.trace!.outcome.extra = [{ margin_cents: -123 }]; fixture.explanation!.reasons[0]!.references = ['outcome.extra[0].margin_cents', 'constraints[0].not_saved'];
  installHttpFixture(() => fixture); openPage(); fireEvent.click(await screen.findByRole('button', { name: '定位 outcome.extra[0].margin_cents' }));
  expect(await screen.findByText('¥-1.23')).toBeVisible(); expect(screen.queryByRole('button', { name: '定位 constraints[0].not_saved' })).not.toBeInTheDocument();
});
