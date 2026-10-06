import { webcrypto } from 'node:crypto';
import { afterEach, expect, test, vi } from 'vitest';
import { composedFixture } from '../tests/composed-action-set-fixture';
import { getOriginalComposedActionSet, parseComposedActionSet } from './composed-action-set';
import { releaseHash } from './goal-release-authorizations';

afterEach(() => vi.unstubAllGlobals());
const without = (value: object, field: string): Record<string, unknown> => Object.fromEntries(Object.entries(value).filter(([key]) => key !== field));
test.each(['auto', 'ask', 'unknown'] as const)('retains exact %s originals, one payment and no new bank authority', async (kind) => {
  vi.stubGlobal('crypto', webcrypto); const value = composedFixture(kind); const raw = JSON.stringify(value);
  const result = await parseComposedActionSet(value, raw);
  expect(getOriginalComposedActionSet(result)).toBe(raw);
  expect(result.global_action_set_complete).toBe(kind !== 'unknown');
  expect(result.bank_authority).toBe(false); expect(result.financial_write).toBe(false);
  if (kind !== 'unknown') { expect(result.candidates).toHaveLength(1); expect(result.candidates[0]!.amount_cents).toBe(300); expect(result.candidates[0]!.autonomy_level).toBe(kind === 'auto' ? 'AUTO_EXECUTE' : 'ASK_ONCE'); expect(result.replaced_original_candidate_keys).toHaveLength(1); }
  else { expect(result.action_set_signature).toBeNull(); expect(result.replaced_original_candidate_keys).toEqual([]); expect(result.candidates.some((row) => row.state === 'UNKNOWN')).toBe(true); }
});
test.each(['owner', 'epoch', 'clock', 'authority', 'notification', 'money', 'family-count', 'relation-count', 'duplicate-relation', 'doubled-payment', 'false-original-key', 'original-table-count', 'unknown-to-complete', 'signature', 'hash'] as const)('rejects %s drift even with recomputed response hashes', async (change) => {
  vi.stubGlobal('crypto', webcrypto); const value = composedFixture(change === 'unknown-to-complete' ? 'unknown' : 'auto');
  const f = value.periodic_family;
  if (change === 'owner') f.user_id = '00000000-0000-0000-0000-000000003333';
  if (change === 'epoch') f.epoch_id = '00000000-0000-0000-0000-000000003333';
  if (change === 'clock') f.as_of = '2026-10-05T00:00:00Z';
  if (change === 'authority') (value as unknown as Record<string, unknown>).bank_authority = true;
  if (change === 'notification') (value as unknown as Record<string, unknown>).notification_support = 'DELIVERED';
  if (change === 'money') value.candidates[0]!.amount_cents = 300.1;
  if (change === 'family-count') f.expected_full_policy_ids.push('00000000-0000-0000-0000-000000003333');
  if (change === 'relation-count') f.relation_source_count += 1;
  if (change === 'duplicate-relation') { f.relation_source_ids.push(f.relation_source_ids[0]!); f.relation_source_count += 1; }
  if (change === 'doubled-payment') { value.candidates.push(structuredClone(value.original_actual_snapshot.candidates[0]!)); value.expected_candidate_keys.push(value.original_actual_snapshot.candidates[0]!.candidate_key); }
  if (change === 'false-original-key') value.replaced_original_candidate_keys = ['payment:00000000-0000-0000-0000-000000003333'];
  if (change === 'original-table-count') { const table = value.original_actual_snapshot.table_coverage[0]!; table.actual_count = table.captured_count - 1; }
  if (change === 'unknown-to-complete') { value.status = 'COMPLETE'; value.global_action_set_complete = true; value.reasons = []; value.unsupported_producers = []; value.action_set_signature = '0'.repeat(64); }
  if (change === 'signature') value.action_set_signature = '0'.repeat(64);
  if (change !== 'hash' && change !== 'money') { f.result_hash = await releaseHash(without(f, 'result_hash')); value.original_actual_snapshot.snapshot_hash = await releaseHash(without(value.original_actual_snapshot, 'snapshot_hash')); value.snapshot_hash = await releaseHash(without(value, 'snapshot_hash')); }
  else value.snapshot_hash = '0'.repeat(64);
  await expect(parseComposedActionSet(value, JSON.stringify(value))).rejects.toThrow();
});
