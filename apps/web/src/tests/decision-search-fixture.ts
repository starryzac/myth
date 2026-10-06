import type { DecisionSearchQuery, DecisionSearchResponse } from '../api/decision-search';

/** Synthetic DTO fixture only; no DB, original audit proof or financial outcome. */
export const searchOwner = '00000000-0000-0000-0000-000000000001';
export const searchAction = '00000000-0000-0000-0000-000000000003';
export const searchVersion = '00000000-0000-0000-0000-000000000006';
export const searchQuery: DecisionSearchQuery = { action_id: searchAction, action_key: null, policy_version_id: null, epoch_id: null, limit: 20, offset: 0 };
export function decisionSearchFixture(query: DecisionSearchQuery = searchQuery, partial = false): DecisionSearchResponse {
  return {
    schema_version: 'full-decision-search-v1', simulation: true, user_id: searchOwner, read_at: '2026-10-04T08:00:00+00:00', business_known_at: '2026-10-04T08:00:00+00:00',
    query: { ...query }, resolved_action_id: query.action_id ?? (query.action_key ? searchAction : null), version_family: query.policy_version_id ? 'MVP' : 'NONE', state: partial ? 'UNKNOWN' : 'SEARCHED', scope: 'CURRENT_PERSISTED_DECISION_ROWS',
    inventory: { actual_owned_decision_count: 2, known_decision_count: 1, selected_scope_count: 1, captured_scope_count: 1, source_bytes: 1234, action_link_count: 1, captured_action_link_count: 1, audit_link_count: 0, captured_audit_link_count: 0, verified_typed_count: partial ? 0 : 1, unverifiable_count: partial ? 1 : 0, returned_count: 1, row_limit: 1024, byte_limit: 67108864 },
    source_hash: 'a'.repeat(64), verified_match_count: partial ? 0 : 1, total_match_count: partial ? null : 1, next_offset: null,
    items: [{ run_id: '00000000-0000-0000-0000-000000000002', as_of: '2026-10-04T08:00:00+00:00', trigger_type: 'SYNTHETIC_TEST_ONLY', record_status: 'SUCCEEDED', action_ids: [searchAction], epoch_ids: query.epoch_id ? [query.epoch_id] : [], phase: 'PREPARE', snapshot_hash: 'b'.repeat(64), trace_hash: partial ? null : 'c'.repeat(64), completeness: partial ? 'LEGACY_PARTIAL' : 'COMPLETE', match_state: partial ? 'UNVERIFIABLE' : 'MATCHED', references: [{ kind: 'ACTION', identity: searchAction, pointer: '/subject_action_plan_id', relation: 'PERSISTED_FOREIGN_KEY' }, ...(query.policy_version_id ? [{ kind: 'MVP_POLICY_VERSION' as const, identity: query.policy_version_id, pointer: '/decision_trace/policies/0/id', relation: 'VERIFIED_TYPED_CAPTURE' as const }] : [])], issues: partial ? ['LEGACY_PARTIAL'] : [], grants_authority: false, financial_success_inferred: false }],
    issues: partial ? ['UNVERIFIABLE_ORIGINALS_INCLUDED_IN_DENOMINATOR'] : [], unsupported_families: ['FULL_POLICY_VERSION_SPECIALIZED_PROOF_FILTER', 'SEALED_ARCHIVE_DECISION_SEARCH', 'FREE_TEXT_OR_INFERRED_UUID_REFERENCES'], absence_is_final: false, grants_authority: false, financial_success_inferred: false, archived_records_searched: false, audit_chain_verified: false,
  };
}
