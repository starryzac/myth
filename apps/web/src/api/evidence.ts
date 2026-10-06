import { request } from './http';
import { object } from '../features/policy-form';

export const evidenceKinds = ['EVIDENCE', 'PROPOSAL', 'POLICY', 'POLICY_VERSION', 'DECISION', 'ACTION', 'RECEIPT'] as const;
export type EvidenceKind = typeof evidenceKinds[number];
export type EvidenceIssue = { code: string; [key: string]: unknown };
export type EvidenceOriginal = { id: string; user_id: string; evidence_level: string; source_type: string; source_ref: string;
  content: Record<string, unknown>; content_hash: string; valid_from: string; valid_to: string | null; observed_at: string; supersedes_id: string | null; status: string };
export type FactGroup = { source_type: string; source_ref: string; state: 'VALID' | 'CONFLICTED' | 'UNKNOWN'; evidence_ids: string[]; originals: EvidenceOriginal[]; execution_authority: false };
export type FactsResponse = { schema_version: 'bitemporal-fact-view-v1'; simulation: true; user_id: string; valid_at: string; known_at: string;
  interval_semantics: 'VALID_FROM_INCLUSIVE_VALID_TO_EXCLUSIVE'; state: FactGroup['state']; groups: FactGroup[]; issues: EvidenceIssue[];
  complete_within_registered_capacity: boolean; historical_status_reconstructed: false; execution_authority: false };
export type GraphNode = { key: string; kind: EvidenceKind; id: string; original: Record<string, unknown> };
export type GraphResponse = { schema_version: 'persisted-evidence-graph-v1'; simulation: true; user_id: string; known_at: string; root: string;
  nodes: GraphNode[]; edges: { from: string; to: string; relation: string }[]; issues: EvidenceIssue[]; state: 'UNKNOWN' | 'REFERENCES_RESOLVED';
  execution_authority: false; historical_status_reconstructed: false; uncovered: string[] };
