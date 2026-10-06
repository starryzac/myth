import { afterEach, expect, test, vi } from 'vitest';
import { getEvidenceGraph, getFacts, getOriginalEvidenceResponse, parseEvidenceGraph, parseFacts } from './evidence';
import type { FactQuery } from './evidence';
import { evidenceId, factsFixture, graphFixture, priorEvidenceId } from '../tests/evidence-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('双时态查询保原时区与来源，未来known传给可信服务端拒绝；无写请求', async () => {
  const capture: { url: URL; method: string; body?: unknown }[] = []; vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local');
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    capture.push({ url: new URL(String(input)), method: options?.method ?? 'GET', body: options?.body });
    return new Response(JSON.stringify({ error: { code: 'FUTURE_KNOWLEDGE', message: '不能查询尚未观察到的系统知识', request_id: 'UNIT-FUTURE-REJECTED' } }), { status: 422 });
  }));
  await expect(getFacts({ valid_at: '2026-10-05T12:00:00+08:00', known_at: '2099-01-01T00:00:00Z', source_type: 'source/with space', source_ref: '原件&ref' })).rejects.toMatchObject({ status: 422, code: 'FUTURE_KNOWLEDGE', requestId: 'UNIT-FUTURE-REJECTED' });
  expect(capture[0]!.url.searchParams.get('known_at')).toBe('2099-01-01T00:00:00Z'); expect(capture[0]!.url.searchParams.get('valid_at')).toBe('2026-10-05T12:00:00+08:00'); expect(capture[0]!.url.searchParams.get('source_ref')).toBe('原件&ref');
  expect(capture[0]).toMatchObject({ method: 'GET', body: undefined });
});
test('缺时区、额外主权参数、非法根kind/id在联网前拒绝', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  expect(() => getFacts({ known_at: '2026-10-05T12:00:00' })).toThrow('校验');
  expect(() => getFacts({ user_id: evidenceId } as unknown as FactQuery)).toThrow('校验');
  expect(() => getEvidenceGraph('BANK' as never, evidenceId)).toThrow('校验'); expect(() => getEvidenceGraph('EVIDENCE', '../wrong')).toThrow('校验'); expect(fetch).not.toHaveBeenCalled();
});
test('事实所有权、原件集合、有效/知悉区间和只读标志不一致拒绝', () => {
  for (const mutate of [
    (value: ReturnType<typeof factsFixture>) => { value.groups[0]!.originals[0]!.user_id = priorEvidenceId; },
    (value: ReturnType<typeof factsFixture>) => { value.groups[0]!.evidence_ids = [priorEvidenceId]; },
    (value: ReturnType<typeof factsFixture>) => { value.groups[0]!.originals[0]!.observed_at = '2099-01-01T00:00:00Z'; },
    (value: ReturnType<typeof factsFixture>) => { value.groups[0]!.originals[0]!.valid_to = value.valid_at; },
    (value: ReturnType<typeof factsFixture>) => { value.groups[0]!.originals = []; value.groups[0]!.evidence_ids = []; },
  ]) { const value = factsFixture(); mutate(value); expect(() => parseFacts(value)).toThrow('校验'); }
  expect(() => parseFacts({ ...factsFixture(), execution_authority: true })).toThrow('校验');
});
test('CONFLICTED/UNKNOWN保留服务端状态及问题，不推算成功；原大整数JSON完整保留', async () => {
  const value = factsFixture(); value.state = 'CONFLICTED'; value.groups[0]!.state = 'CONFLICTED'; expect(parseFacts(value).state).toBe('CONFLICTED');
  value.state = 'UNKNOWN'; value.groups[0]!.state = 'UNKNOWN'; value.issues = [{ code: 'STATUS_HISTORY_NOT_PROVEN' }]; value.groups[0]!.originals[0]!.status = 'SUPERSEDED';
  const original = JSON.stringify(value).replace('"amount_cents":1', '"amount_cents":9223372036854775807');
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); vi.stubGlobal('fetch', vi.fn(async () => new Response(original)));
  const parsed = await getFacts(); expect(parsed.state).toBe('UNKNOWN'); expect(parsed.groups[0]!.originals[0]!.status).toBe('SUPERSEDED'); expect(getOriginalEvidenceResponse(parsed)).toBe(original);
});
test('图只接受原字段确有的有向边；root、owner、类型和重复身份错误拒绝', () => {
  const value = graphFixture(); expect(parseEvidenceGraph(value, `EVIDENCE:${evidenceId}`).edges[0]!.relation).toBe('SUPERSEDES');
  expect(() => parseEvidenceGraph(value, `ACTION:${evidenceId}`)).toThrow('校验');
  for (const mutate of [
    (graph: ReturnType<typeof graphFixture>) => { graph.nodes[0]!.original.user_id = priorEvidenceId; },
    (graph: ReturnType<typeof graphFixture>) => { graph.nodes.push(graph.nodes[0]!); },
    (graph: ReturnType<typeof graphFixture>) => { graph.edges[0]!.relation = 'SUPPORTED_BY'; },
    (graph: ReturnType<typeof graphFixture>) => { graph.edges[0]!.from = `EVIDENCE:${priorEvidenceId}`; },
  ]) { const graph = graphFixture(); mutate(graph); expect(() => parseEvidenceGraph(graph)).toThrow('校验'); }
});
test('实际断链仅在具体UNKNOWN问题存在时保留；不补节点或伪造全解析', () => {
  const graph = graphFixture(); graph.nodes.pop(); expect(() => parseEvidenceGraph(graph)).toThrow('校验');
  graph.state = 'UNKNOWN'; graph.issues = [{ code: 'BROKEN_REFERENCE', kind: 'EVIDENCE', id: priorEvidenceId }];
  expect(parseEvidenceGraph(graph).nodes).toHaveLength(1); expect(parseEvidenceGraph(graph).edges[0]!.to).toBe(`EVIDENCE:${priorEvidenceId}`);
  graph.issues = [{ code: 'UNRELATED_ISSUE' }]; expect(() => parseEvidenceGraph(graph)).toThrow('校验');
});

test('历史查询的时点/来源及图known必须与原请求一致，不能用当前结果替代', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).includes('/graph/') ? graphFixture() : factsFixture()))));
  await expect(getFacts({ valid_at: '2026-10-05T12:00:00+08:00' })).rejects.toThrow('校验');
  await expect(getFacts({ known_at: '2026-10-04T00:00:00Z' })).rejects.toThrow('校验');
  await expect(getFacts({ source_type: 'another-source' })).rejects.toThrow('校验');
  await expect(getEvidenceGraph('EVIDENCE', evidenceId, '2026-10-04T00:00:00Z')).rejects.toThrow('校验');
  expect((await getFacts({ valid_at: '2026-10-05T20:00:00+08:00', known_at: '2026-10-05T21:00:00+08:00' })).state).toBe('VALID');
});
