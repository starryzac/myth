/** SYNTHETIC_HTTP_FIXTURE_ONLY: invented DTOs for client boundary tests, not bank/PG/browser proof. */
import type { Reconciliation, ReconciliationAmount, ReconciliationIssue } from '../api/full-reconciliation';
export const reconcileUser = '91000000-0000-4000-8000-000000000001';
export const reconcileAccount = '91000000-0000-4000-8000-000000000002';
export const reconcilePosition = '91000000-0000-4000-8000-000000000003';
export const reconcileGoal = '91000000-0000-4000-8000-000000000004';
export const reconcileAction = '91000000-0000-4000-8000-000000000005';
export const reconcileOperation = '91000000-0000-4000-8000-000000000006';
export const reconcileDestination = '91000000-0000-4000-8000-000000000007';
const id = (n: number) => `91000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
export const reconcileTime = '2026-10-05T12:00:00Z';
const hash = 'a'.repeat(64);
export function reconcileIssue(kind: ReconciliationIssue['kind'] = 'MISSING'): ReconciliationIssue { return { code: `SYNTHETIC_${kind}`, source_ref: 'SYNTHETIC_SOURCE', kind, message: '合成客户端检查；没有真实金融证明' }; }
export function reconciliationFixture(mode: 'MATCHED' | 'UNKNOWN' | 'UNRESOLVED' | 'CORRUPT' | 'PARTIAL' = 'MATCHED'): Reconciliation {
  const post = (n: number, key: string, dimension: string, balance: number): Reconciliation['bank_postings'][number] => ({ posting_id: id(n), operation_id: null, redemption_id: null, ledger_key: key, ledger_dimension: dimension, leg_ref: 'OPENING', sequence_number: 1, balance_before_cents: 0, delta_cents: balance, balance_after_cents: balance, occurred_at: reconcileTime });
  const postings = [post(20, `CASH:${reconcileAccount}`, 'CASH', 100007), post(21, `POSITION:${reconcilePosition}`, 'POSITION', 67003), post(22, `GOAL_CASH:${reconcileGoal}`, 'GOAL_OWNERSHIP', 33006), post(23, `GOAL_PRINCIPAL:${reconcileGoal}`, 'GOAL_OWNERSHIP', 67003)];
  const amount = (entity: string, kind: ReconciliationAmount['kind'], n: number, application: number): ReconciliationAmount => { const p = postings.find((row) => row.posting_id === id(n))!; return { entity_id: entity, kind, application_cents: application, bank_cents: application, difference_cents: 0, state: 'MATCHED', bank_head: { posting_id: p.posting_id, ledger_key: p.ledger_key, sequence_number: p.sequence_number, occurred_at: p.occurred_at } }; };
  const data: Reconciliation = { schema_version: 'full-reconciliation-v1', user_id: reconcileUser, as_of: reconcileTime, simulation: true, read_only: true, bank_truth: 'INDEPENDENT_SIMULATED_BANK_LEDGER', grants_authority: false, executes_funds: false, repairs_performed: false, receipt_is_current_authority: false, economic_verified: false,
    state: 'MATCHED', manual_review_required: false, bank_ledger_verified: true, current_application_projection_matched: true, pending_application_projection_explained: false,
    audit: { schema_version: 'audit-verification-v1', simulation: true, user_id: reconcileUser, epoch_id: id(10), status: 'VALID', chain_status: 'VALID', reference_status: 'VALID', checkpoint_status: 'NOT_REQUESTED', actual_count: 1, expected_count: 1, actual_tail_id: id(11), actual_tail_hash: hash, expected_tail_id: id(11), expected_tail_hash: hash, verified_through_sequence: 1, errors: [], warnings: [], errors_truncated: false },
    inventory: ['accounts', 'asset_positions', 'goals', 'action_plans', 'bank_operations', 'simulated_bank_redemptions', 'action_receipts', 'simulated_bank_postings'].map((table) => ({ table, actual_count: table === 'simulated_bank_postings' ? postings.length : ['accounts', 'asset_positions', 'goals', 'action_plans'].includes(table) ? 1 : 0, captured_count: table === 'simulated_bank_postings' ? postings.length : ['accounts', 'asset_positions', 'goals', 'action_plans'].includes(table) ? 1 : 0, complete: true })),
    account_cash: [amount(reconcileAccount, 'ACCOUNT_CASH', 20, 100007)], position_principals: [amount(reconcilePosition, 'POSITION_PRINCIPAL', 21, 67003)], goal_ownership: [{ goal_id: reconcileGoal, account_id: reconcileAccount, allocated_cents: 100009, position_ids: [reconcilePosition], ownership_evidence_id: id(12), ownership_evidence_hash: hash, current_ownership_proof_verified: true, cash: amount(reconcileGoal, 'GOAL_CASH', 22, 33006), principal: amount(reconcileGoal, 'GOAL_PRINCIPAL', 23, 67003) }],
    actions: [{ action_id: reconcileAction, action_type: 'TRANSFER_INTERNAL', original_action_status: 'PLANNED', original_idempotency_key: 'synthetic-original-key', original_request_hash: hash, effect_hash: null, expected_amount_cents: 10001, expected_fee_cents: null, expected_loss_cents: null, actual_executed_cents: null, actual_fee_cents: null, actual_loss_cents: null, bank_operation_ids: [], bank_statuses: [], bank_request_hashes: [], posting_ids: [], receipt_ids: [], receipt_statuses: [], complete_settlement_legs_verified: false, service_receipt_verified: false, state: 'PREPARED_NO_BANK_OBSERVED', read_original_action_path: `/api/v1/actions/${reconcileAction}`, query_original_key_only: true, retry_or_repair_performed: false, issues: [] }],
    bank_postings: postings, issues: [], uncovered: [], input_hash: hash, limitations: ['SYNTHETIC_HTTP_NOT_FINANCIAL_PROOF', '旧产权维度/九类全部执行/硬重启仍未完整验收；不修账'] };
  const count = (table: string, total: number) => { const row = data.inventory.find((item) => item.table === table)!; row.actual_count = total; row.captured_count = total; };
  if (mode === 'UNRESOLVED') {
    const debit = { ...post(24, `CASH:${reconcileAccount}`, 'CASH', 90006), operation_id: reconcileOperation, sequence_number: 2, balance_before_cents: 100007, delta_cents: -10001, leg_ref: 'SOURCE' };
    const credit = { ...post(25, `CASH:${reconcileDestination}`, 'CASH', 10001), operation_id: reconcileOperation, delta_cents: 10001, leg_ref: 'DESTINATION' };
    postings.push(debit, credit); data.account_cash[0] = { ...amount(reconcileAccount, 'ACCOUNT_CASH', 24, 100007), bank_cents: 90006, difference_cents: 10001, state: 'DIFFERENCE' }; data.account_cash.push({ ...amount(reconcileDestination, 'ACCOUNT_CASH', 25, 0), bank_cents: 10001, difference_cents: -10001, state: 'DIFFERENCE' });
    data.state = 'MANUAL_REVIEW_REQUIRED'; data.manual_review_required = true; data.current_application_projection_matched = false; data.pending_application_projection_explained = true;
    data.actions[0] = { ...data.actions[0]!, original_action_status: 'UNKNOWN', effect_hash: hash, expected_fee_cents: 0, expected_loss_cents: 0, actual_executed_cents: 10001, actual_fee_cents: 0, actual_loss_cents: 0, bank_operation_ids: [reconcileOperation], bank_statuses: ['SETTLED'], bank_request_hashes: [hash], posting_ids: [debit.posting_id, credit.posting_id], complete_settlement_legs_verified: true, state: 'BANK_SETTLED_APPLICATION_UNRESOLVED', issues: [reconcileIssue('PENDING')] };
    data.issues = [reconcileIssue('DIFFERENCE'), ...data.actions[0]!.issues]; data.uncovered = [...data.actions[0]!.issues]; count('accounts', 2); count('bank_operations', 1); count('simulated_bank_postings', postings.length);
  }
  if (['UNKNOWN', 'CORRUPT', 'PARTIAL'].includes(mode)) {
    data.state = 'UNKNOWN'; data.bank_ledger_verified = false; data.current_application_projection_matched = false; data.issues = [reconcileIssue()]; data.uncovered = [...data.issues];
    for (const row of [...data.account_cash, ...data.position_principals, data.goal_ownership[0]!.cash, data.goal_ownership[0]!.principal]) { row.bank_cents = null; row.difference_cents = null; row.state = 'MISSING'; row.bank_head = null; }
    data.goal_ownership[0]!.current_ownership_proof_verified = false; data.goal_ownership[0]!.ownership_evidence_id = null; data.goal_ownership[0]!.ownership_evidence_hash = null;
  }
  if (mode === 'CORRUPT') { data.state = 'MANUAL_REVIEW_REQUIRED'; data.manual_review_required = true; data.bank_postings[0]!.delta_cents = 100008; data.actions[0]!.original_request_hash = 'stored-invalid-hash'; data.actions[0]!.state = 'MANUAL_REVIEW_REQUIRED'; data.actions[0]!.issues = [reconcileIssue('INTEGRITY')]; data.issues.push(...data.actions[0]!.issues); }
  if (mode === 'PARTIAL') { const row = data.inventory.find((item) => item.table === 'simulated_bank_postings')!; row.actual_count = 100001; row.complete = false; }
  return data;
}
