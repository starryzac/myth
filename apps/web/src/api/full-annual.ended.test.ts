/** Synthetic pure-domain v4 response only, not actual PG, bank, audit or browser proof. */
import { expect, test } from 'vitest';
import raw from '../tests/full-ended-seasonal-annual-fixture.json';
import { parseFullAnnualPlanning } from './full-annual';

test('结束窗口v4只释放1098点下界，保原1800采纳并不增加5000条件现金', () => {
  const result = parseFullAnnualPlanning(structuredClone(raw));
  expect(result.projection.algorithm_version).toBe('registered-full-protection-ended-seasonal-v4');
  expect(result.projection.seasonal_status).toBe('ADOPTED_FLOOR_RELEASED');
  expect(result.projection.seasonal_adopted_adjustment_cents).toBe(0);
  expect(result.projection.full_annual_projection!.calculation_trace).toHaveLength(1098);
  expect(result.projection.full_annual_projection!.calculation_trace.every(point => point.cash_cents === 5000 && point.protected_cents_by_reason.full_seasonal_adopted === 0)).toBe(true);
  expect(raw.full_policy_sources[0]!.reference_snapshots[0]!.proof.original_adopted_cents).toBe(1800);
  expect(result.audit.complete).toBe(false);
});

test('结束原件缺失或标签金额owner分母和到期变动不能显示已释放成功', () => {
  const changes: ((value: typeof raw) => void)[] = [
    value => { value.projection.algorithm_version = 'registered-full-protection-adopted-seasonal-v3'; },
    value => { value.projection.seasonal_status = 'ADOPTED_PROTECTED'; },
    value => { value.projection.seasonal_adopted_adjustment_cents = 1; },
    value => { value.full_policy_sources[0]!.reference_snapshots = []; },
    value => { value.full_policy_sources[0]!.reference_snapshots[0]!.proof.status = 'UNKNOWN'; },
    value => { value.full_policy_sources[0]!.reference_snapshots[0]!.proof.user_id = '00000000-0000-0000-0000-000000009999'; },
    value => { value.full_policy_sources[0]!.reference_snapshots[0]!.proof.protection_end = '1900-01-01'; },
    value => { value.full_policy_sources[0]!.reference_snapshots[0]!.proof.cash_balance_changed = true; },
    value => { value.full_policy_sources[0]!.reference_snapshots[0]!.proof.registered_adoption_evidence_ids = []; },
    value => { value.full_policy_sources[0]!.reference_snapshots[0]!.proof.inputs.records = []; },
    value => { value.source_evidence_ids = []; },
    value => { value.projection.full_annual_projection.calculation_trace[0]!.protected_cents_by_reason.full_seasonal_adopted = 1; },
  ];
  for (const change of changes) { const value = structuredClone(raw); change(value); expect(() => parseFullAnnualPlanning(value)).toThrow(); }
});
