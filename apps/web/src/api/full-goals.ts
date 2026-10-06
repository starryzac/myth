import type { components } from '../../../../packages/contracts/schema';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';
import { request } from './http';
import type { FullGoalIntent } from '../features/full-goal-operation';

export type FullGoalModel = components['schemas']['FullGoalModelResponse'];
export type FullGoalPreview = components['schemas']['FullGoalPreviewResponse'];
export type FullGoalReceipt = components['schemas']['FullGoalConfirmationResponse'];
export type FullGoalLookup = components['schemas']['FullGoalCommandLookup'];
export type GoalModelBinding = { id: string; policy_id: string; policy_version_id: string };
export interface FullGoalConfiguration {
  type: 'long_term_goal'; name: string | null; valid_from: string | null; valid_until: string | null;
  target_cents: number; deadline: string; monthly_contribution: { min_cents: number; target_cents: number; max_cents: number };
  importance: number; minimum_guarantee_cents: number; allow_partial: boolean; allow_deferral: boolean;
  deferral_cost_cents_per_day: number; asset_policy_id: string | null; cross_goal_reallocation_allowed: false; cross_goal_reallocation_policy_id: null;
}
const originalResponses = new WeakMap<FullGoalModel | FullGoalPreview | FullGoalReceipt | FullGoalLookup, string>();
export const getOriginalFullGoalResponse = (value: FullGoalModel | FullGoalPreview | FullGoalReceipt | FullGoalLookup): string | null => originalResponses.get(value) ?? null;
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const text = (value: unknown): value is string => typeof value === 'string';
const digest = (value: unknown) => text(value) && /^[0-9a-f]{64}$/.test(value);
const date = (value: unknown): value is string => text(value) && /^\d{4}-\d\d-\d\d$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const timestamp = (value: unknown): value is string => text(value) && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const cents = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;
const nullable = (value: unknown, check: (value: unknown) => boolean) => value === null || check(value);
function requireValue(condition: unknown): asserts condition { if (!condition) throw new Error('完整目标响应未通过原版本、只读边界或精确金额校验'); }
function sameJson(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((value, index) => sameJson(value, right[index]));
  if (object(left) && object(right)) return Object.keys(left).length === Object.keys(right).length && Object.keys(left).every((key) => Object.hasOwn(right, key) && sameJson(left[key], right[key]));
  return false;
}

