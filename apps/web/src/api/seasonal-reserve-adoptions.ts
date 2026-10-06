import type { components } from '../../../../packages/contracts/schema';
import { request } from './http';
import { object } from '../features/policy-form';
import { assertMoneyFields } from '../features/money';
import { releaseCanonicalJson, releaseDigest, releaseUUID, releaseUUID5 } from './goal-release-authorizations';

export type SeasonalScope = components['schemas']['SeasonalAdoptionScope'];
export type SeasonalPreview = components['schemas']['SeasonalAdoptionPreview'];
export type SeasonalReceipt = components['schemas']['SeasonalAdoptionReceipt'];
export type SeasonalLookup = components['schemas']['SeasonalAdoptionLookup'];
export type SeasonalProof = components['schemas']['SeasonalAdoptionProof'];
export type SeasonalConfirmation = components['schemas']['SeasonalAdoptionConfirmRequest'];
export type SeasonalIntent = { protocol: 'seasonal-adoption-browser-command-v1'; user_id: string; policy_id: string; epoch_id: string; path: string; body: SeasonalConfirmation; body_json: string; request_hash: string; reviewed_scope: SeasonalScope };
const originals = new WeakMap<object, string>(), reads = new WeakSet<object>();
export const seasonalOriginalText = (value: object) => originals.get(value) ?? null;
export const isFreshSeasonalRead = (value: object) => reads.has(value);
const same = (a: unknown, b: unknown) => seasonalCanonicalJson(a) === seasonalCanonicalJson(b);
const exact = (v: Record<string, unknown>, fields: string[]) => Object.keys(v).sort().join('|') === [...fields].sort().join('|');
const time = (v: unknown): v is string => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const day = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d\d-\d\d$/.test(v) && new Date(`${v}T00:00:00Z`).toISOString().slice(0, 10) === v;
const cents = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0;
const key = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(v);
function check(value: unknown): asserts value { if (!value) throw new Error('季节采纳的完整原来源、周期、用户、请求或摘要不一致；保留原键'); }
function save<T extends object>(value: T, raw?: string): T { if (raw !== undefined) originals.set(value, raw); return value; }
function strings(value: unknown): asserts value is string[] { check(Array.isArray(value) && value.every((v) => typeof v === 'string')); }

