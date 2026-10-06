import { webcrypto } from 'node:crypto';
import { beforeEach, expect, test, vi } from 'vitest';
import { categoryBodyFixture, categoryResultFixture, categoryReviewFixture, spendingIntentFixture } from '../tests/spending-evidence-fixture';
let operation: typeof import('./spending-evidence-operation');
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubEnv('VITE_API_BASE_URL', 'http://spending-store-unit.local'); vi.stubGlobal('crypto', webcrypto); operation = await import('./spending-evidence-operation'); });
test('beforePOST原body/key/hash持久化、任意HTTP结束保pending；未找到与换键不清', async () => {
  const intent = await operation.prepareSpendingIntent(categoryReviewFixture(), categoryBodyFixture()); await operation.beginSpendingEvidenceOperation(intent); expect(JSON.parse(sessionStorage.getItem(sessionStorage.key(0)!)!)).toEqual(intent); operation.endSpendingEvidenceAttempt();
  await expect(operation.clearSpendingEvidenceAfterLookup(intent, categoryResultFixture(intent, false))).rejects.toThrow('不是终局'); await expect(operation.beginSpendingEvidenceOperation(spendingIntentFixture('NEW_KEY'))).rejects.toThrow('不能换键'); expect(operation.getSpendingEvidenceOperation().pending).toEqual(intent);
});
test('单独原键GET exact body/receipt/hash解除后只保存读取定位，刷新不POST', async () => {
  vi.stubGlobal('fetch', vi.fn()); const intent = spendingIntentFixture(); await operation.beginSpendingEvidenceOperation(intent); operation.endSpendingEvidenceAttempt();
  await operation.clearSpendingEvidenceAfterLookup(intent, categoryResultFixture(intent)); expect(operation.getSpendingEvidenceOperation().pending).toBeNull(); expect(Object.keys(operation.getSessionTransactionReadonlyReference()!).sort()).toEqual(['epoch_id', 'protocol', 'transaction_id', 'user_id']); expect(fetch).not.toHaveBeenCalled();
  vi.resetModules(); const restored = await import('./spending-evidence-operation'); expect(restored.getSessionTransactionReadonlyReference()?.transaction_id).toBe(intent.transaction_id); expect(restored.getSpendingEvidenceOperation().pending).toBeNull();
});
test('响应丢失刷新恢复原body，不沿已确认交易/旧review hash确认；pending保持不可变', async () => {
  const intent = spendingIntentFixture(); sessionStorage.setItem('bounded-funds-spending-category-operation-v1:http://spending-store-unit.local', JSON.stringify(intent)); expect(operation.recoverSpendingEvidenceOperation().pending).toEqual(intent); expect(Object.isFrozen(operation.getSpendingEvidenceOperation().pending!.body)).toBe(true);
  await expect(operation.prepareSpendingIntent(categoryReviewFixture(true), categoryBodyFixture())).rejects.toThrow(); const bad = categoryReviewFixture(); bad.transaction.category = 'CHANGED'; await expect(operation.prepareSpendingIntent(bad, categoryBodyFixture())).rejects.toThrow('hash');
});
test('跨族blocked、坏存储和无法保存均failclosed且不发送', async () => {
  const intent = spendingIntentFixture(); vi.stubGlobal('fetch', vi.fn()); await expect(operation.beginSpendingEvidenceOperation(intent, true)).rejects.toThrow(); const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('TOOL_ONLY_DENIED'); }); await expect(operation.beginSpendingEvidenceOperation(intent)).rejects.toThrow('无法保存'); expect(operation.getSpendingEvidenceOperation().storage_error).not.toBeNull(); expect(operation.getSpendingEvidenceOperation().busy).toBe(false); expect(fetch).not.toHaveBeenCalled(); spy.mockRestore();
});
test('坏原存储不删除、不转授权；回执读取/删除失败继续锁门', async () => {
  sessionStorage.setItem('bounded-funds-spending-category-operation-v1:http://spending-store-unit.local', '{BAD'); expect(operation.recoverSpendingEvidenceOperation().storage_error).not.toBeNull(); expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe('{BAD');
  vi.resetModules(); sessionStorage.clear(); operation = await import('./spending-evidence-operation'); const intent = spendingIntentFixture(); await operation.beginSpendingEvidenceOperation(intent); operation.endSpendingEvidenceAttempt(); const raw = sessionStorage.getItem(sessionStorage.key(0)!); const bad = categoryResultFixture(intent); bad.original_receipt!.bank_evidence_id = intent.user_id; await expect(operation.clearSpendingEvidenceAfterLookup(intent, bad)).rejects.toThrow(); expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe(raw);
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('TOOL_ONLY_DENIED'); }); await expect(operation.clearSpendingEvidenceAfterLookup(intent, categoryResultFixture(intent))).rejects.toThrow('不能删除'); expect(operation.getSpendingEvidenceOperation().pending).toEqual(intent);
});
