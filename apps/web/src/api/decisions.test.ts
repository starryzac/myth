import { afterEach, expect, test, vi } from 'vitest';
import { getDecisions, getDecision, getOriginalTraceResponse, getTraceReceipt, hasUnsafeDeclarationNumbers, parseTraceResponse } from './decisions';
import { request } from './http';
import { installHttpFixture } from '../tests/policy-fixture';
import { runId, traceFixture } from '../tests/trace-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

test('原始坏金额声明隔离，合法金额options数组逐项严格校验', () => {
  const fixture = traceFixture(); expect(parseTraceResponse(fixture).trace!.sources![0]!.content.amount_cents).toBe('错误金额声明');
  for (const invalid of [[1], [0, 2], [1, 9007199254740992], [1, '2'], [1, 1.1]]) {
    fixture.trace!.inputs.amount_options_cents = invalid; expect(() => parseTraceResponse(fixture)).toThrow();
  }
  fixture.trace!.inputs.amount_options_cents = [1, 2]; fixture.trace!.constraints![0]!.available_cents = 9007199254740992;
  expect(() => parseTraceResponse(fixture)).toThrow();
  fixture.trace!.constraints![0]!.available_cents = null; fixture.trace!.inputs.amount_options_cents = null; expect(parseTraceResponse(fixture)).toEqual(fixture);
});

test('Raw坏声明原始9007199254740993数字词法保留，COMPLETE身份和解释不能缺失', async () => {
  const fixture = traceFixture(); const raw = JSON.stringify(fixture).replace('"错误金额声明"', '9007199254740993');
  installHttpFixture(() => new Response(raw)); const result = await getDecision(runId);
  expect(hasUnsafeDeclarationNumbers(result)).toBe(true); expect(getOriginalTraceResponse(result)).toContain('9007199254740993');
  fixture.explanation = null; expect(() => parseTraceResponse(fixture)).toThrow();
  fixture.explanation = traceFixture().explanation; fixture.explanation!.run_id = fixture.user_id; expect(() => parseTraceResponse(fixture)).toThrow();
});
test('列表opaque游标原样GET，详情身份校验，默认402金额guard未绕过', async () => {
  const fixture = traceFixture(); const requests = installHttpFixture((_method, path) => path === '/api/v1/decisions'
    ? { simulation: true, user_id: fixture.user_id, items: [], next_cursor: null } : fixture);
  await getDecisions('opaque_abc-123'); expect(requests[0]!.method).toBe('GET');
  expect(vi.mocked(fetch).mock.calls[0]![0]).toBe('http://http-unit-fixture.local/api/v1/decisions?limit=20&cursor=opaque_abc-123');
  await getDecision(runId); fixture.run_id = fixture.user_id;
  await expect(getDecision(runId)).rejects.toThrow();
  installHttpFixture(() => ({ simulation: true, amount_cents: '100' }));
  await expect(request('/policies')).rejects.toThrow('精确');
});

test('仅明确Raw声明与legacy原文豁免，动态计算/分项/金额数组不得漏检', () => {
  for (const key of ['inputs', 'outcome']) {
    const fixture = traceFixture(); fixture.trace![key as 'inputs' | 'outcome'].nested = { bad_cents: '100' };
    expect(() => parseTraceResponse(fixture)).toThrow();
  }
  const fixture = traceFixture(); fixture.legacy_result = { bad_cents: '仅原文' }; expect(parseTraceResponse(fixture)).toEqual(fixture);
  fixture.trace!.constraints![0]!.calculation = { protected_cents_by_reason: { bad: 1.5 } }; expect(() => parseTraceResponse(fixture)).toThrow();
  fixture.trace!.constraints![0]!.calculation = {}; fixture.trace!.candidates![0]!.result = { bad_cents: [100, 200] }; expect(() => parseTraceResponse(fixture)).toThrow();
});

test('回执必须同原action/operation/receipt且金额精确，409保留原码', async () => {
  const link = traceFixture().actions[0]!;
  const receipt = { simulation: true, action_id: link.action_id, bank_operation_id: link.bank_operation_id, receipt_id: runId,
    status: 'SUCCEEDED', executed_cents: 100, fee_cents: 0, loss_cents: 0, posting_ids: [], occurred_at: '2026-10-04T00:00:00Z', reconciled_at: null };
  installHttpFixture(() => receipt); expect((await getTraceReceipt(link)).executed_cents).toBe(100);
  receipt.bank_operation_id = runId; await expect(getTraceReceipt(link)).rejects.toThrow();
  installHttpFixture(() => new Response(JSON.stringify({ error: { code: 'RECEIPT_NOT_READY', message: '动作尚无已对账回执', request_id: 'unit-receipt' } }), { status: 409 }));
  await expect(getTraceReceipt(link)).rejects.toMatchObject({ status: 409, code: 'RECEIPT_NOT_READY' });
});

test('无原bank操作编号不能通过同null回执关联', async () => {
  const link = traceFixture().actions[0]!; link.bank_operation_id = null;
  installHttpFixture(() => ({ simulation: true, receipt_id: runId, action_id: link.action_id, bank_operation_id: null, status: 'SUCCEEDED',
    executed_cents: 100, fee_cents: 0, loss_cents: 0, posting_ids: [], occurred_at: '2026-10-04T00:00:00Z', reconciled_at: null }));
  await expect(getTraceReceipt(link)).rejects.toThrow();
});

test('阶段的subject action必须对应当前关联，不强制其原决策等于深子run', () => {
  const fixture = traceFixture(); fixture.trace!.action_id = runId; expect(() => parseTraceResponse(fixture)).toThrow();
  fixture.trace!.action_id = fixture.actions[0]!.action_id; fixture.actions[0]!.decision_run_id = fixture.user_id;
  expect(parseTraceResponse(fixture)).toEqual(fixture);
});

test('可信JSON内foreign user不能跨身份，Raw声明保留foreign原声明', () => {
  const fixture = traceFixture(); fixture.trace!.inputs.effect = { user_id: runId }; expect(() => parseTraceResponse(fixture)).toThrow();
  fixture.trace!.inputs.effect = { user_id: fixture.user_id }; fixture.trace!.sources![0]!.content.user_id = runId;
  expect(parseTraceResponse(fixture)).toEqual(fixture);
});

test('信任区未知金额分项显式null合法，不转成零或伪造空集合', () => {
  const fixture = traceFixture(); fixture.trace!.constraints![0]!.calculation = { protected_cents_by_reason: null };
  expect(parseTraceResponse(fixture)).toEqual(fixture);
});
