import type { components } from '../../../../packages/contracts/schema';
import { annualDayNumber, validateAnnualBoundary } from './planning';
import { request } from './http';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';

export type FullAnnualPlanning = components['schemas']['FullAnnualProtectionResponse'];
const originals = new WeakMap<FullAnnualPlanning, string>();
export const getOriginalFullAnnualResponse = (value: FullAnnualPlanning) => originals.get(value) ?? null;
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const strings = (value: unknown) => Array.isArray(value) && value.every((item) => typeof item === 'string');
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.every(uuid) && new Set(value).size === value.length;
const timestamp = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const cents = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
function sameJson(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((child, index) => sameJson(child, right[index]));
  return object(left) && object(right) && Object.keys(left).length === Object.keys(right).length && Object.keys(left).every((key) => Object.hasOwn(right, key) && sameJson(left[key], right[key]));
}
function requireValue(value: unknown): asserts value { if (!value) throw new Error('完整保护规划的原来源、精确金额或只读边界未通过校验'); }

export function parseFullAnnualPlanning(value: unknown, originalText?: string): FullAnnualPlanning {
  assertMoneyFields(value);
  requireValue(object(value) && value.schema_version === 'full-annual-protection-v1' && value.simulation === true && value.planning_only === true && value.grants_authority === false && value.execution_support === 'NOT_IMPLEMENTED' && value.horizon_days === 365 && uuid(value.user_id) && timestamp(value.as_of) && digest(value.input_digest) && object(value.projection) && object(value.initial_checkpoint) && Array.isArray(value.daily_checkpoints) && value.daily_checkpoints.length === 365);
  const result = value as unknown as FullAnnualPlanning; const projection = result.projection;
  const first = annualDayNumber(result.initial_checkpoint.date);
  requireValue([0, 8 * 3600000].some((offset) => new Date(Date.parse(result.as_of) + offset).toISOString().slice(0, 10) === result.initial_checkpoint.date));
  requireValue(['registered-full-protection-v1', 'registered-full-protection-future-dated-history-v2', 'registered-full-protection-adopted-seasonal-v3', 'registered-full-protection-ended-seasonal-v4'].includes(projection.algorithm_version) && projection.planning_only === true && projection.bank_authority === false && projection.execution_support === 'NOT_IMPLEMENTED' && projection.basis === 'REGISTERED_UPPER_BOUND_UNPAID_CONDITIONAL_CASH' && ['READY', 'LIQUIDITY_RISK', 'UNKNOWN'].includes(projection.status) && typeof projection.full_obligations_complete_within_registered_current_scope === 'boolean' && projection.historical_full_settlement_complete === false && projection.future_income_in_current_cash_cents === 0 && projection.future_income_in_original_execution_cents === 0 && projection.future_income_status === 'NO_REGISTERED_PLANNING_INCOME_SOURCE' && digest(projection.input_hash) && strings(projection.reasons));
  const endedAlgorithm = projection.algorithm_version === 'registered-full-protection-ended-seasonal-v4';
  const adoptedAlgorithm = projection.algorithm_version === 'registered-full-protection-adopted-seasonal-v3' || endedAlgorithm;
  requireValue(adoptedAlgorithm
    ? projection.status === 'UNKNOWN'
      ? projection.seasonal_status === 'ADOPTED_AMOUNT_UNKNOWN' && projection.seasonal_adopted_adjustment_cents === null
      : (projection.seasonal_status === 'ADOPTED_PROTECTED' || endedAlgorithm && projection.seasonal_status === 'ADOPTED_FLOOR_RELEASED' && projection.seasonal_adopted_adjustment_cents === 0) && cents(projection.seasonal_adopted_adjustment_cents)
    : projection.seasonal_adopted_adjustment_cents === null && ['NO_SEASONAL_POLICY', 'ADVICE_ONLY_NO_ADOPTED_AMOUNT'].includes(projection.seasonal_status));
  validateAnnualBoundary(projection.original_execution_view, first, 90); validateAnnualBoundary(projection.original_annual_projection, first, 365);
  requireValue((projection.status === 'UNKNOWN') === (projection.full_annual_projection === null));
  const trace = projection.full_annual_projection ? validateAnnualBoundary(projection.full_annual_projection, first, 365) : new Map();
  if (projection.full_annual_projection) requireValue(projection.full_annual_projection.status === projection.status);
  for (const [day, point] of [result.initial_checkpoint, ...result.daily_checkpoints].entries()) {
    requireValue(object(point) && point.day === day && annualDayNumber(point.date) === first + day);
    const phases = ['BEFORE_PAYMENT', 'AFTER_PAYMENT', 'AFTER_PRINCIPAL'];
    const fields = ['before_payment', 'after_payment', 'after_principal'] as const;
    if (projection.status === 'UNKNOWN') requireValue(point.status === 'NOT_PROVEN' && point.minimum_intraday_margin_cents === null && fields.every((field) => point[field] === null));
    else {
      requireValue(point.status === 'PROVEN');
      for (const [index, field] of fields.entries()) { const phase = point[field]; const original = trace.get(`${day}:${phases[index]}`); requireValue(phase && original && sameJson(phase, original)); }
      requireValue(point.minimum_intraday_margin_cents === Math.min(...fields.map((field) => point[field]!.margin_cents)));
    }
  }
  requireValue(projection.full_obligations_complete_within_registered_current_scope === (projection.status !== 'UNKNOWN'));
  requireValue(Array.isArray(result.full_policy_sources) && result.full_policy_sources.length <= 200);
  for (const source of result.full_policy_sources) {
    requireValue(object(source) && uuid(source.policy_id) && uuid(source.version_id) && digest(source.content_hash) && Number.isSafeInteger(source.version_number) && source.version_number >= 1 && ['DatedExpensePolicy', 'PeriodicTransferPolicy', 'SeasonalReservePolicy'].includes(source.template_name) && object(source.configuration) && object(source.confirmation) && source.confirmation.accepted === true && source.confirmation.reviewed_hash === source.content_hash && ids(source.evidence_ids) && typeof source.planning_confirmation_valid === 'boolean' && typeof source.references_current === 'boolean' && timestamp(source.confirmed_at) && Date.parse(source.confirmed_at) <= Date.parse(result.as_of) && timestamp(source.valid_from) && (source.valid_until === null || timestamp(source.valid_until) && Date.parse(source.valid_until) > Date.parse(source.valid_from)) && typeof source.effective_status === 'string');
    requireValue(Array.isArray(source.reference_snapshots) && source.reference_snapshots.every(object) && Array.isArray(source.protected_references));
    for (const reference of source.protected_references!) requireValue(object(reference) && uuid(reference.policy_id) && uuid(reference.version_id) && digest(reference.content_hash) && ['MVP_POLICY', 'FULL_POLICY'].includes(reference.kind) && typeof reference.current_confirmed === 'boolean' && ids(reference.evidence_ids));
    if (!endedAlgorithm) requireValue(!source.reference_snapshots.some(wrapper => wrapper.kind === 'VERIFIED_ENDED_SEASONAL_ADOPTION'));
  }
  const sources = new Map(result.full_policy_sources.map((source) => [source.policy_id, source]));
  requireValue(sources.size === result.full_policy_sources.length && new Set(result.full_policy_sources.map((source) => source.version_id)).size === sources.size && Array.isArray(projection.policy_states) && projection.policy_states.every(object) && projection.policy_states.length === sources.size);
  const states = new Map(projection.policy_states.map((state) => [state.policy_id, state]));
  requireValue(states.size === sources.size);
  for (const state of projection.policy_states) requireValue(object(state) && sources.get(state.policy_id)?.version_id === state.version_id && sources.get(state.policy_id)?.template_name === state.template_name && ['INCLUDED', 'OUTSIDE_HORIZON', 'INACTIVE', 'ADVICE_ONLY', 'UNKNOWN'].includes(state.state) && strings(state.reasons));
  requireValue(Array.isArray(projection.occurrences) && projection.occurrences.every(object) && projection.occurrences.length <= 2600 && new Set(projection.occurrences.map((entry) => entry.occurrence_id)).size === projection.occurrences.length);
  for (const entry of projection.occurrences) {
    const source = sources.get(entry.policy_id);
    requireValue(object(entry) && source && source.version_id === entry.policy_version_id && ['DATED_EXPENSE', 'PERIODIC_TRANSFER'].includes(entry.kind) && typeof entry.occurrence_id === 'string' && cents(entry.conservative_unpaid_cents) && entry.original_paid_cents === null && entry.settlement_status === 'UNSUPPORTED_CONSERVATIVE_UNPAID' && entry.bank_authority === false && entry.protection_starts_today === true && typeof entry.overdue === 'boolean' && ids(entry.evidence_ids) && sameJson(entry.evidence_ids, source.evidence_ids));
    const due = annualDayNumber(entry.earliest_due_date); const last = annualDayNumber(entry.latest_due_date); const payment = annualDayNumber(entry.hypothetical_payment_date); const prepare = annualDayNumber(entry.prepare_start_date);
    requireValue(due <= last && payment === Math.max(first, due) && payment <= first + 365 && prepare <= due && entry.overdue === (due < first));
    if (entry.kind === 'DATED_EXPENSE') requireValue(source.template_name === 'DatedExpensePolicy' && entry.amount_basis === 'REGISTERED_MAX' && entry.source_account_id === null && entry.payee_id === null && entry.occurrence_id === `FULL:${entry.policy_id}:${entry.policy_version_id}:DATED:${entry.earliest_due_date}:${entry.latest_due_date}`);
    else requireValue(source.template_name === 'PeriodicTransferPolicy' && ['REGISTERED_MAX', 'REGISTERED_EXACT'].includes(entry.amount_basis) && uuid(entry.source_account_id) && typeof entry.payee_id === 'string' && entry.payee_id.trim().length > 0 && last === due && entry.occurrence_id === `FULL:${entry.policy_id}:${entry.policy_version_id}:MONTH:${entry.earliest_due_date.slice(0, 7)}`);
  }
  requireValue(Array.isArray(projection.source_account_checks) && projection.source_account_checks.every(object));
  const accounts = new Map(projection.source_account_checks.map((check) => [check.account_id, check])); const usedAccounts = new Set(projection.occurrences.flatMap((entry) => entry.source_account_id === null ? [] : [entry.source_account_id]));
  requireValue(accounts.size === projection.source_account_checks.length && accounts.size === usedAccounts.size && [...usedAccounts].every((id) => accounts.has(id)));
  for (const check of projection.source_account_checks) {
    requireValue(object(check) && uuid(check.account_id) && check.future_account_debits_complete === false && [check.actual_cash_cents, check.goal_owned_cash_cents, check.reserved_cash_cents, check.registered_periodic_required_cents].every(cents) && Number.isSafeInteger(check.remaining_current_cash_cents));
    const required = projection.occurrences.filter((entry) => entry.source_account_id === check.account_id).reduce((sum, entry) => sum + BigInt(entry.conservative_unpaid_cents), 0n);
    requireValue(BigInt(check.registered_periodic_required_cents) === required && BigInt(check.remaining_current_cash_cents) === BigInt(check.actual_cash_cents) - BigInt(check.goal_owned_cash_cents) - BigInt(check.reserved_cash_cents) - required && check.state === (check.remaining_current_cash_cents >= 0 ? 'CURRENT_SOURCE_SUFFICIENT' : 'SOURCE_LIQUIDITY_RISK'));
  }
  if (projection.full_annual_projection) {
    const full = projection.full_annual_projection; const minimum = Math.min(...full.calculation_trace.map((point) => point.margin_cents)); const sourceRisk = projection.source_account_checks.some((check) => check.state === 'SOURCE_LIQUIDITY_RISK');
    requireValue(full.algorithm_version === projection.algorithm_version && full.minimum_margin_cents === minimum && full.safe_idle_cents === (sourceRisk ? 0 : Math.max(0, minimum)) && full.deficit_cents === Math.max(0, -minimum) && projection.status === (minimum < 0 || sourceRisk ? 'LIQUIDITY_RISK' : 'READY'));
    for (const point of full.calculation_trace) requireValue(Object.values(point.protected_cents_by_reason).every(cents) && BigInt(point.margin_cents) === BigInt(point.cash_cents) - Object.values(point.protected_cents_by_reason).reduce((sum, amount) => sum + BigInt(amount), 0n));
    if (adoptedAlgorithm) {
      const adopted = result.full_policy_sources.flatMap(source => (source.reference_snapshots ?? []).filter(row => row.kind === 'VERIFIED_SEASONAL_ADOPTION').map(wrapper => {
        requireValue(Object.keys(wrapper).length === 2 && object(wrapper.proof)); const proof = wrapper.proof;
        requireValue(proof.status === 'VERIFIED' && proof.user_id === result.user_id && proof.policy_id === source.policy_id && timestamp(proof.as_of) && Date.parse(proof.as_of) === Date.parse(result.as_of) && uuid(proof.evidence_id) && digest(proof.evidence_hash) && digest(proof.trace_hash) && ids(proof.retained_command_ids) && Number.isSafeInteger(proof.actual_adoption_count) && proof.actual_adoption_count === proof.retained_command_ids.length && object(proof.original) && object(proof.original.scope));
        const scope = proof.original.scope;
        requireValue(source.template_name === 'SeasonalReservePolicy' && scope.user_id === result.user_id && scope.policy_id === source.policy_id && scope.version_id === source.version_id && scope.configuration_hash === source.content_hash && scope.epoch_id === proof.epoch_id && cents(scope.adopted_adjustment_cents) && typeof scope.protection_end === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(scope.protection_end) && result.source_evidence_ids.includes(proof.evidence_id));
        return { amount: scope.adopted_adjustment_cents, end: annualDayNumber(scope.protection_end) };
      }));
      const ended = result.full_policy_sources.flatMap(source => (source.reference_snapshots ?? []).filter(wrapper => wrapper.kind === 'VERIFIED_ENDED_SEASONAL_ADOPTION').map(wrapper => {
        requireValue(endedAlgorithm && Object.keys(wrapper).length === 2 && object(wrapper.proof)); const proof = wrapper.proof;
        requireValue(proof.algorithm_version === 'seasonal-ended-adoption-proof-v1' && proof.status === 'VERIFIED_ENDED' && proof.user_id === result.user_id && proof.policy_id === source.policy_id && uuid(proof.epoch_id) && proof.epoch_id === result.audit.epoch_id && timestamp(proof.as_of) && Date.parse(proof.as_of) === Date.parse(result.as_of) && proof.current_floor_cents === 0 && proof.bank_authority === false && proof.financial_execution_performed === false && proof.cash_balance_changed === false && proof.current_permission_proven === false && proof.future_income_in_current_cash_cents === 0 && digest(proof.input_hash) && digest(proof.proof_hash) && strings(proof.reasons) && proof.reasons.length === 0 && ids(proof.retained_command_ids) && ids(proof.registered_adoption_evidence_ids) && proof.registered_adoption_evidence_count === proof.registered_adoption_evidence_ids.length && object(proof.original) && object(proof.original.scope) && object(proof.inputs));
        const scope = proof.original.scope; const inputs = proof.inputs;
        requireValue(source.template_name === 'SeasonalReservePolicy' && scope.user_id === result.user_id && scope.epoch_id === proof.epoch_id && scope.policy_id === source.policy_id && scope.version_id === source.version_id && scope.configuration_hash === source.content_hash && cents(scope.adopted_adjustment_cents) && proof.original_adopted_cents === scope.adopted_adjustment_cents && typeof scope.protection_end === 'string' && proof.protection_end === scope.protection_end && typeof proof.floor_released_on === 'string' && annualDayNumber(proof.floor_released_on) === annualDayNumber(scope.protection_end) + 1 && first >= annualDayNumber(proof.floor_released_on));
        requireValue(inputs.protocol === 'seasonal-ended-adoption-input-v1' && inputs.user_id === proof.user_id && inputs.epoch_id === proof.epoch_id && inputs.policy_id === proof.policy_id && timestamp(inputs.as_of) && Date.parse(inputs.as_of) === Date.parse(result.as_of) && inputs.timezone === 'Asia/Shanghai' && inputs.registered_adoption_evidence_count === proof.registered_adoption_evidence_count && ids(inputs.registered_adoption_evidence_ids) && sameJson([...inputs.registered_adoption_evidence_ids].sort(), [...proof.registered_adoption_evidence_ids].sort()) && Array.isArray(inputs.records) && inputs.records.length === proof.registered_adoption_evidence_count && object(inputs.current_policy_original) && object(inputs.current_policy_original.current_version));
        const version = inputs.current_policy_original.current_version;
        requireValue(inputs.current_policy_original.policy_id === source.policy_id && inputs.current_policy_original.epoch_id === proof.epoch_id && version.version_id === source.version_id && version.version_number === source.version_number && version.content_hash === source.content_hash && sameJson(version.configuration, source.configuration) && sameJson(version.confirmation, source.confirmation));
        const recordIds: string[] = [];
        for (const record of inputs.records) {
          requireValue(object(record) && object(record.adoption_evidence_original) && uuid(record.adoption_evidence_original.id) && record.adoption_evidence_original.user_id === result.user_id && digest(record.adoption_evidence_original.content_hash) && Array.isArray(record.current_evidence_originals) && record.current_evidence_originals.length > 0);
          recordIds.push(record.adoption_evidence_original.id);
          for (const original of record.current_evidence_originals) requireValue(object(original) && uuid(original.id) && original.user_id === result.user_id && digest(original.content_hash) && result.source_evidence_ids.includes(original.id));
        }
        requireValue(ids(recordIds) && sameJson(recordIds.sort(), [...proof.registered_adoption_evidence_ids].sort()));
        return source.policy_id;
      }));
      requireValue(endedAlgorithm ? ended.length > 0 : adopted.length > 0);
      requireValue(projection.seasonal_status === (ended.length > 0 && adopted.length === 0 ? 'ADOPTED_FLOOR_RELEASED' : 'ADOPTED_PROTECTED') && projection.seasonal_adopted_adjustment_cents === adopted.reduce((sum, item) => sum + (first <= item.end ? item.amount : 0), 0));
      for (const point of full.calculation_trace) requireValue(point.protected_cents_by_reason.full_seasonal_adopted === adopted.reduce((sum, item) => sum + (annualDayNumber(point.date) <= item.end ? item.amount : 0), 0));
    }
  }
  requireValue(object(result.future_income) && result.future_income.status === 'NOT_IMPLEMENTED_NO_REGISTERED_SOURCE' && result.future_income.included_in_execution_cents === 0 && result.future_income.included_in_planning_cents === 0 && typeof result.future_income.reason === 'string' && ids(result.source_evidence_ids) && strings(result.limitations) && Array.isArray(result.source_issues) && result.source_issues.every((issue) => object(issue) && (['code', 'message', 'source_ref'] as const).every((field) => typeof issue[field] === 'string')) && object(result.audit) && result.audit.scope === 'CURRENT_LIVE_EPOCH' && (result.audit.epoch_id === null || uuid(result.audit.epoch_id)) && typeof result.audit.status === 'string' && typeof result.audit.complete === 'boolean' && object(result.audit.anchored_run_statuses) && Object.entries(result.audit.anchored_run_statuses).every(([identity, state]) => uuid(identity) && typeof state === 'string'));
  if (originalText !== undefined) originals.set(result, originalText); return result;
}
export const getFullAnnualPlanning = () => request<FullAnnualPlanning>('/planning/full-annual', 'GET', undefined, parseFullAnnualPlanning);
