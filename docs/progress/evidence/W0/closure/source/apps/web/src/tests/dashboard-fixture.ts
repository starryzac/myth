import type { Dashboard } from '../api/dashboard';

/** HTTP unit-test fixture only; never a bank/API/Edge runtime result. */
export function dashboardFixture(): Dashboard {
  const user = '10000000-0000-0000-0000-000000000001';
  return {
    schema_version: 'dashboard-v1', simulation: true, user_id: user,
    as_of: '2026-10-04T02:00:00Z', timezone: 'Asia/Shanghai',
    account_facts: {
      state: 'PROVEN', bank_projection_state: 'MATCHED', issues: [],
      facts: {
        simulation: true, user_id: user, timezone: 'Asia/Shanghai',
        oldest_account_observed_at: '2026-10-04T02:00:00Z', latest_account_observed_at: '2026-10-04T02:00:00Z',
        accounts: [{ id: '10000000-0000-0000-0000-000000000002', external_ref: 'unit-account',
          name: '单元测试现金账户', account_type: 'CASH', bank_code: 'ICBC', currency: 'CNY',
          balance_cents: 500000, observed_at: '2026-10-04T02:00:00Z' }],
        credit_card_bills: [], cash_balance_cents: 500000, position_principal_cents: 0,
        unknown_position_principal_cents: 0, credit_card_unpaid_cents: 0,
      },
    },
    boundary: {
      state: 'PROVEN', financial_only: true, status: 'READY', safe_idle_cents: 380000,
      minimum_margin_cents: 380000, deficit_cents: 0,
      protected_cents_by_reason: { obligations: 1, living: 2, emergency: 3, goal_cash: 4, goal_minimum: 5 },
      current_protected_cents: 120000,
      current_protected_cents_by_reason: { obligations: 70000, living: 10000, emergency: 20000, goal_cash: 0, goal_minimum: 20000 },
      current_margin_cents: 380000, constraining_date: '2026-10-04',
      window_start: '2026-10-04', window_end: '2027-01-02', input_digest: 'unit-input-digest',
      boundary_hash: 'a'.repeat(64), blocking_constraints: [], calculation_notes: [], issues: [],
    },
    goal_ownership: { state: 'PROVEN', cash_owned_cents: 0, principal_owned_cents: 0,
      allocated_cents: 0, unassigned_goal_cash_cents: 0, items: [], issues: [] },
    managed_assets: { state: 'PROVEN', managed_current_principal_cents: 0, general_principal_cents: 0,
      held_or_matured_cents: 0, redeeming_cents: 0, pending_purchase_cents: 0, by_goal: [],
      excluded_manual_count: 2, unknown_position_count: 0, evidence_ids: [], issues: [] },
    next_obligations: {
      status: 'PROVEN', selection_scope: 'KNOWN_PROTECTION_COMMITMENTS_DUE_BY_WINDOW_END_INCLUDING_OVERDUE',
      next_due_date: '2026-10-05', next_count: 1, next_remaining_protection_cents: 20000,
      basis_summary: 'EXACT', items_complete: true,
      items: [{ occurrence_id: 'unit-rent-2026-10', kind: 'RECURRING_ORDINARY', bill_id: null,
        account_id: null, policy_id: '10000000-0000-0000-0000-000000000003',
        policy_version_id: '10000000-0000-0000-0000-000000000004', period: '2026-10', payee_id: '房租',
        due_date: '2026-10-05', projection_payment_date: '2026-10-05', overdue: false,
        protected_total_cents: 20000, remaining_protection_cents: 20000, total_basis: 'POLICY_EXACT',
        actual_final_total_cents: null, paid_cents: null, payment_fact: 'NO_IMPORT_CURRENT_OR_FUTURE', evidence_ids: [] }],
    },
    pending_actions: { state: 'PROVEN', total: 0, items: [], list_complete: true, has_more: false },
    recovery_proposals: { state: 'PROVEN', total: 0, items: [], list_complete: true, has_more: false },
    intervention: { status: 'NONE', known_required_count: 0, complete: true, reason_codes: [] },
    audit: { scope: 'CURRENT_LIVE_EPOCH', epoch_id: '10000000-0000-0000-0000-000000000005',
      status: 'VALID', anchored_run_statuses: {}, complete: true }, source_evidence_ids: [],
  };
}