export function parseFullGoalConfiguration(value: unknown): FullGoalConfiguration {
  requireValue(object(value) && value.type === 'long_term_goal'); assertMoneyFields(value);
  requireValue(nullable(value.name, (name) => text(name) && name.trim().length > 0 && name.length <= 120) && nullable(value.valid_from, date) && nullable(value.valid_until, date) && date(value.deadline));
  requireValue(cents(value.target_cents) && (value.target_cents as number) > 0 && cents(value.minimum_guarantee_cents) && (value.minimum_guarantee_cents as number) <= (value.target_cents as number));
  const monthly = value.monthly_contribution;
  requireValue(object(monthly) && ['min_cents', 'target_cents', 'max_cents'].every((key) => cents(monthly[key])) && (monthly.min_cents as number) <= (monthly.target_cents as number) && (monthly.target_cents as number) <= (monthly.max_cents as number));
  requireValue(Number.isSafeInteger(value.importance) && (value.importance as number) >= 0 && (value.importance as number) <= 100);
  requireValue(typeof value.allow_partial === 'boolean' && typeof value.allow_deferral === 'boolean' && cents(value.deferral_cost_cents_per_day) && (value.allow_deferral || value.deferral_cost_cents_per_day === 0));
  requireValue(nullable(value.asset_policy_id, uuid) && value.cross_goal_reallocation_allowed === false && value.cross_goal_reallocation_policy_id === null);
  requireValue(value.valid_from === null || value.deadline >= (value.valid_from as string)); requireValue(value.valid_until === null || value.deadline <= (value.valid_until as string));
  const allowed = ['type', 'name', 'valid_from', 'valid_until', 'target_cents', 'deadline', 'monthly_contribution', 'importance', 'minimum_guarantee_cents', 'allow_partial', 'allow_deferral', 'deferral_cost_cents_per_day', 'asset_policy_id', 'cross_goal_reallocation_allowed', 'cross_goal_reallocation_policy_id'];
  requireValue(Object.keys(value).every((key) => allowed.includes(key)) && Object.keys(monthly).every((key) => ['min_cents', 'target_cents', 'max_cents'].includes(key)));
  return value as unknown as FullGoalConfiguration;
}
export function parseFullGoalModel(value: unknown, binding: GoalModelBinding, originalText?: string): FullGoalModel {
  requireValue(uuid(binding.id) && uuid(binding.policy_id) && uuid(binding.policy_version_id));
  requireValue(object(value) && value.simulation === true && value.bank_authority === false && value.dedicated_audit_event === false);
  requireValue(value.goal_id === binding.id && value.policy_id === binding.policy_id && value.base_policy_version_id === binding.policy_version_id && uuid(value.epoch_id));
  if (value.status === 'MODEL_MISSING') {
    for (const key of ['policy_effective_status', 'evidence_id', 'evidence_hash', 'full_configuration', 'full_configuration_hash', 'base_configuration_hash', 'confirmed_at']) requireValue(value[key] === null);
  } else {
    requireValue(value.status === 'VERIFIED' && ['ACTIVE', 'CONFIRMED'].includes(value.policy_effective_status as string) && uuid(value.evidence_id) && digest(value.evidence_hash) && digest(value.full_configuration_hash) && digest(value.base_configuration_hash) && timestamp(value.confirmed_at));
    parseFullGoalConfiguration(value.full_configuration);
  }
  assertMoneyFields(value); const result = value as FullGoalModel;
  if (originalText !== undefined) originalResponses.set(result, originalText); return result;
}
export function parseFullGoalPreview(value: unknown, binding: GoalModelBinding, originalText?: string): FullGoalPreview {
  requireValue(object(value) && value.simulation === true && value.preview_only === true && value.grants_authority === false && value.extra_fields_in_base_impact === false);
  requireValue(value.goal_id === binding.id && uuid(value.epoch_id) && value.expected_version_id === binding.policy_version_id && digest(value.full_configuration_hash) && digest(value.base_configuration_hash) && Array.isArray(value.notes) && value.notes.every(text));
  const full = parseFullGoalConfiguration(value.full_configuration); const base = value.base_configuration;
  requireValue(object(base) && base.type === 'goal_saving' && base.name === full.name && base.target_cents === full.target_cents && base.deadline === full.deadline && base.asset_policy_id === full.asset_policy_id && base.cross_goal_reallocation_allowed === false && base.valid_from === full.valid_from && base.valid_until === full.valid_until);
  requireValue(object(base.monthly_contribution) && ['min_cents', 'target_cents', 'max_cents'].every((key) => (base.monthly_contribution as Record<string, unknown>)[key] === full.monthly_contribution[key as keyof typeof full.monthly_contribution]));
  requireValue(object(base.priority) && base.priority.importance === full.importance && base.priority.minimum_cents === full.minimum_guarantee_cents && base.priority.reducible === full.allow_partial && base.priority.deferrable === full.allow_deferral);
  const impact = value.base_policy_impact;
  requireValue(object(impact) && impact.schema_version === 'policy-change-preview-v1' && impact.simulation === true && impact.preview_only === true && impact.financial_only === true && impact.policy_id === binding.policy_id && impact.expected_version_id === binding.policy_version_id && impact.configuration_hash === value.base_configuration_hash && uuid(impact.user_id) && timestamp(impact.as_of) && ['UTC', 'Asia/Shanghai'].includes(impact.timezone as string));
  requireValue(object(impact.configuration) && sameJson(impact.configuration, base));
  for (const card of [impact.before, impact.after]) requireValue(object(card) && ['PROVEN', 'NOT_PROVEN', 'INCOMPLETE'].includes(card.state as string) && ['READY', 'LIQUIDITY_RISK', 'INSUFFICIENT_EVIDENCE'].includes(card.status as string) && card.financial_only === true && ['safe_idle_cents', 'minimum_margin_cents', 'deficit_cents'].every((key) => nullable(card[key], Number.isSafeInteger)));
  requireValue(nullable(impact.delta_safe_idle_cents, Number.isSafeInteger) && nullable(impact.delta_minimum_margin_cents, Number.isSafeInteger) && Array.isArray(impact.notes) && impact.notes.every(text));
  assertMoneyFields(value); const result = value as FullGoalPreview;
  if (originalText !== undefined) originalResponses.set(result, originalText); return result;
}
export function getFullGoalModel(binding: GoalModelBinding): Promise<FullGoalModel> {
  requireValue(uuid(binding.id) && uuid(binding.policy_id) && uuid(binding.policy_version_id));
  return request<FullGoalModel>(`/goals/${binding.id}/full-model`, 'GET', undefined, (value, original) => parseFullGoalModel(value, binding, original));
}
/** Only the original RR/read-only preview endpoint; never invokes confirmation or grants authority. */
export function previewFullGoalModel(binding: GoalModelBinding, configuration: Record<string, unknown>): Promise<FullGoalPreview> {
  requireValue(uuid(binding.id) && uuid(binding.policy_id) && uuid(binding.policy_version_id) && object(configuration)); assertMoneyFields(configuration);
  function exact(value: unknown, depth = 0): boolean {
    if (depth > 64) return false; if (typeof value === 'number') return Number.isSafeInteger(value);
    if (Array.isArray(value)) return value.every((child) => exact(child, depth + 1));
    if (object(value)) return Object.values(value).every((child) => exact(child, depth + 1));
    return value === null || typeof value === 'string' || typeof value === 'boolean';
  }
  requireValue(exact(configuration));
  return request<FullGoalPreview>(`/goals/${binding.id}/full-model/preview`, 'POST', { expected_version_id: binding.policy_version_id, configuration }, (value, original) => parseFullGoalPreview(value, binding, original));
}
function confirmationContext(intent: FullGoalIntent): void { requireValue(intent.protocol === 'full-goal-browser-command-v1' && uuid(intent.user_id) && uuid(intent.goal_id) && uuid(intent.policy_id) && intent.path === `/goals/${intent.goal_id}/full-model/confirm` && uuid(intent.body.expected_version_id) && uuid(intent.body.expected_epoch_id) && intent.body.accepted === true && digest(intent.body.reviewed_full_hash) && digest(intent.body.reviewed_base_hash) && digest(intent.request_hash) && intent.body.idempotency_key.trim().length > 0 && intent.body.idempotency_key.length <= 150 && intent.body.reason.trim().length > 0 && intent.body.reason.length <= 1000 && intent.body_json === JSON.stringify(intent.body)); parseFullGoalConfiguration(intent.body.configuration); }
export function parseFullGoalReceipt(value: unknown, intent: FullGoalIntent, raw?: string): FullGoalReceipt {
  confirmationContext(intent); requireValue(object(value) && value.simulation === true && value.goal_id === intent.goal_id && value.epoch_id === intent.body.expected_epoch_id && value.receipt_is_current_authority === false && value.bank_authority === false && value.dedicated_audit_event === false && typeof value.idempotent_replay === 'boolean' && uuid(value.evidence_id) && digest(value.evidence_hash) && timestamp(value.confirmed_at) && value.full_configuration_hash === intent.body.reviewed_full_hash && value.base_configuration_hash === intent.body.reviewed_base_hash && sameJson(value.full_configuration, intent.body.configuration));
  const lifecycle = value.lifecycle;
  requireValue(object(lifecycle) && lifecycle.simulation === true && lifecycle.policy_id === intent.policy_id && lifecycle.previous_version_id === intent.body.expected_version_id && uuid(lifecycle.current_version_id) && lifecycle.current_version_id !== lifecycle.previous_version_id && ['ACTIVE', 'CONFIRMED', 'SUSPENDED'].includes(lifecycle.status as string) && ['ACTIVE', 'CONFIRMED', 'SUSPENDED'].includes(lifecycle.effective_status as string) && lifecycle.requires_recompute === true);
  for (const ids of [lifecycle.invalidated_action_ids, lifecycle.inflight_action_ids]) requireValue(Array.isArray(ids) && ids.every(uuid) && new Set(ids).size === ids.length);
  assertMoneyFields(value); const result = value as FullGoalReceipt; if (raw !== undefined) originalResponses.set(result, raw); return result;
}
export function parseFullGoalCommandLookup(value: unknown, intent: FullGoalIntent, raw?: string): FullGoalLookup {
  confirmationContext(intent); requireValue(object(value) && value.simulation === true && value.goal_id === intent.goal_id && value.idempotency_key === intent.body.idempotency_key && value.not_found_is_final === false && value.receipt_is_current_authority === false && value.bank_authority === false && value.dedicated_audit_event === false && ['NOT_FOUND', 'RECORDED'].includes(value.status as string));
  if (value.status === 'NOT_FOUND') requireValue(value.record === null);
  else { const record = value.record; requireValue(object(record) && record.original_verified === true && record.audit_chain_verified === true && record.configuration_is_server_canonical === true && record.epoch_archive_verified === false && record.request_hash === intent.request_hash && sameJson(record.original_request, intent.body)); const receipt = parseFullGoalReceipt(record.receipt, intent); requireValue(receipt.idempotent_replay === true); }
  const result = value as FullGoalLookup; if (raw !== undefined) originalResponses.set(result, raw); return result;
}
export function confirmFullGoalModel(intent: FullGoalIntent): Promise<FullGoalReceipt> { confirmationContext(intent); return request<FullGoalReceipt>(intent.path, 'POST', intent.body, (value, raw) => parseFullGoalReceipt(value, intent, raw)); }
export function lookupFullGoalCommand(intent: FullGoalIntent): Promise<FullGoalLookup> { confirmationContext(intent); return request<FullGoalLookup>(`/goals/${intent.goal_id}/full-model/commands/by-key/${encodeURIComponent(intent.body.idempotency_key)}`, 'GET', undefined, (value, raw) => parseFullGoalCommandLookup(value, intent, raw)); }
