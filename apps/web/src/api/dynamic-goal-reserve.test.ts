import { afterEach, expect, test, vi } from 'vitest';
import { getDynamicGoalReserve, getOriginalDynamicGoalResponse, parseDynamicGoalReserve } from './dynamic-goal-reserve';
import { dynamicGoal, dynamicGoalFixture } from '../tests/dynamic-goal-fixture';
import { otherGoalId } from '../tests/full-goal-fixture';
import { installHttpFixture } from '../tests/policy-fixture';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

test('实际GET消费者精确Goal/version/currentmonth绑定，原HTTP文本保留，无金额/时钟/body/query', async () => {
  const source = dynamicGoalFixture(); const original = ` \n${JSON.stringify(source)}\n`; const calls = installHttpFixture(() => new Response(original)); const result = await getDynamicGoalReserve(dynamicGoal); expect(calls).toEqual([{ method: 'GET', path: `/api/v1/goals/${dynamicGoal.id}/dynamic-reserve`, body: undefined }]); expect(getOriginalDynamicGoalResponse(result)).toBe(original); expect(result.reserve!.current_month_contributed_cents).toBe(10001); expect(result.reserve!.future_income_included_cents).toBe(0);
});

test.each(['UNKNOWN', 'EXPIRED', 'INACTIVE'] as const)('%s响应保null金额与具体缺项，不借旧reserve或填0', (state) => {
  const source = dynamicGoalFixture(state); expect(parseDynamicGoalReserve(source, dynamicGoal).reserve).toBeNull(); expect(source.source_issues).toHaveLength(1); expect(() => parseDynamicGoalReserve({ ...source, reserve: dynamicGoalFixture().reserve }, dynamicGoal)).toThrow('校验');
});

test('服务器UTC/AsiaShanghai月边界保实际月，拒绝客户端下一月/非法月份', () => {
  const source = dynamicGoalFixture(); source.as_of = '2026-10-31T18:00:00Z'; source.reserve!.period = '2026-11'; expect(parseDynamicGoalReserve(source, dynamicGoal).reserve!.period).toBe('2026-11'); for (const period of ['2026-12', '2026-13', '2026-1', '2026-10-01']) expect(() => parseDynamicGoalReserve({ ...source, reserve: { ...source.reserve, period } }, dynamicGoal)).toThrow('校验');
});

test('主体/版本/原来源/未来收入/执行权限及缺值/unsafe金额篡改拒绝', () => {
  const mutations = [
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.goal_id = otherGoalId; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.reserve!.policy_version_id = otherGoalId; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.reserve!.goal_id = otherGoalId; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.user_id = 'unknown'; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.source_evidence_ids.push(v.source_evidence_ids[0]!); },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.source_issues = [{ code: 'SOURCE_TAMPER', message: 'unknown', source_ref: 'old' }]; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.reserve!.future_income_included_cents = 1 as never; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.grants_authority = true as never; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.reserve!.preview_only = false as never; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.reserve!.actual_completion_date = '2026-10-05' as never; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.reserve!.current_month_contributed_cents = null; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.reserve!.eligible_available_income_cents = Number.MAX_SAFE_INTEGER + 1; },
    (v: ReturnType<typeof dynamicGoalFixture>) => { v.reserve!.current_month_contributed_cents = -1; },
  ]; for (const mutate of mutations) { const source = dynamicGoalFixture(); mutate(source); expect(() => parseDynamicGoalReserve(source, dynamicGoal)).toThrow(); }
});

test.each(['EXPIRED_POLICY', 'INACTIVE_POLICY', 'LIQUIDITY_RISK', 'INSUFFICIENT_EVIDENCE'] as const)('COMPUTED下的%s阻挡结果所有金额保持null，不能偷填建议', (status) => {
  const source = dynamicGoalFixture('COMPUTED', status); expect(parseDynamicGoalReserve(source, dynamicGoal).reserve!.suggested_additional_cents).toBeNull(); source.reserve!.suggested_additional_cents = 0; expect(() => parseDynamicGoalReserve(source, dynamicGoal)).toThrow('校验');
});

