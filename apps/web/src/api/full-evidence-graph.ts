import type { components } from '../../../../packages/contracts/schema';
import { object } from '../features/policy-form';
import { isEvidenceIdentity } from './evidence';
import { request } from './http';

export type FullGraph = components['schemas']['FullEvidenceGraph'];
export type FullGraphNode = FullGraph['nodes'][number];
export type FullGraphRootKind = FullGraph['requested_root_kind'];
export const fullGraphTables = {
  ACCOUNT: 'accounts', TRANSACTION: 'transactions', BILL: 'credit_card_bills', GOAL: 'goals', POSITION: 'asset_positions',
  BANK_OPERATION: 'bank_operations', BANK_REDEMPTION: 'simulated_bank_redemptions', POSTING: 'simulated_bank_postings', EXTERNAL_FACT: 'external_bank_facts',
  AUDIT_EPOCH: 'audit_epochs', AUDIT_EVENT: 'audit_events', AUDIT_SNAPSHOT: 'audit_subject_snapshots', FULL_POLICY: 'full_policies',
  FULL_POLICY_VERSION: 'full_policy_versions', FULL_POLICY_COMMAND: 'full_policy_commands', EVIDENCE: 'evidence_items', PROPOSAL: 'policy_proposals',
  POLICY: 'policies', POLICY_VERSION: 'policy_versions', DECISION: 'decision_runs', ACTION: 'action_plans', RECEIPT: 'action_receipts',
  PRODUCT: 'asset_products', PRODUCT_CATALOGUE: 'product_catalog_versions', USER: 'users', CONSTRAINT: 'decision_constraints',
  RESOURCE_CLAIM: 'action_resource_reservations', COMMAND_OUTBOX: 'command_outbox', COMMAND_INBOX: 'command_inbox', COMMAND_ATTEMPT: 'command_delivery_attempts',
  INTERVENTION_OUTBOX: 'intervention_outbox', INTERVENTION_INBOX: 'intervention_inbox', ASSET_PORTFOLIO: 'full_asset_execution_portfolios',
  ASSET_BATCH: 'full_asset_execution_batches', ASSET_CONSENT: 'full_asset_execution_consents',
} as const;
export const fullGraphKinds = [...Object.keys(fullGraphTables), 'FULL_GOAL_MODEL'] as FullGraphRootKind[];
const immutableKinds = new Set(['POLICY_VERSION', 'FULL_POLICY_VERSION', 'FULL_POLICY_COMMAND', 'RECEIPT', 'POSTING', 'AUDIT_EVENT', 'AUDIT_SNAPSHOT', 'PRODUCT_CATALOGUE', 'ASSET_PORTFOLIO', 'ASSET_BATCH', 'ASSET_CONSENT']);
const relations = new Set(['PERSISTED_FOREIGN_KEY', 'PERSISTED_TYPED_ARRAY', 'AUDIT_ORIGINAL_LINK', 'EXACT_AUDIT_SUBJECT_SNAPSHOT', 'CURRENT_IDENTITY_OF_RETAINED_SNAPSHOT', 'FULL_CONFIRMED_ORIGINAL_REFERENCE', 'FULL_REFERENCE_VERSION', 'ORIGINAL_CONFIRMATION_EVIDENCE', 'FULL_GOAL_MODEL_ORIGINAL_BINDING', 'ORIGINAL_FULL_PLANNING_CONFIRMATION', 'BANK_CLOSING_POSITION', 'ORIGINAL_EXECUTION_BINDING', 'ORIGINAL_USER_CONSENT', 'ORIGINAL_INTERVENTION_OBSERVATION']);
const originals = new WeakMap<object, string>();
export const getOriginalFullGraphResponse = (value: FullGraph): string | null => originals.get(value) ?? null;
export const isFullGraphKind = (value: unknown): value is FullGraphRootKind => typeof value === 'string' && fullGraphKinds.includes(value as FullGraphRootKind);
export function isFullGraphTime(value: unknown): value is string {
  if (typeof value !== 'string' || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(value) || Number(value.slice(0, 4)) === 0 || !Number.isFinite(Date.parse(value))) return false;
  const wall = value.slice(0, 19); const parsed = Date.parse(wall + 'Z');
  return Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 19) === wall;
}
function timeTicks(value: string): bigint {
  const fraction = value.match(/\.([0-9]+)(?:Z|[+-][0-9]{2}:[0-9]{2})$/)?.[1] ?? '';
  return BigInt(Date.parse(value)) * 1000000n - BigInt(fraction.padEnd(3, '0').slice(0, 3)) * 1000000n + BigInt(fraction.padEnd(9, '0'));
}
const hash = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const count = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0;
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((row) => typeof row === 'string');
const scope = (kind: string) => ['PRODUCT', 'PRODUCT_CATALOGUE'].includes(kind) ? 'SHARED_PRODUCT_CATALOGUE' : 'CURRENT_USER';
function check(value: unknown): asserts value { if (!value) throw new Error('完整证据图的身份、35类分母、历史原件或只读边界未通过校验'); }
function key(v: unknown): v is string { if (typeof v !== 'string') return false; const [kind, identity, extra] = v.split(':'); return extra === undefined && kind !== 'FULL_GOAL_MODEL' && isFullGraphKind(kind) && isEvidenceIdentity(identity) && identity === identity.toLowerCase(); }
function proof(v: unknown): void { check(object(v) && ['VERIFIED', 'UNKNOWN', 'NOT_APPLICABLE', 'NOT_CHECKED'].includes(String(v.state)) && typeof v.check === 'string' && typeof v.detail === 'string' && strings(v.original_refs) && v.grants_authority === false); }
function pointer(v: unknown, path: string): unknown {
  check(path.startsWith('/') && !/~(?![01])/u.test(path));
  let result = v;
  for (const token of path.slice(1).split('/').map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'))) {
    if (Array.isArray(result)) { check(/^(0|[1-9][0-9]*)$/.test(token)); result = result[Number(token)]; }
    else { check(object(result) && Object.hasOwn(result, token)); result = result[token]; }
  }
  return result;
}
export type FullGraphQueryBinding = { kind: FullGraphRootKind; identity: string; knownAt?: string; userId?: string };
export function parseFullEvidenceGraph(value: unknown, expected?: FullGraphQueryBinding, raw?: string): FullGraph {
  check(object(value) && value.protocol === 'persisted-full-evidence-graph-v2' && isEvidenceIdentity(value.user_id) && value.user_id === value.user_id.toLowerCase() && isFullGraphTime(value.as_of) && isFullGraphTime(value.known_at) && timeTicks(value.known_at) <= timeTicks(value.as_of));
  check(value.simulation === true && value.read_only === true && ['financial_success_inferred', 'grants_authority', 'performs_repair'].every((field) => value[field] === false) && isFullGraphKind(value.requested_root_kind) && key(value.root) && hash(value.input_hash) && strings(value.limitations));
  const rootKind = value.requested_root_kind === 'FULL_GOAL_MODEL' ? 'EVIDENCE' : value.requested_root_kind;
  check(value.root.startsWith(rootKind + ':') && ['UNKNOWN', 'REFERENCES_RESOLVED'].includes(String(value.state)) && typeof value.complete_registered_inventory === 'boolean');
  if (expected) check(value.root === `${expected.kind === 'FULL_GOAL_MODEL' ? 'EVIDENCE' : expected.kind}:${expected.identity.toLowerCase()}` && value.requested_root_kind === expected.kind && (expected.userId === undefined || value.user_id === expected.userId.toLowerCase()) && (expected.knownAt === undefined || isFullGraphTime(expected.knownAt) && timeTicks(value.known_at) === timeTicks(expected.knownAt)));
  check(Array.isArray(value.issues) && value.issues.every((issue) => object(issue) && ['code', 'reference', 'detail'].every((field) => typeof issue[field] === 'string')));
  proof(value.audit_proof); proof(value.bank_proof);
  check(Array.isArray(value.inventory) && value.inventory.length === Object.keys(fullGraphTables).length);
  const inventories = new Map<string, { captured: number; complete: boolean }>();
  for (const row of value.inventory) {
    check(object(row) && typeof row.kind === 'string' && Object.hasOwn(fullGraphTables, row.kind) && row.table === fullGraphTables[row.kind as keyof typeof fullGraphTables] && row.owner_scope === scope(row.kind) && !inventories.has(row.kind));
    check([row.actual_owned_count, row.known_count, row.captured_count].every(count) && (row.known_count as number) <= (row.actual_owned_count as number) && (row.captured_count as number) <= (row.known_count as number) && row.complete === (row.captured_count === row.known_count) && hash(row.captured_rows_hash));
    inventories.set(row.kind, { captured: row.captured_count as number, complete: row.complete as boolean });
  }
  const allComplete = [...inventories.values()].every((row) => row.complete);
  const invalidInventory = value.issues.some((issue) => object(issue) && ['REGISTERED_TABLE_DENOMINATOR_MISSING', 'ORIGINAL_OWNER_HASH_OR_KNOWLEDGE_DIFFERS', 'DUPLICATE_ORIGINAL_ID', 'UNREGISTERED_PERSISTED_TABLE', 'REGISTERED_TABLE_ABSENT', 'TABLE_INVENTORY_NOT_COMPLETE'].includes(String(issue.code)));
  check(value.complete_registered_inventory === (allComplete && !invalidInventory));
  check([value.expected_node_count, value.displayed_node_count, value.expected_edge_count].every(count) && Array.isArray(value.nodes) && value.nodes.length === value.displayed_node_count && value.nodes.length <= 2048 && (value.expected_node_count as number) >= value.nodes.length && Array.isArray(value.edges) && value.edges.length <= 20000 && (value.expected_edge_count as number) >= value.edges.length);
  const nodes = new Map<string, FullGraphNode>(); const perKind = new Map<string, number>();
  const historical = timeTicks(value.known_at) < timeTicks(value.as_of);
  for (const node of value.nodes) {
    check(object(node) && isFullGraphKind(node.kind) && node.kind !== 'FULL_GOAL_MODEL' && isEvidenceIdentity(node.id) && node.id === node.id.toLowerCase() && node.key === `${node.kind}:${node.id}` && !nodes.has(String(node.key)) && node.owner_scope === scope(node.kind) && isFullGraphTime(node.known_at) && timeTicks(node.known_at) <= timeTicks(value.known_at));
    check(node.historical_mutable_state_reconstructed === false && node.execution_authority === false && (node.source_classification === null || typeof node.source_classification === 'string')); proof(node.proof);
    const missingHistorical = historical && !immutableKinds.has(node.kind);
    if (missingHistorical) check(node.original === null && node.row_hash === null && object(node.proof) && node.proof.state === 'UNKNOWN' && node.proof.check === 'HISTORICAL_MUTABLE_VALUE' && value.issues.some((issue) => object(issue) && issue.code === 'MUTABLE_HISTORICAL_VALUE_NOT_RECONSTRUCTED' && issue.reference === node.key));
    else {
      check(object(node.original) && node.original.id === node.id && hash(node.row_hash));
      if (node.kind === 'USER') check(node.id === value.user_id);
      else if (scope(node.kind) === 'SHARED_PRODUCT_CATALOGUE') check(!Object.hasOwn(node.original, 'user_id'));
      else check(node.original.user_id === value.user_id);
      if (node.kind === 'EVIDENCE') check(node.source_classification === (node.original.source_type ?? null));
    }
    nodes.set(node.key as string, node as FullGraphNode); perKind.set(node.kind, (perKind.get(node.kind) ?? 0) + 1);
  }
  for (const [kind, total] of perKind) check(total <= (inventories.get(kind)?.captured ?? 0));
  const edges = new Set<string>();
  for (const edge of value.edges) {
    check(object(edge) && key(edge.from_key) && key(edge.to_key) && nodes.has(edge.from_key) && nodes.has(edge.to_key) && typeof edge.relation === 'string' && relations.has(edge.relation) && typeof edge.pointer === 'string' && edge.pointer.startsWith('/'));
    const identity = JSON.stringify([edge.from_key, edge.to_key, edge.relation, edge.pointer]); check(!edges.has(identity)); edges.add(identity);
    const from = nodes.get(edge.from_key)!; const to = nodes.get(edge.to_key)!;
    if (from.original !== null) {
      const originalValue = pointer(from.original, edge.pointer);
      if (edge.relation === 'EXACT_AUDIT_SUBJECT_SNAPSHOT') {
        check(from.kind === 'AUDIT_EVENT' && to.kind === 'AUDIT_SNAPSHOT' && object(originalValue) && to.original !== null && originalValue.kind === to.original.kind && originalValue.id === to.original.entity_id && originalValue.snapshot_hash === to.original.snapshot_hash && from.original.epoch_id === to.original.epoch_id);
      } else check(originalValue === to.id || edge.relation === 'FULL_REFERENCE_VERSION' && Array.isArray(originalValue) && originalValue.includes(to.id));
    }
  }
  const limited = value.expected_node_count !== value.displayed_node_count || value.expected_edge_count !== value.edges.length;
  if (limited) check(value.state === 'UNKNOWN' && value.issues.some((issue) => object(issue) && ['GRAPH_PRESENTATION_CAPACITY', 'REFERENCE_UNAVAILABLE'].includes(String(issue.code))));
  if (!nodes.has(value.root)) check(limited && value.issues.some((issue) => object(issue) && issue.code === 'GRAPH_PRESENTATION_CAPACITY'));
  if (value.requested_root_kind === 'FULL_GOAL_MODEL' && nodes.has(value.root)) check(nodes.get(value.root)!.source_classification === 'FULL_GOAL_MODEL_V1');
  if (value.state === 'REFERENCES_RESOLVED') check(value.issues.length === 0 && value.complete_registered_inventory && !limited && object(value.audit_proof) && value.audit_proof.state === 'VERIFIED' && object(value.bank_proof) && value.bank_proof.state === 'VERIFIED' && value.nodes.every((node) => object(node) && object(node.proof) && node.proof.state !== 'UNKNOWN'));
  else check(value.issues.length > 0);
  // SQL originals are opaque evidence, not JS monetary inputs. Large integers
  // remain exact in the retained HTTP text; no financial value is formatted here.
  const result = value as FullGraph; if (raw !== undefined) originals.set(result, raw); return result;
}
export function getFullEvidenceGraph(kind: FullGraphRootKind, identity: string, knownAt?: string, userId?: string): Promise<FullGraph> {
  check(isFullGraphKind(kind) && isEvidenceIdentity(identity) && (knownAt === undefined || isFullGraphTime(knownAt)) && (userId === undefined || isEvidenceIdentity(userId)));
  const query = knownAt === undefined ? '' : '?' + new URLSearchParams({ known_at: knownAt }).toString();
  return request<FullGraph>(`/evidence/full-graph/${encodeURIComponent(kind)}/${identity.toLowerCase()}${query}`, 'GET', undefined, (value, raw) => parseFullEvidenceGraph(value, { kind, identity, knownAt, userId }, raw));
}
