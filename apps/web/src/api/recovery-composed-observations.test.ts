import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { observationFixture, observationIntentFixture } from '../tests/recovery-composed-observation-fixture';
import { parseRecoveryObservation, originalRecoveryObservation, parseRecoveryObservationIntent, getRecoveryObservation, isFreshRecoveryObservationAbsent } from './recovery-composed-observations';
import { releaseHash } from './goal-release-authorizations';

beforeEach(() => vi.stubGlobal('crypto', webcrypto));
afterEach(() => vi.unstubAllGlobals());
test.each(['complete', 't0_unknown_original', 't1_unknown'] as const)('strict %s originals keep unknown and noauthority', async kind => {
  const intent = await observationIntentFixture(null, '1', kind); const original = await observationFixture(intent, kind); const raw = JSON.stringify(original) + '\n';
  const value = await parseRecoveryObservation(original, raw, null, intent);
  expect(originalRecoveryObservation(value)).toBe(raw); expect(value.kind).toBe(kind === 'complete' ? 'BoundaryObserved' : null); expect(value.financial_write).toBe(false);
});
test.each(['owner', 'epoch', 'run', 'request', 'extra', 'authority', 'complete', 'kind', 'semantic', 'attention', 'notify', 'time', 'bool'] as const)('rejects forged %s even with new request hash', async change => {
  const intent = await observationIntentFixture(); const value = await observationFixture(intent); const row = value as unknown as Record<string, unknown>;
  if (change === 'owner') value.user_id = '00000000-0000-0000-0000-000000000111';
  if (change === 'epoch') value.epoch_id = '00000000-0000-0000-0000-000000000111';
  if (change === 'run') value.observation_run_id = '00000000-0000-0000-0000-000000000111';
  if (change === 'request') value.original_request.idempotency_key = 'changed';
  if (change === 'extra') (value.original_request as unknown as Record<string, unknown>).amount_cents = 1;
  if (change === 'authority') row.bank_authority = true;
  if (change === 'complete') value.global_action_set_complete = false;
  if (change === 'kind') value.kind = 'BoundaryCrossed';
  if (change === 'semantic') value.semantic_key = 'a'.repeat(64);
  if (change === 'attention') value.requires_user_attention = true;
  if (change === 'notify') row.notification_support = 'DELIVERED';
  if (change === 'time') value.snapshot.as_of = 'not-a-clock';
  if (change === 'bool') row.idempotent_replay = 1;
  value.request_hash = await releaseHash(value.original_request);
  await expect(parseRecoveryObservation(value, JSON.stringify(value), null, intent)).rejects.toThrow();
});
test('complete parent exact original determines semantic; a changed kind/parent hash is refused', async () => {
  const parentIntent = await observationIntentFixture(); const parentWire = await observationFixture(parentIntent);
  const parent = await parseRecoveryObservation(parentWire, JSON.stringify(parentWire));
  const intent = await observationIntentFixture(parent.observation_run_id, '2'); const wire = await observationFixture(intent, 'complete', parent);
  const result = await parseRecoveryObservation(wire, JSON.stringify(wire), parent, intent);
  expect(result.kind).toBe('BoundaryObserved'); expect(result.semantic_key).not.toBeNull();
  wire.previous_snapshot_hash = 'f'.repeat(64); await expect(parseRecoveryObservation(wire, JSON.stringify(wire), parent, intent)).rejects.toThrow();
});
test('strict saved body/UUID5/hash rejects injected role and different exact JSON', async () => {
  const intent = await observationIntentFixture(); await expect(parseRecoveryObservationIntent(intent)).resolves.toEqual(intent);
  intent.body_json += ' '; await expect(parseRecoveryObservationIntent(intent)).rejects.toThrow();
});
test('parent 404 is UNVERIFIED, not absent current run or permission to repeat POST', async () => {
  const parentIntent = await observationIntentFixture(); const parent = await observationFixture(parentIntent);
  const intent = await observationIntentFixture(parent.observation_run_id, '2'); const child = await observationFixture(intent, 'complete', parent);
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith(child.observation_run_id) ? child : { error: { code: 'NOT_FOUND', message: '原parent不在' } }), { status: url.endsWith(child.observation_run_id) ? 200 : 404 })); vi.stubGlobal('fetch', fetcher);
  await expect(getRecoveryObservation(intent.expected_run_id, intent)).rejects.toMatchObject({ status: 409, code: 'PARENT_ORIGINAL_UNVERIFIED' });
  expect(isFreshRecoveryObservationAbsent(intent)).toBe(false); expect(fetcher.mock.calls).toHaveLength(2);
});
