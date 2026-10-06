import { afterEach, expect, test, vi } from 'vitest';
import { getCurrentGoalAllocation, getOriginalJointPlanning, parseJointPlanning } from './joint-planning';
import { goalId, jointFixture, otherGoalId } from '../tests/full-goal-fixture';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('真实只读路径无客户端收入/金额/时钟覆盖，原八层/金额/收入使用文本保留', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); const original = `\n${JSON.stringify(jointFixture())}\n`; const fetch = vi.fn(async () => new Response(original)); vi.stubGlobal('fetch', fetch);
  const result = await getCurrentGoalAllocation(); expect(fetch).toHaveBeenCalledWith('http://http-unit-fixture.local/api/v1/planning/current-goal-allocation', { method: 'GET' }); expect(getOriginalJointPlanning(result)).toBe(original); expect(result.allocation!.objective_vector).toHaveLength(8); expect(result.grants_authority).toBe(false);
});
test('遗漏/重复/缩水goal分母、八层缺项、归属伪造和unsafe金额均拒绝', () => {
  for (const mutate of [
    (value: ReturnType<typeof jointFixture>) => { value.registered_goal_count = 2; },
    (value: ReturnType<typeof jointFixture>) => { value.included_goal_ids.push(goalId); },
    (value: ReturnType<typeof jointFixture>) => { value.allocation!.goals[0]!.goal_id = otherGoalId; },
    (value: ReturnType<typeof jointFixture>) => { value.allocation!.objective_vector = [0] as never; },
    (value: ReturnType<typeof jointFixture>) => { value.allocation!.goals[0]!.amount_cents = Number.MAX_SAFE_INTEGER + 1; },
    (value: ReturnType<typeof jointFixture>) => { value.grants_authority = true as never; },
    (value: ReturnType<typeof jointFixture>) => { value.funds_scope = 'PROJECTED_INCOME' as never; },
  ]) { const value = jointFixture(); mutate(value); expect(() => parseJointPlanning(value)).toThrow('校验'); }
});
test('UNKNOWN全null与9目标capacity交叠名册仍保全部分母，不能当0或OPTIMAL', () => {
  const unknown = parseJointPlanning(jointFixture('UNKNOWN')); expect(unknown.allocation!.goals[0]!.amount_cents).toBeNull(); expect(unknown.registered_goal_count).toBe(2);
  const capacity = parseJointPlanning(jointFixture('CAPACITY')); expect(capacity.registered_goal_count).toBe(9); expect(capacity.included_goal_ids).toHaveLength(9); expect(capacity.uncovered_goal_ids).toHaveLength(9); expect(capacity.allocation).toBeNull();
  const fake = jointFixture('UNKNOWN'); fake.allocation!.goals[0]!.amount_cents = 0; expect(() => parseJointPlanning(fake)).toThrow('校验');
});
test('实际碎片计划按目标精确守恒，不接受丢使用项、超过资金池或跨目标', () => {
  for (const mutate of [
    (value: ReturnType<typeof jointFixture>) => { value.allocation!.income_uses = []; },
    (value: ReturnType<typeof jointFixture>) => { value.allocation!.income_uses[0]!.amount_cents = 20004; },
    (value: ReturnType<typeof jointFixture>) => { value.allocation!.income_uses[0]!.goal_id = otherGoalId; },
    (value: ReturnType<typeof jointFixture>) => { value.allocation!.budget_cents = 20002; },
  ]) { const value = jointFixture(); mutate(value); expect(() => parseJointPlanning(value)).toThrow('校验'); }
});
test('延期截尾不能包装完成日期，OPTIMAL来源问题或银行未匹配不升级成功', () => {
  const delay = jointFixture(); delay.allocation!.goals[0]!.completion_date = '2026-10-05'; expect(() => parseJointPlanning(delay)).toThrow('校验');
  const unmatched = jointFixture(); unmatched.independent_bank_projection_matched = false; expect(() => parseJointPlanning(unmatched)).toThrow('校验');
  const issue = jointFixture(); issue.source_issues = [{ code: 'UNPROVEN', source_ref: 'unit', message: 'unit' }]; expect(() => parseJointPlanning(issue)).toThrow('校验');
});
test('完整冲突反事实原见证可读取，少目标/伪执行权限/错input hash拒绝', () => {
  expect(parseJointPlanning(jointFixture('INFEASIBLE')).conflict!.deletion_checks[0]!.witness_amounts_cents).toEqual({ [goalId]: 30007 });
  for (const mutate of [
    (value: ReturnType<typeof jointFixture>) => { value.conflict!.deletion_checks[0]!.witness_amounts_cents = {}; },
    (value: ReturnType<typeof jointFixture>) => { value.conflict!.grants_authority = true as never; },
    (value: ReturnType<typeof jointFixture>) => { value.conflict!.input_hash = 'a'.repeat(64); },
    (value: ReturnType<typeof jointFixture>) => { value.conflict!.deletion_checks[0]!.counterfactual_only = false as never; },
  ]) { const value = jointFixture('INFEASIBLE'); mutate(value); expect(() => parseJointPlanning(value)).toThrow('校验'); }
});
