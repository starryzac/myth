import { webcrypto } from 'node:crypto';
import { beforeEach, expect, test, vi } from 'vitest';
import { futureCandidateBody, futureConfirmBody, futureLookupFixture, futureUser, futureEpoch } from '../tests/future-income-planning-fixture';
let operation: typeof import('./future-income-operation'), api: typeof import('../api/future-income-planning');
const key = `bounded-funds-future-income-operation-v1:${import.meta.env.VITE_API_BASE_URL ?? 'same-origin'}`;
beforeEach(async () => { vi.resetModules(); sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); operation = await import('./future-income-operation'); api = await import('../api/future-income-planning'); });
async function originalRead(intent: import('./future-income-operation').FutureIncomeIntent, absent = false) { vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(futureLookupFixture(intent.kind, intent.body, intent.candidate ?? undefined, absent))))); return api.lookupFutureIncomeCommand(intent.user_id, intent.epoch_id, intent.body.idempotency_key); }
async function createWorkspace() { const intent = await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody()); await operation.beginFutureIncomeOperation(intent); operation.endFutureIncomeAttempt(); await operation.acceptFutureIncomeRead(intent, await originalRead(intent)); return operation.getFutureIncomeOperation().workspace!; }
test('POST前完整body/key/hash深冻结，POST结局不清门，唯独原GET匹配后保存工作区', async () => {
  const intent = await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody()); await operation.beginFutureIncomeOperation(intent);
  expect(JSON.parse(sessionStorage.getItem(key)!).pending).toEqual(intent); expect(Object.isFrozen(operation.getFutureIncomeOperation().pending!.body)).toBe(true);
  operation.endFutureIncomeAttempt(); await expect(operation.acceptFutureIncomeRead(intent, await api.parseFutureIncomeLookup(futureLookupFixture('CANDIDATE'), futureUser, futureEpoch, intent.body.idempotency_key))).rejects.toThrow();
  expect((await operation.acceptFutureIncomeRead(intent, await originalRead(intent))).complete).toBe(true); expect(operation.getFutureIncomeOperation().pending).toBeNull(); expect(operation.isFutureIncomeWorkspaceUnresolved(operation.getFutureIncomeOperation().workspace!)).toBe(true);
});
test('NOT_FOUND保原pending，不更换键/来源；手动同body重放原件不变', async () => {
  const intent = await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody()); await operation.beginFutureIncomeOperation(intent); operation.endFutureIncomeAttempt();
  expect((await operation.acceptFutureIncomeRead(intent, await originalRead(intent, true))).complete).toBe(false);
  await expect(operation.beginFutureIncomeOperation(await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, { ...futureCandidateBody(), idempotency_key: 'different' }))).rejects.toThrow();
  await operation.beginFutureIncomeOperation(intent); expect(operation.getFutureIncomeOperation().pending).toEqual(intent);
});
test('fresh原候选工作区才可显式确认，确认GET原件匹配后变为历史条件声明', async () => {
  const w = await createWorkspace(), c = w.candidate!;
  const intent = await operation.prepareFutureIncomeIntent('CONFIRM', futureUser, futureConfirmBody(c), c); await operation.beginFutureIncomeOperation(intent); operation.endFutureIncomeAttempt();
  expect((await operation.acceptFutureIncomeRead(intent, await originalRead(intent))).complete).toBe(true); expect(operation.isFutureIncomeWorkspaceUnresolved(operation.getFutureIncomeOperation().workspace!)).toBe(false);
  expect(operation.getFutureIncomeOperation().workspace!.confirmation!.grants_authority).toBe(false);
});
test('刷新只恢复原bytes，无网络；存储候选不是freshGET或授权，须独立查回后才能确认', async () => {
  const w = await createWorkspace(), stored = sessionStorage.getItem(key)!; vi.resetModules(); operation = await import('./future-income-operation'); api = await import('../api/future-income-planning'); vi.stubGlobal('fetch', vi.fn());
  const state = await operation.recoverFutureIncomeOperation(); expect(fetch).not.toHaveBeenCalled(); expect(sessionStorage.getItem(key)).toBe(stored); expect(api.isFreshFutureIncomeLookup(state.workspace!)).toBe(false);
  const intent = await operation.prepareFutureIncomeIntent('CONFIRM', futureUser, futureConfirmBody(w.candidate!), w.candidate!);
  await expect(operation.beginFutureIncomeOperation(intent)).rejects.toThrow();
  const readIntent = await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody()); await operation.acceptFutureIncomeWorkspaceRead(await originalRead(readIntent)); await operation.beginFutureIncomeOperation(intent); expect(operation.getFutureIncomeOperation().pending!.kind).toBe('CONFIRM');
});
test.each(['{BROKEN', JSON.stringify({ protocol: 'future-income-browser-state-v1', pending: {}, workspace: null })])('坏原存储锁住且不覆盖 %s', async raw => { sessionStorage.setItem(key, raw); expect((await operation.recoverFutureIncomeOperation()).storage_error).not.toBeNull(); await expect(operation.beginFutureIncomeOperation(await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody()))).rejects.toThrow(); expect(sessionStorage.getItem(key)).toBe(raw); });
test('存储拒绝未发送；独立核对无法持久保存仍不清原门', async () => {
  const intent = await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody()); vi.stubGlobal('fetch', vi.fn()); const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('SYNTHETIC_STORAGE_DENIED'); });
  await expect(operation.beginFutureIncomeOperation(intent)).rejects.toThrow('未发送'); expect(fetch).not.toHaveBeenCalled(); expect(operation.getFutureIncomeOperation().pending).toEqual(intent);
  spy.mockRestore(); const read = await originalRead(intent); vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('SYNTHETIC_STORAGE_DENIED'); }); await expect(operation.acceptFutureIncomeRead(intent, read)).rejects.toThrow('无法持久'); expect(operation.getFutureIncomeOperation().pending).toEqual(intent);
});
test('他族门/writeflight阻POST，自身原GET始终允许；不能latest/foreign原件清原pending', async () => {
  const intent = await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody()); await expect(operation.beginFutureIncomeOperation(intent, true)).rejects.toThrow();
  const flight = await import('./write-flight'); flight.beginWriteFlight(); await expect(operation.beginFutureIncomeOperation(intent)).rejects.toThrow(); expect((await originalRead(intent)).status).toBe('RECORDED'); flight.endWriteFlight();
  await operation.beginFutureIncomeOperation(intent); operation.endFutureIncomeAttempt(); const foreign = await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, { ...futureCandidateBody(), idempotency_key: 'other-key' }); await expect(operation.acceptFutureIncomeRead(intent, await originalRead(foreign))).rejects.toThrow(); expect(operation.getFutureIncomeOperation().pending).toEqual(intent);
});
test('未复核工作区阻第二候选；显式只丢本地复核不删服务器原件', async () => { await createWorkspace(); const second = await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, { ...futureCandidateBody(), idempotency_key: 'another-key' }); await expect(operation.beginFutureIncomeOperation(second)).rejects.toThrow(); vi.stubGlobal('fetch', vi.fn()); operation.discardFutureIncomeReview(); expect(fetch).not.toHaveBeenCalled(); await operation.beginFutureIncomeOperation(second); expect(operation.getFutureIncomeOperation().pending).toEqual(second); });
test.each(['reformatted', 'duplicate-key'] as const)('原body_json被%s改写即使canonical相等仍拒绝恢复，不替换实际POST原bytes', async kind => {
  const intent = structuredClone(await operation.prepareFutureIncomeIntent('CANDIDATE', futureUser, futureCandidateBody()));
  intent.body_json = kind === 'reformatted' ? JSON.stringify(intent.body, null, 2) : intent.body_json.replace('{', `{"idempotency_key":"${intent.body.idempotency_key}",`);
  expect(api.futureCanonical(JSON.parse(intent.body_json))).toBe(api.futureCanonical(intent.body));
  await expect(operation.parseFutureIncomeIntent(intent)).rejects.toThrow();
  sessionStorage.setItem(key, JSON.stringify({ protocol: 'future-income-browser-state-v1', pending: intent, workspace: null }));
  vi.stubGlobal('fetch', vi.fn()); expect((await operation.recoverFutureIncomeOperation()).storage_error).not.toBeNull(); expect(fetch).not.toHaveBeenCalled();
});
