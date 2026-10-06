/** Synthetic unit originals only; no actual bank/PG/experiment/notification evidence. */
import type { RecoveryObserveRequest, RecoveryObservation, RecoveryObservationIntent } from '../api/recovery-composed-observations';
import { releaseHash, releaseUUID5 } from '../api/goal-release-authorizations';
import { recoveryComposedFixture } from './recovery-composed-action-set-fixture';

export async function observationIntentFixture(previous: string | null = null, suffix = '1', kind: 'complete' | 't0_unknown_original' | 't1_unknown' = 'complete'): Promise<RecoveryObservationIntent> {
  const snapshot = recoveryComposedFixture(kind);
  const body: RecoveryObserveRequest = { expected_epoch_id: snapshot.epoch_id, previous_observation_run_id: previous, idempotency_key: `TOOL_ONLY_OBSERVATION:${suffix}` };
  return { protocol: 'recovery-composed-observe-browser-v1', user_id: snapshot.user_id, epoch_id: snapshot.epoch_id, body, body_json: JSON.stringify(body), request_hash: await releaseHash(body), expected_run_id: await releaseUUID5('dfb9d38c-5698-532d-a90f-bcb2458d7d7f', `${snapshot.user_id}:${snapshot.epoch_id}:${body.idempotency_key}`), source_ref: { endpoint: '/boundary/recovery-composed-action-set/current', user_id: snapshot.user_id, epoch_id: snapshot.epoch_id, as_of: snapshot.as_of, snapshot_hash: snapshot.snapshot_hash }, bank_authority: false };
}
export async function observationFixture(intent: RecoveryObservationIntent, kind: 'complete' | 't0_unknown_original' | 't1_unknown' = 'complete', previous: RecoveryObservation | null = null): Promise<RecoveryObservation> {
  const snapshot = recoveryComposedFixture(kind);
  const comparable = !previous || previous.snapshot.global_action_set_complete && previous.snapshot.user_id === snapshot.user_id && previous.snapshot.epoch_id === snapshot.epoch_id && Date.parse(previous.snapshot.as_of) <= Date.parse(snapshot.as_of);
  const tag = snapshot.global_action_set_complete && comparable ? (!previous || previous.snapshot.action_set_signature === snapshot.action_set_signature ? 'BoundaryObserved' : 'BoundaryCrossed') : null;
  const semantic = tag && previous ? await releaseHash({ user_id: snapshot.user_id, epoch_id: snapshot.epoch_id, scope: snapshot.scope, before: previous.snapshot.action_set_signature, after: snapshot.action_set_signature }) : null;
  return { simulation: true, bank_authority: false, grants_authority: false, financial_write: false, notification_support: 'NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4', user_id: snapshot.user_id, epoch_id: snapshot.epoch_id, observation_run_id: intent.expected_run_id, previous_observation_run_id: intent.body.previous_observation_run_id, original_request: structuredClone(intent.body), request_hash: intent.request_hash, snapshot, kind: tag, semantic_key: semantic, requires_user_attention: tag === 'BoundaryCrossed', previous_snapshot_hash: previous?.snapshot.snapshot_hash ?? null, previous_action_set_signature: previous?.snapshot.action_set_signature ?? null, global_action_set_complete: snapshot.global_action_set_complete && comparable, idempotent_replay: false };
}
