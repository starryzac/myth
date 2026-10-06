import { afterEach, expect, test, vi } from 'vitest';
import { getFullAnnualPlanning, getOriginalFullAnnualResponse, parseFullAnnualPlanning } from './full-annual';
import { fullAnnualFixture, fullDatedId, periodicAccountId } from '../tests/full-annual-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

test('显式未来日期历史v2原曲线可读取，算法混合或未知版本拒绝', () => {
  const value = fullAnnualFixture();
  value.projection.algorithm_version = 'registered-full-protection-future-dated-history-v2';
  value.projection.full_annual_projection!.algorithm_version = value.projection.algorithm_version;
  expect(parseFullAnnualPlanning(value).projection.algorithm_version).toBe(value.projection.algorithm_version);
  value.projection.full_annual_projection!.algorithm_version = 'registered-full-protection-v1';
  expect(() => parseFullAnnualPlanning(value)).toThrow('校验');
  value.projection.algorithm_version = 'UNKNOWN_ALGORITHM' as never;
  expect(() => parseFullAnnualPlanning(value)).toThrow('校验');
});

test('完整年度GET保366日期/三阶段及原90/365视图，原响应文本保留且不提交body/时钟', async () => {
  const source = fullAnnualFixture(); const original = ` \n${JSON.stringify(source)}\n`; const calls = installHttpFixture(() => new Response(original)); const result = await getFullAnnualPlanning();
  expect(calls).toEqual([{ method: 'GET', path: '/api/v1/planning/full-annual', body: undefined }]); expect(result.daily_checkpoints).toHaveLength(365); expect(result.projection.full_annual_projection!.calculation_trace).toHaveLength(1098); expect(result.projection.original_execution_view.calculation_trace).toHaveLength(273); expect(getOriginalFullAnnualResponse(result)).toBe(original); expect(result.projection.bank_authority).toBe(false);
});

test('FULL未知不借原READY视图补值；null三阶段和365日期仍完整，收入/节日未接不假填金额', () => {
  const result = parseFullAnnualPlanning(fullAnnualFixture(true)); expect(result.projection.original_annual_projection.status).toBe('READY'); expect(result.projection.full_annual_projection).toBeNull(); expect(result.projection.full_obligations_complete_within_registered_current_scope).toBe(false); expect(result.daily_checkpoints.every((point) => point.status === 'NOT_PROVEN' && point.before_payment === null && point.after_payment === null && point.after_principal === null && point.minimum_intraday_margin_cents === null)).toBe(true); expect(result.projection.seasonal_adopted_adjustment_cents).toBeNull(); expect(result.future_income.included_in_planning_cents).toBe(0);
});

test('合法原JSON字段/保护分项键顺序无关，跨年最后一日保日期且不改金融金额', () => {
  const source = fullAnnualFixture(); const phase = source.daily_checkpoints[0]!.before_payment!; source.daily_checkpoints[0]!.before_payment = Object.fromEntries(Object.entries(phase).reverse()) as typeof phase; source.daily_checkpoints[0]!.before_payment!.protected_cents_by_reason = Object.fromEntries(Object.entries(phase.protected_cents_by_reason).reverse()); expect(parseFullAnnualPlanning(source).daily_checkpoints.at(-1)!.date).toBe('2027-10-05'); expect(source.daily_checkpoints[0]!.minimum_intraday_margin_cents).toBe(66997);
});

test('缺日/重复日/非法日/三阶段或完整trace错配/摘要金额漂移拒绝', () => {
  const mutations = [
    (v: ReturnType<typeof fullAnnualFixture>) => { v.daily_checkpoints.pop(); },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.daily_checkpoints[0]!.day = 0; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.daily_checkpoints[0]!.date = '2026-02-30'; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.daily_checkpoints[0]!.after_payment!.phase = 'BEFORE_PAYMENT'; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.daily_checkpoints[0]!.before_payment!.cash_cents += 1; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.daily_checkpoints[0]!.minimum_intraday_margin_cents! += 1; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.full_annual_projection!.calculation_trace.pop(); },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.original_execution_view.calculation_trace.pop(); },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.full_annual_projection!.minimum_margin_cents! += 1; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.full_annual_projection!.calculation_trace[0]!.protected_cents_by_reason.synthetic_reserve! += 1; },
  ]; for (const mutate of mutations) { const source = fullAnnualFixture(); mutate(source); expect(() => parseFullAnnualPlanning(source)).toThrow('校验'); }
});

