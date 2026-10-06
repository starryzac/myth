import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { assetConfirmFixture, assetExecuteFixture, assetLookupFixture, assetPortfolioFixture, assetPrepareFixture, assetResponseFixture, assetUser } from '../tests/full-asset-execution-fixture';
let operation: typeof import('./full-asset-execution-operation'), api: typeof import('../api/full-asset-execution');
const endpoint = 'http://unit-full-asset-operation.local', key = `bounded-funds-full-asset-execution-operation-v1:${endpoint}`;
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubEnv('VITE_API_BASE_URL', endpoint); vi.stubGlobal('crypto', webcrypto); operation = await import('./full-asset-execution-operation'); api = await import('../api/full-asset-execution'); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); sessionStorage.clear(); });
async function originalRead(intent: Awaited<ReturnType<typeof operation.prepareAssetIntent>>, found = true) { vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(assetLookupFixture(intent, found))))); return api.lookupAssetExecution(intent); }
test('全部原body/key/hash先保存；POST成功/4xx/失联都不清门，仅独立原GET可清', async () => {
  const intent = await operation.prepareAssetIntent('PREPARE', assetUser, assetPrepareFixture()); await operation.beginFullAssetExecutionOperation(intent); expect(sessionStorage.getItem(key)).toBe(JSON.stringify(intent)); expect(operation.getFullAssetExecutionOperation().busy).toBe(true);
  operation.endFullAssetExecutionAttempt(); await expect(operation.acceptFullAssetExecutionRead(intent, await api.parseAssetLookup(assetLookupFixture(intent), intent))).rejects.toThrow('独立原GET');
  expect((await operation.acceptFullAssetExecutionRead(intent, await originalRead(intent))).complete).toBe(true); expect(operation.getFullAssetExecutionOperation().pending).toBeNull(); expect(sessionStorage.getItem(key)).toBeNull();
});
test('NOT_FOUND非终局保持原请求，禁止换body/key；仅同完整原请求可由用户再开始', async () => {
  const intent = await operation.prepareAssetIntent('PREPARE', assetUser, assetPrepareFixture()); await operation.beginFullAssetExecutionOperation(intent); operation.endFullAssetExecutionAttempt(); expect((await operation.acceptFullAssetExecutionRead(intent, await originalRead(intent, false))).complete).toBe(false);
  const replacement = await operation.prepareAssetIntent('PREPARE', assetUser, { ...assetPrepareFixture(), idempotency_key: 'NEW_KEY' }); await expect(operation.beginFullAssetExecutionOperation(replacement)).rejects.toThrow('不能换'); expect(operation.getFullAssetExecutionOperation().pending).toEqual(intent);
  await operation.beginFullAssetExecutionOperation(intent); expect(operation.getFullAssetExecutionOperation().pending!.body_json).toBe(intent.body_json);
});
test('CONFIRM确切原receipt比对后才能清，busy时不能清', async () => {
  const intent = await operation.prepareAssetIntent('CONFIRM', assetUser, assetConfirmFixture(), assetPortfolioFixture()); await operation.beginFullAssetExecutionOperation(intent); const lookup = await originalRead(intent); await expect(operation.acceptFullAssetExecutionRead(intent, lookup)).rejects.toThrow(); operation.endFullAssetExecutionAttempt(); expect((await operation.acceptFullAssetExecutionRead(intent, lookup)).complete).toBe(true);
});
test('EXECUTE固定原批UNKNOWN保门/阻后批；只GET指定原批SETTLED receipt可清而不执行后批', async () => {
  const portfolio = assetPortfolioFixture(), intent = await operation.prepareAssetIntent('EXECUTE', assetUser, assetExecuteFixture(), portfolio); await operation.beginFullAssetExecutionOperation(intent); operation.endFullAssetExecutionAttempt();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(assetResponseFixture('UNKNOWN'))))); expect((await operation.acceptFullAssetExecutionRead(intent, await api.getAssetExecution(portfolio.portfolio_id, assetUser, portfolio))).complete).toBe(false);
  await expect(operation.beginFullAssetExecutionOperation(await operation.prepareAssetIntent('EXECUTE', assetUser, assetExecuteFixture(2), portfolio))).rejects.toThrow('不能换');
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(assetResponseFixture('SETTLED'))))); const read = await api.getAssetExecution(portfolio.portfolio_id, assetUser, portfolio), result = await operation.acceptFullAssetExecutionRead(intent, read); expect(result.complete).toBe(true); expect(result.original!.batches[1]!.original_action!.receipt).toBeNull(); expect(vi.mocked(fetch).mock.calls.every((call) => call[1]!.method === 'GET')).toBe(true);
});
test('刷新只恢复冻结原请求，无HTTP/自动POST；恶意JSON或hash保原件并锁新写', async () => {
  const intent = await operation.prepareAssetIntent('EXECUTE', assetUser, assetExecuteFixture(), assetPortfolioFixture()); sessionStorage.setItem(key, JSON.stringify(intent)); vi.stubGlobal('fetch', vi.fn()); expect((await operation.recoverFullAssetExecutionOperation()).pending).toEqual(intent); expect(Object.isFrozen(operation.getFullAssetExecutionOperation().pending!.reviewed_portfolio!.batches)).toBe(true); expect(fetch).not.toHaveBeenCalled();
});
test.each(['{BROKEN', JSON.stringify({ protocol: 'full-asset-browser-command-v1', request_hash: '0'.repeat(64) })])('坏持久原件不得丢弃或冒成功 %s', async (raw) => { sessionStorage.setItem(key, raw); const intent = await operation.prepareAssetIntent('PREPARE', assetUser, assetPrepareFixture()); expect((await operation.recoverFullAssetExecutionOperation()).storage_error).not.toBeNull(); await expect(operation.beginFullAssetExecutionOperation(intent)).rejects.toThrow(); expect(sessionStorage.getItem(key)).toBe(raw); });
test('存储set失败不POST；原GET后remove失败仍保门', async () => {
  const intent = await operation.prepareAssetIntent('PREPARE', assetUser, assetPrepareFixture()); vi.stubGlobal('fetch', vi.fn()); const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('TOOL_ONLY_DENIED'); }); await expect(operation.beginFullAssetExecutionOperation(intent)).rejects.toThrow('未发送'); expect(fetch).not.toHaveBeenCalled(); spy.mockRestore();
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('TOOL_ONLY_DENIED'); }); await expect(operation.acceptFullAssetExecutionRead(intent, await originalRead(intent))).rejects.toThrow('无法删除'); expect(operation.getFullAssetExecutionOperation().pending).toEqual(intent);
});
test.each(['demo', 'full-policy-storage', 'full-goal-storage', 'onboarding', 'question-storage', 'spending-storage', 'intervention-storage', 'release-storage', 'cash-storage', 'writeflight', 'external'])('他族%s阻新写，自身原GET不受external门阻挡', async (family) => {
  const intent = await operation.prepareAssetIntent('PREPARE', assetUser, assetPrepareFixture());
  const storage: Record<string, string> = { 'full-policy-storage': 'bounded-funds-full-policy-operation-v1', 'full-goal-storage': 'bounded-funds-full-goal-operation-v1', 'question-storage': 'bounded-funds-one-question-operation-v1', 'spending-storage': 'bounded-funds-spending-category-operation-v1', 'intervention-storage': 'bounded-funds-intervention-operation-v1', 'release-storage': 'bounded-funds-goal-release-authorization-operation-v1', 'cash-storage': 'bounded-funds-goal-cash-release-operation-v1' };
  if (storage[family]) sessionStorage.setItem(`${storage[family]}:${endpoint}`, '{BROKEN');
  if (family === 'demo') sessionStorage.setItem(`bounded-funds-demo-operation-v1:${endpoint}`, JSON.stringify({ kind: 'reset', epoch_id: null, reset_key: 'TOOL_ONLY' }));
  if (family === 'onboarding') { const o = await import('./onboarding-draft'), f = await import('../tests/onboarding-fixture'); o.beginOnboardingCommand(o.prepareOnboardingIntent('EMERGENCY', f.onboardingBinding(), f.emergencyText)); o.endOnboardingAttempt(); }
  if (family === 'writeflight') (await import('./write-flight')).beginWriteFlight();
  await expect(operation.beginFullAssetExecutionOperation(intent, family === 'external')).rejects.toThrow(); expect(operation.getFullAssetExecutionOperation().pending).toBeNull();
});
