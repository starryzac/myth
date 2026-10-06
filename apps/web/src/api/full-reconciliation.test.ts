import { expect, test } from 'vitest';
import { getFullReconciliation, getOriginalReconciliationResponse, parseFullReconciliation } from './full-reconciliation';
import { reconcileAccount, reconcileIssue, reconciliationFixture, reconcileUser } from '../tests/full-reconciliation-fixture';
import { installHttpFixture } from '../tests/policy-fixture';

test('八表完整分母及准备行动仍原样保存，MATCHED不是行动已经执行', () => { const source = reconciliationFixture(), raw = JSON.stringify(source); const result = parseFullReconciliation(source, raw); expect(result.state).toBe('MATCHED'); expect(result.actions[0]!.state).toBe('PREPARED_NO_BANK_OBSERVED'); expect(result.actions[0]!.actual_executed_cents).toBeNull(); expect(result.economic_verified).toBe(false); expect(result.inventory).toHaveLength(8); expect(getOriginalReconciliationResponse(result)).toBe(raw); });
test('银行已结算两腿但无应用回执，原UNKNOWN/key和两方向差额保留；不假资金未变', () => { const result = parseFullReconciliation(reconciliationFixture('UNRESOLVED')); expect(result.state).toBe('MANUAL_REVIEW_REQUIRED'); expect(result.pending_application_projection_explained).toBe(true); expect(result.account_cash.map((row) => row.difference_cents)).toEqual([10001, -10001]); expect(result.actions[0]!.original_action_status).toBe('UNKNOWN'); expect(result.actions[0]!.actual_executed_cents).toBe(10001); expect(result.actions[0]!.complete_settlement_legs_verified).toBe(true); expect(result.actions[0]!.service_receipt_verified).toBe(false); expect(result.actions[0]!.receipt_ids).toEqual([]); });
test('独立银行原件缺失，现金/本金/归属各自null UNKNOWN，不补0', () => { const result = parseFullReconciliation(reconciliationFixture('UNKNOWN')); expect(result.state).toBe('UNKNOWN'); expect(result.account_cash[0]!.bank_cents).toBeNull(); expect(result.position_principals[0]!.difference_cents).toBeNull(); expect(result.goal_ownership[0]!.cash.bank_cents).toBeNull(); expect(result.goal_ownership[0]!.current_ownership_proof_verified).toBe(false); expect(result.uncovered).toHaveLength(1); });
test('人工报告中未核的不守恒银行腿与坏原hash保留用于诊断，不借此伪称验真', () => { const source = reconciliationFixture('CORRUPT'), raw = JSON.stringify(source); const result = parseFullReconciliation(source, raw); expect(result.state).toBe('MANUAL_REVIEW_REQUIRED'); expect(result.bank_ledger_verified).toBe(false); expect(result.bank_postings[0]!.delta_cents).toBe(100008); expect(result.actions[0]!.original_request_hash).toBe('stored-invalid-hash'); expect(getOriginalReconciliationResponse(result)).toBe(raw); });
test('容量不足保实际100001分母，捕获清单不伪完整', () => { const result = parseFullReconciliation(reconciliationFixture('PARTIAL')); const row = result.inventory.find((row) => row.table === 'simulated_bank_postings')!; expect(row.actual_count).toBe(100001); expect(row.captured_count).toBe(4); expect(row.complete).toBe(false); expect(result.state).toBe('UNKNOWN'); });
test.each(['economic_verified', 'grants_authority', 'executes_funds', 'repairs_performed', 'receipt_is_current_authority'])('即使MATCHED也拒绝%s升级', (field) => { const source = reconciliationFixture(); (source as unknown as Record<string, unknown>)[field] = true; expect(() => parseFullReconciliation(source)).toThrow(); });
test.each(['difference_sign', 'missing_becomes_zero', 'bank_without_head', 'unsafe', 'goal_sum', 'goal_id'])('金额/归属拒绝%s伪完整', (kind) => {
  const source = reconciliationFixture('UNRESOLVED');
  if (kind === 'difference_sign') source.account_cash[0]!.difference_cents = -10001;
  if (kind === 'missing_becomes_zero') { source.account_cash[0]!.bank_cents = null; source.account_cash[0]!.state = 'MISSING'; source.account_cash[0]!.difference_cents = 0; }
  if (kind === 'bank_without_head') source.account_cash[0]!.bank_head = null;
  if (kind === 'unsafe') source.account_cash[0]!.application_cents = Number.MAX_SAFE_INTEGER + 1;
  if (kind === 'goal_sum') source.goal_ownership[0]!.allocated_cents++;
  if (kind === 'goal_id') source.goal_ownership[0]!.principal.entity_id = reconcileAccount;
  expect(() => parseFullReconciliation(source)).toThrow();
});
test.each(['missing_table', 'duplicate_table', 'fake_complete', 'row_trim', 'false_matched', 'uncovered_trim', 'issue_trim'])('原分母/未证明清单拒绝%s', (kind) => {
  const source = reconciliationFixture('UNRESOLVED');
  if (kind === 'missing_table') source.inventory.pop();
  if (kind === 'duplicate_table') source.inventory[1]!.table = 'accounts';
  if (kind === 'fake_complete') source.inventory[0]!.actual_count++;
  if (kind === 'row_trim') source.bank_postings.pop();
  if (kind === 'false_matched') { source.state = 'MATCHED'; source.manual_review_required = false; }
  if (kind === 'uncovered_trim') source.uncovered = [];
  if (kind === 'issue_trim') { source.issues = []; source.uncovered = []; }
  expect(() => parseFullReconciliation(source)).toThrow();
});
test.each(['owner', 'false_valid', 'tail', 'truncated', 'through_sequence'])('完整AuditVerification拒绝%s矛盾', (kind) => {
  const source = reconciliationFixture(); if (kind === 'owner') source.audit.user_id = reconcileAccount; if (kind === 'false_valid') source.audit.reference_status = 'INCOMPLETE'; if (kind === 'tail') source.audit.expected_tail_hash = 'b'.repeat(64); if (kind === 'truncated') source.audit.errors_truncated = true; if (kind === 'through_sequence') source.audit.verified_through_sequence = 2; expect(() => parseFullReconciliation(source)).toThrow();
});
test.each(['path', 'key_replacement', 'false_receipt', 'statuses_denominator', 'lost_posting'])('原行动拒绝%s伪原件', (kind) => {
  const source = reconciliationFixture('UNRESOLVED'), action = source.actions[0]!;
  if (kind === 'path') action.read_original_action_path = 'https://arbitrary-host/repair';
  if (kind === 'key_replacement') action.retry_or_repair_performed = true as never;
  if (kind === 'false_receipt') action.service_receipt_verified = true;
  if (kind === 'statuses_denominator') action.bank_statuses = [];
  if (kind === 'lost_posting') action.posting_ids[0] = reconcileUser;
  expect(() => parseFullReconciliation(source)).toThrow();
});
test('已核银行声明不能配不守恒原腿；未核审计状态可作为UNKNOWN具体诊断保存', () => {
  const bad = reconciliationFixture(); bad.bank_postings[0]!.delta_cents++; expect(() => parseFullReconciliation(bad)).toThrow();
  const unknown = reconciliationFixture('UNKNOWN'); unknown.audit.status = 'LEGACY_UNAUDITED'; unknown.audit.chain_status = 'LEGACY_UNAUDITED'; unknown.audit.reference_status = 'LEGACY_UNAUDITED'; unknown.audit.expected_count = 3; expect(parseFullReconciliation(unknown).audit.status).toBe('LEGACY_UNAUDITED');
});
test('独立GET只空query/current，无修复输入、自给user/clock或资金POST', async () => { const calls = installHttpFixture(() => reconciliationFixture()); const result = await getFullReconciliation(); expect(result.state).toBe('MATCHED'); expect(calls).toEqual([{ method: 'GET', path: '/api/v1/reconciliation/current', body: undefined }]); });
test('失败HTTP保原诊断、成功坏JSON拒绝，不构造MATCHED', async () => { installHttpFixture(() => new Response(JSON.stringify({ error: { code: 'READ_FAILED', message: '原读取失败', request_id: 'synthetic-request' } }), { status: 409 })); await expect(getFullReconciliation()).rejects.toThrow('原读取失败'); installHttpFixture(() => ({ simulation: true, state: 'MATCHED' })); await expect(getFullReconciliation()).rejects.toThrow(); });
test('空但捕获完整的原范围可以MATCHED，不补造余额/行动', () => { const source = reconciliationFixture(); source.account_cash = []; source.position_principals = []; source.goal_ownership = []; source.actions = []; source.bank_postings = []; source.inventory.forEach((row) => { row.actual_count = 0; row.captured_count = 0; }); expect(parseFullReconciliation(source).actions).toEqual([]); });
test('原服务回执已核只能保原receipt标记，不当新资金授权', () => {
  const source = reconciliationFixture('UNRESOLVED'), action = source.actions[0]!; action.service_receipt_verified = true; action.state = 'SERVICE_RECEIPT_VERIFIED'; action.receipt_ids = [reconcileUser]; action.receipt_statuses = ['SUCCEEDED']; action.issues = []; source.inventory.find((row) => row.table === 'action_receipts')!.actual_count = 1; source.inventory.find((row) => row.table === 'action_receipts')!.captured_count = 1; source.issues = [reconcileIssue('DIFFERENCE')]; source.uncovered = [];
  const result = parseFullReconciliation(source); expect(result.actions[0]!.service_receipt_verified).toBe(true); expect(result.receipt_is_current_authority).toBe(false); expect(result.economic_verified).toBe(false);
});
