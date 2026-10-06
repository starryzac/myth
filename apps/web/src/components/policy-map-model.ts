import type { Configuration, Policy } from '../api/policies';
import { object } from '../features/policy-form';

export type PolicyVersion = NonNullable<Policy['current_version']>;
export type PolicyRelation = {
  id: string; sourceId: string; sourceVersionId: string; targetId: string;
  field: 'asset_policy_id' | 'must_not_reduce_policy_ids' | 'goal_id';
  label: string; targetKind: 'policy' | 'goal';
  state: 'REFERENCE_PRESENT' | 'MISSING_POLICY' | 'MISSING_VERSION' | 'WRONG_POLICY_TYPE' | 'UNAUTHORIZED_TARGET' | 'INACTIVE_TARGET' | 'GOAL_MAPPING_UNAVAILABLE';
};
export const relationStateLabels: Record<PolicyRelation['state'], string> = {
  REFERENCE_PRESENT: '配置引用存在，执行权限仍须服务端核验', MISSING_POLICY: '引用策略未在当前列表找到',
  MISSING_VERSION: '引用策略缺少当前版本', WRONG_POLICY_TYPE: '引用目标不是资产授权策略',
  UNAUTHORIZED_TARGET: '引用策略当前版本未满足有效授权条件', INACTIVE_TARGET: '引用策略当前未生效',
  GOAL_MAPPING_UNAVAILABLE: '目标归属引用，当前接口未提供目标与策略的对应关系',
};
export function buildPolicyRelations(policies: Policy[], replacement?: { policyId: string; version: PolicyVersion | null }) {
  const relations: PolicyRelation[] = []; const issues: { policyId: string; message: string }[] = [];
  const byId = new Map(policies.map((policy) => [policy.id, policy]));
  for (const policy of policies) {
    const version = replacement?.policyId === policy.id ? replacement.version : policy.current_version;
    if (!version || version.policy_id !== policy.id) {
      issues.push({ policyId: policy.id, message: '当前配置版本缺失或版本身份不匹配，关系未知。' }); continue;
    }
    const config: Configuration = version.configuration;
    const sourceVersionId = version.id;
    function add(targetId: unknown, field: PolicyRelation['field'], label: string, targetKind: 'policy' | 'goal') {
      if (targetId == null) return;
      if (typeof targetId !== 'string' || !targetId.trim() || targetId !== targetId.trim()) {
        issues.push({ policyId: policy.id, message: `${field} 引用格式待核验，未建立关系。` }); return;
      }
      const target = byId.get(targetId);
      const state: PolicyRelation['state'] = targetKind === 'goal' ? 'GOAL_MAPPING_UNAVAILABLE'
        : !target ? 'MISSING_POLICY' : !target.current_version || target.current_version.policy_id !== target.id ? 'MISSING_VERSION'
          : field === 'asset_policy_id' && target.policy_type !== 'asset_authorization' ? 'WRONG_POLICY_TYPE'
            : !target.version_authorized ? 'UNAUTHORIZED_TARGET' : target.effective_status !== 'ACTIVE' ? 'INACTIVE_TARGET' : 'REFERENCE_PRESENT';
      relations.push({ id: `${sourceVersionId}:${field}:${targetId}`, sourceId: policy.id, sourceVersionId, targetId, field, label, targetKind, state });
    }
    if (config.type === 'goal_saving') add(config.asset_policy_id, 'asset_policy_id', '关联资产授权', 'policy');
    if (Object.hasOwn(config, 'must_not_reduce_policy_ids')) {
      if (!Array.isArray(config.must_not_reduce_policy_ids)) issues.push({ policyId: policy.id, message: 'must_not_reduce_policy_ids 不是引用列表，保护依赖未知。' });
      else for (const id of new Set(config.must_not_reduce_policy_ids)) add(id, 'must_not_reduce_policy_ids', '不得降低的保护策略', 'policy');
    }
    if (config.type === 'asset_authorization' && config.scope === 'goal') {
      if (config.goal_id == null) issues.push({ policyId: policy.id, message: '指定目标的资产授权缺少 goal_id，目标引用未知。' });
      else add(config.goal_id, 'goal_id', '授权限于此目标归属', 'goal');
    }
  }
  return { relations, issues };
}
export function protectionFacts(configuration: Configuration): string[] {
  if (!object(configuration.priority)) return [];
  const priority = configuration.priority;
  return [typeof priority.minimum_cents === 'number' && Number.isSafeInteger(priority.minimum_cents) ? `最低保护 ${priority.minimum_cents} 分` : '最低保护字段待核验',
    priority.reducible === false ? '不允许降低承诺' : priority.reducible === true ? '允许降低承诺' : '降低承诺许可待核验',
    priority.deferrable === false ? '不允许延期' : priority.deferrable === true ? '允许延期' : '延期许可待核验'];
}
export function canEditPolicy(policy: Policy): boolean {
  return policy.current_version?.policy_id === policy.id && ['ACTIVE', 'CONFIRMED', 'SUSPENDED'].includes(policy.effective_status);
}
