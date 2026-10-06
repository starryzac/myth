import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { spendingUUID as uuid, spendingDigest as digest } from './spending-evidence';
import { parseFullRecoveryPlan } from './full-products';
import { parseRecoveryIntent, parseRecoveryLookup, parseRecoveryPrepare, parseRecoveryPreview, recoveryCanonicalJson as canonical, recoveryCheck as check, recoveryRequestHash as hash } from './full-recovery-execution';
import type { RecoveryIntent } from './full-recovery-execution';

export type RecoveryNextBody = components['schemas']['FullRecoveryNextRequest'];
export type RecoveryNextLookup = Required<components['schemas']['FullRecoveryNextLookup']>;
export type RecoveryNextPreview = Required<components['schemas']['FullRecoveryNextPreview']>;
export type RecoveryNextDraft = { protocol: 'full-recovery-next-preview-browser-v2'; user_id: string; body: RecoveryNextBody; body_json: string; request_hash: string };
const protocol = 'full-recovery-next-whole-v2';
const exact = (value: Record<string, unknown>, fields: string[]) => Object.keys(value).sort().join('|') === [...fields].sort().join('|');
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((row) => typeof row === 'string');
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const originals = new WeakMap<object, string>(), originalReads = new WeakSet<object>();
export const getOriginalRecoveryNextResponse = (value: object) => originals.get(value) ?? null;
export const isOriginalRecoveryNextRead = (value: object) => originalReads.has(value);
const save = <T extends object>(value: T, raw?: string): T => { if (raw !== undefined) originals.set(value, raw); return value; };

