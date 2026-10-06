/** TOOL_ONLY synthetic API/operation/component records; never bank, PG or human confirmation proof. */
import { createHash } from 'node:crypto';
import type { RecoveryAction, RecoveryIntent, RecoveryLookup, RecoveryPrepare, RecoveryPreview, RecoveryProof } from '../api/full-recovery-execution';
import { recoveryCanonicalJson } from '../api/full-recovery-execution';
import base from './full-recovery-execution-base.json';
import { boundaryFixture } from './demo-fixture';
import { recoveryPlanFixture } from './full-products-fixture';

export const recoveryUser = base.proof.user_id, recoveryEpoch = base.prepare.expected_epoch_id;
export const recoveryActionId = base.effect.operation_id, recoveryRunId = '00000000-0000-0000-0000-000000000923', recoveryConfirmationId = '00000000-0000-0000-0000-000000000924';
export const recoveryFixtureHash = (value: unknown) => createHash('sha256').update(recoveryCanonicalJson(value), 'utf8').digest('hex');
export const recoveryPrepareFixture = (): RecoveryPrepare => structuredClone(base.prepare);
export function recoveryProofFixture(status: RecoveryProof['status'] = 'VERIFIED_SCOPE', effectHash = base.proof.effect_hash): RecoveryProof {
  const p = structuredClone(base.proof) as RecoveryProof; p.status = status; p.effect_hash = status === 'VERIFIED_SCOPE' ? effectHash : null; p.reasons = status === 'VERIFIED_SCOPE' ? [] : ['TOOL_ONLY_UNPROVEN_SCOPE'];
  const { proof_hash: _old, ...compact } = p; void _old; p.proof_hash = recoveryFixtureHash(compact); return p;
}
export function recoveryPreviewFixture(body = recoveryPrepareFixture(), status: RecoveryProof['status'] = 'VERIFIED_SCOPE'): RecoveryPreview { return { simulation: true, bank_authority: false, grants_authority: false, preview_only: true, user_id: recoveryUser, original_request: body, proof: recoveryProofFixture(status), execution_effect: status === 'VERIFIED_SCOPE' ? structuredClone(base.effect) as RecoveryAction['effect'] : null, limitations: ['TOOL_ONLY_NOT_FINANCIAL_PROOF', 'CURRENT_INSTANT_DEADLINE_CANNOT_BE_EXTENDED_FOR_CONFIRMATION'] }; }
export function recoveryActionFixture(stage: 'PREPARED' | 'CONFIRMED' | 'UNKNOWN' | 'SETTLED' = 'PREPARED'): RecoveryAction {
  const effect = structuredClone(base.effect) as RecoveryAction['effect'], effectHash = recoveryFixtureHash(effect);
  return { simulation: true, user_id: recoveryUser, action_id: recoveryActionId, decision_run_id: recoveryRunId, status: stage === 'PREPARED' ? 'PLANNED' : stage === 'CONFIRMED' ? 'AUTHORIZED' : stage === 'UNKNOWN' ? 'UNKNOWN' : 'SUCCEEDED', autonomy_level: 'ASK_ONCE', effect, effect_hash: effectHash, prepared_at: effect.valid_from, as_of: effect.valid_from, prepared_validation: { simulation: true, financial_only: true, status: 'CONFIRMATION_REQUIRED', effect_hash: effectHash, baseline_boundary: boundaryFixture(), reasons: ['TOOL_ONLY_SYNTHETIC_NOT_FINANCIAL_PROOF'] }, bank_status: ['UNKNOWN', 'SETTLED'].includes(stage) ? 'SETTLED' : null, receipt: stage === 'SETTLED' ? { simulation: true, action_id: recoveryActionId, receipt_id: '00000000-0000-0000-0000-000000000925', bank_operation_id: '00000000-0000-0000-0000-000000000926', status: 'SETTLED', executed_cents: effect.amount_cents, fee_cents: 0, loss_cents: 0, posting_ids: ['00000000-0000-0000-0000-000000000927'], occurred_at: effect.valid_from, reconciled_at: null } : null };
}
export function recoveryLookupFixture(intent: RecoveryIntent, stage: 'NOT_FOUND' | 'PREPARED' | 'CONFIRMED' | 'UNKNOWN' | 'SETTLED' | 'MISSING_CONSENT' = 'PREPARED'): RecoveryLookup {
  const v: RecoveryLookup = { simulation: true, bank_authority: false, current_authority: false, not_found_is_final: false, user_id: recoveryUser, idempotency_key: intent.prepare_request.idempotency_key, status: stage === 'NOT_FOUND' ? 'NOT_FOUND_NOT_FINAL' : 'RECORDED', original_request: null, original_action_request: null, client_request_hash: null, server_request_hash: null, action: null, epoch_state: 'MISSING', original_consent: null, consent_is_current_authority: false };
  if (stage === 'NOT_FOUND') return v;
  const action = recoveryActionFixture(stage === 'MISSING_CONSENT' ? 'CONFIRMED' : stage), body = structuredClone(intent.prepare_request);
  const original = { intent: { kind: 'redeem_asset', position_id: body.position_id }, execution: { effect: action.effect, effect_hash: action.effect_hash }, prepared_validation: action.prepared_validation, ...(stage === 'PREPARED' ? {} : { confirmation_evidence_id: recoveryConfirmationId }), full_recovery_execution: { protocol: 'full-recovery-execution-v1', user_id: recoveryUser, epoch_id: recoveryEpoch, request: body, request_hash: recoveryFixtureHash(body), effect_hash: action.effect_hash, original_proof: recoveryProofFixture('VERIFIED_SCOPE', action.effect_hash) } };
  Object.assign(v, { original_request: body, original_action_request: original, client_request_hash: recoveryFixtureHash(body), server_request_hash: recoveryFixtureHash(original), epoch_state: 'OPEN', action });
  if (stage !== 'PREPARED' && stage !== 'MISSING_CONSENT') v.original_consent = { protocol: 'full-recovery-execution-v1', simulation: true, grants_authority: false, user_id: recoveryUser, epoch_id: recoveryEpoch, action_id: action.action_id, original_effect_hash: action.effect_hash, original_confirmation_evidence_id: recoveryConfirmationId, confirmed_at: action.prepared_at, principal_at_confirmation: { user_id: recoveryUser, role: 'USER', session_id: '00000000-0000-0000-0000-000000000928', issued_at: action.prepared_at, expires_at: new Date(Date.parse(action.prepared_at) + 900_000).toISOString(), authentication_source: 'LOCAL_SIGNED_SESSION', authenticated: true, human_identity_verified: false } };
  return v;
}
export function recoveryPlanningFixture(unknown = false) {
  const v = recoveryPlanFixture(unknown); v.user_id = recoveryUser; v.policy_id = base.prepare.policy_id;
  if (v.plan) { v.plan.user_id = recoveryUser; v.plan.policy_id = base.prepare.policy_id; v.plan.policy_version_id = base.prepare.expected_version_id; v.plan.candidates = [structuredClone(base.proof.candidate) as RecoveryProof['candidate']]; v.plan.lossless_steps = structuredClone(v.plan.candidates); v.plan.reasons = ['TOOL_ONLY_SYNTHETIC_PLAN']; v.catalogue_bindings = [{ product_id: base.proof.candidate.product_id, catalogue_version_id: base.proof.candidate.catalogue_version_id, product_record_hash: base.proof.candidate.product_record_hash, terms_digest: base.proof.candidate.terms_digest }]; }
  return v;
}
export const recoveryLocalSessionFixture = () => ({ simulation: true, bank_authority: false, confirms_financial_action: false, principal: { user_id: recoveryUser, role: 'USER', session_id: '00000000-0000-0000-0000-000000000928', issued_at: base.effect.valid_from, expires_at: new Date(Date.parse(base.effect.valid_from) + 900_000).toISOString(), authentication_source: 'LOCAL_SIGNED_SESSION', authenticated: true, human_identity_verified: false } });
