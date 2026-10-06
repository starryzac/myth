import { isRunId } from './decisions';
import { request } from './http';
import { object } from '../features/policy-form';

/** Explicit current production DTO until Root registers and generates this new route. */
export interface DecisionSearchQuery {
  action_id: string | null; action_key: string | null; policy_version_id: string | null; epoch_id: string | null; limit: number; offset: number;
}
export interface SearchReference { kind: 'ACTION' | 'MVP_POLICY_VERSION' | 'AUDIT_EPOCH'; identity: string; pointer: string; relation: 'PERSISTED_FOREIGN_KEY' | 'VERIFIED_TYPED_CAPTURE' | 'PERSISTED_AUDIT_LINK' }
export interface DecisionSearchItem {
  run_id: string; as_of: string; trigger_type: string; record_status: string; action_ids: string[]; epoch_ids: string[]; phase: string | null;
  snapshot_hash: string; trace_hash: string | null; completeness: 'COMPLETE' | 'LEGACY_PARTIAL' | 'UNSUPPORTED_VERSION' | 'INVALID';
  match_state: 'MATCHED' | 'UNVERIFIABLE'; references: SearchReference[]; issues: string[]; grants_authority: false; financial_success_inferred: false;
}
export interface DecisionSearchResponse {
  schema_version: 'full-decision-search-v1'; simulation: true; user_id: string; read_at: string; business_known_at: string;
  query: DecisionSearchQuery; resolved_action_id: string | null; version_family: 'NONE' | 'MVP' | 'FULL_UNSUPPORTED'; state: 'SEARCHED' | 'UNKNOWN';
  scope: 'CURRENT_PERSISTED_DECISION_ROWS'; inventory: {
    actual_owned_decision_count: number; known_decision_count: number; selected_scope_count: number; captured_scope_count: number;
    source_bytes: number; action_link_count: number; captured_action_link_count: number; audit_link_count: number; captured_audit_link_count: number;
    verified_typed_count: number; unverifiable_count: number; returned_count: number; row_limit: 1024; byte_limit: 67108864;
  };
  source_hash: string | null; verified_match_count: number; total_match_count: number | null; items: DecisionSearchItem[]; next_offset: number | null;
  issues: string[]; unsupported_families: string[]; absence_is_final: false; grants_authority: false; financial_success_inferred: false;
  archived_records_searched: false; audit_chain_verified: false;
}
const originals = new WeakMap<DecisionSearchResponse, string>();
export const getOriginalDecisionSearch = (result: DecisionSearchResponse) => originals.get(result) ?? null;
function need(condition: unknown): asserts condition { if (!condition) throw new Error('审计搜索响应未通过原身份、来源或分母校验'); }
const uuidOrNull = (value: unknown) => value === null || isRunId(value);
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const digest = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const timestamp = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.length <= 10000 && value.every((item) => typeof item === 'string');
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.length <= 1024 && value.every(isRunId) && new Set(value).size === value.length;

export function validateDecisionSearchQuery(query: DecisionSearchQuery): void {
  need(uuidOrNull(query.action_id) && uuidOrNull(query.policy_version_id) && uuidOrNull(query.epoch_id));
  need(query.action_key === null || (typeof query.action_key === 'string' && query.action_key.trim().length > 0 && query.action_key.length <= 160 && !query.action_key.includes('\0')));
  need(!(query.action_id !== null && query.action_key !== null));
  need(query.action_id !== null || query.action_key !== null || query.policy_version_id !== null);
  need(count(query.limit) && query.limit >= 1 && query.limit <= 100 && count(query.offset) && query.offset <= 100000);
}

