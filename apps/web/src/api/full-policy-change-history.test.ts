import { afterEach, expect, test, vi } from 'vitest';
import { getOriginalHistoryPreviewResponse, parseHistoryChangePreview, previewFullPolicyHistoryFinancialChange } from './full-policy-change-history';
import { historyBinding, historyBody, historyFixture, historyUnknownFixture } from '../tests/financial-history-preview-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
import { seasonalHash } from './seasonal-reserve-adoptions';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

test('新历史v2协议原JSON、完整1098、负差额与原版本2保持', async () => {
  const original = historyFixture(); const raw = ` \n${JSON.stringify(original)}\n`;
  const value = await parseHistoryChangePreview(original, historyBinding(), historyBody(), raw);
  expect(value.protocol).toBe('full-policy-change-history-preview-v2'); expect(value.financial_impact.after?.calculation_trace).toHaveLength(1098);
  expect(value.financial_impact.delta_safe_idle_cents).toBe(-125); expect(value.financial_impact.history_proof?.captured_version_count).toBe(2);
  expect(value.financial_impact.goals[0]?.future_allocation_cents).toBeNull(); expect(value.financial_impact.positions[0]?.current_outstanding_principal_cents).toBe(25);
  expect(getOriginalHistoryPreviewResponse(value)).toBe(raw);
});
test('独立实际POST只有原expectedVersion/config；无accepted/key/owner/clock/GET额外管线', async () => {
  const calls = installHttpFixture(() => historyFixture()); await previewFullPolicyHistoryFinancialChange(historyBinding(), historyBody());
  expect(calls).toEqual([{ method: 'POST', path: `/api/v1/full-policies/${historyBinding().policyId}/financial-change-preview-history`, body: historyBody() }]);
});
test('UNKNOWN保留原curve和具体不可重建原因；missing历史原件也可诚实展示', async () => {
  const unknown = historyUnknownFixture(); unknown.financial_impact.history_proof = null;
  const result = await parseHistoryChangePreview(unknown, historyBinding(), historyBody());
  expect(result.financial_impact.before?.calculation_trace).toHaveLength(1098); expect(result.financial_impact.delta_safe_idle_cents).toBeNull();
  unknown.financial_impact.delta_safe_idle_cents = 0;
  await expect(parseHistoryChangePreview(unknown, historyBinding(), historyBody())).rejects.toThrow();
});
test.each(['protocol', 'oldCurveProtocol', 'owner', 'epoch', 'versionNumber', 'proofOwner', 'proofCurrentVersion', 'proofCount', 'proofAbsent', 'proofDigest', 'confirmation', 'grant', 'futureIncome', 'delta', 'product', 'point', 'phase', 'money'])('%s不能借状态或历史标签通过', async (caseId) => {
  const original = historyFixture(); const p = original.financial_impact; const proof = p.history_proof!;
  switch (caseId) {
    case 'protocol': Object.assign(original, { protocol: 'full-policy-change-preview-v1' }); break;
    case 'oldCurveProtocol': p.after!.curve_hash = await seasonalHash({ protocol: 'hypothetical-full-curve-v1', input_hash: p.input_hash, trace: p.after!.calculation_trace }); break;
    case 'owner': original.user_id = original.policy_id; break;
    case 'epoch': original.epoch_id = original.policy_id; break;
    case 'versionNumber': proof.captured_version_count = 1; break;
    case 'proofOwner': proof.user_id = original.policy_id; break;
    case 'proofCurrentVersion': proof.current_version_id = original.policy_id; break;
    case 'proofCount': proof.actual_version_count = 3; break;
    case 'proofAbsent': p.history_proof = null; break;
    case 'proofDigest': proof.source_digest = 'f'.repeat(64); break;
    case 'confirmation': Object.assign(proof.versions.at(-1)!.confirmation!, { accepted: false }); break;
    case 'grant': Object.assign(p, { grants_authority: true }); break;
    case 'futureIncome': Object.assign(p, { future_income_cents: 1 }); break;
    case 'delta': p.delta_minimum_margin_cents = 0; break;
    case 'product': p.after!.max_allocatable_by_product = {}; break;
    case 'point': p.after!.calculation_trace.pop(); break;
    case 'phase': [p.before!.calculation_trace[0], p.before!.calculation_trace[1]] = [p.before!.calculation_trace[1]!, p.before!.calculation_trace[0]!]; break;
    case 'money': p.after!.safe_idle_cents = Number.MAX_SAFE_INTEGER + 1; break;
  }
  // Keep the digest valid when exercising finite current binding/count checks.
  if (['proofOwner', 'proofCurrentVersion', 'proofCount', 'versionNumber', 'confirmation'].includes(caseId)) {
    const digestInput = { ...proof } as Record<string, unknown>; delete digestInput.source_digest; delete digestInput.status; delete digestInput.reasons;
    proof.source_digest = await seasonalHash(digestInput);
  }
  await expect(parseHistoryChangePreview(original, historyBinding(), historyBody())).rejects.toThrow();
});
test('当前v1宿主不走history分支；拒绝任意host/request额外身份及结果', async () => {
  await expect(previewFullPolicyHistoryFinancialChange({ ...historyBinding(), expectedVersionNumber: 1 }, historyBody())).rejects.toThrow();
  await expect(previewFullPolicyHistoryFinancialChange(historyBinding(), { ...historyBody(), accepted: true } as unknown as ReturnType<typeof historyBody>)).rejects.toThrow();
});