test('动态累计/名义差额允许负值；硬保证短缺建议null，计算摘要和资金上界不一致拒绝', () => {
  const source = dynamicGoalFixture(); source.reserve!.nominal_month_target_cents = 50009; source.reserve!.pace_delta_from_nominal_cents = -10002; expect(parseDynamicGoalReserve(source, dynamicGoal).reserve!.pace_delta_from_nominal_cents).toBe(-10002); for (const status of ['HARD_GUARANTEE_SHORTFALL', 'DEADLINE_BLOCKED'] as const) { const blocked = dynamicGoalFixture('COMPUTED', status); expect(parseDynamicGoalReserve(blocked, dynamicGoal).reserve!.suggested_additional_cents).toBeNull(); blocked.reserve!.suggested_additional_cents = 0; expect(() => parseDynamicGoalReserve(blocked, dynamicGoal)).toThrow(); }
  for (const patch of [{ pace_delta_from_nominal_cents: 1 }, { suggested_additional_cents: 50010 }, { progress_basis_points: 10001 }, { remaining_calendar_month_slots: 0 }, { desired_additional_cents: 180028 }, { status: 'COMPLETE', suggested_additional_cents: 0 }, { status: 'OVERDUE_READY', overdue_days: 0 }]) expect(() => parseDynamicGoalReserve({ ...dynamicGoalFixture(), reserve: { ...dynamicGoalFixture().reserve, ...patch } }, dynamicGoal)).toThrow('校验');
});

test('HTTP409保request_id/no retry，非法Goal/缺原version在连接前拒绝', async () => {
  const calls = installHttpFixture(() => new Response(JSON.stringify({ error: { code: 'INVALID_DYNAMIC_GOAL_SOURCE', message: '实际来源拒绝', request_id: 'UNIT_DYNAMIC_409' } }), { status: 409 })); expect(() => getDynamicGoalReserve({ ...dynamicGoal, id: '../execute' })).toThrow(); expect(() => getDynamicGoalReserve({ ...dynamicGoal, policy_version_id: 'unknown' })).toThrow(); expect(calls).toEqual([]); await expect(getDynamicGoalReserve(dynamicGoal)).rejects.toMatchObject({ requestId: 'UNIT_DYNAMIC_409', code: 'INVALID_DYNAMIC_GOAL_SOURCE' }); expect(calls).toHaveLength(1);
});


test('完成/本月已超新上限保原归属贡献且新增零；PARTIAL仅实际安全收入上界内建议', () => {
  const complete = dynamicGoalFixture(); Object.assign(complete.reserve!, { status: 'COMPLETE', current_owned_cents: 300000, remaining_goal_cents: 0, excess_owned_cents: 99970, progress_basis_points: 10000, current_month_contributed_cents: 0, uncapped_gross_pace_cents: 0, dynamic_month_total_cents: 0, pace_delta_from_nominal_cents: -30001, desired_additional_cents: 0, suggested_additional_cents: 0 }); expect(parseDynamicGoalReserve(complete, dynamicGoal).reserve!.excess_owned_cents).toBe(99970);
  const exceeded = dynamicGoalFixture(); Object.assign(exceeded.reserve!, { status: 'MONTHLY_MAX_ALREADY_EXCEEDED', current_owned_cents: 45007, current_month_contributed_cents: 45007, remaining_goal_cents: 155023, progress_basis_points: 2249, uncapped_gross_pace_cents: 66677, desired_additional_cents: 0, suggested_additional_cents: 0 }); expect(parseDynamicGoalReserve(exceeded, dynamicGoal).reserve!.current_month_contributed_cents).toBe(45007);
  const partial = dynamicGoalFixture(); Object.assign(partial.reserve!, { status: 'PARTIAL', eligible_available_income_cents: 10003, suggested_additional_cents: 10003 }); expect(parseDynamicGoalReserve(partial, dynamicGoal).reserve!.suggested_additional_cents).toBe(10003); for (const source of [complete, exceeded]) expect(() => parseDynamicGoalReserve({ ...source, reserve: { ...source.reserve, suggested_additional_cents: 1 } }, dynamicGoal)).toThrow();
});
