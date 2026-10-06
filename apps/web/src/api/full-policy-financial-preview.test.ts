import { afterEach, expect, test, vi } from 'vitest';
import { getOriginalFinancialPreviewResponse, parseFinancialChangePreview, parseFinancialPreviewBody, previewFullPolicyFinancialChange } from './full-policy-financial-preview';
import { financialBody, financialPolicy, financialPreviewFixture, financialProduct } from '../tests/financial-preview-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
import { spendingHash } from './spending-evidence';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('1098点/负差额/原持仓和未来UNKNOWN保持，原JSON字节留存', async () => {
  const source = await financialPreviewFixture(); const raw = ` \n${JSON.stringify(source)}\n`;
  const result = await parseFinancialChangePreview(source, financialPolicy, financialBody(), raw);
  expect(result.financial_impact.after?.calculation_trace).toHaveLength(1098); expect(result.financial_impact.delta_safe_idle_cents).toBe(-150);
  expect(result.financial_impact.positions[0]?.current_outstanding_principal_cents).toBe(0); expect(result.financial_impact.goals[0]?.future_allocation_cents).toBeNull(); expect(getOriginalFinancialPreviewResponse(result)).toBe(raw);
});
test('显式只读POST仅原版本和候选，无key/accepted/clock/user/金融金额注入', async () => {
  const source = await financialPreviewFixture(); const calls = installHttpFixture(() => source);
  await previewFullPolicyFinancialChange(financialPolicy, financialBody()); expect(calls).toEqual([{ method: 'POST', path: `/api/v1/full-policies/${financialPolicy}/financial-change-preview`, body: financialBody() }]);
  expect(() => parseFinancialPreviewBody({ ...financialBody(), accepted: true })).toThrow();
});
test('UNKNOWN保原curve但候选和全部delta=null，不能解释为零', async () => {
  const source = await financialPreviewFixture(true); expect((await parseFinancialChangePreview(source, financialPolicy, financialBody())).financial_impact.before?.calculation_trace).toHaveLength(1098);
  source.financial_impact.delta_safe_idle_cents = 0; await expect(parseFinancialChangePreview(source, financialPolicy, financialBody())).rejects.toThrow();
});
test.each(['ownerPolicy', 'version', 'candidate', 'grant', 'futureIncome', 'writes', 'delta', 'productDelta', 'productDenominator', 'frameMissing', 'phaseOrder', 'traceHash', 'goalFuture', 'positionOutstanding', 'positionsDenominator', 'unsafeMoney'])('%s篡改明确拒绝，不借状态字符串成功', async (kind) => {
  const value = await financialPreviewFixture(); const p = value.financial_impact;
  switch (kind) {
    case 'ownerPolicy': value.policy_id = '77000000-0000-0000-0000-000000000099'; break;
    case 'version': value.expected_version_id = value.epoch_id; break;
    case 'candidate': value.after_configuration.amount = { min_cents: 0, target_cents: 175, max_cents: 351 }; value.configuration_hash = await spendingHash(value.after_configuration); break;
    case 'grant': Object.assign(value, { grants_authority: true }); break;
    case 'futureIncome': Object.assign(p, { future_income_cents: 1 }); break;
    case 'writes': Object.assign(p, { writes_policy_or_bank: true }); break;
    case 'delta': p.delta_minimum_margin_cents = 0; break;
    case 'productDelta': p.delta_max_allocatable_by_product![financialProduct] = 0; break;
    case 'productDenominator': p.after!.max_allocatable_by_product = {}; break;
    case 'frameMissing': p.after!.calculation_trace.pop(); break;
    case 'phaseOrder': [p.before!.calculation_trace[0], p.before!.calculation_trace[1]] = [p.before!.calculation_trace[1]!, p.before!.calculation_trace[0]!]; break;
    case 'traceHash': p.after!.curve_hash = 'f'.repeat(64); break;
    case 'goalFuture': Object.assign(p.goals[0]!, { future_allocation_cents: 0 }); break;
    case 'positionOutstanding': p.positions[0]!.current_outstanding_principal_cents = 23009; break;
    case 'positionsDenominator': p.positions = []; break;
    case 'unsafeMoney': p.after!.safe_idle_cents = Number.MAX_SAFE_INTEGER + 1; break;
  }
  await expect(parseFinancialChangePreview(value, financialPolicy, financialBody())).rejects.toThrow();
});
test('完整curvehash也不能掩盖负保护/不等式金额内部冲突', async () => {
  const source = await financialPreviewFixture(); const p = source.financial_impact;
  p.after!.calculation_trace[0]!.protected_cents_by_reason.synthetic_reserve = -1;
  p.after!.curve_hash = await spendingHash({ protocol: 'hypothetical-full-curve-v1', input_hash: p.input_hash, trace: p.after!.calculation_trace });
  await expect(parseFinancialChangePreview(source, financialPolicy, financialBody())).rejects.toThrow();
});
