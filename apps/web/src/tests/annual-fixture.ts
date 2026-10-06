/** Synthetic HTTP-reader/component fixture; not annual financial or bank runtime evidence. */
import type { AnnualPlanning, AnnualPoint } from '../api/planning';
export function annualFixture(unknown = false): AnnualPlanning {
  const digest = 'a'.repeat(64); const first = Date.parse('2026-10-05T00:00:00Z');
  const point = (day: number, phase: AnnualPoint['phase']): AnnualPoint => ({ day, phase, date: new Date(first + day * 86400000).toISOString().slice(0, 10),
    cash_cents: phase === 'BEFORE_PAYMENT' ? 100001 : phase === 'AFTER_PAYMENT' ? 99001 : 99501,
    margin_cents: phase === 'BEFORE_PAYMENT' ? 80001 : phase === 'AFTER_PAYMENT' ? 79001 : 79501,
    protected_cents_by_reason: { synthetic_reserve: 20000 }, obligation_occurrence_ids: [], principal_position_ids: [] });
  const checkpoints = Array.from({ length: 366 }, (_, day) => ({ day, date: new Date(first + day * 86400000).toISOString().slice(0, 10),
    status: unknown ? 'NOT_PROVEN' as const : 'PROVEN' as const, before_payment: unknown ? null : point(day, 'BEFORE_PAYMENT'),
    after_payment: unknown ? null : point(day, 'AFTER_PAYMENT'), after_principal: unknown ? null : point(day, 'AFTER_PRINCIPAL'), minimum_intraday_margin_cents: unknown ? null : 79001 }));
  const boundary = (horizon: number): AnnualPlanning['annual_projection'] => ({ algorithm_version: 'HTTP_UNIT_FIXTURE_NOT_FINANCIAL_PROOF', status: unknown ? 'INSUFFICIENT_EVIDENCE' : 'READY', financial_only: true,
    safe_idle_cents: unknown ? null : 79001, minimum_margin_cents: unknown ? null : 79001, deficit_cents: unknown ? null : 0, protected_cents_by_reason: unknown ? {} : { synthetic_reserve: 20000 },
    max_allocatable_by_product: {}, blocking_constraints: [], calculation_trace: unknown ? [] : Array.from({ length: horizon + 1 }, (_, day) => [point(day, 'BEFORE_PAYMENT'), point(day, 'AFTER_PAYMENT'), point(day, 'AFTER_PRINCIPAL')]).flat(),
    boundary_hash: digest, calculation_notes: ['HTTP_UNIT_FIXTURE_NOT_FINANCIAL_PROOF'] });
  return { schema_version: 'annual-planning-v1', simulation: true, user_id: '10000000-0000-0000-0000-000000000001', as_of: '2026-10-05T12:00:00Z', timezone: 'Asia/Shanghai', horizon_days: 365,
    grants_authority: false, projection_basis: 'CURRENT_VERIFIED_FACTS_CONDITIONAL_COMMITMENTS', future_points_are_settled_cash: false, execution_view_horizon_days: 90,
    execution_view: boundary(90), annual_projection: boundary(365), initial_checkpoint: checkpoints[0]!, daily_checkpoints: checkpoints.slice(1),
    future_income: { status: 'NOT_IMPLEMENTED_NO_REGISTERED_SOURCE', included_in_execution_cents: 0, included_in_planning_cents: 0, reason: 'HTTP夹具：未来收入未接入，不是实测收入预测' },
    unavailable_principal: [{ position_id: '10000000-0000-0000-0000-000000000010', reason: 'NO_VERIFIED_RETURN_DATE' }], source_evidence_ids: ['10000000-0000-0000-0000-000000000011'], input_digest: digest,
    source_issues: unknown ? [{ code: 'UNIT_SOURCE_NOT_PROVEN', source_ref: 'synthetic-source', message: 'HTTP夹具：原件不足' }] : [],
    audit: { scope: 'CURRENT_LIVE_EPOCH', epoch_id: '10000000-0000-0000-0000-000000000012', status: unknown ? 'INCOMPLETE' : 'VALID', complete: !unknown, anchored_run_statuses: {} } };
}
