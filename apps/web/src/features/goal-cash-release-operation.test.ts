import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { cashActionFixture, cashIntentFixture, cashLookupFixture } from '../tests/goal-cash-release-fixture';
import { readCashIntent } from '../api/goal-cash-releases';
let operation: typeof import('./goal-cash-release-operation');
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); operation = await import('./goal-cash-release-operation'); });
afterEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
test('prepare完整原body/key/hash写前持久，successPOST/NOT_FOUND不能清门或换键', async () => {
  const intent = await cashIntentFixture(); await operation.beginCashPrepare(intent); const original = await cashActionFixture(intent); await operation.retainCashPostResponse(intent, original); operation.endCashAttempt();
  expect(operation.getGoalCashReleaseOperation().pending!.intent).toEqual(intent); await expect(operation.beginCashPrepare(await cashIntentFixture('NEW_KEY'))).rejects.toThrow();
  await expect(operation.acceptCashRead(intent, cashLookupFixture(intent, await cashActionFixture(intent, 'SUCCEEDED')))).rejects.toThrow('独立GET');
  const reader = await import('../api/goal-cash-releases'); vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(original)))); const read = await reader.readCashIntent(intent, original); expect(await operation.acceptCashRead(intent, read)).toBe(false); expect(operation.getGoalCashReleaseOperation().pending).not.toBeNull();
});
test('reload仅恢复原身份，不fetch/POST；坏bytes与不能保存均fail closed', async () => {
  const intent = await cashIntentFixture(); await operation.beginCashPrepare(intent); operation.endCashAttempt(); vi.stubGlobal('fetch', vi.fn()); vi.resetModules(); operation = await import('./goal-cash-release-operation'); await operation.recoverGoalCashReleaseOperation(); expect(operation.getGoalCashReleaseOperation().pending!.intent.body_json).toBe(intent.body_json); expect(fetch).not.toHaveBeenCalled();
  vi.resetModules(); sessionStorage.setItem('bounded-funds-goal-cash-release-operation-v1:same-origin', '{BROKEN'); operation = await import('./goal-cash-release-operation'); await operation.recoverGoalCashReleaseOperation(); expect(operation.getGoalCashReleaseOperation().storage_error).toBeTruthy(); await expect(operation.beginCashPrepare(intent)).rejects.toThrow();
});
test('执行保存原effect/body后POST；明确GET完整终态才能清门；原REQUEST HASH不能变clienthash', async () => {
  const intent = await cashIntentFixture(), original = await cashActionFixture(intent); await operation.beginCashPrepare(intent); await operation.retainCashPostResponse(intent, original); operation.endCashAttempt();
  const body = { accepted: true as const, reviewed_effect_hash: original.original_command.effect_hash, expected_epoch_id: intent.body.expected_epoch_id }; const saved = await operation.beginCashExecute(body); expect(saved.execute_json).toBe(JSON.stringify(body)); operation.endCashAttempt();
  const paid = await cashActionFixture(intent, 'SUCCEEDED'); await operation.retainCashPostResponse(intent, paid); expect(operation.getGoalCashReleaseOperation().pending).not.toBeNull();
  const reader = await import('../api/goal-cash-releases'); vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(paid)))); const result = await reader.readCashIntent(intent, paid); expect(await operation.acceptCashRead(intent, result)).toBe(true); expect(operation.getGoalCashReleaseOperation().pending).toBeNull();
});
test('他族门/原写在途/错效果拒绝，自身原GET不加写门', async () => {
  const intent = await cashIntentFixture(); await expect(operation.beginCashPrepare(intent, true)).rejects.toThrow(); const flight = await import('./write-flight'); flight.beginWriteFlight(); await expect(operation.beginCashPrepare(intent)).rejects.toThrow(); flight.endWriteFlight();
  await operation.beginCashPrepare(intent); const original = await cashActionFixture(intent); await operation.retainCashPostResponse(intent, original); operation.endCashAttempt(); await expect(operation.beginCashExecute({ accepted: true, reviewed_effect_hash: '0'.repeat(64), expected_epoch_id: intent.body.expected_epoch_id })).rejects.toThrow();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(original)))); const reader = await import('../api/goal-cash-releases'); await reader.readCashIntent(intent, original); expect(flight.isWriteInFlight()).toBe(false);
});
// Import is only a contract reference; all live reads above use each restored module's provenance set.
void readCashIntent;
test('旧POST或reload的已结算标签不能绕他族门；独立原GET之后才明确恢复同效果', async () => {
  const intent = await cashIntentFixture(), unknown = await cashActionFixture(intent, 'UNKNOWN');
  await operation.beginCashPrepare(intent); await operation.retainCashPostResponse(intent, unknown); operation.endCashAttempt();
  const body = { accepted: true as const, reviewed_effect_hash: unknown.original_command.effect_hash, expected_epoch_id: intent.body.expected_epoch_id };
  await expect(operation.beginCashExecute(body, true)).rejects.toThrow();
  vi.resetModules(); operation = await import('./goal-cash-release-operation'); await operation.recoverGoalCashReleaseOperation();
  await expect(operation.beginCashExecute(body, true)).rejects.toThrow();
  const reader = await import('../api/goal-cash-releases'); vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(unknown))));
  const result = await reader.readCashIntent(intent, unknown); expect(await operation.acceptCashRead(intent, result)).toBe(false);
  expect((await operation.beginCashExecute(body, true)).execute_body).toEqual(body); operation.endCashAttempt();
});
