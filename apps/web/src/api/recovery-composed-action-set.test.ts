import { webcrypto } from 'node:crypto';
import { afterEach, expect, test, vi } from 'vitest';
import { recoveryComposedFixture } from '../tests/recovery-composed-action-set-fixture';
import { originalRecoveryComposedActionSet, parseRecoveryComposedActionSet } from './recovery-composed-action-set';
import { releaseHash } from './goal-release-authorizations';

afterEach(() => vi.unstubAllGlobals());
const without = (value: object, field: string): Record<string, unknown> => Object.fromEntries(Object.entries(value).filter(([key]) => key !== field));
test.each(['complete', 't0_unknown_original', 't1_unknown'] as const)('keeps exact %s response, original v3 and all unproved actions', async kind => {
  vi.stubGlobal('crypto', webcrypto); const value = recoveryComposedFixture(kind); const raw = JSON.stringify(value);
  const result = await parseRecoveryComposedActionSet(value, raw);
  expect(originalRecoveryComposedActionSet(result)).toBe(raw); expect(result.global_action_set_complete).toBe(kind === 'complete');
  expect(result.original_composed_snapshot.algorithm_version).toBe('full-policy-action-set-boundary-composed-v3');
  expect(result.bank_authority).toBe(false); expect(result.financial_write).toBe(false);
  if (kind === 't0_unknown_original') { expect(result.recovery_family.recovery_family_complete).toBe(true); expect(result.candidates.some(row => row.candidate_key.startsWith('full-recovery:') && row.amount_cents === 50000 && row.autonomy_level === 'ASK_ONCE')).toBe(true); expect(result.candidates.some(row => row.state === 'UNKNOWN')).toBe(true); }
  if (kind === 't1_unknown') { expect(result.recovery_family.recovery_family_complete).toBe(false); expect(result.unsupported_producers.some(code => code.includes('RecoveryPolicy:'))).toBe(true); expect(result.action_set_signature).toBeNull(); }
});
test.each(['owner', 'clock', 'authority', 'confirmation', 'duplicate-position', 'missing-position', 'hide-original', 'false-complete', 'inherited-auto', 'money', 'notify', 'hash'] as const)('rejects %s even when result hashes are rewritten', async change => {
  vi.stubGlobal('crypto', webcrypto); const value = recoveryComposedFixture(); const family = value.recovery_family;
  if (change === 'owner') family.user_id = '00000000-0000-0000-0000-000000007777';
  if (change === 'clock') family.as_of = '1900-01-01T00:00:00Z';
  if (change === 'authority') (value as unknown as Record<string, unknown>).bank_authority = true;
  if (change === 'confirmation') (family.results[0] as unknown as Record<string, unknown>).requires_new_exact_user_confirmation = false;
  if (change === 'duplicate-position') family.original_position_ids.push(family.original_position_ids[0]!);
  if (change === 'missing-position') family.original_position_ids = [];
  if (change === 'hide-original') { value.candidates = value.candidates.filter(row => row.state !== 'UNKNOWN'); value.expected_candidate_keys = value.candidates.map(row => row.candidate_key).sort(); }
  if (change === 'false-complete') { value.status = 'COMPLETE'; value.global_action_set_complete = true; value.reasons = []; value.action_set_signature = '0'.repeat(64); }
  if (change === 'inherited-auto') family.results[0]!.view.autonomy_level = 'AUTO_EXECUTE';
  if (change === 'money') value.candidates.find(row => row.candidate_key.startsWith('full-recovery:'))!.amount_cents = 0.1;
  if (change === 'notify') (value as unknown as Record<string, unknown>).notification_support = 'DELIVERED';
  family.result_hash = await releaseHash(without(family, 'result_hash')); value.snapshot_hash = ['hash', 'money'].includes(change) ? '0'.repeat(64) : await releaseHash(without(value, 'snapshot_hash'));
  await expect(parseRecoveryComposedActionSet(value, JSON.stringify(value))).rejects.toThrow();
});
