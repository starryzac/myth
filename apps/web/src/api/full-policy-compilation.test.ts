import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { compileFullPolicyCandidate, getFullCompilerGrammar, getOriginalFullCompilationResponse, parseFullCompilation, parseFullCompilerRequest, parseFullGrammar, requireCompilerJson } from './full-policy-compilation';
import { compilerFixture, compilerGrammarFixture, compilerUser } from '../tests/full-policy-compiler-fixture';
beforeEach(() => vi.stubGlobal('crypto', webcrypto));
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('原文本/服务器user/纯候选flags与Unicode码点来源hash全部匹配才读取', async () => {
  const text = '😀保留3000元应急金'; const data = await compilerFixture(text);
  expect((await parseFullCompilation(data, { text, engine: 'rules' }, compilerUser)).compilation.source_fragments[0]!.start).toBe(3);
  await expect(parseFullCompilation(data, { text: text + '!', engine: 'rules' })).rejects.toThrow();
  await expect(parseFullCompilation(data, { text, engine: 'rules' }, '60000000-0000-4000-8000-000000000002')).rejects.toThrow();
  data.compilation.source_fragments[0]!.start++; await expect(parseFullCompilation(data, { text, engine: 'rules' })).rejects.toThrow();
});
test.each(['bank_authority', 'grants_authority', 'confirmation_record_created'] as const)('外层%s伪授权拒绝', async (field) => { const data = await compilerFixture(); await expect(parseFullCompilation({ ...data, [field]: true }, { text: '保留3000元应急金', engine: 'rules' })).rejects.toThrow(); });
test('unknown不带配置、缺hash或unsafe/bool金额不能变可用候选', async () => {
  const data = await compilerFixture(); await expect(parseFullCompilation({ ...data, compilation: { ...data.compilation, status: 'UNKNOWN' } }, { text: '保留3000元应急金', engine: 'rules' })).rejects.toThrow();
  for (const value of [true, 1.2, Number.MAX_SAFE_INTEGER + 1]) await expect(parseFullCompilation({ ...data, compilation: { ...data.compilation, configuration: { ...data.compilation.configuration, amount_cents: value } } }, { text: '保留3000元应急金', engine: 'rules' })).rejects.toThrow();
  await expect(parseFullCompilation({ ...data, compilation: { ...data.compilation, configuration_hash: null } }, { text: '保留3000元应急金', engine: 'rules' })).rejects.toThrow();
});
test('片段越界/错hash、非法日期、伪已验引用及请求extra拒绝', async () => {
  const data = await compilerFixture(); const fragment = data.compilation.source_fragments[0]!;
  for (const change of [{ start: -1 }, { end: 4001 }, { original_fragment_sha256: 'b'.repeat(64) }]) await expect(parseFullCompilation({ ...data, compilation: { ...data.compilation, source_fragments: [{ ...fragment, ...change }] } }, { text: '保留3000元应急金', engine: 'rules' })).rejects.toThrow();
  await expect(parseFullCompilation({ ...data, reference_date: '2026-02-30' }, { text: '保留3000元应急金', engine: 'rules' })).rejects.toThrow();
  await expect(parseFullCompilation({ ...data, compilation: { ...data.compilation, reference_validation: 'VERIFIED' } }, { text: '保留3000元应急金', engine: 'rules' })).rejects.toThrow();
  for (const field of ['role', 'bank_facts', 'time', 'amount_cents', 'accepted', 'provider']) expect(() => parseFullCompilerRequest({ text: '保留3000元应急金', [field]: true })).toThrow();
  expect(() => parseFullCompilerRequest({ text: '原句', engine: ['rules'] })).toThrow();
});
test('原十二句式库存缺模板/伪银行授权拒绝，分位浮点可用且无无限/破损Unicode', () => {
  expect(Object.keys(parseFullGrammar(compilerGrammarFixture()).examples)).toHaveLength(12); const grammar = compilerGrammarFixture(); delete (grammar.examples as Record<string, string>).RecoveryPolicy; expect(() => parseFullGrammar(grammar)).toThrow(); expect(() => parseFullGrammar({ ...compilerGrammarFixture(), bank_authority: true })).toThrow();
  expect(() => requireCompilerJson({ quantile: .95, amount_cents: 301 })).not.toThrow(); expect(() => requireCompilerJson({ quantile: Infinity })).toThrow(); expect(() => requireCompilerJson('\uD800')).toThrow();
});
test('真实URL/原body只preview一次，原JSON保留且没有confirm/activate调用', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://unit-full-compiler.local'); const data = await compilerFixture(); const raw = JSON.stringify(data); const calls: { path: string; method: string; body?: BodyInit | null }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => { calls.push({ path: new URL(String(input)).pathname, method: init?.method ?? 'GET', body: init?.body }); return new Response(init?.method === 'POST' ? raw : JSON.stringify(compilerGrammarFixture())); }));
  await getFullCompilerGrammar(); const result = await compileFullPolicyCandidate({ text: '保留3000元应急金', engine: 'rules' }, compilerUser);
  expect(calls).toEqual([{ path: '/api/v1/full-policy-compilations/grammar', method: 'GET', body: undefined }, { path: '/api/v1/full-policy-compilations/preview', method: 'POST', body: JSON.stringify({ text: '保留3000元应急金', engine: 'rules' }) }]); expect(getOriginalFullCompilationResponse(result)).toBe(raw);
});
