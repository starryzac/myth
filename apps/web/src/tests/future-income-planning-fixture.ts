/** Synthetic HTTP fixtures only; no PG, bank execution, browser or forecast evidence. */
import { createHash } from 'node:crypto';
import original from './future-income-planning-fixture.json';
import { futureCanonical } from '../api/future-income-planning';
import type { FutureIncomeCandidate, FutureIncomeCandidateBody, FutureIncomeConfirmBody, FutureIncomeConfirmation, FutureIncomeLookup, FutureIncomePlanning } from '../api/future-income-planning';
export const futureUser = original.user, futureEpoch = original.epoch;
export const futureFixtureHash = (value: unknown) => createHash('sha256').update(futureCanonical(value)).digest('hex');
function identity(key: string) {
  const basis = `future-income-command-v1:${futureUser}:${futureEpoch}:${key}`;
  const bytes = createHash('sha1').update(Buffer.from('effcb4a8172947d0870cf575c19e0ef2', 'hex')).update(basis).digest();
  bytes[6] = (bytes[6]! & 15) | 80; bytes[8] = (bytes[8]! & 63) | 128;
  const hex = bytes.subarray(0, 16).toString('hex');
  return { id: `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`, reference: `income-plan:${futureEpoch}:${futureFixtureHash({ basis })}` };
}
export function futureCandidateBody(): FutureIncomeCandidateBody { return structuredClone(original.candidate.original_request); }
export function futureCandidateFixture(body = futureCandidateBody()): FutureIncomeCandidate {
  const value = structuredClone(original.candidate) as unknown as FutureIncomeCandidate;
  const id = identity(body.idempotency_key);
  value.candidate_id = id.id; value.original_request = structuredClone(body); value.request_hash = futureFixtureHash(body);
  const evidence = value.original_evidence, content = evidence.content as Record<string, unknown>;
  evidence.id = id.id; evidence.source_ref = id.reference;
  content.candidate_id = id.id; content.original_request = structuredClone(body); content.request_hash = value.request_hash;
  value.evidence_hash = evidence.content_hash = futureFixtureHash(content);
  return value;
}
export function futureConfirmBody(candidate = futureCandidateFixture()): FutureIncomeConfirmBody { return { expected_epoch_id: futureEpoch, candidate_id: candidate.candidate_id, reviewed_candidate_hash: candidate.candidate_hash, accepted: true, idempotency_key: original.confirmation.original_request.idempotency_key }; }
export function futureConfirmationFixture(candidate: FutureIncomeCandidate, body = futureConfirmBody(candidate)): FutureIncomeConfirmation {
  const value = structuredClone(original.confirmation) as unknown as FutureIncomeConfirmation, id = identity(body.idempotency_key);
  value.confirmation_id = id.id; value.candidate_id = candidate.candidate_id; value.candidate_hash = candidate.candidate_hash; value.original_request = structuredClone(body); value.request_hash = futureFixtureHash(body);
  const evidence = value.original_evidence, content = evidence.content as Record<string, unknown>;
  evidence.id = id.id; evidence.source_ref = id.reference; content.confirmation_id = id.id; content.candidate_id = candidate.candidate_id; content.candidate_hash = candidate.candidate_hash; content.original_request = structuredClone(body); content.request_hash = value.request_hash; content.original_candidate_snapshot = structuredClone(candidate.original_evidence);
  value.evidence_hash = evidence.content_hash = futureFixtureHash(content); return value;
}
export function futureLookupFixture(kind: 'CANDIDATE' | 'CONFIRM', body: FutureIncomeCandidateBody | FutureIncomeConfirmBody = futureCandidateBody(), candidate = futureCandidateFixture(), notFound = false): FutureIncomeLookup {
  const value = structuredClone(original.candidate_lookup) as unknown as FutureIncomeLookup;
  value.idempotency_key = body.idempotency_key;
  if (notFound) { value.status = 'NOT_FOUND_NOT_FINAL'; value.command_kind = null; value.original_request = null; value.request_hash = null; value.candidate = null; value.confirmation = null; return value; }
  value.command_kind = kind; value.original_request = structuredClone(body); value.request_hash = futureFixtureHash(body);
  value.candidate = kind === 'CANDIDATE' ? futureCandidateFixture(body as FutureIncomeCandidateBody) : { ...structuredClone(candidate), state: 'USER_CONFIRMED' };
  value.confirmation = kind === 'CONFIRM' ? futureConfirmationFixture(candidate, body as FutureIncomeConfirmBody) : null; return value;
}
export function futurePlanningFixture(known = false): FutureIncomePlanning { return structuredClone(known ? original.known : original.empty) as unknown as FutureIncomePlanning; }
export function futureUnknownFixture(): FutureIncomePlanning {
  const value = futurePlanningFixture(); value.status = 'UNKNOWN'; value.issues = ['ORIGINAL_INCOME_SOURCE_UNKNOWN']; value.sources.status = 'UNKNOWN'; value.sources.complete = false; value.sources.issues = [...value.issues]; value.sources.sources = []; value.sources.captured_origin_count = 0; value.sources.original_origin_count = null; return value;
}
export const futureLocalSessionFixture = () => ({ simulation: true, bank_authority: false, confirms_financial_action: false, principal: structuredClone(original.candidate.original_evidence.content.actor) });
