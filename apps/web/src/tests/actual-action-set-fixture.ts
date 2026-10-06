import { actualActionSetTables } from '../api/actual-action-set';
import type { ActualActionSet } from '../api/actual-action-set';

export function snapshot(): ActualActionSet {
  const hash = 'a'.repeat(64);
  return {
    algorithm_version: 'full-policy-action-set-boundary-actual-v2', scope: 'POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_V2',
    simulation: true, bank_authority: false, grants_authority: false, financial_write: false,
    arbitrary_manual_intents_covered: false, user_id: '00000000-0000-0000-0000-000000000001',
    epoch_id: '00000000-0000-0000-0000-000000000002', as_of: '2026-10-06T00:00:00Z',
    status: 'COMPLETE', global_action_set_complete: true, original_inventory_hash: hash,
    financial_input_hash: hash, input_hash: hash, snapshot_hash: hash, action_set_signature: hash,
    expected_candidate_keys: ['goal-original'], dynamic_candidate_keys: ['goal-original'], asset_candidate_keys: [],
    candidates: [{ candidate_key: 'goal-original', state: 'INCLUDED', action_type: 'ALLOCATE_GOAL', amount_cents: 123,
      autonomy_level: 'ASK_ONCE', signature: hash, reasons: [] }],
    table_coverage: actualActionSetTables.map((table) => ({ table, actual_count: 0, captured_count: 0, complete: true, rows_hash: hash })),
    unsupported_producers: [], reasons: [],
  };
}
