/** Synthetic pure-domain fixture; no PostgreSQL, bank, audit or browser acceptance. */
import { expect, test } from 'vitest';
import raw from '../tests/full-seasonal-annual-fixture.json';
import { parseFullAnnualPlanning } from './full-annual';

test('新季节v3只读展示真实计算的1098点保护floor，窗口后现金不因释放而扣款', () => {
  const value = parseFullAnnualPlanning(structuredClone(raw));
  expect(value.projection.algorithm_version).toBe('registered-full-protection-adopted-seasonal-v3');
  expect(value.projection.seasonal_adopted_adjustment_cents).toBe(1800);
  expect(value.projection.full_annual_projection!.calculation_trace).toHaveLength(1098);
  expect(value.projection.full_annual_projection!.calculation_trace[0]!.protected_cents_by_reason.full_seasonal_adopted).toBe(1800);
  expect(value.projection.full_annual_projection!.calculation_trace.at(-1)!.protected_cents_by_reason.full_seasonal_adopted).toBe(0);
  expect(value.projection.full_annual_projection!.calculation_trace.every(point => point.cash_cents === 5000)).toBe(true);
  expect(value.grants_authority).toBe(false);
});

test('v3改贴旧标签、未采纳原件、金额/用户/原证据/逐阶段floor及到期改写均拒绝', () => {
  for (const mutate of [
    (v: typeof raw) => { v.projection.algorithm_version = 'registered-full-protection-v1'; },
    (v: typeof raw) => { v.projection.seasonal_adopted_adjustment_cents += 1; },
    (v: typeof raw) => { v.full_policy_sources[0]!.reference_snapshots[0]!.proof.status = 'UNKNOWN'; },
    (v: typeof raw) => { v.full_policy_sources[0]!.reference_snapshots[0]!.proof.user_id = '00000000-0000-0000-0000-000000009999'; },
    (v: typeof raw) => { v.source_evidence_ids = []; },
    (v: typeof raw) => { v.full_policy_sources[0]!.reference_snapshots[0]!.proof.original.scope.protection_end = '2026-02-13'; },
    (v: typeof raw) => { v.full_policy_sources[0]!.reference_snapshots = []; },
    (v: typeof raw) => { v.projection.full_annual_projection.calculation_trace[0]!.protected_cents_by_reason.full_seasonal_adopted -= 1; },
  ]) { const value = structuredClone(raw); mutate(value); expect(() => parseFullAnnualPlanning(value)).toThrow(); }
});
