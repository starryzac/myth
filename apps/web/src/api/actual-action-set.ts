import type { components } from '../../../../packages/contracts/schema';
import { ApiError, request } from './http';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';

export type ActualActionSet = components['schemas']['ActualActionSetSnapshot'];
export const actualActionSetTables = 'users accounts policies policy_versions goals asset_positions asset_products action_plans action_receipts bank_operations simulated_bank_postings simulated_bank_redemptions external_bank_facts transactions credit_card_bills action_resource_reservations evidence_items full_policies full_policy_versions full_policy_commands product_catalog_versions full_asset_execution_portfolios full_asset_execution_batches full_asset_execution_consents command_outbox command_inbox command_delivery_attempts'.split(' ');
const originals = new WeakMap<ActualActionSet, string>();
export const getOriginalActualActionSet = (data: ActualActionSet) => originals.get(data) ?? null;
const ids = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const hash = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === 'string');
const keys = (value: unknown): value is string[] => strings(value) && new Set(value).size === value.length;
const count = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;
const fail = () => new ApiError('动作集合响应缺少一致的原件分母，请刷新后核对', 200, 'INVALID_RESPONSE', null);

export function parseActualActionSet(value: unknown, originalText: string): ActualActionSet {
  assertMoneyFields(value);
  if (!object(value) || value.algorithm_version !== 'full-policy-action-set-boundary-actual-v2'
    || value.scope !== 'POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_V2' || value.simulation !== true
    || value.bank_authority !== false || value.grants_authority !== false || value.financial_write !== false
    || value.arbitrary_manual_intents_covered !== false || !ids(value.user_id) || !ids(value.epoch_id)
    || typeof value.as_of !== 'string' || !/(?:Z|[+-]\d\d:\d\d)$/.test(value.as_of) || !Number.isFinite(Date.parse(value.as_of))
    || !['COMPLETE', 'UNKNOWN'].includes(String(value.status)) || typeof value.global_action_set_complete !== 'boolean'
    || !['original_inventory_hash', 'financial_input_hash', 'input_hash', 'snapshot_hash'].every((field) => hash(value[field]))
    || !(value.action_set_signature === null || hash(value.action_set_signature))
    || !keys(value.expected_candidate_keys) || !keys(value.dynamic_candidate_keys) || !keys(value.asset_candidate_keys)
    || !strings(value.unsupported_producers) || !strings(value.reasons)
    || !Array.isArray(value.table_coverage) || !Array.isArray(value.candidates)) throw fail();
  const tableNames = new Set<string>();
  let completeTables = true;
  for (const row of value.table_coverage) {
    if (!object(row) || typeof row.table !== 'string' || !row.table || tableNames.has(row.table)
      || !(row.actual_count === null || count(row.actual_count)) || !count(row.captured_count)
      || typeof row.complete !== 'boolean' || !hash(row.rows_hash)
      || (row.actual_count !== null && (row.captured_count as number) > (row.actual_count as number))) throw fail();
    if (row.complete && (row.actual_count === null || row.captured_count !== row.actual_count)) throw fail();
    tableNames.add(row.table);
    completeTables &&= row.complete;
  }
  const candidateKeys = new Set<string>();
  for (const row of value.candidates) {
    if (!object(row) || typeof row.candidate_key !== 'string' || !row.candidate_key || candidateKeys.has(row.candidate_key)
      || !['INCLUDED', 'EXCLUDED', 'UNKNOWN'].includes(String(row.state)) || !strings(row.reasons)
      || !(row.action_type === null || typeof row.action_type === 'string')
      || !(row.autonomy_level === null || typeof row.autonomy_level === 'string')
      || !(row.amount_cents === null || count(row.amount_cents)) || !(row.signature === null || hash(row.signature))) throw fail();
    if (row.state === 'INCLUDED' && (!hash(row.signature) || row.amount_cents === null || row.action_type === null)) throw fail();
    candidateKeys.add(row.candidate_key);
  }
  if (candidateKeys.size !== value.expected_candidate_keys.length || value.expected_candidate_keys.some((key) => !candidateKeys.has(key))
    || [...value.dynamic_candidate_keys, ...value.asset_candidate_keys].some((key) => !candidateKeys.has(key))) throw fail();
  const complete = value.status === 'COMPLETE';
  if (complete !== value.global_action_set_complete || (complete && (!hash(value.action_set_signature)
    || !completeTables || tableNames.size !== actualActionSetTables.length || actualActionSetTables.some((name) => !tableNames.has(name)) || value.unsupported_producers.length > 0
    || value.candidates.some((row) => object(row) && row.state === 'UNKNOWN')))
    || (!complete && value.action_set_signature !== null)) throw fail();
  const result = value as ActualActionSet;
  originals.set(result, originalText);
  return result;
}
export const getActualActionSet = () => request<ActualActionSet>('/boundary/actual-action-set/current', 'GET', undefined, parseActualActionSet);
