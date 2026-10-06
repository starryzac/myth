import { beforeEach, expect, test, vi } from 'vitest';
import { zhiyuState } from './test-fixtures';
let operation: typeof import('./operation');
beforeEach(async () => { sessionStorage.clear(); localStorage.clear(); vi.resetModules(); operation = await import('./operation'); });
test('lost original persists through module restart; a new scenario cannot replace it or issue automatic POST', async () => {
  const environment = zhiyuState(); operation.recoverOperation(environment);
  const original = operation.makeLocator(environment, 'PREPARE', '/zhiyu/actions/prepare', { expected_epoch_id: environment.epoch_id, goal_id: '10000000-0000-0000-0000-000000000110', scenario: 'RESPONSE_LOSS' });
  operation.beginOperation(original); operation.endAttempt();
  const bytes = localStorage.getItem(operation.storageKey(environment.environment_id));
  vi.stubGlobal('fetch', vi.fn()); vi.resetModules(); operation = await import('./operation'); operation.recoverOperation(environment);
  expect(operation.getOperation().locator).toEqual(original); expect(fetch).not.toHaveBeenCalled();
  expect(() => operation.beginOperation({ ...original, body: { ...original.body, scenario: 'SAFE' }, body_json: JSON.stringify({ ...original.body, scenario: 'SAFE' }) })).toThrow('原操作尚未解决');
  expect(localStorage.getItem(operation.storageKey(environment.environment_id))).toBe(bytes);
});
test.each(['bounded-funds-goal-action-v1:same-origin', 'bounded-funds-full-dynamic-goal-operation-v1:http://old.local:workspace', 'bounded-funds-recovery-composed-observation-v1:same-origin'])('old original %s blocks namespace change and leaves exact bytes', (key) => {
  const storage = key.includes('observation') ? localStorage : sessionStorage;
  const bytes = '{OLD_UNRESOLVED_OR_CORRUPT'; storage.setItem(key, bytes);
  operation.recoverOperation(zhiyuState()); expect(operation.getOperation().error).toContain('旧环境');
  expect(() => operation.beginOperation(operation.makeLocator(zhiyuState(), 'INCOME', '/zhiyu/income', { expected_epoch_id: zhiyuState().epoch_id }))).toThrow();
  expect(storage.getItem(key)).toBe(bytes);
});
test('storage denial prevents any original from starting; foreign environment original is never discarded', () => {
  const environment = zhiyuState(); operation.recoverOperation(environment);
  const original = operation.makeLocator(environment, 'INCOME', '/zhiyu/income', { expected_epoch_id: environment.epoch_id });
  const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('DENIED'); });
  expect(() => operation.beginOperation(original)).toThrow('无法保存'); expect(operation.getOperation().busy).toBe(false);
  spy.mockRestore(); localStorage.setItem(operation.storageKey(environment.environment_id), JSON.stringify(original));
  operation.recoverOperation({ ...environment, environment_id: 'zhiyu-another-environment' });
  expect(operation.getOperation().error).toContain('另一个'); expect(localStorage.getItem(operation.storageKey(environment.environment_id))).toBe(JSON.stringify(original));
});
