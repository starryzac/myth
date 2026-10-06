import { expect, test, vi } from 'vitest';
import { getFullAssetPlan, getFullRecoveryPlan, getOriginalFullProductsResponse, getProductCatalog, getProductPositions, groupPrincipalDates, parseAssetOptions, parseFullAssetPlan, parseFullRecoveryPlan, parsePositions, parseProductCatalog, productBoundaryPoints } from './full-products';
import { annualFixture } from '../tests/annual-fixture';
import { assetOptions, assetPlanFixture, catalogueId, productCatalogue, productPositions, recoveryPlanFixture, recoveryPolicyId } from '../tests/full-products-fixture';
import { fullPolicyId } from '../tests/full-policy-fixture';
import { installHttpFixture } from '../tests/policy-fixture';

test('不可变目录保留原字段与原字节；当前源漂移仍是UNKNOWN历史，不丢旧版本', () => {
  const source = productCatalogue(true), raw = JSON.stringify(source); const parsed = parseProductCatalog(source, raw);
  expect(parsed.state).toBe('UNKNOWN'); expect(parsed.versions[0]!.id).toBe(catalogueId); expect(parsed.versions[0]!.current_source_matched).toBe(false); expect(getOriginalFullProductsResponse(parsed)).toBe(raw);
});
test.each(['authority', 'binding', 'duplicate', 'unsafe_money', 'drift_claims_registered'])('目录拒绝%s伪证明', (kind) => {
  const source = productCatalogue(); const original = source.versions[0]!;
  if (kind === 'authority') original.bank_authority = true as never;
  if (kind === 'binding') original.original_product.id = recoveryPolicyId;
  if (kind === 'duplicate') source.versions.push(structuredClone(original));
  if (kind === 'unsafe_money') original.original_product.minimum_purchase_cents = Number.MAX_SAFE_INTEGER + 1;
  if (kind === 'drift_claims_registered') original.current_source_matched = false;
  expect(() => parseProductCatalog(source)).toThrow();
});
test('原持仓null日期不推出现金或名称期限，完整原响应可取回', () => {
  const source = productPositions(), raw = JSON.stringify(source); const data = parsePositions(source, raw); expect(data.items[0]!.available_at).toBeNull();
  expect(groupPrincipalDates(data.items, (row) => row.available_at)).toEqual([{ timestamp: null, rows: data.items }]); expect(getOriginalFullProductsResponse(data)).toBe(raw);
});
test('日期分桶保持每个原成员；未知最后，非时区日期拒绝', () => {
  const rows = [{ at: null }, { at: '2026-11-05T00:00:00Z' }, { at: '2026-10-05T00:00:00Z' }, { at: '2026-10-05T00:00:00Z' }];
  const buckets = groupPrincipalDates(rows, (row) => row.at); expect(buckets.map((row) => row.rows.length)).toEqual([2, 1, 1]); expect(buckets.flatMap((row) => row.rows)).toHaveLength(4); expect(buckets[2]!.timestamp).toBeNull(); expect(() => groupPrincipalDates([{ at: '2026-10-05' }], (row) => row.at)).toThrow();
});
test('365未来日期加日0只有1098阶段点；未知0点不代表零金额', () => {
  expect(productBoundaryPoints(annualFixture().annual_projection)).toBe(1098); const unknown = annualFixture(true).annual_projection; expect(productBoundaryPoints(unknown)).toBe(0); expect(unknown.safe_idle_cents).toBeNull();
});
test.each(['missing', 'duplicate', 'phase', 'unsafe'])('完整原365曲线拒绝%s阶段/金额', (kind) => {
  const boundary = annualFixture().annual_projection;
  if (kind === 'missing') boundary.calculation_trace.pop();
  if (kind === 'duplicate') boundary.calculation_trace[1] = structuredClone(boundary.calculation_trace[0]!);
  if (kind === 'phase') boundary.calculation_trace[3]!.date = '2026-10-07';
  if (kind === 'unsafe') boundary.calculation_trace[0]!.cash_cents = Number.MAX_SAFE_INTEGER + 1;
  expect(() => productBoundaryPoints(boundary)).toThrow();
});
test.each([{ ...assetOptions(), comparison_days: 366 }, { ...assetOptions(), max_components: 5 }, { ...assetOptions(), funds_use_date: '2026-02-30' }, { ...assetOptions(), max_turnover_cents: 1.01 }, { ...assetOptions(), amount_cents: 1 }])('规划约束拒绝窗口/金额/额外权力输入 %#', (source) => { expect(() => parseAssetOptions(source)).toThrow(); });
test('today原批次条件金额与原资金账户守恒，未声称多期最优或授权', () => {
  const source = assetPlanFixture(); const parsed = parseFullAssetPlan(source, fullPolicyId, assetOptions(), JSON.stringify(source)); expect(parsed.allocation!.total_purchase_cents).toBe(67003); expect(parsed.allocation!.batches[0]!.cash_uses[0]!.amount_cents).toBe(67003); expect(parsed.allocation!.future_income_included_cents).toBe(0); expect(parsed.execution_support).toBe('NOT_IMPLEMENTED'); expect(getOriginalFullProductsResponse(parsed)).toBe(JSON.stringify(source));
});
test('原UNKNOWN资产结果没有虚构allocation，原null保留', () => { const source = assetPlanFixture(assetOptions(), true); expect(parseFullAssetPlan(source, fullPolicyId, assetOptions()).allocation).toBeNull(); });
test.each(['owner', 'options', 'cash_sum', 'term', 'future_purchase', 'fake_ladder', 'count', 'bank_grant', 'future_income'])('资产响应拒绝%s原绑定/资金边界差异', (kind) => {
  const options = { ...assetOptions(), mode: 'FIXED_LADDER' as const }; const source = assetPlanFixture(options); const a = source.allocation!;
  if (kind === 'owner') source.policy_id = recoveryPolicyId;
  if (kind === 'options') source.planning_constraints.comparison_days = 91;
  if (kind === 'cash_sum') a.batches[0]!.cash_uses[0]!.amount_cents--;
  if (kind === 'term') a.candidates[0]!.terms_digest = 'f'.repeat(64);
  if (kind === 'future_purchase') a.batches[0]!.purchase_at = '2026-10-06T12:00:00Z';
  if (kind === 'fake_ladder') a.ladder_status = 'MATCHED_MULTI_MATURITY';
  if (kind === 'count') a.purchase_count = 2;
  if (kind === 'bank_grant') a.bank_authority = true as never;
  if (kind === 'future_income') a.future_income_included_cents = 1 as never;
  expect(() => parseFullAssetPlan(source, fullPolicyId, options)).toThrow();
});
test('既有有损报价没有simulation字段，仍原样读；无损方案不吞入有损候选', () => {
  const source = recoveryPlanFixture(); expect(source.plan!.candidates[0]!.original_quote).not.toHaveProperty('simulation');
  const parsed = parseFullRecoveryPlan(source, recoveryPolicyId, JSON.stringify(source)); expect(parsed.plan!.candidates[0]!.independent_loss_cents).toBe(167); expect(parsed.plan!.lossless_steps).toEqual([]); expect(parsed.plan!.first_sustained_safe_point!.phase).toBe('BEFORE_PAYMENT'); expect(getOriginalFullProductsResponse(parsed)).toBe(JSON.stringify(source));
});
test('恢复UNKNOWN缺口不变0或虚构计划', () => { expect(parseFullRecoveryPlan(recoveryPlanFixture(true), recoveryPolicyId).plan).toBeNull(); });
test.each(['quote_position', 'quote_sum', 'candidate_sum', 'lossless_claim', 'lossless_step', 'catalogue_binding', 'expiry', 'duplicate', 'unsafe', 'false_safe_point', 'false_uncovered', 'false_trigger'])('恢复原报价拒绝%s篡改', (kind) => {
  const source = recoveryPlanFixture(), p = source.plan!, c = p.candidates[0]!, q = c.original_quote!;
  if (kind === 'quote_position') q.position_id = fullPolicyId;
  if (kind === 'quote_sum') q.net_cents++;
  if (kind === 'candidate_sum') c.net_cents!--;
  if (kind === 'lossless_claim') c.lossless_eligible = true;
  if (kind === 'lossless_step') p.lossless_steps.push(structuredClone(c));
  if (kind === 'catalogue_binding') source.catalogue_bindings[0]!.terms_digest = 'f'.repeat(64);
  if (kind === 'expiry') q.expires_at = q.request_at;
  if (kind === 'duplicate') p.candidates.push(structuredClone(c));
  if (kind === 'unsafe') c.principal_cents = Number.MAX_SAFE_INTEGER + 1;
  if (kind === 'false_safe_point') p.first_sustained_safe_point = p.actual_boundary.calculation_trace[1]!;
  if (kind === 'false_uncovered') p.uncovered_checkpoints.push(p.actual_boundary.calculation_trace[0]!);
  if (kind === 'false_trigger') p.observed_triggers = ['FAKE_TRIGGER'] as never;
  expect(() => parseFullRecoveryPlan(source, recoveryPolicyId)).toThrow();
});
test('所有请求只GET既存源；asset约束准确原查询名，Recovery带时区截止不授权或发报价', async () => {
  const options = { ...assetOptions(), comparison_days: 365, max_turnover_cents: 67003, funds_use_date: '2026-12-03', mode: 'FIXED_LADDER' as const }; const deadline = '2026-10-07T09:00:00+08:00';
  const calls: { url: URL; options?: RequestInit }[] = []; vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)); calls.push({ url, options: init }); const value = url.pathname.endsWith('/asset-allocation') ? assetPlanFixture(options) : url.pathname.endsWith('/recovery-planning') ? recoveryPlanFixture() : url.pathname.endsWith('/positions') ? productPositions() : productCatalogue(); return new Response(JSON.stringify(value));
  }));
  await getProductCatalog(); await getProductPositions(); await getFullAssetPlan(fullPolicyId, options); await getFullRecoveryPlan(recoveryPolicyId, deadline);
  expect(calls.every((call) => call.options?.method === 'GET' && call.options.body === undefined)).toBe(true);
  expect(Object.fromEntries(calls[2]!.url.searchParams)).toEqual({ planning_comparison_days: '365', planning_max_components: '3', planning_mode: 'FIXED_LADDER', planning_max_turnover_cents: '67003', planning_funds_use_date: '2026-12-03' });
  expect(Object.fromEntries(calls[3]!.url.searchParams)).toEqual({ planning_deadline_at: deadline }); expect(calls.map((call) => call.url.pathname)).toEqual(['/api/v1/catalog/products', '/api/v1/positions', `/api/v1/full-policies/${fullPolicyId}/asset-allocation`, `/api/v1/full-policies/${recoveryPolicyId}/recovery-planning`]);
});
test('不完整JSON成功响应仍拒绝，不返回空目录伪成功', async () => { installHttpFixture(() => ({ simulation: true, state: 'REGISTERED', versions: [] })); await expect(getProductCatalog()).rejects.toThrow(); });
