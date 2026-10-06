import { afterEach, expect, test, vi } from 'vitest';
import { fullGraphTables, getFullEvidenceGraph, getOriginalFullGraphResponse, parseFullEvidenceGraph } from './full-evidence-graph';
import { fullGraphAccount, fullGraphFixture, fullGraphKnown, fullGraphTransaction, fullGraphUser } from '../tests/full-evidence-graph-fixture';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('完整35类同owner原分母与双向导航仅引用，不升级为金融权限', () => {
  const graph = parseFullEvidenceGraph(fullGraphFixture()); expect(graph.inventory).toHaveLength(35); expect(Object.keys(fullGraphTables)).toHaveLength(35);
  expect(graph.nodes[0]!.proof.state).toBe('NOT_CHECKED'); expect(graph.state).toBe('REFERENCES_RESOLVED'); expect(graph.grants_authority).toBe(false); expect(graph.financial_success_inferred).toBe(false);
});
test.each([
  ['删掉注册表', (v: ReturnType<typeof fullGraphFixture>) => { v.inventory.pop(); }],
  ['重复注册表', (v: ReturnType<typeof fullGraphFixture>) => { v.inventory[1] = v.inventory[0]!; }],
  ['改原表', (v: ReturnType<typeof fullGraphFixture>) => { v.inventory[0]!.table = 'invented_table'; }],
  ['缩分母', (v: ReturnType<typeof fullGraphFixture>) => { v.inventory[0]!.actual_owned_count = 0; }],
  ['boolean分母', (v: ReturnType<typeof fullGraphFixture>) => { (v.inventory[0] as unknown as { known_count: unknown }).known_count = true; }],
  ['不安全整数分母', (v: ReturnType<typeof fullGraphFixture>) => { v.inventory[0]!.actual_owned_count = Number.MAX_SAFE_INTEGER + 1; }],
  ['错误捕获完整', (v: ReturnType<typeof fullGraphFixture>) => { v.inventory[0]!.complete = false; }],
  ['错误图完整', (v: ReturnType<typeof fullGraphFixture>) => { v.complete_registered_inventory = false; }],
  ['节点超捕获分母', (v: ReturnType<typeof fullGraphFixture>) => { v.inventory[0]!.actual_owned_count = 0; v.inventory[0]!.known_count = 0; v.inventory[0]!.captured_count = 0; }],
  ['浮点边分母', (v: ReturnType<typeof fullGraphFixture>) => { v.expected_edge_count = 1.5; }],
] as const)('%s不能冒充完整来源', (_name, change) => { const value = fullGraphFixture(); change(value); expect(() => parseFullEvidenceGraph(value)).toThrow('校验'); });
test.each([
  ['其他owner', (v: ReturnType<typeof fullGraphFixture>) => { v.nodes[0]!.original!.user_id = fullGraphTransaction; }],
  ['共享标签换owner', (v: ReturnType<typeof fullGraphFixture>) => { v.nodes[0]!.owner_scope = 'SHARED_PRODUCT_CATALOGUE'; }],
  ['未来原件', (v: ReturnType<typeof fullGraphFixture>) => { v.nodes[0]!.known_at = '2099-01-01T00:00:00Z'; }],
  ['重复节点', (v: ReturnType<typeof fullGraphFixture>) => { v.nodes[1] = v.nodes[0]!; }],
  ['缺当前内容', (v: ReturnType<typeof fullGraphFixture>) => { v.nodes[0]!.original = null; v.nodes[0]!.row_hash = null; }],
  ['伪金融成功', (v: ReturnType<typeof fullGraphFixture>) => { (v as unknown as { financial_success_inferred: boolean }).financial_success_inferred = true; }],
  ['节点授权限', (v: ReturnType<typeof fullGraphFixture>) => { (v.nodes[0] as unknown as { execution_authority: boolean }).execution_authority = true; }],
  ['false字串', (v: ReturnType<typeof fullGraphFixture>) => { (v as unknown as { performs_repair: unknown }).performs_repair = 'false'; }],
  ['银行UNKNOWN仍称解析', (v: ReturnType<typeof fullGraphFixture>) => { v.bank_proof.state = 'UNKNOWN'; }],
] as const)('%s不能越过身份/时间/只读边界', (_name, change) => { const value = fullGraphFixture(); change(value); expect(() => parseFullEvidenceGraph(value)).toThrow('校验'); });
test('USER和共享产品采用各自实际所有权，不按普通user_id套用', () => {
  const value = fullGraphFixture(); const first = value.nodes[0]!;
  first.key = `USER:${fullGraphUser}`; first.id = fullGraphUser; first.kind = 'USER'; first.original = { id: fullGraphUser, created_at: fullGraphKnown }; value.root = first.key; value.requested_root_kind = 'USER'; value.nodes = [first]; value.displayed_node_count = 1; value.expected_node_count = 1; value.edges = []; value.expected_edge_count = 0;
  const row = value.inventory.find((v) => v.kind === 'USER')!; row.actual_owned_count = 1; row.known_count = 1; row.captured_count = 1;
  expect(parseFullEvidenceGraph(value).nodes[0]!.kind).toBe('USER'); first.id = fullGraphTransaction; first.key = `USER:${first.id}`; first.original.id = first.id; value.root = first.key; expect(() => parseFullEvidenceGraph(value)).toThrow('校验');
  first.kind = 'PRODUCT'; first.key = `PRODUCT:${first.id}`; first.owner_scope = 'SHARED_PRODUCT_CATALOGUE'; value.root = first.key; value.requested_root_kind = 'PRODUCT'; const product = value.inventory.find((v) => v.kind === 'PRODUCT')!; product.actual_owned_count = 1; product.known_count = 1; product.captured_count = 1;
  expect(parseFullEvidenceGraph(value).nodes[0]!.owner_scope).toBe('SHARED_PRODUCT_CATALOGUE'); first.original.user_id = fullGraphUser; expect(() => parseFullEvidenceGraph(value)).toThrow('校验');
});
test('历史可变内容与hash必须null，历史不可变原件保留，不能用当前值补过去', () => {
  const value = fullGraphFixture('HISTORICAL'); expect(parseFullEvidenceGraph(value).nodes[0]!.original).toBeNull();
  value.nodes[0]!.original = fullGraphFixture().nodes[0]!.original; value.nodes[0]!.row_hash = 'a'.repeat(64); expect(() => parseFullEvidenceGraph(value)).toThrow('校验');
  const immutable = fullGraphFixture(); immutable.known_at = fullGraphKnown; const node = immutable.nodes[0]!; node.kind = 'POSTING'; node.key = `POSTING:${node.id}`; immutable.root = node.key; immutable.requested_root_kind = 'POSTING'; immutable.nodes = [node]; immutable.displayed_node_count = 1; immutable.expected_node_count = 1; immutable.edges = []; immutable.expected_edge_count = 0; const row = immutable.inventory.find((item) => item.kind === 'POSTING')!; row.actual_owned_count = 1; row.known_count = 1; row.captured_count = 1;
  expect(parseFullEvidenceGraph(immutable).nodes[0]!.original).not.toBeNull();
});
test.each(['PARTIAL', 'LIMITED', 'BANK_UNKNOWN'] as const)('%s保真实分母及UNKNOWN，不能改成完成', (mode) => {
  const value = fullGraphFixture(mode); expect(parseFullEvidenceGraph(value).state).toBe('UNKNOWN'); value.state = 'REFERENCES_RESOLVED'; expect(() => parseFullEvidenceGraph(value)).toThrow('校验');
});
test('边必须在原字段中，重复/坏pointer/伪关系/缺端点不能生成导航', () => {
  for (const change of [
    (v: ReturnType<typeof fullGraphFixture>) => { v.nodes[1]!.original!.account_id = fullGraphUser; },
    (v: ReturnType<typeof fullGraphFixture>) => { v.edges.push(v.edges[0]!); v.expected_edge_count = 2; },
    (v: ReturnType<typeof fullGraphFixture>) => { v.edges[0]!.pointer = '/missing'; },
    (v: ReturnType<typeof fullGraphFixture>) => { v.edges[0]!.relation = 'FINANCIAL_SUCCESS'; },
    (v: ReturnType<typeof fullGraphFixture>) => { v.edges[0]!.to_key = `ACCOUNT:${fullGraphUser}`; },
  ]) { const value = fullGraphFixture(); change(value); expect(() => parseFullEvidenceGraph(value)).toThrow('校验'); }
});
test('FULL_GOAL_MODEL必须对应原证据source_type，不从普通Evidence猜完整属性', () => {
  const value = fullGraphFixture(); const first = value.nodes[0]!; first.kind = 'EVIDENCE'; first.key = `EVIDENCE:${first.id}`; first.source_classification = 'FULL_GOAL_MODEL_V1'; first.original!.source_type = 'FULL_GOAL_MODEL_V1'; value.root = first.key; value.requested_root_kind = 'FULL_GOAL_MODEL'; value.nodes = [first]; value.displayed_node_count = 1; value.expected_node_count = 1; value.edges = []; value.expected_edge_count = 0; const row = value.inventory.find((r) => r.kind === 'EVIDENCE')!; row.actual_owned_count = 1; row.known_count = 1; row.captured_count = 1;
  expect(parseFullEvidenceGraph(value).requested_root_kind).toBe('FULL_GOAL_MODEL'); first.source_classification = 'BANK_EVENT'; first.original!.source_type = 'BANK_EVENT'; expect(() => parseFullEvidenceGraph(value)).toThrow('校验');
});
test('完整原HTTP整数分字符保留；网络只GET、仅known_at与原根', async () => {
  const value = fullGraphFixture('HISTORICAL'); const raw = JSON.stringify(value); const capture: { url: URL; method?: string; body?: unknown }[] = []; vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local');
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => { capture.push({ url: new URL(String(input)), method: options?.method, body: options?.body }); return new Response(raw); }));
  const graph = await getFullEvidenceGraph('ACCOUNT', fullGraphAccount.toUpperCase(), '2026-10-04T20:00:00+08:00', fullGraphUser); expect(getOriginalFullGraphResponse(graph)).toBe(raw); expect(capture[0]!.url.pathname).toBe(`/api/v1/evidence/full-graph/ACCOUNT/${fullGraphAccount}`); expect(capture[0]!.url.searchParams.get('known_at')).toBe('2026-10-04T20:00:00+08:00'); expect([...capture[0]!.url.searchParams.keys()]).toEqual(['known_at']); expect(capture[0]).toMatchObject({ method: 'GET', body: undefined });
  const currentRaw = JSON.stringify(fullGraphFixture()).replace('"balance_cents":100007', '"balance_cents":9223372036854775807'); vi.stubGlobal('fetch', vi.fn(async () => new Response(currentRaw))); const current = await getFullEvidenceGraph('ACCOUNT', fullGraphAccount); expect(getOriginalFullGraphResponse(current)).toContain('"balance_cents":9223372036854775807');
});
test('类型/路径/缺时区在联网前拒绝；返回必须匹配原root/owner/known时点', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  expect(() => getFullEvidenceGraph('FAKE' as never, fullGraphAccount)).toThrow('校验'); expect(() => getFullEvidenceGraph('ACCOUNT', '../wrong')).toThrow('校验'); expect(() => getFullEvidenceGraph('ACCOUNT', fullGraphAccount, '2026-10-05T12:00:00')).toThrow('校验'); expect(fetch).not.toHaveBeenCalled();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(fullGraphFixture()))));
  await expect(getFullEvidenceGraph('ACCOUNT', fullGraphTransaction)).rejects.toThrow('校验'); await expect(getFullEvidenceGraph('ACCOUNT', fullGraphAccount, fullGraphKnown)).rejects.toThrow('校验'); await expect(getFullEvidenceGraph('ACCOUNT', fullGraphAccount, undefined, fullGraphTransaction)).rejects.toThrow('校验');
});
test('知识时点精确保留微秒，日历非法值和过去一微秒不能被毫秒取整冒当前', () => {
  const value = fullGraphFixture(); value.as_of = '2026-10-05T12:00:00.000002Z'; value.known_at = value.as_of;
  expect(parseFullEvidenceGraph(value, { kind: 'ACCOUNT', identity: fullGraphAccount, knownAt: '2026-10-05T20:00:00.000002+08:00' }).state).toBe('REFERENCES_RESOLVED');
  expect(() => parseFullEvidenceGraph(value, { kind: 'ACCOUNT', identity: fullGraphAccount, knownAt: '2026-10-05T12:00:00.000001Z' })).toThrow('校验');
  value.known_at = '2026-10-05T12:00:00.000001Z'; expect(() => parseFullEvidenceGraph(value)).toThrow('校验');
  expect(() => getFullEvidenceGraph('ACCOUNT', fullGraphAccount, '2026-02-30T12:00:00Z')).toThrow('校验');
});