export function parseDecisionSearch(value: unknown, owner: string, expected: DecisionSearchQuery, original?: string): DecisionSearchResponse {
  validateDecisionSearchQuery(expected);
  need(isRunId(owner) && object(value) && value.schema_version === 'full-decision-search-v1' && value.simulation === true && value.user_id === owner);
  need(value.scope === 'CURRENT_PERSISTED_DECISION_ROWS' && ['SEARCHED', 'UNKNOWN'].includes(String(value.state)));
  need(timestamp(value.read_at) && timestamp(value.business_known_at) && Date.parse(value.read_at) === Date.parse(value.business_known_at));
  need(object(value.query) && Object.keys(value.query).length === 6 && Object.entries(expected).every(([key, expectedValue]) => value.query && object(value.query) && value.query[key] === expectedValue));
  need(uuidOrNull(value.resolved_action_id));
  if (expected.action_id !== null) need(value.resolved_action_id === expected.action_id);
  if (expected.action_key !== null) need(isRunId(value.resolved_action_id));
  if (expected.action_id === null && expected.action_key === null) need(value.resolved_action_id === null);
  need(['NONE', 'MVP', 'FULL_UNSUPPORTED'].includes(String(value.version_family)) && (expected.policy_version_id === null) === (value.version_family === 'NONE'));
  for (const key of ['absence_is_final', 'grants_authority', 'financial_success_inferred', 'archived_records_searched', 'audit_chain_verified']) need(value[key] === false);
  const inventory = value.inventory;
  need(object(inventory));
  for (const key of ['actual_owned_decision_count', 'known_decision_count', 'selected_scope_count', 'captured_scope_count', 'source_bytes', 'action_link_count', 'captured_action_link_count', 'audit_link_count', 'captured_audit_link_count', 'verified_typed_count', 'unverifiable_count', 'returned_count']) need(count(inventory[key]));
  need(inventory.row_limit === 1024 && inventory.byte_limit === 67108864);
  const i = inventory as unknown as DecisionSearchResponse['inventory'];
  need(i.known_decision_count <= i.actual_owned_decision_count && i.selected_scope_count <= i.known_decision_count && i.captured_scope_count <= i.selected_scope_count && i.captured_scope_count <= 1024);
  need(i.verified_typed_count + i.unverifiable_count === i.captured_scope_count && i.captured_action_link_count <= i.action_link_count && i.captured_audit_link_count <= i.audit_link_count);
  need(count(value.verified_match_count) && (value.total_match_count === null || count(value.total_match_count)) && (value.next_offset === null || count(value.next_offset)));
  need(value.verified_match_count <= i.verified_typed_count);
  need(value.source_hash === null || digest(value.source_hash));
  need(strings(value.issues) && strings(value.unsupported_families));
  need(Array.isArray(value.items) && value.items.length <= expected.limit && value.items.length === i.returned_count);
  const runIds = new Set<string>();
  for (const item of value.items) {
    need(object(item) && isRunId(item.run_id) && !runIds.has(item.run_id)); runIds.add(item.run_id);
    need(timestamp(item.as_of) && Date.parse(item.as_of) <= Date.parse(value.business_known_at));
    need(typeof item.trigger_type === 'string' && typeof item.record_status === 'string' && (item.phase === null || typeof item.phase === 'string'));
    need(ids(item.action_ids) && ids(item.epoch_ids) && digest(item.snapshot_hash) && (item.trace_hash === null || digest(item.trace_hash)));
    need(['COMPLETE', 'LEGACY_PARTIAL', 'UNSUPPORTED_VERSION', 'INVALID'].includes(String(item.completeness)) && ['MATCHED', 'UNVERIFIABLE'].includes(String(item.match_state)));
    need(item.grants_authority === false && item.financial_success_inferred === false && strings(item.issues));
    need(Array.isArray(item.references) && item.references.length <= 20000);
    for (const ref of item.references) {
      need(object(ref) && ['ACTION', 'MVP_POLICY_VERSION', 'AUDIT_EPOCH'].includes(String(ref.kind)) && isRunId(ref.identity) && typeof ref.pointer === 'string');
      need(ref.relation === ({ ACTION: 'PERSISTED_FOREIGN_KEY', MVP_POLICY_VERSION: 'VERIFIED_TYPED_CAPTURE', AUDIT_EPOCH: 'PERSISTED_AUDIT_LINK' } as Record<string, string>)[String(ref.kind)]);
    }
    if (isRunId(value.resolved_action_id)) need(item.action_ids.includes(value.resolved_action_id));
    if (expected.epoch_id !== null) need(item.epoch_ids.includes(expected.epoch_id));
    if (item.completeness === 'COMPLETE') need(digest(item.trace_hash));
    if (expected.policy_version_id !== null && item.match_state === 'MATCHED') need(item.completeness === 'COMPLETE' && item.references.some((ref) => object(ref) && ref.kind === 'MVP_POLICY_VERSION' && ref.identity === expected.policy_version_id && ref.relation === 'VERIFIED_TYPED_CAPTURE'));
  }
  if (value.next_offset !== null) need(value.next_offset === expected.offset + expected.limit && i.returned_count === expected.limit);
  if (value.state === 'SEARCHED') need(i.captured_scope_count === i.selected_scope_count && i.unverifiable_count === 0 && i.captured_action_link_count === i.action_link_count && i.captured_audit_link_count === i.audit_link_count && i.source_bytes <= i.byte_limit && Math.max(i.selected_scope_count, i.action_link_count, i.audit_link_count) <= i.row_limit && value.issues.length === 0 && value.source_hash !== null && value.total_match_count === value.verified_match_count && value.version_family !== 'FULL_UNSUPPORTED' && value.items.every((item) => object(item) && item.match_state === 'MATCHED'));
  else need(value.total_match_count === null);
  const result = value as unknown as DecisionSearchResponse;
  if (original !== undefined) originals.set(result, original);
  return result;
}

export async function getDecisionSearch(query: DecisionSearchQuery, owner: string): Promise<DecisionSearchResponse> {
  validateDecisionSearchQuery(query); need(isRunId(owner));
  const canonical = { ...query, action_id: query.action_id?.toLowerCase() ?? null, policy_version_id: query.policy_version_id?.toLowerCase() ?? null, epoch_id: query.epoch_id?.toLowerCase() ?? null };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(canonical)) if (value !== null) params.set(key, String(value));
  return request(`/decision-search?${params}`, 'GET', undefined, (value, raw) => parseDecisionSearch(value, owner.toLowerCase(), canonical, raw));
}