/** Only the original Seasonal policy quantile is a float; money remains exact integer cents. */
export function seasonalCanonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(seasonalCanonicalJson).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().map((k) => {
    if (k === 'quantile' && value.type === 'seasonal_reserve') {
      const q = value[k]; check(typeof q === 'number' && Number.isFinite(q) && q >= 0.8 && q <= 1 && /^(?:0\.\d{1,4}|1)$/.test(String(q)));
      // Python's typed float preserves 1.0, while JSON.stringify(1) would lose it.
      return `${JSON.stringify(k)}:${Number.isInteger(q) ? `${q}.0` : String(q)}`;
    }
    return `${JSON.stringify(k)}:${seasonalCanonicalJson(value[k])}`;
  }).join(',')}}`;
  return releaseCanonicalJson(value);
}
export async function seasonalHash(value: unknown): Promise<string> { check(crypto.subtle); const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(seasonalCanonicalJson(value))); return [...new Uint8Array(digest)].map((n) => n.toString(16).padStart(2, '0')).join(''); }

export async function parseSeasonalScope(value: unknown): Promise<SeasonalScope> {
  assertMoneyFields(value); seasonalCanonicalJson(value);
  check(object(value) && exact(value, ['protocol', 'user_id', 'epoch_id', 'policy_id', 'version_id', 'configuration_hash', 'evaluated_at', 'timezone', 'window_id', 'holiday_code', 'official_start', 'official_end', 'protection_start', 'protection_end', 'adopted_adjustment_cents', 'required_adjustment_cents', 'cap_limited', 'full_policy_original', 'suggestion_original', 'source_evidence_originals', 'parameters', 'future_income_in_current_cash_cents', 'bank_authority', 'payment_or_settlement_proven']));
  check(value.protocol === 'full-seasonal-adoption-v1' && ['user_id', 'epoch_id', 'policy_id', 'version_id'].every((k) => releaseUUID(value[k])) && releaseDigest(value.configuration_hash) && time(value.evaluated_at) && value.timezone === 'Asia/Shanghai');
  check(['official_start', 'official_end', 'protection_start', 'protection_end'].every((k) => day(value[k])) && String(value.official_start) <= String(value.protection_start) && String(value.protection_start) <= String(value.protection_end) && value.official_end === value.protection_end && cents(value.adopted_adjustment_cents) && cents(value.required_adjustment_cents) && value.adopted_adjustment_cents <= value.required_adjustment_cents && value.cap_limited === (value.adopted_adjustment_cents < value.required_adjustment_cents) && value.future_income_in_current_cash_cents === 0 && value.bank_authority === false && value.payment_or_settlement_proven === false);
  const policy = value.full_policy_original, result = value.suggestion_original, parameters = value.parameters;
  check(object(policy) && object(policy.current_version) && object(result) && object(result.suggestion) && object(result.history_proof) && object(parameters));
  const version = policy.current_version, suggestion = result.suggestion, coverage = result.history_proof;
  check(policy.policy_id === value.policy_id && policy.epoch_id === value.epoch_id && policy.template_name === 'SeasonalReservePolicy' && ['ACTIVE', 'CONFIRMED'].includes(String(policy.effective_status)) && policy.planning_confirmation_valid === true && policy.reference_validation === 'CURRENT' && version.version_id === value.version_id && version.content_hash === value.configuration_hash && object(version.configuration) && await seasonalHash(version.configuration) === value.configuration_hash && object(version.confirmation) && version.confirmation.accepted === true && version.confirmation.user_id === value.user_id && version.confirmation.epoch_id === value.epoch_id && version.confirmation.version_id === value.version_id && version.confirmation.bank_authority === false);
  check(result.user_id === value.user_id && time(result.as_of) && Date.parse(result.as_of) === Date.parse(value.evaluated_at) && result.bank_authority === false && result.hard_protection_changed === false && Array.isArray(result.source_issues) && result.source_issues.length === 0 && coverage.verified === true && Array.isArray(coverage.reason_codes) && coverage.reason_codes.length === 0 && Array.isArray(coverage.account_ids) && coverage.account_ids.every(releaseUUID) && object(suggestion.target));
  check(suggestion.status === 'READY' && suggestion.target.window_id === value.window_id && suggestion.target.holiday_code === value.holiday_code && suggestion.target.start === value.official_start && suggestion.target.end === value.official_end && suggestion.effective_window_start === value.protection_start && suggestion.effective_window_end === value.protection_end && suggestion.proposed_adjustment_cents === value.adopted_adjustment_cents && suggestion.required_adjustment_cents === value.required_adjustment_cents && suggestion.cap_limited === value.cap_limited && Number.isSafeInteger(suggestion.window_count) && Number(suggestion.window_count) >= Number(parameters.minimum_historical_windows));
  check(parameters.window_id === value.window_id && cents(parameters.adjustment_cap_cents) && value.adopted_adjustment_cents <= parameters.adjustment_cap_cents && Array.isArray(result.public_windows) && await seasonalHash({ windows: result.public_windows }) === result.calendar_extraction_hash);
  check(Array.isArray(value.source_evidence_originals) && Array.isArray(result.source_evidence_ids) && result.source_evidence_ids.every(releaseUUID) && Array.isArray(version.evidence_ids) && version.evidence_ids.every(releaseUUID) && Array.isArray(result.sources));
  const expected = new Set([...result.source_evidence_ids, ...version.evidence_ids]), found = new Set<string>();
  for (const row of value.source_evidence_originals) {
    check(object(row) && releaseUUID(row.id) && !found.has(row.id) && row.user_id === value.user_id && row.status === 'VALID' && releaseDigest(row.content_hash) && object(row.content) && await seasonalHash(row.content) === row.content_hash && time(row.observed_at) && time(row.valid_from) && Date.parse(row.observed_at) <= Date.parse(value.evaluated_at) && Date.parse(row.valid_from) <= Date.parse(value.evaluated_at) && (row.valid_to === null || time(row.valid_to) && Date.parse(row.valid_to) > Date.parse(value.evaluated_at))); found.add(row.id);
  }
  check(found.size === expected.size && [...expected].every((id) => found.has(id)));
  for (const source of result.sources) { check(object(source)); const row = value.source_evidence_originals.find((r) => object(r) && r.id === source.evidence_id); check(object(row) && row.content_hash === source.evidence_hash && row.source_type === source.evidence_source_type && row.source_ref === source.evidence_source_ref && time(source.evidence_observed_at) && Date.parse(String(row.observed_at)) === Date.parse(source.evidence_observed_at)); }
  return value as SeasonalScope;
}
export async function seasonalReviewHash(scope: SeasonalScope): Promise<string> {
  const value = structuredClone(scope) as unknown as Record<string, unknown>; check(object(value.suggestion_original)); delete value.evaluated_at; delete value.suggestion_original.as_of; delete value.suggestion_original.source_digest; return seasonalHash(value);
}
async function sourceHash(scope: SeasonalScope): Promise<string> {
  const original = scope.suggestion_original; check(object(original.suggestion) && Array.isArray(original.suggestion.comparisons));
  const comparisons = original.suggestion.comparisons.map((row: unknown) => { check(object(row)); const copy = { ...row }; delete copy.scaled_excess_cents; return copy; });
  return seasonalHash({ user_id: scope.user_id, epoch_id: scope.epoch_id, policy: scope.full_policy_original, parameters: scope.parameters, window_id: scope.window_id, official_start: scope.official_start, official_end: scope.official_end, calendar_extraction_hash: original.calendar_extraction_hash, history_proof: original.history_proof, comparisons, sources: original.sources, evidence: scope.source_evidence_originals });
}
export function parseSeasonalConfirmation(value: unknown): SeasonalConfirmation {
  check(object(value) && exact(value, ['expected_version_id', 'window_id', 'expected_epoch_id', 'reviewed_hash', 'accepted', 'reason', 'idempotency_key']) && releaseUUID(value.expected_version_id) && releaseUUID(value.expected_epoch_id) && typeof value.window_id === 'string' && value.window_id.length > 0 && releaseDigest(value.reviewed_hash) && value.accepted === true && typeof value.reason === 'string' && value.reason.length > 0 && value.reason.length <= 500 && value.reason.trim() === value.reason && key(value.idempotency_key)); return value as SeasonalConfirmation;
}
export async function parseSeasonalPreview(value: unknown, userId: string, policyId: string, versionId: string, windowId: string, raw?: string): Promise<SeasonalPreview> {
  check(object(value) && exact(value, ['simulation', 'status', 'scope', 'reviewed_hash', 'reasons', 'bank_authority', 'hard_protection_changed']) && value.simulation === true && value.bank_authority === false && value.hard_protection_changed === false); strings(value.reasons);
  if (value.status === 'UNKNOWN') check(value.scope === null && value.reviewed_hash === null && value.reasons.length > 0);
  else { check(value.status === 'REVIEW_REQUIRED' && value.reasons.length === 0); const scope = await parseSeasonalScope(value.scope); check(scope.user_id === userId && scope.policy_id === policyId && scope.version_id === versionId && scope.window_id === windowId && value.reviewed_hash === await seasonalReviewHash(scope)); }
  return save(value as SeasonalPreview, raw);
}
export async function parseSeasonalIntent(value: unknown): Promise<SeasonalIntent> {
  check(object(value) && exact(value, ['protocol', 'user_id', 'policy_id', 'epoch_id', 'path', 'body', 'body_json', 'request_hash', 'reviewed_scope']) && value.protocol === 'seasonal-adoption-browser-command-v1'); const body = parseSeasonalConfirmation(value.body), scope = await parseSeasonalScope(value.reviewed_scope);
  check(value.user_id === scope.user_id && value.policy_id === scope.policy_id && value.epoch_id === scope.epoch_id && body.expected_epoch_id === scope.epoch_id && body.expected_version_id === scope.version_id && body.window_id === scope.window_id && body.reviewed_hash === await seasonalReviewHash(scope) && value.body_json === JSON.stringify(body) && value.path === `/seasonal-reserve-adoptions/${scope.policy_id}/confirm` && value.request_hash === await seasonalHash({ user_id: value.user_id, policy_id: value.policy_id, request: body })); return value as SeasonalIntent;
}
export async function createSeasonalIntent(scope: SeasonalScope, body: SeasonalConfirmation): Promise<SeasonalIntent> { return parseSeasonalIntent({ protocol: 'seasonal-adoption-browser-command-v1', user_id: scope.user_id, policy_id: scope.policy_id, epoch_id: scope.epoch_id, path: `/seasonal-reserve-adoptions/${scope.policy_id}/confirm`, body: structuredClone(body), body_json: JSON.stringify(body), request_hash: await seasonalHash({ user_id: scope.user_id, policy_id: scope.policy_id, request: body }), reviewed_scope: structuredClone(scope) }); }
export async function parseSeasonalReceipt(value: unknown, intent?: SeasonalIntent, raw?: string): Promise<SeasonalReceipt> {
  check(object(value) && exact(value, ['simulation', 'original', 'evidence_id', 'evidence_hash', 'trace_hash', 'idempotent_replay', 'bank_authority', 'financial_execution_performed']) && value.simulation === true && value.bank_authority === false && value.financial_execution_performed === false && typeof value.idempotent_replay === 'boolean' && releaseDigest(value.trace_hash));
  const o = value.original; check(object(o) && exact(o, ['protocol', 'command_id', 'user_id', 'epoch_id', 'policy_id', 'idempotency_key', 'original_request', 'request_hash', 'reviewed_hash', 'source_binding_hash', 'principal_at_command', 'recorded_at', 'scope', 'future_income_in_current_cash_cents', 'bank_authority', 'financial_execution_performed']) && o.protocol === 'full-seasonal-adoption-v1' && time(o.recorded_at) && o.future_income_in_current_cash_cents === 0 && o.bank_authority === false && o.financial_execution_performed === false);
  const body = parseSeasonalConfirmation(o.original_request), scope = await parseSeasonalScope(o.scope), p = o.principal_at_command;
  check(object(p) && exact(p, ['user_id', 'role', 'session_id', 'issued_at', 'expires_at', 'authentication_source', 'authenticated', 'human_identity_verified']) && p.user_id === scope.user_id && p.role === 'USER' && releaseUUID(p.session_id) && p.authentication_source === 'LOCAL_SIGNED_SESSION' && p.authenticated === true && p.human_identity_verified === false && time(p.issued_at) && time(p.expires_at) && Date.parse(p.issued_at) <= Date.parse(o.recorded_at) && Date.parse(o.recorded_at) < Date.parse(p.expires_at));
  check(o.user_id === scope.user_id && o.epoch_id === scope.epoch_id && o.policy_id === scope.policy_id && Date.parse(o.recorded_at) === Date.parse(scope.evaluated_at) && o.idempotency_key === body.idempotency_key && body.expected_epoch_id === scope.epoch_id && body.expected_version_id === scope.version_id && body.window_id === scope.window_id && o.reviewed_hash === body.reviewed_hash && o.reviewed_hash === await seasonalReviewHash(scope) && o.source_binding_hash === await sourceHash(scope) && o.request_hash === await seasonalHash({ user_id: o.user_id, policy_id: o.policy_id, request: body }) && o.command_id === await releaseUUID5('caaf871c-09d1-44c4-baf1-8b0d8f9fef83', `${o.user_id}:${o.epoch_id}:${o.idempotency_key}`) && value.evidence_id === await releaseUUID5(String(o.command_id), 'evidence') && value.evidence_hash === await seasonalHash(o));
  if (intent) check(o.user_id === intent.user_id && o.policy_id === intent.policy_id && o.epoch_id === intent.epoch_id && same(body, intent.body) && o.request_hash === intent.request_hash && await seasonalReviewHash(scope) === await seasonalReviewHash(intent.reviewed_scope));
  return save(value as SeasonalReceipt, raw);
}
export async function parseSeasonalLookup(value: unknown, intent: SeasonalIntent, raw?: string): Promise<SeasonalLookup> {
  await parseSeasonalIntent(intent); check(object(value) && exact(value, ['simulation', 'user_id', 'epoch_id', 'idempotency_key', 'status', 'original_receipt', 'bank_authority']) && value.simulation === true && value.bank_authority === false && value.user_id === intent.user_id && value.epoch_id === intent.epoch_id && value.idempotency_key === intent.body.idempotency_key);
  if (value.status === 'NOT_FOUND_NOT_FINAL') check(value.original_receipt === null); else { check(value.status === 'RECORDED'); await parseSeasonalReceipt(value.original_receipt, intent); } return save(value as SeasonalLookup, raw);
}
export async function parseSeasonalProof(value: unknown, userId: string, policyId: string, raw?: string): Promise<SeasonalProof> {
  check(object(value) && exact(value, ['simulation', 'protocol', 'status', 'user_id', 'epoch_id', 'policy_id', 'as_of', 'original', 'evidence_id', 'evidence_hash', 'trace_hash', 'current_scope', 'actual_adoption_count', 'retained_command_ids', 'reasons', 'bank_authority', 'financial_execution_performed']) && value.simulation === true && value.protocol === 'full-seasonal-adoption-v1' && value.user_id === userId && value.policy_id === policyId && releaseUUID(value.epoch_id) && time(value.as_of) && value.bank_authority === false && value.financial_execution_performed === false && cents(value.actual_adoption_count) && Array.isArray(value.retained_command_ids) && value.retained_command_ids.every(releaseUUID) && new Set(value.retained_command_ids).size === value.actual_adoption_count); strings(value.reasons);
  if (value.original !== null) { const receipt = await parseSeasonalReceipt({ simulation: true, original: value.original, evidence_id: value.evidence_id, evidence_hash: value.evidence_hash, trace_hash: value.trace_hash, idempotent_replay: true, bank_authority: false, financial_execution_performed: false }); check(receipt.original.user_id === userId && receipt.original.policy_id === policyId && receipt.original.epoch_id === value.epoch_id && value.retained_command_ids.includes(receipt.original.command_id)); }
  else check(value.evidence_id === null && value.evidence_hash === null && value.trace_hash === null);
  if (value.current_scope !== null) { const scope = await parseSeasonalScope(value.current_scope); check(scope.user_id === userId && scope.policy_id === policyId && scope.epoch_id === value.epoch_id && Date.parse(scope.evaluated_at) === Date.parse(value.as_of)); }
  if (value.status === 'VERIFIED') { check(object(value.original) && value.current_scope !== null && value.reasons.length === 0 && value.actual_adoption_count >= 1 && await sourceHash(value.current_scope as SeasonalScope) === value.original.source_binding_hash); }
  else { check(['ADVICE_ONLY', 'UNKNOWN'].includes(String(value.status)) && value.reasons.length > 0 && (value.status !== 'ADVICE_ONLY' || value.original === null && value.current_scope === null)); }
  return save(value as SeasonalProof, raw);
}
export async function previewSeasonalAdoption(userId: string, policyId: string, versionId: string, windowId: string): Promise<SeasonalPreview> { check(releaseUUID(userId) && releaseUUID(policyId) && releaseUUID(versionId)); return request(`/seasonal-reserve-adoptions/${policyId}/preview`, 'POST', { expected_version_id: versionId, window_id: windowId }, (v, raw) => parseSeasonalPreview(v, userId, policyId, versionId, windowId, raw)); }
export async function postSeasonalAdoption(intent: SeasonalIntent): Promise<SeasonalReceipt> { await parseSeasonalIntent(intent); return request(intent.path, 'POST', intent.body, (v, raw) => parseSeasonalReceipt(v, intent, raw)); }
export async function lookupSeasonalAdoption(intent: SeasonalIntent): Promise<SeasonalLookup> { await parseSeasonalIntent(intent); const value = await request(`/seasonal-reserve-adoptions/commands/${intent.epoch_id}/by-key/${encodeURIComponent(intent.body.idempotency_key)}`, 'GET', undefined, (v, raw) => parseSeasonalLookup(v, intent, raw)); reads.add(value); return value; }
export async function readSeasonalAdoption(userId: string, policyId: string): Promise<SeasonalProof> { check(releaseUUID(userId) && releaseUUID(policyId)); return request(`/seasonal-reserve-adoptions/${policyId}`, 'GET', undefined, (v, raw) => parseSeasonalProof(v, userId, policyId, raw)); }
