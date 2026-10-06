import { afterEach, expect, test, vi } from 'vitest';
import { getDeliveries, getDelivery, getOriginalDeliveryResponse, parseDeliveries, parseDelivery } from './delivery';
import { actionId, deliveryFixture, otherOutboxId, outboxId } from '../tests/delivery-fixture';
import { beginWriteFlight, endWriteFlight, isWriteInFlight } from '../features/write-flight';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); endWriteFlight(); });
test('只读列表与同原件详情，仅使用原outbox编号且保原文本，不产生enqueue/POST/新键', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); const source = deliveryFixture(); const original = ` \n${JSON.stringify(source)}\n`; const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    calls.push({ url: String(input), method: options?.method ?? 'GET', body: options?.body });
    return new Response(String(input).includes('?limit=') ? JSON.stringify({ simulation: true, economic_verified: false, items: [source] }) : original);
  }));
  const list = await getDeliveries(); const detail = await getDelivery(outboxId, list.items[0]!);
  expect(calls).toEqual([{ url: 'http://http-unit-fixture.local/api/v1/delivery?limit=50', method: 'GET', body: undefined }, { url: `http://http-unit-fixture.local/api/v1/delivery/${outboxId}`, method: 'GET', body: undefined }]);
  expect(getOriginalDeliveryResponse(detail)).toBe(original); expect(getOriginalDeliveryResponse(list)).toBe(JSON.stringify({ simulation: true, economic_verified: false, items: [source] }));
});
test('非法ID/读取限额在联网前拒绝，读请求不占用或释放原共享写锁', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify(deliveryFixture()))); vi.stubGlobal('fetch', fetch);
  expect(() => getDelivery('../actions/new')).toThrow('校验'); expect(() => getDeliveries(0)).toThrow('校验'); expect(() => getDeliveries(101)).toThrow('校验'); expect(() => getDelivery(outboxId, { ...deliveryFixture(), outbox_id: otherOutboxId })).toThrow('校验'); expect(fetch).not.toHaveBeenCalled();
  beginWriteFlight(); await getDelivery(outboxId); expect(isWriteInFlight()).toBe(true);
});
test('根/动作/原epoch/payload或请求outbox身份漂移拒绝，不拼接别的action', () => {
  for (const change of [
    { root_id: otherOutboxId }, { action_id: otherOutboxId }, { epoch_id: otherOutboxId }, { payload_hash: 'b'.repeat(64) }, { outbox_id: otherOutboxId },
  ]) expect(() => parseDelivery({ ...deliveryFixture(), ...change }, undefined, deliveryFixture())).toThrow('校验');
});
test('尝试缺号、重复编号、跨消息结果、非完整时序和伪资金验真拒绝', () => {
  for (const mutate of [
    (source: ReturnType<typeof deliveryFixture>) => { source.attempts[0]!.attempt_number = 2; },
    (source: ReturnType<typeof deliveryFixture>) => { source.attempts.push({ ...source.attempts[0]!, attempt_number: 2 }); },
    (source: ReturnType<typeof deliveryFixture>) => { source.attempts[0]!.result!.action_id = otherOutboxId; },
    (source: ReturnType<typeof deliveryFixture>) => { source.attempts[0]!.result!.root_id = otherOutboxId; },
    (source: ReturnType<typeof deliveryFixture>) => { source.attempts[0]!.result!.attempt_id = actionId; },
    (source: ReturnType<typeof deliveryFixture>) => { source.attempts[0]!.finished_at = '2026-10-05T11:59:59Z'; },
    (source: ReturnType<typeof deliveryFixture>) => { source.attempts[0]!.started_at = '2026-10-05T12:00:00'; },
    (source: ReturnType<typeof deliveryFixture>) => { source.attempts[0]!.result!.economic_verified = true; },
    (source: ReturnType<typeof deliveryFixture>) => { source.inbox_state = null; },
  ]) { const source = deliveryFixture(); mutate(source); expect(() => parseDelivery(source)).toThrow('校验'); }
});
test('success字符串不代替service核验，UNKNOWN银行SETTLED与归档原历史合法保留', () => {
  const unknown = parseDelivery(deliveryFixture()); expect(unknown.source_action_status).toBe('UNKNOWN'); expect(unknown.bank_status).toBe('SETTLED'); expect(unknown.service_receipt_verified).toBe(false);
  const archived = parseDelivery(deliveryFixture('ARCHIVED')); expect(archived.current_action_available).toBe(false); expect(archived.attempts).toHaveLength(1);
  const claimed = deliveryFixture(); claimed.inbox_state = 'SERVICE_RECEIPT_VERIFIED'; claimed.outbox_state = 'DELIVERED'; expect(parseDelivery(claimed).service_receipt_verified).toBe(false);
  expect(parseDelivery(deliveryFixture('VERIFIED')).economic_verified).toBe(false);
  expect(() => parseDelivery({ ...unknown, service_receipt_verified: true })).toThrow('校验'); expect(() => parseDelivery({ ...unknown, economic_verified: true })).toThrow('校验');
});
test('列表超原查询分母、重复原消息与simulation缺失不作为成功读取', () => {
  const source = deliveryFixture(); expect(() => parseDeliveries({ simulation: true, economic_verified: false, items: [source, source] })).toThrow('校验');
  expect(() => parseDeliveries({ simulation: true, economic_verified: false, items: [source] }, undefined, 0)).toThrow('校验'); expect(() => parseDelivery({ ...source, simulation: false })).toThrow('校验');
});
test('服务错误/连接中断只失败读取，不重试或发送其他请求', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ error: { code: 'INVALID_DELIVERY_SOURCE', message: '原消息冲突', request_id: 'UNIT-DELIVERY-READ-REJECTED' } }), { status: 409 })); vi.stubGlobal('fetch', fetch);
  await expect(getDelivery(outboxId)).rejects.toMatchObject({ code: 'INVALID_DELIVERY_SOURCE', requestId: 'UNIT-DELIVERY-READ-REJECTED' }); expect(fetch).toHaveBeenCalledTimes(1);
  fetch.mockRejectedValueOnce(new Error('UNIT_NETWORK_INTERRUPTED')); await expect(getDelivery(outboxId)).rejects.toMatchObject({ code: 'NETWORK_ERROR' }); expect(fetch).toHaveBeenCalledTimes(2);
});
