import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
export type Goal = components['schemas']['GoalView'];
async function list<T extends { items: unknown[] }>(path: string, fields: string[]): Promise<T> {
  const result = await request<T>(path);
  if (!Array.isArray(result.items) || !result.items.every((item) => object(item) && fields.every((key) => typeof item[key] === 'string'))) throw new Error('目标与资产列表未通过完整性校验');
  return result;
}
export const getGoals = () => list<components['schemas']['GoalList']>('/goals', ['id', 'name', 'policy_id', 'policy_version_id', 'deadline']);
export async function getAccounts() {
  const result = await request<components['schemas']['AccountSummary']>('/accounts/summary');
  if (typeof result.user_id !== 'string' || !Array.isArray(result.accounts) || !result.accounts.every((item) => object(item) && typeof item.id === 'string' && typeof item.name === 'string' && typeof item.account_type === 'string')) throw new Error('账户事实未通过完整性校验');
  return result;
}
export const getPositions = () => list<components['schemas']['PositionList']>('/positions', ['id', 'product_id', 'status']);
export const getProducts = () => list<components['schemas']['ProductList']>('/products', ['id', 'name', 'asset_class']);
export const createGoal = (body: components['schemas']['CreateGoalRequest']) => request<components['schemas']['GoalResponse']>('/goals', 'POST', body);
export const getGoalAllocation = (id: string) => request<components['schemas']['GoalAllocationResponse']>(`/goals/${encodeURIComponent(id)}/allocation-preview`);
export const getAssetAllocation = (id: string) => request<components['schemas']['AssetAllocationResponse']>(`/asset-policies/${encodeURIComponent(id)}/allocation-preview`);
