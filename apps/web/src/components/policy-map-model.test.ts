import { expect, test } from 'vitest';
import type { Policy } from '../api/policies';
import { policyFixture } from '../tests/policy-fixture';
import { buildPolicyRelations, canEditPolicy, protectionFacts } from './policy-map-model';

function asset(): Policy {
  const policy = policyFixture(); const id = '10000000-0000-0000-0000-000000000050';
  return { ...policy, id, name: '工具夹具资产授权', policy_type: 'asset_authorization', current_version: {
    ...policy.current_version!, id: '10000000-0000-0000-0000-000000000051', policy_id: id, configuration: { type: 'asset_authorization', scope: 'goal', goal_id: policy.id },
  } };
}
test('有向边只来自原字段；goal_id保持目标身份、priority不凭空产生保护边', () => {
  const goal = policyFixture('goal_saving'); const target = asset(); goal.current_version!.configuration.asset_policy_id = target.id;
  const result = buildPolicyRelations([goal, target]); expect(result.issues).toEqual([]);
  expect(result.relations.map((edge) => [edge.sourceId, edge.targetId, edge.field, edge.state])).toEqual([
    [goal.id, target.id, 'asset_policy_id', 'REFERENCE_PRESENT'], [target.id, goal.id, 'goal_id', 'GOAL_MAPPING_UNAVAILABLE'],
  ]);
  expect(result.relations[1]!.targetKind).toBe('goal');
  expect(protectionFacts(goal.current_version!.configuration)).toEqual(['最低保护 0 分', '不允许降低承诺', '不允许延期']);
});
test('断链、无版本、错误类型、暂停与无授权分别保留真实状态', () => {
  const goal = policyFixture('goal_saving'); const target = asset(); goal.current_version!.configuration.asset_policy_id = target.id;
  const state = (policies: Policy[]) => buildPolicyRelations(policies).relations.find((edge) => edge.field === 'asset_policy_id')!.state;
  expect(state([goal])).toBe('MISSING_POLICY');
  expect(state([goal, { ...target, current_version: null }])).toBe('MISSING_VERSION');
  expect(state([goal, { ...target, policy_type: 'emergency_buffer' }])).toBe('WRONG_POLICY_TYPE');
  expect(state([goal, { ...target, effective_status: 'SUSPENDED' }])).toBe('INACTIVE_TARGET');
  expect(state([goal, { ...target, version_authorized: false }])).toBe('UNAUTHORIZED_TARGET');
});
test('显式保护数组才建边，坏格式不生成成功关系或修改原配置', () => {
  const policy = policyFixture(); const target = asset();
  policy.current_version!.configuration.must_not_reduce_policy_ids = [target.id, target.id, 3, ' bad-id '];
  const before = structuredClone(policy); const result = buildPolicyRelations([policy, target]);
  expect(result.relations.filter((edge) => edge.field === 'must_not_reduce_policy_ids')).toHaveLength(1);
  expect(result.issues).toHaveLength(2); expect(policy).toEqual(before);
  policy.current_version!.configuration.must_not_reduce_policy_ids = true;
  expect(buildPolicyRelations([policy]).issues[0]!.message).toContain('不是引用列表');
});
test('历史配置只替换选中来源，不重标当前状态；未知历史不回落当前关系', () => {
  const goal = policyFixture('goal_saving'); const target = asset(); goal.current_version!.configuration.asset_policy_id = target.id;
  const old = { ...goal.current_version!, id: 'old-original', version_number: 0, configuration: { type: 'goal_saving', asset_policy_id: 'missing-original' } };
  expect(buildPolicyRelations([goal, target], { policyId: goal.id, version: old }).relations[0]).toMatchObject({ sourceVersionId: 'old-original', targetId: 'missing-original', state: 'MISSING_POLICY' });
  const absent = buildPolicyRelations([goal, target], { policyId: goal.id, version: null });
  expect(absent.relations.some((edge) => edge.sourceId === goal.id)).toBe(false); expect(absent.issues[0]!.message).toContain('关系未知');
  expect(goal.current_version!.configuration.asset_policy_id).toBe(target.id);
});
test('版本身份冲突不能作为地图配置或当前修改依据', () => {
  const policy = policyFixture(); policy.current_version!.policy_id = 'foreign-policy';
  expect(buildPolicyRelations([policy]).relations).toEqual([]); expect(buildPolicyRelations([policy]).issues[0]!.message).toContain('身份不匹配');
  expect(canEditPolicy(policy)).toBe(false);
  for (const state of ['EXPIRED', 'REVOKED', 'INVALIDATED']) expect(canEditPolicy({ ...policyFixture(), effective_status: state })).toBe(false);
});
