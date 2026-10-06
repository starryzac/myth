import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { dynamicActionFixture, dynamicPrepareFixture, dynamicLookupFixture, dynamicExecutionUser } from '../tests/full-dynamic-goal-execution-fixture';
let operation: typeof import('./full-dynamic-goal-operation'), api: typeof import('../api/full-dynamic-goal-execution');
const endpoint = 'http://unit-dynamic-operation.local', key = `bounded-funds-full-dynamic-goal-operation-v1:${endpoint}`;
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubEnv('VITE_API_BASE_URL', endpoint); vi.stubGlobal('crypto', webcrypto); operation = await import('./full-dynamic-goal-operation'); api = await import('../api/full-dynamic-goal-execution'); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); sessionStorage.clear(); });
async function originalRead(intent: Awaited<ReturnType<typeof operation.prepareDynamicGoalIntent>>, stage: Parameters<typeof dynamicLookupFixture>[1] = 'PREPARED') { vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(dynamicLookupFixture(intent, stage))))); return api.lookupDynamicGoal(intent); }
test('POST前存完整原body/key/hash；POST成功及4xx/失联均不清，只有独立原GET', async () => {
  const intent = await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture()); await operation.beginDynamicGoalOperation(intent); expect(sessionStorage.getItem(key)).toBe(JSON.stringify(intent)); operation.endDynamicGoalAttempt();
  await expect(operation.acceptDynamicGoalRead(intent, await api.parseDynamicLookup(dynamicLookupFixture(intent), intent))).rejects.toThrow(); expect(operation.getDynamicGoalOperation().pending).toEqual(intent);
  expect((await operation.acceptDynamicGoalRead(intent, await originalRead(intent))).complete).toBe(true); expect(sessionStorage.getItem(key)).toBeNull(); expect(operation.getDynamicGoalOperation().workspace!.action).not.toBeNull();
});
test('NOT_FOUND非终局保原件，不允许换键/目标/金额；仅手动同完整原body重试', async () => {
  const intent = await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture()); await operation.beginDynamicGoalOperation(intent); operation.endDynamicGoalAttempt(); expect((await operation.acceptDynamicGoalRead(intent, await originalRead(intent, 'NOT_FOUND'))).complete).toBe(false);
  await expect(operation.beginDynamicGoalOperation(await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, { ...dynamicPrepareFixture(), idempotency_key: 'NEW' }))).rejects.toThrow('不能换'); await operation.beginDynamicGoalOperation(intent); expect(operation.getDynamicGoalOperation().pending).toEqual(intent);
});
test('AUTHORIZED但原确认MISSING不能清确认；确切原Evidence完整匹配后才清', async () => {
  const intent = await operation.prepareDynamicGoalIntent('CONFIRM', dynamicExecutionUser, dynamicPrepareFixture(), dynamicActionFixture()); await operation.beginDynamicGoalOperation(intent); operation.endDynamicGoalAttempt(); expect((await operation.acceptDynamicGoalRead(intent, await originalRead(intent, 'MISSING_CONFIRM'))).complete).toBe(false);
  expect((await operation.acceptDynamicGoalRead(intent, await originalRead(intent, 'CONFIRMED'))).complete).toBe(true);
});
test('EXECUTE银行SETTLED但应用无receipt仍UNKNOWN；只能原Action原receipt清门', async () => {
  const intent = await operation.prepareDynamicGoalIntent('EXECUTE', dynamicExecutionUser, dynamicPrepareFixture(), dynamicActionFixture('CONFIRMED')); await operation.beginDynamicGoalOperation(intent); operation.endDynamicGoalAttempt(); const unknown = await originalRead(intent, 'UNKNOWN'); expect((await operation.acceptDynamicGoalRead(intent, unknown)).complete).toBe(false);
  expect((await operation.acceptDynamicGoalRead(intent, await originalRead(intent, 'SETTLED'))).complete).toBe(true); expect(operation.getDynamicGoalOperation().pending).toBeNull();
});
test('busy与另一请求GET不能释放；源版本变化也保同原件', async () => {
  const intent = await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture()); await operation.beginDynamicGoalOperation(intent); const value = await originalRead(intent); await expect(operation.acceptDynamicGoalRead(intent, value)).rejects.toThrow(); operation.endDynamicGoalAttempt(); const foreign = await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, { ...dynamicPrepareFixture(), idempotency_key: 'FOREIGN' }); await expect(operation.acceptDynamicGoalRead(foreign, value)).rejects.toThrow();
});
test('刷新严格恢复原请求，无fetch或自动POST，坏hash不丢原件', async () => {
  const intent = await operation.prepareDynamicGoalIntent('EXECUTE', dynamicExecutionUser, dynamicPrepareFixture(), dynamicActionFixture('CONFIRMED')); sessionStorage.setItem(key, JSON.stringify(intent)); vi.stubGlobal('fetch', vi.fn()); const value = await operation.recoverDynamicGoalOperation(); expect(value.pending).toEqual(intent); expect(Object.isFrozen(value.pending!.action!.effect)).toBe(true); expect(fetch).not.toHaveBeenCalled();
});
test.each(['{BROKEN', JSON.stringify({ protocol: 'full-dynamic-goal-browser-v1' })])('坏存储锁门且原bytes保留 %s', async (raw) => { sessionStorage.setItem(key, raw); const intent = await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture()); expect((await operation.recoverDynamicGoalOperation()).storage_error).not.toBeNull(); await expect(operation.beginDynamicGoalOperation(intent)).rejects.toThrow(); expect(sessionStorage.getItem(key)).toBe(raw); });
test('存储set失败不发送；原GET后remove失败仍保门', async () => {
  const intent = await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture()); vi.stubGlobal('fetch', vi.fn()); const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('TOOL_ONLY_DENIED'); }); await expect(operation.beginDynamicGoalOperation(intent)).rejects.toThrow('未发送'); expect(fetch).not.toHaveBeenCalled(); spy.mockRestore(); vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('TOOL_ONLY_DENIED'); }); await expect(operation.acceptDynamicGoalRead(intent, await originalRead(intent))).rejects.toThrow('无法删除'); expect(operation.getDynamicGoalOperation().pending).toEqual(intent);
});
test.each(['external', 'demo', 'asset_storage', 'payment_storage', 'writeflight'] as const)('跨族%s阻新写，不阻独立原GET', async (family) => {
  const intent = await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture());
  if (family === 'demo') (await import('./demo-operation')).beginDemoOperation({ kind: 'reset', epoch_id: null, reset_key: 'TOOL_ONLY' });
  if (family === 'asset_storage') sessionStorage.setItem(`bounded-funds-full-asset-execution-operation-v1:${endpoint}`, '{BAD');
  if (family === 'payment_storage') sessionStorage.setItem(`bounded-funds-fixed-payment-operation-v1:${endpoint}`, '{BAD');
  if (family === 'writeflight') (await import('./write-flight')).beginWriteFlight();
  await expect(operation.beginDynamicGoalOperation(intent, family === 'external')).rejects.toThrow(); const value = await originalRead(intent); expect(value.status).toBe('RECORDED');
  if (family === 'writeflight') (await import('./write-flight')).endWriteFlight();
});
test('已准备原工作区持久保留，禁止另一键替换未终局固定Action，原GET刷新后可明确确认', async () => {
  const intent = await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture()); await operation.beginDynamicGoalOperation(intent); operation.endDynamicGoalAttempt(); await operation.acceptDynamicGoalRead(intent, await originalRead(intent)); expect(sessionStorage.getItem(`${key}:workspace`)).not.toBeNull();
  await expect(operation.beginDynamicGoalOperation(await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, { ...dynamicPrepareFixture(), idempotency_key: 'NEW' }))).rejects.toThrow('工作区');
  const workspaceIntent = await operation.dynamicGoalWorkspaceIntent(); const read = await operation.acceptDynamicGoalWorkspaceRead(workspaceIntent, await originalRead(workspaceIntent)); expect(read.action?.action_id).toBe(dynamicActionFixture().action_id);
  await operation.beginDynamicGoalOperation(await operation.prepareDynamicGoalIntent('CONFIRM', dynamicExecutionUser, dynamicPrepareFixture(), read.action!)); expect(operation.getDynamicGoalOperation().pending?.kind).toBe('CONFIRM');
});
test('刷新保留已核对工作区但不自动GET/POST，不把缓存当fresh授权', async () => {
  const intent = await operation.prepareDynamicGoalIntent('PREPARE', dynamicExecutionUser, dynamicPrepareFixture()); sessionStorage.setItem(`${key}:workspace`, JSON.stringify(dynamicLookupFixture(intent))); vi.stubGlobal('fetch', vi.fn()); const value = await operation.recoverDynamicGoalOperation(); expect(value.workspace?.action?.action_id).toBe(dynamicActionFixture().action_id); expect(api.isFreshDynamicExecutionRead(value.workspace!)).toBe(false); expect(fetch).not.toHaveBeenCalled();
});
