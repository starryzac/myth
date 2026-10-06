import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';

export type Reconciliation = components['schemas']['FullReconciliationReport'];
export type ReconciliationAmount = components['schemas']['ReconciliationAmount'];
export type ReconciliationIssue = components['schemas']['ReconciliationIssue'];
export const reconciliationTables = ['accounts', 'asset_positions', 'goals', 'action_plans', 'bank_operations', 'simulated_bank_redemptions', 'action_receipts', 'simulated_bank_postings'] as const;
const issueKinds = ['DIFFERENCE', 'INTEGRITY', 'MISSING', 'UNSUPPORTED', 'PENDING'];
const actionStates = ['SERVICE_RECEIPT_VERIFIED', 'BANK_SETTLED_APPLICATION_UNRESOLVED', 'PENDING_BANK', 'PREPARED_NO_BANK_OBSERVED', 'NO_EFFECT_OBSERVED_NOT_FINAL', 'BANK_REJECTION_VERIFIED', 'UNKNOWN', 'MANUAL_REVIEW_REQUIRED'];
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
const digest = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const integer = (v: unknown, min = Number.MIN_SAFE_INTEGER, max = Number.MAX_SAFE_INTEGER): v is number => Number.isSafeInteger(v) && (v as number) >= min && (v as number) <= max;
const nullable = (v: unknown, predicate: (row: unknown) => boolean) => v === null || predicate(v);
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((row) => typeof row === 'string');
const ids = (v: unknown): v is string[] => Array.isArray(v) && v.every(uuid) && new Set(v).size === v.length;
function check(v: unknown): asserts v { if (!v) throw new Error('对账原分母、原键/回执、精确差額或只读边界未通过校验'); }
const originals = new WeakMap<object, string>();
export const getOriginalReconciliationResponse = (v: object) => originals.get(v) ?? null;
function sameJson(a: unknown, b: unknown): boolean { if (a === b) return true; if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((row, index) => sameJson(row, b[index])); return object(a) && object(b) && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((key) => Object.hasOwn(b, key) && sameJson(a[key], b[key])); }
function issues(v: unknown): asserts v is ReconciliationIssue[] { check(Array.isArray(v) && v.every((row) => object(row) && issueKinds.includes(row.kind as string) && ['code', 'message', 'source_ref'].every((key) => typeof row[key] === 'string'))); }
function uniqueRows(v: unknown[], key: string) { check(new Set(v.map((row) => { check(object(row) && uuid(row[key])); return row[key]; })).size === v.length); }
function amount(v: unknown, kind: ReconciliationAmount['kind'], id?: string): asserts v is ReconciliationAmount {
  check(object(v) && uuid(v.entity_id) && (id === undefined || v.entity_id === id) && v.kind === kind && ['MATCHED', 'DIFFERENCE', 'MISSING'].includes(v.state as string) && ['application_cents', 'bank_cents', 'difference_cents'].every((key) => nullable(v[key], integer)));
  if (v.bank_head !== null) check(object(v.bank_head) && uuid(v.bank_head.posting_id) && typeof v.bank_head.ledger_key === 'string' && integer(v.bank_head.sequence_number, 1) && time(v.bank_head.occurred_at));
  else check(v.bank_cents === null);
  if (v.application_cents === null || v.bank_cents === null) check(v.state === 'MISSING' && v.difference_cents === null);
  else { const difference = BigInt(v.application_cents as number) - BigInt(v.bank_cents as number); check(v.difference_cents !== null && difference === BigInt(v.difference_cents as number) && v.state === (difference === 0n ? 'MATCHED' : 'DIFFERENCE')); }
}
function audit(v: unknown, user: string): void {
  const states = ['VALID', 'INTEGRITY_ERROR', 'UNSUPPORTED_VERSION', 'LEGACY_UNAUDITED', 'INCOMPLETE'];
  check(object(v) && v.schema_version === 'audit-verification-v1' && v.simulation === true && v.user_id === user && nullable(v.epoch_id, uuid) && ['status', 'chain_status', 'reference_status'].every((key) => states.includes(v[key] as string)) && ['VERIFIED', 'NOT_REQUESTED', 'MISMATCH', 'UNAVAILABLE'].includes(v.checkpoint_status as string) && ['actual_count', 'expected_count', 'verified_through_sequence'].every((key) => integer(v[key], 0, 2147483647)) && (v.verified_through_sequence as number) <= (v.actual_count as number) && typeof v.errors_truncated === 'boolean');
  check(['actual_tail_id', 'expected_tail_id'].every((key) => nullable(v[key], uuid)) && ['actual_tail_hash', 'expected_tail_hash'].every((key) => nullable(v[key], digest)) && (v.actual_tail_id === null) === (v.actual_tail_hash === null) && (v.expected_tail_id === null) === (v.expected_tail_hash === null));
  for (const key of ['errors', 'warnings']) check(Array.isArray(v[key]) && v[key].every((d) => object(d) && typeof d.code === 'string' && typeof d.message === 'string' && nullable(d.sequence_number, (n) => integer(n, 0, 2147483647)) && nullable(d.event_id, uuid) && nullable(d.reference, (r) => typeof r === 'string')));
  if (v.status === 'VALID') check(v.chain_status === 'VALID' && v.reference_status === 'VALID' && ['VERIFIED', 'NOT_REQUESTED'].includes(v.checkpoint_status as string) && v.actual_count === v.expected_count && v.actual_tail_id === v.expected_tail_id && v.actual_tail_hash === v.expected_tail_hash && Array.isArray(v.errors) && v.errors.length === 0 && v.errors_truncated === false);
}
export function parseFullReconciliation(v: unknown, raw?: string): Reconciliation {
  check(object(v) && v.schema_version === 'full-reconciliation-v1' && uuid(v.user_id) && time(v.as_of) && v.simulation === true && v.read_only === true && v.bank_truth === 'INDEPENDENT_SIMULATED_BANK_LEDGER' && ['grants_authority', 'executes_funds', 'repairs_performed', 'receipt_is_current_authority', 'economic_verified'].every((key) => v[key] === false));
  check(['MATCHED', 'MANUAL_REVIEW_REQUIRED', 'UNKNOWN'].includes(v.state as string) && v.manual_review_required === (v.state === 'MANUAL_REVIEW_REQUIRED') && ['bank_ledger_verified', 'current_application_projection_matched', 'pending_application_projection_explained'].every((key) => typeof v[key] === 'boolean') && !(v.current_application_projection_matched && v.pending_application_projection_explained) && digest(v.input_hash) && strings(v.limitations)); assertMoneyFields(v); issues(v.issues); issues(v.uncovered); audit(v.audit, v.user_id);
  check(sameJson(v.uncovered, v.issues.filter((row) => ['MISSING', 'UNSUPPORTED', 'PENDING'].includes(row.kind))) && Array.isArray(v.inventory) && v.inventory.length === reconciliationTables.length);
  const inventory = new Map<string, { captured_count: number; complete: boolean }>();
  for (const row of v.inventory) { check(object(row) && reconciliationTables.includes(row.table as typeof reconciliationTables[number]) && !inventory.has(row.table as string) && integer(row.actual_count, 0) && integer(row.captured_count, 0, row.table === 'simulated_bank_postings' ? 100000 : 10000) && row.captured_count <= row.actual_count && row.complete === (row.captured_count === row.actual_count)); inventory.set(row.table as string, { captured_count: row.captured_count, complete: row.complete as boolean }); }
  check(Array.isArray(v.account_cash) && v.account_cash.length <= inventory.get('accounts')!.captured_count && Array.isArray(v.position_principals) && v.position_principals.length === inventory.get('asset_positions')!.captured_count && Array.isArray(v.goal_ownership) && v.goal_ownership.length === inventory.get('goals')!.captured_count && Array.isArray(v.actions) && v.actions.length === inventory.get('action_plans')!.captured_count && Array.isArray(v.bank_postings) && v.bank_postings.length === inventory.get('simulated_bank_postings')!.captured_count);
  const complete = [...inventory.values()].every((row) => row.complete); if (!complete) check(v.bank_ledger_verified === false && v.current_application_projection_matched === false);
  const allAmounts: ReconciliationAmount[] = [];
  for (const [rows, kind] of [[v.account_cash, 'ACCOUNT_CASH'], [v.position_principals, 'POSITION_PRINCIPAL']] as const) { uniqueRows(rows, 'entity_id'); for (const row of rows) { amount(row, kind); allAmounts.push(row); } }
  uniqueRows(v.goal_ownership, 'goal_id');
  for (const g of v.goal_ownership) {
    check(object(g) && uuid(g.goal_id) && nullable(g.account_id, uuid) && integer(g.allocated_cents, 0) && ids(g.position_ids) && nullable(g.ownership_evidence_id, uuid) && nullable(g.ownership_evidence_hash, digest) && (g.ownership_evidence_id === null) === (g.ownership_evidence_hash === null) && typeof g.current_ownership_proof_verified === 'boolean');
    if (g.current_ownership_proof_verified) check(g.ownership_evidence_id !== null && complete); amount(g.cash, 'GOAL_CASH', g.goal_id); amount(g.principal, 'GOAL_PRINCIPAL', g.goal_id); allAmounts.push(g.cash, g.principal);
    if (g.cash.application_cents !== null && g.principal.application_cents !== null) check(BigInt(g.cash.application_cents) + BigInt(g.principal.application_cents) === BigInt(g.allocated_cents));
    if (complete) check(g.position_ids.every((id) => (v.position_principals as ReconciliationAmount[]).some((p) => p.entity_id === id)));
  }
  uniqueRows(v.bank_postings, 'posting_id'); const postingById = new Map((v.bank_postings as Reconciliation['bank_postings']).map((posting) => [posting.posting_id, posting]));
  for (const p of v.bank_postings) { check(object(p) && nullable(p.operation_id, uuid) && nullable(p.redemption_id, uuid) && typeof p.ledger_key === 'string' && typeof p.ledger_dimension === 'string' && nullable(p.leg_ref, (r) => typeof r === 'string') && integer(p.sequence_number, 1) && integer(p.balance_before_cents, 0) && integer(p.balance_after_cents, 0) && integer(p.delta_cents) && time(p.occurred_at)); if (v.bank_ledger_verified) check(BigInt(p.balance_before_cents) + BigInt(p.delta_cents) === BigInt(p.balance_after_cents) && Date.parse(p.occurred_at) <= Date.parse(v.as_of)); }
  if (v.bank_ledger_verified) for (const a of allAmounts) if (a.bank_head !== null) { const head = a.bank_head; const original = postingById.get(head.posting_id); check(original && original.ledger_key === head.ledger_key && original.sequence_number === head.sequence_number && original.occurred_at === head.occurred_at && (a.bank_cents === null || original.balance_after_cents === a.bank_cents)); }
  uniqueRows(v.actions, 'action_id');
  for (const a of v.actions) {
    check(object(a) && ['action_type', 'original_action_status', 'original_idempotency_key'].every((key) => typeof a[key] === 'string') && typeof a.original_request_hash === 'string' && nullable(a.effect_hash, digest) && integer(a.expected_amount_cents, 0) && ['expected_fee_cents', 'expected_loss_cents', 'actual_executed_cents', 'actual_fee_cents', 'actual_loss_cents'].every((key) => nullable(a[key], (n) => integer(n, 0))) && ids(a.bank_operation_ids) && strings(a.bank_statuses) && Array.isArray(a.bank_request_hashes) && a.bank_request_hashes.every((hash) => typeof hash === 'string') && a.bank_operation_ids.length === a.bank_statuses.length && a.bank_operation_ids.length === a.bank_request_hashes.length && ids(a.posting_ids) && ids(a.receipt_ids) && strings(a.receipt_statuses) && a.receipt_ids.length === a.receipt_statuses.length && ['complete_settlement_legs_verified', 'service_receipt_verified'].every((key) => typeof a[key] === 'boolean') && actionStates.includes(a.state as string) && a.read_original_action_path === `/api/v1/actions/${a.action_id}` && a.query_original_key_only === true && a.retry_or_repair_performed === false); issues(a.issues);
    check(a.posting_ids.every((id) => postingById.has(id)));
    if (a.service_receipt_verified) check(digest(a.original_request_hash) && a.bank_request_hashes.every(digest) && a.complete_settlement_legs_verified === true && a.receipt_ids.length === 1 && a.bank_operation_ids.length === 1 && a.bank_statuses[0] === 'SETTLED');
    if (a.state === 'SERVICE_RECEIPT_VERIFIED') check(a.service_receipt_verified === true && a.actual_executed_cents !== null && a.actual_fee_cents !== null && a.actual_loss_cents !== null);
    if (a.state === 'BANK_SETTLED_APPLICATION_UNRESOLVED') check(a.service_receipt_verified === false && a.receipt_ids.length === 0 && a.bank_statuses.length === 1 && a.bank_statuses[0] === 'SETTLED');
    if (a.state === 'BANK_REJECTION_VERIFIED') check(a.service_receipt_verified === false && a.actual_executed_cents === 0 && a.actual_fee_cents === 0 && a.actual_loss_cents === 0 && a.posting_ids.length === 0 && a.receipt_ids.length === 0);
    if (a.state === 'PREPARED_NO_BANK_OBSERVED') check(a.bank_operation_ids.length === 0 && a.posting_ids.length === 0 && a.receipt_ids.length === 0 && a.actual_executed_cents === null);
    check(a.issues.every((issue) => (v.issues as ReconciliationIssue[]).some((row) => sameJson(row, issue))));
  }
  const manual = v.issues.some((row) => ['INTEGRITY', 'DIFFERENCE'].includes(row.kind)); const matched = complete && v.bank_ledger_verified && v.current_application_projection_matched && (v.audit as components['schemas']['AuditVerification']).status === 'VALID' && v.issues.length === 0;
  check(v.state === (manual ? 'MANUAL_REVIEW_REQUIRED' : matched ? 'MATCHED' : 'UNKNOWN'));
  const result = v as Reconciliation; if (raw !== undefined) originals.set(result, raw); return result;
}
export const getFullReconciliation = () => request<Reconciliation>('/reconciliation/current', 'GET', undefined, parseFullReconciliation);
