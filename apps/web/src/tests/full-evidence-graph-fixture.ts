/** SYNTHETIC_HTTP_FIXTURE_ONLY: no database, bank, browser, or acceptance proof. */
import { fullGraphTables } from '../api/full-evidence-graph';
import type { FullGraph } from '../api/full-evidence-graph';

export const fullGraphUser = '93000000-0000-4000-8000-000000000001';
export const fullGraphAccount = '93000000-0000-4000-8000-000000000002';
export const fullGraphTransaction = '93000000-0000-4000-8000-000000000003';
export const fullGraphTime = '2026-10-05T12:00:00Z';
export const fullGraphKnown = '2026-10-04T12:00:00Z';
const hash = 'a'.repeat(64);
const proof = (state: 'VERIFIED' | 'NOT_CHECKED' | 'UNKNOWN' = 'NOT_CHECKED'): FullGraph['audit_proof'] => ({ state, check: state === 'NOT_CHECKED' ? 'NAVIGATION_ONLY' : 'SYNTHETIC_SERVER_PROOF', detail: '合成客户端边界测试；没有实际金融证明', original_refs: ['SYNTHETIC_SOURCE'], grants_authority: false });
export function fullGraphFixture(mode: 'CURRENT' | 'HISTORICAL' | 'PARTIAL' | 'BANK_UNKNOWN' | 'LIMITED' = 'CURRENT'): FullGraph {
  const account = `ACCOUNT:${fullGraphAccount}`; const transaction = `TRANSACTION:${fullGraphTransaction}`;
  const data: FullGraph = {
    protocol: 'persisted-full-evidence-graph-v2', simulation: true, read_only: true, user_id: fullGraphUser, as_of: fullGraphTime, known_at: fullGraphTime,
    root: account, requested_root_kind: 'ACCOUNT', complete_registered_inventory: true, state: 'REFERENCES_RESOLVED',
    inventory: Object.entries(fullGraphTables).map(([kind, table]) => { const total = ['ACCOUNT', 'TRANSACTION'].includes(kind) ? 1 : 0; return { kind: kind as FullGraph['inventory'][number]['kind'], table, owner_scope: ['PRODUCT', 'PRODUCT_CATALOGUE'].includes(kind) ? 'SHARED_PRODUCT_CATALOGUE' : 'CURRENT_USER', actual_owned_count: total, known_count: total, captured_count: total, complete: true, captured_rows_hash: hash }; }),
    expected_node_count: 2, displayed_node_count: 2, expected_edge_count: 1,
    nodes: [
      { key: account, kind: 'ACCOUNT', id: fullGraphAccount, owner_scope: 'CURRENT_USER', known_at: fullGraphKnown, original: { id: fullGraphAccount, user_id: fullGraphUser, created_at: fullGraphKnown, balance_cents: 100007 }, row_hash: hash, source_classification: null, proof: proof(), historical_mutable_state_reconstructed: false, execution_authority: false },
      { key: transaction, kind: 'TRANSACTION', id: fullGraphTransaction, owner_scope: 'CURRENT_USER', known_at: fullGraphKnown, original: { id: fullGraphTransaction, user_id: fullGraphUser, account_id: fullGraphAccount, created_at: fullGraphKnown, amount_cents: 10001 }, row_hash: hash, source_classification: null, proof: proof(), historical_mutable_state_reconstructed: false, execution_authority: false },
    ], edges: [{ from_key: transaction, to_key: account, pointer: '/account_id', relation: 'PERSISTED_FOREIGN_KEY' }],
    issues: [], audit_proof: proof('VERIFIED'), bank_proof: proof('VERIFIED'), financial_success_inferred: false, grants_authority: false, performs_repair: false, input_hash: hash,
    limitations: ['SYNTHETIC_HTTP_FIXTURE_ONLY', '引用不是金融成功、授权或因果证明；当前可变值不用于重建过去'],
  };
  if (mode === 'HISTORICAL') {
    data.known_at = fullGraphKnown; data.state = 'UNKNOWN';
    for (const node of data.nodes) { node.original = null; node.row_hash = null; node.proof = { ...proof('UNKNOWN'), check: 'HISTORICAL_MUTABLE_VALUE' }; data.issues.push({ code: 'MUTABLE_HISTORICAL_VALUE_NOT_RECONSTRUCTED', reference: node.key, detail: '没有该过去时点的不可变原快照，内容不返回' }); }
  }
  if (mode === 'PARTIAL') {
    data.state = 'UNKNOWN'; data.complete_registered_inventory = false;
    const row = data.inventory.find((item) => item.kind === 'POSTING')!; row.actual_owned_count = 100001; row.known_count = 100001; row.captured_count = 100000; row.complete = false;
    data.issues.push({ code: 'TABLE_INVENTORY_NOT_COMPLETE', reference: row.table, detail: 'actual=100001,known=100001,captured=100000' });
  }
  if (mode === 'BANK_UNKNOWN') { data.state = 'UNKNOWN'; data.bank_proof = proof('UNKNOWN'); data.issues.push({ code: 'INDEPENDENT_INTEGRITY_NOT_FULLY_VERIFIED', reference: account, detail: '原银行账本未完整验真' }); }
  if (mode === 'LIMITED') {
    data.state = 'UNKNOWN'; data.expected_node_count = 2049; data.expected_edge_count = 20001;
    data.issues.push({ code: 'GRAPH_PRESENTATION_CAPACITY', reference: account, detail: '保留实际分母，展示预算不足' });
  }
  return data;
}
