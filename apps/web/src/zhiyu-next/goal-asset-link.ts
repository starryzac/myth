import { request } from '../api/http';
import { parseFullGoalModel, parseFullGoalConfiguration, type FullGoalModel, type FullGoalConfiguration } from '../api/full-goals';
import { spendingHash as hash, spendingUUID as uuid } from '../api/spending-evidence';
import { object } from '../features/policy-form';
import { assetExact as exact } from './asset-recovery';
import { context, getPolicyRecords, getPolicySchema, type PolicyRecord } from './policies';
import type { NextState } from './api';
import type { Goal } from '../zhiyu/api';
import { parseNativePermissionConfiguration } from './asset-permissions';

function check(v: unknown): asserts v { if (!v) throw new Error('原目标模型或独立购买权限已改变，请重新读取同一目标后审阅。'); }
export type GoalAssetLinkReview = { goal: Goal; original_model: FullGoalModel; permission: PolicyRecord; configuration: FullGoalConfiguration; configuration_hash: string; body: Record<string, unknown> };
export function matchingGoalPermissions(records: PolicyRecord[], goal: Goal) { return records.filter((p) => p.source_kind === 'MVP_POLICY' && p.dsl_version === 'MVP_V1' && p.template_name === 'AssetAuthorizationPolicy' && p.configuration.type === 'asset_authorization' && p.configuration.scope === 'goal' && p.configuration.goal_id === goal.id && p.planning_confirmed && ['ACTIVE', 'CONFIRMED'].includes(p.effective_status)); }
export async function readGoalLinkModel(goal: Goal, env: NextState): Promise<FullGoalModel> {
  check(env.goals.some((g) => g.id === goal.id && g.policy_id === goal.policy_id && g.policy_version_id === goal.policy_version_id));
  return request(`/zhiyu-next/goals/models/${goal.id}`, 'GET', undefined, async (v) => { context(v, env); check(exact(v, ['simulation', 'variant', 'environment_id', 'epoch_id', 'model'])); const model = parseFullGoalModel(v.model, { id: goal.id, policy_id: goal.policy_id, policy_version_id: goal.policy_version_id }); check(model.epoch_id === env.epoch_id && model.status === 'VERIFIED' && object(model.full_configuration) && await hash(model.full_configuration) === model.full_configuration_hash); return model; });
}
export async function prepareGoalAssetLink(goal: Goal, permissionId: string, env: NextState): Promise<GoalAssetLinkReview> {
  check(uuid(permissionId)); const model = await readGoalLinkModel(goal, env); const records = await getPolicyRecords(env); const permission = matchingGoalPermissions(records.items, goal).find((p) => p.policy_id === permissionId); check(permission && await hash(permission.configuration) === permission.configuration_hash);
  parseNativePermissionConfiguration(permission.configuration);
  const schema = await getPolicySchema('LongTermGoalPolicy', env); check(schema.reference_choices.asset_policy_id?.some((p) => p.value === permission.policy_id));
  const original = parseFullGoalConfiguration(model.full_configuration); const configuration = { ...structuredClone(original), asset_policy_id: permission.policy_id }; parseFullGoalConfiguration(configuration); const configuration_hash = await hash(configuration);
  return { goal, original_model: model, permission, configuration, configuration_hash, body: { template_name: 'LongTermGoalPolicy', dsl_version: 'FULL_V1', configuration, goal_id: goal.id, expected_version_id: goal.policy_version_id, goal_account_id: null, expected_epoch_id: env.epoch_id } };
}
