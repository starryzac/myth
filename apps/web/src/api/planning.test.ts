import { afterEach, expect, test, vi } from 'vitest';
import { getAnnualPlanning, getOriginalAnnualResponse, parseAnnualPlanning } from './planning';
import { annualFixture } from '../tests/annual-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('初始点加365日期/三阶段与90执行原视图独立；GET不提交金额或客户端时钟', async () => {
  const source = annualFixture(); const requests = installHttpFixture(() => source); const parsed = await getAnnualPlanning();
  expect(parsed.daily_checkpoints).toHaveLength(365); expect(parsed.annual_projection.calculation_trace).toHaveLength(1098); expect(parsed.execution_view.calculation_trace).toHaveLength(273);
  expect(requests).toEqual([{ method: 'GET', path: '/api/v1/planning/annual', body: undefined }]); expect(getOriginalAnnualResponse(parsed)).toBe(JSON.stringify(source));
});
test('未知值完整保留365日期和空阶段，不填0或伪造曲线', () => {
  const source = annualFixture(true); const parsed = parseAnnualPlanning(source);
  expect(parsed.daily_checkpoints.every((point) => point.before_payment === null && point.after_payment === null && point.after_principal === null)).toBe(true);
  expect(parsed.annual_projection.safe_idle_cents).toBeNull(); expect(parsed.annual_projection.calculation_trace).toEqual([]);
});
test('少日、重复日、错日期或阶段、原trace不一致均拒绝', () => {
  for (const mutation of [
    (value: ReturnType<typeof annualFixture>) => { value.daily_checkpoints.pop(); },
    (value: ReturnType<typeof annualFixture>) => { value.daily_checkpoints[1]!.day = 1; },
    (value: ReturnType<typeof annualFixture>) => { value.daily_checkpoints[1]!.date = '2026-02-30'; },
    (value: ReturnType<typeof annualFixture>) => { value.daily_checkpoints[0]!.after_payment!.phase = 'BEFORE_PAYMENT'; },
    (value: ReturnType<typeof annualFixture>) => { value.daily_checkpoints[0]!.after_payment!.cash_cents += 1; },
    (value: ReturnType<typeof annualFixture>) => { value.execution_view.calculation_trace.pop(); },
  ]) { const source = annualFixture(); mutation(source); expect(() => parseAnnualPlanning(source)).toThrow('校验'); }
});
test('不安全整数、伪授权、未来点已结算或未登记未来收入金额拒绝', () => {
  for (const [field, value] of [['grants_authority', true], ['future_points_are_settled_cash', true], ['horizon_days', 90]] as const) {
    const source = annualFixture(); expect(() => parseAnnualPlanning({ ...source, [field]: value })).toThrow('校验');
  }
  const unsafe = annualFixture(); unsafe.daily_checkpoints[0]!.before_payment!.cash_cents = Number.MAX_SAFE_INTEGER + 1; expect(() => parseAnnualPlanning(unsafe)).toThrow('精确');
  const future = annualFixture(); expect(() => parseAnnualPlanning({ ...future, future_income: { ...future.future_income, included_in_planning_cents: 1 } })).toThrow('校验');
});
