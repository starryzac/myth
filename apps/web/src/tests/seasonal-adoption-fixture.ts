/** Synthetic source-shaped module data, never a recorded USER consent or PG proof. */
import raw from './seasonal-adoption-fixture.json';
import type { FullPolicy } from '../api/full-policies';
import type { LocalActorSession } from '../api/local-actor';
import { seasonalHash, createSeasonalIntent, parseSeasonalPreview, seasonalReviewHash } from '../api/seasonal-reserve-adoptions';
import type { SeasonalIntent, SeasonalLookup, SeasonalPreview, SeasonalProof, SeasonalReceipt, SeasonalScope } from '../api/seasonal-reserve-adoptions';
import { releaseUUID5 } from '../api/goal-release-authorizations';

export async function seasonalFixture() {
  const scope = structuredClone(raw.preview.scope) as unknown as SeasonalScope;
  const preview = await parseSeasonalPreview(structuredClone(raw.preview), scope.user_id, scope.policy_id, scope.version_id, scope.window_id);
  return { scope, preview, full: structuredClone(raw.full_policy) as unknown as FullPolicy, identity: structuredClone(raw.identity) as LocalActorSession };
}
export async function seasonalIntentFixture(): Promise<SeasonalIntent> {
  const { scope } = await seasonalFixture(); return createSeasonalIntent(scope, { expected_version_id: scope.version_id, expected_epoch_id: scope.epoch_id, window_id: scope.window_id, reviewed_hash: await seasonalReviewHash(scope), accepted: true, reason: 'Explicit synthetic test consent', idempotency_key: 'synthetic-adopt-1' });
}
export async function seasonalReceiptFixture(intent: SeasonalIntent): Promise<SeasonalReceipt> {
  const original = structuredClone(raw.receipt.original) as unknown as SeasonalReceipt['original']; original.original_request = structuredClone(intent.body); original.idempotency_key = intent.body.idempotency_key; original.scope = structuredClone(intent.reviewed_scope); original.command_id = await releaseUUID5('caaf871c-09d1-44c4-baf1-8b0d8f9fef83', `${intent.user_id}:${intent.epoch_id}:${intent.body.idempotency_key}`); original.request_hash = intent.request_hash; original.reviewed_hash = intent.body.reviewed_hash;
  return { simulation: true, original, evidence_id: await releaseUUID5(original.command_id, 'evidence'), evidence_hash: await seasonalHash(original), trace_hash: 'a'.repeat(64), idempotent_replay: false, bank_authority: false, financial_execution_performed: false };
}
export async function seasonalLookupFixture(intent: SeasonalIntent, found = true): Promise<SeasonalLookup> { return { simulation: true, user_id: intent.user_id, epoch_id: intent.epoch_id, idempotency_key: intent.body.idempotency_key, status: found ? 'RECORDED' : 'NOT_FOUND_NOT_FINAL', original_receipt: found ? await seasonalReceiptFixture(intent) : null, bank_authority: false }; }
export function seasonalProofFixture(status: SeasonalProof['status'] = 'ADVICE_ONLY'): SeasonalProof {
  const value = structuredClone(raw.proof) as unknown as SeasonalProof;
  if (status === 'ADVICE_ONLY') return { ...value, status, original: null, evidence_id: null, evidence_hash: null, trace_hash: null, current_scope: null, actual_adoption_count: 0, retained_command_ids: [], reasons: ['NO_ORIGINAL_USER_ADOPTION'] };
  return status === 'VERIFIED' ? value : { ...value, status, reasons: ['ADOPTED_ORIGINAL_SOURCE_OR_POLICY_CHANGED'] };
}
export function unknownSeasonalPreview(): SeasonalPreview { return { simulation: true, status: 'UNKNOWN', scope: null, reviewed_hash: null, reasons: ['ACTUAL_COMPLETE_HISTORY_NOT_PROVEN'], bank_authority: false, hard_protection_changed: false }; }