export function parseRecoveryNextBody(value: unknown): RecoveryNextBody {
  check(object(value) && exact(value, ['policy_id', 'expected_version_id', 'expected_epoch_id', 'idempotency_key']) && ['policy_id', 'expected_version_id', 'expected_epoch_id'].every((key) => uuid(value[key])) && typeof value.idempotency_key === 'string' && value.idempotency_key.trim().length > 0 && value.idempotency_key.length <= 120);
  return value as RecoveryNextBody;
}
export const recoveryNextV1Key = async (root: string): Promise<string> => {
  check(typeof root === 'string' && root.trim().length > 0 && root.length <= 120);
  return `next-whole-v2:${await hash({ protocol, root_key: root })}`;
};
export async function parseRecoveryNextDraft(value: unknown): Promise<RecoveryNextDraft> {
  check(object(value) && exact(value, ['protocol', 'user_id', 'body', 'body_json', 'request_hash']) && value.protocol === 'full-recovery-next-preview-browser-v2' && uuid(value.user_id) && typeof value.body_json === 'string' && digest(value.request_hash));
  const body = parseRecoveryNextBody(value.body); check(value.body_json === JSON.stringify(body) && value.request_hash === await hash(body));
  return value as RecoveryNextDraft;
}
export async function recoveryNextOriginalIntent(value: RecoveryNextLookup): Promise<RecoveryIntent> {
  check(value.status === 'RECORDED' && value.original_v1_lookup.original_request);
  const body = parseRecoveryPrepare(value.original_v1_lookup.original_request);
  const intent: RecoveryIntent = { protocol: 'full-recovery-browser-v1', kind: 'PREPARE', user_id: value.user_id, prepare_request: body, action: null, path: '/full-recovery-actions/prepare', body, body_json: JSON.stringify(body), request_hash: await hash(body) };
  return parseRecoveryIntent(intent);
}
export async function parseRecoveryNextLookup(value: unknown, body: RecoveryNextBody, user: string, raw?: string): Promise<RecoveryNextLookup> {
  parseRecoveryNextBody(body);
  check(uuid(user) && object(value) && exact(value, ['protocol', 'simulation', 'bank_authority', 'current_authority', 'not_found_is_final', 'automatically_advances', 'user_id', 'idempotency_key', 'status', 'request_binding_kind', 'original_v2_request_separately_recorded', 'bound_request', 'bound_request_hash', 'original_v1_lookup']) && value.protocol === protocol && value.simulation === true && value.bank_authority === false && value.current_authority === false && value.not_found_is_final === false && value.automatically_advances === false && value.user_id === user && value.idempotency_key === body.idempotency_key && value.request_binding_kind === 'PROJECTION_OF_VERIFIED_V1_ORIGINAL_AND_EXACT_ROOT_KEY' && value.original_v2_request_separately_recorded === false && ['RECORDED', 'NOT_FOUND_NOT_FINAL'].includes(String(value.status)));
  const v1 = value.original_v1_lookup, mapped = await recoveryNextV1Key(body.idempotency_key);
  check(object(v1) && v1.user_id === user && v1.idempotency_key === mapped && v1.status === value.status);
  if (value.status === 'RECORDED') {
    const bound = parseRecoveryNextBody(value.bound_request); check(canonical(bound) === canonical(body) && value.bound_request_hash === await hash(body));
    const v1body = parseRecoveryPrepare(v1.original_request);
    check(v1body.policy_id === body.policy_id && v1body.expected_version_id === body.expected_version_id && v1body.expected_epoch_id === body.expected_epoch_id && v1body.idempotency_key === mapped);
    await parseRecoveryLookup(v1, await recoveryNextOriginalIntent(value as RecoveryNextLookup));
  } else {
    check(value.bound_request === null && value.bound_request_hash === null && exact(v1, ['simulation', 'bank_authority', 'current_authority', 'not_found_is_final', 'user_id', 'idempotency_key', 'status', 'original_request', 'original_action_request', 'client_request_hash', 'server_request_hash', 'action', 'epoch_state', 'original_consent', 'consent_is_current_authority']) && v1.simulation === true && v1.bank_authority === false && v1.current_authority === false && v1.not_found_is_final === false && v1.consent_is_current_authority === false && v1.epoch_state === 'MISSING');
    for (const field of ['original_request', 'original_action_request', 'client_request_hash', 'server_request_hash', 'action', 'original_consent']) check(v1[field] === null);
  }
  assertMoneyFields(value); return save(value as RecoveryNextLookup, raw);
}
export async function parseRecoveryNextPreview(value: unknown, body: RecoveryNextBody, user: string, raw?: string): Promise<RecoveryNextPreview> {
  parseRecoveryNextBody(body);
  check(uuid(user) && object(value) && exact(value, ['protocol', 'simulation', 'read_only', 'bank_authority', 'grants_authority', 'atomic_combination', 'automatically_advances', 'user_id', 'as_of', 'request', 'status', 'planning', 'selection', 'original_v1_preview', 'existing', 'input_hash', 'limitations']) && value.protocol === protocol && value.simulation === true && value.read_only === true && value.bank_authority === false && value.grants_authority === false && value.atomic_combination === false && value.automatically_advances === false && value.user_id === user && canonical(parseRecoveryNextBody(value.request)) === canonical(body) && time(value.as_of) && /(?:Z|\+00:00)$/.test(value.as_of) && digest(value.input_hash) && strings(value.limitations) && ['READY_TO_PREPARE', 'BLOCKED', 'UNKNOWN', 'NO_ELIGIBLE_NEXT_POSITION', 'ORIGINAL_ACTION_RETAINED'].includes(String(value.status)));
  const existing = await parseRecoveryNextLookup(value.existing, body, user);
  if (existing.status === 'RECORDED') check(value.status === 'ORIGINAL_ACTION_RETAINED' && value.planning === null && value.selection === null && value.original_v1_preview === null);
  else {
    check(value.status !== 'ORIGINAL_ACTION_RETAINED');
    if (value.planning !== null) {
      const planning = parseFullRecoveryPlan(value.planning, body.policy_id);
      check(planning.user_id === user && planning.as_of === value.as_of && (!planning.plan || planning.plan.policy_version_id === body.expected_version_id));
    }
    if (value.selection !== null) {
      const selection = value.selection, planning = value.planning;
      check(object(selection) && exact(selection, ['protocol', 'bank_authority', 'atomic_combination', 'current_candidate_denominator', 'current_selected_denominator', 'eligibility', 'next_v1_request', 'reasons']) && selection.protocol === protocol && selection.bank_authority === false && selection.atomic_combination === false && object(planning) && planning.state === 'COMPUTED' && Array.isArray(planning.source_issues) && planning.source_issues.length === 0 && object(planning.plan) && strings(selection.reasons) && Array.isArray(selection.eligibility));
      const plan = planning.plan; check(Array.isArray(plan.candidates) && Array.isArray(plan.lossless_steps) && selection.current_candidate_denominator === plan.candidates.length && selection.current_selected_denominator === plan.lossless_steps.length && selection.eligibility.length === plan.candidates.length);
      const candidates = plan.candidates, steps = plan.lossless_steps, eligibility = selection.eligibility;
      check(new Set(eligibility.map((row) => object(row) ? row.position_id : null)).size === eligibility.length);
      eligibility.forEach((row, index) => { const candidate = candidates[index]; check(object(row) && exact(row, ['position_id', 'selected_by_original_plan', 'eligible_for_v1_preview', 'reasons']) && object(candidate) && row.position_id === candidate.position_id && typeof row.selected_by_original_plan === 'boolean' && typeof row.eligible_for_v1_preview === 'boolean' && strings(row.reasons) && row.selected_by_original_plan === steps.some((step: unknown) => object(step) && step.position_id === row.position_id) && (row.eligible_for_v1_preview ? row.selected_by_original_plan === true && row.reasons.length === 0 : row.reasons.length > 0)); });
      const first = steps.find((step: unknown) => object(step) && eligibility.some((row: unknown) => object(row) && row.position_id === step.position_id && row.eligible_for_v1_preview === true));
      if (selection.next_v1_request !== null) {
        const forwarded = parseRecoveryPrepare(selection.next_v1_request);
        check(object(first) && forwarded.position_id === first.position_id && forwarded.policy_id === body.policy_id && forwarded.expected_version_id === body.expected_version_id && forwarded.expected_epoch_id === body.expected_epoch_id && forwarded.idempotency_key === await recoveryNextV1Key(body.idempotency_key));
        check(value.original_v1_preview !== null);
        const v1 = await parseRecoveryPreview(value.original_v1_preview, forwarded, user);
        check(v1.proof.as_of === value.as_of && v1.proof.deadline_at === plan.deadline_at);
        const candidate = v1.proof.candidate;
        check(['position_id', 'goal_id', 'destination_account_id', 'product_id', 'product_version_number', 'terms_digest', 'principal_cents', 'original_policy_version_id'].every((field) => candidate[field as keyof typeof candidate] === first[field]));
        if (value.status === 'READY_TO_PREPARE') check(v1.proof.status === 'VERIFIED_SCOPE' && v1.execution_effect !== null && selection.reasons.length === 0 && (first.liquidity_rank === 0 || first.liquidity_rank === 1) && v1.execution_effect.settlement_delay_days === first.liquidity_rank);
        else check(value.status === (v1.proof.status === 'UNKNOWN' ? 'UNKNOWN' : 'BLOCKED'));
      } else check(first === undefined && value.original_v1_preview === null && ['UNKNOWN', 'NO_ELIGIBLE_NEXT_POSITION'].includes(String(value.status)) && selection.reasons.length > 0);
    } else check(value.original_v1_preview === null && value.status === 'UNKNOWN');
  }
  // The producer hashes its explicit Python now.isoformat(), while JSON uses Z.
  const asOf = value.as_of.endsWith('Z') ? value.as_of.slice(0, -1) + '+00:00' : value.as_of;
  check(value.input_hash === await hash({ protocol, user_id: user, as_of: asOf, request: body, existing: value.existing, planning: value.planning, selection: value.selection, original_v1_preview: value.original_v1_preview }));
  assertMoneyFields(value); return save(value as RecoveryNextPreview, raw);
}
export async function previewRecoveryNext(body: RecoveryNextBody, user: string): Promise<RecoveryNextPreview> {
  parseRecoveryNextBody(body); const r = await request<{ simulation: true; value: unknown; raw: string }>('/full-recovery-next-actions/preview', 'POST', body, (value, raw) => ({ simulation: true, value, raw })); return parseRecoveryNextPreview(r.value, body, user, r.raw);
}
export async function lookupRecoveryNext(body: RecoveryNextBody, user: string): Promise<RecoveryNextLookup> {
  parseRecoveryNextBody(body); const r = await request<{ simulation: true; value: unknown; raw: string }>(`/full-recovery-next-actions/by-key/${encodeURIComponent(body.idempotency_key)}`, 'GET', undefined, (value, raw) => ({ simulation: true, value, raw })); const value = await parseRecoveryNextLookup(r.value, body, user, r.raw); originalReads.add(value); return value;
}
