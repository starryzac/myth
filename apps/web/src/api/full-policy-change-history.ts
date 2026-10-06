import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { annualDayNumber, validateAnnualBoundary } from './planning';
import { seasonalHash, seasonalCanonicalJson } from './seasonal-reserve-adoptions';
import { parseFinancialPreviewBody } from './full-policy-financial-preview';
import type { FinancialPreviewBody } from './full-policy-financial-preview';
import type { components } from '../../../../packages/contracts/schema';

export type HistoryPreviewBody = FinancialPreviewBody;
export type HistoryPreviewBinding = { policyId: string; userId: string; expectedVersionId: string; expectedVersionNumber: number; expectedEpochId?: string };
type NativeHistoryPreview = components['schemas']['FullPolicyHistoryChangeFinancialPreview'];
type HistoryImpact = Required<NativeHistoryPreview['financial_impact']>;
export type HistoryProof = NonNullable<HistoryImpact['history_proof']>;
/** The reader requires these generated optional fields before returning its checked result. */
export type HistoryChangePreview = Omit<Required<NativeHistoryPreview>, 'financial_impact'> & {
  financial_impact: Omit<HistoryImpact, 'goals' | 'positions' | 'candidate_commitments'> & {
    goals: Required<HistoryImpact['goals'][number]>[];
    positions: Required<HistoryImpact['positions'][number]>[];
    candidate_commitments: Required<HistoryImpact['candidate_commitments'][number]>[];
  };
};
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value);
const hash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((row) => typeof row === 'string');
const ids = (value: unknown): value is string[] => strings(value) && value.every(uuid) && new Set(value).size === value.length;
const integer = (value: unknown, min = Number.MIN_SAFE_INTEGER): value is number => Number.isSafeInteger(value) && Number(value) >= min;
const nullable = (value: unknown, predicate: (item: unknown) => boolean) => value === null || predicate(value);
const phases = ['BEFORE_PAYMENT', 'AFTER_PAYMENT', 'AFTER_PRINCIPAL'] as const;
const originals = new WeakMap<object, string>();
export const getOriginalHistoryPreviewResponse = (value: object) => originals.get(value) ?? null;
function check(value: unknown): asserts value { if (!value) throw new Error('财务修改预览的原版本、完整三阶段、差额或只读边界未通过校验'); }
function validateBinding(binding: HistoryPreviewBinding): void {
  check(uuid(binding.policyId) && uuid(binding.userId) && uuid(binding.expectedVersionId) && integer(binding.expectedVersionNumber, 2) && binding.expectedVersionNumber <= 128 && (binding.expectedEpochId === undefined || uuid(binding.expectedEpochId)));
}
async function readHistoryProof(proof: unknown, response: Record<string, unknown>, binding: HistoryPreviewBinding, projected: boolean): Promise<void> {
  check(object(proof) && proof.protocol === 'full-future-dated-history-v1' && ['VERIFIED_FUTURE_DATED_HISTORY', 'UNKNOWN'].includes(String(proof.status)) && uuid(proof.user_id) && uuid(proof.epoch_id) && uuid(proof.policy_id) && uuid(proof.current_version_id) && hash(proof.current_content_hash) && time(proof.as_of) && ['Asia/Shanghai', 'UTC'].includes(String(proof.timezone)) && Number.isInteger(annualDayNumber(proof.today)) && strings(proof.reasons) && hash(proof.source_digest));
  check(proof.user_id === binding.userId && proof.epoch_id === response.epoch_id && proof.policy_id === binding.policyId && proof.current_version_id === binding.expectedVersionId);
  check(proof.unpaid_amount_proven === false && proof.settlement_proven === false && proof.grants_authority === false && proof.bank_authority === false);
  for (const key of ['actual_version_count', 'actual_command_count', 'expected_evidence_count']) check(nullable(proof[key], (value) => integer(value, 0)));
  for (const key of ['captured_version_count', 'captured_command_count', 'captured_evidence_count']) check(integer(proof[key], 0));
  for (const key of ['policy_original', 'user_original', 'epoch_original']) check(nullable(proof[key], object));
  check(Array.isArray(proof.versions) && proof.versions.every(object) && Array.isArray(proof.commands) && proof.commands.every(object) && Array.isArray(proof.evidence_originals) && proof.evidence_originals.every(object));
  const digestInput = { ...proof }; delete digestInput.status; delete digestInput.reasons; delete digestInput.source_digest;
  check(await seasonalHash(digestInput) === proof.source_digest);
  // Raw UNKNOWN originals may carry the exact disagreement being reported.
  // Only a PROJECTED current proof can be described as verified current history.
  if (!projected) return;
  check(proof.status === 'VERIFIED_FUTURE_DATED_HISTORY' && proof.reasons.length === 0 && proof.user_id === binding.userId && proof.epoch_id === response.epoch_id && proof.policy_id === binding.policyId && proof.current_version_id === binding.expectedVersionId && proof.current_content_hash === response.current_configuration_hash && time(response.as_of) && Date.parse(proof.as_of) === Date.parse(response.as_of));
  check(proof.actual_version_count === binding.expectedVersionNumber && proof.captured_version_count === proof.actual_version_count && proof.versions.length === proof.captured_version_count && proof.actual_command_count === proof.captured_command_count && proof.commands.length === proof.captured_command_count && proof.expected_evidence_count === proof.captured_evidence_count && proof.evidence_originals.length === proof.captured_evidence_count && integer(proof.actual_command_count, 1) && proof.captured_command_count <= 256 && integer(proof.expected_evidence_count, 1));
  const current = proof.versions.at(-1);
  check(object(current) && current.id === binding.expectedVersionId && current.user_id === binding.userId && current.policy_id === binding.policyId && current.version_number === binding.expectedVersionNumber && current.content_hash === response.current_configuration_hash && object(current.configuration) && seasonalCanonicalJson(current.configuration) === seasonalCanonicalJson(response.before_configuration) && object(current.confirmation));
  const confirmation = current.confirmation;
  check(confirmation.protocol === 'full-policy-confirmation-v1' && confirmation.user_id === binding.userId && confirmation.epoch_id === response.epoch_id && confirmation.policy_id === binding.policyId && confirmation.version_id === binding.expectedVersionId && confirmation.template_name === 'DatedExpensePolicy' && confirmation.reviewed_hash === response.current_configuration_hash && confirmation.accepted === true && confirmation.bank_authority === false && uuid(confirmation.confirmation_evidence_id) && hash(confirmation.request_hash) && time(confirmation.confirmed_at) && Date.parse(confirmation.confirmed_at) <= Date.parse(proof.as_of));
  check(object(current.configuration.window) && annualDayNumber(current.configuration.window.start) > annualDayNumber(proof.today));
  check(object(proof.policy_original) && proof.policy_original.id === binding.policyId && proof.policy_original.user_id === binding.userId && proof.policy_original.epoch_id === response.epoch_id && object(proof.user_original) && proof.user_original.id === binding.userId && proof.user_original.is_simulated === true && object(proof.epoch_original) && proof.epoch_original.id === response.epoch_id && proof.epoch_original.user_id === binding.userId && proof.epoch_original.status === 'OPEN');
}
function centsMap(value: unknown, nonnegative = false): asserts value is Record<string, number> {
  check(object(value) && Object.entries(value).every(([id, cents]) => uuid(id) && integer(cents, nonnegative ? 0 : Number.MIN_SAFE_INTEGER)));
}
function difference(after: number, before: number, delta: unknown) { check(integer(delta) && BigInt(delta) === BigInt(after) - BigInt(before)); }
/** Server may fill DSL defaults; every submitted field must still describe this candidate. */
function candidateFieldsMatch(submitted: unknown, canonical: unknown): boolean {
  if (Array.isArray(submitted)) return Array.isArray(canonical) && submitted.length === canonical.length && submitted.every((row, index) => candidateFieldsMatch(row, canonical[index]));
  if (object(submitted)) return object(canonical) && Object.entries(submitted).every(([key, row]) => Object.hasOwn(canonical, key) && candidateFieldsMatch(row, canonical[key]));
  if (time(submitted) && time(canonical)) return Date.parse(submitted) === Date.parse(canonical);
  return submitted === canonical;
}
/** Validate the original server curve, not a client-side financial recomputation. */
function beforeCurve(value: unknown): void {
  check(object(value) && Array.isArray(value.calculation_trace));
  if (value.status === 'INSUFFICIENT_EVIDENCE') { validateAnnualBoundary(value, 0, 365); return; }
  check(value.calculation_trace.length === 1098 && object(value.calculation_trace[0]));
  const first = annualDayNumber(value.calculation_trace[0].date); validateAnnualBoundary(value, first, 365);
  value.calculation_trace.forEach((row, index) => check(object(row) && row.day === Math.floor(index / 3) && row.phase === phases[index % 3]));
  if (value.safe_idle_cents !== null) check(integer(value.safe_idle_cents, 0));
  check(object(value.max_allocatable_by_product) && Object.entries(value.max_allocatable_by_product).every(([id, cents]) => uuid(id) && nullable(cents, (item) => integer(item, 0))));
}
export async function parseHistoryChangePreview(value: unknown, binding: HistoryPreviewBinding, body: HistoryPreviewBody, raw?: string): Promise<HistoryChangePreview> {
  validateBinding(binding); const { policyId, userId, expectedVersionId } = binding; check(body.expected_version_id === expectedVersionId);
  parseFinancialPreviewBody(body); assertMoneyFields(value);
  check(uuid(policyId) && object(value) && value.protocol === 'full-policy-change-history-preview-v2' && value.user_id === userId && (binding.expectedEpochId === undefined || value.epoch_id === binding.expectedEpochId) && value.simulation === true && value.hypothetical === true && value.grants_authority === false && value.policy_id === policyId && value.expected_version_id === body.expected_version_id && uuid(value.epoch_id) && time(value.as_of));
  check(hash(value.configuration_hash) && hash(value.current_configuration_hash) && hash(value.current_fact_digest) && object(value.before_configuration) && object(value.after_configuration) && candidateFieldsMatch(body.configuration, value.after_configuration) && strings(value.changed_fields) && Array.isArray(value.reference_snapshots) && value.reference_snapshots.every(object));
  if (object(value.after_configuration)) check(await seasonalHash(value.after_configuration) === value.configuration_hash && await seasonalHash(value.before_configuration) === value.current_configuration_hash);
  check(ids(value.original_action_ids) && ids(value.original_position_ids) && value.action_impact === 'ORIGINAL_ACTIONS_UNCHANGED_REQUIRES_FRESH_RECOMPUTATION_AFTER_CONFIRMATION');
  const originalPositionIds = value.original_position_ids;
  const p = value.financial_impact;
  check(object(p) && p.protocol === 'full-policy-financial-impact-history-v2' && p.user_id === userId && p.epoch_id === value.epoch_id && p.receipt_is_current_authority === false && p.simulation === true && p.hypothetical === true && p.grants_authority === false && p.writes_policy_or_bank === false && p.future_income_cents === 0 && p.horizon_days === 365 && p.initial_day_and_365_future_days === true && p.basis === 'VERIFIED_FUTURE_DATED_HISTORY_CONSERVATIVE_REPLACEMENT' && ['PROJECTED', 'UNKNOWN'].includes(String(p.status)) && hash(p.input_hash) && strings(p.reasons) && strings(p.limitations) && strings(p.retained_original_occurrence_ids) && new Set(p.retained_original_occurrence_ids).size === p.retained_original_occurrence_ids.length);
  const retainedIds = p.retained_original_occurrence_ids;
  if (p.history_proof !== null) await readHistoryProof(p.history_proof, value, binding, p.status === 'PROJECTED');
  if (p.status === 'PROJECTED') check(p.history_proof !== null && value.before_configuration.type === 'dated_expense' && value.after_configuration.type === 'dated_expense');
  if (p.before !== null) beforeCurve(p.before);
  check(Array.isArray(p.goals) && p.goals.every((row) => object(row) && uuid(row.goal_id) && integer(row.current_owned_cash_cents, 0) && integer(row.current_owned_principal_cents, 0) && row.current_allocation_delta_cents === 0 && row.current_principal_delta_cents === 0 && row.future_allocation_cents === null && row.future_allocation_status === 'UNKNOWN_NO_CANDIDATE_GOAL_SOLVER' && ids(row.original_evidence_ids)) && new Set(p.goals.map((row) => row.goal_id)).size === p.goals.length);
  check(Array.isArray(p.positions) && p.positions.every((row) => object(row) && uuid(row.position_id) && nullable(row.goal_id, uuid) && integer(row.original_recorded_principal_cents, 0) && integer(row.current_outstanding_principal_cents, 0) && row.current_principal_delta_cents === 0 && typeof row.original_status === 'string' && row.current_outstanding_principal_cents === (row.original_status === 'REDEEMED' ? 0 : row.original_recorded_principal_cents) && nullable(row.original_principal_available_at, time) && ids(row.original_evidence_ids) && row.future_disposition_status === 'UNKNOWN_NO_CANDIDATE_ACTION_GENERATION') && new Set(p.positions.map((row) => row.position_id)).size === p.positions.length && p.positions.every((row) => originalPositionIds.includes(row.position_id)));
  check(Array.isArray(p.candidate_commitments) && p.candidate_commitments.every((row) => object(row) && typeof row.identity === 'string' && row.identity.length > 0 && nullable(row.original_occurrence_id, (item) => typeof item === 'string') && uuid(row.policy_id) && row.source_kind === 'UNCONFIRMED_CANDIDATE' && row.kind === 'DATED_EXPENSE' && integer(row.amount_cents, 0) && nullable(row.source_account_id, uuid) && row.bank_authority === false && Number.isInteger(annualDayNumber(row.due_date)) && (row.source_kind === 'UNCONFIRMED_CANDIDATE' ? row.original_occurrence_id === null && row.policy_id === policyId : row.original_occurrence_id === row.identity && retainedIds.includes(row.identity))) && new Set(p.candidate_commitments.map((row) => row.identity)).size === p.candidate_commitments.length);
  if (p.status === 'UNKNOWN') check(p.reasons.length > 0 && p.after === null && p.delta_safe_idle_cents === null && p.delta_minimum_margin_cents === null && p.delta_max_allocatable_by_product === null);
  else {
    const before = p.before; const after = p.after;
    check(object(before) && before.status !== 'INSUFFICIENT_EVIDENCE' && integer(before.safe_idle_cents, 0) && integer(before.minimum_margin_cents) && Array.isArray(before.calculation_trace) && object(after) && ['READY', 'LIQUIDITY_RISK'].includes(String(after.status)) && after.financial_capacity_is_authority === false && integer(after.safe_idle_cents, 0) && integer(after.minimum_margin_cents) && strings(after.source_account_limitations) && hash(after.curve_hash) && Array.isArray(after.calculation_trace) && after.calculation_trace.length === 1098 && p.reasons.length === 0);
    centsMap(before.max_allocatable_by_product, true); centsMap(after.max_allocatable_by_product, true); centsMap(p.delta_max_allocatable_by_product);
    check(before.minimum_margin_cents === Math.min(...before.calculation_trace.map((row) => Number(row.margin_cents))) && before.safe_idle_cents <= Math.max(0, before.minimum_margin_cents));
    check(Object.keys(before.max_allocatable_by_product).sort().join('|') === Object.keys(after.max_allocatable_by_product).sort().join('|') && Object.keys(before.max_allocatable_by_product).sort().join('|') === Object.keys(p.delta_max_allocatable_by_product).sort().join('|'));
    for (const [id, cents] of Object.entries(after.max_allocatable_by_product)) difference(cents, before.max_allocatable_by_product[id]!, p.delta_max_allocatable_by_product[id]);
    difference(after.safe_idle_cents, before.safe_idle_cents, p.delta_safe_idle_cents); difference(after.minimum_margin_cents, before.minimum_margin_cents, p.delta_minimum_margin_cents);
    const beforePoints = before.calculation_trace;
    after.calculation_trace.forEach((row, index) => {
      const original = beforePoints[index];
      check(object(original) && object(row) && row.day === original.day && row.date === original.date && row.phase === original.phase && integer(row.cash_cents) && integer(row.margin_cents) && object(row.protected_cents_by_reason) && Object.values(row.protected_cents_by_reason).every((cents) => integer(cents, 0)) && strings(row.obligation_occurrence_ids) && ids(row.principal_position_ids) && JSON.stringify(row.principal_position_ids) === JSON.stringify(original.principal_position_ids));
      check(BigInt(row.margin_cents) === BigInt(row.cash_cents) - Object.values(row.protected_cents_by_reason).reduce<bigint>((sum, cents) => sum + BigInt(cents as number), 0n));
    });
    check(after.minimum_margin_cents === Math.min(...after.calculation_trace.map((row) => Number(row.margin_cents))) && after.safe_idle_cents <= Math.max(0, after.minimum_margin_cents) && (after.minimum_margin_cents < 0 ? after.status === 'LIQUIDITY_RISK' : true));
    check(await seasonalHash({ protocol: 'hypothetical-full-curve-history-v2', input_hash: p.input_hash, trace: after.calculation_trace }) === after.curve_hash);
    check(p.positions.length === value.original_position_ids.length);
  }
  const result = value as unknown as HistoryChangePreview; if (raw !== undefined) originals.set(result, raw); return result;
}
export async function previewFullPolicyHistoryFinancialChange(binding: HistoryPreviewBinding, body: HistoryPreviewBody): Promise<HistoryChangePreview> {
  validateBinding(binding); parseFinancialPreviewBody(body); check(body.expected_version_id === binding.expectedVersionId);
  const original = await request<{ simulation: true; value: unknown; raw: string }>(`/full-policies/${binding.policyId}/financial-change-preview-history`, 'POST', body, (value, raw) => ({ simulation: true, value, raw }));
  return parseHistoryChangePreview(original.value, binding, body, original.raw);
}
