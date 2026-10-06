/** Synthetic HTTP source/provenance fixture; never an actual bank, DB or audit result. */
import type { EvidenceOriginal, FactsResponse, GraphResponse } from '../api/evidence';
export const evidenceUser = '10000000-0000-0000-0000-000000000001';
export const evidenceId = '10000000-0000-0000-0000-000000000021';
export const priorEvidenceId = '10000000-0000-0000-0000-000000000020';
export function evidenceOriginal(id = evidenceId): EvidenceOriginal {
  return { id, user_id: evidenceUser, evidence_level: 'USER_DECLARED', source_type: 'HTTP_UNIT_FIXTURE', source_ref: 'synthetic-source-original', content: { amount_cents: 1 }, content_hash: 'a'.repeat(64),
    valid_from: '2026-10-03T00:00:00Z', valid_to: null, observed_at: '2026-10-04T00:00:00Z', supersedes_id: id === evidenceId ? priorEvidenceId : null, status: id === evidenceId ? 'ACTIVE' : 'SUPERSEDED' };
}
export function factsFixture(): FactsResponse {
  return { schema_version: 'bitemporal-fact-view-v1', simulation: true, user_id: evidenceUser, valid_at: '2026-10-05T12:00:00Z', known_at: '2026-10-05T13:00:00Z',
    interval_semantics: 'VALID_FROM_INCLUSIVE_VALID_TO_EXCLUSIVE', state: 'VALID', groups: [{ source_type: 'HTTP_UNIT_FIXTURE', source_ref: 'synthetic-source-original', state: 'VALID', evidence_ids: [evidenceId], originals: [evidenceOriginal()], execution_authority: false }],
    issues: [], complete_within_registered_capacity: true, historical_status_reconstructed: false, execution_authority: false };
}
export function graphFixture(): GraphResponse {
  return { schema_version: 'persisted-evidence-graph-v1', simulation: true, user_id: evidenceUser, known_at: '2026-10-05T13:00:00Z', root: `EVIDENCE:${evidenceId}`,
    nodes: [evidenceOriginal(), evidenceOriginal(priorEvidenceId)].map((original) => ({ key: `EVIDENCE:${original.id}`, kind: 'EVIDENCE', id: original.id, original: { ...original } })),
    edges: [{ from: `EVIDENCE:${evidenceId}`, to: `EVIDENCE:${priorEvidenceId}`, relation: 'SUPERSEDES' }], issues: [], state: 'REFERENCES_RESOLVED', historical_status_reconstructed: false, execution_authority: false,
    uncovered: ['Public ingestion of new fact revisions', 'External bank anchor verification in this view', 'Exact historical mutable action/policy status'] };
}
