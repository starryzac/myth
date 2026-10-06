/** Synthetic local reader risks only. These are not actual financial observations. */
import { webcrypto } from 'node:crypto';
import { afterEach, expect, test, vi } from 'vitest';
import { installHttpFixture } from '../tests/policy-fixture';
import { registeredFixture } from '../tests/registered-action-set-fixture';
import { getRegisteredActionSet, originalRegisteredActionSet, parseRegisteredActionSet } from './registered-action-set';
import { releaseHash } from './goal-release-authorizations';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const without = (value: object, field: string): Record<string, unknown> => Object.fromEntries(Object.entries(value).filter(([key]) => key !== field));
test.each(['complete', 'release_missing', 'joint', 'joint_missing', 'release'] as const)('retains original %s family denominators and hashes', async kind => {
  vi.stubGlobal('crypto', webcrypto); const value = registeredFixture(kind); const raw = JSON.stringify(value);
  const parsed = await parseRegisteredActionSet(value, raw);
  expect(originalRegisteredActionSet(parsed)).toBe(raw);
  expect(parsed.global_action_set_complete).toBe(['complete', 'joint'].includes(kind));
  expect(parsed.original_recovery_composed_snapshot.algorithm_version).toBe('full-policy-action-set-boundary-recovery-composed-v4');
  expect(parsed.bank_authority).toBe(false); expect(parsed.financial_write).toBe(false);
  if (kind === 'joint') { expect(parsed.replaced_original_candidate_keys).toEqual(['goal:00000000-0000-0000-0000-000000000002']); expect(parsed.candidates[0]!.candidate_key.startsWith('full-joint:')).toBe(true); }
  if (kind === 'joint_missing') { expect(parsed.candidates.some(row => row.candidate_key.startsWith('goal:'))).toBe(true); expect(parsed.expected_candidate_keys.some(key => key.startsWith('full-joint:'))).toBe(true); expect(parsed.action_set_signature).toBeNull(); }
});
test.each(['owner', 'epoch', 'clock', 'authority', 'notify', 'hide-release-reason', 'hide-joint-denominator', 'duplicate-goal', 'bad-goal-key', 'bad-shadow', 'hide-old', 'money', 'false-complete', 'inflight', 'joint-execution', 'hash'] as const)('rejects %s even after supplied result hashes are rewritten', async change => {
  vi.stubGlobal('crypto', webcrypto); const value = registeredFixture(change === 'hide-release-reason' ? 'release_missing' : change === 'hide-old' || change === 'false-complete' ? 'joint_missing' : 'joint');
  const r = value.release_family; const j = value.joint_family;
  if (change === 'owner') r.user_id = '00000000-0000-0000-0000-000000007777';
  if (change === 'epoch') j.epoch_id = '00000000-0000-0000-0000-000000007777';
  if (change === 'clock') j.as_of = '1900-01-01T00:00:00Z';
  if (change === 'authority') (value as unknown as Record<string, unknown>).bank_authority = true;
  if (change === 'notify') (value as unknown as Record<string, unknown>).notification_support = 'DELIVERED';
  if (change === 'hide-release-reason') value.reasons = value.reasons.filter(reason => !r.reasons.includes(reason));
  if (change === 'hide-joint-denominator') { j.expected_goal_ids = []; j.expected_candidate_keys = []; j.results = []; }
  if (change === 'duplicate-goal') j.expected_goal_ids.push(j.expected_goal_ids[0]!);
  if (change === 'bad-goal-key') j.results[0]!.goal_id = '00000000-0000-0000-0000-000000007777';
  if (change === 'bad-shadow') j.results[0]!.shadow_original_candidate_key = 'goal:not-original';
  if (change === 'hide-old') { value.candidates = value.candidates.filter(row => !row.candidate_key.startsWith('goal:')); value.expected_candidate_keys = value.candidates.map(row => row.candidate_key).sort(); }
  if (change === 'money') value.candidates[0]!.amount_cents = 0.1;
  if (change === 'false-complete') { value.status = 'COMPLETE'; value.global_action_set_complete = true; value.reasons = []; value.action_set_signature = '0'.repeat(64); }
  if (change === 'inflight') j.unresolved_original_action_ids = ['00000000-0000-0000-0000-000000007777'];
  if (change === 'joint-execution') (j as unknown as Record<string, unknown>).different_allocation_execution = 'SUPPORTED';
  r.result_hash = await releaseHash(without(r, 'result_hash')); j.result_hash = await releaseHash(without(j, 'result_hash'));
  value.snapshot_hash = ['hash', 'money'].includes(change) ? '0'.repeat(64) : await releaseHash(without(value, 'snapshot_hash'));
  await expect(parseRegisteredActionSet(value, JSON.stringify(value))).rejects.toThrow();
});
test.each(['key', 'role', 'source-denominator', 'auto', 'duplicate'] as const)('rejects altered Release %s while retaining its other unknown branches', async change => {
  vi.stubGlobal('crypto', webcrypto); const value = registeredFixture('release'); const r = value.release_family; const row = r.results[0]!;
  if (change === 'key') row.destination_account_id = '00000000-0000-0000-0000-000000007777';
  if (change === 'role') (row as unknown as Record<string, unknown>).requires_new_exact_user_confirmation = false;
  if (change === 'source-denominator') r.expected_full_policy_ids = [];
  if (change === 'auto') { row.view.state = 'INCLUDED'; row.view.action_type = 'RELEASE_GOAL'; row.view.amount_cents = 10; row.view.signature = 'a'.repeat(64); row.view.autonomy_level = 'AUTO_EXECUTE'; }
  if (change === 'duplicate') r.results.push(structuredClone(row));
  r.result_hash = await releaseHash(without(r, 'result_hash')); value.snapshot_hash = await releaseHash(without(value, 'snapshot_hash'));
  await expect(parseRegisteredActionSet(value, JSON.stringify(value))).rejects.toThrow();
});
test('rejects different raw response and supplied object, and only requests the fixed GET endpoint', async () => {
  vi.stubGlobal('crypto', webcrypto); const value = registeredFixture();
  await expect(parseRegisteredActionSet(value, JSON.stringify(registeredFixture('joint')))).rejects.toThrow();
  const calls = installHttpFixture((method, path, body) => { expect(method).toBe('GET'); expect(path).toBe('/api/v1/boundary/registered-action-set/current'); expect(body).toBeUndefined(); return value; });
  expect((await getRegisteredActionSet()).snapshot_hash).toBe(value.snapshot_hash); expect(calls).toHaveLength(1);
});
