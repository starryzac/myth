import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { createFullIntent, fullLookupFixture, stateFullIntent } from '../tests/full-policy-fixture';
type Module = typeof import('./full-policy-operation');
let operation: Module;
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubEnv('VITE_API_BASE_URL', 'http://unit-full-operation.local'); operation = await import('./full-policy-operation'); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); sessionStorage.clear(); });
test('原body/key保存先于发送阶段，HTTP结束仍pending，NOT_FOUND不能解除', () => {
  const original = createFullIntent(); vi.stubGlobal('fetch', vi.fn()); operation.beginFullPolicyOperation(original); expect(operation.getFullPolicyOperation().busy).toBe(true); expect(sessionStorage.length).toBe(1); expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe(JSON.stringify(original));
  operation.endFullPolicyAttempt(); expect(operation.getFullPolicyOperation().pending).toEqual(original); expect(() => operation.clearFullPolicyOperationAfterLookup(original, fullLookupFixture(original, false))).toThrow(); expect(fetch).not.toHaveBeenCalled();
});
test('同一原请求手动重放保key/body，另一body或kind不能覆盖pending', () => {
  const original = createFullIntent(); operation.beginFullPolicyOperation(original); operation.endFullPolicyAttempt(); operation.beginFullPolicyOperation(original); operation.endFullPolicyAttempt();
  const changed = operation.prepareFullPolicyIntent({ ...original, body: { ...original.body, reason: 'changed-reason' } }); expect(() => operation.beginFullPolicyOperation(changed)).toThrow('禁止换键'); expect(() => operation.beginFullPolicyOperation(stateFullIntent('SUSPEND'))).toThrow('禁止换键'); expect(operation.getFullPolicyOperation().pending).toEqual(original);
});
test('只完整匹配的RECORDED原命令解除本族门，原记录无授权意义', () => {
  const original = createFullIntent(); operation.beginFullPolicyOperation(original); operation.endFullPolicyAttempt(); operation.clearFullPolicyOperationAfterLookup(original, fullLookupFixture(original)); expect(operation.getFullPolicyOperation()).toEqual({ pending: null, busy: false, storage_error: null }); expect(sessionStorage.length).toBe(0);
});
test.each(['key', 'body', 'kind', 'policy', 'request_hash', 'result_hash', 'version', 'epoch', 'authority', 'receipt', 'owner', 'sequence', 'extra_envelope', 'timestamp'])('恢复证据%s不匹配时保留全部原记录', (field) => {
  const original = stateFullIntent('SUSPEND'); const bad = fullLookupFixture(original); const request = bad.original_request!; const command = bad.command!;
  if (field === 'key') bad.idempotency_key = 'different-key';
  if (field === 'body') (request.body as Record<string, unknown>).reason = 'different-body';
  if (field === 'kind') request.kind = 'REVOKE';
  if (field === 'policy') request.policy_id = null;
  if (field === 'request_hash') bad.request_hash = '0'.repeat(64);
  if (field === 'result_hash') command.result_hash = 'invalid';
  if (field === 'version') command.result.version_id = '71000000-0000-4000-8000-000000000099';
  if (field === 'epoch') command.result.epoch_id = '71000000-0000-4000-8000-000000000099';
  if (field === 'authority') (bad as unknown as Record<string, unknown>).bank_authority = true;
  if (field === 'receipt') command.result.receipt_is_current_authority = true;
  if (field === 'owner') request.user_id = 'not-a-user';
  if (field === 'sequence') command.previous_hash = null;
  if (field === 'extra_envelope') request.override = true;
  if (field === 'timestamp') command.created_at = 'not-original-time';
  operation.beginFullPolicyOperation(original); operation.endFullPolicyAttempt(); const saved = sessionStorage.getItem(sessionStorage.key(0)!); expect(() => operation.clearFullPolicyOperationAfterLookup(original, bad)).toThrow(); expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe(saved);
});
test('session重载只恢复exact请求，不发HTTP、不自动改键', () => {
  const original = createFullIntent(); sessionStorage.setItem('bounded-funds-full-policy-operation-v1:http://unit-full-operation.local', JSON.stringify(original)); vi.stubGlobal('fetch', vi.fn()); expect(operation.recoverFullPolicyOperation().pending).toEqual(original); expect(fetch).not.toHaveBeenCalled(); expect(Object.isFrozen(operation.getFullPolicyOperation().pending!.body)).toBe(true);
});
test('坏session数据与额外金额/键字段不能静默放行新写', () => {
  const bad = { ...createFullIntent(), body_json: '{}' }; sessionStorage.setItem('bounded-funds-full-policy-operation-v1:http://unit-full-operation.local', JSON.stringify(bad)); expect(operation.recoverFullPolicyOperation().storage_error).not.toBeNull(); expect(() => operation.beginFullPolicyOperation(createFullIntent())).toThrow(); expect(sessionStorage.length).toBe(1);
});
test('保存失败不进入busy发送，原body仍保留且锁住新写', () => {
  const original = createFullIntent(); vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('UNIT_STORAGE_DENIED'); }); expect(() => operation.beginFullPolicyOperation(original)).toThrow('无法保存'); expect(operation.getFullPolicyOperation().pending).toEqual(original); expect(operation.getFullPolicyOperation().busy).toBe(false); expect(operation.getFullPolicyOperation().storage_error).not.toBeNull();
});
test('删除恢复记录失败不能放行其他族，已核对也保原pending', () => {
  const original = createFullIntent(); operation.beginFullPolicyOperation(original); operation.endFullPolicyAttempt(); vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('UNIT_REMOVE_DENIED'); }); expect(() => operation.clearFullPolicyOperationAfterLookup(original, fullLookupFixture(original))).toThrow('无法删除'); expect(operation.getFullPolicyOperation().pending).toEqual(original); expect(operation.getFullPolicyOperation().storage_error).not.toBeNull();
});
test('外族blocked与原writeflight阻止发送而不生成新pending', async () => {
  const original = createFullIntent(); expect(() => operation.beginFullPolicyOperation(original, true)).toThrow(); const write = await import('./write-flight'); write.beginWriteFlight(); expect(() => operation.beginFullPolicyOperation(original)).toThrow(); write.endWriteFlight(); expect(operation.getFullPolicyOperation().pending).toBeNull();
});
test('尚未挂载其他页面也先恢复原demo session；待核对资金请求阻断新的FULL命令', () => {
  sessionStorage.setItem('bounded-funds-demo-operation-v1:http://unit-full-operation.local', JSON.stringify({ kind: 'reset', epoch_id: null, reset_key: 'other-family-original-key' })); expect(() => operation.beginFullPolicyOperation(createFullIntent())).toThrow('其他原请求'); expect(operation.getFullPolicyOperation().pending).toBeNull(); expect(sessionStorage.getItem('bounded-funds-demo-operation-v1:http://unit-full-operation.local')).toContain('other-family-original-key');
});
test('恢复/修改必须新版本；候选多余授权/金额字段和旧模板不得构造成写入', () => {
  const original = stateFullIntent('RESUME'); const bad = fullLookupFixture(original); bad.command!.version_id = (original.body as { expected_version_id: string }).expected_version_id; bad.command!.result.version_id = bad.command!.version_id; expect(operation.recordedFullPolicyLookupMatches(original, bad)).toBe(false);
  const create = createFullIntent(); expect(operation.validFullPolicyIntent({ ...create, body: { ...create.body, bank_authority: true } })).toBe(false); expect(operation.validFullPolicyIntent({ ...create, body: { ...create.body, template_name: 'LongTermGoalPolicy' } })).toBe(false);
});
