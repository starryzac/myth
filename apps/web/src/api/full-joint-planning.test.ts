import { afterEach, expect, test, vi } from 'vitest';
import { getFullCurrentGoalAllocation, getOriginalFullJointPlanning, parseFullJointPlanning, validateFullJointGoals } from './full-joint-planning';
import { fullJointFixture, fullJointGoalFixture } from '../tests/full-joint-fixture';
import { otherGoalId } from '../tests/full-goal-fixture';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test('实际GET零payload保留完整原文本与1098原来源绑定/八层', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://http-unit-fixture.local'); const raw = `\n${JSON.stringify(fullJointFixture())}\n`; const fetch = vi.fn(async () => new Response(raw)); vi.stubGlobal('fetch', fetch);
  const result = await getFullCurrentGoalAllocation(); expect(fetch).toHaveBeenCalledWith('http://http-unit-fixture.local/api/v1/planning/full-current-goal-allocation', { method: 'GET' }); expect(getOriginalFullJointPlanning(result)).toBe(raw); expect(result.binding!.bound_point_count).toBe(1098); expect(result.allocation!.objective_vector).toHaveLength(8); expect(result.grants_authority).toBe(false); validateFullJointGoals(result, [fullJointGoalFixture()]);
});
test('原JSON不规范化且UNKNOWN/完整分母容量null不被填零', () => {
  const value = fullJointFixture('UNKNOWN'); const before = JSON.stringify(value); const parsed = parseFullJointPlanning(value); expect(parsed.allocation!.goals[0]!.amount_cents).toBeNull(); expect(parsed.binding!.full_point_count).toBe(0); expect(JSON.stringify(value)).toBe(before);
  const capacity = parseFullJointPlanning(fullJointFixture('CAPACITY')); expect(capacity.original_joint.registered_goal_count).toBe(9); expect(capacity.binding).toBeNull(); expect(capacity.allocation).toBeNull();
  value.allocation!.goals[0]!.amount_cents = 0; expect(() => parseFullJointPlanning(value)).toThrow('校验');
});
const mutations: ((value: ReturnType<typeof fullJointFixture>) => void)[] = [
  (value) => { value.user_id = otherGoalId; },
  (value) => { value.original_joint.as_of = '2026-10-05T13:00:00Z'; },
  (value) => { value.full_protection.user_id = otherGoalId; },
  (value) => { value.binding!.candidate.user_id = otherGoalId; },
  (value) => { value.grants_authority = true as never; },
  (value) => { value.execution_support = 'IMPLEMENTED' as never; },
  (value) => { value.original_joint.registered_goal_count = 2; },
  (value) => { value.binding!.candidate.goals[0]!.effective_policy_version_id = otherGoalId; },
  (value) => { value.allocation!.goals[0]!.effective_policy_version_id = otherGoalId; },
  (value) => { value.binding!.candidate.goals[0]!.current_owned_cents += 1; },
  (value) => { value.binding!.verified_source_refs[0]!.content_hash = 'f'.repeat(64); },
  (value) => { value.binding!.verified_source_refs[0]!.user_id = otherGoalId; },
  (value) => { value.binding!.verified_source_refs.pop(); },
  (value) => { value.binding!.original_input_hash = 'f'.repeat(64); },
  (value) => { value.binding!.full_projection_input_hash = 'f'.repeat(64); },
  (value) => { value.binding!.bound_point_count = 1097; },
  (value) => { value.binding!.candidate.hard_protection_points.pop(); },
  (value) => { value.binding!.candidate.hard_protection_points[25]!.living_floor_cents -= 1; },
  (value) => { value.binding!.candidate.hard_protection_points[25]!.owned_goal_cash_cents -= 1; },
  (value) => { value.binding!.candidate.hard_protection_points[25]!.other_protection_floor_cents -= 1; },
  (value) => { value.binding!.candidate.hard_protection_points[25]!.cash_cents += 7001; },
  (value) => { value.binding!.candidate.hard_protection_points[25]!.date = '2026-12-30'; },
  (value) => { value.allocation!.budget_cents += 1; },
  (value) => { value.allocation!.income_uses[0]!.fragment_id = otherGoalId; },
  (value) => { value.allocation!.income_uses[0]!.origin_transaction_id = otherGoalId; },
  (value) => { value.allocation!.income_uses[0]!.source_account_id = otherGoalId; },
  (value) => { value.binding!.candidate.income_lots[0]!.bank_evidence_hash = 'f'.repeat(64); },
  (value) => { value.binding!.candidate.income_lots[0]!.observed_at = '2026-10-06T00:00:00Z'; },
  (value) => { value.binding!.candidate.income_lots[0]!.received_cents = 10; },
  (value) => { value.allocation!.goals[0]!.amount_cents = Number.MAX_SAFE_INTEGER+1; },
  (value) => { value.binding!.candidate.source_issues.push('BANK_NOT_PROVEN'); },
  (value) => { value.full_protection.audit.complete = false; },
];
test.each(mutations.map((mutate, index) => [index, mutate] as const))('拒绝原source/owner/clock/版本/curve/owned/银行flow漂移 %s', (_index, mutate) => { const value = fullJointFixture(); mutate(value); expect(() => parseFullJointPlanning(value)).toThrow(); });
test('来源已绑但原solver容量UNKNOWN仍全部null，不冒充安全最优', () => {
  const value = fullJointFixture(); value.state = 'UNKNOWN'; value.allocation!.status = 'UNKNOWN'; value.allocation!.objective_vector = null; value.allocation!.income_uses = []; value.reasons = ['COMBINATORIAL_STATE_CAPACITY_EXCEEDED'];
  for (const goal of value.allocation!.goals) { goal.amount_cents = null; goal.minimum_shortfall_cents = null; goal.projected_owned_cents = null; goal.delay_lower_bound_days = null; goal.deferral_cost_lower_bound_cents = null; }
  expect(parseFullJointPlanning(value).binding!.status).toBe('VERIFIED');
});
test('独立actualGoal list stale版本/金额/缺失或重复必须阻止当前金额展示', () => {
  const parsed = parseFullJointPlanning(fullJointFixture()); validateFullJointGoals(parsed, undefined); validateFullJointGoals(parsed, [fullJointGoalFixture()]);
  for (const values of [[], [fullJointGoalFixture(), fullJointGoalFixture()], [{ ...fullJointGoalFixture(), policy_version_id: otherGoalId }], [{ ...fullJointGoalFixture(), allocated_cents: 0 }], [{ ...fullJointGoalFixture(), account_id: otherGoalId }]]) expect(() => validateFullJointGoals(parsed, values)).toThrow('校验');
});
