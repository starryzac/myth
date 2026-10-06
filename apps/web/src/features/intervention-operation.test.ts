import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { interventionFixture, interventionIntentFixture, interventionLookupFixture } from '../tests/intervention-fixture';
let operation: typeof import('./intervention-operation');
const key = 'bounded-funds-intervention-operation-v1:http://intervention-store-unit.local';
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubEnv('VITE_API_BASE_URL', 'http://intervention-store-unit.local'); vi.stubGlobal('crypto', webcrypto); operation = await import('./intervention-operation'); });
afterEach(() => { vi.restoreAllMocks(); });

test('发送前保存完整原body/hash；所有HTTP终态均保留，未找到和换键不清', async () => {
  const intent = interventionIntentFixture(); await operation.beginInterventionOperation(intent); expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(intent); operation.endInterventionAttempt();
  await expect(operation.clearInterventionAfterRead(intent, interventionLookupFixture(intent, false))).rejects.toThrow('不是终局'); await expect(operation.beginInterventionOperation(interventionIntentFixture('OBSERVE', 'TOOL_ONLY_NEW_KEY'))).rejects.toThrow('不能换键'); expect(operation.getInterventionOperation().pending).toEqual(intent);
});

test('逐完整原命令/receipt/hash只读核对后清；恢复方法从不POST', async () => {
  vi.stubGlobal('fetch', vi.fn()); const intent = interventionIntentFixture('ACKNOWLEDGE'); await operation.beginInterventionOperation(intent); operation.endInterventionAttempt();
  await operation.clearInterventionAfterRead(intent, interventionLookupFixture(intent)); expect(operation.getInterventionOperation().pending).toBeNull(); expect(sessionStorage.getItem(key)).toBeNull(); expect(fetch).not.toHaveBeenCalled();
});

test('DELIVER丢回复只认实际固定收件身份，previously_claimed布尔/别的epoch绝不清', async () => {
  const intent = interventionIntentFixture('DELIVER'); await operation.beginInterventionOperation(intent); operation.endInterventionAttempt();
  await expect(operation.clearInterventionAfterRead(intent, interventionFixture())).rejects.toThrow('原固定收件'); const bool = interventionFixture(); bool.previously_claimed = true; await expect(operation.clearInterventionAfterRead(intent, bool)).rejects.toThrow();
  const wrong = interventionFixture(true); wrong.original_inbox_claim!.epoch_id = intent.user_id; await expect(operation.clearInterventionAfterRead(intent, wrong)).rejects.toThrow();
  await operation.clearInterventionAfterRead(intent, interventionFixture(true)); expect(operation.getInterventionOperation().pending).toBeNull();
});

test('失回刷新恢复不可变原命令；恶意hash拒绝，旧body不改成新金额', async () => {
  const intent = interventionIntentFixture(); sessionStorage.setItem(key, JSON.stringify(intent)); expect(operation.recoverInterventionOperation().pending).toEqual(intent); expect(Object.isFrozen(operation.getInterventionOperation().pending!.body)).toBe(true);
  const changed = structuredClone(intent); changed.request_hash = '0'.repeat(64); await expect(operation.beginInterventionOperation(changed)).rejects.toThrow(); expect(sessionStorage.getItem(key)).toBe(JSON.stringify(intent));
});

test('并发/跨族门/存储失败阻止POST，保留原件与storage_error', async () => {
  vi.stubGlobal('fetch', vi.fn()); const intent = interventionIntentFixture(); await expect(operation.beginInterventionOperation(intent, true)).rejects.toThrow();
  const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('TOOL_ONLY_STORAGE_DENIED'); }); await expect(operation.beginInterventionOperation(intent)).rejects.toThrow('无法保存'); expect(operation.getInterventionOperation().busy).toBe(false); expect(operation.getInterventionOperation().storage_error).not.toBeNull(); expect(fetch).not.toHaveBeenCalled(); spy.mockRestore();
});

test('坏JSON保持原字节；删除失败不得解除门，发送中不可抢先用GET清', async () => {
  sessionStorage.setItem(key, '{BAD'); expect(operation.recoverInterventionOperation().storage_error).not.toBeNull(); expect(sessionStorage.getItem(key)).toBe('{BAD'); vi.resetModules(); sessionStorage.clear(); operation = await import('./intervention-operation');
  const intent = interventionIntentFixture(); await operation.beginInterventionOperation(intent); await expect(operation.clearInterventionAfterRead(intent, interventionLookupFixture(intent))).rejects.toThrow('仍在发送'); operation.endInterventionAttempt();
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('TOOL_ONLY_STORAGE_DENIED'); }); await expect(operation.clearInterventionAfterRead(intent, interventionLookupFixture(intent))).rejects.toThrow('无法删除'); expect(operation.getInterventionOperation().pending).toEqual(intent);
});
