/** Explicit synthetic domain/service-double output. No current source or financial proof. */
import raw from './goal-adjustment-fixtures.json';
import type { GoalAdjustmentRead, GoalAdjustmentPreview, GoalAdjustmentSelection } from '../api/goal-adjustments';
import type { Goal } from '../api/goals';
export const adjustmentRead = (): GoalAdjustmentRead => structuredClone(raw.read) as unknown as GoalAdjustmentRead;
export const adjustmentPreview = (): GoalAdjustmentPreview => structuredClone(raw.preview) as unknown as GoalAdjustmentPreview;
export const adjustmentSelection = (): GoalAdjustmentSelection => structuredClone(raw.selection) as GoalAdjustmentSelection;
export const adjustmentOwner = raw.read.user_id;
export function adjustmentGoals(): Goal[] { return raw.read.goals.map((row) => ({ id: row.goal_id, policy_id: row.policy_id, policy_version_id: row.current_version_id, name: 'SYNTHETIC_DIRECT_ONLY', target_cents: 50, deadline: row.deadline, monthly_min_cents: row.monthly_min_cents, monthly_target_cents: row.monthly_target_cents, monthly_max_cents: row.monthly_max_cents, importance: 50, account_id: '00000000-0000-0000-0000-000000000110', allocated_cents: row.current_owned_cents, owned_cash_cents: row.current_owned_cents, owned_principal_cents: 0, policy_status: 'ACTIVE', created_at: '2026-10-03T12:00:00Z' } as unknown as Goal)); }
