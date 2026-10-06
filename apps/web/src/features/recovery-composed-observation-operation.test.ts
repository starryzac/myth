import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { observationFixture, observationIntentFixture } from '../tests/recovery-composed-observation-fixture';

beforeEach(() => { vi.resetModules(); localStorage.clear(); vi.stubGlobal('crypto', webcrypto); });
afterEach(() => vi.unstubAllGlobals());
test('full intent persists before POST; POST result cannot clear; only exact fresh GET clears', async () => {
  const api = await import('../api/recovery-composed-observations'); const store = await import('./recovery-composed-observation-operation');
  const intent = await observationIntentFixture(); const wire = await observationFixture(intent);
  await store.beginRecoveryComposedObservation(intent, false);
  const raw = localStorage.getItem('bounded-funds-recovery-composed-observation-v1:same-origin')!; expect(JSON.parse(raw).pending).toEqual(intent);
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(wire))));
  const post = await api.postRecoveryObservation(intent); store.endRecoveryComposedObservationAttempt();
  await expect(store.acceptRecoveryObservationRead(intent, post)).rejects.toThrow(); expect(store.getRecoveryComposedObservationOperation().pending).not.toBeNull();
  const original = await api.getRecoveryObservation(intent.expected_run_id, intent); await store.acceptRecoveryObservationRead(intent, original);
  expect(store.getRecoveryComposedObservationOperation().pending).toBeNull(); expect(store.getRecoveryComposedObservationOperation().original_json).toBe(JSON.stringify(wire));
});
test('fake404 and non404 read cannot open same-key retry; fresh actual404 preserves exact body', async () => {
  const api = await import('../api/recovery-composed-observations'); const store = await import('./recovery-composed-observation-operation'); const intent = await observationIntentFixture();
  await store.beginRecoveryComposedObservation(intent, false); store.endRecoveryComposedObservationAttempt();
  expect(() => store.noteRecoveryObservationAbsent(intent)).toThrow(); await expect(store.beginRecoveryObservationSameKeyRetry(intent, false)).rejects.toThrow();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { code: 'MISSING', message: '未知' } }), { status: 503 })));
  await expect(api.getRecoveryObservation(intent.expected_run_id, intent)).rejects.toMatchObject({ status: 503 }); expect(() => store.noteRecoveryObservationAbsent(intent)).toThrow();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: '未找到' } }), { status: 404 })));
  await expect(api.getRecoveryObservation(intent.expected_run_id, intent)).rejects.toMatchObject({ status: 404 }); store.noteRecoveryObservationAbsent(intent);
  await expect(store.beginRecoveryObservationSameKeyRetry(intent, true)).rejects.toThrow(); await store.beginRecoveryObservationSameKeyRetry(intent, false);
  expect(store.getRecoveryComposedObservationOperation().pending!.body_json).toBe(intent.body_json);
});
test('external gate and localStorage failure stop POST without replacing original', async () => {
  const store = await import('./recovery-composed-observation-operation'); const intent = await observationIntentFixture();
  await expect(store.beginRecoveryComposedObservation(intent, true)).rejects.toThrow(); expect(localStorage.length).toBe(0);
  const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('full'); });
  await expect(store.beginRecoveryComposedObservation(intent, false)).rejects.toThrow(); expect(store.getRecoveryComposedObservationOperation().pending?.expected_run_id).toBe(intent.expected_run_id); expect(store.getRecoveryComposedObservationOperation().storage_error).not.toBeNull(); spy.mockRestore();
});
test('reload keeps original exact pending identity; changed stored hash blocks', async () => {
  let store = await import('./recovery-composed-observation-operation'); const intent = await observationIntentFixture(); await store.beginRecoveryComposedObservation(intent, false); store.endRecoveryComposedObservationAttempt();
  vi.resetModules(); store = await import('./recovery-composed-observation-operation'); await store.recoverRecoveryComposedObservationOperation(); expect(store.getRecoveryComposedObservationOperation().pending).toEqual(intent);
  const key = 'bounded-funds-recovery-composed-observation-v1:same-origin'; const raw = JSON.parse(localStorage.getItem(key)!); raw.pending.request_hash = '0'.repeat(64); localStorage.setItem(key, JSON.stringify(raw));
  vi.resetModules(); store = await import('./recovery-composed-observation-operation'); await store.recoverRecoveryComposedObservationOperation(); expect(store.getRecoveryComposedObservationOperation().storage_error).not.toBeNull();
});
test('storage failure stays blocked until exact original GET and successful original persistence', async () => {
  const api = await import('../api/recovery-composed-observations'); const store = await import('./recovery-composed-observation-operation'); const intent = await observationIntentFixture(); const wire = await observationFixture(intent);
  const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('full'); }); await expect(store.beginRecoveryComposedObservation(intent, false)).rejects.toThrow(); spy.mockRestore();
  expect(store.getRecoveryComposedObservationOperation().storage_error).not.toBeNull(); await expect(store.beginRecoveryComposedObservation(intent, false)).rejects.toThrow();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(wire)))); const original = await api.getRecoveryObservation(intent.expected_run_id, intent); await store.acceptRecoveryObservationRead(intent, original);
  expect(store.getRecoveryComposedObservationOperation().pending).toBeNull(); expect(store.getRecoveryComposedObservationOperation().storage_error).toBeNull(); expect(JSON.parse(localStorage.getItem('bounded-funds-recovery-composed-observation-v1:same-origin')!).original_json).toBe(JSON.stringify(wire));
});
