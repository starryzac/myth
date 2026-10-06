import type { components } from '../../../../packages/contracts/schema';
import type { DemoAction, DemoCommand, DemoPresets, DemoState, DemoTemplate, DemoTemplateKind } from '../api/demo';
import { dashboardFixture } from './dashboard-fixture';
import { hash, policyId, versionId } from './policy-fixture';
// Typed HTTP unit fixtures only: no bank, PG or browser acceptance evidence.
export const epochId = '10000000-0000-0000-0000-000000000090';
export const newEpochId = '10000000-0000-0000-0000-000000000091';
export const commandId = '10000000-0000-0000-0000-000000000092';
export const demoActionId = '10000000-0000-0000-0000-000000000093';
export const demoProposalId = '10000000-0000-0000-0000-000000000094';
export const time = '2026-10-04T00:00:00Z';
export function templateFixture(kind: DemoTemplateKind): DemoTemplate {
  const configuration = kind === 'CAR_GOAL' ? { type: 'goal_saving', name: '单元买车目标', target_cents: 3000000, deadline: '2027-10-31',
    monthly_contribution: { min_cents: 10000, target_cents: 200000, max_cents: 200000 }, priority: { importance: 60, minimum_cents: 0, reducible: true, deferrable: true }, cross_goal_reallocation_allowed: false, asset_policy_id: null }
    : kind === 'RENT' ? { type: 'recurring_obligation', name: '单元房租保护', payee_id: 'unit-landlord', amount_rule: { kind: 'exact', amount_cents: 150000 },
      due_day: 15, prepare_days_before: 5, auto_execute: false, priority: { importance: 100, minimum_cents: 150000, reducible: false, deferrable: false }, valid_from: null, valid_until: null }
      : { type: 'asset_authorization', name: kind === 'FIXED_ASSET' ? '单元固定期限与损失需确认' : '单元无损自动恢复', scope: 'general_idle_funds',
        allowed_asset_classes: [kind === 'FIXED_ASSET' ? 'FIXED_DEPOSIT' : 'CASH_MGMT_T0'], max_auto_managed_cents: kind === 'FIXED_ASSET' ? 50000 : 250000, single_action_cap_cents: kind === 'FIXED_ASSET' ? 50000 : 250000,
        max_redemption_delay_days: 0, max_lock_days: kind === 'FIXED_ASSET' ? 30 : 0, max_principal_risk_level: 0, allow_auto_recovery_without_penalty: true, allow_early_withdrawal_with_penalty: kind === 'FIXED_ASSET', goal_id: null, valid_from: null, valid_until: null };
  return { simulation: true, kind, title: String(configuration.name), configuration, configuration_hash: hash, status: 'NOT_PREPARED', proposal_id: null, evidence_id: null, confirmed_policy_id: null };
}
export function presetsFixture(): DemoPresets {
  return { simulation: true, preset_version: 'demo-console-v1', templates: ['CAR_GOAL', 'LIQUID_ASSET', 'FIXED_ASSET', 'RENT'].map((kind) => templateFixture(kind as DemoTemplateKind)),
    events: [
      { event_kind: 'SALARY_RECEIVED', title: '工资到账', description: '单元HTTP夹具：固定工资与实际引擎结果分离', required_templates: ['CAR_GOAL', 'LIQUID_ASSET'], amount_cents: 200000 },
      { event_kind: 'CREATE_CAR_GOAL', title: '新建买车目标', description: '零归属目标', required_templates: ['CAR_GOAL'], amount_cents: null },
      { event_kind: 'LARGE_CONSUMPTION', title: '用户大额消费', description: '服务端核验正余量后注入', required_templates: [], amount_cents: null },
      { event_kind: 'AUTO_REDEEM', title: '自动赎回', description: '实际无损恢复', required_templates: [], amount_cents: null },
      { event_kind: 'FIXED_EARLY_WITHDRAWAL', title: '定存提前支取需确认', description: '具体经济后果再次明确确认', required_templates: ['FIXED_ASSET'], amount_cents: null },
      { event_kind: 'CHANGE_RENT', title: '修改房租策略', description: '完整版本修改确认', required_templates: ['RENT'], amount_cents: null },
      { event_kind: 'RESET', title: '恢复演示初始状态', description: '销毁投影并封存旧审计，无新增授权', required_templates: [], amount_cents: null },
    ] };
}
export function stateFixture(): DemoState { return { simulation: true, preset_version: 'demo-console-v1', epoch_id: epochId, available: true, reason: null, templates: presetsFixture().templates, commands: [] }; }
export function commandFixture(event_kind: DemoCommand['event_kind'] = 'SALARY_RECEIVED'): DemoCommand {
  return { simulation: true, preset_version: 'demo-console-v1', command_id: commandId, epoch_id: epochId, event_kind, admitted_at: time,
    status: 'WAITING_TEMPLATE', message: '单元HTTP夹具：请明确确认所需完整模板', proposal_ids: [], goal_id: null, fact: null, actions: [], recovery: null, policy_change: null };
}
export function boundaryFixture(): components['schemas']['BoundaryResult'] {
  return { algorithm_version: 'unit-boundary', status: 'READY', financial_only: true, safe_idle_cents: 10000, minimum_margin_cents: -1200, deficit_cents: 1200,
    protected_cents_by_reason: {}, max_allocatable_by_product: {}, blocking_constraints: [], calculation_trace: [], boundary_hash: hash, calculation_notes: ['单元HTTP夹具，不是金融计算'] };
}
export function actionFixture(): DemoAction {
  const user_id = dashboardFixture().user_id;
  return { simulation: true, user_id, action_id: demoActionId, decision_run_id: commandId, status: 'PLANNED', autonomy_level: 'ASK_ONCE', effect_hash: hash, prepared_at: time, as_of: time,
    effect: { simulation: true, operation_id: demoActionId, user_id, business_key: 'unit-original-fixed-exit', action_type: 'REDEEM_ASSET', amount_cents: 50000, cash_uses: [], income_uses: [], fee_cents: 100, loss_cents: 500, net_cents: 49400,
      position_id: policyId, position_account_id: policyId, return_account_id: versionId, product_id: policyId, product_version_number: 1, terms_digest: hash, quote_id: versionId, settlement_delay_days: 0, latest_arrival_at: time, valid_from: time, expires_at: '2026-10-04T00:15:00Z', original_policy_version_id: versionId, policy_version_id: versionId },
    prepared_validation: { simulation: true, financial_only: true, status: 'CONFIRMATION_REQUIRED', effect_hash: hash, baseline_boundary: boundaryFixture(), reasons: ['UNIT_LOSS_CONFIRMATION'] }, bank_status: null, receipt: null };
}
