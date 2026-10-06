import { actionFixture, epochId } from '../tests/demo-fixture';
import { dashboardFixture } from '../tests/dashboard-fixture';
import { policyFixture, policyId, versionId } from '../tests/policy-fixture';
import type { Action, Goal, Presets, State } from './api';

// Typed HTTP fixtures only. These are not bank, PostgreSQL or browser evidence.
export const goalId = '10000000-0000-0000-0000-000000000110';
export function goalFixture(): Goal { return { id: goalId, policy_id: policyId, policy_version_id: versionId, account_id: '10000000-0000-0000-0000-000000000111', name: '旅行目标', target_cents: 1200000, allocated_cents: 0, deadline: '2027-06-30', monthly_min_cents: 100000, monthly_target_cents: 100000, monthly_max_cents: 100000, importance: 50, minimum_protection_cents: 0, reducible: false, deferrable: false, cross_goal_reallocation_allowed: false, asset_policy_id: null }; }
export function zhiyuState(): State { return { simulation: true, environment_id: 'zhiyu-unit-isolated', epoch_id: epochId, dashboard: dashboardFixture(), goals: [], actions: [], activity: [], income_received: false }; }
export function zhiyuPresets(): Presets { return { simulation: true, preset_version: 'zhiyu-v1', income_cents: 200000, intents: [{ id: 'EMERGENCY', title: '保留应急金', text: '保留3000元应急金' }, { id: 'TRAVEL', title: '旅行目标', text: '旅行目标1.2万元，截止2027-06-30，每月固定储备1000元。' }] }; }
export function zhiyuAction(status = 'AUTHORIZED'): Action {
  const value = actionFixture(); value.status = status;
  value.effect.action_type = 'ALLOCATE_GOAL'; value.effect.amount_cents = 100000; value.effect.fee_cents = 0; value.effect.loss_cents = 0; value.effect.net_cents = 100000; value.effect.goal_id = goalId; value.effect.policy_id = policyId; value.effect.policy_version_id = versionId;
  value.effect.income_uses = [{ origin_transaction_id: '10000000-0000-0000-0000-000000000112', account_id: dashboardFixture().account_facts.facts.accounts[0]!.id, fragment_id: '10000000-0000-0000-0000-000000000113', amount_cents: 100000 }];
  if (status === 'SUCCEEDED') {
    value.bank_status = 'SETTLED';
    value.receipt = { simulation: true, receipt_id: '10000000-0000-0000-0000-000000000114', action_id: value.action_id, bank_operation_id: '10000000-0000-0000-0000-000000000115', status: 'SUCCEEDED', executed_cents: 100000, fee_cents: 0, loss_cents: 0, posting_ids: [], occurred_at: value.as_of, reconciled_at: null };
  } else value.bank_status = status === 'UNKNOWN' ? 'UNKNOWN' : null;
  return value;
}
export const travelPolicyFixture = () => policyFixture('goal_saving');
