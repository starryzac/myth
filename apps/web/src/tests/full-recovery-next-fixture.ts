/** TOOL_ONLY synthetic HTTP/UI records. No bank, PG, clock, human consent or runtime proof. */
import type { RecoveryIntent } from '../api/full-recovery-execution';
import type { RecoveryNextBody, RecoveryNextLookup, RecoveryNextPreview } from '../api/full-recovery-next';
import { recoveryFixtureHash, recoveryLookupFixture, recoveryPlanningFixture, recoveryPrepareFixture, recoveryPreviewFixture, recoveryUser } from './full-recovery-execution-fixture';

export function nextBodyFixture(key = 'root-test/one') : RecoveryNextBody {
  const old = recoveryPrepareFixture(); return { policy_id: old.policy_id, expected_version_id: old.expected_version_id, expected_epoch_id: old.expected_epoch_id, idempotency_key: key };
}
export function nextForwardedFixture(body = nextBodyFixture()) {
  return { ...recoveryPrepareFixture(), policy_id: body.policy_id, expected_version_id: body.expected_version_id, expected_epoch_id: body.expected_epoch_id, idempotency_key: `next-whole-v2:${recoveryFixtureHash({ protocol: 'full-recovery-next-whole-v2', root_key: body.idempotency_key })}` };
}
export function nextIntentFixture(body = nextBodyFixture()): RecoveryIntent {
  const prepare = nextForwardedFixture(body); return { protocol: 'full-recovery-browser-v1', kind: 'PREPARE', user_id: recoveryUser, prepare_request: prepare, action: null, path: '/full-recovery-actions/prepare', body: prepare, body_json: JSON.stringify(prepare), request_hash: recoveryFixtureHash(prepare) };
}
export function nextLookupFixture(body = nextBodyFixture(), state: 'NOT_FOUND' | 'PREPARED' | 'CONFIRMED' | 'UNKNOWN' | 'SETTLED' = 'NOT_FOUND'): RecoveryNextLookup {
  return { protocol: 'full-recovery-next-whole-v2', simulation: true, bank_authority: false, current_authority: false, not_found_is_final: false, automatically_advances: false, user_id: recoveryUser, idempotency_key: body.idempotency_key, status: state === 'NOT_FOUND' ? 'NOT_FOUND_NOT_FINAL' : 'RECORDED', request_binding_kind: 'PROJECTION_OF_VERIFIED_V1_ORIGINAL_AND_EXACT_ROOT_KEY', original_v2_request_separately_recorded: false, bound_request: state === 'NOT_FOUND' ? null : body, bound_request_hash: state === 'NOT_FOUND' ? null : recoveryFixtureHash(body), original_v1_lookup: recoveryLookupFixture(nextIntentFixture(body), state) };
}
export function nextPreviewFixture(body = nextBodyFixture(), state: 'READY' | 'UNKNOWN' | 'NO_ELIGIBLE' | 'RETAINED' = 'READY'): RecoveryNextPreview {
  const forwarded = nextForwardedFixture(body), delegated = recoveryPreviewFixture(forwarded, state === 'UNKNOWN' ? 'UNKNOWN' : 'VERIFIED_SCOPE'), planning = recoveryPlanningFixture();
  const asOf = delegated.proof.as_of; planning.as_of = asOf;
  if (!planning.plan) throw new Error('TOOL_ONLY_EXPECTED_PLAN');
  planning.plan.as_of = asOf; planning.plan.deadline_at = delegated.proof.deadline_at;
  if (state === 'NO_ELIGIBLE') planning.plan.lossless_steps = [];
  const selection = { protocol: 'full-recovery-next-whole-v2' as const, bank_authority: false as const, atomic_combination: false as const, current_candidate_denominator: planning.plan.candidates.length, current_selected_denominator: planning.plan.lossless_steps.length, eligibility: planning.plan.candidates.map((row) => ({ position_id: row.position_id, selected_by_original_plan: state !== 'NO_ELIGIBLE', eligible_for_v1_preview: state !== 'NO_ELIGIBLE', reasons: state === 'NO_ELIGIBLE' ? ['NOT_SELECTED_BY_ORIGINAL_PLAN'] : [] })), next_v1_request: state === 'NO_ELIGIBLE' ? null : forwarded, reasons: state === 'NO_ELIGIBLE' ? ['NO_CURRENT_ELIGIBLE_T0_T1_WHOLE_POSITION'] : [] };
  const value: RecoveryNextPreview = { protocol: 'full-recovery-next-whole-v2', simulation: true, read_only: true, bank_authority: false, grants_authority: false, atomic_combination: false, automatically_advances: false, user_id: recoveryUser, as_of: asOf, request: body, status: state === 'RETAINED' ? 'ORIGINAL_ACTION_RETAINED' : state === 'UNKNOWN' ? 'UNKNOWN' : state === 'NO_ELIGIBLE' ? 'NO_ELIGIBLE_NEXT_POSITION' : 'READY_TO_PREPARE', planning: state === 'RETAINED' ? null : planning, selection: state === 'RETAINED' ? null : selection, original_v1_preview: ['RETAINED', 'NO_ELIGIBLE'].includes(state) ? null : delegated, existing: nextLookupFixture(body, state === 'RETAINED' ? 'PREPARED' : 'NOT_FOUND'), input_hash: '', limitations: ['TOOL_ONLY_SYNTHETIC_NOT_BANK_PROOF', 'NO_PARTIAL_MATURE_LOSS_ATOMIC_OR_DEADLINE_EXTENSION'] };
  value.input_hash = recoveryFixtureHash({ protocol: value.protocol, user_id: value.user_id, as_of: asOf.replace(/Z$/, '+00:00'), request: body, existing: value.existing, planning: value.planning, selection: value.selection, original_v1_preview: value.original_v1_preview });
  return value;
}