export type FactQuery = { valid_at?: string; known_at?: string; source_type?: string; source_ref?: string };
const originals = new WeakMap<FactsResponse | GraphResponse, string>();
export const getOriginalEvidenceResponse = (value: FactsResponse | GraphResponse) => originals.get(value) ?? null;
export const isEvidenceKind = (value: unknown): value is EvidenceKind => evidenceKinds.includes(value as EvidenceKind);
export const isEvidenceIdentity = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
export const isEvidenceTime = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((entry) => typeof entry === 'string');
const states = ['VALID', 'CONFLICTED', 'UNKNOWN'];
const relationKinds = ['SUPPORTED_BY', 'USES_VERSION', 'SUPERSEDES', 'CONFIRMED_AS', 'DERIVED_FROM', 'GENERATED_BY', 'EXECUTION_OF', 'HAS_VERSION'];
function requireValue(condition: unknown): asserts condition { if (!condition) throw new Error('事实与证据响应的身份、原件关系或只读边界未通过校验'); }
function issues(value: unknown): value is EvidenceIssue[] { return Array.isArray(value) && value.every((entry) => object(entry) && typeof entry.code === 'string'); }
function envelope(value: unknown): asserts value is Record<string, unknown> & { issues: EvidenceIssue[] } {
  requireValue(object(value) && value.simulation === true && isEvidenceIdentity(value.user_id) && value.execution_authority === false && value.historical_status_reconstructed === false && issues(value.issues));
}
export function parseFacts(value: unknown, originalText?: string): FactsResponse {
  envelope(value);
  requireValue(value.schema_version === 'bitemporal-fact-view-v1' && isEvidenceTime(value.valid_at) && isEvidenceTime(value.known_at) &&
    value.interval_semantics === 'VALID_FROM_INCLUSIVE_VALID_TO_EXCLUSIVE' && states.includes(String(value.state)) &&
    typeof value.complete_within_registered_capacity === 'boolean' && Array.isArray(value.groups) && value.groups.length <= 10000);
  const seen = new Set<string>(); let count = 0;
  for (const group of value.groups) {
    requireValue(object(group) && typeof group.source_type === 'string' && typeof group.source_ref === 'string' && states.includes(String(group.state)) &&
      group.execution_authority === false && Array.isArray(group.evidence_ids) && group.evidence_ids.every(isEvidenceIdentity) && Array.isArray(group.originals) && group.originals.length > 0);
    const groupKey = JSON.stringify([group.source_type, group.source_ref]); requireValue(!seen.has(groupKey)); seen.add(groupKey);
    const ids = new Set<string>();
    for (const row of group.originals) {
      requireValue(object(row) && isEvidenceIdentity(row.id) && row.user_id === value.user_id && typeof row.evidence_level === 'string' &&
        row.source_type === group.source_type && row.source_ref === group.source_ref && object(row.content) && typeof row.content_hash === 'string' && /^[0-9a-f]{64}$/.test(row.content_hash) &&
        isEvidenceTime(row.valid_from) && (row.valid_to === null || isEvidenceTime(row.valid_to)) && isEvidenceTime(row.observed_at) &&
        (row.supersedes_id === null || isEvidenceIdentity(row.supersedes_id)) && typeof row.status === 'string' && !ids.has(row.id));
      requireValue(Date.parse(String(row.observed_at)) <= Date.parse(String(value.known_at)) && Date.parse(String(row.valid_from)) <= Date.parse(String(value.valid_at)) &&
        (row.valid_to === null || Date.parse(String(value.valid_at)) < Date.parse(String(row.valid_to))));
      ids.add(row.id); count += 1;
    }
    requireValue(group.evidence_ids.length === ids.size && group.evidence_ids.every((id) => ids.has(String(id))));
  }
  requireValue(count <= 10000 && (value.state !== 'VALID' || value.issues.length === 0 && value.groups.length > 0 && value.complete_within_registered_capacity && value.groups.every((group) => object(group) && group.state === 'VALID')));
  const result = value as unknown as FactsResponse; if (originalText !== undefined) originals.set(result, originalText); return result;
}
function nodeKey(value: unknown): value is string {
  if (typeof value !== 'string') return false; const [kind, id, extra] = value.split(':'); return extra === undefined && isEvidenceKind(kind) && isEvidenceIdentity(id);
}
export function parseEvidenceGraph(value: unknown, expectedRoot?: string, originalText?: string): GraphResponse {
  envelope(value);
  requireValue(value.schema_version === 'persisted-evidence-graph-v1' && isEvidenceTime(value.known_at) && nodeKey(value.root) &&
    (expectedRoot === undefined || value.root === expectedRoot) && ['UNKNOWN', 'REFERENCES_RESOLVED'].includes(String(value.state)) &&
    Array.isArray(value.nodes) && value.nodes.length > 0 && value.nodes.length <= 512 && Array.isArray(value.edges) && strings(value.uncovered));
  const keys = new Set<string>();
  const nodes = new Map<string, GraphNode>();
  for (const node of value.nodes) {
    requireValue(object(node) && isEvidenceKind(node.kind) && isEvidenceIdentity(node.id) && node.key === `${node.kind}:${node.id}` &&
      object(node.original) && node.original.id === node.id && node.original.user_id === value.user_id && !keys.has(String(node.key)));
    keys.add(String(node.key));
    nodes.set(String(node.key), node as unknown as GraphNode);
  }
  requireValue(keys.has(String(value.root)));
  const edges = new Set<string>();
  for (const edge of value.edges) {
    requireValue(object(edge) && nodeKey(edge.from) && nodeKey(edge.to) && keys.has(edge.from) && relationKinds.includes(String(edge.relation)));
    const edgeKey = JSON.stringify([edge.from, edge.to, edge.relation]); requireValue(!edges.has(edgeKey)); edges.add(edgeKey);
    const source = nodes.get(edge.from)!; const [targetKind, targetId] = edge.to.split(':'); const raw = source.original;
    const pointsTo = (field: string) => raw[field] === targetId;
    const listPointsTo = (field: string) => Array.isArray(raw[field]) && raw[field].includes(targetId);
    const matching = edge.relation === 'SUPPORTED_BY' ? targetKind === 'EVIDENCE' && listPointsTo('evidence_ids')
      : edge.relation === 'USES_VERSION' ? targetKind === 'POLICY_VERSION' && (listPointsTo('policy_version_ids') || source.kind === 'ACTION' && pointsTo('policy_version_id'))
        : edge.relation === 'SUPERSEDES' ? source.kind === 'EVIDENCE' && targetKind === 'EVIDENCE' && pointsTo('supersedes_id')
          : edge.relation === 'CONFIRMED_AS' ? source.kind === 'PROPOSAL' && targetKind === 'POLICY' && pointsTo('confirmed_policy_id')
            : edge.relation === 'DERIVED_FROM' ? source.kind === 'DECISION' && targetKind === 'DECISION' && pointsTo('parent_run_id')
              : edge.relation === 'GENERATED_BY' ? source.kind === 'ACTION' && targetKind === 'DECISION' && pointsTo('decision_run_id')
                : edge.relation === 'EXECUTION_OF' ? source.kind === 'RECEIPT' && targetKind === 'ACTION' && pointsTo('action_plan_id')
                  : source.kind === 'POLICY' && targetKind === 'POLICY_VERSION' && (!nodes.has(edge.to) || nodes.get(edge.to)!.original.policy_id === source.id);
    requireValue(matching);
    if (!keys.has(edge.to)) {
      const [kind, id] = edge.to.split(':');
      requireValue(value.state === 'UNKNOWN' && value.issues.some((issue) => issue.code === 'GRAPH_CAPACITY_EXCEEDED' || ['BROKEN_REFERENCE', 'REFERENCE_NOT_KNOWN'].includes(issue.code) && issue.kind === kind && issue.id === id));
    }
  }
  requireValue(value.state !== 'REFERENCES_RESOLVED' || value.issues.length === 0);
  const result = value as unknown as GraphResponse; if (originalText !== undefined) originals.set(result, originalText); return result;
}
function queryString(query: FactQuery): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    requireValue(['valid_at', 'known_at', 'source_type', 'source_ref'].includes(key));
    if (value === undefined || value === '') continue;
    if (key === 'valid_at' || key === 'known_at') requireValue(isEvidenceTime(value));
    else requireValue(typeof value === 'string' && value.length <= (key === 'source_type' ? 48 : 160));
    search.set(key, value);
  }
  return search.size ? `?${search.toString()}` : '';
}
export function getFacts(query: FactQuery = {}): Promise<FactsResponse> {
  const frozenQuery = { ...query };
  return request<FactsResponse>(`/evidence/facts${queryString(frozenQuery)}`, 'GET', undefined, (value, originalText) => {
    const result = parseFacts(value, originalText);
    requireValue((!frozenQuery.valid_at || Date.parse(result.valid_at) === Date.parse(frozenQuery.valid_at)) &&
      (!frozenQuery.known_at || Date.parse(result.known_at) === Date.parse(frozenQuery.known_at)) && result.groups.every((group) =>
        (!frozenQuery.source_type || group.source_type === frozenQuery.source_type) && (!frozenQuery.source_ref || group.source_ref === frozenQuery.source_ref)));
    return result;
  });
}
export function getEvidenceGraph(kind: EvidenceKind, identity: string, knownAt?: string): Promise<GraphResponse> {
  requireValue(isEvidenceKind(kind) && isEvidenceIdentity(identity)); const id = identity.toLowerCase();
  return request<GraphResponse>(`/evidence/graph/${kind}/${id}${queryString({ known_at: knownAt })}`, 'GET', undefined,
    (value, originalText) => {
      const result = parseEvidenceGraph(value, `${kind}:${id}`, originalText);
      requireValue(!knownAt || Date.parse(result.known_at) === Date.parse(knownAt)); return result;
    });
}