test('来源/状态分母、版本、confirmationhash、义务发生身份/日期/证据篡改拒绝', () => {
  const mutations = [
    (v: ReturnType<typeof fullAnnualFixture>) => { v.full_policy_sources.push(structuredClone(v.full_policy_sources[0]!)); },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.policy_states.pop(); },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.policy_states[1]!.policy_id = fullDatedId; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.policy_states[0]!.version_id = periodicAccountId; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.full_policy_sources[0]!.confirmation.reviewed_hash = '0'.repeat(64); },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.full_policy_sources[0]!.confirmed_at = '2099-01-01T00:00:00Z'; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.occurrences.push(structuredClone(v.projection.occurrences[0]!)); },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.occurrences[0]!.policy_version_id = periodicAccountId; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.occurrences[0]!.earliest_due_date = '2027-02-30'; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.occurrences[0]!.occurrence_id = 'UNKNOWN_DERIVED_ID'; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.occurrences[0]!.hypothetical_payment_date = '2027-01-01'; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.occurrences[0]!.overdue = true; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.occurrences[0]!.evidence_ids = []; },
  ]; for (const mutate of mutations) { const source = fullAnnualFixture(); mutate(source); expect(() => parseFullAnnualPlanning(source)).toThrow('校验'); }
});

test('账户真实分母与整数分守恒检查；负剩余保风险，不假作全未来账户覆盖', () => {
  const valid = parseFullAnnualPlanning(fullAnnualFixture(false, true)); expect(valid.projection.source_account_checks[0]!.remaining_current_cash_cents).toBe(-1002); expect(valid.projection.status).toBe('LIQUIDITY_RISK');
  for (const mutate of [
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.source_account_checks = []; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.source_account_checks[0]!.account_id = fullDatedId; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.source_account_checks[0]!.registered_periodic_required_cents += 1; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.source_account_checks[0]!.remaining_current_cash_cents += 1; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.source_account_checks[0]!.state = 'SOURCE_LIQUIDITY_RISK'; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.source_account_checks[0]!.future_account_debits_complete = true as never; },
  ]) { const source = fullAnnualFixture(); mutate(source); expect(() => parseFullAnnualPlanning(source)).toThrow('校验'); }
});

test('unsafe整数/缺金额/伪执行授权/历史结清/已采纳Seasonal/未来收入及UNKNOWN假曲线拒绝', () => {
  for (const mutate of [
    (v: ReturnType<typeof fullAnnualFixture>) => { v.daily_checkpoints[0]!.before_payment!.cash_cents = Number.MAX_SAFE_INTEGER + 1; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.source_account_checks[0]!.actual_cash_cents = null as never; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.grants_authority = true as never; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.bank_authority = true as never; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.execution_support = 'IMPLEMENTED' as never; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.historical_full_settlement_complete = true as never; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.seasonal_adopted_adjustment_cents = 100 as never; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.future_income_in_original_execution_cents = 1 as never; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.future_income.included_in_planning_cents = 1 as never; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.projection.status = 'UNKNOWN'; },
    (v: ReturnType<typeof fullAnnualFixture>) => { v.audit.epoch_id = 'unknown'; },
  ]) { const source = fullAnnualFixture(); mutate(source); expect(() => parseFullAnnualPlanning(source)).toThrow(); }
});

test('原错误request_id保留，GET服务409没有隐式重试或POST', async () => {
  const calls = installHttpFixture(() => new Response(JSON.stringify({ error: { code: 'INVALID_FULL_PROTECTION_INPUT', message: '原年度保护输入拒绝', request_id: 'UNIT_FULL_ANNUAL_409' } }), { status: 409 })); await expect(getFullAnnualPlanning()).rejects.toMatchObject({ code: 'INVALID_FULL_PROTECTION_INPUT', requestId: 'UNIT_FULL_ANNUAL_409' }); expect(calls).toHaveLength(1); expect(calls[0]!.method).toBe('GET');
});
