import type { components } from '../../../../packages/contracts/schema';
import { parseDashboard, type Dashboard } from '../api/dashboard';
import { request } from '../api/http';
import { isRunId } from '../api/decisions';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';

export type Action = components['schemas']['ActionResponse'];
export type Goal = components['schemas']['GoalView'];
export type AllocationPreview = components['schemas']['GoalAllocationResponse'];
export type Scenario = 'SAFE' | 'REVOKED' | 'RESPONSE_LOSS';
export type Activity = { id: string; at: string; intent: string; authorization: string; decision: string; amount_cents: number | null; status: string; action_id: string | null; scenario?: Scenario | null; goal_id?: string | null };
export type State = { simulation: true; environment_id: string; epoch_id: string; dashboard: Dashboard; goals: Goal[]; actions: Action[]; activity: Activity[]; income_received: boolean };
export type Presets = { simulation: true; preset_version: 'zhiyu-v1'; intents: { id: 'EMERGENCY' | 'TRAVEL'; title: string; text: string }[]; income_cents: number };
const check = (value: unknown): void => { if (!value) throw new Error('知余响应无法完整核对，请保留原操作并重新读取。'); };
const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const time = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d\d:\d\d)$/.test(value);
export function parseAction(value: unknown): Action {
  assertMoneyFields(value);
  check(object(value) && value.simulation === true && isRunId(value.action_id) && isRunId(value.user_id) && digest(value.effect_hash) && object(value.effect) && object(value.prepared_validation));
  if (!object(value) || !object(value.effect)) throw new Error('缺少原动作');
  const effect = value.effect;
  check(effect.operation_id === value.action_id && effect.user_id === value.user_id && effect.simulation === true && typeof value.status === 'string' && typeof value.autonomy_level === 'string' && ['amount_cents', 'fee_cents', 'loss_cents'].every((key) => Number.isSafeInteger(effect[key]) && Number(effect[key]) >= 0));
  check(time(effect.valid_from) && time(effect.expires_at) && object(value.prepared_validation) && value.prepared_validation.effect_hash === value.effect_hash && value.prepared_validation.simulation === true && value.prepared_validation.financial_only === true && object(value.prepared_validation.baseline_boundary));
  if (value.receipt !== null) check(object(value.receipt) && value.receipt.simulation === true && value.receipt.action_id === value.action_id && isRunId(value.receipt.receipt_id) && isRunId(value.receipt.bank_operation_id));
  return value as Action;
}
export function parseState(value: unknown): State {
  assertMoneyFields(value);
  check(object(value) && value.simulation === true && typeof value.environment_id === 'string' && value.environment_id.length > 0 && value.environment_id.length <= 160 && isRunId(value.epoch_id) && typeof value.income_received === 'boolean' && Array.isArray(value.goals) && Array.isArray(value.actions) && Array.isArray(value.activity));
  if (!object(value) || !Array.isArray(value.goals) || !Array.isArray(value.actions) || !Array.isArray(value.activity)) throw new Error('缺少服务端状态');
  const dashboard = parseDashboard(value.dashboard);
  for (const row of value.goals) check(object(row) && isRunId(row.id) && isRunId(row.policy_id) && isRunId(row.policy_version_id) && typeof row.name === 'string' && ['target_cents', 'allocated_cents', 'monthly_min_cents', 'monthly_target_cents', 'monthly_max_cents'].every((key) => Number.isSafeInteger(row[key])));
  for (const row of value.actions) check(parseAction(row).user_id === dashboard.user_id);
  for (const row of value.activity) check(object(row) && typeof row.id === 'string' && time(row.at) && ['intent', 'authorization', 'decision', 'status'].every((key) => typeof row[key] === 'string') && (row.amount_cents === null || Number.isSafeInteger(row.amount_cents)) && (row.action_id === null || isRunId(row.action_id)) && (row.scenario === undefined || row.scenario === null || ['SAFE', 'REVOKED', 'RESPONSE_LOSS'].includes(String(row.scenario))) && (row.goal_id === undefined || row.goal_id === null || isRunId(row.goal_id)));
  return { ...value, dashboard } as State;
}
export async function getState(): Promise<State> { return request('/zhiyu/state', 'GET', undefined, parseState); }
export async function getAllocationPreview(goal: Goal, user: string): Promise<AllocationPreview> {
  const value = await request<AllocationPreview>(`/goals/${goal.id}/allocation-preview`);
  check(value.goal_id === goal.id && value.user_id === user && object(value.allocation) && value.allocation.goal_id === goal.id && value.allocation.policy_version_id === goal.policy_version_id && value.allocation.preview_only === true && value.allocation.financial_only === true && object(value.allocation.baseline_boundary));
  return value;
}
export async function getPresets(): Promise<Presets> {
  const value = await request<Presets>('/zhiyu/presets');
  check(value.preset_version === 'zhiyu-v1' && value.income_cents === 200000 && Array.isArray(value.intents) && value.intents.length === 2 && new Set(value.intents.map((row) => row.id)).size === 2 && value.intents.every((row) => ['EMERGENCY', 'TRAVEL'].includes(row.id) && typeof row.title === 'string' && typeof row.text === 'string'));
  return value;
}
export async function readAction(id: string, hash: string): Promise<Action> {
  check(isRunId(id) && digest(hash)); const value = await request(`/actions/${id}`, 'GET', undefined, parseAction);
  check(value.action_id === id && value.effect_hash === hash); return value;
}
export async function postAction(path: string, body: Record<string, unknown>): Promise<Action> { return request(path, 'POST', body, parseAction); }
export function terminal(action: Action): boolean {
  return ['SUCCEEDED', 'RECONCILED'].includes(action.status) ? action.bank_status === 'SETTLED' && action.receipt !== null
    : ['BLOCKED', 'REJECTED', 'FAILED', 'INVALIDATED', 'CANCELLED', 'EXPIRED'].includes(action.status) && [null, 'REJECTED'].includes(action.bank_status ?? null) && action.receipt === null;
}
export const statusText: Record<string, string> = { PLANNED: '待确认', AUTHORIZED: '已确认', SUBMITTED: '结果待核实', UNKNOWN: '结果待核实', SUCCEEDED: '已完成', RECONCILED: '已完成', BLOCKED: '已拒绝', REJECTED: '已拒绝', INVALIDATED: '权限已失效', FAILED: '已拒绝', CANCELLED: '已取消', EXPIRED: '已过期', COMPLETED: '已完成', SETTLED: '已到账', ACTIVE: '生效中', CONFIRMED: '已确认', SUSPENDED: '已暂停', REVOKED: '已撤销', PROPOSED: '待确认' };
