import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { dynamicActionFixture, dynamicPrepareFixture, dynamicPreviewFixture, dynamicLookupFixture, dynamicExecutionUser, dynamicFixtureHash } from '../tests/full-dynamic-goal-execution-fixture';
import { parseDynamicPrepare, parseDynamicPreview, parseDynamicAction, parseDynamicLookup, previewDynamicGoal, postDynamicGoal, lookupDynamicGoal, getOriginalDynamicExecutionResponse, isFreshDynamicExecutionRead, dynamicCanonicalJson } from './full-dynamic-goal-execution';
import { prepareDynamicGoalIntent } from '../features/full-dynamic-goal-operation';
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('身份-only完整六字段，拒绝客户端金额、当前clock、Proof和授权', () => {
  expect(parseDynamicPrepare(dynamicPrepareFixture())).toEqual(dynamicPrepareFixture());
  for (const field of ['amount_cents', 'now', 'proof', 'accepted', 'user_id']) expect(() => parseDynamicPrepare({ ...dynamicPrepareFixture(), [field]: 1 })).toThrow();
  for (const idempotency_key of ['', ' ', 'x'.repeat(121)]) expect(() => parseDynamicPrepare({ ...dynamicPrepareFixture(), idempotency_key })).toThrow();
});
test('真实compact proof原hash与原模型/epoch身份一致；NULL保持UNKNOWN', async () => {
  const ready = dynamicPreviewFixture(), unknown = dynamicPreviewFixture(undefined, 'UNKNOWN');
  expect((await parseDynamicPreview(ready, ready.request, dynamicExecutionUser)).proof.dynamic_cap_cents).toBe(30006);
  expect((await parseDynamicPreview(unknown, unknown.request, dynamicExecutionUser)).proof.dynamic_cap_cents).toBeNull();
});
test.each(['epoch', 'modelHash', 'authority', 'digest', 'unsafeMoney', 'futureIncome', 'outOfRange'] as const)('拒绝预览%s篡改，不将字符串或成功flag视为证明', async (kind) => {
  const value = dynamicPreviewFixture();
  if (kind === 'epoch') value.proof.epoch_id = '92000000-0000-4000-8000-000000000099';
  if (kind === 'modelHash') value.proof.model_evidence_hash = 'f'.repeat(64);
  if (kind === 'authority') Object.assign(value.proof, { bank_authority: true });
  if (kind === 'digest') value.proof.proof_hash = '0'.repeat(64);
  if (kind === 'unsafeMoney') value.proof.dynamic_cap_cents = Number.MAX_SAFE_INTEGER + 1;
  if (kind === 'futureIncome') Object.assign(value.proof.reserve!, { future_income_included_cents: 1 });
  if (kind === 'outOfRange') value.proof.remaining_max_cents = 1;
  if (!['digest', 'unsafeMoney'].includes(kind)) { const { proof_hash: ignored, ...body } = value.proof; void ignored; value.proof.proof_hash = dynamicFixtureHash(body); }
  await expect(parseDynamicPreview(value, value.request, dynamicExecutionUser)).rejects.toThrow();
});
test('固定原Action与服务器effect hash、完整来源金额对齐；银行SETTLED且无回执仍UNKNOWN', async () => {
  const action = dynamicActionFixture('UNKNOWN'); expect((await parseDynamicAction(action, dynamicPrepareFixture(), dynamicExecutionUser)).receipt).toBeNull();
  const changed = structuredClone(action); changed.effect.amount_cents += 1; await expect(parseDynamicAction(changed, dynamicPrepareFixture(), dynamicExecutionUser)).rejects.toThrow();
  const substituted = structuredClone(action); substituted.action_id = '92000000-0000-4000-8000-000000000098'; await expect(parseDynamicAction(substituted, dynamicPrepareFixture(), dynamicExecutionUser, action)).rejects.toThrow();
});
test('原服务receipt必须固定Action/金额/原银行操作，不拿另一成功动作替代', async () => {
  const ready = dynamicActionFixture('SETTLED'); await expect(parseDynamicAction(ready, dynamicPrepareFixture(), dynamicExecutionUser)).resolves.toBe(ready);
  ready.receipt!.executed_cents += 1; await expect(parseDynamicAction(ready, dynamicPrepareFixture(), dynamicExecutionUser)).rejects.toThrow();
});
test('lookup全原六字段+双request hash+marker/proof+固定Action，NOT_FOUND非终局', async () => {
  const intent = await prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture());
  expect((await parseDynamicLookup(dynamicLookupFixture(intent, 'NOT_FOUND'), intent)).not_found_is_final).toBe(false);
  expect((await parseDynamicLookup(dynamicLookupFixture(intent), intent)).action?.action_id).toBe(dynamicActionFixture().action_id);
  const v = dynamicLookupFixture(intent); v.original_request = { ...v.original_request!, idempotency_key: 'ANOTHER_KEY' }; await expect(parseDynamicLookup(v, intent)).rejects.toThrow();
});
test('AUTHORIZED缺原Evidence仍MISSING；原确认不能被body/hash/latest状态代替', async () => {
  const intent = await prepareDynamicGoalIntent('CONFIRM', dynamicExecutionUser, dynamicPrepareFixture(), dynamicActionFixture());
  expect((await parseDynamicLookup(dynamicLookupFixture(intent, 'MISSING_CONFIRM'), intent)).confirmation).toBeNull();
  const v = dynamicLookupFixture(intent, 'CONFIRMED'); v.confirmation!.effect_hash = 'e'.repeat(64); await expect(parseDynamicLookup(v, intent)).rejects.toThrow();
});
test('全Action.request原hash拒绝marker被改；前端不重造审计/source权限引擎', async () => {
  const intent = await prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture()), value = dynamicLookupFixture(intent);
  value.server_request_hash = 'e'.repeat(64); await expect(parseDynamicLookup(value, intent)).rejects.toThrow();
});
test('原确认过期可以历史回读，falsecurrentauthority不可升级；SEALED不冒当前', async () => {
  const intent = await prepareDynamicGoalIntent('CONFIRM', dynamicExecutionUser, dynamicPrepareFixture(), dynamicActionFixture()), value = dynamicLookupFixture(intent, 'CONFIRMED');
  value.epoch_state = 'SEALED'; value.historical = true; value.action!.as_of = '2026-10-06T12:00:00Z'; expect((await parseDynamicLookup(value, intent)).historical).toBe(true);
  Object.assign(value, { confirmation_is_current_authority: true }); await expect(parseDynamicLookup(value, intent)).rejects.toThrow();
});
test('actual路径/body一次发送，不输入金额；独立GET原键/无query，保原响应bytes', async () => {
  const intent = await prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture()); const raw = ` \n${JSON.stringify(dynamicLookupFixture(intent))}\n`;
  vi.stubEnv('VITE_API_BASE_URL', 'http://unit-dynamic.local'); vi.stubGlobal('fetch', vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => new Response(init?.method === 'GET' ? raw : JSON.stringify(dynamicActionFixture()))));
  await postDynamicGoal(intent); const value = await lookupDynamicGoal(intent); expect(isFreshDynamicExecutionRead(value)).toBe(true); expect(getOriginalDynamicExecutionResponse(value)).toBe(raw);
  const calls = vi.mocked(fetch).mock.calls; expect(calls).toHaveLength(2); expect(calls[0]![1]!.body).toBe(intent.body_json); expect(String(calls[1]![0])).toBe('http://unit-dynamic.local/api/v1/dynamic-goal-actions/by-key/TOOL_ONLY_DYNAMIC_001'); expect(calls[1]![1]!.body).toBeUndefined();
});
test('只读preview真实POST且同六字段；它没有Action/receipt或执行请求', async () => {
  const body = dynamicPrepareFixture(); vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(dynamicPreviewFixture(body))))); await previewDynamicGoal(body, dynamicExecutionUser); expect(vi.mocked(fetch).mock.calls).toHaveLength(1); expect(String(vi.mocked(fetch).mock.calls[0]![0])).toBe('/api/v1/dynamic-goal-actions/preview'); expect(dynamicCanonicalJson(JSON.parse(vi.mocked(fetch).mock.calls[0]![1]!.body as string))).toBe(dynamicCanonicalJson(body));
});
